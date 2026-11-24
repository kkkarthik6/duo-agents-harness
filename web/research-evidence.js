/* Paper evidence belongs to the researcher; render exact records without HTML injection. */
export function createEvidenceCard(state, doc = document) {
  const gaps = state?.research_evidence_needed;
  if (!Array.isArray(gaps) || !gaps.length) return null;
  const el = (tag, text) => {
    const node = doc.createElement(tag);
    if (text != null) node.textContent = String(text);
    return node;
  };
  const card = el('details');
  card.className = 'stopwhy research-evidence';
  card.open = Boolean(state.paused || ['waiting', 'stopped', 'error'].includes(state.status));
  card.append(el('summary', `Research evidence: ${gaps.length} items remain`));
  card.append(el('p', 'Duo collects these records. You only need to act if a specific source needs access or a decision.'));
  const scroll = el('div');
  scroll.style.cssText = 'max-height:22rem;overflow:auto';
  const table = el('table');
  const header = el('tr');
  for (const label of ['Paper or search', 'Evidence needed', 'Researcher action']) header.append(el('th', label));
  table.append(header);
  for (const gap of gaps) {
    const row = el('tr');
    const source = el('td');
    source.append(el('strong', `${gap.id} — ${gap.title}`));
    try {
      const url = new URL(gap.url);
      if (url.protocol === 'https:') {
        const link = el('a', 'Open source');
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        source.append(el('br'), link);
      }
    } catch {}
    const needed = el('td', gap.requirement);
    for (const path of gap.expected_paths || []) needed.append(el('br'), el('code', path));
    row.append(source, needed, el('td', gap.action));
    table.append(row);
  }
  scroll.append(table);
  card.append(scroll);
  if (state.research_evidence_report) card.append(el('p', `Saved list: ${state.research_evidence_report}`));
  return card;
}
