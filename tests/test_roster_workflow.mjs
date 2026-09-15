import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const element = (extra = {}) => ({handlers: {}, addEventListener(name, handler) {this.handlers[name] = handler;}, ...extra});
const seed = {meta: {}, members: [{id: 66, name: '何泾', school: '大连理工大学'}], pendingHonors: [
  {id: 'historic', team: 'Old Team', event: '2019 ICPC Regional', date: '2019-10-20', medal: '铜牌',
    school: '大连理工大学', expectedMembers: 3, suggestedMembers: ['董霄然', '傅心语', '何泾'], pendingSubmissionCount: 1},
]};
const queue = (status = 'pending') => ({submissions: [{id: 7, honorId: 'historic', team: 'Old Team',
  date: '2019-10-20', event: '2019 ICPC Regional', school: '大连理工大学', medal: '铜牌', status,
  members: [{value: '董霄然', name: '董霄然', newMember: true}, {value: 66, name: '何泾', newMember: false}],
  note: '<script>bad</script>', submittedAt: '2026-09-15'}], total: 1, page: 1, pages: 1, pendingCount: status === 'pending' ? 1 : 0});
const emptyQueue = {submissions: [], total: 0, page: 1, pages: 1, pendingCount: 0};
const response = (body, ok = true, status = 200) => ({ok, status, json: async () => body});

function harness(route, fetcher) {
  let ready = false;
  const nodes = new Map();
  const arrays = new Map();
  const calls = [];
  const context = vm.createContext({
    document: {querySelector: selector => nodes.get(selector) ?? null, querySelectorAll: selector => arrays.get(selector) ?? [],
      addEventListener() {}, createElement: () => element({setAttribute() {}})},
    window: {addEventListener() {}}, location: {pathname: `/${route}`},
    FormData: class {constructor(form) {return new Map(Object.entries(form.fields));}},
    fetch: (path, options) => {
      if (!ready) return new Promise(() => {});
      calls.push({path, options});
      return fetcher(path, options);
    },
  });
  for (const selector of ['#app', '.nav-links', '.nav-toggle', '#footerYear']) nodes.set(selector, element());
  vm.runInContext(source, context);
  vm.runInContext(`state.data = ${JSON.stringify(seed)}; state.guestPendingId = 'historic';
    state.adminSession = {authenticated: true, username: 'admin', csrf: 'test-csrf'};
    state.adminView = 'reviews'; state.adminReviews = ${JSON.stringify(queue())}`, context);
  context.renderRoute = () => {};
  ready = true;
  return {context, nodes, arrays, calls, state: () => vm.runInContext('state', context)};
}

test('visitor page offers submission, not direct confirmation, and scopes existing choices', () => {
  const h = harness('pending', () => {});
  const data = structuredClone(seed);
  data.members.push({id: 67, name: '城市成员', school: '大连理工大学城市学院'});
  const html = h.context.pendingPage(data);
  assert.ok(html.includes('data-guest-honor="historic"'));
  assert.ok(html.includes('提交审核'));
  assert.ok(html.includes('待审核 1 份'));
  assert.ok(html.includes('何泾 · #66'));
  assert.ok(!html.includes('城市成员'));
  assert.ok(!html.includes('adminConfirmMembers'));
  assert.ok(!html.includes('>确认成员</button>'));
});

test('visitor submit handler sends names and selected IDs to the queue only', async () => {
  const h = harness('pending', async path => response(path === '/api/roster-submissions' ? {submissionId: 7, duplicate: false} : seed));
  const button = element({disabled: false});
  const form = element({fields: {honorId: 'historic', member1: '董霄然', member2: '傅心语', member3: '何泾 · #66', note: 'evidence'},
    querySelector: () => button});
  h.nodes.set('#guestRoster', form);
  h.context.bindGuestRosterEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {honorId: 'historic', members: ['董霄然', '傅心语', 66], note: 'evidence'});
  assert.deepEqual(h.calls.map(call => call.path), ['/api/roster-submissions', '/api/site']);
  assert.equal(h.state().guestPendingId, null);
  assert.match(h.state().guestMessage, /等待管理员审核/);
  assert.equal(h.state().data.members.length, 1);
});

