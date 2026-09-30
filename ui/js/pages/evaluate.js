// Evaluate: run one question over many labelled examples on several models. The setup is a column on the left
// (question, examples, models); the results fill the rest: a leaderboard, charts for the selected model,
// one-click recommendations, and every example.

import { buildQuestions, createBuilder, fromQuestion } from '../builder.js';
import { setSub } from '../shell.js';
import { decide, ensureReady, markLearned, readyModels, registry, setPref, store } from '../store.js';
import { $, $$, esc, fmtMs, fmtPct, icon, term, toast } from '../util.js';
import { setTemperature } from './playground.js';

const SAMPLE = `I was charged twice for my subscription this month, please refund one of them.\tbilling
The app crashes as soon as I open the settings screen.\ttechnical
I can't log in, it says my password is wrong but I just reset it.\taccount
Where is my order? It was supposed to arrive on Monday.\tshipping
Can I get an invoice with my company's VAT number on it?\tbilling
After the latest update, notifications stopped working on Android.\ttechnical
Please delete my account and all my data.\taccount
The package arrived but the box was empty.\tshipping
Do you offer a discount for non-profits?\tother
My card was declined but the money left my bank account.\tbilling
The export to PDF button does nothing.\ttechnical
How do I change the email address on my profile?\taccount
Tracking says delivered but nothing is at my door.\tshipping
I love the new design, great job!\tother
You billed me after I cancelled. I want my money back.\tbilling
Two-factor codes never arrive by SMS.\taccount
The website is extremely slow today, pages take a minute to load.\ttechnical
Can I change the delivery address for an order I placed an hour ago?\tshipping
Is there a student plan?\tother
The receipt shows the wrong amount, I paid 49 not 59.\tbilling
Syncing between my laptop and phone has been broken since Tuesday.\ttechnical
Someone else seems to have logged into my account from another country.\taccount
The courier left my parcel in the rain and it's soaked.\tshipping
Do you have a public roadmap?\tother`;
const SAMPLE_Q = { type: 'choice', instructions: 'Which team should handle this message?', criteria: { billing: 'payments, charges, refunds, invoices', technical: 'bugs, crashes, errors, slowness', account: 'login, password, security, profile, deletion', shipping: 'delivery, tracking, parcels, addresses', other: 'anything else' } };
const KEY = 'bud.eval.v2';

let root, builder, reg;
let saved = load();
let selected = new Set();
let run = null;          // { question, keys, rows, results: {model: {items|error}}, running, done, total }
let focus = null;        // model id shown in detail
let budget = 0.05;
let show = 'all';

function load() {
  try { const s = JSON.parse(localStorage.getItem(KEY)); if (s?.question) return s; } catch { /* fresh */ }
  return { question: fromQuestion('team', SAMPLE_Q), dataText: SAMPLE };
}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch { /* ignore */ } };

export async function mount(el) {
  root = el;
  reg = await registry();
  setSub('Measure a model on your own labelled examples');
  root.innerHTML = `<div class="view panes eval-view">
    <section class="spec" aria-label="Setup">
      <div class="spec-scroll">
        <div class="spec-section"><div class="spec-title"><h2>1. The question</h2></div><div id="qb"></div></div>
        <div class="spec-section"><div class="spec-title"><h2>2. Examples</h2><span class="right">
            <button class="btn btn-quiet sm" id="sample">${icon('sparkle')}Sample</button>
            <label class="btn btn-quiet sm">${icon('file-arrow-up')}Open file<input type="file" id="file" accept=".txt,.tsv,.csv,.jsonl" hidden></label></span></div>
          <div class="state-card" id="dcard"></div></div>
        <div class="spec-section"><div class="spec-title"><h2>3. Models</h2><span class="right small muted">Loaded models run in parallel</span></div><div class="group model-checks" id="mbox"></div></div>
      </div>
      <div class="decide-bar"><button class="btn btn-primary" id="go">${icon('play')}Run</button></div>
    </section>
    <section class="pane" aria-label="Results"><div class="pane-body" id="results"></div></section>
  </div>`;
  const list = [saved.question];
  builder = createBuilder($('#qb', root), { list: () => list, spec: () => null, onChange: () => { saved.question = list[0]; save(); }, onStructure: () => { saved.question = list[0]; save(); }, types: ['choice', 'score', 'noul'], single: true });
  builder.render();
  $('#sample', root).addEventListener('click', () => { saved = { question: fromQuestion('team', SAMPLE_Q), dataText: SAMPLE }; save(); list[0] = saved.question; builder.render(); renderData(); });
  $('#file', root).addEventListener('change', async (e) => { const f = e.target.files[0]; if (!f) return; saved.dataText = await f.text(); save(); renderData(); });
  $('#go', root).addEventListener('click', start);
  renderData(); renderModels(); renderResults();
}
export function onState() { if (root?.isConnected && !run?.running) renderModels(); }

