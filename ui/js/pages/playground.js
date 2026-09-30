// Playground: the situation and questions on the left, the answers in the middle, decision settings in an
// inspector on the right. The model button opens the loader palette (Ctrl+L), as in LM Studio.

import { addGridHTML, buildQuestions, createBuilder, fromQuestion } from '../builder.js';
import { EXAMPLES } from '../examples.js';
import { guideFor } from '../model-guides.js';
import { animate, decisionOf, figures, mini, odo } from '../figures.js';
import { codeBlock, jsonTree } from '../format.js';
import { openLoader } from '../loader.js';
import { setSub } from '../shell.js';
import { FORMATS, LANGS, snippet } from '../snippets.js';
import { api, decide, download, ensureReady, markLearned, model, phaseOf, phaseTrack, readyModels, registry, setPref, store } from '../store.js';
import { $, $$, copy, debounce, esc, fmtBytes, fmtGB, fmtMs, fmtNum, fmtPct, icon, popMenu, term, toast } from '../util.js';

const KEY = 'bud.draft.v2';
let draft = loadDraft();
let last = null;
let tab = 'answers';
let busy = false;
let root, builder, keyHandler, reg;
let pendingModel = null;   // a model named in the URL before the first state poll has arrived
let exampleForPending = false;   // ...and whether to show that model's own example once it is applied
let lang = 'python', format = 'typesafe';
let inspOpen = null;
let hint = null;   // the last "what's missing" message, cleared as soon as the person fixes it
const clearHint = () => { hint?.remove(); hint = null; };

// ------------------------------------------------------------------ draft
function fromExample(ex, keep = draft) {
  const isObj = typeof ex.state !== 'string';
  return {
    ...keep, example: ex.id, stateMode: isObj ? 'json' : 'text',
    stateText: isObj ? JSON.stringify(ex.state, null, 2) : ex.state,
    questions: Object.entries(ex.questions).map(([k, q]) => fromQuestion(k, q)), media: [],
  };
}
function loadDraft() {
  try { const d = JSON.parse(localStorage.getItem(KEY)); if (d?.questions) return { media: [], temperature: null, compare: false, ...d }; } catch { /* fresh start */ }
  return fromExample(EXAMPLES.find((e) => e.id === 'support'), { model: null, temperature: null, compare: false, media: [] });
}
const saveDraft = debounce(() => { try { localStorage.setItem(KEY, JSON.stringify(draft)); } catch { /* storage full */ } }, 250);

function parseState() {
  if (draft.stateMode === 'json') {
    try { return { value: JSON.parse(draft.stateText) }; } catch (e) { return { error: `The situation is not valid JSON: ${e.message}` }; }
  }
  return { value: draft.stateText };
}

export function buildRequest() {
  if (!String(draft.stateText).trim()) return { error: 'Describe the situation first: paste an email, a ticket, a message or some JSON.', field: 'state' };
  const st = parseState();
  if (st.error) return { error: st.error, field: 'state' };
  const built = buildQuestions(draft.questions, model(draft.model));
  if (built.error) return built;
  const request = { model: draft.model, state: st.value, questions: built.questions };
  if (draft.temperature) request.settings = { temperature: draft.temperature };
  if (draft.media.length) request.media = draft.media.map((m) => ({ type: m.type, path: m.id, name: m.name }));
  return { request };
}

// Used by Evaluate to hand over a fitted calibration temperature.
export function setTemperature(t) {
  draft.temperature = t || null;
  try { localStorage.setItem(KEY, JSON.stringify(draft)); } catch { /* ignore */ }
}
// Used by Activity to replay a logged request.
export function loadRequest(req, modelId) {
  const isObj = typeof req.state !== 'string';
  draft = { ...draft, model: modelId || req.model || draft.model, stateMode: isObj ? 'json' : 'text', stateText: isObj ? JSON.stringify(req.state, null, 2) : req.state,
    questions: Object.entries(req.questions || {}).map(([k, q]) => fromQuestion(k, q)), media: [] };
  last = null;
  try { localStorage.setItem(KEY, JSON.stringify(draft)); } catch { /* ignore */ }
}

const statePlaceholder = () => (draft.stateMode === 'json' ? '{ "ticket": "..." }' : guideFor(draft.model)?.state || 'Paste an email, a support ticket, a review, a log line...');

// An example exactly as shipped (not edited), so it can be swapped for one that suits the chosen model.
function pristineExample() {
  const ex = EXAMPLES.find((e) => e.id === draft.example);
  if (!ex) return false;
  const p = fromExample(ex, {});
  return draft.stateText === p.stateText && draft.questions.length === p.questions.length
    && draft.questions.every((q, i) => q.text === p.questions[i].text && q.type === p.questions[i].type);
}

function loadExample(ex) {
  draft = fromExample(ex); last = null; saveDraft();
  if (ex.needsMedia && !model(draft.model)?.modalities.includes(ex.needsMedia)) {
    const alt = (store.state?.models || []).find((m) => m.modalities.includes(ex.needsMedia) && (m.worker || m.downloaded));
    if (alt) { draft.model = alt.id; toast(`Switched to ${alt.name}: it can read images.`); }
  }
  render();
  if (ex.sample) attachSample(ex.sample);
}

// Choosing a model while an untouched example is showing swaps in an example made for that model, so what the
// model is good at is visible straight away (CLM opens on an agent's next action, not a support ticket).
function showModelExample({ quiet = false } = {}) {
  const g = guideFor(draft.model);
  if (!g || !pristineExample() || g.examples.includes(draft.example)) return;
  const ex = EXAMPLES.find((e) => e.id === g.examples[0]);
  if (!ex) return;
  const before = { draft: JSON.parse(JSON.stringify(draft)), last };
  const keepModel = draft.model;
  draft = fromExample(ex); draft.model = keepModel; last = null; saveDraft();
  if (root?.isConnected) { render(); if (ex.sample) attachSample(ex.sample); }
  const m = model(keepModel);
  if (quiet) return;
  toast(`Showing an example made for ${m?.name || 'this model'}.`, '', { action: 'Undo', onAction: () => { draft = before.draft; last = before.last; saveDraft(); if (root?.isConnected) render(); } });
}

