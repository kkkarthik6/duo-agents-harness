/* Notifications stay in document flow. Dismissal survives polling and reloads. */
export class NoticeStore {
  constructor(storage = null) {
    this.items = new Map(); this.dismissed = new Set(); this.storage = storage;
    try { this.dismissed = new Set(JSON.parse(storage?.getItem('duo.dismissed-notices') || '[]')); } catch {}
  }
  signature(source, value) {
    const text = source + ':' + (value.identity || value.message);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(36);
  }
  put(source, value) {
    const signature = this.signature(source, value);
    if (this.dismissed.has(signature)) return this.items.delete(source);
    if (this.items.get(source)?.signature === signature) return false;
    this.items.set(source, {...value, source, signature});
    return true;
  }
  dismiss(source) {
    const item = this.items.get(source);
    if (!item) return;
    this.dismissed.add(item.signature); this.items.delete(source);
    try { this.storage?.setItem('duo.dismissed-notices', JSON.stringify([...this.dismissed])); } catch {}
  }
  clear(source) { return this.items.delete(source); }
}

export function createNotificationCenter(root, storage) {
  const store = new NoticeStore(storage), timers = new Map();
  let expanded = false;
  root.innerHTML = '<div class="notice-toolbar"><button type="button" class="btn" id="noticeToggle" aria-expanded="false" aria-controls="noticeList">Notifications (0)</button><button type="button" id="noticeLatest" class="notice-latest" hidden></button><button type="button" class="btn" id="noticeDismissAll">Dismiss all</button><span id="noticeEmpty" hidden>No active notifications</span></div><div id="noticeList" hidden></div>';
  const toggle = root.querySelector('#noticeToggle'), list = root.querySelector('#noticeList');
  const latest = root.querySelector('#noticeLatest'), empty = root.querySelector('#noticeEmpty');
  function render() {
    const items = [...store.items.values()];
    toggle.textContent = `Notifications (${items.length})`;
    toggle.setAttribute('aria-expanded', String(expanded));
    list.hidden = !expanded; list.replaceChildren();
    empty.hidden = true; latest.hidden = true;
    root.querySelector('#noticeDismissAll').hidden = !items.length;
    if (items.length) {
      const item = items.at(-1);
      latest.textContent = item.message;
      latest.title = 'Click to dismiss this notification';
      latest.setAttribute('aria-label', 'Dismiss notification: ' + item.message);
      latest.onclick = () => { store.dismiss(item.source); render(); };
    }
    for (const item of items) {
      const row = document.createElement('div'); row.className = 'notice-row';
      const message = document.createElement('button'); message.type = 'button'; message.className = 'notice-message';
      message.textContent = item.message; message.title = 'Click to dismiss';
      message.setAttribute('aria-label', 'Dismiss notification: ' + item.message);
      message.onclick = () => { store.dismiss(item.source); render(); };
      row.append(message);
      for (const action of item.actions || []) {
        const button = document.createElement('button'); button.className = 'btn'; button.textContent = action.label;
        button.onclick = async () => {
          store.dismiss(item.source); render();
          try { await action.run(); } catch (error) { publish('notification-action-error', {message: error.message || 'The action failed.'}); }
        };
        row.append(button);
      }
      const close = document.createElement('button'); close.type = 'button'; close.className = 'icon-btn'; close.textContent = '×';
      close.setAttribute('aria-label', 'Dismiss notification'); close.onclick = message.onclick; row.append(close);
      list.append(row);
    }
  }
  function publish(source, value) {
    if (source === 'toast') value = {...value, identity: value.identity || String(Date.now())};
    const changed = store.put(source, value); clearTimeout(timers.get(source));
    const ttl = value.ttl ?? 8000;
    if (ttl > 0) timers.set(source, setTimeout(() => { store.dismiss(source); render(); }, ttl));
    if (changed) render();
  }
  toggle.onclick = () => { expanded = !expanded; render(); };
  root.querySelector('#noticeDismissAll').onclick = () => { for (const source of [...store.items.keys()]) store.dismiss(source); expanded = false; render(); };
  render();
  return {publish, clear(source) { const changed = store.clear(source); clearTimeout(timers.get(source)); if (changed) render(); }, store};
}