// ------------------------------------------------------------------ examples
function parseRows(text) {
  const t = text.trim();
  if (!t) return [];
  const lines = t.split('\n').filter((l) => l.trim());
  if (lines.every((l) => l.trim().startsWith('{'))) return lines.map((l) => { const o = JSON.parse(l); return { text: o.text ?? o.state ?? JSON.stringify(o), label: o.label ?? o.answer ?? null }; });
  const header = lines[0].toLowerCase();
  if (header.includes(',') && /(^|,)\s*"?text"?\s*(,|$)/.test(header)) {
    const cols = csvLine(lines[0]).map((c) => c.toLowerCase().trim());
    const ti = cols.indexOf('text'), li = cols.findIndex((c) => ['label', 'answer', 'correct'].includes(c));
    return lines.slice(1).map((l) => { const c = csvLine(l); return { text: c[ti] ?? '', label: li >= 0 ? (c[li] ?? null) : null }; });
  }
  return lines.map((l) => { const i = l.lastIndexOf('\t'); return i > 0 ? { text: l.slice(0, i).trim(), label: l.slice(i + 1).trim() || null } : { text: l.trim(), label: null }; });
}
function csvLine(line) {
  const out = []; let cur = '', inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQ) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') inQ = false; else cur += ch; }
    else if (ch === '"') inQ = true; else if (ch === ',') { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out;
}

function renderData() {
  const box = $('#dcard', root);
  let rows = [];
  try { rows = parseRows(saved.dataText); } catch { rows = []; }
  const labeled = rows.filter((r) => r.label != null).length;
  box.innerHTML = `<div style="padding:10px 14px;display:flex;gap:8px;align-items:baseline;border-bottom:1px solid var(--line-2)"><b style="font-weight:600">${rows.length} example${rows.length === 1 ? '' : 's'}</b><span class="muted small">${labeled} with a correct answer</span></div>
    ${rows.length ? `<table class="table ex-preview" style="font-size:12.5px"><tbody>${rows.slice(0, 5).map((r) => `<tr><td><span class="txt" title="${esc(r.text)}">${esc(r.text)}</span></td><td class="muted lbl">${esc(r.label ?? '')}</td></tr>`).join('')}
      ${rows.length > 5 ? `<tr><td colspan="2" class="muted small">and ${rows.length - 5} more</td></tr>` : ''}</tbody></table>` : '<p class="muted small" style="padding:12px 14px">No examples yet. Load the sample or open a file.</p>'}
    <details class="disclose" style="border-top:1px solid var(--line-2)"><summary style="padding:0 14px">Edit as text${icon('caret-right', 'chev')}</summary><div class="body" style="padding:0 14px 14px">
      <textarea class="textarea code" id="data" rows="9" spellcheck="false" placeholder="One example per line. Add the correct answer after a tab.">${esc(saved.dataText)}</textarea>
      <span class="help">One example per line with the correct answer after a tab, a CSV with <code>text</code> and <code>label</code> columns, or JSON lines. Answers are optional; without them you still get a results table.</span></div></details>`;
  const ta = $('#data', box);
  ta.addEventListener('keydown', (e) => { if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText('\t', s, ta.selectionEnd, 'end'); } });
  ta.addEventListener('change', () => { saved.dataText = ta.value; save(); const open = true; renderData(); if (open) $('#dcard details', root).open = true; });
  ta.addEventListener('input', () => { saved.dataText = ta.value; save(); });
  $('#go', root).innerHTML = `${icon('play')}Run on ${rows.length} example${rows.length === 1 ? '' : 's'}`;
}