// ------------------------------------------------------------------ page lifecycle
export async function mount(el, params = {}) {
  root = el;
  reg = await registry();
  if (params.example) {
    const ex = EXAMPLES.find((e) => e.id === params.example);
    if (ex) {
      draft = fromExample(ex); last = null;
      if (ex.needsMedia) {
        const can = (store.state?.models || []).filter((m) => m.modalities.includes(ex.needsMedia) && (m.downloaded || m.worker));
        if (can.length) draft.model = (can.find((m) => m.worker?.status === 'ready') || can[0]).id;
      }
      saveDraft();
      if (ex.sample) setTimeout(() => attachSample(ex.sample), 0);
    }
  }
  pendingModel = params.model || null;
  exampleForPending = !params.example && !!params.model;
  applyPendingModel();
  pickDefaultModel();
  if (inspOpen === null) inspOpen = false;
  render();
  keyHandler = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); run(); }
    if ((e.metaKey || e.ctrlKey) && (e.key === 'l' || e.key === 'L')) { e.preventDefault(); pickModel(); }
  };
  document.addEventListener('keydown', keyHandler);
  maybeAutorun();
}
export function unmount() { document.removeEventListener('keydown', keyHandler); }

function applyPendingModel() {
  if (!pendingModel || !store.state) return false;
  if (model(pendingModel)) { draft.model = pendingModel; saveDraft(); }
  pendingModel = null;
  if (exampleForPending) { exampleForPending = false; showModelExample(); }
  return true;
}

// First visit in this browser session: if a model is already loaded, run the example so the answer is the first thing seen.
function maybeAutorun() {
  if (pendingModel) return;
  if (last || busy || sessionStorage.getItem('bud.autorun') || model(draft.model)?.worker?.status !== 'ready') return;
  sessionStorage.setItem('bud.autorun', '1');
  setTimeout(run, 350);
}

export function onState() {
  if (!root?.isConnected || !$('#play', root)) return;
  if (applyPendingModel()) { renderMedia(); builder.render(); }
  pickDefaultModel();
  renderModelChip();
  renderStateMeta();
  if (!last && !busy) renderOut();
  maybeAutorun();
}

function pickDefaultModel() {
  const ms = store.state?.models || [];
  const cur = ms.find((m) => m.id === draft.model);
  if (cur && (cur.downloaded || cur.worker)) return;
  const pick = readyModels()[0] || ms.find((m) => m.worker) || ms.find((m) => m.badge === 'Start here' && m.downloaded) || ms.find((m) => m.id === 'laya' && m.downloaded) || ms.find((m) => m.downloaded);
  if (pick && pick.id !== draft.model) { draft.model = pick.id; saveDraft(); showModelExample({ quiet: true }); }
}

async function pickModel() {
  const id = await openLoader({ current: draft.model });
  if (!id || !root?.isConnected) return;
  draft.model = id; setPref('lastModel', id); saveDraft();
  if (pristineExample() && !guideFor(id)?.examples.includes(draft.example)) { showModelExample(); return; }
  renderModelChip(); renderMedia(); renderStateMeta(); builder.render();
  const st = $('#state', root); if (st) st.placeholder = statePlaceholder();
  if (!last) renderOut();
}

// ------------------------------------------------------------------ layout
function render() {
  root.innerHTML = `<div class="view play" id="play">
    <section class="spec" id="spec" aria-label="Decision">
      <div class="spec-top">
        <button class="model-chip" id="mchip" aria-haspopup="dialog" aria-label="Choose a model"></button>
        <button class="btn" id="newdec" data-tip="Start a blank decision: your own situation and questions">${icon('plus')}New</button>
        <button class="btn" id="examples" aria-haspopup="menu">${icon('sparkle')}Examples</button>
      </div>
      <div class="spec-scroll">
        <div class="spec-section">
          <div class="spec-title"><h2>${term('state', 'State')}</h2><span class="muted small">the situation to judge</span>
            <span class="right"><span class="seg" role="group" aria-label="State format"><button data-mode="text" aria-pressed="${draft.stateMode === 'text'}">Text</button><button data-mode="json" aria-pressed="${draft.stateMode === 'json'}">JSON</button></span></span></div>
          <textarea class="textarea state-box ${draft.stateMode === 'json' ? 'code' : ''}" id="state" spellcheck="${draft.stateMode === 'text'}" aria-label="State"
            placeholder="${esc(statePlaceholder())}">${esc(draft.stateText)}</textarea>
          <div class="state-meta" id="statemeta"></div>
          <div id="media"></div>
        </div>
        <div class="spec-section">
          <div class="spec-title"><h2>Questions</h2><span class="right small muted" id="qcount"></span></div>
          <div class="branches" id="qs"></div>
          ${addGridHTML()}
        </div>
      </div>
      <div class="decide-bar"><button class="btn btn-primary" id="decide">${icon(draft.compare ? 'columns' : 'play')}${draft.compare ? 'Compare' : 'Decide'}</button><span class="small muted"><kbd>Ctrl</kbd> <kbd>Enter</kbd></span></div>
    </section>
    <section class="results" aria-label="Answers">
      <div class="results-head">
        <div class="utabs" role="tablist" id="tabs">${[['answers', 'Answers', 'chart-bar'], ['json', 'JSON', 'brackets-curly'], ['code', 'Code', 'code']].map(([k, n, i]) => `<button role="tab" data-tab="${k}" aria-pressed="${tab === k}" aria-selected="${tab === k}">${icon(i)}${n}</button>`).join('')}</div>
        <button class="icon-btn" id="insptoggle" aria-pressed="${inspOpen}" aria-label="Decision settings" data-tip="Decision settings">${icon('sliders-horizontal')}</button>
      </div>
      <div class="results-body">
        <div class="run-stamp" id="stamp"></div>
        <div id="out" aria-live="polite"></div>
      </div>
    </section>
    <aside class="inspector" id="insp" ${inspOpen ? '' : 'hidden'} aria-label="Decision settings"></aside>
  </div>`;

  builder = createBuilder($('#qs', root), {
    list: () => draft.questions,
    spec: () => model(draft.model),
    onChange: () => { saveDraft(); renderCount(); if (tab === 'code') renderOut(); },
    onStructure: () => { renderCount(); clearHint(); if (!last && !busy) renderOut(); },
  });
  builder.render();
  renderModelChip(); renderStateMeta(); renderMedia(); renderCount(); renderInspector(); renderOut();
  bind();
}

