// App shell: sidebar (brand, navigation, loaded models, memory), titlebar (page title, downloads, theme),
// status bar (device, memory, downloads, server address) and routing. The window is filled at every size.

import { openChooser } from './chooser.js';
import { GLOSSARY } from './glossary.js';
import * as activity from './pages/activity.js';
import * as apiPage from './pages/api.js';
import * as evaluate from './pages/evaluate.js';
import * as learn from './pages/learn.js';
import * as models from './pages/models.js';
import * as playground from './pages/playground.js';
import * as system from './pages/system.js';
import { shell } from './shell.js';
import { cancelDownload, downloadPct, ejectModel, setPref, startPolling, store, subscribe } from './store.js';
import { $, $$, esc, fmtBytes, fmtDuration, fmtGB, icon, initTips, popMenu } from './util.js';

const ROUTES = {
  playground: { page: playground, label: 'Playground', icon: 'flask', sec: '' },
  models: { page: models, label: 'Models', icon: 'squares-four', sec: '' },
  evaluate: { page: evaluate, label: 'Evaluate', icon: 'list-checks', sec: '' },
  activity: { page: activity, label: 'Activity', icon: 'pulse', sec: 'Developer' },
  api: { page: apiPage, label: 'API', icon: 'terminal-window', sec: 'Developer' },
  learn: { page: learn, label: 'Learn', icon: 'book-open', sec: 'Help' },
  system: { page: system, label: 'System', icon: 'cpu', sec: 'Help' },
};
const ALIASES = { try: 'playground', batch: 'evaluate', code: 'api', history: 'activity' };

let current = null;