// ------------------------------------------------------------------ models
function renderModels() {
  const box = $('#mbox', root);
  const st = store.state;
  if (!st || !box) return;
  const usable = st.models.filter((m) => m.downloaded || m.worker);
  const ready = new Set(readyModels().map((m) => m.id));
  if (!selected.size) ready.forEach((id) => selected.add(id));
  const html = usable.length ? usable.map((m) => {
    const r = reg.models[m.id] || {}; const mk = reg.makers[r.maker_id] || {}; const src = mk.avatar || r.logo || mk.logo;
    return `<label><input type="checkbox" value="${m.id}" ${selected.has(m.id) ? 'checked' : ''}><span class="logo sm">${src ? `<img src="/ui/${esc(src)}" alt="">` : ''}</span>
      <span class="nm"><b>${esc(m.name)}</b><span>${ready.has(m.id) ? 'Loaded' : 'Loads first'}, ${esc(m.params)}</span></span></label>`;
  }).join('') : '<p class="muted small" style="padding:12px 14px">Download a model on the Models page first.</p>';
  if (box.dataset.html === html) return;
  box.dataset.html = html; box.innerHTML = html;
  $$('input[type=checkbox]', box).forEach((c) => c.addEventListener('change', () => { c.checked ? selected.add(c.value) : selected.delete(c.value); box.dataset.html = ''; }));
}

// ------------------------------------------------------------------ running
async function start() {
  if (run?.running) return;
  let question, rows;
  const built = buildQuestions([saved.question], null);
  if (built.error) { toast(built.error, 'error'); builder.setStrict(true); return; }
  question = Object.values(built.questions)[0];
  try { rows = parseRows(saved.dataText); } catch (e) { toast(`The examples could not be read: ${e.message}`, 'error'); return; }
  if (!rows.length) { toast('Add at least one example.', 'error'); return; }
  if (rows.length > 2000) { toast('Up to 2,000 examples per run.', 'error'); return; }
  const models = [...selected].filter((id) => store.state.models.some((m) => m.id === id && (m.downloaded || m.worker)));
  if (!models.length) { toast('Choose at least one model.', 'error'); return; }
  const keys = question.type === 'choice' ? Object.keys(question.criteria) : question.type === 'score' ? question.criteria.map((_, i) => String(i)) : ['false', 'true'];
  rows = rows.map((r) => ({ ...r, gold: matchLabel(r.label, question, keys) }));
  const unmatched = rows.filter((r) => r.label != null && r.gold == null).length;
  if (unmatched) toast(`${unmatched} answer${unmatched === 1 ? '' : 's'} did not match any option and will not be scored.`);
  run = { question, keys, rows, results: {}, running: true, done: 0, total: rows.length * models.length, started: performance.now() };
  focus = models[0];
  $('#go', root).disabled = true;
  renderResults();
  await Promise.all(models.map((mid) => runModel(mid)));
  run.running = false;
  run.seconds = (performance.now() - run.started) / 1000;
  if (run.rows.some((r) => r.gold != null)) markLearned('evaluated');
  const best = Object.entries(run.results).filter(([, R]) => R.items).map(([id, R]) => [id, metrics(R.items, run.rows)?.acc ?? -1]).sort((a, b) => b[1] - a[1])[0];
  if (best) focus = best[0];
  $('#go', root).disabled = false;
  renderResults();
}

