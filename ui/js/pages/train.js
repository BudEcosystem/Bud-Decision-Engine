// Train: teach a model your own decisions. Made for people who know nothing about training or data:
//   1. drop a file of examples (or try the example file),
//   2. check in plain words what it will learn (the question, the answers, the recommended model, the time),
//   3. start, watch it practise, and see whether it got better on examples it never saw.
// Everything technical (splits, LoRA, replay, calibration, the release gate) happens in basal/training and is only
// ever described by its effect.

import { api, store } from '../store.js';
import { loadDecision } from './playground.js';
import { fromQuestion } from '../builder.js';
import { setSub } from '../shell.js';
import { $, $$, confirmDialog, esc, fmtDuration, icon, popMenu, toast } from '../util.js';

let root, timer, params = {};
let caps = null;           // /api/training/capabilities
let ds = null;             // the dataset being reviewed
let chosen = null;         // model id picked on the review screen

export function mount(el, p = {}) {
  root = el;
  params = p;
  clearInterval(timer);
  setSub('Teach a model your own decisions');
  if (p.id) return jobView(p.id);
  if (p.data) return reviewView(p.data);
  homeView();
}

export function unmount() { clearInterval(timer); }
export function onState() {}

const pct = (x) => (x == null ? '' : `${Math.round(x * 100)}%`);
const shell = (inner) => `<div class="view scroll"><div class="pad train">${inner}</div></div>`;

// ------------------------------------------------------------------ 1. start

async function homeView() {
  root.innerHTML = shell('<p class="faint">Loading…</p>');
  const [c, ft, jb] = await Promise.all([
    api('/api/training/capabilities').catch((e) => ({ enabled: false, reason: e.message, models: [] })),
    api('/api/finetunes').catch(() => ({ finetunes: [] })),
    api('/api/training/jobs').catch(() => ({ jobs: [] })),
  ]);
  caps = c;
  const running = jb.jobs.find((j) => ['running', 'queued', 'waiting'].includes(j.state));
  root.innerHTML = shell(`
    <div class="page-title"><div><h1>Teach a model your own decisions</h1>
      <p>Show it examples of the right answer. It practises on most of them, then proves on the ones it has never seen
      that it got better, without forgetting what it already knew.</p></div></div>
    ${running ? `<a class="note violet train-live" href="#/train/${esc(running.id)}">${icon(running.state === 'waiting' ? 'hourglass-medium' : 'pulse')}<span><b>${esc(running.name)}</b> ${running.state === 'waiting' ? 'starts when the training before it finishes' : `is learning now: ${esc(running.text || 'starting')}`}. Open</span></a>` : ''}
    ${c.enabled ? dropPanel() : unavailable(c)}
    <ol class="train-how">
      <li>${icon('file-arrow-up')}<div><b>Show it examples</b><span>A spreadsheet with the text in one column and the right answer in another. A few hundred rows is ideal.</span></div></li>
      <li>${icon('sparkle')}<div><b>It practises</b><span>It learns from most of your examples, while being reminded of everything it already knows.</span></div></li>
      <li>${icon('seal-check')}<div><b>It proves itself</b><span>It is tested on examples it never saw. You only get the new model if it did better.</span></div></li>
    </ol>
    ${finetuneList(ft.finetunes)}
    ${jobList(jb.jobs)}`);
  wireDrop();
  $('#allow-exp', root)?.addEventListener('click', allowExperimental);
  $('#ft-import', root)?.addEventListener('click', () => $('#ft-file', root).click());
  $('#ft-file', root)?.addEventListener('change', (e) => e.target.files[0] && importFinetune(e.target.files[0]));
  $$('[data-use]', root).forEach((b) => b.addEventListener('click', () => {
    const f = ft.finetunes.find((x) => x.id === b.dataset.use);
    useIt(b.dataset.use, { showcase: f?.example ? { request: f.example } : null });
  }));
  $$('[data-del-ft]', root).forEach((b) => b.addEventListener('click', () => deleteFinetune(b.dataset.delFt, b.dataset.name)));
}