function parseHash() {
  const [path, qs] = (location.hash.replace(/^#\/?/, '') || '').split('?');
  const [head, ...rest] = path.split('/');
  const name = ALIASES[head] || head;
  const params = Object.fromEntries(new URLSearchParams(qs || ''));
  if (rest.length) params.id = decodeURIComponent(rest.join('/'));
  return { name: ROUTES[name] ? name : null, params };
}

function route() {
  const { name, params } = parseHash();
  if (!name) { history.replaceState(null, '', '#/playground'); return route(); }
  if (current && ROUTES[current]?.page.unmount) ROUTES[current].page.unmount();
  current = name;
  const main = $('#content');
  main.scrollTop = 0;
  shell.setTitle(ROUTES[name].label, '');
  ROUTES[name].page.mount(main, params);
  document.title = `${ROUTES[name].label} · Bud Decision Studio`;
  renderNav();
}

shell.setTitle = (t, sub = '') => { $('#ptitle').textContent = t; $('#psub').innerHTML = sub; };
shell.setSub = (sub) => { const el = $('#psub'); if (el.innerHTML !== sub) el.innerHTML = sub; };

// ------------------------------------------------------------------ sidebar
function renderNav() {
  const st = store.state;
  const ms = st?.models || [];
  const downloaded = ms.filter((m) => m.downloaded).length;
  let sec = null;
  const html = Object.entries(ROUTES).map(([k, r]) => {
    let head = '';
    if (r.sec !== sec) { sec = r.sec; head = r.sec ? `<div class="nav-sec">${r.sec}</div>` : ''; }
    const count = k === 'models' && st ? `<span class="count" title="${downloaded} of ${ms.length} on this machine">${downloaded}</span>` : '';
    return `${head}<a class="nav-item" href="#/${k}" ${current === k ? 'aria-current="page"' : ''} aria-label="${r.label}">${icon(r.icon)}<span>${r.label}</span>${count}</a>`;
  }).join('');
  const nav = $('#nav');
  if (nav.dataset.html !== html) { nav.dataset.html = html; nav.innerHTML = html; }
}

function renderLoaded() {
  const st = store.state;
  const box = $('#loaded');
  const ms = (st?.models || []).filter((m) => m.worker);
  const html = `<div class="nav-sec">Loaded</div>${ms.length ? `<div class="loaded">${ms.map((m) => {
    const cls = m.worker.status === 'ready' ? '' : m.worker.status === 'error' ? 'error' : 'busy';
    return `<div class="loaded-item" title="${esc(m.worker.stage || '')}"><span class="dot ${cls}"></span><span class="name">${esc(m.name)}</span>
      <span class="gb">${m.worker.status === 'ready' ? fmtGB(m.worker.gpu_gb || m.memory_gb) : ''}</span>
      <button class="icon-btn" data-eject="${m.id}" aria-label="Eject ${esc(m.name)}" data-tip="Eject">${icon('eject')}</button></div>`;
  }).join('')}</div>` : '<div class="loaded-empty">No model loaded</div>'}`;
  if (box.dataset.html === html) return;
  box.dataset.html = html; box.innerHTML = html;
  $$('[data-eject]', box).forEach((b) => b.addEventListener('click', () => ejectModel(b.dataset.eject)));
}

function memParts() {
  const st = store.state;
  const s = st.system;
  const used = st.models.filter((m) => m.worker).reduce((a, m) => a + (m.worker.gpu_gb || 0), 0);
  const other = Math.max(0, s.mem_used_gb - used);
  const w = (g) => `${Math.max(0, (g / s.mem_total_gb) * 100).toFixed(1)}%`;
  return { s, bar: `<span class="meter" title="Violet: loaded models. Grey: other programs."><i style="width:${w(used)}"></i><i class="other" style="width:${w(other)}"></i></span>` };
}

// Memory lives in the status bar alone, as in LM Studio.
function renderSideFoot() {}

// ------------------------------------------------------------------ status bar
function renderStatus() {
  const st = store.state;
  const bar = $('#statusbar');
  let html;
  if (!st) html = '<span class="item">Connecting to the studio</span>';
  else if (store.error) html = `<span class="item" style="color:var(--red-text)">${icon('warning-circle')}${esc(store.error)}</span>`;
  else {
    const s = st.system;
    const { bar: meter } = memParts();
    const dl = st.downloads?.active;
    const dlm = dl && st.models.find((m) => m.id === dl.model_id);
    const onDisk = st.models.filter((m) => m.downloaded).length;
    const onCpu = st.runtime?.device === 'cpu';
    const busy = onCpu ? s.cpu_percent : s.gpu_util;
    html = `<span class="item">${icon(onCpu ? 'cpu' : 'lightning')}${esc(st.runtime?.device_name || s.gpu_name)}${busy != null ? ` <span class="num">${Math.round(busy)}% busy</span>` : ''}</span>
      <span class="item hide-sm"><span class="term" data-term="unified_memory" tabindex="0">Memory</span>${meter}<span class="num">${fmtGB(s.mem_used_gb)} of ${fmtGB(s.mem_total_gb)}</span></span>
      <span class="item hide-sm">${icon('hard-drives')}${onDisk} of ${st.models.length} models on this machine</span>
      <span class="right">
        ${dlm ? `<a class="item" href="#/models">${icon('download-simple')}Downloading ${esc(dlm.name)} <span class="progress"><i style="width:${downloadPct(dlm)}%"></i></span><span class="num">${downloadPct(dlm)}%</span></a>` : ''}
        <span class="item hide-sm">API <code>${esc(location.host)}</code></span>
      </span>`;
  }
  if (bar.dataset.html !== html) { bar.dataset.html = html; bar.innerHTML = html; }
}

// ------------------------------------------------------------------ titlebar buttons
function renderTitleButtons() {
  const st = store.state;
  const q = st?.downloads || {};
  const n = (q.active ? 1 : 0) + (q.queue?.length || 0);
  const dl = $('#downloads');
  const html = `${icon('download-simple')}${n ? `<span class="badge">${n}</span>` : ''}`;
  if (dl.dataset.html !== html) { dl.dataset.html = html; dl.innerHTML = html; }
  const t = $('#theme');
  const mode = store.prefs.theme || 'auto';
  if (t.dataset.mode !== mode) { t.dataset.mode = mode; t.innerHTML = icon(mode === 'dark' ? 'moon' : mode === 'light' ? 'sun' : 'circle-half'); t.setAttribute('aria-label', `Colour theme: ${mode === 'auto' ? 'follow the system' : mode}`); }
  const sb = $('#sidetoggle');
  if (!sb.dataset.done) { sb.dataset.done = '1'; sb.innerHTML = icon('sidebar-simple'); }
}

$('#downloads').addEventListener('click', (e) => {
  const st = store.state;
  if (!st) return;
  const q = st.downloads || {};
  const act = q.active && st.models.find((m) => m.id === q.active.model_id);
  const queued = (q.queue || []).map((id) => st.models.find((m) => m.id === id)).filter(Boolean);
  let rows = '';
  if (act) {
    const d = act.download, got = d.have + d.partial, sp = q.active.speed_bps;
    const eta = sp > 0 ? (d.total - got) / sp : null;
    rows += `<div style="padding:8px 9px;display:grid;gap:6px;min-width:300px"><div style="display:flex;gap:8px;align-items:baseline"><b style="font-weight:600">${esc(act.name)}</b><span class="muted small num">${fmtBytes(got)} of ${fmtBytes(d.total)}</span>
      <button class="btn btn-quiet sm" data-pick="cancel:${act.id}" style="margin-left:auto">Cancel</button></div>
      <span class="progress"><i style="width:${downloadPct(act)}%"></i></span><span class="small muted">${sp ? `${fmtBytes(sp)}/s` : ''}${eta ? `, ${fmtDuration(eta)} left` : ''}</span></div>`;
  }
  rows += queued.map((m) => `<div style="padding:6px 9px;display:flex;gap:8px;align-items:center"><span>${esc(m.name)}</span><span class="muted small">${fmtBytes(m.download.remaining || m.download_bytes)}, queued</span><button class="btn btn-quiet sm" data-pick="cancel:${m.id}" style="margin-left:auto">Remove</button></div>`).join('');
  if (!rows) rows = '<div style="padding:10px 9px" class="muted">No downloads in progress.</div>';
  const tip = !st.hf_signed_in && act ? '<div class="small muted" style="padding:8px 9px;border-top:1px solid var(--line-2);margin-top:4px;max-width:340px">Hugging Face throttles anonymous downloads. Run <code>hf auth login</code> once, then restart the studio, for faster downloads.</div>' : '';
  const more = `<button class="menu-item" data-pick="choose:">${icon('plus')}Download more models</button>`;
  popMenu(e.currentTarget, `<div class="menu-group">Downloads</div>${rows}${tip}<div class="menu-sep"></div>${more}`, { align: 'right', onPick: (v) => { const [a, id] = v.split(':'); if (a === 'cancel') cancelDownload(id); if (a === 'choose') openChooser(); } });
});

$('#theme').addEventListener('click', () => {
  const order = ['auto', 'light', 'dark'];
  const next = order[(order.indexOf(store.prefs.theme || 'auto') + 1) % 3];
  setPref('theme', next);
  if (next === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = next;
  renderTitleButtons();
});

$('#sidetoggle').addEventListener('click', () => {
  const app = $('#app');
  app.classList.toggle('collapsed');
  setPref('sidebar', app.classList.contains('collapsed') ? 'collapsed' : 'open');
});
if (store.prefs.sidebar === 'collapsed') $('#app').classList.add('collapsed');

// First run: no model on disk and nothing downloading, so ask which models to fetch. Asked once per browser;
// afterwards the Models page and the downloads menu offer the same sheet.
let asked = false;
function firstRun() {
  const st = store.state;
  if (asked || !st) return;
  asked = true;
  const busy = st.downloads?.active || st.downloads?.queue?.length;
  if (!st.models.some((m) => m.downloaded) && !busy && !store.prefs.chooserSeen) {
    setPref('chooserSeen', true);
    openChooser({ firstRun: true });
  }
}

subscribe(() => {
  firstRun();
  renderNav(); renderLoaded(); renderSideFoot(); renderStatus(); renderTitleButtons();
  const r = ROUTES[current];
  if (r?.page.onState) r.page.onState();
});

// Inside the desktop app, web links open in the person's browser rather than in the app window.
if (window.__TAURI__) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || !/^https?:/.test(a.href) || new URL(a.href).host === location.host) return;
    e.preventDefault();
    window.__TAURI__.core.invoke('open_external', { url: a.href });
  });
}

window.addEventListener('hashchange', route);
initTips(GLOSSARY);
renderTitleButtons();
startPolling();
route();
