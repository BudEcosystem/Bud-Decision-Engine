// The decision builder: questions written as sentences, answers added as chips.
// Six question types. The first three are TypeSafe's own (choice, score, noul); pick any, put in order and
// estimate a number are studio extensions built from those three, so every model can answer all six.

import { TYPE_META, unitFor } from './figures.js';
import { guideFor } from './model-guides.js';
import { $, $$, askText, esc, icon, popMenu } from './util.js';

export const TYPES = ['choice', 'score', 'noul', 'multi', 'rank', 'number'];
const TYPE_HELP = {
  choice: 'Exactly one option from a list, like which team owns a ticket.',
  score: 'A position on an ordered scale, like how urgent something is.',
  noul: 'The probability that a statement is true.',
  multi: 'Every option that applies, like which topics a message raises.',
  rank: 'All options, ordered from most to least likely.',
  number: 'A quantity with an honest range, like days until a customer leaves.',
};
const PLACEHOLDER = {
  choice: 'Which team should handle this?',
  score: 'How urgent is this?',
  noul: 'Does the customer ask for a refund?',
  multi: 'Which topics does the message raise?',
  rank: 'Which problem should we solve first?',
  number: 'How many days until the customer leaves?',
};
const SCALES = [
  ['Low to high', ['low', 'medium', 'high']],
  ['1 to 5 stars', ['1 star', '2 stars', '3 stars', '4 stars', '5 stars']],
  ['Never to always', ['never', 'rarely', 'sometimes', 'often', 'always']],
  ['Disagree to agree', ['strongly disagree', 'disagree', 'neutral', 'agree', 'strongly agree']],
  ['Trivial to critical', ['trivial', 'minor', 'major', 'critical']],
];
const NUMBERS = [
  ['0 to 10', [0, 1, 2, 3, 5, 7, 10], ''],
  ['Days', [1, 7, 30, 90, 365], 'days'],
  ['Percent', [0, 25, 50, 75, 100], '%'],
  ['Hours', [1, 4, 8, 24, 72], 'hours'],
];
// Option ideas from words in the question, for people facing an empty list.
const IDEAS = [
  [/\b(team|department|queue|owner|route)\b/i, ['billing', 'technical', 'sales', 'account', 'other']],
  [/\b(sentiment|tone|mood|feel)\b/i, ['positive', 'negative', 'mixed', 'neutral']],
  [/\b(intent|want|asking)\b/i, ['question', 'request', 'complaint', 'praise', 'other']],
  [/\b(language)\b/i, ['english', 'spanish', 'french', 'german', 'other']],
  [/\b(topic|topics|about|labels?|tags?)\b/i, ['billing', 'bug', 'feature request', 'pricing', 'security']],
  [/\b(risk|severity|priority)\b/i, ['low', 'medium', 'high', 'critical']],
];
const STOP = new Set('which what does do did is are was were the a an of to in on for this that these those should would could can will it its be we our you your there any how many much there with from by as at or and if has have had who whom whose when where why'.split(' '));

let uidSeq = 0;
export function blank(type = 'choice') {
  return { uid: `q${Date.now().toString(36)}${uidSeq++}`, key: '', keyEdited: false, typeChosen: false, type, text: '',
    options: [], levels: type === 'score' ? ['low', 'medium', 'high'] : [], yes: '', no: '', threshold: 0.5, values: [], unit: '' };
}

// Turn a request-style question into a draft row.
export function fromQuestion(key, q) {
  const b = blank(q.type);
  Object.assign(b, { key, keyEdited: true, typeChosen: true, text: typeof q.instructions === 'string' ? q.instructions : JSON.stringify(q.instructions ?? '') });
  const crit = q.criteria;
  if (['choice', 'multi', 'rank'].includes(q.type)) {
    b.options = Array.isArray(crit) ? crit.map((n) => ({ name: String(n), desc: '' })) : Object.entries(crit || {}).map(([name, desc]) => ({ name, desc: desc ?? '' }));
  }
  if (q.type === 'score') b.levels = (crit || []).map(String);
  if (q.type === 'noul' && crit) { b.yes = crit.true ?? ''; b.no = crit.false ?? ''; }
  if (q.type === 'multi' && q.threshold) b.threshold = q.threshold;
  if (q.type === 'number') { b.values = Array.isArray(crit) ? crit.map(Number) : Object.keys(crit || {}).map(Number); b.unit = q.unit || ''; }
  return b;
}

