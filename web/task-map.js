/* Task records in normal document flow. No overlays and no HTML from agent text. */
const roots = new WeakMap();
const labels = {ready:'Ready', working:'Working', reviewing_previous:'Reviewing previous task',
  partial:'Partial', completed:'Complete', stage_completed:'Stage complete', submitted:'Submitted for review',
  needs_work:'Work remains', review_pending:'Review pending'};
const make = (tag, text, className) => {
  const value = document.createElement(tag);
  if (text != null) value.textContent = String(text);
  if (className) value.className = className;
  return value;
};
const isImage = output => /\.(?:png|jpe?g|gif|webp)$/i.test(output.path);
const when = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString() : 'No saved update';

function styles() {
  if (document.querySelector('link[data-task-map]')) return;
  const link = document.createElement('link'); link.rel = 'stylesheet'; link.dataset.taskMap = '';
  link.href = new URL('./task-map.css', import.meta.url).href; document.head.append(link);
}

export function imageGallery(title, images) {
  if (!images.length) return null;
  styles();
  const section = make('section', null, 'duo-image-gallery');
  section.append(make('h2', title));
  const grid = make('div', null, 'duo-image-grid');
  for (const {path, url, project} of images) {
    const link = make('a');
    link.href = url; link.target = '_blank'; link.rel = 'noopener';
    const name = path.split('/').pop();
    link.title = 'Open full image · ' + path;
    const image = make('img');
    image.alt = ''; image.loading = 'lazy';
    image.onerror = () => image.replaceWith(make('span', 'Preview unavailable'));
    image.src = url;
    link.append(image, make('strong', name));
    if (project) link.append(make('small', project));
    grid.append(link);
  }
  section.append(grid);
  return section;
}