async function runModel(mid) {
  const m = store.state.models.find((x) => x.id === mid);
  if (m.worker?.status !== 'ready') {
    try { await ensureReady(mid, () => setProgress(`Loading ${m.name}`)); }
    catch (e) { run.results[mid] = { error: e.message }; return; }
  }
  const out = [];
  run.results[mid] = { items: out };
  for (const r of run.rows) {
    try {
      const { data: res } = await decide({ model: mid, state: r.text, questions: { q: run.question } }, { surface: 'eval' });
      const a = res.answers.q;
      const probs = run.keys.map((k) => a.probabilities[k] ?? 0);
      const pi = probs.indexOf(Math.max(...probs));
      out.push({ probs, pred: pi, top: probs[pi], latency: res.latency_ms });
    } catch (e) { out.push({ error: e.message }); }
    run.done++;
    if (run.done % 3 === 0 || run.done === run.total) setProgress(`${run.done} of ${run.total} decisions`);
    liveRender();
  }
}

let liveTimer = null;
function liveRender() { if (liveTimer) return; liveTimer = setTimeout(() => { liveTimer = null; if (run?.running && root?.isConnected) renderResults(); }, 500); }
function setProgress(t) { if (run) run.progressText = t; const el = $('#prog', root); if (el) el.textContent = t; }

function matchLabel(label, question, keys) {
  if (label == null) return null;
  const l = String(label).trim().toLowerCase();
  if (question.type === 'noul') { if (['yes', 'true', '1', 'y'].includes(l)) return 1; if (['no', 'false', '0', 'n'].includes(l)) return 0; return null; }
  if (question.type === 'score') { if (/^\d+$/.test(l) && +l < keys.length) return +l; const i = question.criteria.findIndex((c) => String(c).toLowerCase() === l); return i >= 0 ? i : null; }
  const i = keys.findIndex((k) => k.toLowerCase() === l);
  return i >= 0 ? i : null;
}

// ------------------------------------------------------------------ metrics
function scale(p, T) {
  if (T === 1) return p;
  const w = p.map((x) => Math.exp(Math.log(Math.max(x, 1e-12)) / T)); const s = w.reduce((a, b) => a + b, 0);
  return w.map((x) => x / s);
}
function metrics(items, rows, T = 1) {
  const pairs = items.map((it, i) => [it, rows[i]]).filter(([it, r]) => it && !it.error && r.gold != null);
  const n = pairs.length;
  if (!n) return null;
  let correct = 0, brier = 0, nll = 0, confWrong = 0;
  const bins = Array.from({ length: 10 }, () => ({ n: 0, conf: 0, acc: 0 }));
  for (const [it, r] of pairs) {
    const p = scale(it.probs, T);
    const pi = p.indexOf(Math.max(...p)); const top = p[pi]; const ok = pi === r.gold;
    correct += ok; nll -= Math.log(Math.max(p[r.gold], 1e-12));
    brier += p.reduce((a, x, k) => a + (x - (k === r.gold ? 1 : 0)) ** 2, 0);
    if (top >= 0.9 && !ok) confWrong++;
    const b = bins[Math.min(9, Math.floor(top * 10))]; b.n++; b.conf += top; b.acc += ok;
  }
  const ece = bins.reduce((a, b) => a + (b.n ? Math.abs(b.acc / b.n - b.conf / b.n) * b.n / n : 0), 0);
  return { n, acc: correct / n, brier: brier / n, nll: nll / n, ece, confWrong, bins: bins.map((b, i) => ({ lo: i / 10, n: b.n, conf: b.n ? b.conf / b.n : null, acc: b.n ? b.acc / b.n : null })) };
}
function fitTemperature(items, rows) {
  let best = { T: 1, nll: Infinity };
  for (let i = 0; i <= 60; i++) {
    const T = Math.exp(Math.log(0.25) + (Math.log(8) - Math.log(0.25)) * i / 60);
    const m = metrics(items, rows, T);
    if (m && m.nll < best.nll) best = { T, nll: m.nll };
  }
  return best.T;
}
function coverage(items, rows, th) {
  const pairs = items.map((it, i) => [it, rows[i]]).filter(([it, r]) => it && !it.error && r.gold != null);
  const cov = pairs.filter(([it]) => it.top >= th);
  const acc = cov.length ? cov.filter(([it, r]) => it.pred === r.gold).length / cov.length : null;
  return { share: pairs.length ? cov.length / pairs.length : 0, acc, n: cov.length };
}
function safeThreshold(items, rows) {
  for (let t = 0.3; t <= 0.995; t += 0.005) {
    const c = coverage(items, rows, t);
    if (c.n >= 3 && c.acc >= 1 - budget) return { t, ...c };
  }
  return null;
}
const nameOf = (id) => esc(store.state?.models.find((m) => m.id === id)?.name || id);
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };

