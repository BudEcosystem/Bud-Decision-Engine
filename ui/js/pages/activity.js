// Activity: every decision request the studio served, from any client, live. A summary strip, a timeline of
// requests by speed, breakdowns by model and client, the request list, and an inspector that redraws the answers.

import { animate, figures, mini } from '../figures.js';
import { jsonTree } from '../format.js';
import { setSub } from '../shell.js';
import { api, model, registry } from '../store.js';
import { $, $$, confirmDialog, copy, esc, fmtMs, fmtNum, icon, toast } from '../util.js';
import { loadRequest } from './playground.js';

let root, reg, timer, onResize;
let items = [];
let paused = false;
let sel = null;
let itab = 'answers';
let f = { range: '60', model: 'all', status: 'all' };
let view = 'time';   // the chart card: timeline or one of the breakdowns

const FORMAT = { typesafe: 'TypeSafe', openrouter: 'OpenRouter', vercel: 'Vercel', evaluate: 'Vercel evaluate' };
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
const pct = (a, p) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(s.length * p))]; };
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}
const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const nameOf = (id) => model(id)?.name || id || 'Unknown';

export async function mount(el) {
  root = el;
  reg = await registry();
  root.innerHTML = `<div class="view panes act-view">
    <section class="pane" aria-label="Requests">
      <div class="pane-head">
        <span class="live" id="live"><i></i>Live</span>
        <button class="btn btn-quiet sm" id="pause">${icon('pause')}Pause</button>
        <span class="seg" role="group" aria-label="Time range">${[['15', '15 min'], ['60', '1 hour'], ['1440', '24 hours'], ['all', 'All']].map(([v, l]) => `<button data-range="${v}" aria-pressed="${f.range === v}">${l}</button>`).join('')}</span>
        <select class="select" id="fmodel" style="width:auto" aria-label="Model"></select>
        <select class="select" id="fstatus" style="width:auto" aria-label="Status"><option value="all">All results</option><option value="ok">Succeeded</option><option value="err">Failed</option></select>
        <div class="right"><button class="btn btn-quiet sm" id="clear">${icon('trash')}Clear</button></div>
      </div>
      <div class="pane-body" id="abody"></div>
    </section>
    <aside class="pane white" id="ainsp" aria-label="Request details"></aside>
  </div>`;
  $('#pause', root).addEventListener('click', () => { paused = !paused; $('#pause', root).innerHTML = paused ? `${icon('play')}Resume` : `${icon('pause')}Pause`; $('#live', root).classList.toggle('paused', paused); $('#live', root).lastChild.textContent = paused ? 'Paused' : 'Live'; if (!paused) load(); });
  $$('[data-range]', root).forEach((b) => b.addEventListener('click', () => { f.range = b.dataset.range; $$('[data-range]', root).forEach((x) => x.setAttribute('aria-pressed', x === b)); draw(); }));
  $('#fmodel', root).addEventListener('change', (e) => { f.model = e.target.value; draw(); });
  $('#fstatus', root).addEventListener('change', (e) => { f.status = e.target.value; draw(); });
  $('#clear', root).addEventListener('click', async () => {
    if (!(await confirmDialog({ title: 'Clear the activity log?', body: '<p>This removes every logged request from the studio. Models and settings are not affected.</p>', confirm: 'Clear log', danger: true }))) return;
    try { await api('/api/history', { method: 'DELETE' }); items = []; sel = null; draw(); drawInspector(); toast('Activity log cleared'); } catch (e) { toast(e.message, 'error'); }
  });
  $('#abody', root).addEventListener('click', (e) => {
    const v = e.target.closest('[data-view]');
    if (v) { view = v.dataset.view; draw(); return; }
    const r = e.target.closest('[data-req]');
    if (r) { sel = r.dataset.req; drawInspector(); $$('[data-req]', root).forEach((x) => x.classList.toggle('sel', x.dataset.req === sel)); draw(); }
  });
  drawInspector();
  let rt; onResize = () => { clearTimeout(rt); rt = setTimeout(() => { if (root.isConnected) draw(); }, 150); };
  window.addEventListener('resize', onResize);
  await load();
  clearInterval(timer);
  timer = setInterval(() => { if (!root.isConnected) { clearInterval(timer); return; } if (!paused && !document.hidden) load(); }, 2000);
}
export function unmount() { clearInterval(timer); window.removeEventListener('resize', onResize); }