export function renderTaskMap(root, graph, options = {}) {
  if (!root) return;
  styles(); root.classList.add('duo-taskmap');
  let state = roots.get(root);
  if (!state || state.run !== options.runId) {
    state = {run: options.runId, selected: null, open: new Set(['map','nodes', ...(options.runStatus === 'running' ? ['goals','contributions'] : [])]), files: new Map(), errors: null, errorsBusy: false};
    roots.set(root, state);
  }
  const signature = JSON.stringify([graph, options.helper, options.runStatus, options.globalGoal, options.goalUpdates, options.currentGoal, options.contributions, options.packages, options.checks]);
  if (state.signature === signature && !state.dirty) return;
  state.signature = signature; state.dirty = false;
  if (state.version !== graph?.updated) {
    state.version = graph?.updated; state.errors = null; state.diagnosticError = null;
  }
  state.graph = graph || {nodes:[], progress:[]}; state.options = options;
  const nodes = state.graph.nodes || [];
  if (!nodes.some(n => n.id === state.selected)) state.selected = null;
  const redraw = () => { state.dirty = true; renderTaskMap(root, state.graph, state.options); };
  const block = (id, title, build) => {
    const details = make('details', null, 'taskmap-section'); details.dataset.section = id;
    details.open = state.open.has(id);
    details.append(make('summary', title));
    const body = make('div', null, 'taskmap-section-body'); details.append(body);
    build(body);
    details.addEventListener('toggle', () => {
      if (!details.isConnected) return;
      if (details.open) state.open.add(id); else state.open.delete(id);
      if (id === 'diagnostics' && details.open && state.errors === null && !state.errorsBusy && !state.diagnosticError) loadErrors();
    });
    return details;
  };
  const pre = text => make('pre', text == null ? 'No record is available.' : typeof text === 'string' ? text : JSON.stringify(text, null, 2), 'taskmap-text');
  const action = (title, run) => {
    const button = make('button', title, 'taskmap-action'); button.type = 'button'; button.onclick = run; return button;
  };
  const select = id => {
    state.selected = id; state.open.add('inspector'); state.open.add('task-context'); state.errors = null;
    state.open.delete('diagnostics'); state.diagnosticError = null; redraw();
  };
  const file = (container, path, caption) => {
    if (!path) return;
    const item = make('div', null, 'taskmap-file');
    const button = action(caption || path, async () => {
      state.files.set(path, {loading:true}); redraw();
      try { state.files.set(path, await options.readFile(path)); }
      catch (error) { state.files.set(path, {error:error.message}); }
      state.open.add('file:' + path); redraw();
    });
    item.append(button); container.append(item);
    const loaded = state.files.get(path);
    if (loaded) item.append(block('file:' + path, 'File preview', body => {
      if (loaded.loading) body.append(make('p', 'Loading the complete file…'));
      else if (loaded.error) body.append(make('p', loaded.error));
      else if (loaded.kind === 'binary') body.append(make('p', `Binary file · ${loaded.size} bytes`), pre(loaded.path));
      else body.append(pre(loaded.content));
    }));
  };
  async function loadErrors() {
    state.errorsBusy = true; state.diagnosticError = null; redraw();
    try { state.errors = (await options.readErrors()).errors || []; }
    catch (error) { state.diagnosticError = error.message; }
    state.errorsBusy = false; redraw();
  }
  const panel = block('map', `Task map · ${nodes.length} task${nodes.length === 1 ? '' : 's'}`, body => {
    if (options.globalGoal) {
      body.append(block('goals', 'Global goal and current assignment', content => {
        content.append(make('h4', 'Global goal'), make('p', options.globalGoal));
        for (const update of options.goalUpdates || []) content.append(make('p', 'User update · ' + update));
        if (options.currentGoal) content.append(make('h4', 'Current goal'), make('p', options.currentGoal));
      }));
    }
    const turns = options.contributions || [];
    const packages = options.packages || [];
    if (turns.length || packages.length) body.append(block('contributions', 'What this run contributed', content => {
      const reviews = turns.filter(turn => turn.kind === 'critique');
      const latest = new Map();
      for (const review of reviews) {
        for (const [item, result] of Object.entries(review.outcomes || {})) latest.set(item, result);
      }
      for (const [item, result] of latest) content.append(make('p', item + ' · ' + result));
      const last = turns.at(-1);
      if (last && !Object.keys(last.outcomes || {}).length) content.append(make('p', (last.kind === 'critique' ? 'Reviewer' : 'Builder') + ' reported · ' + last.summary));
      if (last && options.openTurn) content.append(action('Open latest report', () => options.openTurn(last.n)));
      if (last?.next) content.append(make('p', 'Next · ' + last.next));
      const done = new Set(packages.filter(p => p.status === 'done').map(p => p.id));
      const blocked = new Set((options.checks || []).filter(c => c.status === 'blocked').map(c => c.id));
      for (const p of packages) {
        const waiting = [...(p.depends || []).filter(id => !done.has(id)), ...(p.requires || []).filter(id => blocked.has(id))];
        const status = p.status === 'done' ? 'Built' : p.status === 'blocked' ? 'Blocked' :
          waiting.length ? 'Waiting on ' + waiting.join(', ') : 'Pending';
        content.append(make('p', p.id + ' · ' + p.title + ' · ' + status + (p.retry ? ' · ' + p.retry : '')));
      }
      if (reviews.length) content.append(block('cycle-history', 'Build review history', history => {
        for (const review of reviews) {
          history.append(make('h4', 'Review ' + review.n + ' · ' + (review.verdict || 'Recorded')));
          const notes = Object.entries(review.outcomes || {});
          if (!notes.length) history.append(make('p', review.summary));
          for (const [item, result] of notes) history.append(make('p', item + ' · ' + result));
          if (options.openTurn) history.append(action('Open review ' + review.n, () => options.openTurn(review.n)));
          if (review.next) history.append(make('p', 'Next · ' + review.next));
        }
      }));
    }));
    const completion = state.graph.completion_summary;
    if (completion || options.runStatus === 'done') body.append(block('completion', 'Detailed completion handoff', content => {
      const status = completion?.status || 'not_started';
      const ready = ['ready','superseded'].includes(status) ? completion : completion?.previous;
      const descriptions = {writing:'The task is done. A medium summary agent is preparing the detailed handoff.',
        ready:'The detailed handoff includes evidence, reusable files, user actions, and a next-run plan.',
        unavailable:'The task remains done. The summary agent could not finish. Diagnostics are saved separately.',
        interrupted:'Summary preparation was interrupted. The task remains done. You can retry.',
        superseded:'This handoff belongs to an earlier completion. Review the current run before reusing it.'};
      content.append(make('p', descriptions[status] || 'Create the detailed handoff for this completed run.', 'taskmap-hint'));
      if (ready) {
        content.append(make('p', `Version ${ready.version} · ${ready.model} · ${when(ready.generated_at || ready.time)}`, 'taskmap-hint'));
        file(content, ready.path, 'Open detailed summary');
        if(options.openCompletionSummary) content.append(action('Read summary document',options.openCompletionSummary));
        file(content, ready.next_run, 'Open next-run prompt');
        if (options.prepareNextRun) content.append(action('Prepare another run', async () => {
          try { await options.prepareNextRun(ready.next_run); }
          catch (error) { state.completionError=error.message; redraw(); }
        }));
        content.append(make('p', 'Review user actions and edit the next-run goal before starting. This button does not start agents.', 'taskmap-hint'));
      }
      if (options.runStatus === 'done' && options.createCompletionSummary) {
        const button=action(status==='writing' ? 'Preparing detailed handoff…' : ready ? 'Create a new summary version' : 'Create or retry detailed summary', async () => {
          button.disabled=true;
          try { await options.createCompletionSummary(); }
          catch (error) { state.completionError=error.message; redraw(); }
          finally { button.disabled=false; }
        });
        button.disabled=status==='writing'; content.append(button);
      }
      if (state.completionError) content.append(make('p',state.completionError));
    }));
    if (!nodes.length) { body.append(make('p', 'Task records appear when an agent receives an assignment.', 'taskmap-hint')); return; }
    const map = make('div', null, 'taskmap-nodes'); map.setAttribute('aria-label', 'Task nodes');
    for (const node of nodes) {
      const button = make('button', null, 'taskmap-node'); button.type = 'button';
      button.dataset.task = node.id; button.dataset.status = node.status;
      button.setAttribute('aria-pressed', String(node.id === state.selected));
      const header = make('span', null, 'taskmap-node-top');
      header.append(make('span', labels[node.status] || node.status, 'taskmap-badge'),
        make('span', `${node.outputs?.length || 0} ${(node.outputs?.length || 0) === 1 ? 'file' : 'files'}`, 'taskmap-count'));
      button.append(header, make('strong', node.title || node.id, 'taskmap-node-title'));
      button.title = node.id;
      const cover = node.outputs?.find(isImage);
      if (cover && options.imageUrl) {
        const thumb = make('img', null, 'taskmap-thumbnail');
        thumb.alt = '';
        thumb.loading = 'lazy';
        thumb.onerror = () => thumb.remove();
        thumb.src = options.imageUrl(cover.path);
        button.append(thumb);
      }
      if (node.current_role) button.append(make('span', node.current_role.replaceAll('_',' '), 'taskmap-hint'));
      if (node.parent) button.append(make('span', '↳ Continues ' + node.parent, 'taskmap-parent'));
      button.append(make('span', 'Updated ' + when(node.updated), 'taskmap-time'));
      button.onclick = () => select(node.id); map.append(button);
    }
    body.append(block('nodes', 'Task nodes', content => content.append(map)));
    const selected = nodes.find(n => n.id === state.selected);
    if (!selected) return;
    body.append(block('inspector', selected.id + ' · ' + (labels[selected.status] || selected.status), inspector => {
      const line = make('div', null, 'taskmap-toolbar');
      line.append(make('p', selected.title, 'taskmap-title'), action('Close task details', () => { state.selected = null; redraw(); }));
      inspector.append(line);
      const entries = new Map();
      for (const output of selected.inherited_outputs || []) entries.set(output.path, {...output, inherited:true});
      for (const output of selected.outputs || []) entries.set(output.path, output);
      if (selected.parent) inspector.append(action('View parent ' + selected.parent, () => select(selected.parent)));
      if(selected.task_context) inspector.append(block('task-context','Goal, plan, progress, and results',content=>{
        const current=selected.task_context;
        content.append(make('p',current.summary_current?'Summarized task context. Review evidence before treating a claim as accepted.':'This context uses the latest recorded facts. A fresh task summary is still needed.','taskmap-hint'));
        if(options.createSummary) content.append(action('Refresh task context',async()=>{try{await options.createSummary(selected.id);}catch(error){state.summaryError=error.message;redraw();}}));
        for(const [title,value] of [['Goal',current.goal],['Plan',current.plan],['Finished',current.finished],['Doing',current.doing],['Results summary',current.results_summary],['Next steps',current.next_steps]]){
          const section=make('section',null,'taskmap-summary-card');section.append(make('h4',title));
          section.append(make('div',Array.isArray(value)?value.join('\n\n') || 'Not recorded yet.':value || 'Not recorded yet.','taskmap-verbal'));content.append(section);
        }
      }));
      inspector.append(block('summary', 'Subtask summary', content => {
        const top = make('div', null, 'taskmap-toolbar');
        const message = selected.summary_state === 'updating'
          ? 'The summary agent is updating this view. Task workers continue independently.'
          : selected.summary_is_current ? 'Verbal summary based on saved task records and outputs.'
          : 'A current verbal summary is not available yet. Recorded task facts remain available below.';
        top.append(make('p', message, 'taskmap-hint'));
        if (selected.summary_model) {
          const name = selected.summary_model.includes('haiku') ? 'Haiku' : selected.summary_model.includes('terra') ? 'Terra' : selected.summary_model;
          const badge = make('span', 'Summary · ' + name, 'taskmap-badge'); badge.title = selected.summary_model; top.append(badge);
        }
        if (options.createSummary) {
          const button = action(selected.summary_state === 'updating' ? 'Updating summary…' : 'Create or refresh summary', async () => {
            button.disabled = true;
            try { await options.createSummary(selected.id); }
            catch (error) { state.summaryError = error.message; redraw(); }
            finally { button.disabled = false; }
          });
          button.disabled = selected.summary_state === 'updating'; top.append(button);
        }
        content.append(top);
        if (state.summaryError) content.append(make('p', state.summaryError));
        const sections = selected.summary_sections || {};
        const cards = make('div', null, 'taskmap-summary-cards');
        const values = [
          ['Requirement', sections.goal || sections.requirement || selected.task_context?.goal || selected.specification || 'The requirement has not been recorded.'],
          ['Outcomes', sections['results summary'] || sections.outcomes || (selected.verbal_summary && !Object.keys(sections).length ? selected.verbal_summary :
            'Read the saved outputs and review state below. No verbal outcome summary has been saved yet.')],
          ['Next steps', sections['next steps'] || (selected.work_items || []).filter(item => !item.checked).map(item => item.text).join('\n\n') ||
            'Read the current work plan before deciding what remains.'],
        ];
        for (const [title,text] of values) {
          const card = make('section', null, 'taskmap-summary-card');
          card.append(make('h4', title), make('div', text, 'taskmap-verbal')); cards.append(card);
        }
        content.append(cards);
        file(content, selected.summary_file, 'Open summary.md');
      }));
      inspector.append(block('checklist', 'Task checklist · ' + (selected.work_items || []).filter(item=>item.checked).length +
        ' of ' + (selected.work_items || []).length + ' recorded items complete', content => {
        content.append(make('p', 'Agents update these task-specific items in task.md. Checkmarks report progress; review determines acceptance.', 'taskmap-hint'));
        for (const item of selected.work_items || []) {
          const row = make('label', null, 'taskmap-check'); const input = make('input');
          input.type='checkbox'; input.checked=!!item.checked; input.disabled=true;
          row.append(input, make('span', item.text)); content.append(row);
        }
        file(content, selected.task_file, 'Open task.md');
      }));
      inspector.append(block('assignment', 'Assignment and task history', content => {
        content.append(pre(selected.specification));
        for (const [index, assignment] of (selected.assignments || []).entries()) {
          content.append(block('assignment:' + selected.id + ':' + index, `${assignment.role} · ${when(assignment.time)}`, sub => {
            sub.append(pre(assignment.specification)); file(sub, assignment.reply, 'Open saved handoff');
          }));
        }
      }));
      inspector.append(block('outputs', 'Saved outputs and reusable parent work', content => {
        if (!entries.size) content.append(make('p', 'No product files have been recorded yet.', 'taskmap-hint'));
        for (const output of entries.values()) {
          if (isImage(output) && options.imageUrl) {
            const link = make('a', output.path, 'taskmap-action');
            link.href = options.imageUrl(output.path);
            link.target = '_blank';
            link.rel = 'noopener';
            content.append(link);
          } else file(content, output.path);
          content.append(make('p', `${output.inherited ? 'From parent · ' : ''}${output.state || 'Saved'}${output.current?.sha256 && output.current.sha256 !== output.sha256 ? ' · Changed since the recorded version' : ''}`, 'taskmap-hint'));
          if (output.sha256) content.append(block('hash:' + output.path, 'Recorded file version', sub => sub.append(pre(output.sha256))));
        }
        file(content, selected.recovery_plan, 'Open continuation review and work plan');
      }));
      inspector.append(block('requirements', 'Requirement evidence', content => {
        const requirements = {...selected.inherited_requirements, ...selected.requirements};
        if (!Object.keys(requirements).length) content.append(make('p', 'No individual requirement decisions were recorded. Read the review plan before assuming completion.', 'taskmap-hint'));
        for (const [id, requirement] of Object.entries(requirements)) content.append(block('requirement:' + id,
          `${id} · ${requirement.status}`, sub => sub.append(pre(requirement))));
      }));
      inspector.append(block('jobs', 'Shared jobs and inherited job references', content => {
        const references = selected.inherited_jobs || [];
        const jobs = (state.graph.jobs || []).filter(job => job.task_id === selected.id || job.task_id === selected.parent);
        content.append(pre({inherited:references, shared:jobs}));
        content.append(make('p', 'Recorded intent is not proof of process liveness. Agents inspect native job state before starting a replacement.', 'taskmap-hint'));
      }));
      inspector.append(block('diagnostics', 'Diagnostics · separate from task outputs', content => {
        content.append(action('Refresh diagnostics', loadErrors));
        if (state.errorsBusy) content.append(make('p', 'Loading diagnostic records…', 'taskmap-hint'));
        if (state.diagnosticError) content.append(make('p', state.diagnosticError));
        const lineage = new Set([selected.id]); let parent = selected.parent;
        while (parent && !lineage.has(parent)) { lineage.add(parent); parent = nodes.find(n => n.id === parent)?.parent; }
        const records = (state.errors || []).filter(row => lineage.has(row.task_id) || row.applicability?.[selected.id]);
        if (state.errors !== null && !records.length) content.append(make('p', 'No diagnostics are recorded for this task lineage.', 'taskmap-hint'));
        for (const record of records) content.append(block('error:' + record.id,
          `${record.id} · ${record.resolution} · ${record.applicability?.[selected.id] || 'Effect on this task not recorded'}`, sub => {
            sub.append(make('p', `Recorded by ${record.task_id} · ${when(record.time)}`, 'taskmap-hint'));
            sub.append(block('message:' + record.id, 'Complete diagnostic message', raw => raw.append(pre(record.message))));
            sub.append(block('payload:' + record.id, 'Raw tool record', raw => raw.append(pre(record.raw ?? record))));
            sub.append(block('history:' + record.id, 'Status history and evidence', raw => raw.append(pre(record.status_events))));
          }));
      }));
      inspector.append(block('progress', 'Progress updates', content => {
        const progress = (state.graph.progress || []).filter(row => row.node === selected.id);
        if (!progress.length) content.append(make('p', 'No successful progress update has been saved yet.', 'taskmap-hint'));
        for (const update of progress) content.append(block('progress:' + update.id,
          when(update.time) + ' · ' + (update.source || 'Agent report'), sub => sub.append(pre(update.text))));
      }));
      inspector.append(block('helper', 'Helper · explain this task', content => {
        const button = action('Ask Helper about this task', async () => {
          button.disabled = true;
          try { await options.askHelper(selected.id); }
          catch (error) { state.helperError = error.message; redraw(); }
          finally { button.disabled = false; }
        });
        content.append(button);
        if (state.helperError) content.append(make('p', state.helperError));
        const updates = (options.helper || []).filter(row => row.focus === selected.id);
        for (const update of updates) content.append(block('helper:' + update.at, when(update.at), sub => {
          sub.append(make('p', update.q), pre(update.a ?? (['failed','interrupted'].includes(update.status)
            ? 'The answer did not finish. Ask again or retry it in Chat.'
            : 'Helper is reading the task records. Workers continue independently.')));
        }));
      }));
    }));
  });
  const paths = new Set(nodes.flatMap(node => [...(node.outputs || []), ...(node.inherited_outputs || [])]
    .filter(isImage).map(output => output.path)));
  const groups = new Map();
  if (options.imageUrl) for (const path of paths) {
    const folders = path.split('/').slice(0,-1);
    const title = folders.includes('Proposed design') ? 'Proposed design' : folders.includes('References') ? 'References' : 'Images';
    if (!groups.has(title)) groups.set(title, []);
    groups.get(title).push({path, url:options.imageUrl(path)});
  }
  root.replaceChildren(...['Proposed design','References','Images'].filter(title => groups.has(title))
    .map(title => imageGallery(title, groups.get(title))), panel);
}