// ------------------------------------------------------------------ results
function renderResults() {
  const box = $('#results', root);
  if (!box) return;
  if (!run) {
    box.innerHTML = `<div class="empty" style="min-height:60vh"><div class="glyph">${icon('list-checks')}</div><h2>How good is a model on your data?</h2>
      <p>Write one question, add examples with the right answer, pick models and press Run. You will see how often each model is right, whether its percentages can be trusted, and which ${term('threshold', 'act threshold')} keeps mistakes under your budget.</p>
      <p class="small">The sample is 24 support tickets, each labelled with the team that should handle it.</p></div>`;
    return;
  }
  const ids = Object.keys(run.results);
  const labeled = run.rows.some((r) => r.gold != null);
  const pctDone = run.total ? Math.round((run.done / run.total) * 100) : 0;
  const head = `<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <h2 style="font-size:17px">Results</h2>
      ${run.running ? `<span class="progress" style="width:160px"><i style="width:${pctDone}%"></i></span><span class="small muted" id="prog">${esc(run.progressText || '')}</span>` : `<span class="small muted">${run.total} decisions in ${run.seconds?.toFixed(1)} s</span>`}
      <span style="margin-left:auto;display:flex;gap:8px;align-items:center">${labeled ? `<label class="small muted" style="display:flex;gap:6px;align-items:center">Error budget <select class="select" id="budget" style="width:auto">${[0.01, 0.02, 0.05, 0.1, 0.2].map((b) => `<option value="${b}" ${b === budget ? 'selected' : ''}>${fmtPct(b)}</option>`).join('')}</select></label>` : ''}
        <button class="btn sm" id="csv" ${run.running ? 'disabled' : ''}>${icon('file-csv')}Export CSV</button></span></div>`;
  box.innerHTML = `${head}${labeled ? leaderboard(ids) : plainBoard(ids)}${labeled && focus && run.results[focus]?.items ? detail(focus) : ''}${examplesTable(ids, labeled)}`;
  $('#budget', box)?.addEventListener('change', (e) => { budget = +e.target.value; renderResults(); });
  $('#csv', box)?.addEventListener('click', exportCsv);
  $$('[data-focus]', box).forEach((r) => r.addEventListener('click', () => { focus = r.dataset.focus; renderResults(); }));
  $$('[data-show]', box).forEach((b) => b.addEventListener('click', () => { show = b.dataset.show; renderResults(); }));
  $('[data-use-t]', box)?.addEventListener('click', (e) => { setTemperature(+e.currentTarget.dataset.useT); toast(`The Playground now applies a calibration temperature of ${e.currentTarget.dataset.useT}.`); });
  $('[data-use-th]', box)?.addEventListener('click', (e) => { setPref('threshold', +e.currentTarget.dataset.useTh); toast(`Act threshold set to ${fmtPct(+e.currentTarget.dataset.useTh, 1)}.`); });
}