function dropPanel() {
  return `<section class="create-dec train-drop" id="drop">
    <div class="dropzone train-dz" tabindex="0" role="button" aria-label="Choose a file of examples">
      <span class="dz-icon">${icon('upload-simple')}</span>
      <div><b>Drop a file of examples here</b>
      <span>A spreadsheet saved as CSV works best. JSON lines and tab-separated text work too.</span></div>
    </div>
    <div class="train-drop-actions">
      <button class="btn btn-primary" id="choose">${icon('file-arrow-up')}Choose a file</button>
      <button class="btn btn-plain" id="sample">Try it with an example file</button>
      <details class="train-format"><summary>What should the file look like?</summary>
        <table class="table small"><thead><tr><th>ticket</th><th>team</th><th>urgent</th></tr></thead><tbody>
        <tr><td>My laptop screen cracked this morning.</td><td>devices</td><td>no</td></tr>
        <tr><td>The whole team can't sign in to Keystone.</td><td>identity</td><td>yes</td></tr></tbody></table>
        <p class="small muted">One row per example. The column with the most text is what the model reads; every other
        column is an answer it learns to give. Yes/no columns become yes/no questions.</p></details>
    </div>
    <input type="file" id="file" accept=".csv,.tsv,.txt,.jsonl,.json" hidden>
  </section>`;
}

function unavailable(c) {
  return `<section class="note ${c.experimental ? 'amber' : ''} train-off">${icon(c.experimental ? 'warning' : 'info')}
    <div><b>${c.experimental ? 'Training on this GPU is experimental' : 'Training isn’t available on this computer'}</b>
    <p>${esc(c.reason || 'Training needs a GPU.')}</p>
    ${c.experimental ? `<p>It may be slower, and a training that goes wrong is stopped and nothing is saved.</p>
      <button class="btn btn-plain" id="allow-exp">Try training on this GPU</button>` : '<p>Every model still runs here; only teaching them new decisions needs a GPU.</p>'}</div></section>`;
}

