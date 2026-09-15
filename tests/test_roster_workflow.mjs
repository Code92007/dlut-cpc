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

test('account deletion requires a separate confirmation and sends only the selected binding', async () => {
  const h = harness('admin', async path => response(path === '/api/site' ? seed : {ok: true}));
  const button = element({dataset: {memberId: '66', handle: 'Wrong', accountDelete: 'ask'}});
  button.closest = () => ({querySelectorAll: () => [button]});
  h.arrays.set('[data-account-delete]', [button]);
  h.context.bindAdminEvents();
  await button.handlers.click();
  assert.equal(h.calls.length, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(h.state().adminAccountDelete)), {memberId: 66, handle: 'Wrong'});
  button.dataset.accountDelete = 'confirm';
  await button.handlers.click();
  assert.equal(h.calls[0].path, '/api/admin/account-delete');
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {memberId: 66, handle: 'Wrong'});
  assert.equal(h.state().adminAccountDelete, null);
});

test('visitor account form offers all independent groups and escapes saved input', () => {
  const h = harness('rating', () => {});
  h.state().guestAccountOpen = true;
  h.state().guestAccountDraft = {member: '何泾 · #66', handle: 'Example', note: '<script>bad</script>'};
  const html = h.context.guestAccountForm({...seed, members: [...seed.members, {id: 67, name: '城市成员', school: '大连理工大学城市学院'}]});
  assert.ok(html.includes('提交审核'));
  assert.ok(html.includes('城市成员 · #67 · 大连理工大学城市学院'));
  assert.ok(html.includes('value="何泾 · #66"'));
  assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!html.includes('<script>bad</script>'));
});

test('visitor account submission sends only member ID, handle and note without modifying public accounts', async () => {
  const h = harness('rating', async () => response({ok: true, submissionId: 12}));
  h.state().guestAccountOpen = true;
  const button = element();
  const form = element({fields: {member: '何泾 · #66', handle: ' Example ', note: 'evidence'}, querySelector: () => button});
  h.nodes.set('#guestAccount', form);
  h.context.bindGuestAccountEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/api/account-submissions');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {memberId: 66, handle: 'Example', note: 'evidence'});
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], undefined);
  assert.equal(h.state().guestAccountOpen, false);
  assert.match(h.state().guestAccountMessage, /等待管理员审核/);
  assert.equal(h.state().data.members[0].accounts, undefined);
});

test('failed guest account submission preserves draft and re-enables submit', async () => {
  const h = harness('rating', async () => response({error: '该账号已绑定其他成员'}, false, 400));
  h.state().guestAccountOpen = true;
  const button = element({disabled: false});
  let alert;
  const fields = {member: '何泾 · #66', handle: 'Taken', note: 'evidence'};
  const form = element({fields, querySelector: selector => selector === 'button' ? button : null, prepend: value => {alert = value;}});
  h.nodes.set('#guestAccount', form);
  h.context.bindGuestAccountEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(button.disabled, false);
  assert.equal(h.state().guestAccountOpen, true);
  assert.deepEqual(JSON.parse(JSON.stringify(h.state().guestAccountDraft)), fields);
  assert.equal(alert.textContent, '该账号已绑定其他成员');
});

test('guest account submission rejects free text instead of guessing a same-name member', async () => {
  const h = harness('rating', () => {throw new Error('should not send');});
  const button = element();
  const form = element({fields: {member: '何泾', handle: 'Example'}, querySelector: selector => selector === 'button' ? button : null, prepend() {}});
  h.nodes.set('#guestAccount', form);
  h.context.bindGuestAccountEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(h.calls.length, 0);
  assert.match(h.state().guestAccountMessage, /请选择名单/);
});

for (const approve of [true, false]) {
  test(`account ${approve ? 'approval' : 'rejection'} uses its own authenticated endpoint`, async () => {
    const h = harness('admin', async path => response(path === '/api/site' ? seed : path.includes('review-account-submission') ? {ok: true, warning: approve ? 'Rating offline' : null} : emptyQueue));
    const button = element({dataset: {reviewId: '7', reviewKind: 'account', approve: String(approve)}});
    button.closest = () => ({querySelectorAll: () => [button]});
    h.arrays.set('[data-review-id]', [button]);
    h.context.bindAdminEvents();
    await button.handlers.click();
    assert.equal(h.calls[0].path, '/api/admin/review-account-submission');
    assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
    assert.deepEqual(JSON.parse(h.calls[0].options.body), {submissionId: 7, approve});
    assert.match(h.state().adminMessage, approve ? /Rating offline/ : /正式数据未修改/);
  });
}

test('account review shows member, requested handle, existing handles and approval controls', () => {
  const h = harness('admin', () => {});
  h.state().adminReviews = {kind: 'account', submissions: [{id: 4, memberId: 66, memberName: '何泾',
    school: '大连理工大学', handle: 'NewHandle', existingAccounts: ['OldHandle'], note: '<script>bad</script>', status: 'pending'}], total: 1, page: 1, pages: 1};
  const html = h.context.adminReviewPage();
  assert.ok(html.includes('何泾 · CF 账号补充'));
  assert.ok(html.includes('已有账号：OldHandle'));
  assert.ok(html.includes('https://codeforces.com/profile/NewHandle'));
  assert.ok(html.includes('data-review-kind="account"'));
  assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!html.includes('<script>bad</script>'));
});

