// Models, after LM Studio's My Models: a table of every model on the left, an inspector on the right with
// Overview, a comparison with Jev, and Load settings. #/models/<id> selects a model.

import { openChooser } from '../chooser.js';
import { setSub } from '../shell.js';
import { api, cancelDownload, download, downloadPct, ejectModel, loadModel, phaseOf, phaseTrack, refresh, registry, setPref, store } from '../store.js';
import { $, $$, confirmDialog, esc, fmtBytes, fmtDuration, fmtGB, icon, term, toast } from '../util.js';

let root, reg;
let selected = null;
let tab = 'overview';
let filter = { q: '', show: 'all', input: 'any' };
let sort = { key: 'recommended', dir: 1 };
let finder = { input: 'text', language: 'en', priority: 'balanced' };

// Registry text comes from publishers; normalise its dashes to the studio's plain punctuation.
const clean = (s) => String(s ?? '').replace(/(\d)\s*[–—]\s*(\d)/g, '$1 to $2').replace(/\s+[—–]\s+/g, ': ').replace(/[—–]/g, '-');
const isLocal = (u) => !/^https?:\/\//.test(String(u || ''));   // sources that are not web pages
const JEV = '__jev';

export async function mount(el, p = {}) {
  root = el;
  root.innerHTML = '<div class="view"><div class="empty"><p>Loading the registry.</p></div></div>';
  reg = await registry();
  if (root !== el || !el.isConnected) return;
  const ms = store.state?.models || [];
  selected = p.id || selected || ms.find((m) => m.worker?.status === 'ready')?.id || 'intern-decision-4b';
  render();
}
export function onState() {
  if (!root?.isConnected || !reg || !$('#mtable', root)) return;
  renderTable();
  if (!$('#insp .hero', root)) renderInspector(); else { renderActs(); if (tab === 'load') renderTab(); }
}

function render() {
  root.innerHTML = `<div class="view panes models-view">
    <section class="pane" aria-label="All models">
      <div class="pane-head">
        <label class="search">${icon('magnifying-glass')}<input id="q" placeholder="Filter models" value="${esc(filter.q)}" aria-label="Filter models"></label>
        <span class="seg" role="group" aria-label="Show">${[['all', 'All'], ['downloaded', 'On this machine'], ['loaded', 'Loaded']].map(([v, l]) => `<button data-show="${v}" aria-pressed="${filter.show === v}">${l}</button>`).join('')}</span>
        <select class="select" id="input" aria-label="Input" style="width:auto;height:28px;min-height:28px;padding-top:2px;padding-bottom:2px">
          ${[['any', 'Any input'], ['image', 'Reads images'], ['av', 'Audio and video'], ['multi', 'Multilingual']].map(([v, l]) => `<option value="${v}" ${filter.input === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <div class="right">
          <button class="btn btn-plain sm" id="help">${icon('lightbulb')}Help me choose</button>
          <button class="btn sm" id="dlall">${icon('cloud-arrow-down')}Download models</button>
        </div>
      </div>
      <div id="mtable"></div>
    </section>
    <aside class="pane white m-insp" id="insp" aria-label="Model details"></aside>
  </div>`;
  $('#q', root).addEventListener('input', (e) => { filter.q = e.target.value; renderTable(); });
  $$('[data-show]', root).forEach((b) => b.addEventListener('click', () => { filter.show = b.dataset.show; $$('[data-show]', root).forEach((x) => x.setAttribute('aria-pressed', x === b)); renderTable(); }));
  $('#input', root).addEventListener('change', (e) => { filter.input = e.target.value; renderTable(); });
  $('#help', root).addEventListener('click', helpMeChoose);
  $('#dlall', root).addEventListener('click', () => openChooser());
  $('#mtable', root).addEventListener('click', (e) => {
    const th = e.target.closest('th[data-sort]');
    if (th) { const k = th.dataset.sort; sort = { key: k, dir: sort.key === k ? -sort.dir : 1 }; renderTable(); return; }
    if (e.target.closest('button, a')) return;
    const tr = e.target.closest('tr[data-id]');
    if (tr) select(tr.dataset.id);
  });
  $('#mtable', root).addEventListener('dblclick', (e) => {
    const tr = e.target.closest('tr[data-id]');
    const m = tr && store.state.models.find((x) => x.id === tr.dataset.id);
    if (m?.worker?.status === 'ready') location.hash = `#/playground?model=${m.id}`;
  });
  $('#mtable', root).addEventListener('keydown', (e) => {
    if (!['ArrowDown', 'ArrowUp'].includes(e.key)) return;
    const rows = $$('tr[data-id]', root); const i = rows.findIndex((r) => r.dataset.id === selected);
    const n = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
    if (n) { e.preventDefault(); select(n.dataset.id); n.focus(); }
  });
  renderTable(); renderInspector();
}