test('visitor failed validation preserves input and allows correction', async () => {
  const h = harness('pending', async () => response({error: '同名成员请选择 ID'}, false, 400));
  const button = element({disabled: false});
  let alert;
  const fields = {honorId: 'historic', member1: '甲', member2: '乙', member3: '丙', note: 'evidence'};
  const form = element({fields, querySelector: selector => selector === 'button' ? button : null, prepend: message => {alert = message;}});
  h.nodes.set('#guestRoster', form);
  h.context.bindGuestRosterEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(button.disabled, false);
  assert.equal(h.state().guestPendingId, 'historic');
  assert.deepEqual(form.fields, fields);
  assert.equal(alert.textContent, '同名成员请选择 ID');
  assert.equal(h.calls.length, 1);
});

for (const approve of [true, false]) {
  test(`admin ${approve ? 'approve' : 'reject'} is a single authenticated action`, async () => {
    const h = harness('admin', async path => response(path.includes('review-submission') ? {ok: true} : path === '/api/site' ? seed : emptyQueue));
    const yes = element({dataset: {reviewId: '7', approve: 'true'}});
    const no = element({dataset: {reviewId: '7', approve: 'false'}});
    const actions = {querySelectorAll: () => [yes, no]};
    yes.closest = no.closest = () => actions;
    h.arrays.set('[data-review-id]', [yes, no]);
    h.context.bindAdminEvents();
    await (approve ? yes : no).handlers.click();
    assert.equal(h.calls[0].path, '/api/admin/review-submission');
    assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
    assert.deepEqual(JSON.parse(h.calls[0].options.body), {submissionId: 7, approve});
    assert.equal(h.state().adminReviews.pendingCount, 0);
    assert.equal(h.state().adminError, false);
    assert.match(h.state().adminMessage, approve ? /已通过/ : /正式数据未修改/);
    assert.equal(yes.disabled, true);
    assert.equal(no.disabled, true);
  });
}

test('stale or ambiguous approval is shown as an error and the queue is refreshed', async () => {
  const h = harness('admin', async path => path.includes('review-submission') ? response({error: '名单有同名歧义'}, false, 400) : response(queue()));
  const button = element({dataset: {reviewId: '7', approve: 'true'}});
  button.closest = () => ({querySelectorAll: () => [button]});
  h.arrays.set('[data-review-id]', [button]);
  h.context.bindAdminEvents();
  await button.handlers.click();
  assert.equal(h.state().adminError, true);
  assert.equal(h.state().adminMessage, '名单有同名歧义');
  assert.equal(h.state().data.members.length, 1);
  assert.equal(h.state().adminReviews.total, 1);
});

test('late queue responses cannot overwrite a newer status selection', async () => {
  const pending = [];
  const h = harness('admin', () => new Promise(resolve => pending.push(resolve)));
  const first = h.context.loadAdminReviews();
  vm.runInContext("state.adminReviewStatus = 'approved'", h.context);
  const second = h.context.loadAdminReviews();
  pending[1](response(queue('approved')));
  await second;
  pending[0](response(queue()));
  await first;
  assert.equal(h.state().adminReviews.submissions[0].status, 'approved');
  assert.equal(h.state().adminReviewsLoading, false);
});

test('review history is escaped and has no decision controls', () => {
  const h = harness('admin', () => {});
  for (const status of ['approved', 'rejected', 'superseded']) {
    vm.runInContext(`state.adminReviews = ${JSON.stringify(queue(status))}`, h.context);
    const html = h.context.adminReviewPage();
    assert.ok(!html.includes('data-review-id'));
    assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
    assert.ok(!html.includes('<script>bad</script>'));
  }
});

test('logout clears cached private reviews', async () => {
  const h = harness('admin', async () => response({authenticated: false, enabled: true}));
  await h.context.loadAdminSession();
  assert.equal(h.state().adminReviews, null);
  assert.ok(!h.context.adminPage(seed).includes('private evidence'));
  assert.ok(h.context.adminPage(seed).includes('adminLogin'));
});
