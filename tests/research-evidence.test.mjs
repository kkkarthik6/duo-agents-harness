import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const code = await readFile(new URL('../web/research-evidence.js', import.meta.url), 'utf8');
const {createEvidenceCard} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const doc = {createElement(tag) { return {tag, textContent: '', children: [], style: {}, append(...nodes) { this.children.push(...nodes); }}; }};
const flatten = node => [node, ...node.children.flatMap(flatten)];
const gap = {id: 'S1', title: '<script>Paper</script>', url: 'https://example.org/paper',
  requirement: 'Full paper and complete reading record', expected_paths: ['research/sources/S1-full.txt'],
  action: 'Read the saved full text.'};

test('missing evidence shows the title, source, path and researcher action as text', () => {
  const card = createEvidenceCard({paused: true, research_evidence_needed: [gap], research_evidence_report: 'research/evidence-needed.md'}, doc);
  const nodes = flatten(card);
  assert.equal(card.open, true);
  for (const text of [gap.requirement, gap.action, gap.expected_paths[0], `S1 — ${gap.title}`]) {
    assert.ok(nodes.some(n => n.textContent === text));
  }
  assert.equal(nodes.find(n => n.tag === 'a').href, gap.url);
  assert.ok(nodes.some(n => n.textContent.includes('Duo collects these records')));
  assert.ok(nodes.every(n => n.innerHTML === undefined));
});

test('unsafe source URLs do not become links', () => {
  const nodes = flatten(createEvidenceCard({research_evidence_needed: [{...gap, url: 'javascript:alert(1)'}]}, doc));
  assert.ok(!nodes.some(n => n.tag === 'a'));
});

test('no missing evidence produces no card', () => {
  assert.equal(createEvidenceCard({}, doc), null);
  assert.equal(createEvidenceCard({research_evidence_needed: []}, doc), null);
});