function bind() {
  const st = $('#state', root);
  st.addEventListener('input', () => {
    const had = !!draft.stateText.trim();
    draft.stateText = st.value; saveDraft(); renderStateMeta(); clearHint();
    if (had !== !!st.value.trim() && !last && !busy) renderOut();
  });
  $$('[data-mode]', root).forEach((b) => b.addEventListener('click', () => {
    const mode = b.dataset.mode;
    if (mode === draft.stateMode) return;
    if (mode === 'json' && draft.stateText.trim() && !/^[[{]/.test(draft.stateText.trim())) draft.stateText = JSON.stringify({ text: draft.stateText }, null, 2);
    else if (mode === 'text') { try { const v = JSON.parse(draft.stateText); if (typeof v?.text === 'string' && Object.keys(v).length === 1) draft.stateText = v.text; } catch { /* keep as is */ } }
    draft.stateMode = mode; saveDraft(); render();
  }));
  $$('[data-add-type]', root).forEach((b) => b.addEventListener('click', () => builder.add(b.dataset.addType)));
  $('#mchip', root).addEventListener('click', pickModel);
  $('#examples', root).addEventListener('click', (e) => examplesMenu(e.currentTarget));
  $('#newdec', root).addEventListener('click', newDecision);
  $('#decide', root).addEventListener('click', run);
  $('#tabs', root).addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) { tab = b.dataset.tab; $$('[data-tab]', root).forEach((x) => { x.setAttribute('aria-pressed', x === b); x.setAttribute('aria-selected', x === b); }); renderOut({ instant: true }); } });
  $('#insptoggle', root).addEventListener('click', (e) => { inspOpen = !inspOpen; e.currentTarget.setAttribute('aria-pressed', inspOpen); $('#insp', root).hidden = !inspOpen; });
}

function renderCount() {
  const m = model(draft.model);
  const el = $('#qcount', root);
  if (el) el.textContent = draft.questions.length ? `${draft.questions.length}${m?.max_questions ? ` of up to ${m.max_questions}` : ''}, answered in one pass` : 'None yet';
}

function renderStateMeta() {
  const el = $('#statemeta', root);
  if (!el) return;
  const m = model(draft.model);
  const tokens = Math.round(draft.stateText.length / 4);
  let msg = `<span>About ${tokens.toLocaleString()} ${term('token', 'tokens')}</span>`;
  if (m?.context_tokens) {
    msg += tokens / m.context_tokens > 0.9 ? `<span style="color:var(--orange-text)">${icon('warning')} close to ${esc(m.name)}'s ${m.context_tokens.toLocaleString()}-token limit</span>` : `<span>${esc(m.name)} reads up to ${m.context_tokens.toLocaleString()}</span>`;
  }
  if (draft.stateMode === 'json') { const p = parseState(); msg += p.error ? `<span style="color:var(--red-text)">${icon('warning-circle')} ${esc(p.error.replace('The situation is not valid JSON: ', 'Invalid JSON: '))}</span>` : '<span>Valid JSON</span>'; }
  if (el.innerHTML !== msg) el.innerHTML = msg;
}

// ------------------------------------------------------------------ model button (opens the loader)
function renderModelChip() {
  const box = $('#mchip', root);
  if (!box) return;
  const m = model(draft.model);
  let html;
  if (!m) html = `<span class="logo">${icon('cube')}</span><span><b>Choose a model</b><span class="sub">Nothing downloaded yet</span></span>${icon('caret-down', 'chev')}`;
  else {
    const r = reg?.models[m.id] || {};
    const mk = reg?.makers[r.maker_id] || {};
    const src = mk.avatar || r.logo || mk.logo;
    const p = phaseOf(m);
    html = `<span class="logo">${src ? `<img src="/ui/${esc(src)}" alt="">` : icon('cube')}</span>
      <span style="min-width:0"><b>${esc(m.name)}</b><span class="sub">${phaseTrack(m)}${esc(p.word)}, ${esc(m.params)}, ${fmtGB(m.memory_gb)}</span></span>
      <span class="chev" style="display:flex;align-items:center;gap:6px"><kbd>Ctrl</kbd><kbd>L</kbd>${icon('caret-down')}</span>`;
  }
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
  setSub(m ? `${esc(m.name)}, ${esc(phaseOf(m).word.toLowerCase())}` : '');
}

