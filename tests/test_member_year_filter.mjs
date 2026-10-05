import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

function setup() {
  const context = vm.createContext({document: {querySelector: () => ({addEventListener(){}}), querySelectorAll: () => [], addEventListener(){}}, window: {addEventListener(){}}, fetch: () => new Promise(() => {})});
  vm.runInContext(readFileSync(new URL('../web/app.js', import.meta.url), 'utf8'), context);
  const member = (name, participationYears, status = 'alumni') => ({name, participationYears, status, firstYear: 2018, lastYear: 2024, teams: ['区间外队伍'], medals: {gold: 9}, honorCount: 9});
  const data = {meta: {}, members: [member('跨年成员', [2018, 2024]), member('区间成员', [2020], 'current'), member('无记录成员', [])]};
  const render = (from = '', to = '', status = 'all') => {
    context.bounds = {from, to, status};
    vm.runInContext('state.memberYearFrom = bounds.from; state.memberYearTo = bounds.to; state.memberStatus = bounds.status;', context);
    return context.ratingPage(data);
  };
  return {data, render};
}

test('year filter uses actual participation, includes bounds, and retains lifetime results', () => {
  const {render, data} = setup();
  const original = JSON.stringify(data);
  let html = render('2019', '2021');
  assert.ok(html.includes('<strong title="">区间成员</strong>'));
  assert.ok(!html.includes('<strong title="">跨年成员</strong>'));
  assert.ok(!html.includes('<strong title="">无记录成员</strong>'));
  html = render('2024', '2024');
  assert.ok(html.includes('<strong title="">跨年成员</strong>'));
  assert.ok(html.includes('金 9'));
  assert.ok(html.includes('2018–2024'));
  assert.ok(html.includes('区间外队伍'));
  assert.equal(JSON.stringify(data), original);
});

test('unselected and open bounds, status intersection, and reversed bounds', () => {
  const {render} = setup();
  assert.ok(render().includes('3 位成员'));
  assert.ok(render('', '2018').includes('1 位成员'));
  assert.ok(render('2024', '').includes('1 位成员'));
  assert.ok(render('2024', '2024', 'current').includes('0 位成员'));
  const html = render('2024', '2018');
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes('起始年份不能晚于截止年份'));
  assert.ok(html.includes('0 位成员'));
});