// A fine-tune exported from another studio (Export on its row): the adapter and its record, a few to a few hundred MB.
// The model it was trained from downloads like any other model.
async function importFinetune(file) {
  const btn = $('#ft-import', root);
  btn.disabled = true;
  btn.innerHTML = `${icon('circle-notch')}Importing`;
  try {
    const form = new FormData();
    form.append('file', file);
    const man = await api('/api/finetunes/import', { method: 'POST', form });
    toast(`${man.name || 'The model'} is ready to use.`);
    homeView();
  } catch (e) {
    toast(e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = `${icon('file-arrow-up')}Import a trained model`;
  }
}

async function allowExperimental() {
  try {
    await api('/api/settings', { method: 'POST', body: { experimental_training: true } });
    toast('Experimental training is on. You can turn it off on the System page.');
    homeView();
  } catch (e) { toast(e.message, 'error'); }
}

function wireDrop() {
  const dz = $('.train-dz', root);
  if (!dz) return;
  const input = $('#file', root);
  const pick = () => input.click();
  dz.addEventListener('click', pick);
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
  $('#choose', root).addEventListener('click', pick);
  input.addEventListener('change', () => input.files[0] && upload(input.files[0]));
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', (e) => e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]));
  $('#sample', root).addEventListener('click', async () => {
    const r = await fetch('/ui/samples/support-tickets.csv');
    upload(new File([await r.blob()], 'support-tickets.csv', { type: 'text/csv' }));
  });
}

async function upload(file) {
  const dz = $('.train-dz', root);
  dz?.classList.add('busy');
  if (dz) dz.querySelector('b').textContent = `Reading ${file.name}…`;
  const form = new FormData();
  form.append('file', file);
  try {
    const meta = await api('/api/training/datasets', { method: 'POST', form });
    location.hash = `#/train?data=${meta.id}`;
  } catch (e) {
    toast(e.message, 'error');
    homeView();
  }
}

function finetuneList(list) {
  const importer = `<button class="btn btn-quiet sm" id="ft-import">${icon('file-arrow-up')}Import a trained model</button>
    <input type="file" id="ft-file" accept=".zip" hidden>`;
  if (!list.length) return `<section class="train-sec train-import-only">${importer}</section>`;
  return `<section class="train-sec"><div class="train-sec-head"><h2>Your trained models</h2>${importer}</div><div class="card train-list">
    ${list.map((f) => `<div class="train-row">
      <span class="train-row-main"><b>${esc(f.name)}</b><span class="muted small">From ${esc(baseName(f.base_model))}, ${esc(dateOf(f.created))}</span></span>
      ${f.accuracy_before != null ? `<span class="train-delta" title="On examples it never saw during training">${pct(f.accuracy_before)} ${icon('arrow-right')} <b>${pct(f.accuracy_after)}</b></span>` : ''}
      <span class="train-row-acts"><button class="btn btn-quiet sm" data-use="${esc(f.id)}" data-ft="1">Use</button>
      <a class="icon-btn" href="/api/finetunes/${encodeURIComponent(f.id)}/export" download title="Export, to use it on another computer" aria-label="Export ${esc(f.name)}">${icon('download-simple')}</a>
      <button class="icon-btn" data-del-ft="${esc(f.id)}" data-name="${esc(f.name)}" aria-label="Delete ${esc(f.name)}">${icon('trash')}</button></span>
    </div>`).join('')}</div></section>`;
}

function jobList(jobs) {
  if (!jobs.length) return '';
  const chip = (j) => ({ done: j.result?.accepted ? '<span class="chip ok">Improved</span>' : '<span class="chip">Kept the original</span>',
    running: '<span class="chip violet">Learning</span>', queued: '<span class="chip violet">Starting</span>', waiting: '<span class="chip">Waiting</span>',
    paused: '<span class="chip orange">Paused</span>', failed: '<span class="chip danger">Stopped</span>', cancelled: '<span class="chip">Cancelled</span>' }[j.state] || '');
  return `<section class="train-sec"><h2>Recent training</h2><div class="card train-list">
    ${jobs.slice(0, 8).map((j) => `<a class="train-row" href="#/train/${esc(j.id)}">
      <span class="train-row-main"><b>${esc(j.name || j.model_id)}</b><span class="muted small">${esc(j.result?.message || j.message || j.text || '')}</span></span>
      ${chip(j)}${icon('caret-right')}</a>`).join('')}</div></section>`;
}

async function deleteFinetune(id, name) {
  const ok = await confirmDialog({ title: `Delete ${name}?`, body: 'The model it was trained from stays as it is. This can’t be undone.', confirm: 'Delete', danger: true });
  if (!ok) return;
  try { await api(`/api/finetunes/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast('Deleted'); homeView(); } catch (e) { toast(e.message, 'error'); }
}

// ------------------------------------------------------------------ 2. review

async function reviewView(id) {
  root.innerHTML = shell('<p class="faint">Reading your examples…</p>');
  try { ds = await api(`/api/training/datasets/${encodeURIComponent(id)}`); } catch (e) { toast(e.message, 'error'); location.hash = '#/train'; return; }
  chosen = ds.recommended?.id || null;
  renderReview();
}

function renderReview() {
  const rep = ds.report;
  const errors = rep.problems.filter((p) => p.level === 'error');
  const warns = rep.problems.filter((p) => p.level !== 'error');
  const model = ds.models.find((m) => m.id === chosen);
  const qs = Object.entries(rep.questions);
  root.innerHTML = shell(`
    <div class="page-title"><div>${errors.length || !rep.usable
      ? `<h1>This file needs a few changes</h1><p>${esc(ds.filename)} can’t be used for teaching yet. Fix what’s listed below, save the file and choose it again.</p>`
      : `<h1>Here’s what it will learn</h1><p>${rep.examples.toLocaleString()} examples from ${esc(ds.filename)}. Check that each question reads the way you would ask it.</p>`}</div>
      <div class="actions"><a class="btn btn-quiet" href="#/train">${icon('arrow-left')}Choose another file</a></div></div>
    <div class="train-review">
      <div class="train-qs">${qs.map(([qid, q], i) => questionCard(qid, q, i)).join('')}
        ${errors.map((p) => `<div class="note danger">${icon('warning-circle')}<span>${esc(p.message)}</span></div>`).join('')}
        ${warns.map((p) => `<div class="note amber">${icon('warning')}<span>${esc(p.message)}</span></div>`).join('')}</div>
      <aside class="card card-pad train-side">
        <div class="label">Model to teach</div>
        ${model ? `<div class="train-model"><b>${esc(model.name)}</b>${model.id === ds.recommended?.id ? '<span class="chip violet">Recommended</span>' : ''}
          <span class="muted small">${esc(model.tagline || '')}</span></div>`
          : `<div class="note amber">${icon('warning')}<span>${esc(noModelReason())}</span></div>`}
        <button class="btn btn-quiet sm" id="change">${icon('swap')}Change model</button>
        ${model?.eta_minutes && rep.usable && !errors.length ? `<p class="small muted">About ${model.eta_minutes} minutes on this computer. You can keep using the studio while it learns.</p>` : ''}
        <label class="label" for="ftname">Name for the new model</label>
        <input class="input" id="ftname" value="${esc(model ? `${model.name} for ${prettyFile(ds.filename)}` : '')}">
        <button class="btn btn-primary train-go" id="go" ${!model || errors.length || !rep.usable ? 'disabled' : ''}>${icon('sparkle')}Start teaching</button>
        <p class="small muted">It sets some examples aside to test itself, so you’ll see honestly whether it got better.</p>
      </aside>
    </div>`);
  $$('[data-q]', root).forEach((inp) => inp.addEventListener('change', () => saveQuestion(inp.dataset.q, inp.value)));
  $('#change', root).addEventListener('click', (e) => chooseModel(e.currentTarget));
  $('#go', root).addEventListener('click', start);
}

function questionCard(qid, q, i) {
  const counts = Object.entries(q.counts || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...counts.map(([, n]) => n));
  const kind = q.type === 'noul' ? 'Yes or no' : q.type === 'score' ? `A scale of ${q.options.length}` : q.type === 'multi' ? `Any of ${q.options.length}` : `Pick one of ${q.options.length}`;
  const shown = counts.slice(0, 8);
  return `<section class="card card-pad train-q">
    <div class="train-q-head"><span class="muted small">Question ${i + 1}</span><span class="chip">${kind}</span><span class="muted small train-q-n">${q.labelled.toLocaleString()} answered</span></div>
    <input class="input train-q-text" data-q="${esc(qid)}" value="${esc(q.instructions || '')}" aria-label="Question ${i + 1}">
    <div class="hplot train-counts">${shown.map(([name, n]) => `<div class="row row-2"><span class="lab" title="${esc(name)}">${esc(name)}</span>
      <span class="track"><span class="bar" style="transform:scaleX(${(n / max).toFixed(3)})"></span></span><span class="val">${n}</span></div>`).join('')}</div>
    ${counts.length > shown.length ? `<p class="small muted">and ${counts.length - shown.length} more answers</p>` : ''}
  </section>`;
}

async function saveQuestion(qid, text) {
  try {
    ds = await api(`/api/training/datasets/${ds.id}/questions`, { method: 'POST', body: { questions: { [qid]: { instructions: text } } } });
    toast('Question updated');
  } catch (e) { toast(e.message, 'error'); }
}

function noModelReason() {
  const r = ds.models.find((m) => m.reason)?.reason;
  return r ? `No downloaded model can learn this here yet. ${r}` : 'No downloaded model can learn this on this computer. Download one on the Models page.';
}

function chooseModel(anchor) {
  const items = ds.models.map((m) => `<button class="menu-item train-mi" data-pick="${esc(m.id)}" ${m.trainable ? '' : 'disabled'}>
    <span><b>${esc(m.name)}</b> <span class="muted">${esc(m.params)}</span>${m.id === ds.recommended?.id ? ' <span class="chip violet sm">Recommended</span>' : ''}
    <br><span class="small muted">${esc(m.trainable ? (m.eta_minutes ? `About ${m.eta_minutes} minutes` : m.tagline) : m.reason)}</span></span>
    ${m.id === chosen ? icon('check') : ''}</button>`).join('');
  popMenu(anchor, items, { onPick: (id) => { chosen = id; renderReview(); } });
}

async function start() {
  const btn = $('#go', root);
  btn.disabled = true;
  btn.innerHTML = `${icon('circle-notch')}Starting`;
  try {
    const job = await api('/api/training/jobs', { method: 'POST', body: { dataset_id: ds.id, model_id: chosen, name: $('#ftname', root).value.trim() } });
    location.hash = `#/train/${job.id}`;
  } catch (e) {
    toast(e.message, 'error');
    btn.disabled = false;
    btn.innerHTML = `${icon('sparkle')}Start teaching`;
  }
}

// ------------------------------------------------------------------ 3. progress and result

async function jobView(id) {
  root.innerHTML = shell('<p class="faint">Loading…</p>');
  const tick = async () => {
    let j;
    try { j = await api(`/api/training/jobs/${encodeURIComponent(id)}`); } catch (e) { clearInterval(timer); root.innerHTML = shell(`<div class="note danger">${icon('warning-circle')}<span>${esc(e.message)}</span></div>`); return; }
    renderJob(j);
    if (!['running', 'queued', 'waiting'].includes(j.state)) clearInterval(timer);
  };
  await tick();
  clearInterval(timer);
  timer = setInterval(tick, 1500);
}

function renderJob(j) {
  const live = ['running', 'queued', 'waiting'].includes(j.state);
  const model = store.state?.models.find((m) => m.id === j.model_id);
  const name = esc(model?.name || j.model_id);
  const title = live ? `Teaching ${name}` : j.result?.accepted ? `${name} got better` : j.state === 'done' ? `Finished teaching ${name}`
    : j.state === 'paused' ? `Teaching ${name}, paused` : `Teaching ${name}`;
  const head = `<div class="page-title"><div><h1>${title}</h1>
    <p>${esc(j.name || '')}</p></div><div class="actions"><a class="btn btn-quiet" href="#/train">${icon('arrow-left')}All training</a></div></div>`;
  let body;
  if (live) body = progressCard(j);
  else if (j.state === 'done' && j.result?.accepted) body = resultCard(j, model);
  else if (j.state === 'done') body = keptCard(j, model);
  else if (j.state === 'paused') body = `<div class="note amber">${icon('pause')}<div><b>Paused</b><p>${esc(j.message || '')}</p>
      <p class="small">It keeps what it has learned so far and continues from there.</p>
      <button class="btn btn-primary sm" data-act="resume">${icon('play')}Continue</button> <button class="btn btn-quiet sm" data-act="delete">Discard</button></div></div>`;
  else if (j.state === 'cancelled') body = `<div class="note">${icon('x-circle')}<div><b>Cancelled</b><p>Nothing was changed.</p>
      <a class="btn btn-quiet sm" href="#/train">Start over</a></div></div>`;
  else body = `<div class="note danger">${icon('warning-circle')}<div><b>Training stopped</b><p>${esc(j.message || 'Something went wrong.')}</p>
      <p class="small">Nothing was changed. The job folder has a log if you need details.</p>
      <button class="btn btn-quiet sm" data-act="resume">Try again</button></div></div>`;
  const html = shell(head + body);
  if (root.dataset.html === html) return;
  root.dataset.html = html;
  const scroll = $('.view', root)?.scrollTop || 0;
  root.innerHTML = html;
  $('.view', root).scrollTop = scroll;
  $$('[data-act]', root).forEach((b) => b.addEventListener('click', () => jobAction(j, b.dataset.act)));
  $('[data-use]', root)?.addEventListener('click', (e) => useIt(e.currentTarget.dataset.use, j.result));
  $('[data-compare]', root)?.addEventListener('click', () => compare(j));
  if (j.state === 'done' && !j.result?.accepted) offerAnother(j);
}

function progressCard(j) {
  const p = Math.max(0.02, Math.min(1, j.progress || 0));
  const eta = j.eta_seconds ? `about ${fmtDuration(j.eta_seconds)} left` : '';
  const waiting = j.state === 'waiting' || j.state === 'queued';
  return `<section class="card card-pad train-progress">
    <div class="train-stage">${icon(waiting ? 'hourglass-medium' : 'sparkle')}<b>${esc(j.text || (waiting ? 'Waiting to start' : 'Working'))}</b></div>
    <span class="progress train-bar"><i style="width:${(p * 100).toFixed(1)}%"></i></span>
    <div class="small muted train-meta"><span>${Math.round(p * 100)}%</span><span>${esc(eta)}</span></div>
    ${curve(j)}
    ${(j.notes || []).slice(-2).map((n) => `<p class="small muted">${esc(n)}</p>`).join('')}
    <div class="train-acts">${waiting ? '' : `<button class="btn btn-quiet sm" data-act="pause">${icon('pause')}Pause</button>`}
      <button class="btn btn-quiet sm" data-act="cancel">${icon('x')}Cancel</button>
      <span class="small muted">${waiting ? 'It starts by itself when the other training is done.' : 'You can leave this page; it keeps learning in the background.'}</span></div>
  </section>`;
}

function curve(j) {
  const pts = j.curve || [];
  if (j.baseline_accuracy == null || pts.length < 1) return '';
  const all = [{ step: 0, accuracy: j.baseline_accuracy }, ...pts];
  const W = 520, H = 120, maxS = Math.max(1, j.total_steps || all[all.length - 1].step);
  const ys = all.map((p) => p.accuracy);
  const lo = Math.max(0, Math.min(...ys) - 0.1), hi = Math.min(1, Math.max(...ys) + 0.1);
  const x = (s) => 8 + (s / maxS) * (W - 16), y = (a) => H - 18 - ((a - lo) / Math.max(0.01, hi - lo)) * (H - 36);
  const path = all.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.accuracy).toFixed(1)}`).join(' ');
  const last = all[all.length - 1];
  return `<figure class="train-curve"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Practice score from ${pct(j.baseline_accuracy)} to ${pct(last.accuracy)}">
    <line x1="8" x2="${W - 8}" y1="${y(j.baseline_accuracy).toFixed(1)}" y2="${y(j.baseline_accuracy).toFixed(1)}" class="base"/>
    <path d="${path}" class="line"/><circle cx="${x(last.step).toFixed(1)}" cy="${y(last.accuracy).toFixed(1)}" r="3.5" class="dot"/>
    <text x="${W - 8}" y="${(y(j.baseline_accuracy) - 5).toFixed(1)}" text-anchor="end" class="lbl">before: ${pct(j.baseline_accuracy)}</text></svg>
    <figcaption class="small muted">Practice score on examples it isn’t learning from: ${pct(last.accuracy)} now, ${pct(j.baseline_accuracy)} before it started.</figcaption></figure>`;
}

function resultCard(j, model) {
  const r = j.result;
  const bars = [['Before training', r.accuracy_before, false], ['After training', r.accuracy_after, true]];
  const guard = r.guard_before != null && r.guard_after != null
    ? (r.guard_after < r.guard_before - 0.005
      ? `<p>On general decisions it already knew it is slightly lower, ${pct(r.guard_after)} (${pct(r.guard_before)} before): a small cost next to the gain on your examples. The original model is still there for anything else.</p>`
      : `<p>On general decisions it already knew, it scores ${pct(r.guard_after)} (${pct(r.guard_before)} before), so nothing was forgotten.</p>`) : '';
  return `<section class="card card-pad train-result">
    <div class="train-big"><span class="num">${pct(r.accuracy_after)}</span><span>correct on ${r.test_questions ? `${r.test_questions} ` : ''}questions it had never seen, up from ${pct(r.accuracy_before)}</span></div>
    <div class="hplot train-ba">${bars.map(([lab, v, win]) => `<div class="row ${win ? 'row-win' : ''}"><span class="lab">${lab}</span>
      <span class="track"><span class="bar" style="transform:scaleX(${(v || 0).toFixed(3)})"></span></span><span class="val">${pct(v)}</span></div>`).join('')}</div>
    ${guard}
    ${showcase(r.showcase)}
    <p class="small muted">It learned from ${(r.data?.train || 0).toLocaleString()} answered questions and was tested on ${(r.data?.test || 0).toLocaleString()} it never saw${r.seconds ? `, in ${fmtDuration(r.seconds)}` : ''}. Its confidence was adjusted to match how often it is right.</p>
    <div class="train-acts">${r.finetune_id ? `<button class="btn btn-primary" data-use="${esc(r.finetune_id)}">${icon('play')}Use it now</button>` : ''}
      <button class="btn btn-quiet" data-compare>${icon('list-checks')}Compare on Evaluate</button></div>
  </section>`;
}

// One held-out example the original model got wrong and the new one gets right: the improvement, made concrete.
function showcase(sc) {
  if (!sc || !sc.questions?.length) return '';
  const fixed = sc.fixed > 0;
  return `<figure class="train-case"><figcaption class="small muted">${fixed ? 'One it used to get wrong' : 'One of the examples it never saw'}</figcaption>
    <blockquote>${esc(sc.text)}</blockquote>
    <table class="table small"><thead><tr><th>Question</th><th>Before</th><th>Now</th></tr></thead><tbody>
    ${sc.questions.map((q) => `<tr><td>${esc(q.question)}</td>
      <td>${q.before == null ? '' : `<span class="train-ans ${q.before === q.right ? 'ok' : 'bad'}">${icon(q.before === q.right ? 'check' : 'x')}${esc(q.before)}</span>`}</td>
      <td><span class="train-ans ok">${icon('check')}${esc(q.after)}</span></td></tr>`).join('')}</tbody></table></figure>`;
}

// Evaluate, prepared: the held-out examples (never trained on) and both models, the original and the new one.
function compare(j) {
  const ev = j.result?.evaluate;
  if (ev) {
    try {
      localStorage.setItem('bud.eval.v2', JSON.stringify({ question: fromQuestion(ev.question_id, ev.question), dataText: ev.text }));
      sessionStorage.setItem('bud.eval.select', JSON.stringify([j.model_id, j.result.finetune_id].filter(Boolean)));
    } catch { /* storage unavailable: Evaluate opens as it was */ }
  }
  location.hash = '#/evaluate';
}

async function useIt(id, r) {
  if (r?.showcase?.request) {
    try { await loadDecision({ input: r.showcase.request, model: id }); } catch { /* opens with the model only */ }
    location.hash = '#/playground';
    return;
  }
  location.hash = `#/playground?model=${encodeURIComponent(id)}`;
}

