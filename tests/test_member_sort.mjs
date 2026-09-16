import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const element = () => ({ addEventListener() {} });
const context = vm.createContext({
  document: { querySelector: element, querySelectorAll: () => [], addEventListener() {} },
  window: { addEventListener() {} },
  fetch: () => new Promise(() => {}),
});
vm.runInContext(readFileSync(new URL("../web/app.js", import.meta.url), "utf8"), context);
const compare = context.compareMemberMedals;
const member = (name, gold, silver, bronze, iron) => ({ name, medals: { gold, silver, bronze, iron } });

test('annual medal chart groups three bars per year and excludes iron from chart and scale', () => {
  const summary = [{year: 2025, gold: 1, silver: 2, bronze: 3, iron: 100},
    {year: 2026, gold: 2, silver: 1, bronze: 2, iron: 200}];
  const html = context.medalChart(summary);
  assert.equal((html.match(/class="medal-bar"/g) || []).length, 6);
  assert.equal((html.match(/<rect /g) || []).length, 6);
  assert.ok(!html.includes('<polyline'));
  assert.ok(!html.includes('<circle'));
  assert.ok(html.includes('年度奖牌数量柱状图'));
  assert.ok(html.includes('2025 年铜牌：3 枚'));
  assert.ok(!html.includes('铁牌'));
  assert.equal(html, context.medalChart(summary.map(({iron, ...item}) => item)));
});

test('annual medal chart supports stacked cumulative totals without counting iron', () => {
  const summary = [{year: 2025, gold: 2, silver: 3, bronze: 4, iron: 99}];
  const html = context.medalChart(summary, 'stacked');
  assert.equal((html.match(/class="medal-bar medal-stack-segment"/g) || []).length, 3);
  assert.ok(html.includes('年度奖牌累计柱状图'));
  assert.ok(html.includes('金+银：5 枚'));
  assert.ok(html.includes('金+银+铜：9 枚'));
  assert.ok(!html.includes('99'));
  const rects = [...html.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)]
    .map(match => match.slice(1).map(Number));
  assert.equal(rects.length, 3);
  assert.ok(rects.every(([x]) => x === rects[0][0]));
  assert.ok(Math.abs(rects.reduce((sum, rect) => sum + rect[3], 0) - (292 - rects[2][1])) < 0.00001);
});

test('home page exposes an accessible grouped and cumulative chart switch', () => {
  const data = {meta: {updatedAt: '2026-09-16', firstYear: 2018, bestRank: 1, memberCoverage: 50}, honors: [],
    medalSummary: [{year: 2025, gold: 1, silver: 2, bronze: 3}]};
  vm.runInContext("state.medalChartMode = 'stacked'", context);
  const html = context.homePage(data);
  assert.ok(html.includes('aria-label="奖牌图表显示方式"'));
  assert.ok(html.includes('data-chart-mode="grouped" aria-pressed="false"'));
  assert.ok(html.includes('data-chart-mode="stacked" aria-pressed="true"'));
  assert.ok(html.includes('年度奖牌累计柱状图'));
  vm.runInContext("state.medalChartMode = 'grouped'", context);
});