function select(id) {
  selected = id;
  history.replaceState(null, '', id === JEV ? '#/models' : `#/models/${id}`);
  $$('tr[data-id]', root).forEach((r) => r.classList.toggle('sel', r.dataset.id === id));
  renderInspector();
}

// ------------------------------------------------------------------ numbers and comparisons
const paramsNum = (p) => { const m = String(p).match(/([\d.]+)\s*([MB])/i); return m ? +m[1] * (m[2].toUpperCase() === 'B' ? 1 : 0.001) : 0; };
function scaled(b, v) { if (v == null) return null; return b.unit === 'ratio' && b.higher_is_better ? v * 100 : v; }
function fmtVal(b, v, display) {
  if (display) return clean(display);
  if (v == null) return 'n/a';
  if (b.unit === '%') return `${+v.toFixed(1)}%`;
  if (b.unit === 'ratio') return b.higher_is_better ? `${+(v * 100).toFixed(1)}%` : v.toFixed(3);
  if (b.unit === 'score') return `${+v.toFixed(1)}`;
  return `${+(+v).toPrecision(4)} ${clean(b.unit)}`;
}
const wins = (b) => (b.higher_is_better ? b.model > b.jev : b.model < b.jev);
const level = (b) => b.model === b.jev;
function h2h(r) {
  const rows = [...(r?.benchmarks || []), ...(r?.independent || [])].filter((b) => b.model != null && b.jev != null);
  const w = rows.filter(wins).length, t = rows.filter(level).length;
  return { rows, w, t, l: rows.length - w - t, n: rows.length, rate: rows.length ? w / rows.length : -1 };
}
const onPctAxis = (b) => ['%', 'score'].includes(b.unit) || (b.unit === 'ratio' && b.higher_is_better);
function winbar(s, cls = '') {
  if (!s.n) return `<span class="winbar ${cls}" aria-hidden="true"></span>`;
  return `<span class="winbar ${cls}" role="img" aria-label="Ahead of Jev on ${s.w}, level on ${s.t}, behind on ${s.l} of ${s.n} published results"><i class="a" style="flex:${s.w}"></i>${s.t ? `<i class="t" style="flex:${s.t}"></i>` : ''}${s.l ? `<i class="b" style="flex:${s.l}"></i>` : ''}</span>`;
}
function dumbbell(b) {
  const m = scaled(b, b.model), j = scaled(b, b.jev);
  const x = (v) => `${Math.max(0, Math.min(100, v)).toFixed(2)}%`;
  const lo = Math.min(m, j), hi = Math.max(m, j);
  return `<div class="dumbbell" role="img" aria-label="This model ${esc(fmtVal(b, b.model, b.model_display))}, Jev ${esc(fmtVal(b, b.jev, b.jev_display))}">
    <span class="span" style="left:${x(lo)};width:calc(${x(hi)} - ${x(lo)})"></span><span class="dot j" style="left:${x(j)}"></span><span class="dot m" style="left:${x(m)}"></span></div>`;
}
function headlineRows(rows, max = 6) {
  const seen = new Set();
  return rows.filter(onPctAxis).filter((b) => { const k = clean(b.suite).toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, max);
}

// ------------------------------------------------------------------ small pieces
function logoFor(m, r, cls = '') {
  const maker = reg.makers[r?.maker_id] || {};
  const src = maker.avatar || r?.logo || maker.logo;
  return `<span class="logo ${cls}">${src ? `<img src="/ui/${esc(src)}" alt="" loading="lazy">` : icon('cube')}</span>`;
}
const INPUT_ICON = { text: 'text-t', image: 'image', audio: 'speaker-high', video: 'film-strip' };
const INPUT = { text: 'Text', image: 'Images', audio: 'Audio', video: 'Video' };
const inputs = (m) => m.modalities.map((k) => INPUT[k]).join(', ').replace(/, ([^,]*)$/, ' and $1');
const inputIcons = (m) => m.modalities.map((k) => `<span data-tip="${INPUT[k]}">${icon(INPUT_ICON[k])}</span>`).join('');
const lang = (m) => clean(m.languages).replace(/\s*\(.*\)/, '');

function statusWord(m) {
  const st = store.state;
  const d = m.download;
  if (st.downloads?.active?.model_id === m.id) {
    const got = d.have + d.partial, speed = st.downloads.active.speed_bps;
    const eta = speed > 0 ? (d.total - got) / speed : null;
    return `Downloading ${downloadPct(m)}%${eta ? `, ${fmtDuration(eta)} left` : ''}`;
  }
  if (m.worker?.status === 'ready') return 'Loaded';
  if (m.worker?.status === 'error') return 'Failed to load';
  if (m.worker) return esc(m.worker.stage || 'Loading');
  if (m.fit && !m.fit.ok) return `<span data-tip="${esc(m.fit.reason)}">${m.needs_gpu && st.runtime?.device === 'cpu' ? 'Needs a GPU' : 'Too large here'}</span>`;
  if (m.downloaded) return 'On this machine';
  if (st.downloads?.queue?.includes(m.id)) return 'Queued';
  return fmtBytes(d.remaining || m.download_bytes);
}
function actions(m, { size = 'sm', full = false, quiet = false, single = false } = {}) {
  const st = store.state;
  const free = st.system.mem_available_gb;
  const fits = free == null || m.memory_gb <= free + 1;
  const primary = quiet ? 'btn-quiet' : 'btn-primary';
  if (m.worker?.status === 'ready') return `<a class="btn ${primary} ${size}" href="#/playground?model=${m.id}">${icon('play')}${full ? 'Try in Playground' : 'Try'}</a>${single ? '' : `<button class="btn btn-quiet ${size}" data-eject="${m.id}">${icon('eject')}Eject</button>`}`;
  if (m.worker?.status === 'error') return `<button class="btn ${primary} ${size}" data-load="${m.id}">${icon('arrows-clockwise')}Retry</button>${full ? `<button class="btn btn-quiet ${size}" data-logs="${m.id}">${icon('scroll')}View log</button>` : ''}`;
  if (m.worker) return `<button class="btn btn-quiet ${size}" data-eject="${m.id}">${icon('x')}Cancel</button>`;
  if (st.downloads?.active?.model_id === m.id || st.downloads?.queue?.includes(m.id)) return `<button class="btn btn-quiet ${size}" data-cancel="${m.id}">${icon('stop')}Cancel</button>`;
  if (m.fit && !m.fit.ok) return full ? `<span class="small muted">${esc(m.fit.reason)}</span>` : '';
  if (m.downloaded) return `<button class="btn ${primary} ${size}" data-load="${m.id}" ${fits ? '' : `data-tip="Needs about ${fmtGB(m.memory_gb)}; only ${fmtGB(free)} is free. Eject another model first."`}>${icon('play')}Load</button>`;
  if (m.fit && !m.fit.ok) return full ? `<span class="small muted">${esc(m.fit.reason)}</span>` : '';
  return `<button class="btn ${quiet ? 'btn-quiet' : ''} ${size}" data-download="${m.id}">${icon('download-simple')}Download${full ? ` ${fmtBytes(m.download.remaining || m.download_bytes)}` : ''}</button>`;
}
function bindActions(box) {
  $$('[data-download]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); download(b.dataset.download); }));
  $$('[data-cancel]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); cancelDownload(b.dataset.cancel); }));
  $$('[data-load]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); loadModel(b.dataset.load); }));
  $$('[data-eject]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); ejectModel(b.dataset.eject); }));
  $$('[data-logs]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); showLogs(b.dataset.logs); }));
  $$('[data-delete]', box).forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); del(b.dataset.delete); }));
}

// ------------------------------------------------------------------ table
const rankScore = (m) => (m.worker ? 100 : 0) + (m.downloaded ? 50 : 0) + (m.badge === 'Start here' ? 30 : 0) + Math.log10((m.likes || 1) + 1);
function renderTable() {
  const st = store.state;
  const box = $('#mtable', root);
  if (!st || !box) return;
  let list = st.models.slice();
  const q = filter.q.trim().toLowerCase();
  if (q) list = list.filter((m) => `${m.name} ${m.maker} ${m.tagline} ${m.languages}`.toLowerCase().includes(q));
  if (filter.show === 'downloaded') list = list.filter((m) => m.downloaded || m.worker);
  if (filter.show === 'loaded') list = list.filter((m) => m.worker);
  if (filter.input === 'image') list = list.filter((m) => m.modalities.includes('image'));
  if (filter.input === 'av') list = list.filter((m) => m.modalities.includes('audio'));
  if (filter.input === 'multi') list = list.filter((m) => !/^English$/.test(m.languages));
  const key = { recommended: (m) => -rankScore(m), name: (m) => m.name.toLowerCase(), size: (m) => paramsNum(m.params), memory: (m) => m.memory_gb, jev: (m) => -h2h(reg.models[m.id]).rate };
  const f = key[sort.key] || key.recommended;
  list.sort((a, b) => { const x = f(a), y = f(b); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir; });
  const th = (k, label, cls = '') => `<th class="${cls}" data-sort="${k}" style="cursor:pointer" aria-sort="${sort.key === k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}${sort.key === k ? icon(sort.dir > 0 ? 'caret-up' : 'caret-down') : ''}</th>`;
  const rows = list.map((m) => {
    const r = reg.models[m.id] || {};
    const s = h2h(r);
    return `<tr data-id="${m.id}" class="${m.id === selected ? 'sel' : ''}" tabindex="0">
      <td><div class="mname">${logoFor(m, r, 'sm')}<div style="min-width:0"><b>${esc(m.name)}${m.badge === 'Start here' ? ' <span class="chip violet tag">Start here</span>' : ''}</b><span>${esc(clean(m.tagline))}</span></div></div></td>
      <td class="num">${esc(m.params)}</td>
      <td class="num">${fmtGB(m.memory_gb)}</td>
      <td><span class="ins">${inputIcons(m)}</span></td>
      <td><span class="vsj">${winbar(s)}${s.n ? `<span class="num">${s.w} of ${s.n}</span>` : '<span>None yet</span>'}</span></td>
      <td><div class="st"><span class="word" title="${esc(statusWord(m).replace(/<[^>]+>/g, ''))}">${phaseTrack(m)}${store.state.downloads?.active?.model_id === m.id ? ` <span class="num">${downloadPct(m)}%</span>` : m.worker && m.worker.status !== 'ready' ? ' Loading' : ''}</span><span class="row-acts">${actions(m, { quiet: true, single: true })}</span></div></td></tr>`;
  }).join('');
  const jevRow = reg.jev?.name ? `<tr data-id="${JEV}" class="ref ${selected === JEV ? 'sel' : ''}" tabindex="0"><td><div class="mname"><span class="logo sm">${reg.makers[reg.jev.maker_id]?.logo ? `<img src="/ui/${esc(reg.makers[reg.jev.maker_id].logo)}" alt="">` : ''}</span><div><b>${esc(reg.jev.name)} <span class="chip tag">Reference</span></b><span>TypeSafe AI's hosted model, for comparison</span></div></div></td>
    <td class="num muted">n/a</td><td class="num"></td><td><span class="ins">${icon('text-t')}</span></td><td></td><td><div class="st"><span class="word">Hosted only</span></div></td></tr>` : '';
  const html = `<table class="table hover mtable"><colgroup><col><col class="c-size"><col class="c-mem"><col class="c-reads"><col class="c-jev"><col class="c-st"></colgroup><thead><tr>${th('name', 'Model')}${th('size', 'Size', 'num')}${th('memory', '<span data-tip="Memory it needs when loaded">Needs</span>', 'num')}<th>Reads</th>${th('jev', 'Ahead of Jev')}<th style="text-align:right">Status</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="6"><div class="empty" style="padding:24px"><p>No model matches "${esc(filter.q)}".</p></div></td></tr>`}${jevRow}</tbody></table>`;
  const onDisk = st.models.filter((m) => m.downloaded).length;
  // Count a shared base model (for example the Qwen base under Kev 4B and Lev) once.
  const bases = new Map();
  const disk = st.models.reduce((a, m) => {
    if (!m.downloaded) return a + (m.download?.have || 0);
    if (m.base) bases.set(m.base.id, m.base_bytes || 0);
    return a + (m.download_bytes - (m.base ? m.base_bytes || 0 : 0));
  }, 0) + [...bases.values()].reduce((a, v) => a + v, 0);
  const foot = `<div class="table-foot">${onDisk} of ${st.models.length} models on this machine, taking up ${fmtBytes(disk)} of disk.${st.hf_cache ? ` <code>${esc(st.hf_cache)}</code>` : ''}</div>`;
  const all = html + foot;
  if (box.dataset.html !== all) { box.dataset.html = all; box.innerHTML = all; bindActions(box); }
  setSub(`${onDisk} of ${st.models.length} on this machine`);
}

// ------------------------------------------------------------------ inspector
function renderInspector() {
  const box = $('#insp', root);
  if (!box) return;
  if (selected === JEV) { box.innerHTML = jevInspector(); return; }
  const m = store.state?.models.find((x) => x.id === selected);
  if (!m) { box.innerHTML = '<div class="empty"><p>Select a model to see its details.</p></div>'; return; }
  const r = reg.models[m.id] || {};
  const maker = reg.makers[r.maker_id] || {};
  box.innerHTML = `
    <div class="hero">${logoFor(m, r, 'lg')}<div style="min-width:0"><h2>${esc(m.name)}</h2>
      <p>${esc(clean(r.maker || m.maker))}${maker.url ? ` <a href="${esc(maker.url)}" target="_blank" rel="noopener" aria-label="Publisher website">${icon('arrow-square-out')}</a>` : ''}${m.badge ? ` <span class="chip ${m.badge === 'Start here' ? 'violet' : ''}" style="margin-left:4px">${esc(m.badge)}</span>` : ''}</p></div></div>
    <div class="acts" id="acts"></div>
    <div class="tabsbar"><span class="seg full" role="tablist">${[['overview', 'Overview'], ['jev', 'vs Jev'], ['load', 'Load']].map(([k, l]) => `<button role="tab" data-tab="${k}" aria-pressed="${tab === k}" aria-selected="${tab === k}">${l}</button>`).join('')}</span></div>
    <div class="tabbody" id="tabbody"></div>`;
  $$('[data-tab]', box).forEach((b) => b.addEventListener('click', () => { tab = b.dataset.tab; $$('[data-tab]', box).forEach((x) => { x.setAttribute('aria-pressed', x === b); x.setAttribute('aria-selected', x === b); }); renderTab(); }));
  renderActs(); renderTab();
}

function renderActs() {
  const box = $('#acts', root);
  const m = store.state?.models.find((x) => x.id === selected);
  if (!box || !m) return;
  const html = `${actions(m, { size: '', full: true })}<span class="status">${phaseTrack(m)}${statusWord(m)}</span>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; bindActions(box); }
}