function keptCard(j, model) {
  const r = j.result || {};
  return `<section class="card card-pad train-result">
    <div class="note">${icon('info')}<div><b>${esc(model?.name || 'The model')} is unchanged</b><p>${esc(r.message || '')}</p></div></div>
    <p class="small muted">Nothing is lost: the new version was simply not kept.${r.outcome === 'not_improved' && r.accuracy_before < 0.85 ? ' Things that usually help:' : ''}</p>
    ${r.outcome === 'not_improved' && r.accuracy_before < 0.85 ? `<ul class="small train-tips"><li>More examples: a few hundred of each answer is ideal.</li>
      <li>Answers that are easier to tell apart, or a clearer question.</li>
      <li>Another model: some learn certain tasks more easily.</li></ul>` : ''}
    <div class="train-acts" id="alt-acts"><a class="btn btn-quiet" href="#/train">Start over</a></div>
  </section>`;
}

// After a result that was not kept: offer the next model that can learn the same examples, in one click.
async function offerAnother(j) {
  const box = $('#alt-acts', root);
  if (!box || !j.dataset_id || box.dataset.done) return;
  box.dataset.done = '1';
  let ds2;
  try { ds2 = await api(`/api/training/datasets/${encodeURIComponent(j.dataset_id)}`); } catch { return; }
  const tried = new Set([j.model_id]);
  const alt = (ds2.models || []).find((m) => m.trainable && !tried.has(m.id));
  if (!alt || !$('#alt-acts', root)) return;
  const was = baseName(j.model_id);
  const name = (j.name || '').startsWith(was) ? alt.name + j.name.slice(was.length) : `${alt.name} for ${prettyFile(ds2.filename)}`;
  box.insertAdjacentHTML('afterbegin', `<button class="btn btn-primary" id="alt-go">${icon('sparkle')}Teach ${esc(alt.name)} instead</button>
    <span class="small muted">${alt.eta_minutes ? `About ${alt.eta_minutes} minutes. ` : ''}Same examples; some models learn some tasks more easily.</span>`);
  $('#alt-go', box).addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      const job = await api('/api/training/jobs', { method: 'POST', body: { dataset_id: j.dataset_id, model_id: alt.id, name } });
      location.hash = `#/train/${job.id}`;
    } catch (err) { toast(err.message, 'error'); e.currentTarget.disabled = false; }
  });
}

async function jobAction(j, act) {
  if (act === 'cancel' && !(await confirmDialog({ title: 'Stop training?', body: 'Nothing will be changed. You can start again later.', confirm: 'Stop', danger: true }))) return;
  try {
    await api(`/api/training/jobs/${encodeURIComponent(j.id)}/${act}`, { method: 'POST' });
    if (act === 'delete') { location.hash = '#/train'; return; }
    delete root.dataset.html;
    jobView(j.id);
  } catch (e) { toast(e.message, 'error'); }
}

// ------------------------------------------------------------------ helpers

function baseName(id) { return store.state?.models.find((m) => m.id === id)?.name || caps?.models?.find((m) => m.id === id)?.name || id; }
function dateOf(s) { try { return new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); } catch { return ''; } }
function prettyFile(name) { return String(name || 'your examples').replace(/\.[a-z0-9]+$/i, '').replace(/[_-]+/g, ' '); }