export function autoKey(text, used = new Set()) {
  const words = String(text).toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter((w) => w && !STOP.has(w));
  let k = words.slice(0, 3).join('_') || 'question';
  if (/^\d/.test(k)) k = `q_${k}`;
  let out = k, n = 2;
  while (used.has(out)) out = `${k}_${n++}`;
  return out;
}

export function suggestType(text) {
  const t = String(text).trim().toLowerCase();
  if (!t) return null;
  if (/^(how many|how much|how long|how old|how far|what (?:is|will be) the (?:number|amount|price|cost))/.test(t)) return 'number';
  if (/\b(all that apply|which .*(apply|mentioned|raise|topics|labels|tags))\b/.test(t) || /^(which|what) (topics|labels|tags|issues)\b/.test(t)) return 'multi';
  if (/^(rank|order|prioriti[sz]e|sort)\b|\bfirst\b.*\?$/.test(t)) return 'rank';
  if (/^how (urgent|likely|severe|serious|important|risky|confident|well|good|bad|positive|negative|hostile|clear|legible)\b|^(rate|on a scale)\b/.test(t)) return 'score';
  if (/^(is|are|does|do|did|can|could|should|would|will|has|have|was|were)\b/.test(t)) return 'noul';
  if (/^(which|what|who|where)\b/.test(t)) return 'choice';
  return null;
}

// Moving between types keeps whatever still makes sense.
function retype(q, type) {
  if (q.type === type) return;
  const names = ['choice', 'multi', 'rank'].includes(q.type) ? q.options.map((o) => o.name) : q.type === 'score' ? q.levels : q.type === 'number' ? q.values.map(String) : [];
  if (['choice', 'multi', 'rank'].includes(type) && !q.options.length) q.options = names.map((name) => ({ name, desc: '' }));
  if (type === 'score' && names.length >= 2 && q.type !== 'noul') q.levels = [...names];
  if (type === 'score' && q.levels.length < 2) q.levels = ['low', 'medium', 'high'];
  if (type === 'number' && !q.values.length) q.values = names.map(Number).filter((v) => Number.isFinite(v));
  q.type = type; q.typeChosen = true;
}

// ------------------------------------------------------------------ validation and request building
export function issues(q, spec, { strict = false } = {}) {
  const out = [];
  const max = spec?.max_options;
  if (strict && !q.text.trim()) out.push('Write the question as a sentence.');
  if (['choice', 'multi', 'rank'].includes(q.type)) {
    const names = q.options.map((o) => o.name.trim()).filter(Boolean);
    if (strict && names.length < (q.type === 'multi' ? 1 : 2)) out.push(q.type === 'multi' ? 'Add at least one option.' : 'Add at least two options.');
    const dup = names.find((n, i) => names.indexOf(n) !== i);
    if (dup) out.push(`Two options are both called "${dup}". Names must be different.`);
    if (max && names.length > max && q.type !== 'multi') out.push(`${spec.name} accepts up to ${max} options in one question.`);
  }
  if (q.type === 'score') {
    if (strict && q.levels.filter((l) => l.trim()).length < 2) out.push('A scale needs at least two levels.');
    if (q.levels.length > 10) out.push('Scales work best with at most 10 levels.');
  }
  if (q.type === 'number') {
    if (strict && q.values.length < 2) out.push('Add at least two possible values.');
    if (new Set(q.values).size !== q.values.length) out.push('Each value can appear only once.');
  }
  return out;
}

