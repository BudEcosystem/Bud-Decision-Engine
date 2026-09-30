// Template mode in the Playground: a form for a template's variables (built from its variable specs), and its
// questions shown locked, with the per-decision changes the template allows: skip a question, add options.

import { TYPE_META } from './figures.js';
import { $$, esc, icon } from './util.js';

const MEDIA = ['image', 'audio', 'video'];

// ------------------------------------------------------------------ variables
function field(name, v, val, media) {
  const req = v.required ? '' : '<span class="faint small">optional</span>';
  const help = v.description ? `<span class="help">${esc(v.description)}</span>` : '';
  const lab = `<span class="label"><code>${esc(name)}</code> ${req}${v.sensitive ? ` <span class="chip" data-tip="Used for this decision, never written to History">${icon('lock-simple')}sensitive</span>` : ''}</span>`;
  const ph = v.example != null ? String(typeof v.example === 'string' ? v.example : JSON.stringify(v.example)) : v.default != null ? `Default: ${typeof v.default === 'string' ? v.default : JSON.stringify(v.default)}` : '';
  let input;
  if (v.type === 'string' && v.enum) {
    input = `<select class="select" data-var="${esc(name)}">${v.required && v.default == null ? '' : `<option value="">${v.default != null ? `Default (${esc(v.default)})` : 'Not set'}</option>`}${v.enum.map((o) => `<option ${val === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  } else if (v.type === 'string' && (v.max_length == null || v.max_length > 160) && !v.format) {
    input = `<textarea class="textarea" rows="${name.includes('message') || (v.max_length || 20000) > 1000 ? 4 : 2}" data-var="${esc(name)}" placeholder="${esc(ph)}">${esc(val ?? '')}</textarea>`;
  } else if (v.type === 'string') {
    input = `<input class="input" data-var="${esc(name)}" value="${esc(val ?? '')}" placeholder="${esc(ph || (v.format ? `A ${v.format}, e.g. 2026-10-01` : ''))}">`;
  } else if (v.type === 'integer' || v.type === 'number') {
    input = `<input class="input" type="number" data-var="${esc(name)}" value="${esc(val ?? '')}" ${v.type === 'integer' ? 'step="1"' : 'step="any"'} ${v.minimum != null ? `min="${v.minimum}"` : ''} ${v.maximum != null ? `max="${v.maximum}"` : ''} placeholder="${esc(ph)}" style="max-width:180px">`;
  } else if (v.type === 'boolean') {
    input = `<select class="select" data-var="${esc(name)}" style="max-width:180px"><option value="">${v.default != null ? `Default (${v.default ? 'yes' : 'no'})` : 'Not set'}</option><option value="true" ${val === 'true' ? 'selected' : ''}>Yes</option><option value="false" ${val === 'false' ? 'selected' : ''}>No</option></select>`;
  } else if (v.type === 'json') {
    input = `<textarea class="textarea code" rows="4" data-var="${esc(name)}" spellcheck="false" placeholder="${esc(ph || '{ }')}">${esc(val ?? '')}</textarea>`;
  } else if (v.type === 'options') {
    input = `<textarea class="textarea" rows="4" data-var="${esc(name)}" placeholder="One option per line. Add a description after a colon: click search: open the search page">${esc(val ?? '')}</textarea>`;
  } else if (MEDIA.includes(v.type)) {
    const m = media?.[name];
    input = `<label class="dropzone" data-drop="${esc(name)}"><input type="file" accept="${v.type}/*" hidden data-file="${esc(name)}">
      ${m ? (v.type === 'image' ? `<img src="${esc(m.url)}" alt="" style="height:36px;border-radius:5px">` : icon('file')) : icon('file-arrow-up')}
      <span>${m ? `<b style="font-weight:500">${esc(m.name)}</b> <span class="muted">replace</span>` : `<b style="font-weight:500">Attach ${v.type}</b> <span class="muted">or drop a file here</span>`}</span>
      ${m ? `<button class="icon-btn" data-unfile="${esc(name)}" aria-label="Remove">${icon('x')}</button>` : ''}</label>`;
  }
  return `<label class="field var-field">${lab}${input}${help}</label>`;
}

export function varFormHTML(tpl, vars, media) {
  const entries = Object.entries(tpl.variables || {});
  return `<div class="var-form">${entries.map(([n, v]) => field(n, v, vars[n], media)).join('')}</div>`;
}

export function bindVarForm(box, { vars, onChange, onFile, onUnfile }) {
  $$('[data-var]', box).forEach((el) => {
    const ev = el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(ev, () => { vars[el.dataset.var] = el.value; onChange(); });
  });
  $$('[data-file]', box).forEach((el) => el.addEventListener('change', () => { if (el.files[0]) onFile(el.dataset.file, el.files[0]); }));
  $$('[data-drop]', box).forEach((dz) => {
    dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
    dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag'); if (e.dataTransfer.files[0]) onFile(dz.dataset.drop, e.dataTransfer.files[0]); });
  });
  $$('[data-unfile]', box).forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); onUnfile(b.dataset.unfile); }));
}