function examplesMenu(anchor) {
  const groups = [...new Set(EXAMPLES.map((e) => e.group))];
  const blank = `<div class="menu-group">Start</div><button class="menu-item" role="menuitem" data-pick="__blank">${icon('plus')}<span><b>Blank decision</b><span>Your own situation and questions, from scratch.</span></span></button>`;
  const m = model(draft.model), g = guideFor(draft.model);
  const mine = (g?.examples || []).map((id) => EXAMPLES.find((e) => e.id === id)).filter(Boolean);
  const item = (e) => `<button class="menu-item" role="menuitem" data-pick="${e.id}">${icon(e.needsMedia ? 'image' : 'sparkle')}<span><b>${esc(e.title)}</b><span>${esc(e.blurb)}</span></span></button>`;
  const made = mine.length ? `<div class="menu-group">Made for ${esc(m?.name || 'this model')}</div>${mine.map(item).join('')}` : '';
  popMenu(anchor, made + blank + groups.map((gr) => [gr, EXAMPLES.filter((e) => e.group === gr && !mine.includes(e))]).filter(([, list]) => list.length).map(([gr, list]) => `<div class="menu-group">${esc(gr)}</div>${list.map((e) => item(e)).join('')}`).join(''), {
    onPick: (id) => {
      if (id === '__blank') { newDecision(); return; }
      loadExample(EXAMPLES.find((e) => e.id === id));
    },
  });
}

// ------------------------------------------------------------------ inspector: decision settings
function renderInspector() {
  const box = $('#insp', root);
  if (!box) return;
  const t = draft.temperature || 1;
  box.innerHTML = `<div class="insp-head"><h2>Decision settings</h2><button class="icon-btn" id="inspclose" aria-label="Close decision settings" style="margin-left:auto">${icon('x')}</button></div>
    <div class="insp-body">
      <div class="insp-sec"><h3>When to act</h3>
        <div class="setting"><div class="top"><label for="th-n">${term('threshold', 'Act threshold')}</label><input class="input num-field" id="th-n" type="number" min="50" max="99" step="1" value="${Math.round(store.prefs.threshold * 100)}" aria-label="Act threshold in percent"></div>
          <input type="range" id="th" min="0.5" max="0.99" step="0.01" value="${store.prefs.threshold}" aria-label="Act threshold">
          <span class="help">Answers at least this likely say "Act automatically"; the rest say "Ask a human".</span></div></div>
      <div class="insp-sec"><h3>Calibration</h3>
        <div class="setting"><div class="top"><label for="tp-n">${term('temperature', 'Temperature')}</label><input class="input num-field" id="tp-n" type="number" min="0.25" max="4" step="0.05" value="${t}" aria-label="Calibration temperature"></div>
          <input type="range" id="tp" min="0.25" max="4" step="0.05" value="${t}" aria-label="Calibration temperature">
          <span class="help">1 leaves the model's numbers as they are. Above 1 makes it less sure, below 1 more sure. Evaluate can fit one for you.</span></div></div>
      <div class="insp-sec"><h3>Compare</h3>
        <label class="switch" style="justify-content:space-between"><span>Ask every loaded model</span><input type="checkbox" id="cmp" ${draft.compare ? 'checked' : ''}></label>
        <span class="help">Decide sends the same request to all ${readyModels().length || 'loaded'} models at once and shows where they agree.</span></div>
      <div class="insp-sec"><h3>Robustness</h3>
        <button class="btn" id="order">${icon('shuffle')}Test option order</button>
        <span class="help">Reruns pick-one and order questions with shuffled options to check for ${term('order_bias', 'option-order bias')}.</span></div>
    </div>`;
  const fill = (r) => r.style.setProperty('--fill-pct', `${((r.value - r.min) / (r.max - r.min || 1)) * 100}%`);
  const th = $('#th', box), thn = $('#th-n', box), tp = $('#tp', box), tpn = $('#tp-n', box);
  fill(th); fill(tp);
  const setTh = (v) => { setPref('threshold', Math.min(0.99, Math.max(0.5, v))); if (last?.kind === 'result' && tab === 'answers') renderOut({ instant: true }); };
  th.addEventListener('input', () => { thn.value = Math.round(th.value * 100); fill(th); setTh(+th.value); });
  thn.addEventListener('change', () => { th.value = thn.value / 100; fill(th); setTh(thn.value / 100); });
  const setT = (v) => { draft.temperature = v > 0 && Math.abs(v - 1) > 0.001 ? v : null; saveDraft(); };
  tp.addEventListener('input', () => { tpn.value = tp.value; fill(tp); setT(+tp.value); });
  tpn.addEventListener('change', () => { tp.value = tpn.value; fill(tp); setT(+tpn.value); });
  $('#cmp', box).addEventListener('change', (e) => { draft.compare = e.target.checked; saveDraft(); $('#decide', root).innerHTML = `${icon(draft.compare ? 'columns' : 'play')}${draft.compare ? 'Compare' : 'Decide'}`; });
  $('#order', box).addEventListener('click', orderTest);
  $('#inspclose', box).addEventListener('click', () => { inspOpen = false; box.hidden = true; $('#insptoggle', root).setAttribute('aria-pressed', 'false'); });
}

