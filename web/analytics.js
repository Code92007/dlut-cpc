const analyticsState = {
  data: null, loading: false, error: '',
  performanceQuery: '', performanceYear: 'all', performanceStage: 'all', performanceSchool: 'all', performanceSort: 'rating',
  eloQuery: '', eloSchool: 'all', eloScope: 'ranked', eloSort: 'conservative', eloPlayer: null,
};

async function loadAnalytics(force = false) {
  if (analyticsState.loading || (analyticsState.data && !force)) return;
  analyticsState.loading = true;
  analyticsState.error = '';
  try {
    const response = await fetch('/api/analytics', {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    analyticsState.data = await response.json();
  } catch (error) {
    analyticsState.error = error.message;
  } finally {
    analyticsState.loading = false;
    if (['elo', 'performance'].includes(routeFromPath())) renderRoute(routeFromPath());
  }
}

function analyticsEmpty() {
  return `<div class="empty-state" role="status">${analyticsState.error
    ? `数据加载失败：${escapeHtml(analyticsState.error)} <button class="admin-button secondary" data-analytics-retry>重试</button>`
    : '正在读取历史成绩…'}</div>`;
}

function analyticsTabs(active) {
  return `<nav class="analytics-tabs" aria-label="实力分析"><a href="/performance" data-route="performance" ${active === 'performance' ? 'aria-current="page"' : ''}>Performance</a><a href="/elo" data-route="elo" ${active === 'elo' ? 'aria-current="page"' : ''}>Elo <small>试行</small></a></nav>`;
}

function analysisStage(text) {
  if (!text) return '<span class="unknown">—</span>';
  const style = text.includes('金') ? 'gold' : text.includes('银') ? 'silver' : text.includes('铜') ? 'bronze' : '';
  return `<span class="performance-stage ${style}">${escapeHtml(text)}</span>`;
}

function performanceRows() {
  const s = analyticsState;
  const query = s.performanceQuery.trim().toLowerCase();
  const rows = (s.data?.performances.records || []).filter(row =>
    (s.performanceYear === 'all' || String(row.season) === s.performanceYear)
    && (s.performanceSchool === 'all' || row.school === s.performanceSchool)
    && (s.performanceStage === 'all' || row.stage.includes(s.performanceStage))
    && (!query || [row.season, row.series, row.location, row.team, ...(row.honor?.members || [])].join(' ').toLowerCase().includes(query)));
  return rows.sort((a, b) => s.performanceSort === 'recent'
    ? b.season - a.season || b.maxSolvedRating - a.maxSolvedRating || a.sourceRow - b.sourceRow
    : b.maxSolvedRating - a.maxSolvedRating || b.season - a.season || a.sourceRow - b.sourceRow);
}

function performancePage() {
  const s = analyticsState;
  const archive = s.data?.performances;
  const rows = performanceRows();
  const years = [...new Set((archive?.records || []).map(row => row.season))].sort((a, b) => b - a);
  return `<div class="page-shell analysis-page">
    <div class="page-heading"><div><span class="eyebrow">Team Performance</span><h1>Performance</h1><p>把解题上限与赛场结果放在一起，看队伍在不同赛站的发挥。</p></div><a href="/home" data-route="home">返回首页 ›</a></div>
    ${analyticsTabs('performance')}
    ${!archive ? analyticsEmpty() : `
    <div class="analysis-summary"><div><strong>${archive.records.length}</strong><span>已核验记录</span></div><div><strong>${new Set(archive.records.map(r => `${r.season}-${r.series}-${r.location}`)).size}</strong><span>赛站</span></div><div><strong>${Math.max(...archive.records.map(r => r.maxSolvedRating))}</strong><span>已解最高 R</span></div><div><strong>${Math.min(...years)}–${Math.max(...years)}</strong><span>实际比赛年份</span></div></div>
    <p class="analysis-note">原表完整收录 160 条，新增 2026 年六站 23 条。最高 R 取赛时 AC；升级目标保留原有 AC，再补最容易的未解题。<a href="/assets/team-performance-2020-2026.xlsx" download>下载完整原表 ↓</a> · <a href="#performance-source">查看来源与口径 ↓</a></p>
    <div class="filters analysis-filters">
      <label class="filter-group grow"><span>搜索队伍 / 赛站 / 成员</span><input id="analyticsQuery" type="search" value="${escapeHtml(s.performanceQuery)}" placeholder="例如：暂无名、西安、2022"></label>
      <label class="filter-group"><span>比赛年份</span><select data-analysis-select="performanceYear"><option value="all">全部年份</option>${years.map(year => `<option ${String(year) === s.performanceYear ? 'selected' : ''}>${year}</option>`).join('')}</select></label>
      <label class="filter-group"><span>校区</span><select data-analysis-select="performanceSchool"><option value="all">全部校区</option>${[...new Set(archive.records.map(r => r.school))].map(school => `<option ${s.performanceSchool === school ? 'selected' : ''}>${escapeHtml(school)}</option>`).join('')}</select></label>
      <label class="filter-group"><span>当前阶段</span><select data-analysis-select="performanceStage">${[['all','全部阶段'],['金','金牌阶段'],['银','银牌阶段'],['铜','铜牌阶段'],['铁','铁牌阶段']].map(([v,t]) => `<option value="${v}" ${s.performanceStage === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
      <label class="filter-group"><span>排序</span><select data-analysis-select="performanceSort"><option value="rating">已解最高 R</option><option value="recent" ${s.performanceSort === 'recent' ? 'selected' : ''}>年份优先</option></select></label>
    </div>
    <div class="analysis-table-heading"><span role="status">${rows.length} 条记录</span><button class="analysis-text-button" data-analysis-export="performance">导出当前筛选 CSV ↓</button></div>
    <div class="data-table-wrap"><table class="data-table performance-table"><thead><tr><th>年份 / 赛站</th><th>队伍 / 成员</th><th>已解最高 R</th><th>当前阶段</th><th>下一阶段 R</th><th>下两阶段 R</th><th>档案成绩</th></tr></thead><tbody>
    ${rows.map(row => `<tr><td><strong>${row.season} ${escapeHtml(row.series)}</strong><small class="result-status">${escapeHtml(row.location)}</small></td><td class="team-name">${escapeHtml(row.team)}<small class="result-status">${row.honor ? escapeHtml(row.honor.members.join(' / ') || '名单待补') : '原表记录 · 档案暂未匹配'}</small><small class="result-status">${escapeHtml(row.school)} · ${row.sourceKind === 'workbook' ? '原表第 ' + row.sourceRow + ' 行' : '2026 新增'}</small><details><summary>赛时 AC / 依据</summary><p>${escapeHtml(row.accepted || (row.solved === 0 ? '0 AC' : '逐题 Rating 暂缺'))}</p><p>${escapeHtml(row.note || '')}</p>${sourceLink({name: '查看原榜', url: row.boardUrl})}</details></td><td><strong class="problem-rating-number">${row.maxSolvedRating ?? '—'}</strong>${row.maxSolvedRating == null ? `<small class="result-status">${row.solved === 0 ? '0 AC' : 'Rating 暂缺'}</small>` : `<span class="rating-meter" style="--rating-percent:${Math.min(100, row.maxSolvedRating / 3000 * 100)}%" aria-hidden="true"></span>`}</td><td>${analysisStage(row.stage)}</td><td>${analysisStage(row.nextStage)}</td><td>${analysisStage(row.nextTwoStages)}</td><td>${row.honor ? `${escapeHtml(row.honor.official === false ? '打星' : row.honor.medal || '奖项待确认')}<small class="result-status">${escapeHtml(row.honor.rank || '名次待补')}</small><small class="result-status">${escapeHtml(row.honor.date)} · ${sourceLink(row.honor.source)}</small>` : '<span class="unknown">待核验</span>'}</td></tr>`).join('') || '<tr><td colspan="7" class="table-empty">没有符合条件的记录</td></tr>'}
    </tbody></table></div>
    <details class="analysis-method" id="performance-source"><summary>数据来源与阅读方式</summary><p>${escapeHtml(archive.source.note)}</p><p>阶段依次为：铁牌 → 速铜 → 稳铜 → 速银 → 稳银 → 速金 → 稳金。速：达到该奖项末队题数，仍需比较罚时；稳：超过所有未获该奖项队伍的最高题数。若某个稳档与更高奖项的稳档重合，保留该档空白，下一/下两阶段按固定顺序取值。仅罚时表示已达到目标题数。新增 R 是补题集合的最高难度，可能低于已解最高 R。</p><p>打星无正式奖项，星号沿用原表；空白可能是无独立阶段、不可达或 Rating 暂缺。0 AC 的最高 R 显示空值。年份为实际比赛年，延期赛事另有赛季；2026 新增使用 CF-Bot 当前题目 Rating 和原榜赛时 AC，核对全场 AC 集合及正式资格的分布。乐山原榜 300 支正式队，本站 CPC Finder 成绩为 299 支口径，升级门槛使用原榜。</p><p>原表：${escapeHtml(archive.source.name)} · 工作表 ${escapeHtml(archive.source.sheet)} · 2026 数据核验于 ${escapeHtml(archive.updatedAt)} · <a href="https://cf-bot.wannafly.cn/regionals" target="_blank" rel="noopener noreferrer">CF-Bot 题目 Rating ↗</a> · <a href="/assets/team-performance-20261114.png" target="_blank" rel="noopener noreferrer">最初截图 ↗</a>。原表历史 R 保留原快照，2026 六站 R 固定于本次核验；它们不直接参与个人积分。</p></details>`}
  </div>`;
}

function eloRows() {
  const s = analyticsState;
  const query = s.eloQuery.trim().toLowerCase();
  return (s.data?.elo.players || []).filter(p =>
    (s.eloScope === 'all' || (s.eloScope === 'ranked' ? !p.provisional : p.contests > 0 && p.provisional))
    && (s.eloSchool === 'all' || p.school === s.eloSchool)
    && (!query || [p.name, p.school, ...p.history.map(h => h.team)].join(' ').toLowerCase().includes(query)))
    .sort((a, b) => (b[s.eloSort] ?? -Infinity) - (a[s.eloSort] ?? -Infinity) || b.contests - a.contests || a.id - b.id);
}

function eloChart(player) {
  if (!player.history.length) return '<p class="contest-caption">没有满足计分条件的正式参赛记录。</p>';
  const values = [1500, ...player.history.map(h => h.rating)];
  const low = Math.floor((Math.min(...values) - 80) / 100) * 100;
  const high = Math.ceil((Math.max(...values) + 80) / 100) * 100;
  const x = i => 55 + i / Math.max(1, values.length - 1) * 670;
  const y = v => 190 - (v - low) / (high - low) * 155;
  return `<div class="elo-chart"><svg viewBox="0 0 760 245" role="img" aria-label="${escapeHtml(player.name)}的积分变化，按参赛顺序排列，横轴不是等距时间轴">
    ${Array.from({length: 4}, (_, i) => {const v = low + (high - low) * i / 3; return `<line x1="55" x2="725" y1="${y(v)}" y2="${y(v)}" stroke="#dfe3e8"/><text x="45" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}</text>`;}).join('')}
    <polyline points="${values.map((v, i) => `${x(i)},${y(v)}`).join(' ')}" fill="none" stroke="#1769aa" stroke-width="2.5"/>
    ${player.history.map((h, i) => `<circle cx="${x(i + 1)}" cy="${y(h.rating)}" r="4" fill="#1769aa"><title>${escapeHtml(h.date)} ${escapeHtml(h.event)}：${Math.round(h.rating)} (${h.delta > 0 ? '+' : ''}${h.delta})</title></circle>`).join('')}
    <text x="55" y="225">初始 1500</text><text x="725" y="225" text-anchor="end">${escapeHtml(player.lastDate)} · 第 ${player.contests} 场</text>
    </svg></div>`;
}

function eloMedals(player) {
  const medals = player.careerMedals ?? player.medals;
  return ['金牌','银牌','铜牌','铁牌'].map(m => `${medals?.[m] || 0}${m[0]}`).join(' / ');
}

function eloDetail(player) {
  if (!player) return '';
  const cf = player.cfReference;
  const unrated = player.unratedHistory || [];
  return `<section class="elo-detail" id="elo-detail"><div class="section-heading"><div><span class="eyebrow">Player History</span><h2>${escapeHtml(player.name)} <small>${escapeHtml(player.school)}</small></h2></div><button class="analysis-text-button" data-elo-close>收起 ×</button></div>
    <p class="contest-caption">已收录 ${player.careerContests ?? player.contests} 场正式战绩（${eloMedals(player)}） · ${player.contests} 场计分 · ${unrated.length} 场待补计分依据 · ${player.teamCount} 套计分阵容${player.teamCount === 1 ? ' · 目前仅有一套阵容，无法从队伍成绩区分三人的独立贡献。' : ''}</p>
    ${cf?.verified && cf.handle ? `<p class="contest-caption">个人赛参考：<a href="https://codeforces.com/profile/${encodeURIComponent(cf.handle)}" target="_blank" rel="noopener noreferrer">CF ${escapeHtml(cf.handle)} ↗</a> · 当前 ${escapeHtml(String(cf.rating ?? '未评级'))} / 最高 ${escapeHtml(String(cf.maxRating ?? '未评级'))}。不参与历史积分回放。${cf.ratingUpdatedAt ? ` 更新于 ${escapeHtml(cf.ratingUpdatedAt.slice(0,10))}。` : ''}</p>` : ''}
    ${eloChart(player)}
    <div class="data-table-wrap"><table class="data-table"><thead><tr><th>日期 / 比赛</th><th>队伍 / 队友</th><th>正式名次</th><th>奖项 / 单场表现分</th><th>积分变化</th><th>赛后积分</th></tr></thead><tbody>${player.history.map(h => `<tr><td>${escapeHtml(h.date)}<small class="result-status">${escapeHtml(h.event)}</small></td><td>${escapeHtml(h.team)}<small class="result-status">${escapeHtml(h.members.join(' / '))}</small></td><td>${h.rank} / ${h.total}<small class="result-status">前 ${h.percentile}%</small>${h.rankSource ? `<small class="result-status">${sourceLink(h.rankSource)}</small>` : ''}</td><td>${escapeHtml(h.medal)}<small class="result-status">P ${Math.round(h.performance)}${h.fieldAdjustment ? ` · ${h.event.includes('CCPC') ? 'CCPC Final' : 'EC Final'} +${h.fieldAdjustment}` : ''}</small></td><td class="${h.delta > 0 ? 'delta-up' : h.delta < 0 ? 'delta-down' : ''}">${h.delta > 0 ? '+' : ''}${h.delta.toFixed(1)}<small class="result-status">赛前 ${Math.round(h.before)}</small></td><td><strong>${Math.round(h.rating)}</strong></td></tr>`).join('') || '<tr><td colspan="6" class="table-empty">没有可计分记录</td></tr>'}</tbody></table></div>
    ${unrated.length ? `<h3>已收录、暂未计分的正式战绩</h3><p class="contest-caption">这些奖项已计入上方战绩。补齐依据后才回放积分，不把资料缺失记作失利。</p><div class="data-table-wrap"><table class="data-table"><thead><tr><th>日期 / 比赛</th><th>队伍</th><th>奖项 / 原始名次</th><th>待补依据</th></tr></thead><tbody>${unrated.map(h => `<tr><td>${escapeHtml(h.date)}<small class="result-status">${escapeHtml(h.event)}</small></td><td>${escapeHtml(h.team)}</td><td>${escapeHtml(h.medal)}<small class="result-status">${escapeHtml(h.rank || '名次待考')}</small></td><td>${escapeHtml(h.reason)}${h.auditNote ? `<small class="result-status">${escapeHtml(h.auditNote)}</small>` : ''}<small class="result-status">${sourceLink(h.source)}</small></td></tr>`).join('')}</tbody></table></div>` : ''}</section>`;
}

function eloPage() {
  const s = analyticsState;
  const elo = s.data?.elo;
  const rows = eloRows();
  const c = elo?.coverage;
  const player = elo?.players.find(p => p.id === s.eloPlayer);
  return `<div class="page-shell analysis-page"><div class="page-heading"><div><span class="eyebrow">Historical Rating · v4</span><h1>Elo <span class="analysis-badge">试行</span></h1><p>让已确认的历史参赛记录，拥有一条可追溯的积分轨迹。</p></div><a href="#elo-method">计算规则 ↓</a></div>
    ${analyticsTabs('elo')}
    ${!elo ? analyticsEmpty() : `
    <div class="analysis-summary"><div><strong>${c.ratedPlayers}</strong><span>有计分记录的成员</span></div><div><strong>${c.ratedResults} / ${c.totalResults}</strong><span>有效队伍成绩 / 已收录</span></div><div><strong>${c.rankedPlayers}</strong><span>满 ${elo.minContests} 场进入正式榜</span></div><div><strong>${c.missingPlayers}</strong><span>尚缺有效记录的成员</span></div></div>
    <div class="analysis-notice"><strong>同场共享表现分，按各自赛前积分更新。</strong><p>每场统一更新 30% 的表现差距：表现高于个人赛前分就涨，低于就跌。全员使用相同系数，不设新人加速，也不因队友此前积分低而额外涨分。EC Final 表现分 +200，CCPC Final +300，不累计奖牌积分。普通赛站强度及跨年代比较仍待校准；队伍成绩无法证明个人贡献；默认按扣除样本量参考带的保守分排名，原始积分与峰值可另行排序。</p></div>
    <div class="filters analysis-filters"><label class="filter-group grow"><span>搜索姓名 / 队伍</span><input id="analyticsQuery" type="search" value="${escapeHtml(s.eloQuery)}" placeholder="输入成员姓名或历史队名"></label>
    <label class="filter-group"><span>榜单范围</span><select data-analysis-select="eloScope">${[['ranked','正式榜 · 至少三场'],['provisional','暂定 · 一至两场'],['all','全部成员 · 含无数据']].map(([v,t]) => `<option value="${v}" ${s.eloScope === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label class="filter-group"><span>所属范围</span><select data-analysis-select="eloSchool"><option value="all">全部范围</option>${schoolGroups.map(school => `<option ${s.eloSchool === school ? 'selected' : ''}>${escapeHtml(school)}</option>`).join('')}</select></label>
    <label class="filter-group"><span>排序</span><select data-analysis-select="eloSort">${[['conservative','保守分 · 默认'],['peak','历史峰值'],['current','最后参赛积分'],['contests','计分场次']].map(([v,t]) => `<option value="${v}" ${s.eloSort === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label></div>
    <div class="analysis-table-heading"><span role="status">${rows.length} 位成员 · 点击姓名查看轨迹 · 同分并列</span><button class="analysis-text-button" data-analysis-export="elo">导出当前筛选 CSV ↓</button></div>
    <div class="data-table-wrap"><table class="data-table elo-table"><thead><tr><th>名次</th><th>成员</th><th>保守分</th><th>历史峰值</th><th>最后参赛积分</th><th>已收录战绩</th><th>计分 / 收录</th><th>最后参赛</th></tr></thead><tbody>
    ${rows.map((p, i) => {const value = p[s.eloSort];const rank = rows.findIndex(other => other[s.eloSort] === value) + 1;return `<tr class="${p.id === s.eloPlayer ? 'selected-row' : ''}"><td>${p.provisional || value == null ? '—' : rank}</td><td><button class="elo-player" data-elo-player="${p.id}" aria-expanded="${p.id === s.eloPlayer}">${escapeHtml(p.name)}</button><small class="result-status">${escapeHtml(p.school)}${p.provisional ? ` · ${p.contests ? '暂定' : '无数据'}` : ''}</small></td><td class="elo-score">${p.conservative ?? '—'}</td><td>${p.peak ?? '—'}</td><td>${p.current ?? '—'}<small class="result-status">末场 ${p.history.length ? (p.history.at(-1).delta > 0 ? '+' : '') + p.history.at(-1).delta.toFixed(1) : '—'}</small></td><td>${eloMedals(p)}</td><td>${p.contests} / ${p.careerContests ?? p.contests}<small class="result-status">${p.teamCount} 套阵容${p.unratedHistory?.length ? ` · ${p.unratedHistory.length} 场未计分` : ''}</small></td><td>${escapeHtml(p.lastDate || '—')}</td></tr>`;}).join('') || '<tr><td colspan="8" class="table-empty">没有符合条件的成员</td></tr>'}
    </tbody></table></div>
    ${eloDetail(player)}
    <details id="elo-method" class="analysis-method"><summary>计算规则、数据边界与维护机制</summary>
      <h3>奖项锚定的逐场 Rating · v4</h3><p>所有年代从 1500 起算，按实际比赛日期回放。实际金牌单场表现分为 2600–3200、银牌 1900–2400、铜牌 1300–1800、铁牌 800–1200。同奖项内，正式名次越靠前，表现分越高。实际奖项决定分档，不按名次比例反推奖项。</p>
      <p>令 q = (r − 0.5) / N，各奖项的名次参考区间依次为 [0, 0.1]、[0.1, 0.3]、[0.3, 0.6]、[0.6, 1]。在本档参考区间内线性映射至表现分上限→下限，超出则截到本档边界。每人按自己的赛前分 R 更新：R′ = R + 0.3(P − R)，所有人、所有场次都使用 0.3。比如个人赛前 2400、表现 2200，本场下降 60 分。共同参赛时，原有分差每场缩小为 70%，不会永久锁定，也不会因同场相同表现而交叉反超。</p>
      <p>缺少参赛资格不补造铜牌或失利。单场队伍结果给三人同一个表现观测；队友的此前积分或场次数不会改变自己的更新。固定队友履历相同则同分；履历不同的队员允许涨跌不同，但相同系数使分差收敛。队伍结果仍不能分离个人贡献，不能据此断言谁更强。已确认的 CF 当前 / 最高分在详情中独立展示，不将今天的 CF 分数代入历史。本站标尺与 CF 分数不直接对标。</p>
      <p>正式队数用于计算本场分位，不代表阵容强度：百余支筛选队与三百余支普通赛站队伍不能只比较百分位。先对决赛做独立强度调整：EC Final +200，CCPC Final +300，适用于所有奖项。例如 EC 金牌 P=2800–3400，CCPC Final 金牌 P=2900–3500。两项是公开的初始先验，尚非统计拟合；普通赛站间及年代因素暂不额外调整。近年规则亦有奖励名额、多队名额，不能一概视为每校仅一队。<a href="https://icpc.pku.edu.cn/docs/2025-08/3e349141f2e6420aabb5aae82fbdb198.pdf" target="_blank" rel="noopener noreferrer">EC 名额规则 ↗</a> · <a href="https://ccpc.io/rules/369" target="_blank" rel="noopener noreferrer">CCPC 名额规则 ↗</a></p>
      <h3>峰值、样本量与参考带</h3><p>正式榜至少 ${elo.minContests} 场。历史峰值取第三场及之后的最高赛后积分；最后参赛积分保留最后一次结果，退役与不活跃不扣分。参考带 B = max(150, 400 / √(1 + n / 3))，保守分 = 最后参赛积分 − B。默认按保守分排名，履历短的成员先保留更大的不确定性，避免把少参赛当作更强的证据；可切换原始积分或历史峰值排序。参考带未做统计校准，不是置信区间，也没有消除固定队友的混淆。</p>
      <h3>只使用已经确认的数据</h3><p>仅纳入 ICPC / CCPC 正式区域赛与区决赛，要求有效完整日期、三位稳定成员 ID、已确认名单及实际奖项、正式名次 / 正式队总数。不按奖牌猜排名，不从总榜替代正式排名，不将缺失成绩记作失利。重复记录去重，同日跨队身份冲突暂停计分。无有效记录的成员保留在“全部成员”，显示无数据。</p>
      <p>主表战绩统计已收录且身份确认的正式奖项，包括暂缺计分依据的铁牌；不是完整生涯总数。详情列出未计分场次与原因。${escapeHtml(elo.evidenceVerifiedAt || '')} 已复查缺少名次口径的记录，本次补证 ${elo.supplementedResults ?? 0} 条；补证链接见逐场明细，仍待考的记录见下方核验说明。早年只收录获牌队的选择偏差仍存在，历史峰值也受参赛次数影响。后续需继续补全全国榜、校准场强，并用留出比赛检验预测。</p>
      <h3>后续维护</h3><p>使用本站实时成绩库；每日同步、管理员补录或纠错后，下次打开本页即从历史重算。按成员 ID 关联，不按同名猜人。规则带版本号和输入指纹，历史补录可改变后续积分。<button class="analysis-text-button" data-analysis-export="audit">下载完整计算记录 JSON ↓</button></p>
      <p><code>${escapeHtml(elo.version)}</code> · 输入指纹 <code class="input-hash">${escapeHtml(elo.inputHash)}</code></p>
      <p>参考项目：<a href="https://zzzzzzyt.github.io/xcpc-elo/" target="_blank" rel="noopener noreferrer">XCPC Elo ↗</a>、<a href="https://junjiecharles.github.io/xcpc-sight/?series=2026-2027" target="_blank" rel="noopener noreferrer">XCPC Sight ↗</a>。当前积分由本站独立计算，未导入外部分数。</p>
    </details>
    <details class="analysis-method"><summary>未计分记录 · ${elo.skipped.length} 条（查看原因）</summary><div class="data-table-wrap"><table class="data-table"><thead><tr><th>日期 / 比赛</th><th>队伍</th><th>未计分原因</th></tr></thead><tbody>${elo.skipped.map(h => `<tr><td>${escapeHtml(h.date)}<small class="result-status">${escapeHtml(h.event)}</small></td><td>${escapeHtml(h.team)}</td><td>${escapeHtml(h.reason)}${h.auditNote ? `<small class="result-status">${escapeHtml(h.auditNote)}</small>` : ''}<small class="result-status">${sourceLink(h.source)}</small></td></tr>`).join('')}</tbody></table></div></details>`}
    </div>`;
}

function downloadAnalysis(kind) {
  let content, type, filename;
  if (kind === 'audit') {
    content = JSON.stringify(analyticsState.data.elo, null, 2);
    type = 'application/json'; filename = 'dlut-historical-rating-v4.json';
  } else {
    const rows = kind === 'elo'
      ? [['姓名','所属范围','历史峰值','最后参赛积分','参考带','保守分','计分场次','已收录正式场次','已收录战绩','未计分场次','最后参赛'], ...eloRows().map(p => [p.name,p.school,p.peak,p.current,p.band,p.conservative,p.contests,p.careerContests ?? p.contests,eloMedals(p),p.unratedHistory?.length || 0,p.lastDate])]
      : [['比赛年份','赛事','赛站','队伍','已解最高R','当前阶段','下一阶段R','下两阶段R','来源行','校区','赛时AC','原榜'], ...performanceRows().map(r => [r.season,r.series,r.location,r.team,r.maxSolvedRating,r.stage,r.nextStage,r.nextTwoStages,r.sourceRow,r.school,r.accepted,r.boardUrl])];
    content = '\uFEFF' + rows.map(row => row.map(value => {
      let text = String(value ?? '');
      if (/^[=+@\-\t\r]/.test(text)) text = "'" + text;
      return '"' + text.replaceAll('"','""') + '"';
    }).join(',')).join('\r\n');
    type = 'text/csv;charset=utf-8'; filename = `dlut-${kind}.csv`;
  }
  const url = URL.createObjectURL(new Blob([content], {type}));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function bindAnalyticsEvents(route) {
  document.querySelector('[data-analytics-retry]')?.addEventListener('click', () => loadAnalytics(true));
  document.querySelectorAll('[data-analysis-select]').forEach(select => select.addEventListener('change', event => {
    analyticsState[select.dataset.analysisSelect] = event.target.value;
    renderRoute(route);
  }));
  const input = document.querySelector('#analyticsQuery');
  const update = () => {
    analyticsState[route === 'elo' ? 'eloQuery' : 'performanceQuery'] = input.value;
    const position = input.selectionStart;
    renderRoute(route);
    const replacement = document.querySelector('#analyticsQuery');
    replacement.focus(); replacement.setSelectionRange(position, position);
  };
  let composing = false;
  input?.addEventListener('compositionstart', () => { composing = true; });
  input?.addEventListener('compositionend', () => { composing = false; update(); });
  input?.addEventListener('input', event => { if (!composing && !event.isComposing) update(); });
  document.querySelectorAll('[data-elo-player]').forEach(button => button.addEventListener('click', () => {
    analyticsState.eloPlayer = Number(button.dataset.eloPlayer);
    renderRoute('elo');
    document.querySelector('#elo-detail')?.scrollIntoView({block: 'start'});
  }));
  document.querySelector('[data-elo-close]')?.addEventListener('click', () => { analyticsState.eloPlayer = null; renderRoute('elo'); });
  document.querySelectorAll('[data-analysis-export]').forEach(button => button.addEventListener('click', () => downloadAnalysis(button.dataset.analysisExport)));
}