function renderTab() {
  const box = $('#tabbody', root);
  const m = store.state?.models.find((x) => x.id === selected);
  if (!box || !m) return;
  const r = reg.models[m.id] || {};
  if (tab === 'overview') box.innerHTML = overview(m, r);
  if (tab === 'jev') box.innerHTML = vsJev(m, r);
  if (tab === 'load') { const html = loadTab(m); if (box.dataset.html === html) return; box.dataset.html = html; box.innerHTML = html; bindLoad(box, m); }
  else box.dataset.html = '';
  bindActions(box);
}

function overview(m, r) {
  const facts = [
    [term('parameters', 'Parameters'), esc(m.params)],
    ['Memory when loaded', `about ${fmtGB(m.memory_gb)}`],
    ['Reads', esc(inputs(m))],
    ['Languages', esc(lang(m))],
    [term('context', 'Context'), `${m.context_tokens.toLocaleString()} tokens`],
    ['Download', fmtBytes(m.download_bytes)],
    ['License', esc(m.license)],
  ];
  return `<p class="lede">${esc(clean(m.summary))}</p>
    <div class="group">${facts.map(([k, v]) => `<div class="grow"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')}</div>
    <div class="gw">
      <div class="good"><h3>Good for</h3><ul>${m.good_for.map((x) => `<li>${icon('check-circle')}<span>${esc(clean(x))}</span></li>`).join('')}</ul></div>
      <div class="watch"><h3>Watch out for</h3><ul>${m.watch_out.map((x) => `<li>${icon('warning')}<span>${esc(clean(x))}</span></li>`).join('')}</ul></div>
    </div>
    <details class="disclose"><summary>Technical details${icon('caret-right', 'chev')}</summary><div class="body">
      <dl class="specs">
        ${r.architecture ? `<dt>Architecture</dt><dd>${esc(clean(r.architecture))}</dd>` : ''}
        ${r.base_model ? `<dt>${term('base_model', 'Base model')}</dt><dd><code>${esc(r.base_model)}</code></dd>` : ''}
        ${r.training ? `<dt>Training</dt><dd>${esc(clean(r.training))}</dd>` : ''}
        <dt>Question types</dt><dd>All six: ${m.types.map((t) => ({ choice: 'pick one', score: 'scale', noul: 'yes or no' }[t])).join(', ')} natively, plus pick any, order and number as ${term('extensions', 'extensions')}</dd>
        <dt>Options per question</dt><dd>up to ${m.max_options.toLocaleString()}</dd>
        <dt>Questions per pass</dt><dd>${m.one_pass ? 'all at once' : 'one at a time'}</dd>
        <dt>Source</dt><dd><a href="${esc(m.hf_url)}" target="_blank" rel="noopener">${esc(m.repo.id)}</a>${r.paper_or_code_url && !isLocal(r.paper_or_code_url) ? `<br><a href="${esc(r.paper_or_code_url)}" target="_blank" rel="noopener">Code or paper</a>` : ''}</dd>
      </dl></div></details>`;
}

function resultRow(m, b, withJev = true) {
  const mv = esc(fmtVal(b, b.model, b.model_display));
  const jv = esc(fmtVal(b, b.jev, b.jev_display));
  const name = b.note ? `<span class="term" tabindex="0" data-tip="${esc(clean(b.note))}">${esc(clean(b.suite))}</span>` : esc(clean(b.suite));
  return `<div class="grow stack"><span>${name}</span><span class="v small" style="display:flex;gap:8px;justify-content:space-between"><span>${esc(clean(b.metric))}${b.higher_is_better ? '' : ', lower is better'}</span>
    <span class="num">${withJev ? `<b style="${wins(b) ? 'color:var(--accent-text);' : ''}font-weight:600">${mv}</b> vs ${jv}` : `<b style="font-weight:600">${mv}</b>`}</span></span></div>`;
}

function vsJev(m, r) {
  const s = h2h(r);
  const top = headlineRows(s.rows);
  const other = [...(r.benchmarks || []), ...(r.independent || [])].filter((b) => b.jev == null && b.model != null);
  if (!s.n && !other.length) return '<div class="empty" style="padding:24px"><p>No published results for this model yet.</p></div>';
  const pct = s.rows.filter(onPctAxis), rest = s.rows.filter((b) => !onPctAxis(b));
  const srcs = [...new Set([...s.rows, ...other].map((b) => b.source))];
  const links = srcs.filter((u) => !isLocal(u)).map((u) => { let h = u; try { const x = new URL(u); h = (x.hostname + x.pathname).replace(/\/$/, ''); } catch { /* keep */ } return `<li><a href="${esc(u)}" target="_blank" rel="noopener">${esc(h.length > 60 ? `${h.slice(0, 58)}...` : h)}</a></li>`; });
  for (const u of new Set(srcs.filter(isLocal).map((x) => String(x).replace(/\s*\(.*$/, '')))) if (u) links.push(`<li>${esc(u)}</li>`);
  return `${s.n ? `<div style="display:grid;gap:8px">
      <div style="display:flex;align-items:baseline;gap:8px"><b style="font-size:22px;font-weight:650;letter-spacing:-0.02em" class="num">${s.w} of ${s.n}</b><span class="muted">published results favour ${esc(m.name)}</span></div>
      ${winbar(s, 'lg')}
      <div class="legend"><span><i style="background:var(--data-3)"></i>Ahead</span>${s.t ? '<span><i style="background:var(--data-1)"></i>Level</span>' : ''}<span><i style="background:var(--text-3)"></i>Behind</span></div></div>` : ''}
    ${top.length ? `<div style="display:grid;gap:10px">
      <div style="display:flex;align-items:center"><h3 style="font-size:12.5px;color:var(--text-2)">Headline results</h3><span class="legend" style="margin-left:auto"><span><i style="background:var(--data-3)"></i>${esc(m.name)}</span><span><i style="background:var(--text-3)"></i>Jev</span></span></div>
      <figure class="hl">${top.map((b) => `<div class="hl-row"><div class="top"><span class="nm" title="${esc(clean(b.suite))}">${esc(clean(b.suite))}</span><span class="vals"><b>${esc(fmtVal(b, b.model, b.model_display))}</b> <span class="muted">vs ${esc(fmtVal(b, b.jev, b.jev_display))}</span></span></div>${dumbbell(b)}</div>`).join('')}
        <div class="ticks"><span>0</span><span>25</span><span>50</span><span>75</span><span>100%</span></div></figure></div>` : ''}
    <div>
      ${pct.length ? `<details class="disclose"><summary>Accuracy and scores <span class="muted num" style="font-weight:400">${pct.length}</span>${icon('caret-right', 'chev')}</summary><div class="body"><div class="group">${pct.map((b) => resultRow(m, b)).join('')}</div></div></details>` : ''}
      ${rest.length ? `<details class="disclose"><summary>Calibration, speed and cost <span class="muted num" style="font-weight:400">${rest.length}</span>${icon('caret-right', 'chev')}</summary><div class="body"><div class="group">${rest.map((b) => resultRow(m, b)).join('')}</div></div></details>` : ''}
      ${other.length ? `<details class="disclose"><summary>Without a Jev comparison <span class="muted num" style="font-weight:400">${other.length}</span>${icon('caret-right', 'chev')}</summary><div class="body"><div class="group">${other.map((b) => resultRow(m, b, false)).join('')}</div></div></details>` : ''}
      <details class="disclose"><summary>Sources${icon('caret-right', 'chev')}</summary><div class="body">
        ${r.headline ? `<p class="small muted">${esc(clean(r.headline))}</p>` : ''}<ul class="sources">${links.join('')}</ul>
        <p class="help">Publisher numbers are the makers' own claims; independent results come from third parties. Test on your own data in Evaluate before relying on either.</p></div></details>
    </div>`;
}

function optionField(o) {
  if (o.type === 'select' && o.choices.length <= 3) return `<div class="setting"><div class="top"><label>${esc(o.label)}</label></div>
    <span class="seg full" data-opt="${o.key}">${o.choices.map(([v, l]) => `<button type="button" data-v="${esc(v)}" aria-pressed="${v === o.default}">${esc(l.replace(/\s*\(.*\)/, ''))}</button>`).join('')}</span><span class="help">${esc(clean(o.help))}</span></div>`;
  if (o.type === 'select') return `<div class="setting"><div class="top"><label for="opt-${o.key}">${esc(o.label)}</label></div><select class="select" id="opt-${o.key}" name="${o.key}">${o.choices.map(([v, l]) => `<option value="${esc(v)}" ${v === o.default ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select><span class="help">${esc(clean(o.help))}</span></div>`;
  if (o.type === 'number') return `<div class="setting"><div class="top"><label for="opt-${o.key}">${esc(o.label)}</label><input class="input num-field" type="number" id="opt-${o.key}" name="${o.key}" value="${o.default}" min="${o.min}" max="${o.max}" step="${o.step}"></div>
    <input type="range" data-for="opt-${o.key}" min="${o.min}" max="${o.max}" step="${o.step}" value="${o.default}" aria-label="${esc(o.label)}"><span class="help">${esc(clean(o.help))}</span></div>`;
  if (o.type === 'toggle') return `<div class="setting"><label class="switch" style="justify-content:space-between"><span style="font-weight:500">${esc(o.label)}</span><input type="checkbox" name="${o.key}" ${o.default ? 'checked' : ''}></label><span class="help">${esc(clean(o.help))}</span></div>`;
  return '';
}

function loadTab(m) {
  const opts = m.load_options || [];
  let head;
  if (m.worker) head = `<div class="note">${icon('info')}<span>Loaded with ${Object.entries(m.worker.options || {}).map(([k, v]) => `${esc(k.replace(/_/g, ' '))} <b>${esc(String(v))}</b>`).join(', ') || 'the defaults'}. Eject it to change these settings.</span></div>`;
  else if (!m.downloaded) head = `<div class="note">${icon('info')}<span>Download ${esc(m.name)} (${fmtBytes(m.download.remaining || m.download_bytes)}) to choose how it runs.</span></div>`;
  else head = '';
  return `${head}
    ${m.downloaded && !m.worker ? `<form class="insp-sec" id="loadform">${opts.map(optionField).join('')}
      <button class="btn btn-primary" type="submit">${icon('play')}Load with these settings</button></form>` : ''}
    <div class="insp-sec"><h3>Files</h3><div class="group">
      <div class="grow"><span class="k">On disk</span><span class="v">${fmtBytes(m.download.have)}${m.base ? ', including the shared base model' : ''}</span></div>
      ${m.worker ? `<div class="grow link" data-logs="${m.id}"><span class="k">View the model log</span><span class="v">${icon('caret-right')}</span></div>` : ''}
      ${m.downloaded || m.download.have > 0 ? `<div class="grow link" data-delete="${m.id}"><span class="k" style="color:var(--red-text)">Delete files</span><span class="v">${icon('trash')}</span></div>` : ''}
    </div></div>`;
}

function bindLoad(box, m) {
  // Number fields and sliders move together, like LM Studio's load settings.
  const fill = (r) => r.style.setProperty('--fill-pct', `${((r.value - r.min) / (r.max - r.min || 1)) * 100}%`);
  $$('input[type=range][data-for]', box).forEach((r) => {
    const n = $(`#${r.dataset.for}`, box);
    fill(r);
    r.addEventListener('input', () => { n.value = r.value; fill(r); });
    n.addEventListener('input', () => { r.value = n.value; fill(r); });
  });
  $$('[data-opt] button', box).forEach((b) => b.addEventListener('click', () => $$('button', b.parentElement).forEach((x) => x.setAttribute('aria-pressed', x === b))));
  $('#loadform', box)?.addEventListener('submit', (e) => {
    e.preventDefault();
    const o = {};
    for (const el of e.target.elements) if (el.name) o[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
    $$('[data-opt]', box).forEach((s) => { o[s.dataset.opt] = $('[aria-pressed="true"]', s)?.dataset.v; });
    loadModel(m.id, o);
  });
}

function jevInspector() {
  const j = reg.jev;
  const maker = reg.makers[j.maker_id] || {};
  return `<div class="hero"><span class="logo lg">${maker.logo ? `<img src="/ui/${esc(maker.logo)}" alt="">` : ''}</span><div><h2>${esc(j.name)}</h2><p>${esc(j.maker)}, released ${esc(j.released)}</p></div></div>
    <div class="tabbody">
      <p class="lede">${term('jev', 'Jev')} is TypeSafe AI's hosted decision model and the reference every model here is compared with. It cannot run on this machine, but every model here speaks its API, so code written for Jev runs here by changing the base URL.</p>
      <div class="group">
        <div class="grow stack"><span class="k">Price</span><span class="v">${esc(clean(j.pricing))}</span></div>
        <div class="grow stack"><span class="k">Latency</span><span class="v">${esc(clean(j.latency_band))}</span></div>
        <div class="grow stack"><span class="k">Context</span><span class="v">${esc(clean(j.context))}</span></div>
        <div class="grow stack"><span class="k">Question types</span><span class="v">${esc(clean(j.question_types))}</span></div>
      </div>
      <div class="insp-sec"><h3>Independent measurements</h3><div class="group">${(j.independent || []).map((b) => `<div class="grow"><span class="k">${esc(clean(b.suite))}<br><span class="small muted">${esc(clean(b.metric))}${b.higher_is_better ? '' : ', lower is better'}</span></span><span class="v num">${esc(fmtVal(b, b.value))}</span></div>`).join('')}</div></div>
      ${j.docs_url ? `<a href="${esc(j.docs_url)}" target="_blank" rel="noopener">${icon('arrow-square-out')} Jev API documentation</a>` : ''}
    </div>`;
}

// ------------------------------------------------------------------ help me choose, download all
function recommend() {
  const { input, language, priority } = finder;
  if (input === 'av') return ['jev-omni', 'the only model here that listens to audio and watches video.'];
  if (input === 'image') return ['intern-decision-4b', 'it reads up to 8 images per request.'];
  if (language === 'multi') return priority === 'speed' ? ['julia-1', 'tiny, multilingual and fast.'] : ['laya-multilingual', 'it covers 100+ languages.'];
  if (priority === 'speed') return ['gliner2.5-decide', 'small and fast, even on a CPU.'];
  if (priority === 'options') return ['lev', 'it handles hundreds of options per question.'];
  if (priority === 'agents') return ['clm-v0.1-8b', 'trained on agent trajectories: picking tools and next actions.'];
  return ['intern-decision-4b', 'the best all-rounder in its publisher\'s tests.'];
}
function helpMeChoose() {
  const d = document.createElement('dialog');
  const seg = (key, opts) => `<span class="seg full" role="group">${opts.map(([v, l]) => `<button type="button" data-k="${key}" data-v="${v}" aria-pressed="${finder[key] === v}">${l}</button>`).join('')}</span>`;
  const draw = () => {
    const [id, why] = recommend();
    const m = store.state.models.find((x) => x.id === id);
    d.innerHTML = `<form method="dialog"><div class="dialog-body" style="gap:14px"><h2 style="font-size:17px">Which model should I use?</h2>
      <div class="field"><span class="label">What will you give it?</span>${seg('input', [['text', 'Text'], ['image', 'Images'], ['av', 'Audio or video']])}</div>
      <div class="field"><span class="label">In which language?</span>${seg('language', [['en', 'English'], ['multi', 'Other or mixed']])}</div>
      <div class="field"><span class="label">What matters most?</span>${seg('priority', [['speed', 'Speed'], ['balanced', 'Accuracy'], ['options', 'Many options'], ['agents', 'Agents']])}</div>
      <div class="note violet">${icon('lightbulb')}<span><b>${esc(m.name)}</b>: ${esc(why)}</span></div></div>
      <div class="dialog-foot"><button class="btn" value="cancel">Close</button><button class="btn btn-primary" value="${id}">Show ${esc(m.name)}</button></div></form>`;
    $$('[data-k]', d).forEach((b) => b.addEventListener('click', () => { finder[b.dataset.k] = b.dataset.v; draw(); }));
  };
  document.body.append(d); draw();
  d.addEventListener('close', () => { const v = d.returnValue; d.remove(); if (v && v !== 'cancel') { filter = { q: '', show: 'all', input: 'any' }; render(); select(v); } });
  d.showModal();
}
async function del(id) {
  const m = store.state.models.find((x) => x.id === id);
  const shared = m.base && store.state.models.some((o) => o.id !== id && o.downloaded && o.base?.id === m.base.id);
  const ok = await confirmDialog({
    title: `Delete ${m.name}'s files?`,
    body: `<p>This frees about <b>${fmtBytes(m.download.have)}</b> of disk${shared ? `, keeping the ${esc(m.base.id)} base because another model uses it` : ''}. You can download it again at any time.</p>`,
    confirm: 'Delete files', danger: true,
  });
  if (!ok) return;
  try { const r = await api(`/api/models/${id}/files`, { method: 'DELETE' }); toast(r.removed.length ? `Deleted ${m.name}'s files` : 'Nothing to delete'); }
  catch (e) { toast(e.message, 'error'); }
  refresh();
}

export async function showLogs(id) {
  const text = await (await api(`/api/models/${id}/logs?lines=300`, { raw: true })).text();
  const d = document.createElement('dialog');
  d.style.maxWidth = '920px';
  d.innerHTML = `<div class="dialog-body"><h2 style="font-size:17px">Model log</h2><p class="help">What the model's process printed while loading and running.</p>
    <div class="code"><pre style="max-height:60vh;padding:12px 14px;white-space:pre-wrap">${esc(text || '(empty)')}</pre></div></div><div class="dialog-foot"><button class="btn" id="cl">Close</button></div>`;
  document.body.append(d);
  d.querySelector('#cl').onclick = () => d.close();
  d.addEventListener('close', () => d.remove());
  d.showModal();
}

export { setPref };
