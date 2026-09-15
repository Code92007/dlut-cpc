const app = document.querySelector("#app");
const nav = document.querySelector(".nav-links");
const navToggle = document.querySelector(".nav-toggle");
const routes = new Set(["home", "honor", "rating", "training", "admin"]);
const state = {
  data: null,
  honorYear: "all",
  honorMedal: "all",
  honorQuery: "",
  memberQuery: "",
  memberStatus: "all",
  memberSort: "recent",
  trainingId: null,
  trainingSeries: "all",
  adminSession: null,
  adminView: "members",
  adminMessage: "",
  adminError: false,
};

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const routeFromPath = () => {
  const route = location.pathname.split("/").filter(Boolean)[0] || "home";
  return routes.has(route) ? route : "home";
};

const medalClass = (medal) => ({ 金牌: "gold", 银牌: "silver", 铜牌: "bronze", 铁牌: "iron" }[medal] || "");
const resultRank = (honor) => `${escapeHtml(honor.rank || honor.overallRank || "—")}${honor.official === false ? '<small class="result-status">非正式</small>' : ""}`;

const renderMembers = (members) => {
  if (!members?.length) return '<span class="unknown">暂无成员记录</span>';
  return members.map(escapeHtml).map((name) => `<span>${name}</span>`).join("");
};

const sourceLink = (source) => source?.url
  ? `<a class="source-link" href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.name || "来源")}</a>`
  : '<span class="source-link">待补充</span>';

function updateNav(route) {
  document.querySelectorAll("[data-route]").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (link.closest("nav")) link.setAttribute("aria-current", active ? "page" : "false");
  });
  nav.classList.remove("open");
  navToggle.setAttribute("aria-expanded", "false");
}

function navigate(route, push = true) {
  if (!routes.has(route)) route = "home";
  if (push && location.pathname !== `/${route}`) history.pushState({}, "", `/${route}`);
  updateNav(route);
  renderRoute(route);
  if (route === "admin") loadAdminSession();
  window.scrollTo({ top: 0, behavior: "instant" });
  app.focus({ preventScroll: true });
}

function medalChart(summary) {
  const width = 1040;
  const height = 340;
  const margin = { top: 30, right: 32, bottom: 48, left: 44 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...summary.flatMap((item) => [item.gold, item.silver, item.bronze, item.iron || 0]));
  const maxY = Math.ceil(maxValue / 3) * 3;
  const x = (index) => margin.left + (chartWidth * index) / Math.max(1, summary.length - 1);
  const y = (value) => margin.top + chartHeight - (chartHeight * value) / maxY;
  const colors = { gold: "#d8a126", silver: "#9aa2ad", bronze: "#b86d45", iron: "#52606d" };
  const grids = [];
  for (let value = 0; value <= maxY; value += 3) {
    grids.push(`<line x1="${margin.left}" y1="${y(value)}" x2="${width - margin.right}" y2="${y(value)}" stroke="#dfe3e8" />`);
    grids.push(`<text x="${margin.left - 10}" y="${y(value) + 4}" text-anchor="end" fill="#7a838f" font-size="11">${value}</text>`);
  }
  const series = ["gold", "silver", "bronze", "iron"].map((key) => {
    const points = summary.map((item, index) => `${x(index)},${y(item[key] || 0)}`).join(" ");
    const dots = summary.map((item, index) => `<circle cx="${x(index)}" cy="${y(item[key] || 0)}" r="3.5" fill="#fff" stroke="${colors[key]}" stroke-width="2" />`).join("");
    return `<polyline points="${points}" fill="none" stroke="${colors[key]}" stroke-width="2.2" stroke-linejoin="round" />${dots}`;
  }).join("");
  const years = summary.map((item, index) => `<text x="${x(index)}" y="${height - 18}" text-anchor="middle" fill="#68717e" font-size="11">${item.year}</text>`).join("");
  return `<div class="chart-legend"><span class="gold">金牌</span><span class="silver">银牌</span><span class="bronze">铜牌</span><span class="iron">铁牌</span></div>
    <div class="chart-scroller"><svg class="medal-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="年度成绩趋势图">${grids.join("")}${series}${years}</svg></div>`;
}

function honorRows(honors) {
  return honors.map((honor) => `<tr>
      <td>${escapeHtml(honor.date)}</td>
      <td>${escapeHtml(honor.location)}</td>
      <td class="team-name">${escapeHtml(honor.team)}</td>
      <td><div class="member-list">${renderMembers(honor.members)}</div></td>
      <td class="medal ${medalClass(honor.medal)}">${escapeHtml(honor.medal)}</td>
      <td>${resultRank(honor)}</td>
      <td>${sourceLink(honor.source)}</td>
    </tr>`).join("");
}