// ------------------------------------------------------------------ media (images, audio, video)
function renderMedia() {
  const box = $('#media', root);
  if (!box) return;
  const sel = model(draft.model);
  const can = sel?.modalities?.filter((x) => x !== 'text') || [];
  if (!can.length && !draft.media.length) { box.innerHTML = ''; return; }
  const accept = can.map((k) => `${k}/*`).join(',') || 'image/*';
  const bad = [...new Set(draft.media.map((x) => x.type))].filter((t) => !can.includes(t));
  box.innerHTML = `<div style="display:grid;gap:8px">
    <label class="dropzone" id="dz"><input type="file" id="file" accept="${accept}" multiple hidden>
      ${icon('file-arrow-up')}<span><b style="font-weight:500">Attach ${can.join(', ') || 'media'}</b> <span class="muted">or drop a file here</span></span></label>
    ${can.includes('image') ? `<div class="presets">Samples: <button data-sample="receipt.png">Shop receipt</button><button data-sample="revenue-chart.png">Revenue chart</button></div>` : ''}
    ${draft.media.length ? `<div class="thumbs">${draft.media.map((x, i) => `<div class="thumb">${x.type === 'image' ? `<img src="${esc(x.url)}" alt="${esc(x.name)}">` : `${esc(x.name).slice(0, 16)}`}<button data-rm="${i}" aria-label="Remove ${esc(x.name)}">${icon('x')}</button></div>`).join('')}</div>` : ''}
    ${bad.length ? `<div class="note amber">${icon('warning')}<span>${esc(sel?.name || 'This model')} cannot read ${bad.join(' or ')}. Choose a model that can, or remove the file.</span></div>` : ''}</div>`;
  const dz = $('#dz', box);
  const handle = async (files) => {
    for (const f of files) {
      const form = new FormData(); form.append('file', f);
      try { const r = await api('/api/uploads', { method: 'POST', form }); draft.media.push({ id: r.id, type: r.type, name: r.name, url: r.url }); }
      catch (e) { toast(e.message, 'error'); }
    }
    saveDraft(); renderMedia();
  };
  $('#file', box).addEventListener('change', (e) => handle(e.target.files));
  $$('[data-sample]', box).forEach((b) => b.addEventListener('click', () => attachSample(b.dataset.sample, true)));
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag'); handle(e.dataTransfer.files); });
  $$('[data-rm]', box).forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); draft.media.splice(+b.dataset.rm, 1); saveDraft(); renderMedia(); }));
}

async function attachSample(name, add = false) {
  try {
    const blob = await (await fetch(`/ui/samples/${name}`)).blob();
    const form = new FormData(); form.append('file', new File([blob], name, { type: blob.type || 'image/png' }));
    const r = await api('/api/uploads', { method: 'POST', form });
    const item = { id: r.id, type: r.type, name: r.name, url: r.url };
    draft.media = add ? [...draft.media, item] : [item];
    saveDraft(); renderMedia();
  } catch (e) { toast(e.message, 'error'); }
}

// ------------------------------------------------------------------ running
function validated() {
  const built = buildRequest();
  if (built.error) {
    clearHint();
    hint = toast(built.error, 'error');
    if (built.uid) { builder.setStrict(true); builder.focus(built.uid); }
    if (built.field === 'state') $('#state', root)?.focus();
    if (built.field === 'questions') showCreatePanel();
    return null;
  }
  return built.request;
}

// While the model reads, each question card lights up in turn.
function reading(on) {
  const spec = $('#spec', root);
  if (!spec) return () => {};
  if (!on) { spec.classList.remove('reading'); $$('.q', spec).forEach((q) => q.classList.remove('lit')); return () => {}; }
  spec.classList.add('reading');
  const qs = $$('.branches .q', spec);
  let i = 0;
  const t = setInterval(() => { if (i < qs.length) qs[i].classList.add('lit'); i++; if (i > qs.length) clearInterval(t); }, 90);
  return () => clearInterval(t);
}

async function run() {
  if (busy || !root?.isConnected) return;
  const request = validated();
  if (!request) return;
  builder.setStrict(false);
  const btn = $('#decide', root);
  busy = true; btn.disabled = true;
  let stop = () => {};
  try {
    if (draft.compare) {
      const ids = readyModels().map((m) => m.id);
      if (!ids.length) throw new Error('Compare asks every loaded model. Load at least one model first.');
      renderBusy(`Asking ${ids.length} model${ids.length === 1 ? '' : 's'} at once`);
      stop = reading(true);
      const res = await api('/api/compare', { method: 'POST', body: { models: ids, request } });
      last = { kind: 'compare', request, results: res.results, run: nextRun() };
    } else {
      await ensureReady(draft.model, (m) => renderLoading(m));
      renderBusy('Reading the situation');
      stop = reading(true);
      const { data, meta } = await decide(request);
      last = { kind: 'result', request, data, meta, model: draft.model, run: nextRun() };
      markLearned('ran');
    }
    if (tab !== 'answers') tab = 'answers';
  } catch (e) {
    last = { kind: 'error', request, error: e.message, status: e.status, data: e.data, model: draft.model };
    tab = 'answers';
  } finally {
    busy = false; stop(); reading(false);
    if (btn.isConnected) btn.disabled = false;
    $$('[data-tab]', root).forEach((x) => { x.setAttribute('aria-pressed', x.dataset.tab === tab); x.setAttribute('aria-selected', x.dataset.tab === tab); });
    renderOut();
    revealAnswer();
  }
}

function revealAnswer() {
  if (!matchMedia('(max-width: 860px)').matches) return;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  $('.results', root)?.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
}

function nextRun() {
  const n = (+sessionStorage.getItem('bud.run') || 0) + 1;
  sessionStorage.setItem('bud.run', n);
  return n;
}

async function orderTest() {
  const request = validated();
  if (!request) return;
  const targets = Object.entries(request.questions).filter(([, q]) => ['choice', 'rank'].includes(q.type) && Object.keys(q.criteria).length > 2);
  if (!targets.length) { toast('The order test needs a pick-one or order question with three or more options.'); return; }
  const btn = $('#order', root); if (btn) { btn.disabled = true; btn.innerHTML = `${icon('hourglass-medium')}Testing`; }
  try {
    await ensureReady(draft.model, (m) => renderLoading(m));
    renderBusy('Shuffling options, five runs');
    const runs = [];
    for (let r = 0; r < 5; r++) {
      const qs = {};
      for (const [id, q] of targets) {
        const keys = Object.keys(q.criteria);
        if (r > 0) for (let i = keys.length - 1; i > 0; i--) { const k = Math.floor(Math.random() * (i + 1)); [keys[i], keys[k]] = [keys[k], keys[i]]; }
        qs[id] = { ...q, criteria: Object.fromEntries(keys.map((k) => [k, q.criteria[k]])) };
      }
      const { data } = await decide({ ...request, questions: qs });
      runs.push({ order: Object.fromEntries(Object.entries(qs).map(([id, q]) => [id, Object.keys(q.criteria)])), answers: data.answers });
    }
    last = { kind: 'order', request, runs, ids: targets.map(([id]) => id), model: draft.model };
    tab = 'answers';
    $$('[data-tab]', root).forEach((x) => { x.setAttribute('aria-pressed', x.dataset.tab === tab); x.setAttribute('aria-selected', x.dataset.tab === tab); });
  } catch (e) { toast(e.message, 'error'); }
  finally { if (btn?.isConnected) { btn.disabled = false; btn.innerHTML = `${icon('shuffle')}Test option order`; } renderOut(); revealAnswer(); }
}

