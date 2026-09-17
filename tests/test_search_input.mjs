import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');

function harness(selector, key, route) {
  const timers = new Map();
  let next = 0;
  let renders = 0;
  const element = () => ({value: '', selectionStart: 0, selectionEnd: 0, handlers: {},
    addEventListener(name, handler) {this.handlers[name] = handler;},
    focus() {document.activeElement = this;},
    setSelectionRange(start, end) {this.selectionStart = start; this.selectionEnd = end;}});
  let input = element();
  const document = {querySelector: value => value === selector ? input : element(),
    querySelectorAll: () => [], addEventListener() {}, activeElement: input};
  const context = vm.createContext({document, window: {addEventListener() {},
    setTimeout(callback) {timers.set(++next, callback); return next;}, clearTimeout(id) {timers.delete(id);}},
    fetch: () => new Promise(() => {})});
  vm.runInContext(source, context);
  context.renderRoute = value => {
    assert.equal(value, route);
    renders++;
    input = element();
    input.value = vm.runInContext(`state.${key}`, context);
  };
  context.bindSearchInput(selector, key, route);
  const flush = () => {const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback());};
  return {input: () => input, document, context, flush, renders: () => renders, state: () => vm.runInContext(`state.${key}`, context),
    detach() {input = element();}};
}

for (const [selector, key, route] of [['#memberQuery', 'memberQuery', 'rating'], ['#honorQuery', 'honorQuery', 'honor'], ['#pendingQuery', 'pendingQuery', 'pending'], ['#resourceQuery', 'resourceQuery', 'resources']]) {
  test(`${route}: IME stays attached during composition and searches committed Chinese text`, () => {
    const h = harness(selector, key, route);
    const input = h.input();
    input.value = 'c'; input.handlers.input({isComposing: false});
    input.handlers.compositionstart();
    input.value = 'chen'; input.handlers.input({isComposing: true});
    h.flush();
    assert.equal(h.renders(), 0);
    assert.equal(h.input(), input);
    input.value = '陈嘉佑'; input.selectionStart = 3; input.selectionEnd = 3;
    input.handlers.compositionend();
    input.handlers.input({isComposing: false});
    h.flush();
    assert.equal(h.renders(), 1);
    assert.equal(h.state(), '陈嘉佑');
    assert.equal(h.document.activeElement, h.input());
    assert.equal(h.input().selectionStart, 3);
  });
}

test('stale search callbacks do not navigate back after the page changes', () => {
  const h = harness('#memberQuery', 'memberQuery', 'rating');
  h.input().handlers.input({isComposing: false});
  h.detach(); h.flush();
  assert.equal(h.renders(), 0);
});