function homePage(data) {
  const recent = [...data.honors].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12);
  const total = data.medalSummary.reduce((sum, year) => sum + year.gold + year.silver + year.bronze, 0);
  const gold = data.medalSummary.reduce((sum, year) => sum + year.gold, 0);
  return `<section class="hero-band">
      <div class="hero-inner">
        <div class="hero-copy">
          <span class="eyebrow">Competitive Programming</span>
          <h1>大连理工大学程序设计竞赛队<span>Dalian University of Technology CPC Team</span></h1>
          <p>记录队伍在 ICPC、CCPC 等程序设计竞赛中的成绩、成员与训练轨迹。</p>
        </div>
        <img class="hero-mark" src="/assets/dlut-mark.png" alt="大连理工大学校徽">
      </div>
    </section>
    <section class="metric-strip" aria-label="队伍概览">
      <div class="metric"><strong>${total}</strong><span>2020 年以来奖牌</span></div>
      <div class="metric"><strong>${gold}</strong><span>金牌</span></div>
      <div class="metric"><strong>${escapeHtml(data.meta.bestRank)}</strong><span>区域赛最高正式排名</span></div>
      <div class="metric"><strong>${escapeHtml(data.meta.memberCoverage)}%</strong><span>参赛成员覆盖率</span></div>
    </section>
    <div class="home-content">
      <section class="section-block">
        <div class="section-heading"><div><span class="eyebrow">Summary</span><h2>年度成绩趋势</h2></div><div class="source-status"><i></i><span>更新于 ${escapeHtml(data.meta.updatedAt)}</span></div></div>
        <div class="chart-panel">${medalChart(data.medalSummary)}</div>
      </section>
      <section class="section-block">
        <div class="section-heading"><div><span class="eyebrow">Honor</span><h2>最近参赛</h2></div><a href="/honor" data-route="honor">查看全部成绩 ›</a></div>
        <div class="data-table-wrap"><table class="data-table">
          <thead><tr><th>日期</th><th>赛区</th><th>队伍</th><th>成员</th><th>成绩</th><th>排名</th><th>来源</th></tr></thead>
          <tbody>${honorRows(recent)}</tbody>
        </table></div>
      </section>
    </div>`;
}

