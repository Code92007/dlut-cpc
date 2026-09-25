import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const response = body => ({ok: true, status: 200, json: async () => body});
const element = (extra = {}) => ({
  handlers: {},
  addEventListener(name, handler) {this.handlers[name] = handler;},
  ...extra,
});

function harness(fetcher = () => new Promise(() => {})) {
  let ready = false;
  const nodes = new Map();
  const calls = [];
  const document = {
    querySelector: selector => nodes.get(selector) ?? null,
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => element({setAttribute() {}}),
  };
  for (const selector of ['#app', '.nav-links', '.nav-toggle', '#footerYear']) nodes.set(selector, element());
  const context = vm.createContext({
    document,
    window: {addEventListener() {}},
    location: {pathname: '/admin'},
    FormData: class {constructor(form) {return new Map(Object.entries(form.fields));}},
    fetch: (path, options) => {
      if (!ready) return new Promise(() => {});
      calls.push({path, options});
      return fetcher(path, options);
    },
  });
  vm.runInContext(source, context);
  vm.runInContext(`state.data = {meta: {}, members: [], honors: [], pendingHonors: []};
    state.adminSession = {authenticated: true, username: 'admin', csrf: 'test-csrf'};
    state.adminView = 'resources';`, context);
  context.renderRoute = () => {};
  ready = true;
  return {context, nodes, calls, state: () => vm.runInContext('state', context)};
}

test('resource admin renders each existing category once with its item count', () => {
  const h = harness();
  h.state().adminResources = {items: [
    {id: 1, title: 'A', resourceType: 'link', category: '图论', published: true},
    {id: 2, title: 'B', resourceType: 'link', category: '图论', published: false},
    {id: 3, title: 'C', resourceType: 'link', category: '数学', published: true},
  ]};
  const html = h.context.adminResourcePage();
  assert.ok(html.includes('id="adminResourceCategoryRename"'));
  assert.ok(html.includes('<option value="图论">图论（2）</option>'));
  assert.ok(html.includes('<option value="数学">数学（1）</option>'));
  assert.equal((html.match(/<option value="图论">图论（2）<\/option>/g) || []).length, 1);
});

test('category rename uses the authenticated endpoint and refreshes admin and public resources', async () => {
  const renamedItems = [{id: 1, title: 'A', resourceType: 'link', category: '专题', published: true}];
  const h = harness(async path => {
    if (path === '/api/admin/resource-category-rename') return response({ok: true, updated: 2});
    if (path === '/api/admin/resources') return response({items: renamedItems});
    if (path === '/api/resources') return response({items: renamedItems, categories: [{name: '专题', count: 1}], tags: []});
    throw new Error(`unexpected request: ${path}`);
  });
  h.state().adminResources = {items: [{id: 1, title: 'A', resourceType: 'link', category: '旧分类', published: true}]};
  h.state().resourceCategory = '旧分类';
  const button = element({disabled: false});
  const form = element({fields: {oldName: '旧分类', newName: '专题'}, querySelector: () => button});
  h.nodes.set('#adminResourceCategoryRename', form);

  h.context.bindAdminEvents();
  await form.handlers.submit({preventDefault() {}, currentTarget: form});

  assert.equal(h.calls[0].path, '/api/admin/resource-category-rename');
  assert.equal(h.calls[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(JSON.parse(h.calls[0].options.body), {oldName: '旧分类', newName: '专题'});
  assert.deepEqual(h.calls.slice(1).map(call => call.path).sort(), ['/api/admin/resources', '/api/resources']);
  assert.equal(h.state().resourceCategory, '专题');
  assert.equal(h.state().adminMessage, '分类已重命名 · 2 份资料');
  assert.equal(h.state().adminError, false);
});
