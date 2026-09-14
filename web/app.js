const app = document.querySelector("#app");
const nav = document.querySelector(".nav-links");
const navToggle = document.querySelector(".nav-toggle");
const routes = new Set(["home", "honor", "rating", "training"]);
const state = { data: null, honorYear: "all", honorMedal: "all", honorQuery: "", trainingId: null };

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

const medalClass = (medal) => ({ 金牌: "gold", 银牌: "silver", 铜牌: "bronze" }[medal] || "");

const renderMembers = (members) => {
  if (!members?.length) return '<span class="unknown">成员待核验</span>';
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
  window.scrollTo({ top: 0, behavior: "instant" });
  app.focus({ preventScroll: true });
}

function medalChart(summary) {
  const width = 1040;
  const height = 340;
  const margin = { top: 30, right: 32, bottom: 48, left: 44 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const maxValue = Math.max(1, ...summary.flatMap((item) => [item.gold, item.silver, item.bronze]));
  const maxY = Math.ceil(maxValue / 3) * 3;
  const x = (index) => margin.left + (chartWidth * index) / Math.max(1, summary.length - 1);
  const y = (value) => margin.top + chartHeight - (chartHeight * value) / maxY;
  const colors = { gold: "#d8a126", silver: "#9aa2ad", bronze: "#b86d45" };
  const grids = [];
  for (let value = 0; value <= maxY; value += 3) {
    grids.push(`<line x1="${margin.left}" y1="${y(value)}" x2="${width - margin.right}" y2="${y(value)}" stroke="#dfe3e8" />`);
    grids.push(`<text x="${margin.left - 10}" y="${y(value) + 4}" text-anchor="end" fill="#7a838f" font-size="11">${value}</text>`);
  }
  const series = ["gold", "silver", "bronze"].map((key) => {
    const points = summary.map((item, index) => `${x(index)},${y(item[key])}`).join(" ");
    const dots = summary.map((item, index) => `<circle cx="${x(index)}" cy="${y(item[key])}" r="3.5" fill="#fff" stroke="${colors[key]}" stroke-width="2" />`).join("");
    return `<polyline points="${points}" fill="none" stroke="${colors[key]}" stroke-width="2.2" stroke-linejoin="round" />${dots}`;
  }).join("");
  const years = summary.map((item, index) => `<text x="${x(index)}" y="${height - 18}" text-anchor="middle" fill="#68717e" font-size="11">${item.year}</text>`).join("");
  return `<div class="chart-legend"><span class="gold">金牌</span><span class="silver">银牌</span><span class="bronze">铜牌</span></div>
    <div class="chart-scroller"><svg class="medal-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="年度奖牌趋势图">${grids.join("")}${series}${years}</svg></div>`;
}

function honorRows(honors) {
  return honors.map((honor) => `<tr>
      <td>${escapeHtml(honor.date)}</td>
      <td>${escapeHtml(honor.location)}</td>
      <td class="team-name">${escapeHtml(honor.team)}</td>
      <td><div class="member-list">${renderMembers(honor.members)}</div></td>
      <td class="medal ${medalClass(honor.medal)}">${escapeHtml(honor.medal)}</td>
      <td>${escapeHtml(honor.rank || "—")}</td>
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
      <div class="metric"><strong>${escapeHtml(data.meta.verifiedTeams)}</strong><span>已核验成员队伍</span></div>
    </section>
    <div class="home-content">
      <section class="section-block">
        <div class="section-heading"><div><span class="eyebrow">Summary</span><h2>年度奖牌趋势</h2></div><div class="source-status"><i></i><span>更新于 ${escapeHtml(data.meta.updatedAt)}</span></div></div>
        <div class="chart-panel">${medalChart(data.medalSummary)}</div>
      </section>
      <section class="section-block">
        <div class="section-heading"><div><span class="eyebrow">Honor</span><h2>最近获奖</h2></div><a href="/honor" data-route="honor">查看完整荣誉 ›</a></div>
        <div class="data-table-wrap"><table class="data-table">
          <thead><tr><th>日期</th><th>赛区</th><th>队伍</th><th>成员</th><th>奖项</th><th>正式排名</th><th>来源</th></tr></thead>
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
        <thead><tr><th>队伍</th><th>成员</th><th>奖项</th><th>正式排名</th><th>来源</th></tr></thead>
        <tbody>${rows.map((honor) => `<tr><td class="team-name">${escapeHtml(honor.team)}</td><td><div class="member-list">${renderMembers(honor.members)}</div></td><td class="medal ${medalClass(honor.medal)}">${escapeHtml(honor.medal)}</td><td>${escapeHtml(honor.rank || "—")}</td><td>${sourceLink(honor.source)}</td></tr>`).join("")}</tbody>
      </table></div>
    </section>`).join("");
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Honor Archive</span><h1>获奖记录</h1><p>公开榜单经学校、赛事、日期与队名归一化去重；成员仅展示已找到官方名单或可信榜单交叉验证的记录。</p></div><div class="source-status"><i></i><span>${filtered.length} 条记录</span></div></div>
      <div class="filters">
        <label class="filter-group"><span>奖项</span><select id="honorMedal"><option value="all">全部奖项</option>${["金牌", "银牌", "铜牌"].map((m) => `<option value="${m}" ${state.honorMedal === m ? "selected" : ""}>${m}</option>`).join("")}</select></label>
        <label class="filter-group grow"><span>搜索</span><input id="honorQuery" type="search" value="${escapeHtml(state.honorQuery)}" placeholder="比赛、赛区、队伍或成员"></label>
      </div>
      <div class="season-layout">
        <aside class="season-nav" aria-label="赛季筛选"><button type="button" data-year="all" class="${state.honorYear === "all" ? "active" : ""}">全部赛季</button>${years.map((year) => `<button type="button" data-year="${year}" class="${state.honorYear === year ? "active" : ""}">${year}</button>`).join("")}</aside>
        <div>${sections || '<div class="empty-state">没有符合筛选条件的记录</div>'}</div>
      </div>
    </div>`;
}

function ratingColor(rating) {
  if (!rating) return "gray";
  if (rating >= 2400) return "red";
  if (rating >= 2100) return "orange";
  if (rating >= 1900) return "purple";
  return "blue";
}

function ratingPage(data) {
  const groupRows = data.ratingGroups.map((group) => {
    const members = Array.from({ length: 3 }, (_, index) => group.members[index] || {});
    return `<tr><td class="rating-team">${escapeHtml(group.name)}</td>${members.map((member) => `<td class="handle ${ratingColor(member.rating)}">${escapeHtml(member.handle || member.name || "待关联")}</td><td class="handle ${ratingColor(member.rating)}">${member.rating ? escapeHtml(member.rating) : "—"}</td>`).join("")}</tr>`;
  }).join("");
  const maxScore = Math.max(...data.ratingGroups.map((group) => group.teamScore), 1);
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Rating</span><h1>Codeforces Rating</h1><p>成员名单来自公开赛事材料；Codeforces 账号仅在能够可靠关联时展示。</p></div><div class="source-status"><i></i><span>账号核验中</span></div></div>
      <div class="notice-band">Codeforces 账号待队员本人确认。</div>
      <div class="data-table-wrap"><table class="data-table rating-table">
        <thead><tr><th class="rating-team">队伍</th><th colspan="6">成员</th></tr></thead>
        <tbody>${groupRows}</tbody>
      </table></div>
      <section class="score-chart"><div><span class="eyebrow">Team Score</span><h2>队伍累计积分</h2></div>
        <div class="bar-list">${[...data.ratingGroups].sort((a, b) => b.teamScore - a.teamScore).map((group) => `<div class="bar-row"><span class="bar-label" title="${escapeHtml(group.name)}">${escapeHtml(group.name)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, group.teamScore / maxScore * 100)}%"></div></div><span class="bar-value">${escapeHtml(group.teamScore)}</span></div>`).join("")}</div>
      </section>
    </div>`;
}

function standingsTable(contest) {
  const problemHeaders = contest.problems.map((problem) => `<th>${escapeHtml(problem)}</th>`).join("");
  const rows = contest.teams.map((team, index) => `<tr class="${team.highlight ? "highlight" : ""}">
      <td class="rank-cell">${index + 1}</td><td class="who-cell">${escapeHtml(team.name)}</td><td class="solved-cell">${team.solved}</td><td class="penalty-cell">${team.penalty}</td>
      ${contest.problems.map((problem) => {
        const result = team.problems[problem];
        if (!result) return "<td></td>";
        if (!result.solved) return `<td class="problem-cell failed"><strong>-${result.tries || 1}</strong></td>`;
        const tries = result.tries > 1 ? `+${result.tries - 1}` : "+";
        return `<td class="problem-cell ${result.first ? "first" : ""}"><strong>${tries}</strong><small>${escapeHtml(result.time)}</small></td>`;
      }).join("")}
    </tr>`).join("");
  return `<div class="standings-wrap"><div class="standings-caption">Standings</div><table class="standings"><thead><tr><th class="rank-cell">#</th><th class="who-cell">Who</th><th class="solved-cell">=</th><th class="penalty-cell">Penalty</th>${problemHeaders}</tr></thead><tbody>${rows}</tbody></table></div>`;
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
  const contests = data.training;
  if (!state.trainingId || !contests.some((item) => item.id === state.trainingId)) state.trainingId = contests[0]?.id;
  const contest = contests.find((item) => item.id === state.trainingId);
  if (!contest) return '<div class="page-shell"><div class="empty-state">暂无训练记录</div></div>';
  return `<div class="page-shell">
      <div class="page-heading"><div><span class="eyebrow">Training</span><h1>训练记录</h1><p>暑期集训与校内训练赛记录。</p></div><div class="source-status"><i></i><span>${contest.demo ? "演示数据" : "公开榜单"}</span></div></div>
      <div class="training-layout">
        <aside class="training-dates" aria-label="训练日期">${contests.map((item) => `<button type="button" data-training-id="${escapeHtml(item.id)}" class="${item.id === contest.id ? "active" : ""}"><span>${escapeHtml(item.year)}</span><span>${escapeHtml(item.date.slice(5))}</span></button>`).join("")}</aside>
        <div><div class="training-title"><h1>${escapeHtml(contest.title)}</h1><p>${escapeHtml(contest.date)}</p></div>${standingsTable(contest)}${rankChart(contest)}</div>
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
  if (route === "training") {
    document.querySelectorAll("[data-training-id]").forEach((button) => button.addEventListener("click", () => {
      state.trainingId = button.dataset.trainingId;
      renderRoute("training");
    }));
  }
}

function renderRoute(route) {
  if (!state.data) return;
  const renderers = { home: homePage, honor: honorPage, rating: ratingPage, training: trainingPage };
  app.innerHTML = renderers[route](state.data);
  document.title = `${route === "home" ? "DLUT CPC" : `${route[0].toUpperCase()}${route.slice(1)} · DLUT CPC`}`;
  bindPageEvents(route);
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