// ------------------------------------------------------------------ results pane
function stamp(html) { const s = $('#stamp', root); if (s && s.innerHTML !== html) { s.innerHTML = html; animate(s); } }

function renderBusy(word) {
  const out = $('#out', root);
  if (!out || tab !== 'answers') return;
  const m = model(draft.model);
  stamp(`<span class="phase busy"><i class="on"></i><i class="on"></i><i class="on"></i><i class="now"></i></span><b>${esc(draft.compare ? 'Compare' : m?.name || '')}</b><span>${esc(word)}</span>`);
  out.innerHTML = ghost(true);
}

function renderLoading(m) {
  const out = $('#out', root);
  if (!out) return;
  const p = m.worker?.progress;
  stamp(`${phaseTrack(m)}<b>${esc(m.name)}</b><span>${esc(m.worker?.stage || 'Starting')}</span><span class="num">${Math.round(m.worker?.elapsed || 0)} s</span>`);
  out.innerHTML = `<div class="empty"><div class="glyph">${icon('cpu')}</div><h2>Loading ${esc(m.name)}</h2>
    <p>It reads ${esc(m.params)} ${term('parameters', 'parameters')} into memory once; every request after that is fast (milliseconds on a GPU).</p>
    <span class="progress" style="width:min(360px,80%)"><i style="width:${Math.round((p || 0.04) * 100)}%"></i></span>
    <p class="small">Uses about ${fmtGB(m.memory_gb)} of memory once loaded.</p></div>`;
}

// The empty figures the answer will land in.
function ghost(busyNow = false) {
  const n = draft.questions.length;
  const m = model(draft.model);
  return `<div class="figs">${draft.questions.slice(0, 4).map((q) => `<figure class="fig" style="margin:0">
      <div class="fig-head"><span class="fig-title">${esc(q.text || 'Question')}</span></div>
      <div class="fig-answer"><span style="display:block;width:40%;height:22px;border-radius:6px;background:var(--fill)"></span></div>
      <div class="hplot">${[0, 0, 0].map(() => '<div class="row"><span class="lab"><span style="display:block;width:70%;height:9px;border-radius:4px;background:var(--fill)"></span></span><span class="track"></span><span class="val"></span></div>').join('')}</div>
    </figure>`).join('')}</div>
    ${!busyNow && guideFor(m?.id)?.tip ? `<p class="note" style="margin-top:16px;max-width:64ch">${icon('lightbulb')}<span><b>Tip for ${esc(m.name)}:</b> ${esc(guideFor(m.id).tip)}</span></p>` : ''}
    ${!busyNow && m ? `<p class="muted" style="margin-top:16px;max-width:64ch">Press <b>Decide</b>. ${esc(m.name)} reads the situation once and ${n === 1 ? 'answers the question' : `answers all ${n} questions together`}. Each answer appears here with every option's probability on a 0 to 100% scale.</p>` : ''}`;
}

function emptyState() {
  const ms = store.state?.models || [];
  if (!store.state) return '<div class="empty"><p>Connecting to the studio.</p></div>';
  if (!ms.some((m) => m.downloaded || m.worker)) {
    const first = ms.find((m) => m.id === 'laya') || ms[0];
    return `<div class="empty"><div class="glyph">${icon('download-simple')}</div><h2>Download a model to start</h2>
      <p>${esc(first.name)} is the most popular open decision model: ${fmtBytes(first.download_bytes)}, ready in about a minute.</p>
      <button class="btn btn-primary" data-dl="${first.id}">${icon('download-simple')}Download ${esc(first.name)}</button></div>`;
  }
  const m = model(draft.model);
  stamp(m ? `${phaseTrack(m)}<b>${esc(m.name)}</b><span>${esc(phaseOf(m).word)}</span>` : '');
  return draft.stateText.trim() && draft.questions.length ? ghost() : guide(m);
}

// A blank or half-written decision: what to do next, ticked off as it is done.
function guide(m) {
  const hasState = !!draft.stateText.trim(), hasQ = draft.questions.length > 0;
  const step = (done, n, title, body, go) => `<li class="${done ? 'done' : ''}"><span class="n">${done ? icon('check') : n}</span>
    <span><button class="step-title" ${go ? `data-go="${go}"` : 'disabled'}>${title}</button><span class="small muted">${body}</span></span></li>`;
  return `<div class="guide">
    <h2>${hasState || hasQ ? 'Finish your decision' : 'Start a decision'}</h2>
    <ol class="guide-steps">
      ${step(hasState, 1, 'Describe the situation', guideFor(m?.id) ? `Under <b>State</b>. ${esc(guideFor(m.id).state)}` : 'Type or paste it under <b>State</b>: an email, a support ticket, a review, a log line or some JSON.', 'state')}
      ${step(hasQ, 2, 'Add a question', 'Under <b>Create new Decision</b>, pick the kind of answer you want, then write the question and its options.', 'questions')}
      ${step(false, 3, 'Press Decide', `${esc(m?.name || 'The model')} reads the situation once and answers every question here, each as a chart.`, '')}
    </ol>
    ${modelCard(m)}
    <button class="btn btn-plain" data-go="examples">${icon('sparkle')}More examples</button>
  </div>`;
}