// The form's text -> typed variable values, as the API checks them (no coercion there, so it happens here).
export function varValues(tpl, vars, media) {
  const out = {};
  for (const [n, v] of Object.entries(tpl.variables || {})) {
    if (MEDIA.includes(v.type)) { if (media?.[n]) out[n] = media[n].id; else if (v.required) return { error: `Attach the ${v.type} for ${n}.`, field: n }; continue; }
    const raw = vars[n];
    if (raw == null || String(raw).trim() === '') {
      if (v.required && v.default == null) return { error: `Fill in ${n}: ${v.description || 'it is required'}.`, field: n };
      continue;
    }
    const s = String(raw);
    if (v.type === 'integer') { if (!/^-?\d+$/.test(s.trim())) return { error: `${n} must be a whole number.`, field: n }; out[n] = parseInt(s, 10); }
    else if (v.type === 'number') { const x = Number(s); if (!Number.isFinite(x)) return { error: `${n} must be a number.`, field: n }; out[n] = x; }
    else if (v.type === 'boolean') out[n] = s === 'true';
    else if (v.type === 'json') { try { out[n] = JSON.parse(s); } catch (e) { return { error: `${n} is not valid JSON: ${e.message}`, field: n }; } }
    else if (v.type === 'options') {
      const lines = s.split('\n').map((x) => x.trim()).filter(Boolean);
      const described = lines.some((l) => l.includes(':'));
      out[n] = described ? Object.fromEntries(lines.map((l) => { const i = l.indexOf(':'); return i < 0 ? [l, null] : [l.slice(0, i).trim(), l.slice(i + 1).trim() || null]; })) : lines;
    } else out[n] = s;
  }
  return { values: out };
}

// A form filled with the template's examples or defaults, so the first Decide works.
export function initialVars(tpl) {
  const out = {};
  for (const [n, v] of Object.entries(tpl.variables || {})) {
    if (v.example != null) out[n] = typeof v.example === 'string' ? v.example : JSON.stringify(v.example, null, 2);
  }
  return out;
}

// Stored variables of a decision -> form text.
export function varsFromDecision(tpl, stored) {
  const out = {};
  for (const [n, v] of Object.entries(stored || {})) {
    const spec = tpl.variables?.[n];
    if (!spec || MEDIA.includes(spec.type) || (v && typeof v === 'object' && v.$redacted)) continue;
    if (spec.type === 'options') out[n] = Array.isArray(v) ? v.join('\n') : Object.entries(v).map(([k, d]) => (d ? `${k}: ${d}` : k)).join('\n');
    else if (spec.type === 'json') out[n] = JSON.stringify(v, null, 2);
    else out[n] = String(v);
  }
  return out;
}

// ------------------------------------------------------------------ locked template questions
function optionsOf(q) {
  const c = q.criteria;
  if (typeof c === 'string') return { dynamic: c };
  if (['choice', 'multi', 'rank'].includes(q.type)) return { list: (Array.isArray(c) ? c.map((n) => [String(n), null]) : Object.entries(c || {})) };
  if (q.type === 'score') return { list: (c || []).map((l) => [typeof l === 'string' ? l : JSON.stringify(l), null]), scale: true };
  if (q.type === 'number') return { list: (Array.isArray(c) ? c : Object.keys(c || {})).map((v) => [`${v}${q.unit ? ` ${q.unit}` : ''}`, null]) };
  return {};
}