export function buildQuestions(list, spec) {
  const questions = {};
  const used = new Set();
  for (const q of list) {
    const errs = issues(q, spec, { strict: true });
    if (errs.length) return { error: errs[0], uid: q.uid };
    let key = (q.keyEdited && q.key) || autoKey(q.text, used);
    if (used.has(key)) key = autoKey(key, used);
    used.add(key);
    const out = { type: q.type, instructions: q.text.trim() };
    if (['choice', 'multi', 'rank'].includes(q.type)) out.criteria = Object.fromEntries(q.options.filter((o) => o.name.trim()).map((o) => [o.name.trim(), o.desc.trim() || null]));
    if (q.type === 'multi' && q.threshold !== 0.5) out.threshold = q.threshold;
    if (q.type === 'score') out.criteria = q.levels.map((l) => l.trim()).filter(Boolean);
    if (q.type === 'noul' && (q.yes.trim() || q.no.trim())) out.criteria = { true: q.yes.trim() || null, false: q.no.trim() || null };
    if (q.type === 'number') { out.criteria = [...q.values].sort((a, b) => a - b); if (q.unit.trim()) out.unit = q.unit.trim(); }
    questions[key] = out;
  }
  if (!Object.keys(questions).length) return { error: 'Add at least one question: choose a kind of answer under Create new Decision.', field: 'questions' };
  if (spec?.max_questions && Object.keys(questions).length > spec.max_questions) return { error: `${spec.name} accepts up to ${spec.max_questions} questions per request.` };
  return { questions };
}

// ------------------------------------------------------------------ rendering
const chip = (i, name, desc, { kind = 'opt' } = {}) => `<span class="opt" data-i="${i}" data-kind="${kind}" tabindex="0" role="button" aria-label="Edit ${esc(name)}">
  <span class="name">${esc(name)}</span>${desc ? `<span class="desc">${esc(desc)}</span>` : ''}
  <button class="x" data-act="rm" data-i="${i}" aria-label="Remove ${esc(name)}">${icon('x')}</button></span>`;

function optionsEditor(q, editing) {
  const ideas = !q.options.length && IDEAS.find(([re]) => re.test(q.text));
  const hint = { choice: 'The model picks exactly one.', multi: 'The model judges each option on its own.', rank: 'The model orders all of them.' }[q.type];
  return `<div class="opts">${q.options.map((o, i) => chip(i, o.name, o.desc)).join('')}
      <input class="opt-add" data-add="opt" placeholder="${q.options.length ? 'Add another option' : 'Type an option, press Enter'}" aria-label="Add an option"></div>
    ${editing != null && q.options[editing] ? `<div class="opt-edit" data-edit="${editing}">
      <div class="row2"><label class="field"><span class="label">Option name</span><input class="input" data-f="name" value="${esc(q.options[editing].name)}"></label>
      <label class="field"><span class="label">What it means (the model reads this)</span><input class="input" data-f="desc" value="${esc(q.options[editing].desc)}" placeholder="e.g. invoices, payments, refunds"></label></div>
      <div style="display:flex;gap:6px"><button class="btn sm" data-act="done">${icon('check')}Done</button><button class="btn btn-quiet sm" data-act="rm" data-i="${editing}">${icon('trash')}Remove</button></div></div>` : ''}
    ${ideas ? `<div class="presets">Ideas: <button data-act="ideas" data-v="${esc(ideas[1].join('|'))}">${esc(ideas[1].join(', '))}</button></div>` : ''}
    ${q.type === 'multi' ? `<label class="q-hint" style="gap:10px;flex-wrap:nowrap"><span style="white-space:nowrap">Select when at least</span>
      <input type="range" min="0.1" max="0.9" step="0.05" value="${q.threshold}" data-f="threshold" style="flex:1;min-width:80px;max-width:180px" aria-label="Selection cut-off">
      <b class="num" data-th>${Math.round(q.threshold * 100)}%</b><span>likely</span></label>` : ''}
    <p class="help">${hint} Click an option to describe it; paste a list to add many at once.</p>`;
}

function levelsEditor(q, editing) {
  const chips = q.levels.map((l, i) => `${i ? `<span class="arrow">${icon('caret-right')}</span>` : ''}${chip(i, l, '', { kind: 'level' })}`).join('');
  return `<div class="opts">${chips}<input class="opt-add" data-add="level" placeholder="Add a level (highest last)" aria-label="Add a level"></div>
    ${editing != null && q.levels[editing] != null ? `<div class="opt-edit" data-edit="${editing}">
      <label class="field"><span class="label">Level ${editing + 1} of ${q.levels.length}</span><input class="input" data-f="level" value="${esc(q.levels[editing])}"></label>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn sm" data-act="done">${icon('check')}Done</button>
        <button class="btn btn-quiet sm" data-act="lv-move" data-d="-1" ${editing === 0 ? 'disabled' : ''}>${icon('arrow-up')}Move lower</button>
        <button class="btn btn-quiet sm" data-act="lv-move" data-d="1" ${editing === q.levels.length - 1 ? 'disabled' : ''}>${icon('arrow-down')}Move higher</button>
        <button class="btn btn-quiet sm" data-act="rm" data-i="${editing}">${icon('trash')}Remove</button></div></div>` : ''}
    <div class="presets">Scales: ${SCALES.map(([n], i) => `<button data-act="scale" data-i="${i}">${esc(n)}</button>`).join('')}</div>
    <p class="help">Lowest first. You get the chance of each level and an average position between them.</p>`;
}