test('annual bar chart handles no years, a single year, zero awards and long histories', () => {
  for (const summary of [[], [{year: 2000, gold: 0, silver: 0, bronze: 0}],
    Array.from({length: 50}, (_, index) => ({year: 1977 + index, gold: index % 4, silver: 3, bronze: 8}))]) {
    const html = context.medalChart(summary);
    assert.equal((html.match(/<rect /g) || []).length, summary.length * 3);
    assert.ok(!/NaN|Infinity/.test(html));
    const width = Number(html.match(/viewBox="0 0 ([\d.]+) /)[1]);
    for (const rect of html.matchAll(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/g)) {
      const [x, y, barWidth, barHeight] = rect.slice(1).map(Number);
      assert.ok(x >= 44 && x + barWidth <= width - 32);
      assert.ok(y >= 30 && barHeight >= 0 && y + barHeight <= 292.00001);
    }
  }
});

test('silver and iron text use clearly different hues with readable contrast', () => {
  const css = readFileSync(new URL('../web/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.medal\.silver\s*\{\s*color: #4f6b88;/);
  assert.match(css, /\.member-medals \.silver\s*\{\s*color: #4f6b88;/);
  assert.match(css, /\.medal\.iron,[\s\S]*?color: #8b3a46;/);
  const luminance = hex => {
    const rgb = hex.match(/../g).map(part => parseInt(part, 16) / 255)
      .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  };
  for (const hex of ['4f6b88', '8b3a46']) {
    assert.ok((luminance('e8f2fa') + 0.05) / (luminance(hex) + 0.05) >= 4.5);
  }
});

test('result iron labels are transparent until selected without hiding member counts', () => {
  const css = readFileSync(new URL('../web/styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.result-table \.medal\.iron\s*\{\s*color: transparent;/);
  assert.match(css, /\.result-table \.medal\.iron::selection\s*\{\s*color: #8b3a46;\s*background-color: #c5e2f4;/);
  const html = context.honorPage({honors: [{date:'2025-11-30',event:'CCPC 重庆站',series:'CCPC',location:'重庆',
    team:'队伍',school:'大连理工大学',members:[],medal:'铁牌',source:{}}],pendingHonors:[]});
  assert.match(html, /honor-table result-table/);
  assert.match(html, /class="medal iron">铁牌<\/td>/);
  assert.match(context.honorRows([{date:'2025-11-30',members:[],medal:'铁牌',source:{}}]), /class="medal iron">铁牌/);
});

test('account management lists main and secondary accounts and scopes confirmation to one account', () => {
  const data = {members: [{id: 1, name: '杨君泓', accounts: {codeforces: [
    {handle: 'Farewell', rating: 1551, maxRating: 1595}, {handle: 'Other', rating: 1400, maxRating: 1500}]}},
    {id: 2, name: '其他成员', accounts: {codeforces: [{handle: 'Else', rating: 1000, maxRating: 1200}]}}]};
  vm.runInContext("state.adminAccountMember = '1'; state.adminAccountEdit = {memberId: 1, handle: 'Farewell'}; state.adminAccountDelete = {memberId: 1, handle: 'Other'}", context);
  const html = context.adminAccountPage(data);
  assert.ok(html.includes('name="oldHandle" type="hidden" value="Farewell"'));
  assert.ok(html.includes('>主号</td>'));
  assert.ok(html.includes('>副号</td>'));
  assert.ok(html.includes('data-account-delete="confirm" data-member-id="1" data-handle="Other"'));
  assert.ok(!html.includes('data-handle="Else"'));
  vm.runInContext("state.adminAccountMember = 'all'; state.adminAccountEdit = null; state.adminAccountDelete = null", context);
});

test("gold, silver, bronze outrank a lower iron count", () => {
  assert.ok(compare(member("A", 2, 0, 0, 99), member("B", 1, 99, 99, 0)) < 0);
  assert.ok(compare(member("A", 1, 2, 0, 99), member("B", 1, 1, 99, 0)) < 0);
  assert.ok(compare(member("A", 1, 1, 2, 99), member("B", 1, 1, 1, 0)) < 0);
});

test("fewer iron results rank first when gold, silver, bronze tie", () => {
  const sorted = [member("A", 1, 2, 3, 5), member("B", 1, 2, 3, 0), member("C", 1, 2, 3, 2)].sort(compare);
  assert.deepEqual(sorted.map(m => m.name), ["B", "C", "A"]);
});

test("an unknown iron count is not treated as zero", () => {
  assert.ok(compare(member("A", 1, 2, 3, 4), member("B", 1, 2, 3, null)) < 0);
  assert.ok(Number.isFinite(compare(member("A", 1, 2, 3, null), member("B", 1, 2, 3, null))));
});

test("all standard Codeforces rating color bands are represented", () => {
  for (const [rating, color] of [[null, 'gray'], [0, 'gray'], [1199, 'gray'], [1200, 'green'], [1400, 'cyan'], [1600, 'blue'], [1900, 'purple'], [2100, 'orange'], [2400, 'red']]) {
    assert.equal(context.ratingColor(rating), color);
  }
});

test("secondary account and registration alias searches retain their member", () => {
  const data = {meta: {memberCount: 1, honorsWithMembers: 0}, members: [{id: 1, name: '卜方昱', aliases: ['Fangyu Bu'],
    handles: {codeforces: {handle: 'primary', rating: 1800, maxRating: 2000}},
    accounts: {codeforces: [{handle: 'primary', rating: 1800, maxRating: 2000}, {handle: 'secondary', rating: 1700, maxRating: 1900}]}}]};
  for (const query of ['secondary', 'fangyu bu']) {
    vm.runInContext(`state.memberQuery = ${JSON.stringify(query)}`, context);
    const html = context.ratingPage(data);
    assert.ok(html.includes('卜方昱'));
    assert.ok(html.includes('CF 主号'));
    assert.ok(html.includes('CF 副号'));
    assert.ok(html.indexOf('<th>最高 Rating</th>') < html.indexOf('<th>当前 Rating</th>'));
  }
});

test("independent school filters do not mix same-name members", () => {
  const data = {meta: {}, members: [{id: 1, name: '本部选手', school: '大连理工大学'},
    {id: 2, name: '城市选手', school: '大连理工大学城市学院'}]};
  vm.runInContext("state.memberQuery = ''; state.memberSchool = '大连理工大学城市学院'", context);
  const html = context.ratingPage(data);
  assert.ok(html.includes('城市选手'));
  assert.ok(!html.includes('本部选手'));
  vm.runInContext("state.memberSchool = 'all'", context);
});

test("pending awards are read-only for visitors and filter by independent school", () => {
  const data = {pendingHonors: [{id:'a', date:'2018-01-01', event:'ICPC Regional', team:'本部队', school:'大连理工大学', medal:'金牌'},
    {id:'b', date:'2018-01-01', event:'ICPC Regional', team:'城市队', school:'大连理工大学城市学院', medal:'银牌'}]};
  vm.runInContext("state.pendingQuery = ''; state.pendingSchool = '大连理工大学城市学院'", context);
  const html = context.pendingPage(data);
  assert.ok(html.includes('城市队'));
  assert.ok(!html.includes('本部队'));
  assert.ok(!html.includes('data-pending-id'));
  assert.ok(context.pendingTable(data, true).includes('data-pending-id="b"'));
  vm.runInContext("state.pendingSchool = 'all'", context);
});

test("roster confirmation accepts plain names while preserving selected member IDs", () => {
  const data = {members: [{id: 66, name: '何泾', school: '大连理工大学'},
    {id: 67, name: '何泾', school: '大连理工大学城市学院'}]};
  vm.runInContext(`state.data = ${JSON.stringify(data)}`, context);
  assert.equal(context.adminRosterMember(' 董霄然 '), '董霄然');
  assert.equal(context.adminRosterMember('傅心语'), '傅心语');
  assert.equal(context.adminRosterMember('何泾'), '何泾');
  assert.equal(context.adminRosterMember(' 何泾 · #66 '), 66);
  assert.equal(context.adminRosterMember('何泾 · #67 · 大连理工大学城市学院'), 67);
  assert.throws(() => context.adminMemberId('董霄然'), /请选择名单中的成员/);
});

test("historical roster suggestions are escaped and prefilled without auto submission", () => {
  const data = {members: [], pendingHonors: [{id: 'historic', team: 'Old Team', date: '2018-01-01',
    event: 'ICPC Regional', school: '大连理工大学', medal: '金牌', expectedMembers: 3,
    suggestedMembers: ['董霄然', 'O\"Brien', '<name>']}]};
  vm.runInContext("state.adminSession = {authenticated: true, username: 'admin'}; state.adminView = 'pending'; state.pendingId = 'historic'; state.adminMessage = ''", context);
  const html = context.adminPage(data);
  assert.ok(html.includes('value="董霄然"'));
  assert.ok(html.includes('value="O&quot;Brien"'));
  assert.ok(html.includes('value="&lt;name&gt;"'));
  assert.ok(html.includes('确认成员'));
});