function honorPage(data) {
  const years = [...new Set(data.honors.map((item) => item.date.slice(0, 4)))].sort().reverse();
  if (state.honorYear !== "all" && !years.includes(state.honorYear)) state.honorYear = "all";
  let filtered = data.honors.filter((item) => state.honorYear === "all" || item.date.startsWith(state.honorYear));
  filtered = filtered.filter((item) => state.honorMedal === "all" || item.medal === state.honorMedal);
  const query = state.honorQuery.trim().toLowerCase();
  if (query) filtered = filtered.filter((item) => [item.event, item.team, item.location, ...(item.members || [])].join(" ").toLowerCase().includes(query));
  const grouped = Object.groupBy
    ? Object.groupBy(filtered, (item) => item.event)
    : filtered.reduce((groups, item) => ((groups[item.event] ||= []).push(item), groups), {});
  const sections = Object.entries(grouped).map(([event, rows]) => `<section class="season-section">
      <h2>${escapeHtml(event)}</h2>
      <p class="contest-caption">${escapeHtml(rows[0].date)} · ${escapeHtml(rows[0].location)}</p>
      <div class="data-table-wrap"><table class="data-table honor-table">
        <thead><tr><th>队伍</th><th>成员</th><th>成绩</th><th>排名</th><th>来源</th></tr></thead>
        <tbody>${rows.map((honor) => `<tr><td class="team-name">${escapeHtml(honor.team)}</td><td><div class="member-list">${renderMembers(honor.members)}</div></td><td class="medal ${medalClass(honor.medal)}">${escapeHtml(honor.medal)}</td><td>${resultRank(honor)}</td><td>${sourceLink(honor.source)}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>`).join("");
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Contest Results</span><h1>参赛成绩</h1><p>2020 年以来 ICPC、CCPC 等赛事的获牌与未获牌记录。</p></div><div class="source-status"><i></i><span>${filtered.length} 条记录</span></div></div>
      <div class="filters">
        <label class="filter-group"><span>成绩</span><select id="honorMedal"><option value="all">全部成绩</option>${["金牌", "银牌", "铜牌", "铁牌"].map((m) => `<option value="${m}" ${state.honorMedal === m ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label class="filter-group grow"><span>搜索</span><input id="honorQuery" type="search" value="${escapeHtml(state.honorQuery)}" placeholder="比赛、赛区、队伍或成员"></label>
      </div>
      <div class="season-layout">
        <aside class="season-nav" aria-label="赛季筛选"><button type="button" data-year="all" class="${state.honorYear === "all" ? "active" : ""}">全部赛季</button>${years.map((year) => `<button type="button" data-year="${year}" class="${state.honorYear === year ? "active" : ""}">${year}</button>`).join("")}</aside>
        <div>${sections || '<div class="empty-state">没有符合筛选条件的记录</div>'}</div>
      </div>
    </div>`;
}

function ratingColor(rating) {
  if (rating == null) return "gray";
  if (rating >= 2400) return "red";
  if (rating >= 2100) return "orange";
  if (rating >= 1900) return "purple";
  if (rating >= 1600) return "blue";
  if (rating >= 1400) return "cyan";
  if (rating >= 1200) return "green";
  return "gray";
}

function memberAccounts(member) {
  return member.accounts?.codeforces || (member.handles?.codeforces ? [member.handles.codeforces] : []);
}

function accountLink(account) {
  return `<a class="handle ${ratingColor(account.rating)}" href="https://codeforces.com/profile/${encodeURIComponent(account.handle)}" target="_blank" rel="noreferrer">${escapeHtml(account.handle)}</a>`;
}

function compareMemberMedals(a, b) {
  const aMedals = a.medals || {};
  const bMedals = b.medals || {};
  const aIron = aMedals.iron ?? Infinity;
  const bIron = bMedals.iron ?? Infinity;
  return (bMedals.gold || 0) - (aMedals.gold || 0)
    || (bMedals.silver || 0) - (aMedals.silver || 0)
    || (bMedals.bronze || 0) - (aMedals.bronze || 0)
    || (aIron === bIron ? 0 : aIron - bIron)
    || a.name.localeCompare(b.name, "zh-CN");
}

function ratingPage(data) {
  const statusLabel = { current: "近年成员", alumni: "往届成员", unknown: "年代待补", manual: "人工补录" };
  const query = state.memberQuery.trim().toLowerCase();
  let members = (data.members || []).filter((member) => {
    if (state.memberStatus === "manual" && !member.manual) return false;
    if (state.memberStatus !== "all" && state.memberStatus !== "manual" && member.status !== state.memberStatus) return false;
    if (!query) return true;
    return [member.name, ...(member.aliases || []), ...(member.teams || []), ...memberAccounts(member).map(account => account.handle)].join(" ").toLowerCase().includes(query);
  });
  if (state.memberSort === "honors") members.sort((a, b) => b.honorCount - a.honorCount || a.name.localeCompare(b.name, "zh-CN"));
  if (state.memberSort === "medals") members.sort(compareMemberMedals);
  if (state.memberSort === "rating") members.sort((a, b) => (b.handles?.codeforces?.rating ?? -1) - (a.handles?.codeforces?.rating ?? -1) || a.name.localeCompare(b.name, "zh-CN"));
  if (state.memberSort === "maxrating") members.sort((a, b) => (b.handles?.codeforces?.maxRating ?? -1) - (a.handles?.codeforces?.maxRating ?? -1) || a.name.localeCompare(b.name, "zh-CN"));
  if (state.memberSort === "cpcfinder") members.sort((a, b) => (b.cpcfinder?.rating || -1) - (a.cpcfinder?.rating || -1) || a.name.localeCompare(b.name, "zh-CN"));
  const handleCount = (data.members || []).filter((member) => member.handles?.codeforces?.handle).length;
  const publicMemberCount = (data.members || []).filter((member) => member.cpcfinder).length;
  const rows = members.map((member) => {
    const account = member.handles?.codeforces;
    const secondary = memberAccounts(member).slice(1);
    const years = member.firstYear && member.lastYear
      ? (member.firstYear === member.lastYear ? member.firstYear : `${member.firstYear}–${member.lastYear}`)
      : (member.firstYear || member.lastYear || "—");
    const teams = member.teams?.length
      ? `<span class="member-teams" title="${escapeHtml(member.teams.join(" / "))}">${escapeHtml(member.teams.slice(0, 2).join(" / "))}${member.teams.length > 2 ? ` 等 ${member.teams.length} 支` : ""}</span>`
      : '<span class="unknown">待补充</span>';
    const medals = member.medals || {};
    const profile = member.cpcfinder;
    const memberName = profile?.url
      ? `<a class="member-profile-link" title="${escapeHtml((member.aliases || []).join(' / '))}" href="${escapeHtml(profile.url)}" target="_blank" rel="noreferrer">${escapeHtml(member.name)}</a>`
      : `<strong title="${escapeHtml((member.aliases || []).join(' / '))}">${escapeHtml(member.name)}</strong>`;
    const publicRating = profile?.rating == null
      ? "—"
      : `<a class="public-rating" href="${escapeHtml(profile.url)}" target="_blank" rel="noreferrer" title="CPC Finder Rating · 来源排名 ${escapeHtml(profile.rank || "—")}">${escapeHtml(Math.round(profile.rating))}</a>`;
    const accountCell = account
      ? accountLink(account)
      : '<span class="unknown">待补充</span>';
    return `<tr>
      <td>${memberName}${member.manual ? '<span class="manual-tag">人工</span>' : ""}</td>
      <td>${escapeHtml(statusLabel[member.status] || "成员")}</td>
      <td>${escapeHtml(years)}</td>
      <td>${teams}</td>
      <td><div class="member-medals"><span class="gold">金 ${medals.gold || 0}</span><span class="silver">银 ${medals.silver || 0}</span><span class="bronze">铜 ${medals.bronze || 0}</span><span class="iron">${medals.iron == null ? "铁待补" : `铁 ${medals.iron}`}</span></div></td>
      <td>${publicRating}</td>
      <td>${accountCell}</td>
      <td class="handle ${ratingColor(account?.maxRating)}">${account?.maxRating != null ? escapeHtml(account.maxRating) : "—"}</td>
      <td class="handle ${ratingColor(account?.rating)}" title="${account?.ratingUpdatedAt ? escapeHtml(new Date(account.ratingUpdatedAt).toLocaleString('zh-CN')) : ''}">${account?.rating != null ? escapeHtml(account.rating) : (account?.ratingUpdatedAt ? "未评级" : "—")}</td>
      <td><div class="secondary-accounts">${secondary.length ? secondary.map(item => `<div>${accountLink(item)}<small title="最高 Rating / 当前 Rating">${escapeHtml(item.maxRating ?? '—')} / ${escapeHtml(item.rating ?? '—')}</small></div>`).join('') : '<span class="unknown">—</span>'}</div></td>
    </tr>`;
  }).join("");
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Members & Rating</span><h1>成员与 Rating</h1><p>成员名册以 CPC Finder 的稳定选手编号为主，并与赛事名单、队内补录合并。</p></div><div class="source-status"><i></i><span>${members.length} 位成员</span></div></div>
      <section class="directory-stats" aria-label="成员数据概览">
        <div><strong>${escapeHtml(data.meta.memberCount)}</strong><span>已收录成员</span></div>
        <div><strong>${publicMemberCount}</strong><span>CPC Finder 名册</span></div>
        <div><strong>${handleCount}</strong><span>已关联 CF 成员</span></div>
        <div><strong>${escapeHtml(data.meta.honorsWithMembers)}</strong><span>含成员的参赛成绩</span></div>
      </section>
      <div class="filters member-filters">
        <label class="filter-group"><span>范围</span><select id="memberStatus"><option value="all">全部成员</option><option value="current" ${state.memberStatus === "current" ? "selected" : ""}>近年成员</option><option value="alumni" ${state.memberStatus === "alumni" ? "selected" : ""}>往届成员</option><option value="unknown" ${state.memberStatus === "unknown" ? "selected" : ""}>年代待补</option><option value="manual" ${state.memberStatus === "manual" ? "selected" : ""}>人工补录</option></select></label>
        <label class="filter-group"><span>排序</span><select id="memberSort"><option value="recent">最近参赛</option><option value="medals" ${state.memberSort === "medals" ? "selected" : ""}>奖牌榜顺序</option><option value="honors" ${state.memberSort === "honors" ? "selected" : ""}>获奖次数</option><option value="cpcfinder" ${state.memberSort === "cpcfinder" ? "selected" : ""}>CPC Finder Rating</option><option value="maxrating" ${state.memberSort === "maxrating" ? "selected" : ""}>CF 最高 Rating</option><option value="rating" ${state.memberSort === "rating" ? "selected" : ""}>CF 当前 Rating</option></select></label>
        <label class="filter-group grow"><span>搜索</span><input id="memberQuery" type="search" value="${escapeHtml(state.memberQuery)}" placeholder="成员、队伍或 Codeforces 账号"></label>
      </div>
      <div class="data-table-wrap"><table class="data-table member-directory-table">
        <thead><tr><th>成员</th><th>类别</th><th>参赛年份</th><th>队伍</th><th>奖牌</th><th>CPC Finder Rating</th><th>CF 主号</th><th>最高 Rating</th><th>当前 Rating</th><th>CF 副号</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="10" class="table-empty">没有符合条件的成员</td></tr>'}</tbody>
      </table></div>
    </div>`;
}

function adminMemberLabel(member) {
  return `${member.name} · #${member.id}`;
}

function adminMemberId(value) {
  const member = state.data.members.find(item => adminMemberLabel(item) === value);
  if (!member) throw new Error("请选择名单中的成员");
  return member.id;
}

function adminPage(data) {
  const session = state.adminSession;
  const message = state.adminMessage ? `<div class="admin-message ${state.adminError ? 'error' : ''}" role="status">${escapeHtml(state.adminMessage)}</div>` : "";
  const heading = `<div class="page-heading"><div><span class="eyebrow">Admin</span><h1>数据管理</h1></div>${session?.authenticated ? `<div class="admin-session"><span>${escapeHtml(session.username)}</span><button id="adminLogout" class="admin-button secondary" type="button">退出登录</button></div>` : ''}</div>`;
  if (!session) return `<div class="page-shell">${heading}<div class="empty-state">正在验证登录状态</div>${message}</div>`;
  if (!session.authenticated) return `<div class="page-shell">${heading}${message}<form id="adminLogin" class="admin-login">
    ${!session.enabled ? '<div class="admin-message error">管理员账号尚未配置</div>' : ''}
    <label>账号<input name="username" autocomplete="username" required value="admin" maxlength="100"></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required maxlength="512"></label>
    <button class="admin-button" ${!session.enabled ? 'disabled' : ''}>登录</button>
  </form></div>`;
  const memberInput = (name, label, required = true) => `<label>${label}<input name="${name}" list="adminMembers" autocomplete="off" ${required ? 'required' : ''} placeholder="姓名或成员 ID"></label>`;
  const options = data.members.map(member => `<option value="${escapeHtml(adminMemberLabel(member))}"></option>`).join('');
  const forms = {
    members: `<form id="adminMember" class="admin-form">
      <h2>补录成员</h2><div class="admin-fields">
      <label>姓名<input name="name" required maxlength="150"></label>
      <label>类别<select name="status"><option value="alumni">往届成员</option><option value="current">近年成员</option><option value="unknown">年代待补</option></select></label>
      <label>入学年份<input name="entryYear" type="number" min="1900" max="2046"></label>
      <label>毕业年份<input name="graduationYear" type="number" min="1900" max="2046"></label>
      <label class="wide">内部备注<textarea name="notes" rows="3" maxlength="2000"></textarea></label>
      <label class="admin-checkbox wide"><input name="allowSameName" type="checkbox">独立的同名成员</label>
      </div><button class="admin-button">保存成员</button></form>`,
    accounts: `<form id="adminAccount" class="admin-form"><h2>追加 Codeforces 账号</h2><div class="admin-fields">
      ${memberInput('member', '成员')}<label>Codeforces 账号<input name="handle" required maxlength="100" autocomplete="off"></label>
      </div><button class="admin-button">保存账号</button><button id="adminRefreshRatings" class="admin-button secondary" type="button">更新全部 Rating</button></form>`,
    names: `<form id="adminName" class="admin-form"><h2>姓名映射</h2><div class="admin-fields">
      ${memberInput('member', '成员')}<label>显示姓名<input name="displayName" required maxlength="150"></label>
      <label class="wide">报名别名<textarea name="aliases" rows="3" placeholder="Fangyu Bu" maxlength="3000"></textarea></label>
      </div><button class="admin-button">保存映射</button></form>`,
    honors: `<form id="adminHonor" class="admin-form"><h2>补录参赛成绩</h2><div class="admin-fields">
      <label>比赛名称<input name="event" required maxlength="300"></label><label>日期<input name="date" type="date" required></label>
      <label>赛事<select name="series"><option>ICPC</option><option>CCPC</option><option>其他</option></select></label><label>赛区<input name="location" maxlength="150"></label>
      <label>队伍<input name="team" required maxlength="200"></label><label>成绩<select name="medal"><option>金牌</option><option>银牌</option><option>铜牌</option><option>铁牌</option></select></label>
      ${memberInput('member1', '成员 1')}${memberInput('member2', '成员 2', false)}${memberInput('member3', '成员 3', false)}
      <label>排名<input name="rank" maxlength="100" placeholder="11 / 200"></label><label class="wide">来源链接<input name="sourceUrl" type="url" maxlength="1500"></label>
      </div><button class="admin-button">保存成绩</button></form>`,
  };
  return `<div class="page-shell">${heading}${message}<div class="admin-tabs" role="tablist" aria-label="管理项目">
    ${[['members','成员'],['accounts','账号'],['names','姓名映射'],['honors','参赛成绩']].map(([view,label]) => `<button type="button" role="tab" aria-selected="${state.adminView === view}" data-admin-view="${view}">${label}</button>`).join('')}
    </div><datalist id="adminMembers">${options}</datalist>${forms[state.adminView]}
    <section class="admin-recent"><h2>人工补录成员</h2><div class="data-table-wrap"><table class="data-table"><thead><tr><th>ID</th><th>姓名</th><th>入学年份</th><th>毕业年份</th><th>Codeforces</th></tr></thead><tbody>
    ${data.members.filter(member => member.manual).map(member => `<tr><td>${member.id}</td><td>${escapeHtml(member.name)}</td><td>${escapeHtml(member.entryYear || '—')}</td><td>${escapeHtml(member.graduationYear || '—')}</td><td>${memberAccounts(member).map(accountLink).join(' / ') || '—'}</td></tr>`).join('') || '<tr><td colspan="5" class="table-empty">暂无人工补录成员</td></tr>'}
    </tbody></table></div></section></div>`;
}

async function loadAdminSession() {
  try {
    const response = await fetch('/api/admin/session', {cache: 'no-store'});
    if (!response.ok) throw new Error('登录状态加载失败');
    state.adminSession = await response.json();
  } catch (error) {
    state.adminMessage = error.message;
    state.adminError = true;
  }
  if (routeFromPath() === 'admin') renderRoute('admin');
}

async function adminRequest(path, body) {
  const response = await fetch(`/api/admin/${path}`, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.adminSession?.csrf || ''},
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) state.adminSession = null;
    throw new Error(result.error || `HTTP ${response.status}`);
  }
  return result;
}