function valuesEditor(q) {
  return `<div class="opts">${q.values.map((v, i) => chip(i, `${v}${unitFor(v, q.unit)}`, '', { kind: 'value' })).join('')}
      <input class="opt-add" data-add="value" inputmode="decimal" placeholder="Add a possible value" aria-label="Add a possible value"></div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <label class="q-hint">Unit <input class="input" data-f="unit" value="${esc(q.unit)}" placeholder="days" style="width:110px;height:28px;padding:3px 8px"></label>
      <div class="presets">Sets: ${NUMBERS.map(([n], i) => `<button data-act="numbers" data-i="${i}">${esc(n)}</button>`).join('')}</div></div>
    <p class="help">List the values the answer could take. You get a best estimate between them and an 80% range.</p>`;
}

function noulEditor(q) {
  const open = q.yes || q.no;
  return `<p class="help">The model returns the probability that the answer is yes.</p>
    <details class="more" ${open ? 'open' : ''}><summary>${icon('caret-right')}Define what counts as yes and no</summary>
      <div class="opt-edit" style="margin-top:8px"><div class="row2">
        <label class="field"><span class="label">Yes means</span><input class="input" data-f="yes" value="${esc(q.yes)}" placeholder="optional"></label>
        <label class="field"><span class="label">No means</span><input class="input" data-f="no" value="${esc(q.no)}" placeholder="optional"></label></div></div></details>`;
}

export function rowHTML(q, { index, editing, strict, spec, types = TYPES, single = false }) {
  const meta = TYPE_META[q.type];
  const sug0 = !q.typeChosen ? suggestType(q.text) : null;
  const sug = types.includes(sug0) ? sug0 : null;
  const tips = issues(q, spec, { strict });
  const body = { choice: optionsEditor, multi: optionsEditor, rank: optionsEditor, score: levelsEditor, number: valuesEditor, noul: noulEditor }[q.type](q, editing);
  return `<div class="q" data-uid="${q.uid}">
    <div class="q-top">
      <button class="q-type" data-act="type" aria-haspopup="menu" aria-label="Question type: ${meta.name}. Change type">${icon(meta.icon)}${meta.name}${icon('caret-down')}</button>
      <textarea class="q-text" rows="1" data-f="text" placeholder="${esc(guideFor(spec?.id)?.q?.[q.type] || PLACEHOLDER[q.type])}" aria-label="Question ${index + 1}">${esc(q.text)}</textarea>
      ${single ? '' : `<button class="icon-btn" data-act="more" aria-haspopup="menu" aria-label="More for question ${index + 1}">${icon('dots-three')}</button>`}
    </div>
    <div class="q-body">
      ${sug && sug !== q.type ? `<div class="q-hint">${icon('lightbulb')}<span>This reads like ${TYPE_META[sug].name.toLowerCase()}.</span><button data-act="suggest" data-t="${sug}">Switch to ${TYPE_META[sug].name.toLowerCase()}</button></div>` : ''}
      ${body}
      ${tips.length ? `<div class="q-tips">${tips.map((t) => `<p>${icon('warning')}${esc(t)}</p>`).join('')}</div>` : ''}
    </div>
  </div>`;
}

const TILE_HELP = {
  choice: 'One option from a list.',
  score: 'A level on an ordered scale.',
  noul: 'How likely a statement is true.',
  multi: 'Every option that applies.',
  rank: 'All options, most likely first.',
  number: 'A value with an honest range.',
};

