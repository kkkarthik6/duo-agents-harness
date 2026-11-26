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

test('task images appear on their node and in the selected task details', () => {
  const root = new Element();
  const all = node => [node, ...node.children.flatMap(all)];
  globalThis.document = {querySelector:() => ({}), createElement:tag => {
    const el = new Element(); el.tag = tag; el.setAttribute = () => {}; return el;
  }};
  try {
    const graph = {nodes:[{id:'art', title:'Reading list', outputs:[]},
      {id:'text', title:'Notes', outputs:[{path:'notes.md'}]}]};
    const options = {runId:'trial', imageUrl:path => '/api/task-image?' + new URLSearchParams({id:'trial', path})};
    renderTaskMap(root, graph, options);
    all(root).find(el => el.dataset.task === 'art').onclick();
    assert.equal(all(root).filter(el => el.tag === 'img').length, 0);
    graph.nodes[0].outputs.push({path:'draft #2.PNG'});
    renderTaskMap(root, graph, options);
    assert.equal(all(root).filter(el => el.tag === 'img').length, 2);
    const preview = all(root.children[0]).find(el => el.tag === 'img');
    assert.equal(preview.src, '/api/task-image?id=trial&path=draft+%232.PNG');
    all(root).find(el => el.dataset.task === 'text').onclick();
    assert.match(root.children[0].text(), /Images.*draft #2.PNG/s);
    graph.nodes[0].outputs.push({path:'References/source.png'}, {path:'Proposed design/original.png'});
    renderTaskMap(root, graph, options);
    assert.match(root.children[0].text(), /Proposed design.*original.png/s);
    assert.match(root.children[1].text(), /References.*source.png/s);
  } finally { delete globalThis.document; }
});