function leaderboard(ids) {
  const rows = ids.map((id) => {
    const R = run.results[id];
    if (R.error) return `<tr><td>${nameOf(id)}</td><td colspan="4" style="color:var(--red-text)">${esc(R.error)}</td></tr>`;
    const m = metrics(R.items, run.rows);
    const lat = median(R.items.filter((i) => i.latency != null).map((i) => i.latency));
    if (!m) return `<tr><td>${nameOf(id)}</td><td colspan="4" class="muted">Running</td></tr>`;
    return `<tr data-focus="${id}" class="${id === focus ? 'sel' : ''}" tabindex="0"><td><b style="font-weight:600">${nameOf(id)}</b><div class="small muted">${m.n} scored${run.running ? ', running' : ''}</div></td>
      <td><span class="bar"><i style="width:${(m.acc * 100).toFixed(1)}%"></i></span><b class="num">${fmtPct(m.acc)}</b></td>
      <td class="num">${m.ece.toFixed(3)}</td><td class="num">${m.confWrong}</td><td class="num">${fmtMs(lat)}</td></tr>`;
  }).join('');
  return `<div class="card" style="overflow:hidden"><table class="table hover board"><thead><tr><th>Model</th><th>Right</th><th class="num"><span class="term" data-term="calibration" tabindex="0">Calibration error</span></th><th class="num"><span class="term" tabindex="0" data-tip="Answers given with 90% or more that were wrong: the most dangerous kind of mistake.">Confident mistakes</span></th><th class="num">Typical time</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function plainBoard(ids) {
  return `<div class="card" style="overflow:hidden"><table class="table"><thead><tr><th>Model</th><th class="num">Done</th><th class="num">Typical time</th><th class="num">Errors</th></tr></thead><tbody>
    ${ids.map((id) => { const R = run.results[id]; if (R.error) return `<tr><td>${nameOf(id)}</td><td colspan="3" style="color:var(--red-text)">${esc(R.error)}</td></tr>`;
      return `<tr><td>${nameOf(id)}</td><td class="num">${R.items.length} of ${run.rows.length}</td><td class="num">${fmtMs(median(R.items.filter((i) => i.latency != null).map((i) => i.latency)))}</td><td class="num">${R.items.filter((i) => i.error).length}</td></tr>`; }).join('')}
  </tbody></table></div><p class="help">Add the correct answer to each example (after a tab) to see accuracy, calibration and a safe threshold.</p>`;
}

function detail(id) {
  const items = run.results[id].items;
  const m = metrics(items, run.rows);
  if (!m) return '';
  const T = run.running ? null : fitTemperature(items, run.rows);
  const mT = T ? metrics(items, run.rows, T) : null;
  const safe = run.running ? undefined : safeThreshold(items, run.rows);
  const small = m.n < 50 ? ` With ${m.n} examples this is a rough estimate; 100 or more give a dependable one.` : '';
  let tempRow = '';
  if (T) {
    tempRow = mT.ece >= m.ece - 0.005
      ? `<div class="grow stack"><span class="k"><b style="font-weight:600">No temperature helps</b></span><span class="v small">The best fit (${T.toFixed(2)}) would not lower the calibration error of ${m.ece.toFixed(3)}, so keep the model's own numbers.${small}</span></div>`
      : `<div class="grow rec"><span class="k" style="display:grid;gap:2px"><b style="font-weight:600">Calibration temperature ${T.toFixed(2)}</b><span class="small muted">Lowers the calibration error from ${m.ece.toFixed(3)} to ${mT.ece.toFixed(3)} without changing any answer: the model is ${T > 1 ? 'overconfident' : 'underconfident'} here.${small}</span></span><span class="v"><button class="btn sm" data-use-t="${T.toFixed(2)}">Use in Playground</button></span></div>`;
  }
  let thRow = '';
  if (safe !== undefined) {
    thRow = safe
      ? `<div class="grow rec"><span class="k" style="display:grid;gap:2px"><b style="font-weight:600">Act threshold ${fmtPct(safe.t, 1)}</b><span class="small muted">Keeps mistakes under ${fmtPct(budget)} while automating ${fmtPct(safe.share)} of these examples at ${fmtPct(safe.acc)} accuracy; the rest go to a person.</span></span><span class="v"><button class="btn sm" data-use-th="${safe.t.toFixed(3)}">Use this threshold</button></span></div>`
      : `<div class="grow stack"><span class="k" style="color:var(--orange-text)"><b style="font-weight:600">No safe threshold</b></span><span class="v small">Even the most confident answers are wrong more than ${fmtPct(budget)} of the time. Send these to a person, or try another model.</span></div>`;
  }
  return `<div style="display:grid;gap:10px">
    <div style="display:flex;align-items:baseline;gap:8px"><h3 style="font-size:15px">${nameOf(id)}</h3><span class="small muted">Select another model in the table above to compare.</span></div>
    <div class="chart-row" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <div class="chart-card"><h3>Are its percentages honest?</h3><span class="sub">How sure it said it was, against how often it was right. Points on the dashed line are honest; below it means overconfident.</span>${reliability(m)}</div>
      <div class="chart-card"><h3>What each act threshold would do</h3><span class="sub">Share of examples handled automatically (mid violet) and how often those answers were right (deep violet). The orange line is your error budget.</span>${thresholdChart(items, safe)}</div>
    </div>
    ${tempRow || thRow ? `<div class="group">${thRow}${tempRow}</div>` : ''}
  </div>`;
}