// A miniature of the answer each type produces, so the tiles show what you get rather than describe it.
const MINI = {
  choice: '<i class="bar" style="--w:82%"></i><i class="bar" style="--w:30%"></i><i class="bar" style="--w:16%"></i>',
  score: '<i class="scale">' + [0, 1, 2, 3, 4].map((k) => `<b class="${k === 3 ? 'on' : ''}"></b>`).join('') + '</i>',
  noul: '<i class="yes"><b style="--w:74%"></b></i><i class="yn"><span>Yes</span><span>No</span></i>',
  multi: '<i class="tick on"></i><i class="tick on"></i><i class="tick"></i>',
  rank: '<i class="bar n" style="--w:78%" data-n="1"></i><i class="bar n" style="--w:52%" data-n="2"></i><i class="bar n" style="--w:28%" data-n="3"></i>',
  number: '<i class="range"><b></b><em></em></i>',
};

export function addGridHTML({ title = 'Create new Decision', text = 'Choose the kind of answer you want. Add as many as you need; the model answers them all at once.' } = {}) {
  return `<div class="create-dec" role="group" aria-labelledby="createdec">
    <div class="create-dec-head"><h3 id="createdec">${esc(title)}</h3>
      <p>${esc(text)}</p></div>
    <div class="add-q">${TYPES.map((t) => `<button data-add-type="${t}"><span class="mini mini-${t}" aria-hidden="true">${MINI[t]}</span><b>${TYPE_META[t].name}</b><span class="d">${TILE_HELP[t]}</span></button>`).join('')}</div>
  </div>`;
}

