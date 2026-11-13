import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const makeContext = () => {
  const element = () => ({addEventListener() {}});
  const context = vm.createContext({document: {querySelector: element, querySelectorAll: () => [], addEventListener() {}},
    window: {addEventListener() {}}, fetch: () => new Promise(() => {})});
  vm.runInContext(readFileSync(new URL('../web/analytics.js', import.meta.url), 'utf8'), context);
  vm.runInContext(readFileSync(new URL('../web/app.js', import.meta.url), 'utf8'), context);
  return context;
};
const setData = (context, data) => {
  context.fixture = data;
  vm.runInContext('analyticsState.data = fixture', context);
};
const performance = JSON.parse(readFileSync(new URL('../data/team_performances.json', import.meta.url), 'utf8'));

test('all screenshot records render with a source link and independent archive status', () => {
  const c = makeContext();
  setData(c, {performances: performance});
  const html = c.performancePage();
  assert.equal((html.match(/class="problem-rating-number"/g) || []).length, 47);
  assert.ok(html.includes('/assets/team-performance-20261114.png'));
  assert.ok(html.includes('档案暂未匹配'));
  assert.ok(html.includes('仅罚时'));
  vm.runInContext("analyticsState.performanceQuery = '暂无名'; analyticsState.performanceYear = '2022'", c);
  assert.equal(c.performanceRows().length, 4);
  vm.runInContext("analyticsState.performanceStage = '金'", c);
  assert.equal(c.performanceRows().length, 2);
});

test('qualified, provisional and no-data players stay distinct and ties remain tied', () => {
  const c = makeContext();
  const p = (id, name, contests, current, peak) => ({id,name,school:'大连理工大学',contests,current,peak,
    provisional:contests < 3,history:[],band:200,teamCount:1});
  setData(c, {elo:{version:'test',inputHash:'test',minContests:3,skipped:[],coverage:{},players:[
    p(1,'<img onerror=alert(1)>',5,1700,1800),p(2,'B',3,1700,1800),p(3,'暂定',1,1650,null),p(4,'无数据',0,null,null)]}});
  assert.equal(c.eloRows().length, 2);
  let html = c.eloPage();
  assert.equal((html.match(/<td>1<\/td>/g) || []).length, 2);
  assert.ok(!html.includes('<img onerror'));
  assert.ok(html.includes('&lt;img onerror'));
  vm.runInContext("analyticsState.eloScope = 'provisional'", c);
  assert.equal(c.eloRows().length, 1);
  vm.runInContext("analyticsState.eloScope = 'all'", c);
  assert.equal(c.eloRows().length, 4);
  html = c.eloPage();
  assert.ok(html.includes('无数据'));
});

test('one-contest and flat rating charts stay finite and state their time scale', () => {
  const c = makeContext();
  for (const history of [[{rating:1500,date:'2010-11-01',event:'站',delta:0}],
    [{rating:1600,date:'2010-11-01',event:'站',delta:100},{rating:1600,date:'2011-11-01',event:'站',delta:0}]]) {
    const html = c.eloChart({name:'A',contests:history.length,lastDate:history.at(-1).date,history});
    assert.ok(!/NaN|Infinity/.test(html));
    assert.ok(html.includes('不是等距时间轴'));
  }
});

test('new routes expose loading errors with a retry, and have SPA navigation', () => {
  const c = makeContext();
  vm.runInContext("analyticsState.error = '<error>'", c);
  assert.ok(c.eloPage().includes('&lt;error&gt;'));
  assert.ok(c.performancePage().includes('data-analytics-retry'));
  const index = readFileSync(new URL('../web/index.html', import.meta.url),'utf8');
  assert.ok(index.includes('href="/elo" data-route="elo"'));
  assert.ok(index.includes('href="/performance" data-route="performance"'));
});
