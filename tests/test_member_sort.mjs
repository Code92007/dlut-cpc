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
