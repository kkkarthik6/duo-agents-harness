/* Feature controls use declared settings and registered components. */
export async function mountFeatures(root, {api, esc, toast, onSaved, onAdd}) {
  root.innerHTML = '<p class="hint" role="status">Loading features...</p>';
  let data, config, roleData;
  try {
    [data, {config}, roleData] = await Promise.all([api('/api/extensions'), api('/api/config'), api('/api/models/roles')]);
  } catch (error) {
    root.innerHTML = `<p role="alert">${esc(error.message)}</p>`;
    return;
  }
  const runtime = [
    ['plan_challenge_rounds', 'Lead–Challenger exchanges (0 disables)', 0, 10, 1, 1],
    ['job_runtime_multiplier', 'Experiment runtime multiplier', 1, 10, 0.1, 1],
    ['long_job_timeout_seconds', 'Unknown runtime limit (hours)', 1 / 60, 720, 'any', 3600],
    ['turn_timeout_seconds', 'Normal agent turn limit (minutes)', 1, 1440, 1, 60],
    ['review_timeout_seconds', 'Review turn limit (minutes)', 1, 1440, 1, 60],
    ['progress_nudge_seconds', 'Progress checkpoint after (minutes)', 0.5, 1440, 0.5, 60],
    ['progress_timeout_seconds', 'Stall recovery after (minutes)', 1, 2880, 1, 60],
    ['task_recovery_limit', 'Recovery attempts', 1, 20, 1, 1],
  ];
  const slotLabels = {home: 'Home', header: 'Header', settings: 'Defaults'};
  const setting = control => {
    const attrs = `data-setting="${esc(control.key)}" aria-label="${esc(control.label)}"`;
    let input;
    if (control.choices) input = `<select ${attrs}>${control.choices.map((v, i) => `<option value="${i}" ${v === control.value ? 'selected' : ''}>${esc(String(v))}</option>`).join('')}</select>`;
    else if (control.type === 'bool') input = `<input type="checkbox" ${attrs} ${control.value ? 'checked' : ''}>`;
    else input = `<input ${attrs} type="${control.type === 'text' ? 'text' : 'number'}" value="${esc(String(control.value))}" ${control.minimum !== undefined ? `min="${control.minimum}"` : ''} ${control.maximum !== undefined ? `max="${control.maximum}"` : ''} ${control.type === 'int' ? 'step="1"' : 'step="any"'}>`;
    return `<label class="field"><span>${esc(control.label)}</span>${input}${control.description ? `<small class="hint">${esc(control.description)}</small>` : ''}</label>`;
  };
  const card = (feature, i) => {
    const state = feature.blocked ? 'Needs repair' : feature.loaded ? 'Enabled' : feature.enabled ? 'Load error' : 'Off';
    const points = [...feature.hooks.map(x => `Event: ${x}`), ...feature.stages.map(x => `${x.name} after ${x.after}`), ...feature.gates.map(x => `Gate: ${x.name} after ${x.after}`)];
    return `<section class="ext-card" data-feature-index="${i}">
      <div class="ext-top"><div><h3>${esc(feature.name.replaceAll('_', ' '))}</h3><span class="hint">${esc(feature.version || '')}</span></div>
      <span class="ext-state">${state}</span><button class="btn" data-toggle ${feature.blocked ? 'disabled' : ''}>${feature.enabled ? 'Turn off' : 'Enable'}</button></div>
      <p>${esc(feature.description || 'Adds features to Duo.')}</p>
      ${feature.problems.length && (feature.enabled || feature.blocked) ? `<p class="ext-error" role="status">${esc(feature.problems.join(' '))}</p>` : ''}
      ${points.length ? `<details><summary>Workflow connections (${points.length})</summary><ul>${points.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>` : ''}
      <form data-feature-form>
      ${feature.controls.length ? `<h4>Settings</h4><div class="ext-grid">${feature.controls.map(setting).join('')}</div>` : ''}
      ${feature.components.length ? `<h4>UI elements</h4>${feature.components.map((c, j) => `<div class="ext-placement" data-component-index="${j}">
        <label><input type="checkbox" data-visible ${c.visible !== false ? 'checked' : ''}> ${esc(c.title || 'Panel')}</label>
        ${c.kind === 'panel' ? `<label>Placement <select data-slot aria-label="Panel placement">${data.extension_points.slots.map(s => `<option value="${esc(s)}" ${c.slot === s ? 'selected' : ''}>${esc(slotLabels[s] || s)}</option>`).join('')}</select></label>` : '<span class="hint">Feature page</span>'}
        <label>Order <input type="number" data-order min="0" max="1000" value="${c.order ?? 100}" aria-label="UI element order"></label>
      </div>`).join('')}` : ''}
      ${feature.controls.length || feature.components.length ? '<button class="btn" type="submit">Save feature</button>' : '<p class="hint">This feature has no declared UI controls.</p>'}
      <p class="hint" data-result role="status" aria-live="polite"></p></form>
    </section>`;
  };
  root.innerHTML = `<div class="ext-head"><div><p class="ext-kicker">WORKSPACE FEATURES</p><h2>Extend Duo</h2><p>Add screens, controls, and workflow steps through features.</p></div><button class="btn primary" id="extAdd">Add a feature</button></div>
    <div class="ext-summary"><span>Core <b>${esc(data.core.version || 'development')}</b></span><span>Feature API <b>${esc(data.contract)}</b></span><span><b>${data.features.filter(f => f.loaded).length}</b> enabled</span></div>
    <section class="ext-card"><h3>Task engine</h3><p class="hint">Control recovery and job timing. Live jobs keep their assigned deadlines. Other limits apply to future checks.</p>
      <form id="extEngine"><div class="ext-grid">${runtime.map(([key, label, min, max, step, scale]) => `<label class="field"><span>${label}</span><input type="number" data-runtime="${key}" min="${min}" max="${max}" step="${step}" value="${config[key] / scale}" required></label>`).join('')}</div>
      <p class="hint" id="extExample"></p><button class="btn" type="submit">Save engine settings</button><p id="extEngineResult" role="status" aria-live="polite" class="hint"></p></form></section>
    <section class="ext-card"><h3>Role models</h3><p class="hint">Each role uses a distinct model. Lead and Builder use large models. Smaller models escalate after execution failures. Model names appear here for you; agent instructions and review packets stay anonymous.</p>
      <dl class="roles">${roleData.roles.map(r => `<dt>${esc(r.role[0].toUpperCase() + r.role.slice(1))}</dt><dd>${esc(r.selected_provider === 'claude' ? 'Claude' : 'Codex')} · ${esc(r.selected_model)}<span class="hint model-name">${esc(r.size)} · agentic mode</span></dd>`).join('')}</dl></section>
    <h3>Installed features</h3><div class="ext-features">${data.features.length ? data.features.map(card).join('') : '<section class="ext-card"><h3>No features installed</h3><p>Add a feature to create a page, panel, or workflow step.</p></section>'}</div>
    <details class="ext-card"><summary>Available extension points</summary><p>Features can add pages and panels, listen for workflow events, and add steps or decision gates after a core stage.</p>
      <p><b>Panel locations:</b> ${data.extension_points.slots.map(esc).join(', ')}</p><p><b>Workflow stages:</b> ${data.extension_points.stages.map(esc).join(', ')}</p>
      <p class="hint">Command guards and sandbox rules remain controlled by the engine.</p></details>`;
  root.querySelector('#extAdd').onclick = onAdd;
  const factorInput = root.querySelector('[data-runtime="job_runtime_multiplier"]');
  const example = () => {
    const hours = Number((12 * Number(factorInput.value)).toFixed(2));
    root.querySelector('#extExample').textContent = `A 12-hour estimate receives ${hours} hours. Quiet live jobs suspend ordinary stall recovery.`;
  };
  factorInput.addEventListener('input', example); example();
  root.querySelector('#extEngine').onsubmit = async event => {
    event.preventDefault();
    const result = root.querySelector('#extEngineResult');
    const values = Object.fromEntries(runtime.map(([key, , , , , scale]) => {
      const value = Number(root.querySelector(`[data-runtime="${key}"]`).value) * scale;
      return [key, key === 'job_runtime_multiplier' ? value : Math.round(value)];
    }));
    try { await api('/api/config', values); result.textContent = 'Engine settings saved.'; toast('Engine settings saved.'); }
    catch (error) { result.textContent = error.message; }
  };
  root.querySelectorAll('[data-feature-index]').forEach(section => {
    const feature = data.features[Number(section.dataset.featureIndex)];
    const result = section.querySelector('[data-result]');
    const apply = async payload => {
      try {
        await api('/api/extensions/configure', payload);
        await onSaved();
        await mountFeatures(root, {api, esc, toast, onSaved, onAdd});
        toast('Feature saved.');
      } catch (error) { result.textContent = error.message; }
    };
    section.querySelector('[data-toggle]').onclick = () => apply({feature: feature.name, enabled: !feature.enabled});
    section.querySelector('[data-feature-form]').onsubmit = event => {
      event.preventDefault();
      const payload = {feature: feature.name, values: {}};
      feature.controls.forEach(control => {
        const input = [...section.querySelectorAll('[data-setting]')].find(x => x.dataset.setting === control.key);
        payload.values[control.key] = control.choices ? control.choices[Number(input.value)] : control.type === 'bool' ? input.checked : control.type === 'text' ? input.value : Number(input.value);
      });
      section.querySelectorAll('[data-component-index]').forEach(row => {
        const c = feature.components[Number(row.dataset.componentIndex)];
        const patch = {visible: row.querySelector('[data-visible]').checked, order: Number(row.querySelector('[data-order]').value)};
        if (c.kind === 'panel') patch.slot = row.querySelector('[data-slot]').value;
        if (patch.visible !== (c.visible !== false) || patch.order !== c.order || (patch.slot && patch.slot !== c.slot)) {
          (payload.layout ||= {})[c.control_id] = patch;
        }
      });
      apply(payload);
    };
  });
}