function bindAdminEvents() {
  document.querySelectorAll('[data-admin-view]').forEach(button => button.addEventListener('click', () => {
    state.adminView = button.dataset.adminView;
    state.adminMessage = '';
    renderRoute('admin');
  }));
  const bindForm = (id, path, buildBody, success) => document.querySelector(id)?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button');
    button.disabled = true;
    state.adminMessage = '';
    try {
      const values = Object.fromEntries(new FormData(form));
      const result = await adminRequest(path, buildBody(values));
      state.adminMessage = result.warning || success(result);
      state.adminError = false;
      if (path === 'login') await loadAdminSession();
      else {
        const response = await fetch('/api/site', {cache: 'no-store'});
        if (!response.ok) throw new Error('数据刷新失败');
        state.data = await response.json();
      }
      if (routeFromPath() === 'admin') renderRoute('admin');
    } catch (error) {
      state.adminMessage = error.message;
      state.adminError = true;
      button.disabled = false;
      let message = document.querySelector('.admin-message');
      if (!message) {
        message = document.createElement('div');
        form.before(message);
      }
      message.className = 'admin-message error';
      message.setAttribute('role', 'alert');
      message.textContent = error.message;
      if (!state.adminSession) await loadAdminSession();
    }
  });
  bindForm('#adminLogin', 'login', values => values, () => '已登录');
  bindForm('#adminMember', 'member', values => ({...values, entryYear: values.entryYear ? Number(values.entryYear) : null,
    graduationYear: values.graduationYear ? Number(values.graduationYear) : null, allowSameName: values.allowSameName === 'on'}), result => `成员已保存 · #${result.memberId}`);
  bindForm('#adminAccount', 'account', values => ({memberId: adminMemberId(values.member), handle: values.handle}), () => '账号及 Rating 已保存');
  bindForm('#adminName', 'name', values => ({memberId: adminMemberId(values.member), displayName: values.displayName,
    aliases: values.aliases.split(/\n/).map(alias => alias.trim()).filter(Boolean)}), () => '姓名映射已保存');
  bindForm('#adminHonor', 'honor', values => ({...values, memberIds: [values.member1, values.member2, values.member3].filter(Boolean).map(adminMemberId)}), () => '参赛成绩已保存');
  const bindAction = (id, path, success) => document.querySelector(id)?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await adminRequest(path, {});
      state.adminMessage = result.warning || success(result);
      state.adminError = false;
      await loadAdminSession();
      const response = await fetch('/api/site', {cache: 'no-store'});
      if (!response.ok) throw new Error('数据刷新失败');
      state.data = await response.json();
    } catch (error) {
      state.adminMessage = error.message;
      state.adminError = true;
    }
    if (routeFromPath() === 'admin') renderRoute('admin');
  });
  bindAction('#adminLogout', 'logout', () => '已退出登录');
  bindAction('#adminRefreshRatings', 'refresh-ratings', result => `已更新 ${result.updated} 个账号的 Rating`);
}