async function load() {
  try { items = await api('/api/history?limit=500'); } catch { return; }
  if (!sel && items.length) sel = items[0].id;
  const opts = `<option value="all">All models</option>${[...new Set(items.map((x) => x.model).filter(Boolean))].map((id) => `<option value="${esc(id)}" ${f.model === id ? 'selected' : ''}>${esc(nameOf(id))}</option>`).join('')}`;
  const fm = $('#fmodel', root);
  if (fm && fm.dataset.html !== opts) { fm.dataset.html = opts; fm.innerHTML = opts; }
  draw();
  if (!$('#ainsp [data-shown]', root) || $('#ainsp [data-shown]', root).dataset.shown !== sel) drawInspector();
}

function filtered() {
  const now = Date.now() / 1000;
  return items.filter((x) => (f.range === 'all' || x.time >= now - +f.range * 60)
    && (f.model === 'all' || x.model === f.model)
    && (f.status === 'all' || (f.status === 'ok' ? x.status === 200 : x.status !== 200)));
}

// ------------------------------------------------------------------ the page body
function draw() {
  const box = $('#abody', root);
  if (!box) return;
  const list = filtered();
  setSub(`${items.length} request${items.length === 1 ? '' : 's'} logged`);
  if (!items.length) {
    box.innerHTML = `<div class="empty"><div class="glyph">${icon('pulse')}</div><h2>No requests yet</h2>
      <p>Every decision the studio serves appears here, from the Playground, your code, an SDK or curl. Run one in the Playground, or call <code>POST /v1/systemone</code>.</p>
      <a class="btn btn-primary" href="#/playground">${icon('flask')}Open the Playground</a></div>`;
    return;
  }
  const ok = list.filter((x) => x.status === 200);
  const lat = ok.map((x) => x.latency_ms).filter((v) => v != null);
  const mins = f.range === 'all' ? Math.max(1, (Date.now() / 1000 - Math.min(...list.map((x) => x.time), Date.now() / 1000)) / 60) : +f.range;
  const qs = list.reduce((a, x) => a + (x.questions || 0), 0);
  // The chart is drawn at the card's real width so its labels stay legible at every window size.
  const W = Math.max(300, (box.clientWidth || 700) - 64);
  const chart = view === 'time' ? timeline(list, W)
    : view === 'model' ? bars(list, (x) => x.model, nameOf, true)
    : view === 'client' ? bars(list, (x) => x.client || 'Earlier requests', (k) => k, false)
    : bars(list, (x) => FORMAT[x.format] || 'TypeSafe', (k) => k, false);
  const sub = { time: 'Each dot is one request: when it ran, and how long the model took. Red dots failed. Click one to inspect it.', model: 'Requests per model, with the typical model time.', client: 'Who sent the requests: the studio, an SDK, curl or your own code.', format: 'Which API format each request used.' }[view];
  const html = `
    <div class="stats-strip slim">
      <div><span>Requests</span><b>${list.length.toLocaleString()}</b><small>${fmtNum(list.length / mins, 1)} per minute</small></div>
      <div><span>Typical model time</span><b>${lat.length ? fmtMs(median(lat)) : 'n/a'}</b><small>${lat.length ? `95% under ${fmtMs(pct(lat, 0.95))}` : 'no successful requests'}</small></div>
      <div><span>Succeeded</span><b>${list.length ? `${Math.round((ok.length / list.length) * 100)}%` : 'n/a'}</b><small>${list.length - ok.length} failed</small></div>
      <div><span>Questions answered</span><b>${qs.toLocaleString()}</b><small>${list.length ? fmtNum(qs / list.length, 1) : 0} per request</small></div>
    </div>
    <div class="chart-card">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><h2>${{ time: 'Requests over time', model: 'By model', client: 'By client', format: 'By API format' }[view]}</h2>
        <span class="seg" role="group" aria-label="Chart" style="margin-left:auto">${[['time', 'Timeline'], ['model', 'Models'], ['client', 'Clients'], ['format', 'Formats']].map(([k, l]) => `<button data-view="${k}" aria-pressed="${view === k}">${l}</button>`).join('')}</span></div>
      <span class="sub">${sub}</span>
      <div class="chart-body">${chart}</div>
    </div>
    <div class="card" style="overflow:hidden"><div class="tscroll"><table class="table hover">
      <thead><tr><th>When</th><th>Result</th><th>Model</th><th>Client</th><th class="num">Questions</th><th>First answer</th><th class="num">Model time</th></tr></thead>
      <tbody>${list.slice(0, 200).map((x) => row(x, lat)).join('') || '<tr><td colspan="7" class="muted">No requests match these filters.</td></tr>'}</tbody></table></div></div>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

function row(x, lat) {
  const maxLat = Math.max(...lat, 1);
  const k = x.response?.answers ? Object.keys(x.response.answers)[0] : null;
  const first = x.status === 200 && k ? mini(x.response.answers[k]) : x.status !== 200 ? `<span style="color:var(--red-text)">${esc(String(x.response?.detail || 'Failed')).slice(0, 80)}</span>` : '';
  return `<tr data-req="${esc(x.id)}" class="${x.id === sel ? 'sel' : ''}" tabindex="0">
    <td class="small" title="${esc(new Date(x.time * 1000).toLocaleString())}">${ago(x.time)}</td>
    <td>${x.status === 200 ? '<span class="chip ok">OK</span>' : `<span class="chip danger">${x.status}</span>`}</td>
    <td>${esc(nameOf(x.model))}</td>
    <td class="small muted">${esc(x.client || 'Earlier requests')}</td>
    <td class="num">${x.questions ?? ''}</td>
    <td class="small" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${first}</td>
    <td><span class="lat"><span class="num">${fmtMs(x.latency_ms)}</span><span class="b"><i style="width:${x.latency_ms ? Math.max(3, (x.latency_ms / maxLat) * 100) : 0}%"></i></span></span></td></tr>`;
}

function bars(list, keyFn, label, withLatency) {
  const groups = {};
  for (const x of list) { const k = keyFn(x) || 'Unknown'; (groups[k] ??= []).push(x); }
  const rows = Object.entries(groups).sort((a, b) => b[1].length - a[1].length).slice(0, 6);
  const max = Math.max(...rows.map(([, v]) => v.length), 1);
  if (!rows.length) return '<p class="small muted">Nothing in this range.</p>';
  return `<div class="hbars">${rows.map(([k, v]) => {
    const m = median(v.filter((x) => x.status === 200 && x.latency_ms != null).map((x) => x.latency_ms));
    return `<div class="hb"><span>${esc(label(k))}</span><span class="n">${v.length}${withLatency && m != null ? `, ${fmtMs(m)}` : ''}</span><span class="b"><i style="width:${(v.length / max) * 100}%"></i></span></div>`;
  }).join('')}</div>`;
}

// A scatter of requests: time across, model latency up (log scale when the spread is wide).
function timeline(list, W = 760) {
  if (!list.length) return '<p class="small muted">Nothing in this range.</p>';
  const H = 240, L = 54, R = 12, T = 12, B = 26;
  const now = Date.now() / 1000;
  const t0 = f.range === 'all' ? Math.min(...list.map((x) => x.time)) : now - +f.range * 60;
  const t1 = Math.max(now, t0 + 60);
  const lats = list.map((x) => x.latency_ms ?? x.wall_ms ?? 1).map((v) => Math.max(1, v));
  const lo = Math.min(...lats), hi = Math.max(...lats, lo * 2);
  const log = hi / lo > 20;
  const ymin = log ? Math.pow(10, Math.floor(Math.log10(lo))) : 0, ymax = log ? Math.pow(10, Math.ceil(Math.log10(hi))) : hi * 1.15;
  const y = (v) => T + (1 - (log ? (Math.log10(v) - Math.log10(ymin)) / (Math.log10(ymax) - Math.log10(ymin)) : (v - ymin) / (ymax - ymin || 1))) * (H - T - B);
  const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R);
  const yt = log ? Array.from({ length: Math.log10(ymax) - Math.log10(ymin) + 1 }, (_, i) => ymin * 10 ** i) : [0, ymax / 2, ymax].map((v) => Math.round(v));
  const xt = (W < 520 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1]).map((p) => t0 + p * (t1 - t0));
  const fmtT = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return `<svg class="tl" viewBox="0 0 ${W} ${H}" role="img" aria-label="Scatter of ${list.length} requests by time and model latency">
    ${yt.map((v) => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v || ymin || 0)}" y2="${y(v || ymin || 0)}"/><text class="gt" x="${L - 6}" y="${y(v || ymin || 0) + 4}" text-anchor="end">${fmtMs(v)}</text>`).join('')}
    ${xt.map((t, i) => `<text class="gt" x="${x(t)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === xt.length - 1 ? 'end' : 'middle'}">${f.range === 'all' || +f.range > 60 ? fmtT(t) : fmtT(t)}</text>`).join('')}
    ${list.map((it, i) => { const cx = x(it.time).toFixed(1), cy = y(lats[i]).toFixed(1); return `<circle class="pt ${it.status === 200 ? '' : 'err'} ${it.id === sel ? 'sel' : ''}" cx="${cx}" cy="${cy}" r="${it.id === sel ? 5 : 3.5}"/><circle class="hit" data-req="${esc(it.id)}" cx="${cx}" cy="${cy}" r="9"><title>${esc(nameOf(it.model))}, ${fmtMs(it.latency_ms)}, ${clock(it.time)}${it.status === 200 ? '' : `, failed (${it.status})`}</title></circle>`; }).join('')}
  </svg>`;
}

// ------------------------------------------------------------------ inspector
function drawInspector() {
  const box = $('#ainsp', root);
  if (!box) return;
  const x = items.find((i) => i.id === sel);
  if (!x) { box.innerHTML = `<div class="empty" style="height:100%"><div class="glyph">${icon('cursor-click')}</div><h2>Select a request</h2><p>Its answers are redrawn here, with the exact request and response.</p></div>`; return; }
  const req = x.request || {};
  box.innerHTML = `<div class="req-insp" data-shown="${esc(x.id)}">
    <div class="hero"><div style="display:flex;gap:8px;align-items:center">${x.status === 200 ? '<span class="chip ok">Succeeded</span>' : `<span class="chip danger">Failed, HTTP ${x.status}</span>`}<span class="muted small">${esc(new Date(x.time * 1000).toLocaleString())}</span></div>
      <h2>${esc(nameOf(x.model))}</h2></div>
    <div class="tabbody" style="padding:6px 18px 24px;display:grid;gap:14px">
      <div class="group">
        <div class="grow"><span class="k">Model time</span><span class="v num">${fmtMs(x.latency_ms)}${x.wall_ms ? `, ${fmtMs(x.wall_ms)} in total` : ''}</span></div>
        <div class="grow"><span class="k">Client</span><span class="v">${esc(x.client || 'Unknown')}</span></div>
        <div class="grow"><span class="k">Endpoint</span><span class="v"><code>${esc(x.path || '/v1/systemone')}</code></span></div>
        <div class="grow"><span class="k">Questions</span><span class="v num">${x.questions}${x.media ? `, ${x.media} attached file${x.media === 1 ? '' : 's'}` : ''}</span></div>
        ${x.request_id ? `<div class="grow link" id="rid"><span class="k">Request id</span><span class="v" style="display:flex;gap:6px;align-items:center"><code style="overflow:hidden;text-overflow:ellipsis">${esc(x.request_id)}</code>${icon('copy')}</span></div>` : ''}
      </div>
      <div style="display:flex;gap:8px"><button class="btn btn-primary" id="replay">${icon('flask')}Open in Playground</button></div>
      <span class="seg full" role="tablist">${[['answers', 'Answers'], ['request', 'Request'], ['response', 'Response']].map(([k, l]) => `<button role="tab" data-itab="${k}" aria-pressed="${itab === k}" aria-selected="${itab === k}">${l}</button>`).join('')}</span>
      <div id="itabbody"></div>
    </div></div>`;
  const body = $('#itabbody', box);
  const drawTab = () => {
    if (itab === 'answers') {
      body.innerHTML = x.status === 200 && x.response?.answers ? figures(x.response, req, { run: 1 }) : `<div class="note danger">${icon('warning-circle')}<span>${esc(String(x.response?.detail || 'The request failed.'))}</span></div>`;
      const g = $('.figs', body); if (g) g.style.gridTemplateColumns = 'minmax(0, 1fr)';
      animate(body, { instant: true });
    }
    if (itab === 'request') body.innerHTML = jsonTree(req, { file: 'Request', openDepth: 3 });
    if (itab === 'response') body.innerHTML = jsonTree(x.response || {}, { file: 'Response', openDepth: 2, collapse: ['legend', 'model_extras'] });
  };
  $$('[data-itab]', box).forEach((b) => b.addEventListener('click', () => { itab = b.dataset.itab; $$('[data-itab]', box).forEach((y) => { y.setAttribute('aria-pressed', y === b); y.setAttribute('aria-selected', y === b); }); drawTab(); }));
  $('#rid', box)?.addEventListener('click', () => copy(x.request_id, 'Request id copied'));
  $('#replay', box).addEventListener('click', () => { loadRequest(req, x.model); location.hash = '#/playground'; });
  drawTab();
}