export function lockedQuestionsHTML(tpl, { skip = [], addOptions = {} } = {}) {
  const ext = tpl.extensions || {};
  return Object.entries(tpl.questions).map(([k, q]) => {
    const meta = TYPE_META[q.type];
    const skipped = skip.includes(k);
    const canSkip = ext.skip === true || (ext.skip || []).includes(k);
    const canAdd = (ext.options === true || (ext.options || []).includes(k)) && !['score', 'noul'].includes(q.type);
    const o = optionsOf(q);
    const added = addOptions[k] || [];
    const chips = o.dynamic ? `<span class="chip violet">from ${esc(o.dynamic)}</span>`
      : (o.list || []).map(([n, d], i) => `${o.scale && i ? `<span class="arrow">${icon('caret-right')}</span>` : ''}<span class="opt ro" ${d ? `title="${esc(typeof d === 'string' ? d : JSON.stringify(d))}"` : ''}><span class="name">${esc(n.replace(/_/g, ' '))}</span></span>`).join('');
    return `<div class="q locked ${skipped ? 'skipped' : ''}" data-tq="${esc(k)}">
      <div class="q-top"><span class="q-type ro">${icon(meta.icon)}${meta.name}</span>
        <span class="q-text ro">${esc(typeof q.instructions === 'string' ? q.instructions : JSON.stringify(q.instructions ?? ''))}</span>
        ${canSkip ? `<label class="switch small" data-tip="${skipped ? 'Skipped in this decision' : 'Ask this question'}"><input type="checkbox" data-skip="${esc(k)}" ${skipped ? '' : 'checked'} aria-label="Ask ${esc(k)}"></label>` : `<span class="lock" data-tip="From the template">${icon('lock-simple')}</span>`}
      </div>
      ${skipped ? '' : `<div class="q-body"><div class="opts ro">${chips}
        ${added.map((n, i) => `<span class="opt added"><span class="name">${esc(n)}</span><span class="desc">added for this decision</span><button class="x" data-unadd="${esc(k)}" data-i="${i}" aria-label="Remove ${esc(n)}">${icon('x')}</button></span>`).join('')}
        ${canAdd ? `<input class="opt-add" data-addopt="${esc(k)}" inputmode="${q.type === 'number' ? 'decimal' : 'text'}" placeholder="Add an option for this decision" aria-label="Add an option to ${esc(k)}">` : ''}</div></div>`}
    </div>`;
  }).join('');
}

export function bindLocked(box, { skip, addOptions, tpl, onChange }) {
  $$('[data-skip]', box).forEach((c) => c.addEventListener('change', () => {
    const k = c.dataset.skip;
    const i = skip.indexOf(k);
    if (c.checked && i >= 0) skip.splice(i, 1);
    if (!c.checked && i < 0) skip.push(k);
    onChange(true);
  }));
  $$('[data-addopt]', box).forEach((inp) => inp.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const k = inp.dataset.addopt;
    const v = inp.value.trim();
    if (!v) return;
    const q = tpl.questions[k];
    const have = (optionsOf(q).list || []).map(([n]) => n);
    if (have.includes(v) || (addOptions[k] || []).includes(v)) { inp.value = ''; return; }
    if (q.type === 'number' && !Number.isFinite(Number(v))) return;
    (addOptions[k] ||= []).push(v);
    onChange(true, `[data-addopt="${k}"]`);
  }));
  $$('[data-unadd]', box).forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.unadd;
    addOptions[k].splice(+b.dataset.i, 1);
    if (!addOptions[k].length) delete addOptions[k];
    onChange(true);
  }));
}

// add_options for the request: numbers for number questions, names otherwise.
export function addOptionsBody(tpl, addOptions) {
  const out = {};
  for (const [k, names] of Object.entries(addOptions || {})) {
    if (!names?.length || !tpl.questions[k]) continue;
    out[k] = tpl.questions[k].type === 'number' ? names.map(Number) : names;
  }
  return out;
}
