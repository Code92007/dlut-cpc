import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const element = extra => ({handlers: {}, addEventListener(name, handler) { this.handlers[name] = handler; }, ...extra});
const queue = {items: [{id: 9, honor_id: 'existing', provider: 'rankland', status: 'pending', fields: ['rank', 'team'],
  before: {team: 'Old Team', rank: '10 / 100'}, after: {team: '<script>bad</script>', rank: '11 / 100', date: '2026-10-18', event: '西安站'}}],
  total: 1, pendingCount: 1, page: 1, pages: 1, sync: {status: 'ok', lastFinishedAt: '2026-10-23T01:00:00+00:00', summary: {added: 7, diffsCreated: 1}}};

function harness() {
  const calls = [];
  const arrays = new Map();
  const nodes = new Map([['#app', element()], ['.nav-links', element()], ['.nav-toggle', element()], ['#footerYear', element()]]);
  let ready = false;
  const context = vm.createContext({
    document: {querySelector: selector => nodes.get(selector) ?? null, querySelectorAll: selector => arrays.get(selector) ?? [], addEventListener() {}},
    window: {addEventListener() {}}, location: {pathname: '/admin'},
    fetch: async (path, options) => {
      if (!ready) return new Promise(() => {});
      calls.push({path, options});
      return {ok: true, json: async () => path.includes('result-diffs?') ? queue : path === '/api/site' ? {meta: {}} : {ok: true}};
    },
  });
  vm.runInContext(source, context);
  vm.runInContext(`state.adminSession = {authenticated: true, csrf: 'csrf-test'}; state.adminView = 'diffs'; state.adminDiffs = ${JSON.stringify(queue)}`, context);
  context.renderRoute = () => {};
  ready = true;
  return {context, calls, arrays, state: () => vm.runInContext('state', context)};
}

test('diff view escapes source content and shows old A versus incoming B', () => {
  const fixture = harness();
  const html = fixture.context.adminDiffPage();
  assert.ok(html.includes('10 / 100'));
  assert.ok(html.includes('11 / 100'));
  assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!html.includes('<script>bad</script>'));
  assert.ok(html.includes('A · 保留网站当前值'));
  assert.ok(html.includes('B · 接受来源变更'));
});

for (const choice of ['a', 'b']) {
  test(`choice ${choice} needs confirmation and uses the protected admin request`, async () => {
    const fixture = harness();
    const button = element({dataset: {diffId: '9', diffChoice: choice, diffConfirm: 'false'}});
    fixture.arrays.set('[data-diff-choice]', [button]);
    fixture.context.bindAdminEvents();
    await button.handlers.click();
    assert.equal(fixture.calls.length, 0);
    assert.equal(fixture.state().adminDiffDecision.choice, choice);
    button.dataset.diffConfirm = 'true';
    await button.handlers.click();
    assert.equal(fixture.calls[0].path, '/api/admin/resolve-result-diff');
    assert.equal(fixture.calls[0].options.headers['X-CSRF-Token'], 'csrf-test');
    assert.deepEqual(JSON.parse(fixture.calls[0].options.body), {diffId: 9, choice});
    assert.equal(fixture.state().adminDiffDecision, null);
  });
}