function standingsTable(contest) {
  const showProblems = contest.problemDetailsAvailable !== false;
  const problemHeaders = showProblems ? contest.problems.map((problem) => `<th>${escapeHtml(problem)}</th>`).join("") : "";
  const rows = contest.teams.map((team, index) => `<tr class="${team.highlight ? "highlight" : ""}">
      <td class="rank-cell">${escapeHtml(team.rank || index + 1)}</td><td class="who-cell">${escapeHtml(team.name)}</td><td class="solved-cell">${team.solved}</td><td class="penalty-cell">${team.penalty}</td>
      ${showProblems ? contest.problems.map((problem) => {
        const result = team.problems[problem];
        if (!result) return "<td></td>";
        if (!result.solved) return `<td class="problem-cell failed"><strong>-${result.tries || 1}</strong></td>`;
        const tries = result.tries > 1 ? `+${result.tries - 1}` : "+";
        return `<td class="problem-cell ${result.first ? "first" : ""}"><strong>${tries}</strong><small>${escapeHtml(result.time)}</small></td>`;
      }).join("") : ""}
    </tr>`).join("");
  return `<div class="standings-wrap ${showProblems ? "" : "compact"}"><div class="standings-caption"><span>Standings</span><span>大连理工大学队伍 · ${escapeHtml(contest.rankScope || "榜内名次")}</span></div><table class="standings"><thead><tr><th class="rank-cell">#</th><th class="who-cell">Who</th><th class="solved-cell">=</th><th class="penalty-cell">Penalty</th>${problemHeaders}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function rankChart(contest) {
  const width = 960;
  const height = 470;
  const margin = { top: 24, right: 170, bottom: 48, left: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maxRank = contest.teams.length;
  const x = (time) => margin.left + (plotWidth * time) / contest.duration;
  const y = (rank) => margin.top + (plotHeight * (rank - 1)) / Math.max(1, maxRank - 1);
  const colors = ["#1769aa", "#9a315f", "#3e8516", "#a0683f", "#b64d36", "#245f85", "#8c3d94", "#657d2a", "#b47421", "#547e75"];
  const vertical = [0, 60, 120, 180, 240, 300].filter((v) => v <= contest.duration).map((value) => `<line x1="${x(value)}" y1="${margin.top}" x2="${x(value)}" y2="${height - margin.bottom}" stroke="#e2e5e9"/><text x="${x(value)}" y="${height - 20}" text-anchor="middle" fill="#68717e" font-size="11">${value}</text>`).join("");
  const horizontal = contest.teams.map((_, index) => `<line x1="${margin.left}" y1="${y(index + 1)}" x2="${width - margin.right}" y2="${y(index + 1)}" stroke="#eef0f2"/><text x="${margin.left - 10}" y="${y(index + 1) + 4}" text-anchor="end" fill="#68717e" font-size="10">${index + 1}</text>`).join("");
  const lines = contest.teams.map((team, index) => {
    const color = colors[index % colors.length];
    const history = team.rankHistory || [{ time: 0, rank: index + 1 }, { time: contest.duration, rank: index + 1 }];
    const points = history.map((point) => `${x(point.time)},${y(point.rank)}`).join(" ");
    const last = history.at(-1);
    return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/><circle cx="${x(last.time)}" cy="${y(last.rank)}" r="3" fill="${color}"/><text x="${width - margin.right + 16}" y="${margin.top + 17 * index}" fill="${color}" font-size="10">${escapeHtml(team.name.slice(0, 16))}</text>`;
  }).join("");
  return `<div class="rank-chart-wrap"><svg class="rank-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="训练赛排名变化图">${vertical}${horizontal}${lines}<text x="${margin.left + plotWidth / 2}" y="${height - 3}" text-anchor="middle" fill="#68717e" font-size="11">Time</text><text x="12" y="${margin.top + plotHeight / 2}" fill="#68717e" font-size="11">Rank</text></svg></div>`;
}