function reliability(m) {
  const W = 360, H = 220, L = 38, B = 26, T = 10, R = 12;
  const x = (v) => L + v * (W - L - R), y = (v) => T + (1 - v) * (H - T - B);
  const pts = m.bins.filter((b) => b.n);
  const maxN = Math.max(...pts.map((b) => b.n));
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return `<figure class="reliability" style="margin:0"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Reliability chart: confidence against accuracy">
    ${ticks.map((t) => `<line class="grid-l" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t * 100}%</text><text x="${x(t)}" y="${H - 8}" text-anchor="middle">${t * 100}%</text>`).join('')}
    <line class="diag" x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}"/>
    ${pts.length > 1 ? `<polyline class="curve" points="${pts.map((b) => `${x(b.conf)},${y(b.acc)}`).join(' ')}"/>` : ''}
    ${pts.map((b) => { const r = 3.5 + 3.5 * Math.sqrt(b.n / maxN); const tip = `Said ${fmtPct(b.lo)} to ${fmtPct(b.lo + 0.1)} sure: ${b.n} example${b.n === 1 ? '' : 's'}, right ${fmtPct(b.acc)} of the time`;
      return `<circle class="pt" cx="${x(b.conf)}" cy="${y(b.acc)}" r="${r.toFixed(1)}"/><circle class="hit" cx="${x(b.conf)}" cy="${y(b.acc)}" r="12" data-tip="${esc(tip)}"/>`; }).join('')}
  </svg></figure>`;
}

function thresholdChart(items, safe) {
  const W = 360, H = 220, L = 38, B = 26, T = 10, R = 12;
  const xs = Array.from({ length: 50 }, (_, i) => 0.5 + i * 0.01);
  const x = (v) => L + ((v - 0.5) / 0.49) * (W - L - R), y = (v) => T + (1 - v) * (H - T - B);
  const pts = xs.map((t) => [t, coverage(items, run.rows, t)]);
  const cov = pts.map(([t, c]) => `${x(t).toFixed(1)},${y(c.share).toFixed(1)}`).join(' ');
  const acc = pts.filter(([, c]) => c.acc != null).map(([t, c]) => `${x(t).toFixed(1)},${y(c.acc).toFixed(1)}`).join(' ');
  return `<figure class="reliability" style="margin:0"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Automated share and accuracy by act threshold">
    ${[0, 0.25, 0.5, 0.75, 1].map((t) => `<line class="grid-l" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t * 100}%</text>`).join('')}
    ${[0.5, 0.6, 0.7, 0.8, 0.9, 0.99].map((t) => `<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${Math.round(t * 100)}%</text>`).join('')}
    <line class="budget" x1="${L}" x2="${W - R}" y1="${y(1 - budget)}" y2="${y(1 - budget)}"/>
    ${safe && safe.t >= 0.5 ? `<line class="pick" x1="${x(safe.t)}" x2="${x(safe.t)}" y1="${T}" y2="${H - B}"/>` : ''}
    <polyline class="cov" points="${cov}"/><polyline class="acc" points="${acc}"/>
  </svg></figure>`;
}