// What the chosen model is made for, with its own examples to try in one click.
function modelCard(m) {
  const g = guideFor(m?.id);
  if (!g) return '';
  const mine = g.examples.map((id) => EXAMPLES.find((e) => e.id === id)).filter(Boolean);
  return `<div class="model-card"><span class="small muted">What ${esc(m.name)} is made for</span><p>${esc(g.madeFor)}</p>
    ${g.tip ? `<p class="small muted">${esc(g.tip)}</p>` : ''}
    <div class="model-card-ex">${mine.map((e) => `<button class="btn" data-ex="${e.id}">${icon(e.needsMedia ? 'image' : 'play')}${esc(e.title)}</button>`).join('')}</div></div>`;
}

function showCreatePanel() {
  const panel = $('.create-dec', root);
  if (!panel) return;
  panel.scrollIntoView({ block: 'center', behavior: 'smooth' });
  panel.classList.remove('pulse'); void panel.offsetWidth; panel.classList.add('pulse');
  setTimeout(() => $('[data-add-type]', panel)?.focus({ preventScroll: true }), 350);
}

// Start from scratch: empty situation, no questions, no attachments, no answers. The model and settings stay.
function newDecision() {
  const before = { draft: JSON.parse(JSON.stringify(draft)), last };
  draft = { ...draft, example: null, stateMode: 'text', stateText: '', questions: [], media: [] };
  last = null; tab = 'answers';
  saveDraft(); render();
  $('#state', root)?.focus();
  toast('Started a new decision.', '', { action: 'Undo', onAction: () => { draft = before.draft; last = before.last; saveDraft(); if (root?.isConnected) render(); } });
}

function renderOut({ instant = false } = {}) {
  const out = $('#out', root);
  if (!out) return;
  if (tab === 'code') return renderCode(out);
  if (tab === 'json') return renderJSON(out);
  if (busy) return;
  if (!last) {
    out.innerHTML = emptyState();
    $$('[data-dl]', out).forEach((b) => b.addEventListener('click', () => download(b.dataset.dl)));
    $$('[data-go]', out).forEach((b) => b.addEventListener('click', () => {
      if (b.dataset.go === 'state') $('#state', root)?.focus();
      if (b.dataset.go === 'questions') showCreatePanel();
      if (b.dataset.go === 'examples') examplesMenu(b);
    }));
    $$('[data-ex]', out).forEach((b) => b.addEventListener('click', () => loadExample(EXAMPLES.find((e) => e.id === b.dataset.ex))));
    return;
  }
  if (last.kind === 'error') { stamp(''); out.innerHTML = errorView(); return; }
  if (last.kind === 'compare') { out.innerHTML = compareView(); animate(out, { instant }); return; }
  if (last.kind === 'order') { out.innerHTML = orderView(); return; }
  const { data, meta, request } = last;
  const m = model(last.model);
  const n = Object.keys(data.answers).length;
  stamp(`<b>${esc(m?.name || data.model)}</b><span>answered ${n} question${n === 1 ? '' : 's'} in</span><span class="num">${odo(fmtMs(data.latency_ms ?? meta.roundtrip))}</span>
    <span>${data.passes === 1 ? term('one_pass', 'one pass') : ''}</span><span class="num">${(data.usage?.input_tokens ?? 0).toLocaleString()} tokens</span>
    ${request.settings?.temperature ? `<span class="chip">T ${fmtNum(request.settings.temperature)}</span>` : ''}
    <button class="btn btn-plain sm" data-copy-id="${esc(meta.requestId || '')}" aria-label="Copy request id">${icon('copy')}Request id</button>`);
  $('[data-copy-id]', root)?.addEventListener('click', (e) => copy(e.currentTarget.dataset.copyId, 'Request id copied'));
  out.innerHTML = `${(data.notes || []).length ? `<div class="note" style="margin-bottom:12px">${icon('info')}<span>${data.notes.map(esc).join('<br>')}</span></div>` : ''}
    ${figures(data, request, { run: last.run, threshold: store.prefs.threshold })}`;
  animate(out, { instant });
}

function errorView() {
  const m = model(last.model);
  const hints = [];
  if (last.status === 422 || last.status === 400) hints.push('The request did not pass validation. The message names the field; the JSON tab shows the full error.');
  if (last.status === 503 || last.status === 504) hints.push(`${m?.name || 'The model'} did not answer in time. Try again; if it repeats, eject it and load it again.`);
  if (/memory|CUDA/i.test(last.error)) hints.push('The model ran out of memory. Eject models you are not using, then try again.');
  return `<div class="empty"><div class="glyph" style="background:var(--red-tint);color:var(--red)">${icon('warning-circle')}</div><h2>The decision did not run</h2>
    <p>${last.status ? `HTTP ${last.status}: ` : ''}${esc(last.error)}</p>${hints.map((h) => `<p class="small">${esc(h)}</p>`).join('')}</div>`;
}

