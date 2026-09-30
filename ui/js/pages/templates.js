// Templates: reusable decisions. A template holds the questions, the typed variables that fill in the situation, a
// default model and settings; every save is a new, numbered version, and every decision made with it is in its history.
// The library lists your templates and the starter ones; a template's page shows it, its history, a comparison of two
// versions over real traffic, its test examples and its versions.

import { TYPE_META } from '../figures.js';
import { codeBlock, jsonTree } from '../format.js';
import { setSub, shell } from '../shell.js';
import { model, studio } from '../store.js';
import { $, $$, askText, confirmDialog, copy, esc, fmtMs, fmtNum, fmtPct, icon, popMenu, toast } from '../util.js';
import { openTemplate } from './playground.js';

let root;
let tab = 'overview';
let state = { list: null, builtins: null, showArchived: false, q: '' };
let cur = null;           // the open template (latest version)
let shown = null;         // the version shown on Overview
let cmp = { a: null, b: null, model: '', range: '' };

const nameOf = (id) => model(id)?.name || id;
const ago = (t) => { if (!t) return 'never'; const s = Date.now() / 1000 - t; if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`; if (s < 86400) return `${Math.round(s / 3600)} h ago`; return new Date(t * 1000).toLocaleDateString(); };
const CLASS = { created: ['First version', ''], extended: ['Extended', 'violet'], wording: ['Wording', ''], settings_only: ['Settings', ''], breaking: ['Breaking', 'amber'], unchanged: ['Unchanged', ''] };
const COMP = { identical: ['Same question', 'ok'], text_changed: ['Reworded', ''], options_changed: ['Options changed', 'violet'], incomparable: ['Not comparable', 'amber'], added: ['New', 'violet'], removed: ['Removed', 'amber'], reference: ['Reference', ''] };

export async function mount(el, params = {}) {
  root = el;
  if (params.id) {
    if (params.tab) tab = params.tab;
    await openDetail(params.id);
  } else {
    shell.setTitle('Templates', '');
    await drawLibrary();
  }
}

// ------------------------------------------------------------------ library
async function drawLibrary() {
  root.innerHTML = `<div class="view scroll"><div class="lib-page">
    <div class="lib-top">
      <p class="muted" style="max-width:70ch">A template is a decision you can reuse with any model: the questions, the variables that fill in the situation, and the model and settings to use. Every save is a new version, and every decision made with it is kept in its history.</p>
      <div class="lib-actions">
        <label class="search">${icon('magnifying-glass')}<input class="input" id="tq" placeholder="Search templates" value="${esc(state.q)}"></label>
        <label class="switch small"><span>Show archived</span><input type="checkbox" id="arch" ${state.showArchived ? 'checked' : ''}></label>
        <button class="btn btn-primary" id="newtpl">${icon('plus')}New template</button>
      </div>
    </div>
    <div id="lib"></div></div></div>`;
  $('#newtpl', root).addEventListener('click', newTemplate);
  $('#arch', root).addEventListener('change', (e) => { state.showArchived = e.target.checked; loadLibrary(); });
  let t; $('#tq', root).addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); renderLibrary(); }, 200); });
  await loadLibrary();
}

async function loadLibrary() {
  try {
    const [mine, builtins] = await Promise.all([
      studio(`/templates?origin=user&limit=100${state.showArchived ? '&include_archived=true' : ''}`),
      studio('/templates?origin=builtin&limit=100'),
    ]);
    state.list = mine.data; state.builtins = builtins.data;
  } catch (e) { $('#lib', root).innerHTML = `<div class="empty"><h2>Templates are not available</h2><p>${esc(e.message)}</p></div>`; return; }
  renderLibrary();
}

function match(t) {
  const q = state.q.toLowerCase();
  return !q || `${t.id} ${t.name} ${t.description}`.toLowerCase().includes(q);
}

function card(t, starter = false) {
  const s = t.summary || {};
  const recommended = (t.metadata?.['basal.recommended_models'] || '').split(',').filter(Boolean);
  return `<a class="tpl-card ${t.archived ? 'archived' : ''}" href="#/templates/${esc(t.id)}">
    <span class="tpl-card-top"><b>${esc(t.name)}</b>${starter ? '' : `<span class="chip">v${t.version}</span>`}${t.archived ? '<span class="chip">Archived</span>' : ''}</span>
    <span class="small muted tpl-desc">${esc(t.description || (starter ? '' : t.id))}</span>
    <span class="tpl-meta small muted">
      <span>${icon('question')}${(s.questions || []).length} question${(s.questions || []).length === 1 ? '' : 's'}</span>
      ${(s.variables || []).length ? `<span>${icon('brackets-curly')}${s.variables.length} variable${s.variables.length === 1 ? '' : 's'}</span>` : `<span>${icon('text-t')}Takes a situation</span>`}
      ${s.model ? `<span>${icon('cube')}${esc(nameOf(s.model))}</span>` : recommended.length ? `<span>${icon('cube')}${esc(recommended.map(nameOf).join(', '))}</span>` : ''}
      ${starter ? '' : `<span>${icon('clock')}${t.last_used_at ? `used ${ago(t.last_used_at)}` : 'not used yet'}</span>`}
    </span></a>`;
}

function renderLibrary() {
  const box = $('#lib', root);
  if (!box || !state.list) return;
  const mine = state.list.filter(match);
  const starters = (state.builtins || []).filter(match);
  const groups = [...new Set(starters.map((t) => t.metadata?.['basal.group'] || 'Other'))];
  setSub(`${state.list.length} of yours, ${state.builtins?.length || 0} starters`);
  box.innerHTML = `
    <section class="lib-sec"><h2>Your templates</h2>
      ${mine.length ? `<div class="tpl-grid">${mine.map((t) => card(t)).join('')}</div>` : `<div class="lib-empty">${icon('stack')}<div><b>${state.q ? 'No template matches' : 'No templates yet'}</b>
        <p class="small muted">${state.q ? 'Try another word.' : 'Build a decision in the Playground and press <b>Save as template</b>, or start from one of the starter templates below.'}</p></div>
        ${state.q ? '' : `<a class="btn" href="#/playground">${icon('flask')}Open the Playground</a>`}</div>`}
    </section>
    <section class="lib-sec"><h2>Starter templates</h2><p class="small muted" style="margin-top:-4px">Ready-made decisions. Use one as it is, or clone it and make it yours.</p>
      ${groups.map((g) => `<h3 class="lib-group">${esc(g)}</h3><div class="tpl-grid">${starters.filter((t) => (t.metadata?.['basal.group'] || 'Other') === g).map((t) => card(t, true)).join('')}</div>`).join('')}
    </section>`;
}

function newTemplate() {
  popMenu($('#newtpl', root), `
    <button class="menu-item" data-pick="play">${icon('flask')}<span><b>Build it in the Playground</b><span>Write the situation and questions, try them on a model, then Save as template.</span></span></button>
    <button class="menu-item" data-pick="json">${icon('brackets-curly')}<span><b>Paste a definition</b><span>JSON with variables, state and questions, as the API takes it.</span></span></button>`, {
    align: 'right',
    onPick: (p) => { if (p === 'play') { location.hash = '#/playground?new=1'; } else pasteDefinition(); },
  });
}

function pasteDefinition() {
  const d = document.createElement('dialog');
  const sample = { id: 'support-triage', name: 'Support triage', variables: { customer_message: { type: 'string' }, account_tier: { type: 'string', enum: ['free', 'pro', 'enterprise'], default: 'free' } },
    state: { tier: '{{account_tier}}', message: '{{customer_message}}' },
    questions: { department: { type: 'choice', instructions: 'Which department should handle this?', criteria: ['billing', 'technical', 'sales'] }, urgent: { type: 'noul', instructions: 'Is it urgent?' } } };
  d.innerHTML = `<form method="dialog" style="width:min(720px,92vw)"><div class="dialog-body"><h2 style="font-size:17px">New template from a definition</h2>
    <p class="small muted">The same JSON that <code>POST /v1/studio/templates</code> takes. Every problem is listed at once.</p>
    <textarea class="textarea code" id="def" rows="16" spellcheck="false">${esc(JSON.stringify(sample, null, 2))}</textarea><div id="deferr"></div></div>
    <div class="dialog-foot"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" id="defsave" value="ok">Create template</button></div></form>`;
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  $('#defsave', d).addEventListener('click', async (e) => {
    e.preventDefault();
    let body;
    try { body = JSON.parse($('#def', d).value); } catch (err) { $('#deferr', d).innerHTML = `<div class="note danger">${esc(err.message)}</div>`; return; }
    try {
      const t = await studio('/templates', { method: 'POST', body: { note: 'First version', ...body } });
      d.close(); toast(`Created ${t.id}`); location.hash = `#/templates/${t.id}`;
    } catch (err) { $('#deferr', d).innerHTML = problems(err); }
  });
  d.showModal();
}