function examplesTable(ids, labeled) {
  const label = (i) => i == null ? '' : run.question.type === 'noul' ? (i ? 'yes' : 'no') : run.question.type === 'score' ? run.question.criteria[i] : run.keys[i];
  const rows = run.rows.map((r, i) => ({ r, i })).filter(({ r, i }) => {
    if (show === 'all' || !labeled) return true;
    const outs = ids.map((id) => run.results[id].items?.[i]).filter((x) => x && !x.error);
    if (show === 'wrong') return outs.some((o) => o.pred !== r.gold);
    return outs.some((o) => o.pred !== r.gold && o.top >= 0.9);
  });
  return `<div class="card" style="overflow:hidden">
    <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line-2);flex-wrap:wrap"><h3 style="font-size:13.5px">Every example</h3>
      ${labeled ? `<span class="seg" role="group" style="margin-left:auto">${[['all', 'All'], ['wrong', 'Mistakes'], ['confident', 'Confident mistakes']].map(([k, l]) => `<button data-show="${k}" aria-pressed="${show === k}">${l}</button>`).join('')}</span>` : ''}</div>
    <div class="tscroll"><table class="table ex-table"><thead><tr><th class="num">#</th><th>Example</th>${labeled ? '<th>Correct</th>' : ''}${ids.map((id) => `<th>${nameOf(id)}</th>`).join('')}</tr></thead><tbody>
    ${rows.slice(0, 400).map(({ r, i }) => `<tr><td class="num muted">${i + 1}</td><td><span class="txt" title="${esc(r.text)}">${esc(r.text)}</span></td>
      ${labeled ? `<td>${esc(label(r.gold))}</td>` : ''}
      ${ids.map((id) => { const o = run.results[id].items?.[i]; if (!o) return '<td class="muted">...</td>'; if (o.error) return `<td class="tag-bad" title="${esc(o.error)}">error</td>`;
        const mark = r.gold == null ? '' : o.pred === r.gold ? `<span class="tag-ok" aria-label="correct">${icon('check')}</span>` : `<span class="tag-bad" aria-label="wrong">${icon('x')}</span>`;
        return `<td style="white-space:nowrap">${mark} ${esc(label(o.pred))} <span class="muted num">${fmtPct(o.top)}</span></td>`; }).join('')}</tr>`).join('')}
    </tbody></table></div>${rows.length > 400 ? `<p class="help" style="padding:10px 14px">Showing 400 of ${rows.length}. Export CSV for all.</p>` : ''}</div>`;
}

function exportCsv() {
  const ids = Object.keys(run.results).filter((id) => run.results[id].items);
  const label = (i) => i == null ? '' : run.question.type === 'noul' ? (i ? 'yes' : 'no') : run.question.type === 'score' ? run.question.criteria[i] : run.keys[i];
  const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
  const head = ['text', 'correct', ...ids.flatMap((id) => [`${id}_answer`, `${id}_probability`])];
  const lines = run.rows.map((r, i) => [q(r.text), q(label(r.gold)), ...ids.flatMap((id) => { const o = run.results[id].items[i]; return o && !o.error ? [q(label(o.pred)), o.top.toFixed(4)] : ['', '']; })].join(','));
  const blob = new Blob([`${head.join(',')}\n${lines.join('\n')}\n`], { type: 'text/csv' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'bud-decision-results.csv' });
  a.click(); URL.revokeObjectURL(a.href);
}