// ------------------------------------------------------------------ controller
export function createBuilder(root, { list, onChange, spec, onStructure, types = TYPES, single = false }) {
  let editing = null;   // { uid, i }
  let strict = false;
  const L = () => list();
  const find = (el) => { const row = el.closest('[data-uid]'); return row && L().find((q) => q.uid === row.dataset.uid); };
  const changed = (structural = false) => { onChange(); if (structural) { render(); onStructure?.(); } };
  const grow = (ta) => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; };

  function render(focus) {
    const s = spec();
    root.innerHTML = L().map((q, index) => rowHTML(q, { index, editing: editing?.uid === q.uid ? editing.i : null, strict, spec: s, types, single })).join('');
    $$('.q-text', root).forEach(grow);
    if (focus) {
      const row = root.querySelector(`[data-uid="${focus.uid}"]`);
      const el = row && (focus.sel ? $(focus.sel, row) : null);
      el?.focus();
      if (el?.select && focus.select) el.select();
    }
  }
  function renderRow(q, focusSel) {
    const row = root.querySelector(`[data-uid="${q.uid}"]`);
    if (!row) return render();
    const index = L().indexOf(q);
    const tmp = document.createElement('div');
    tmp.innerHTML = rowHTML(q, { index, editing: editing?.uid === q.uid ? editing.i : null, strict, spec: spec(), types, single });
    const fresh = tmp.firstElementChild;
    row.replaceWith(fresh);
    $$('.q-text', fresh).forEach(grow);
    if (focusSel) $(focusSel, fresh)?.focus();
  }

  const addItems = (q, kind, raw) => {
    const parts = raw.split(/\n|,(?![^(]*\))|;/).map((s) => s.trim()).filter(Boolean);
    for (const p of parts) {
      if (kind === 'opt') { if (!q.options.some((o) => o.name === p)) q.options.push({ name: p.replace(/\s*[:=]\s*.*/, ''), desc: (p.match(/[:=]\s*(.*)/) || [])[1] || '' }); }
      if (kind === 'level') q.levels.push(p);
      if (kind === 'value') { const v = parseFloat(p.replace(/[^\d.eE+-]/g, '')); if (Number.isFinite(v) && !q.values.includes(v)) q.values.push(v); }
    }
    if (kind === 'value') q.values.sort((a, b) => a - b);
  };

  root.addEventListener('input', (e) => {
    const q = find(e.target); if (!q) return;
    const f = e.target.dataset.f;
    if (f === 'text') {
      q.text = e.target.value; grow(e.target);
      if (!q.keyEdited) q.key = '';
      const sug0 = !q.typeChosen ? suggestType(q.text) : null;
      const sug = types.includes(sug0) ? sug0 : null;
      const row = e.target.closest('.q');
      const had = $('.q-hint [data-act="suggest"]', row)?.dataset.t || null;
      if ((sug && sug !== q.type ? sug : null) !== had) { const pos = e.target.selectionStart; renderRow(q, '.q-text'); const ta = $(`[data-uid="${q.uid}"] .q-text`, root); ta?.setSelectionRange(pos, pos); }
      onChange(); return;
    }
    if (f === 'name' || f === 'desc') { q.options[editing.i][f] = e.target.value; const c = $(`[data-uid="${q.uid}"] .opt[data-i="${editing.i}"] .${f}`, root); if (c) c.textContent = e.target.value; onChange(); return; }
    if (f === 'level') { q.levels[editing.i] = e.target.value; const c = $(`[data-uid="${q.uid}"] .opt[data-i="${editing.i}"] .name`, root); if (c) c.textContent = e.target.value; onChange(); return; }
    if (f === 'threshold') { q.threshold = +e.target.value; $('[data-th]', e.target.closest('.q')).textContent = `${Math.round(q.threshold * 100)}%`; onChange(); return; }
    if (f === 'yes' || f === 'no' || f === 'unit') { q[f] = e.target.value; onChange(); }
  });
  root.addEventListener('change', (e) => { if (e.target.dataset.f === 'unit') { const q = find(e.target); renderRow(q); } });

  root.addEventListener('paste', (e) => {
    const inp = e.target.closest('[data-add]'); if (!inp) return;
    const text = e.clipboardData.getData('text');
    if (!/[\n,;]/.test(text)) return;
    e.preventDefault();
    const q = find(inp); addItems(q, inp.dataset.add, text); changed(); renderRow(q, '.opt-add');
  });

  root.addEventListener('keydown', (e) => {
    const inp = e.target.closest('[data-add]');
    if (inp) {
      const q = find(inp);
      if (e.key === 'Enter' || (e.key === ',' && inp.dataset.add !== 'value')) {
        e.preventDefault();
        if (!inp.value.trim()) return;
        addItems(q, inp.dataset.add, inp.value); changed(); renderRow(q, '.opt-add');
      } else if (e.key === 'Backspace' && !inp.value) {
        const arr = inp.dataset.add === 'opt' ? q.options : inp.dataset.add === 'level' ? q.levels : q.values;
        if (arr.length) { arr.pop(); changed(); renderRow(q, '.opt-add'); }
      }
      return;
    }
    if (e.target.classList.contains('q-text') && e.key === 'Enter' && !e.shiftKey && !(e.metaKey || e.ctrlKey)) {
      e.preventDefault(); $('.opt-add, .input', e.target.closest('.q'))?.focus();
      return;
    }
    const c = e.target.closest('.opt');
    if (c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); c.click(); }
    if (c && (e.key === 'Delete' || e.key === 'Backspace')) { e.preventDefault(); $('[data-act="rm"]', c)?.click(); }
    if (e.target.closest('.opt-edit') && e.key === 'Enter') { e.preventDefault(); const q = find(e.target); editing = null; renderRow(q, '.opt-add'); }
    if (e.target.closest('.opt-edit') && e.key === 'Escape') { const q = find(e.target); editing = null; renderRow(q, '.opt-add'); }
  });

  root.addEventListener('click', (e) => {
    const q = find(e.target); if (!q) return;
    const a = e.target.closest('[data-act]')?.dataset.act;
    const btn = e.target.closest('[data-act]');
    if (a === 'rm') {
      e.stopPropagation();
      const i = +btn.dataset.i;
      if (q.type === 'score') q.levels.splice(i, 1); else if (q.type === 'number') q.values.splice(i, 1); else q.options.splice(i, 1);
      editing = null; changed(); renderRow(q, '.opt-add'); return;
    }
    if (a === 'done') { editing = null; renderRow(q, '.opt-add'); return; }
    if (a === 'lv-move') {
      const d = +btn.dataset.d, i = editing.i, j = i + d;
      [q.levels[i], q.levels[j]] = [q.levels[j], q.levels[i]]; editing.i = j; changed(); renderRow(q, `[data-act="lv-move"][data-d="${d}"]`); return;
    }
    if (a === 'scale') { q.levels = [...SCALES[+btn.dataset.i][1]]; editing = null; changed(); renderRow(q); return; }
    if (a === 'numbers') { const [, vals, unit] = NUMBERS[+btn.dataset.i]; q.values = [...vals]; q.unit = unit; changed(); renderRow(q); return; }
    if (a === 'scalemenu') {
      popMenu(btn, `<div class="menu-group">Scale presets</div>${SCALES.map(([n, l], i) => `<button class="menu-item" data-pick="${i}" role="menuitem">${icon('gauge')}<span><b>${esc(n)}</b><span>${esc(l.join(', '))}</span></span></button>`).join('')}`,
        { onPick: (i) => { q.levels = [...SCALES[+i][1]]; editing = null; changed(); renderRow(q); } });
      return;
    }
    if (a === 'numbermenu') {
      popMenu(btn, `<div class="menu-group">Value presets</div>${NUMBERS.map(([n, vals, unit], i) => `<button class="menu-item" data-pick="${i}" role="menuitem">${icon('ruler')}<span><b>${esc(n)}</b><span>${esc(vals.join(', '))}${unit ? ` ${esc(unit)}` : ''}</span></span></button>`).join('')}`,
        { onPick: (i) => { const [, vals, unit] = NUMBERS[+i]; q.values = [...vals]; q.unit = unit; changed(); renderRow(q); } });
      return;
    }
    if (a === 'ideas') { addItems(q, 'opt', btn.dataset.v.replace(/\|/g, '\n')); changed(); renderRow(q, '.opt-add'); return; }
    if (a === 'suggest') { retype(q, btn.dataset.t); changed(true); return; }
    if (a === 'type') {
      popMenu(btn, `<div class="menu-group">Question type</div>${types.map((t) => `<button class="menu-item" data-pick="${t}" role="menuitem">${icon(TYPE_META[t].icon)}<span><b>${TYPE_META[t].name}${t === q.type ? ' (current)' : ''}</b><span>${esc(TYPE_HELP[t])}${['multi', 'rank', 'number'].includes(t) ? ' Studio extension.' : ''}</span></span></button>`).join('')}`,
        { onPick: (t) => { retype(q, t); editing = null; changed(true); } });
      return;
    }
    if (a === 'more') {
      const i = L().indexOf(q);
      popMenu(btn, `
        <button class="menu-item" data-pick="up" ${i === 0 ? 'disabled' : ''}>${icon('arrow-up')}<span><b>Move up</b></span></button>
        <button class="menu-item" data-pick="down" ${i === L().length - 1 ? 'disabled' : ''}>${icon('arrow-down')}<span><b>Move down</b></span></button>
        <button class="menu-item" data-pick="dup">${icon('copy')}<span><b>Duplicate</b></span></button>
        <button class="menu-item" data-pick="key">${icon('key')}<span><b>Rename its API key</b><span>Now: ${esc(q.key || autoKey(q.text))}</span></span></button>
        <button class="menu-item" data-pick="del">${icon('trash')}<span><b>Delete question</b></span></button>`, {
        align: 'right',
        onPick: async (p) => {
          const arr = L();
          if (p === 'up' && i > 0) [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
          if (p === 'down' && i < arr.length - 1) [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
          if (p === 'dup') arr.splice(i + 1, 0, { ...structuredClone(q), uid: blank().uid, key: '', keyEdited: false });
          if (p === 'del') arr.splice(i, 1);
          if (p === 'key') {
            const k = await askText({ title: 'Rename the API key', label: 'The name this answer has in the JSON response. Letters, digits and underscores.', value: q.key || autoKey(q.text), pattern: '[A-Za-z_][A-Za-z0-9_]{0,63}' });
            if (k) { q.key = k; q.keyEdited = true; }
          }
          changed(true);
        },
      });
      return;
    }
    const c = e.target.closest('.opt');
    if (c && !e.target.closest('.x')) {
      if (c.dataset.kind === 'value') return;
      editing = { uid: q.uid, i: +c.dataset.i };
      renderRow(q, c.dataset.kind === 'level' ? '[data-f="level"]' : '[data-f="desc"]');
    }
  });

  return {
    render,
    add(type) {
      const q = blank(type); q.typeChosen = true;
      L().push(q); changed(true);
      render({ uid: q.uid, sel: '.q-text' });
    },
    setStrict(v) { strict = v; render(); },
    focus(uid) {
      const row = root.querySelector(`[data-uid="${uid}"]`);
      row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      $('.q-text', row)?.focus();
    },
  };
}