function trainingPage(data) {
  const allContests = data.training || [];
  const series = [...new Set(allContests.map((item) => item.series).filter(Boolean))];
  const contests = allContests.filter((item) => state.trainingSeries === "all" || item.series === state.trainingSeries);
  if (!state.trainingId || !contests.some((item) => item.id === state.trainingId)) state.trainingId = contests[0]?.id;
  const contest = contests.find((item) => item.id === state.trainingId);
  if (!contest) return '<div class="page-shell"><div class="empty-state">暂无训练记录</div></div>';
  const bestRank = Math.min(...contest.teams.map((team) => team.rank || Number.MAX_SAFE_INTEGER));
  const totalSolved = contest.teams.reduce((sum, team) => sum + (team.solved || 0), 0);
  const source = contest.source || {};
  const chart = contest.rankHistoryAvailable ? rankChart(contest) : `<div class="training-data-note"><strong>最终榜数据</strong><span>${contest.problemDetailsAvailable === false ? "公开镜像仅保留最终名次、过题数和罚时。" : "公开接口未提供全场排名变化，不生成推测曲线。"}</span></div>`;
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Training</span><h1>训练记录</h1><p>牛客暑期多校、杭电多校与队内训练赛档案。</p></div><div class="source-status"><i></i>${sourceLink(source)}</div></div>
      <div class="training-series" role="group" aria-label="训练系列">${["all", ...series].map((item) => `<button type="button" data-training-series="${escapeHtml(item)}" class="${state.trainingSeries === item ? "active" : ""}">${item === "all" ? "全部" : escapeHtml(item)}</button>`).join("")}</div>
      <div class="training-layout">
        <aside class="training-dates" aria-label="训练日期">${contests.map((item) => {
          const round = item.title?.match(/Round\s*0?(\d+)/i)?.[1];
          const label = item.date?.length >= 10 ? item.date.slice(5) : (round ? `R${round}` : item.series?.slice(0, 2) || "记录");
          return `<button type="button" data-training-id="${escapeHtml(item.id)}" class="${item.id === contest.id ? "active" : ""}"><span>${escapeHtml(item.year)}</span><span>${escapeHtml(label)}</span></button>`;
        }).join("")}</aside>
        <div><div class="training-title"><span>${escapeHtml(contest.series || "训练赛")}</span><h1>${escapeHtml(contest.title)}</h1><p>${escapeHtml(contest.dateLabel || contest.date)}</p></div>
          <section class="training-summary" aria-label="本场概览"><div><strong>${contest.teams.length}</strong><span>DLUT 队伍</span></div><div><strong>${bestRank === Number.MAX_SAFE_INTEGER ? "—" : bestRank}</strong><span>全榜最佳名次</span></div><div><strong>${totalSolved}</strong><span>合计过题</span></div><div><strong>${escapeHtml(contest.resultType || "最终榜")}</strong><span>数据粒度</span></div></section>
          ${standingsTable(contest)}${chart}</div>
      </div>
    </div>`;
}

function bindPageEvents(route) {
  if (route === "honor") {
    document.querySelectorAll("[data-year]").forEach((button) => button.addEventListener("click", () => {
      state.honorYear = button.dataset.year;
      renderRoute("honor");
    }));
    document.querySelector("#honorMedal")?.addEventListener("change", (event) => {
      state.honorMedal = event.target.value;
      renderRoute("honor");
    });
    document.querySelector("#honorQuery")?.addEventListener("input", (event) => {
      state.honorQuery = event.target.value;
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(() => renderRoute("honor"), 180);
    });
  }
  if (route === "rating") {
    document.querySelector("#memberStatus")?.addEventListener("change", (event) => {
      state.memberStatus = event.target.value;
      renderRoute("rating");
    });
    document.querySelector("#memberSort")?.addEventListener("change", (event) => {
      state.memberSort = event.target.value;
      renderRoute("rating");
    });
    document.querySelector("#memberQuery")?.addEventListener("input", (event) => {
      state.memberQuery = event.target.value;
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(() => renderRoute("rating"), 180);
    });
  }
  if (route === "training") {
    document.querySelectorAll("[data-training-series]").forEach((button) => button.addEventListener("click", () => {
      state.trainingSeries = button.dataset.trainingSeries;
      state.trainingId = null;
      renderRoute("training");
    }));
    document.querySelectorAll("[data-training-id]").forEach((button) => button.addEventListener("click", () => {
      state.trainingId = button.dataset.trainingId;
      renderRoute("training");
    }));
  }
}

function renderRoute(route) {
  if (!state.data) return;
  const renderers = { home: homePage, honor: honorPage, rating: ratingPage, training: trainingPage, admin: adminPage };
  app.innerHTML = renderers[route](state.data);
  document.title = `${route === "home" ? "DLUT CPC" : `${route[0].toUpperCase()}${route.slice(1)} · DLUT CPC`}`;
  bindPageEvents(route);
  if (route === 'admin') bindAdminEvents();
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.dataset.route);
});

navToggle.addEventListener("click", () => {
  const open = nav.classList.toggle("open");
  navToggle.setAttribute("aria-expanded", String(open));
});

window.addEventListener("popstate", () => navigate(routeFromPath(), false));
document.querySelector("#footerYear").textContent = String(new Date().getFullYear());

fetch("/api/site", { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((data) => {
    state.data = data;
    navigate(routeFromPath(), false);
  })
  .catch((error) => {
    app.innerHTML = `<div class="page-shell"><div class="empty-state">数据加载失败：${escapeHtml(error.message)}</div></div>`;
  });