test('switching review type resets pagination and requests the account queue', async () => {
  const h = harness('admin', async () => response({...emptyQueue, kind: 'account'}));
  const select = element();
  h.nodes.set('#reviewKind', select);
  h.state().adminReviewPage = 4;
  h.context.bindAdminEvents();
  select.handlers.change({target: {value: 'account'}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.state().adminReviewPage, 1);
  assert.ok(h.calls[0].path.includes('kind=account'));
  assert.equal(h.state().adminReviews.kind, 'account');
});

test('account edit sends old and new handles and leaves saved edit mode even when rating sync warns', async () => {
  const h = harness('admin', async path => response(path === '/api/site' ? seed : {ok: true, warning: 'Rating sync unavailable'}));
  const button = element();
  const form = element({fields: {memberId: '66', oldHandle: 'Wrong', handle: 'Correct'}, querySelector: () => button});
  h.nodes.set('#adminEditAccount', form);
  h.state().adminAccountEdit = {memberId: 66, handle: 'Wrong'};
  h.context.bindAdminEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(h.calls[0].path, '/api/admin/account-edit');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {memberId: 66, oldHandle: 'Wrong', handle: 'Correct'});
  assert.equal(h.state().adminAccountEdit, null);
  assert.equal(h.state().adminMessage, 'Rating sync unavailable');
});

test('failed account edit keeps input and re-enables saving', async () => {
  const h = harness('admin', async () => response({error: 'account already belongs to another member'}, false, 400));
  const button = element({disabled: false});
  const fields = {memberId: '66', oldHandle: 'Old', handle: 'Taken'};
  let alert;
  const form = element({fields, querySelector: () => button, before: message => {alert = message;}});
  h.nodes.set('#adminEditAccount', form);
  h.state().adminAccountEdit = {memberId: 66, handle: 'Old'};
  h.context.bindAdminEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(button.disabled, false);
  assert.deepEqual(form.fields, fields);
  assert.equal(h.state().adminAccountEdit.handle, 'Old');
  assert.match(alert.textContent, /another member/);
});

test('local roster edit form preselects exact IDs and excludes untouched public rosters', () => {
  const h = harness('admin', () => {});
  const data = structuredClone(seed);
  data.honors = [{...data.pendingHonors[0], rosterConfirmed: true, rosterEditable: true,
    members: ['董霄然', '傅心语', '何泾'], memberDetails: [{id: 68}, {id: 69}, {id: 66}]},
    {id: 'public', rosterConfirmed: true, rosterEditable: false, date: '2025-01-01', team: 'Untouched Public'}];
  data.members.push({id: 68, name: '董霄然'}, {id: 69, name: '傅心语'});
  h.state().adminRosterId = 'historic';
  const html = h.context.adminRosterPage(data);
  assert.ok(html.includes('value="董霄然 · #68"'));
  assert.ok(html.includes('value="何泾 · #66"'));
  assert.ok(html.includes('保存名单修改'));
  assert.ok(!html.includes('Untouched Public'));
});

test('local roster edit submits mixed IDs and names with admin csrf and refreshes statistics', async () => {
  const h = harness('admin', async path => response(path === '/api/site' ? seed : {ok: true}));
  const button = element();
  const form = element({fields: {honorId: 'historic', member1: '董霄然', member2: '傅心语', member3: '何泾 · #66'},
    querySelector: () => button});
  h.nodes.set('#adminEditMembers', form);
  h.state().adminRosterId = 'historic';
  h.context.bindAdminEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});
  assert.equal(h.calls[0].path, '/api/admin/edit-members');
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {honorId: 'historic', members: ['董霄然', '傅心语', 66]});
  assert.equal(h.state().adminRosterId, null);
  assert.match(h.state().adminMessage, /奖牌统计已更新/);
});

test('starred results display only explicit medal grades and unknown official awards remain pending', () => {
  const h = harness('honor', () => {});
  for (const [honor, label] of [[{official: false, medal: '铁牌'}, ''], [{official: false, medal: '银牌'}, '打星银牌'],
    [{official: true, medal: '', medalPending: true}, '奖项待确认']]) {
    assert.equal(vm.runInContext(`resultMedal(${JSON.stringify(honor)})`, h.context), label);
  }
});

test('unknown award selector is available to admin only for missing formal awards', () => {
  const h = harness('admin', () => {});
  const html = h.context.unknownMedalInput({medalPending: true});
  assert.match(html, /<select name="medal">/);
  assert.match(html, /铁牌/);
  assert.equal(h.context.unknownMedalInput({medalPending: false}), '');
});

for (const [selector, endpoint] of [['#adminConfirmMembers', 'confirm-members'], ['#adminEditMembers', 'edit-members']]) {
  test(`${endpoint}: admin can submit a missing medal with the roster`, async () => {
    const h = harness('admin', async path => response(path === '/api/site' ? seed : {ok: true}));
    const button = element();
    const form = element({fields: {honorId: 'historic', member1: '甲', member2: '乙', member3: '丙', medal: '银牌'}, querySelector: () => button});
    h.nodes.set(selector, form);
    h.context.bindAdminEvents();
    await form.handlers.submit({preventDefault() {}, currentTarget: form});
    assert.equal(h.calls[0].path, `/api/admin/${endpoint}`);
    assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
    assert.equal(JSON.parse(h.calls[0].options.body).medal, '银牌');
  });
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