function problems(err) {
  const det = err.data?.error?.details || [];
  return `<div class="note danger" style="margin-top:8px">${icon('warning-circle')}<span>${det.length ? det.map((x) => `${x.param ? `<code>${esc(x.param)}</code> ` : ''}${esc(x.message)}`).join('<br>') : esc(err.message)}</span></div>`;
}

// ------------------------------------------------------------------ one template
async function openDetail(id) {
  try { cur = await studio(`/templates/${id}`); }
  catch (e) { shell.setTitle('Templates', ''); root.innerHTML = `<div class="empty"><div class="glyph">${icon('warning-circle')}</div><h2>Template not found</h2><p>${esc(e.message)}</p><a class="btn" href="#/templates">All templates</a></div>`; return; }
  shown = cur;
  cmp = { a: Math.max(1, cur.version - 1), b: cur.version, model: '', range: '' };
  shell.setTitle(cur.name, '');
  drawDetail();
}

function drawDetail() {
  const t = cur;
  const builtin = t.origin === 'builtin';
  const aliases = Object.entries(t.aliases).filter(([a]) => a !== 'latest');
  setSub(`<code>${esc(t.id)}</code>, version ${t.version}`);
  root.innerHTML = `<div class="view scroll"><div class="tpl-page">
    <div class="tpl-head">
      <div style="min-width:0">
        <a class="small muted back" href="#/templates">${icon('caret-left')}Templates</a>
        <h1>${esc(t.name)}</h1>
        <p class="muted">${esc(t.description || (builtin ? 'A starter template.' : ''))}</p>
        <div class="tpl-badges"><span class="chip mono">${esc(t.id)}</span><span class="chip violet">v${t.version} latest</span>
          ${aliases.map(([a, v]) => `<span class="chip">${icon('tag')}${esc(a)}: v${v}</span>`).join('')}
          ${builtin ? '<span class="chip">Starter, read only</span>' : ''}${t.archived ? '<span class="chip amber">Archived</span>' : ''}
          ${t.storage !== 'full' ? `<span class="chip">History: ${t.storage === 'none' ? 'off' : 'answers only'}</span>` : ''}</div>
      </div>
      <div class="tpl-head-actions">
        <button class="btn btn-primary" id="use">${icon('flask')}Open in Playground</button>
        ${builtin ? `<button class="btn" id="clone">${icon('copy')}Clone to edit</button>` : `<button class="btn" id="edit">${icon('pencil-simple')}Edit</button>`}
        <button class="icon-btn" id="tmore" aria-label="More">${icon('dots-three')}</button>
      </div>
    </div>
    <div class="utabs tpl-tabs" role="tablist">${[['overview', 'Overview', 'info'], ['history', 'History', 'clock-counter-clockwise'], ['compare', 'Compare versions', 'git-diff'], ['examples', 'Test examples', 'list-checks'], ['versions', `Versions (${t.version})`, 'git-commit']].map(([k, l, i]) => `<button role="tab" data-tab="${k}" aria-pressed="${tab === k}" aria-selected="${tab === k}">${icon(i)}${l}</button>`).join('')}</div>
    <div id="tbody" class="tpl-body"></div></div></div>`;
  $$('[data-tab]', root).forEach((b) => b.addEventListener('click', () => { tab = b.dataset.tab; $$('[data-tab]', root).forEach((x) => { x.setAttribute('aria-pressed', x === b); x.setAttribute('aria-selected', x === b); }); drawTab(); }));
  $('#use', root).addEventListener('click', () => { openTemplate(shown); location.hash = '#/playground'; });
  $('#edit', root)?.addEventListener('click', () => { openTemplate(cur, { edit: true }); location.hash = '#/playground'; });
  $('#clone', root)?.addEventListener('click', cloneIt);
  $('#tmore', root).addEventListener('click', (e) => moreMenu(e.currentTarget));
  drawTab();
}

