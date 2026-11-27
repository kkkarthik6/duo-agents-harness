import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/task-map.js', import.meta.url), 'utf8');
const { renderTaskMap, money } = await import('data:text/javascript,' + encodeURIComponent(source));

class Element {
  children = [];
  dataset = {};
  classList = {add() {}, remove() {}};
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
  setAttribute() {}
  remove() {}
  text() { return [this.textContent, ...this.children.map(child => child.text())].filter(Boolean).join('\n'); }
}

test('money shows an approximate amount, a floor for tiny amounts and nothing for no cost', () => {
  assert.equal(money(0.4237), '~$0.42');
  assert.equal(money(0.002), '~<$0.01');
  assert.equal(money(0), '');
  assert.equal(money(undefined), '');
});

test('task cards and the map header show approximate cost', () => {
  const root = new Element();
  globalThis.document = {querySelector: () => ({}), createElement: () => new Element()};
  const node = (id, usd) => ({id, title: id, status: 'completed', outputs: [], assignments: [], cost: usd == null ? undefined : {usd, calls: 2, unpriced: 0}});
  renderTaskMap(root, {nodes: [node('A', 0.5), node('B', 1.25), node('C')]}, {runId: 'cost'});
  const text = root.text();
  assert.match(text, /Task map · 3 tasks · ~\$1\.75/);
  assert.match(text, /~\$0\.50/);
  assert.match(text, /~\$1\.25/);
});
