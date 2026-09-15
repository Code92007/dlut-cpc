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
