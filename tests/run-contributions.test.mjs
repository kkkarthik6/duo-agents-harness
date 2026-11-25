import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/task-map.js', import.meta.url), 'utf8');
const { renderTaskMap } = await import('data:text/javascript,' + encodeURIComponent(source));

class Element {
  children = [];
  dataset = {};
  classList = {add() {}};
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
  text() { return [this.textContent, ...this.children.map(child => child.text())].filter(Boolean).join('\n'); }
}

test('run map shows the latest outcome, earlier reviews and waiting dependencies', () => {
  const root = new Element();
  globalThis.document = {querySelector:() => ({}), createElement:() => new Element()};
  try {
    const options = {runId:'run', globalGoal:'Deliver the export tool', currentGoal:'3. Prepare independent checks', contributions:[
      {kind:'critique', n:2, outcomes:{Save:'Retry failed.'}},
      {kind:'critique', n:4, outcomes:{Save:'Retry passed.'}, next:'Verify reopening.'}
    ], packages:[
      {id:'P2', title:'Import', status:'blocked', retry:'Input arrives'},
      {id:'P4', title:'Export', status:'pending', depends:['P2']}
    ]};
    renderTaskMap(root, {nodes:[]}, options);
    const [summary, history] = root.text().split('Build review history');
    assert.match(summary, /Global goal.*Deliver the export tool.*Current goal.*3. Prepare independent checks/s);
    assert.match(summary, /Save · Retry passed/);
    assert.doesNotMatch(summary, /Retry failed/);
    assert.match(summary, /Waiting on P2/);
    assert.match(history, /Review 2.*Retry failed/s);
    assert.match(history, /Review 4.*Verify reopening/s);
    renderTaskMap(root, {nodes:[]}, {runId:'quick', contributions:[
      {kind:'build', n:1, summary:'Added save button.'}
    ]});
    assert.match(root.text(), /Builder reported · Added save button/);
    assert.doesNotMatch(root.text(), /Retry passed/);
  } finally {
    delete globalThis.document;
  }
});