function compareView() {
  const { results, request } = last;
  const keys = Object.keys(request.questions);
  const ok = results.filter((r) => r.status === 200);
  const agree = (k) => {
    const vals = ok.map((r) => JSON.stringify(decisionOf(r.response.answers?.[k])));
    if (vals.length < 2) return '';
    const top = Object.entries(vals.reduce((a, v) => ({ ...a, [v]: (a[v] || 0) + 1 }), {})).sort((a, b) => b[1] - a[1])[0];
    return `<span class="chip ${top[1] === vals.length ? 'ok' : 'amber'}">${top[1]} of ${vals.length} agree</span>`;
  };
  stamp(`<b>Compare</b><span>${results.length} models, the same request, at the same time</span>`);
  return `<div class="matrix" style="grid-template-columns:minmax(180px,1.1fr) repeat(${results.length}, minmax(150px,1fr));overflow-x:auto">
      <div class="mhead">Question</div>
      ${results.map((r) => `<div class="mhead">${esc(model(r.model)?.name || r.model)}<span class="muted num" style="margin-left:auto;font-weight:400">${r.status === 200 ? fmtMs(r.response.latency_ms) : `HTTP ${r.status}`}</span></div>`).join('')}
      ${keys.map((k) => `<div class="qlabel">${esc(request.questions[k].instructions || k)}<div style="margin-top:6px">${agree(k)}</div></div>
        ${results.map((r) => `<div class="small">${r.status === 200 ? mini(r.response.answers?.[k]) : `<span style="color:var(--red-text)">${esc(String(r.response?.detail || 'failed')).slice(0, 140)}</span>`}</div>`).join('')}`).join('')}
    </div>
    <p class="help" style="margin-top:10px">Each model runs in its own process, so they answer in parallel. Where they disagree, the input deserves a human look or a bigger model.</p>`;
}

function orderView() {
  const { runs, ids, request } = last;
  stamp(`<b>Option-order test</b><span>${esc(model(last.model)?.name || '')}, 5 runs, options shuffled after the first</span>`);
  return `<div class="figs">${ids.map((id) => {
    const winners = runs.map((r) => decisionOf(r.answers[id]));
    const stable = winners.every((w) => w === winners[0]);
    return `<figure class="fig ${stable ? '' : 'unsure'}" style="margin:0">
      <div class="fig-head"><span class="fig-title">${esc(request.questions[id].instructions)}</span><span class="chip ${stable ? 'ok' : 'amber'}">${stable ? 'Stable' : 'Changes with order'}</span></div>
      <table class="table" style="margin-top:10px"><thead><tr><th>Run</th><th>Order shown</th><th>Answer</th><th class="num">Probability</th></tr></thead><tbody>
      ${runs.map((r, i) => `<tr><td class="num">${i + 1}${i === 0 ? ' <span class="muted">(yours)</span>' : ''}</td><td class="small muted">${r.order[id].map(esc).join(', ')}</td><td><b>${esc(winners[i])}</b></td><td class="num">${fmtPct(r.answers[id].probabilities?.[winners[i]])}</td></tr>`).join('')}
      </tbody></table>
      <figcaption class="fig-cap"><span>${stable ? 'The same answer every time: this question is not sensitive to option order.' : 'The answer depends on the order of the options. Treat it as unsure, rephrase the options, or try a larger model.'}</span></figcaption>
    </figure>`;
  }).join('')}</div>`;
}

function renderJSON(out) {
  const built = buildRequest();
  const request = last?.request || built.request;
  if (!request) { out.innerHTML = `<div class="note amber">${icon('warning')}<span>${esc(built.error)}</span></div>`; return; }
  const resp = last?.kind === 'result' ? last.data : last?.kind === 'error' ? last.data : last?.kind === 'compare' ? { results: last.results } : null;
  out.innerHTML = `<div class="two">
    <div style="display:grid;gap:8px"><div class="small muted" style="display:flex;gap:8px;align-items:center"><span class="chip violet">POST</span><code>/v1/systemone</code></div>${jsonTree(request, { file: 'Request', openDepth: 4 })}</div>
    <div style="display:grid;gap:8px">${resp ? `<div class="small muted" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${last.meta ? `<span class="chip ${last.meta.status === 200 ? 'ok' : 'danger'}">HTTP ${last.meta.status}</span><code>x-typesafe-request-id: ${esc(last.meta.requestId || '')}</code>` : '<span class="chip">Response</span>'}</div>
      ${jsonTree(resp, { file: 'Response', openDepth: 3, collapse: ['legend', 'model_extras'] })}` : '<div class="empty"><p>Press Decide to see the response here.</p></div>'}</div>
  </div>
  <p class="help" style="margin-top:12px">The studio sends <code>X-Basal-Extensions: 1</code> to receive its extra fields. Without that header the response is exactly TypeSafe's shape.</p>`;
}

function renderCode(out) {
  const built = buildRequest();
  if (built.error) { out.innerHTML = `<div class="note amber">${icon('warning')}<span>${esc(built.error)} The code appears once the request is complete.</span></div>`; return; }
  const req = { ...built.request };
  delete req.media;
  const L = LANGS[lang];
  out.innerHTML = `<div style="display:grid;gap:12px">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <span class="seg" role="group" aria-label="Language">${Object.entries(LANGS).map(([k, v]) => `<button data-lang="${k}" aria-pressed="${k === lang}">${esc(v.name)}</button>`).join('')}</span>
      ${L.sdk ? '' : `<select class="select" id="fmt" style="width:auto" aria-label="API format">${Object.entries(FORMATS).map(([k, f]) => `<option value="${k}" ${k === format ? 'selected' : ''}>${esc(f.name)}</option>`).join('')}</select>`}
      <span class="help">${L.sdk ? 'TypeSafe\'s official SDK, pointed at this studio.' : esc(FORMATS[format].note)}</span>
    </div>
    ${codeBlock(snippet(lang, req, { format }), { lang: L.lang, file: L.file })}
    <p class="help">No key is needed on this machine. ${draft.media.length ? 'Attached files are left out; send them as media items with a base64 data: URL. ' : ''}<a href="#/api">API reference</a></p>
  </div>`;
  $$('[data-lang]', out).forEach((b) => b.addEventListener('click', () => { lang = b.dataset.lang; renderCode(out); markLearned('code'); }));
  $('#fmt', out)?.addEventListener('change', (e) => { format = e.target.value; renderCode(out); });
}