async function cloneIt() {
  const id = await askText({ title: 'Clone to your templates', label: 'An id for your copy: lower-case letters, digits and dashes.', value: cur.id.replace(/^builtin\//, 'my-'), pattern: '[a-z0-9][a-z0-9_-]{0,63}', confirm: 'Clone' });
  if (!id) return;
  try {
    const t = await studio('/templates', { method: 'POST', body: { id, name: `${cur.name} (copy)`, description: cur.description, from: { template: `${cur.id}@${cur.version}` }, note: `Cloned from ${cur.id} v${cur.version}` } });
    toast(`Cloned to ${t.id}`); location.hash = `#/templates/${t.id}`;
  } catch (e) { toast(e.message, 'error'); }
}

function moreMenu(anchor) {
  const builtin = cur.origin === 'builtin';
  popMenu(anchor, `
    ${builtin ? '' : `<button class="menu-item" data-pick="rename">${icon('pencil-simple-line')}<span><b>Rename or describe</b><span>Does not create a version.</span></span></button>
    <button class="menu-item" data-pick="storage">${icon('clock-counter-clockwise')}<span><b>History for this template</b><span>What is kept, and for how long.</span></span></button>`}
    ${builtin ? '' : `<button class="menu-item" data-pick="clone">${icon('copy')}<span><b>Duplicate</b></span></button>`}
    <button class="menu-item" data-pick="json">${icon('brackets-curly')}<span><b>Copy definition as JSON</b></span></button>
    ${builtin ? '' : `<div class="menu-sep"></div>
    <button class="menu-item" data-pick="archive">${icon('archive')}<span><b>${cur.archived ? 'Unarchive' : 'Archive'}</b><span>${cur.archived ? 'Show it in the library again.' : 'Hide it from the library; code that uses it keeps working.'}</span></span></button>
    <button class="menu-item" data-pick="delete">${icon('trash')}<span><b>Delete</b><span>Code that uses it stops working.</span></span></button>`}`, {
    align: 'right',
    onPick: async (p) => {
      try {
        if (p === 'json') { const def = Object.fromEntries(['modalities', 'variables', 'state', 'questions', 'model', 'settings', 'extensions'].map((k) => [k, shown[k]])); copy(JSON.stringify({ id: cur.id, name: cur.name, ...def }, null, 2), 'Definition copied'); }
        if (p === 'clone') cloneIt();
        if (p === 'rename') {
          const name = await askText({ title: 'Rename', label: 'The name shown in the library.', value: cur.name, confirm: 'Save' });
          if (!name) return;
          cur = await studio(`/templates/${cur.id}`, { method: 'PATCH', body: { name } }); shown = cur; shell.setTitle(cur.name, ''); drawDetail();
        }
        if (p === 'storage') storageDialog();
        if (p === 'archive') { cur = await studio(`/templates/${cur.id}/${cur.archived ? 'unarchive' : 'archive'}`, { method: 'POST' }); shown = cur; drawDetail(); toast(cur.archived ? 'Archived. It still answers calls.' : 'Unarchived'); }
        if (p === 'delete') {
          const keep = await confirmDialog({ title: `Delete ${cur.name}?`, body: `<p>Code that calls <code>${esc(cur.id)}</code> will get "template not found". Its decisions stay in History, with the exact version they used; delete them there if you want them gone.</p>`, confirm: 'Delete template', danger: true });
          if (!keep) return;
          await studio(`/templates/${cur.id}?confirm=${encodeURIComponent(cur.id)}&history=keep`, { method: 'DELETE' });
          toast('Template deleted. Its history is kept.'); location.hash = '#/templates';
        }
      } catch (e) { toast(e.message, 'error'); }
    },
  });
}

function storageDialog() {
  const d = document.createElement('dialog');
  const levels = [['full', 'Everything'], ['answers_only', 'Answers only'], ['none', 'Nothing']];
  d.innerHTML = `<form method="dialog"><div class="dialog-body"><h2 style="font-size:17px">History for ${esc(cur.name)}</h2>
    <label class="field"><span class="label">What is kept for each decision</span><select class="select" name="storage">${levels.map(([v, l]) => `<option value="${v}" ${cur.storage === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <span class="help">Callers can keep less than this, never more. "Answers only" keeps answers, model and timing but not the situation.</span></label>
    <label class="field"><span class="label">Keep decisions for (days)</span><input class="input" name="days" type="number" min="0" placeholder="The studio setting" value="${cur.retention_days ?? ''}">
      <span class="help">Empty uses the studio setting; 0 keeps them for ever.</span></label></div>
    <div class="dialog-foot"><button class="btn" value="cancel">Cancel</button><button class="btn btn-primary" value="ok">Save</button></div></form>`;
  document.body.append(d);
  d.addEventListener('close', async () => {
    if (d.returnValue === 'ok') {
      const f = new FormData(d.querySelector('form'));
      const days = f.get('days');
      try { cur = await studio(`/templates/${cur.id}`, { method: 'PATCH', body: { storage: f.get('storage'), retention_days: days === '' ? null : +days } }); shown = cur; drawDetail(); toast('Saved'); } catch (e) { toast(e.message, 'error'); }
    }
    d.remove();
  });
  d.showModal();
}

function drawTab() {
  const box = $('#tbody', root);
  if (!box) return;
  ({ overview, history: historyTab, compare: compareTab, examples: examplesTab, versions: versionsTab })[tab](box);
}

// ------------------------------------------------------------------ overview
function varType(v) {
  if (v.type === 'string' && v.enum) return `one of ${v.enum.join(', ')}`;
  if (v.type === 'string') return `text${v.max_length ? `, up to ${v.max_length.toLocaleString()} characters` : ''}`;
  return { integer: 'whole number', number: 'number', boolean: 'yes or no', json: 'JSON', options: 'a list of options', image: 'image', audio: 'audio', video: 'video' }[v.type] || v.type;
}

function questionCard(k, q, t) {
  const meta = TYPE_META[q.type];
  const c = q.criteria;
  let opts = '';
  if (typeof c === 'string') opts = `<span class="chip violet">${esc(c)}</span><span class="small muted">options come from this variable</span>`;
  else if (['choice', 'multi', 'rank'].includes(q.type)) opts = (Array.isArray(c) ? c.map((n) => [n, null]) : Object.entries(c || {})).map(([n, d]) => `<span class="opt ro"><span class="name">${esc(String(n).replace(/_/g, ' '))}</span>${d ? `<span class="desc">${esc(typeof d === 'string' ? d : JSON.stringify(d))}</span>` : ''}</span>`).join('');
  else if (q.type === 'score') opts = (c || []).map((l, i) => `${i ? `<span class="arrow">${icon('caret-right')}</span>` : ''}<span class="opt ro"><span class="name">${esc(typeof l === 'string' ? l : JSON.stringify(l))}</span></span>`).join('');
  else if (q.type === 'number') opts = (Array.isArray(c) ? c : Object.keys(c || {})).map((v) => `<span class="opt ro"><span class="name">${esc(String(v))}${q.unit ? ` ${esc(q.unit)}` : ''}</span></span>`).join('');
  const ext = t.extensions || {};
  const canAdd = ext.options === true || (ext.options || []).includes(k);
  const canSkip = ext.skip === true || (ext.skip || []).includes(k);
  const qs = t.settings?.questions?.[k];
  return `<div class="qcard"><div class="qcard-top"><span class="q-type ro">${icon(meta.icon)}${meta.name}</span><code class="small muted">${esc(k)}</code>
      ${canAdd ? '<span class="chip">callers may add options</span>' : ''}${canSkip ? '<span class="chip">callers may skip</span>' : ''}
      ${qs?.act_threshold ? `<span class="chip">acts at ${fmtPct(qs.act_threshold)}</span>` : ''}</div>
    <p class="qcard-text">${withVars(typeof q.instructions === 'string' ? q.instructions : JSON.stringify(q.instructions ?? ''))}</p>
    ${opts ? `<div class="opts ro">${opts}</div>` : ''}</div>`;
}

// Question text with its {{variable}} placeholders shown as variables.
const withVars = (text) => esc(text).replace(/\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/g, '<code class="var">$1</code>');

function exampleVariables(t) {
  const out = {};
  for (const [n, v] of Object.entries(t.variables || {})) {
    if (v.example != null) out[n] = v.example;
    else if (v.default != null) continue;
    else if (!v.required) continue;
    else out[n] = v.type === 'string' ? (v.enum ? v.enum[0] : '...') : v.type === 'integer' || v.type === 'number' ? 0 : v.type === 'boolean' ? false : v.type === 'options' ? ['option_a', 'option_b'] : ['image', 'audio', 'video'].includes(v.type) ? 'file_...' : {};
  }
  return out;
}

function overview(box) {
  const t = shown;
  const vars = Object.entries(t.variables || {});
  const s = t.settings || {};
  const perModel = Object.entries(s.models || {});
  const ext = t.extensions || {};
  const body = vars.length ? { template: t.id, variables: exampleVariables(t) } : { template: t.id, state: 'the situation to decide about' };
  const curl = `curl ${location.origin}/v1/studio/decisions \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify(body)}'`;
  const py = `import httpx\n\nstudio = httpx.Client(base_url="${location.origin}/v1/studio")\nd = studio.post("/decisions", json=${JSON.stringify(body, null, 4).replace(/\n/g, '\n').replace(/true/g, 'True').replace(/false/g, 'False').replace(/null/g, 'None')}).json()\n\nif d["act"]:\n    ...  # every answer is sure enough to act on\nelse:\n    print("Ask a human about", d["needs_review"])`;
  box.innerHTML = `<div class="tpl-cols">
    <div class="tpl-main">
      ${t.version !== cur.version ? `<div class="note amber">${icon('clock-counter-clockwise')}<span>Showing version ${t.version}. The latest is ${cur.version}.</span><button class="btn sm" id="latest" style="margin-left:auto">Show latest</button></div>` : ''}
      <section class="tpl-sec"><h2>The situation</h2>
        ${vars.length ? `<p class="small muted">Callers fill in these variables; the template turns them into the situation the model reads.</p>
          <div class="group">${vars.map(([n, v]) => `<div class="grow"><span class="k"><code>${esc(n)}</code>${v.required ? '' : ' <span class="faint small">optional</span>'}${v.sensitive ? ` <span class="chip" data-tip="Used for the decision, never written to History">${icon('lock-simple')}sensitive</span>` : ''}${v.description ? `<span class="small muted" style="display:block">${esc(v.description)}</span>` : ''}</span><span class="v small">${esc(varType(v))}${v.default != null ? `, default ${esc(JSON.stringify(v.default))}` : ''}</span></div>`).join('')}</div>
          ${t.state != null ? `<details class="more"><summary>${icon('caret-right')}How they become the situation</summary><div style="margin-top:8px">${typeof t.state === 'string' ? `<pre class="code" style="white-space:pre-wrap">${esc(t.state)}</pre>` : jsonTree(t.state, { file: 'state', openDepth: 3 })}</div></details>` : '<p class="small muted">The variables themselves are the situation, as a JSON object.</p>'}`
        : '<p class="small muted">Callers send the whole situation as <code>state</code>: text, or a JSON object or list. Declare variables to give it a fixed shape.</p>'}
        ${t.modalities.length > 1 ? `<p class="small muted">Also accepts ${t.modalities.filter((m) => m !== 'text').join(', ')}.</p>` : ''}
      </section>
      <section class="tpl-sec"><h2>Questions</h2>${Object.entries(t.questions).map(([k, q]) => questionCard(k, q, t)).join('')}
        ${ext.questions ? `<p class="small muted">Callers may add up to ${ext.max_questions} questions of their own to a decision.</p>` : '<p class="small muted">Callers cannot add questions.</p>'}</section>
    </div>
    <aside class="tpl-side">
      <section class="tpl-sec"><h2>Model and settings</h2>
        <div class="group">
          <div class="grow"><span class="k">Default model</span><span class="v">${t.model ? esc(nameOf(t.model)) : 'the most recently loaded'}</span></div>
          <div class="grow"><span class="k">Act automatically at</span><span class="v num">${s.act_threshold ? fmtPct(s.act_threshold) : 'studio default'}</span></div>
          <div class="grow"><span class="k">Temperature</span><span class="v num">${s.temperature ? fmtNum(s.temperature) : '1 (none)'}</span></div>
          ${perModel.map(([m, v]) => `<div class="grow"><span class="k small">On ${esc(nameOf(m))}</span><span class="v small">${[v.temperature ? `temperature ${fmtNum(v.temperature)}` : '', v.act_threshold ? `acts at ${fmtPct(v.act_threshold)}` : '', v.questions ? `${Object.keys(v.questions).length} question setting(s)` : ''].filter(Boolean).join(', ')}</span></div>`).join('')}
        </div>
        <p class="small muted">A request can set its own model and settings; History records which layer each value came from.</p>
      </section>
      <section class="tpl-sec"><h2>Models that can run it</h2><div id="compat" class="small muted">Checking</div></section>
      <section class="tpl-sec"><h2>Use it from code</h2>
        ${codeBlock(curl, { lang: 'bash', file: 'curl' })}
        <details class="more"><summary>${icon('caret-right')}Python</summary><div style="margin-top:8px">${codeBlock(py, { lang: 'python', file: 'python' })}</div></details>
        <p class="small muted">Pin a version with <code>${esc(t.id)}@${t.version}</code>, or name an alias such as <code>${esc(t.id)}@production</code>. <a href="#/api">API reference</a></p>
      </section>
    </aside></div>`;
  $('#latest', box)?.addEventListener('click', () => { shown = cur; overview(box); });
  studio(`/templates/${t.id}/compatibility?version=${t.version}`).then((c) => {
    const el = $('#compat', box);
    if (!el) return;
    const ok = c.models.filter((m) => m.ok && m.model !== 'fake-decider');
    const bad = c.models.filter((m) => !m.ok && m.model !== 'fake-decider');
    el.innerHTML = `<div class="compat">${ok.map((m) => `<span class="chip ${m.status === 'loaded' ? 'ok' : ''}" title="${esc(m.notes.join(' '))}">${icon('check')}${esc(m.name)}</span>`).join('')}
      ${bad.map((m) => `<span class="chip faint" title="${esc(m.problems.map((p) => p.message).join(' '))}">${esc(m.name)}</span>`).join('')}</div>
      ${bad.length ? '<p class="small muted" style="margin-top:6px">Greyed models cannot run it; hover one to see why.</p>' : ''}`;
  }).catch(() => {});
}

// ------------------------------------------------------------------ history
async function historyTab(box) {
  box.innerHTML = '<p class="muted">Loading</p>';
  let page, stats;
  try {
    [page, stats] = await Promise.all([studio(`/templates/${cur.id}/decisions?limit=50&attribution=all`), studio(`/templates/${cur.id}/stats`)]);
  } catch (e) { box.innerHTML = `<div class="note danger">${esc(e.message)}</div>`; return; }
  if (!page.data.length) {
    box.innerHTML = `<div class="empty"><div class="glyph">${icon('clock-counter-clockwise')}</div><h2>No decisions with this template yet</h2><p>Open it in the Playground, or call it from code. Every decision is kept here with the exact version it used.</p></div>`;
    return;
  }
  const total = stats.groups.reduce((a, g) => a + g.count, 0);
  box.innerHTML = `<div class="stats-strip slim" style="margin-bottom:14px">
      ${stats.groups.slice(-4).map((g) => `<div><span>Version ${g.key.version}</span><b>${g.count.toLocaleString()}</b><small>${g.act_rate != null ? `${fmtPct(g.act_rate)} acted automatically` : ''}${g.latency_ms?.p50 != null ? `, ${fmtMs(g.latency_ms.p50)} typical` : ''}</small></div>`).join('')}
    </div>
    <div class="card" style="overflow:hidden"><div class="tscroll"><table class="table hover">
      <thead><tr><th>When</th><th>Result</th><th>Version</th><th>Model</th><th>Answers</th></tr></thead>
      <tbody>${page.data.map((x) => `<tr data-open="${esc(x.id)}" tabindex="0"><td class="small">${ago(x.created_at)}</td>
        <td>${x.status === 'failed' ? '<span class="chip danger">Failed</span>' : x.act ? '<span class="chip violet">Acted</span>' : '<span class="chip amber">Ask a human</span>'}</td>
        <td class="small">v${x.template.version}${x.extended ? ' <span class="faint">extended</span>' : ''}</td><td>${esc(nameOf(x.model))}</td>
        <td class="small" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Object.entries(x.answers).slice(0, 3).map(([k, a]) => `${esc(k)}: <b>${esc(String(Array.isArray(a.decision) ? a.decision.join(', ') : a.decision ?? '').replace(/_/g, ' '))}</b>`).join(', ')}</td></tr>`).join('')}</tbody>
    </table></div></div>
    <p class="small muted" style="margin-top:8px">${total.toLocaleString()} decisions in all. <a href="#/history?template=${encodeURIComponent(cur.id)}">Filter and label them in History</a></p>`;
  $$('[data-open]', box).forEach((r) => r.addEventListener('click', () => { location.hash = `#/history/${r.dataset.open}?template=${encodeURIComponent(cur.id)}`; }));
}

// ------------------------------------------------------------------ compare versions
async function compareTab(box) {
  if (cur.version < 2) {
    box.innerHTML = `<div class="empty"><div class="glyph">${icon('git-diff')}</div><h2>One version so far</h2><p>Once you save a second version, this compares the two on real traffic: how answers are distributed, how often each acts on its own, how accurate they are on labelled decisions, and how the answer changed for the same input.</p></div>`;
    return;
  }
  const opts = (sel) => Array.from({ length: cur.version }, (_, i) => cur.version - i).map((v) => `<option value="${v}" ${v === sel ? 'selected' : ''}>Version ${v}${v === cur.version ? ' (latest)' : ''}</option>`).join('');
  box.innerHTML = `<div class="cmp-bar">
      <select class="select" id="va" style="width:auto">${opts(cmp.a)}</select><span class="muted">${icon('arrow-right')}</span><select class="select" id="vb" style="width:auto">${opts(cmp.b)}</select>
      <select class="select" id="vm" style="width:auto"><option value="">Every model</option>${[...new Set((await modelsUsed()))].map((m) => `<option value="${esc(m)}" ${cmp.model === m ? 'selected' : ''}>${esc(nameOf(m))}</option>`).join('')}</select>
      <span class="seg" role="group">${[['', 'All time'], ['10080', '7 days'], ['43200', '30 days']].map(([v, l]) => `<button data-crange="${v}" aria-pressed="${cmp.range === v}">${l}</button>`).join('')}</span>
    </div><div id="cmpbody"><p class="muted">Comparing</p></div>`;
  $('#va', box).addEventListener('change', (e) => { cmp.a = +e.target.value; compareTab(box); });
  $('#vb', box).addEventListener('change', (e) => { cmp.b = +e.target.value; compareTab(box); });
  $('#vm', box).addEventListener('change', (e) => { cmp.model = e.target.value; compareTab(box); });
  $$('[data-crange]', box).forEach((b) => b.addEventListener('click', () => { cmp.range = b.dataset.crange; compareTab(box); }));
  if (cmp.a === cmp.b) { $('#cmpbody', box).innerHTML = '<p class="muted">Choose two different versions.</p>'; return; }
  const p = new URLSearchParams({ versions: `${cmp.a},${cmp.b}` });
  if (cmp.model) p.set('model', cmp.model);
  if (cmp.range) p.set('created_after', `-${cmp.range}m`);
  let r, diff;
  try { [r, diff] = await Promise.all([studio(`/templates/${cur.id}/compare?${p}`), studio(`/templates/${cur.id}/versions/${cmp.b}/diff?against=${cmp.a}`)]); }
  catch (e) { $('#cmpbody', box).innerHTML = `<div class="note danger">${esc(e.message)}</div>`; return; }
  const [A, B] = [String(cmp.a), String(cmp.b)];
  const op = r.operational;
  $('#cmpbody', box).innerHTML = `
    <div class="stats-strip slim" style="margin-bottom:14px">
      <div><span>Decisions</span><b>${op[A].n} ${icon('arrow-right')} ${op[B].n}</b><small>version ${A} to ${B}</small></div>
      <div><span>Typical time</span><b>${fmtMs(op[A].latency_ms.p50)} ${icon('arrow-right')} ${fmtMs(op[B].latency_ms.p50)}</b><small>95%: ${fmtMs(op[A].latency_ms.p95)} and ${fmtMs(op[B].latency_ms.p95)}</small></div>
      <div><span>Failed</span><b>${op[A].failed} ${icon('arrow-right')} ${op[B].failed}</b><small>&nbsp;</small></div>
      <div><span>What changed</span><b style="font-size:16px">${esc((CLASS[diff.class] || [diff.class])[0])}</b><small>${diff.breaking_for_callers ? 'some callers may be refused' : 'callers keep working'}</small></div>
    </div>
    ${diff.summary?.length ? `<div class="note" style="margin-bottom:14px">${icon('git-diff')}<span>${diff.summary.map(esc).join('<br>')}</span></div>` : ''}
    <div class="cmp-grid">${Object.entries(r.questions).map(([k, q]) => cmpQuestion(k, q, A, B)).join('')}</div>
    <p class="small muted" style="margin-top:10px">Distributions count only decisions that used the template's own questions unchanged. <b>Same input</b> pairs the latest decision of each version for identical ${r.paired_by === 'variables_hash' ? 'variables' : 'situations'}.</p>`;
}

async function modelsUsed() {
  try { const s = await studio(`/templates/${cur.id}/stats?group_by=model`); return s.groups.map((g) => g.key.model).filter(Boolean); } catch { return []; }
}

// A score answer is a level number; show the level's words from the template.
function labelFor(k, n) {
  const q = cur.questions[k];
  if (q?.type === 'score' && Array.isArray(q.criteria) && /^\d+$/.test(n)) { const l = q.criteria[+n]; if (l != null) return typeof l === 'string' ? l : JSON.stringify(l); }
  return n.replace(/_/g, ' ');
}

function cmpQuestion(k, q, A, B) {
  const [label, tone] = COMP[q.comparability] || [q.comparability, ''];
  const a = q.by_version[A], b = q.by_version[B];
  const both = a && b;
  const names = [...new Set([...Object.keys(a?.distribution || {}), ...Object.keys(b?.distribution || {})])];
  const bars = names.slice(0, 10).map((n) => {
    const pa = a?.distribution?.[n] ?? 0, pb = b?.distribution?.[n] ?? 0;
    return `<div class="cb"><span class="cb-name">${esc(labelFor(k, n))}</span>
      <span class="cb-bars">${a ? `<i class="a" style="width:${pa * 100}%"></i>` : ''}${b ? `<i class="b" style="width:${pb * 100}%"></i>` : ''}</span>
      <span class="cb-v num">${both ? `${fmtPct(pa)} ${icon('arrow-right')} ${fmtPct(pb)}` : fmtPct(a ? pa : pb)}</span></div>`;
  }).join('');
  const stat = (lbl, fa, fb) => `<div><span class="small muted">${lbl}</span><b class="num">${fa === null ? fb : `${fa} ${icon('arrow-right')} ${fb}`}</b></div>`;
  const pr = q.paired;
  return `<div class="card cmp-q"><div class="cmp-q-top"><b>${esc(k)}</b><span class="chip ${tone}">${esc(label)}</span>
      ${q.added_options?.length ? `<span class="small muted">added ${q.added_options.map(esc).join(', ')}</span>` : ''}${q.removed_options?.length ? `<span class="small muted">removed ${q.removed_options.map(esc).join(', ')}</span>` : ''}</div>
    ${a && b ? `<div class="cmp-stats">${stat('Acted automatically', fmtPct(a.act_rate), fmtPct(b.act_rate))}${stat('Average certainty', fmtPct(a.mean_certainty), fmtPct(b.mean_certainty))}
      ${stat('Accuracy on labels', a.feedback.labelled ? `${fmtPct(a.feedback.accuracy)} <span class="small muted">(${a.feedback.labelled})</span>` : 'none', b.feedback.labelled ? `${fmtPct(b.feedback.accuracy)} <span class="small muted">(${b.feedback.labelled})</span>` : 'none')}
      ${pr ? stat('Same input, same answer', null, pr.n ? `${fmtPct(pr.agreement)} <span class="small muted">of ${pr.n} pairs</span>` : 'no pairs yet') : ''}</div>` : `<p class="small muted">${a ? `Only in version ${A}` : `Only in version ${B}`}: ${fmtNum((a || b).n)} decisions.</p>`}
    ${bars && q.comparability !== 'incomparable' ? `<div class="cb-list"><div class="cb-legend small muted">${a ? `<span><i class="a"></i>v${A}</span>` : ''}${b ? `<span><i class="b"></i>v${B}</span>` : ''}</div>${bars}</div>` : ''}
    ${pr?.flips?.length ? `<p class="small muted">Changed answers on the same input: ${pr.flips.slice(0, 4).map((x) => `${esc(x.from)} ${icon('arrow-right')} ${esc(x.to)} (${x.n})`).join(', ')}</p>` : ''}</div>`;
}

// ------------------------------------------------------------------ examples
async function examplesTab(box) {
  box.innerHTML = '<p class="muted">Loading</p>';
  let r;
  try { r = await studio(`/templates/${cur.id}/examples?limit=200`); } catch (e) { box.innerHTML = `<div class="note danger">${esc(e.message)}</div>`; return; }
  const builtin = cur.origin === 'builtin';
  const preview = (ex) => { const v = ex.variables ? Object.values(ex.variables).find((x) => typeof x === 'string') : typeof ex.state === 'string' ? ex.state : JSON.stringify(ex.state); return esc(String(v ?? '').slice(0, 140)); };
  box.innerHTML = `<div class="ex-top"><p class="muted" style="max-width:72ch">Test examples are inputs with the right answers. Use them to check a new version or another model before switching. Label decisions in History, then add them here in one click.</p>
      <div style="display:flex;gap:6px;margin-left:auto">${builtin ? '' : `<button class="btn" id="exadd">${icon('plus')}Add example</button>`}<a class="btn" href="/v1/studio/templates/${esc(cur.id)}/examples/export" download>${icon('download-simple')}Export</a></div></div>
    ${r.data.length ? `<div class="card" style="overflow:hidden"><div class="tscroll"><table class="table">
      <thead><tr><th>Input</th><th>Right answers</th><th>Tags</th><th></th></tr></thead>
      <tbody>${r.data.map((ex) => `<tr><td class="small" style="max-width:420px">${preview(ex)}</td>
        <td class="small">${Object.entries(ex.expected).map(([k, v]) => `${esc(k)}: <b>${esc(Array.isArray(v) ? v.join(', ') : String(v))}</b>`).join('<br>') || '<span class="faint">not labelled</span>'}</td>
        <td>${ex.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join(' ')}</td>
        <td class="num">${builtin ? '' : `<button class="icon-btn" data-exdel="${esc(ex.id)}" aria-label="Remove example">${icon('trash')}</button>`}</td></tr>`).join('')}</tbody></table></div></div>
      <p class="small muted" style="margin-top:8px">${r.data.length} example${r.data.length === 1 ? '' : 's'}, revision ${r.revision}. Every change creates a new revision, so past evaluations stay reproducible.</p>`
    : `<div class="empty"><div class="glyph">${icon('list-checks')}</div><h2>No test examples yet</h2><p>In History, label a decision's right answers, then choose <b>Add to test examples</b>. Or add one here.</p></div>`}`;
  $('#exadd', box)?.addEventListener('click', () => addExample(box));
  $$('[data-exdel]', box).forEach((b) => b.addEventListener('click', async () => {
    try { await studio(`/templates/${cur.id}/examples/${b.dataset.exdel}`, { method: 'DELETE' }); toast('Example removed'); examplesTab(box); } catch (e) { toast(e.message, 'error'); }
  }));
}

function addExample(box) {
  const d = document.createElement('dialog');
  const vars = cur.variables || {};
  const input = Object.keys(vars).length ? { variables: exampleVariables(cur) } : { state: '' };
  const expected = Object.fromEntries(Object.entries(cur.questions).slice(0, 2).map(([k, q]) => [k, q.type === 'noul' ? 'yes' : q.type === 'score' ? '0' : Array.isArray(q.criteria) ? String(q.criteria[0]) : Object.keys(q.criteria || { '': 0 })[0]]));
  d.innerHTML = `<form method="dialog" style="width:min(640px,92vw)"><div class="dialog-body"><h2 style="font-size:17px">Add a test example</h2>
    <p class="small muted">The input and the right answers. Scales take the level number ("0" is the lowest); yes/no takes "yes" or "no".</p>
    <textarea class="textarea code" id="exj" rows="12" spellcheck="false">${esc(JSON.stringify({ ...input, expected, tags: [] }, null, 2))}</textarea><div id="exerr"></div></div>
    <div class="dialog-foot"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" id="exsave" value="ok">Add example</button></div></form>`;
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  $('#exsave', d).addEventListener('click', async (e) => {
    e.preventDefault();
    let body;
    try { body = JSON.parse($('#exj', d).value); } catch (err) { $('#exerr', d).innerHTML = `<div class="note danger">${esc(err.message)}</div>`; return; }
    try { await studio(`/templates/${cur.id}/examples`, { method: 'POST', body }); d.close(); toast('Example added'); examplesTab(box); }
    catch (err) { $('#exerr', d).innerHTML = problems(err); }
  });
  d.showModal();
}

// ------------------------------------------------------------------ versions
async function versionsTab(box) {
  box.innerHTML = '<p class="muted">Loading</p>';
  let r;
  try { r = await studio(`/templates/${cur.id}/versions?limit=100`); } catch (e) { box.innerHTML = `<div class="note danger">${esc(e.message)}</div>`; return; }
  const builtin = cur.origin === 'builtin';
  box.innerHTML = `<div class="ver-list">${r.data.map((v) => {
    const [cl, tone] = CLASS[v.changes.class] || [v.changes.class, ''];
    return `<div class="ver"><div class="ver-dot"></div><div class="ver-body">
      <div class="ver-top"><b>Version ${v.version}</b><span class="chip ${tone}">${esc(cl)}</span>${v.aliases.map((a) => `<span class="chip ${a === 'latest' ? 'violet' : ''}">${icon('tag')}${esc(a)}</span>`).join('')}
        <span class="small muted">${esc(new Date(v.created_at * 1000).toLocaleString())}</span></div>
      ${v.note ? `<p>${esc(v.note)}</p>` : ''}
      ${v.changes.summary?.length && v.changes.class !== 'created' ? `<ul class="small muted">${v.changes.summary.slice(0, 6).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${v.changes.breaking_for_callers ? `<p class="small" style="color:var(--orange-text)">${icon('warning')}Requests that worked on version ${v.changes.from} may be refused.</p>` : ''}
      <div class="ver-actions">
        <button class="btn sm" data-show="${v.version}">${icon('eye')}View</button>
        ${v.version > 1 ? `<button class="btn sm" data-cmp="${v.version}">${icon('git-diff')}Compare with ${v.version - 1}</button>` : ''}
        ${builtin ? '' : `<button class="btn sm" data-alias="${v.version}">${icon('tag')}Set alias</button>${v.version !== cur.version ? `<button class="btn sm" data-restore="${v.version}">${icon('arrow-u-up-left')}Restore</button>` : ''}`}
      </div></div></div>`;
  }).join('')}</div>
  <p class="small muted">Versions never change once saved. An alias, such as <code>production</code>, points at one version: move it to promote a new version without touching your code.</p>`;
  $$('[data-show]', box).forEach((b) => b.addEventListener('click', async () => {
    try { shown = await studio(`/templates/${cur.id}?version=${b.dataset.show}`); tab = 'overview'; drawDetail(); } catch (e) { toast(e.message, 'error'); }
  }));
  $$('[data-cmp]', box).forEach((b) => b.addEventListener('click', () => { cmp.b = +b.dataset.cmp; cmp.a = cmp.b - 1; tab = 'compare'; drawDetail(); }));
  $$('[data-restore]', box).forEach((b) => b.addEventListener('click', async () => {
    try { cur = await studio(`/templates/${cur.id}/versions/${b.dataset.restore}/restore`, { method: 'POST', body: { note: `Restore version ${b.dataset.restore}` } }); shown = cur; toast(`Saved as version ${cur.version}`); drawDetail(); } catch (e) { toast(e.message, 'error'); }
  }));
  $$('[data-alias]', box).forEach((b) => b.addEventListener('click', async () => {
    const alias = await askText({ title: `Point an alias at version ${b.dataset.alias}`, label: 'For example production or staging. Code that calls the template with @alias gets this version.', value: 'production', pattern: '[a-z][a-z0-9_-]{0,31}', confirm: 'Set alias' });
    if (!alias) return;
    try { await studio(`/templates/${cur.id}/aliases/${alias}`, { method: 'PUT', body: { version: +b.dataset.alias } }); cur = await studio(`/templates/${cur.id}`); shown = cur; toast(`${alias} now points at version ${b.dataset.alias}`); drawDetail(); } catch (e) { toast(e.message, 'error'); }
  }));
}
