// Learn: what a decision model is, why you would use one, and how to read its answers.

import { EXAMPLES } from '../examples.js';
import { animate, figures } from '../figures.js';
import { GLOSSARY } from '../glossary.js';
import { decide, readyModels, store } from '../store.js';
import { setSub } from '../shell.js';
import { $, esc, fmtMs, fmtPct, icon, term } from '../util.js';

// Shown until a model is loaded; replaced by a live run as soon as one is.
// Real answers recorded from Intern-Decision 4B on an NVIDIA GB10.
const RECORDED = {
  model: 'Intern-Decision 4B', latency_ms: 106.0,
  answers: {
    department: { type: 'choice', probabilities: { billing: 0.9826, technical: 0.0114, sales: 0.0025, other: 0.0035 }, decision: 'billing', top_probability: 0.9826, choice: 'billing', confidence: 0.9768 },
    urgency: { type: 'score', probabilities: { 0: 0.0035, 1: 0.0069, 2: 0.81, 3: 0.1797 }, decision: '2', top_probability: 0.81, score: 2.1659, legend: { 0: 'can wait', 1: 'soon', 2: 'today', 3: 'blocking or at risk of churn' }, confidence: 0.8065 },
    churn_risk: { type: 'noul', probabilities: { false: 0.0066, true: 0.9934 }, decision: 'yes', top_probability: 0.9934, noul: 0.9934 },
  },
};
const EX = EXAMPLES.find((e) => e.id === 'support');
const TYPES = [
  ['choice', 'Pick one', 'One option from a list you write: teams, intents, categories, next actions.', '"Which department should handle this?" billing, technical, sales', 'email'],
  ['score', 'Rate on a scale', 'A position on an ordered scale of 2 to 10 levels, plus an average that can land between levels.', '"How urgent is it?" from can wait to blocking', 'review'],
  ['noul', 'Yes or no', 'The probability that a statement is true. TypeSafe calls this type "noul".', '"Does the customer threaten to cancel?"', 'injection'],
  ['multi', 'Pick any', 'Every option that applies, each judged on its own against a cut-off you choose.', '"Which topics does the message raise?"', 'tags'],
  ['rank', 'Put in order', 'All options ordered from most to least likely.', '"Which incident should we fix first?"', 'backlog'],
  ['number', 'Estimate a number', 'A quantity with a best estimate and an 80% range, from values you list.', '"How many seats will they buy?"', 'estimate'],
];

let root, live = null, liveFor = null, running = false;

export function mount(el) {
  root = el;
  setSub('Decision models in ten minutes');
  root.innerHTML = `<div class="view scroll"><div class="learn-view"><div class="l-main">
    <section class="l-hero">
      <div>
        <h1>Models that decide instead of write.</h1>
        <p class="lede">A ${term('decision_model', 'decision model')} reads a situation and returns the probability of every answer you allow. No text to parse, nothing made up, and it answers in milliseconds on this computer's GPU (in about a second on the processor).</p>
        <div class="cta"><a class="btn btn-primary lg" href="#/playground">${icon('flask')}Try it now</a><a class="btn lg" href="#/models">${icon('squares-four')}Choose a model</a></div>
        <ol class="read-legend" aria-label="How to read a figure">
          <li><span><b>The answer</b> in large type, with how likely the model thinks it is.</span></li>
          <li><span><b>Every option</b> on the same 0 to 100% scale, so you can see what came second and by how much.</span></li>
          <li><span><b>The caption</b> says it in words, and whether to act automatically or ask a person.</span></li>
        </ol>
      </div>
    </section>

    <section class="l-sec">
      <header><h2>How is this different from a chatbot?</h2><p>Same kind of neural network, a very different job.</p></header>
      <div class="prose">
        <p>Most AI calls inside software are not conversations. They are small judgement calls: which team gets this ticket, is this comment allowed, did the agent finish its task. Asking a chat model means paying for paragraphs, then parsing them back into a value and hoping the format holds.</p>
        <div class="vs">
          <div><h3>Chat model (LLM)</h3>
            <pre>"Based on the message, this seems to be a billing issue, most likely a duplicate charge. I'd suggest routing it to..."</pre>
            <ul><li>Writes free text, one word at a time</li><li>You parse it and hope the format holds</li><li>Can answer something you did not offer</li><li>Seconds per answer; pays for every output word</li></ul></div>
          <div><h3>Decision model</h3>
            <pre>billing 97%   technical 1%   sales 1%   other 1%</pre>
            <ul><li>A probability for each option you define</li><li>Cannot answer outside your options</li><li>Every question answered in ${term('one_pass', 'one pass')}</li><li>Milliseconds; zero output tokens</li></ul></div>
        </div>
        <p>The category was popularised by ${term('jev', 'Jev')} in September 2026 as ${term('system_one', 'System One')} models. Every model in this studio is an open alternative that runs on your own hardware.</p>
      </div>
    </section>

    <section class="l-sec">
      <header><h2>Six kinds of question</h2><p>Everything you ask is one of these. The first three are TypeSafe's; the studio builds the other three from them.</p></header>
      <div class="types6">${TYPES.map(([t, n, d, ex, id]) => `<div><h3>${icon(t)}${n}</h3><p>${d}</p><p class="ex">${esc(ex)}</p><a href="#/playground?example=${id}" class="small">Open an example</a></div>`).join('')}</div>
    </section>

    <section class="l-sec">
      <header><h2>Reading the numbers</h2><p>The probabilities are the product. This is how to use them.</p></header>
      <div class="prose">
        <p><strong>Probability</strong> is how much of the model's belief goes to each answer; the options of a pick-one question always add up to 100%. The highest one is the model's answer.</p>
        <p><strong>${term('calibration', 'Calibration')}</strong> is whether you can take those numbers at face value: of all the times a well-calibrated model says 80%, it is right about 80% of the time. Most models ship a little overconfident, so check them on a few dozen of your own labelled examples before trusting a threshold. <a href="#/evaluate">Evaluate</a> measures this and fits a ${term('temperature', 'calibration temperature')} that corrects it.</p>
        <p>The standard way to use a decision model is <strong>${term('gating', 'confidence gating')}</strong>: act automatically when the top answer is likely enough, and send the rest to a person or a bigger model. Move the threshold to see the trade-off.</p>
        <div class="gate-demo" id="gatedemo"></div>
      </div>
    </section>

    <section class="l-sec">
      <header><h2>Where they shine, and where they do not</h2><p>Knowing the limits is most of the skill.</p></header>
      <div class="vs">
        <div><h3>Good at</h3><ul>
          <li>Routing and triage: tickets, emails, alerts</li><li>Moderation, safety and prompt-injection screening</li>
          <li>Checking another AI's answer against a source</li><li>Choosing an agent's next tool or action</li><li>Tagging and scoring large volumes cheaply</li></ul></div>
        <div><h3>Not good at</h3><ul>
          <li>Arithmetic, counting and date maths (do those in code)</li><li>Facts that are not in the text you give it</li>
          <li>Multi-step reasoning: one pass cannot carry intermediate results</li><li>Explaining why: there is no reasoning to show</li><li>Reading probabilities stated in the text, like dice odds</li></ul></div>
      </div>
    </section>

    <section class="l-sec">
      <header><h2>What people build with them</h2><p>Each opens a ready-made example in the Playground.</p></header>
      <div class="uses">${EXAMPLES.map((e) => `<a class="use" href="#/playground?example=${e.id}"><b>${esc(e.title)}</b><span>${esc(e.blurb)}</span></a>`).join('')}</div>
    </section>

    <section class="l-sec">
      <header><h2>Your path</h2><p>Ticks itself off as you go.</p></header>
      <ol class="steps" id="path"></ol>
    </section>

    <section class="l-sec">
      <header><h2>Words you will see</h2><p>Hover any dotted word in the studio for the same definitions.</p></header>
      <dl class="glossary">${Object.values(GLOSSARY).map((g) => `<div><dt>${esc(g.term)}</dt><dd>${esc(g.def)}</dd></div>`).join('')}</dl>
    </section>
  </div><aside class="l-demo" id="demo" aria-label="A live decision"></aside></div></div>`;
  renderDemo();
  renderGate(store.prefs.threshold);
  renderPath();
  maybeRunLive();
}

export function onState() {
  if (!root?.isConnected) return;
  renderPath();
  maybeRunLive();
}

async function maybeRunLive() {
  const ready = readyModels();
  const pick = ready.find((m) => m.id === 'intern-decision-4b') || ready[0];
  if (!pick || running || liveFor === pick.id) return;
  running = true;
  try {
    const { data } = await decide({ model: pick.id, state: EX.state, questions: EX.questions });
    live = { ...data, modelName: pick.name };
    liveFor = pick.id;
    renderDemo();
  } catch { /* keep the recorded example */ }
  running = false;
}

function renderDemo() {
  const box = $('#demo', root);
  if (!box) return;
  const r = live || RECORDED;
  const caption = live
    ? `Live from ${esc(live.modelName)} on this computer: ${fmtMs(live.latency_ms)} for all three answers.`
    : `Recorded from ${esc(RECORDED.model)} on an NVIDIA GB10: ${fmtMs(RECORDED.latency_ms)} for all three. Load a model and this runs live.`;
  box.innerHTML = `<div class="card card-pad" style="display:grid;gap:8px"><span class="label">The situation</span><p class="muted">${esc(EX.state)}</p></div>
    ${figures(r, { questions: EX.questions }, { run: 1, threshold: store.prefs.threshold })}
    <p class="help">${caption}</p>`;
  const g = $('.figs', box); if (g) g.style.gridTemplateColumns = 'minmax(0, 1fr)';
  animate(box);
}

const GATE_ITEMS = [
  ['Refund request, clearly billing', 0.97], ['Password reset email', 0.93], ['Angry review, mixed topics', 0.71],
  ['Bug report in Spanish', 0.88], ['Vague "it does not work" message', 0.46], ['Spam with a real question inside', 0.62],
];
function renderGate(th) {
  const box = $('#gatedemo', root);
  const acted = GATE_ITEMS.filter(([, p]) => p >= th).length;
  box.innerHTML = `<label class="field"><span class="label">${term('threshold', 'Act threshold')}: <b class="num">${fmtPct(th)}</b></span>
      <input type="range" min="0.4" max="0.99" step="0.01" value="${th}" id="gth" aria-label="Act threshold"></label>
    <div style="display:grid;gap:4px">${GATE_ITEMS.map(([t, p]) => `<div class="gate-row"><span>${esc(t)}</span><span class="num muted">${fmtPct(p)}</span>
      <span class="gate ${p >= th ? 'act' : 'ask'}" style="display:inline-flex;gap:6px;align-items:center;font-weight:600">${icon(p >= th ? 'act' : 'ask')}${p >= th ? 'Act automatically' : 'Ask a human'}</span></div>`).join('')}</div>
    <p class="small muted"><b style="color:var(--text)">${acted} of ${GATE_ITEMS.length}</b> handled automatically. A higher threshold means fewer mistakes but more work for people; this trade-off is called a ${term('cascade', 'cascade')}.</p>`;
  const s = $('#gth', box);
  s.addEventListener('input', () => { renderGate(+s.value); $('#gth', root).focus(); });
}

function renderPath() {
  const box = $('#path', root);
  if (!box) return;
  const st = store.state;
  const L = store.prefs.learned || {};
  const steps = [
    [st?.models.some((m) => m.downloaded), 'Download a model', 'Laya is the most popular (850 MB). Intern-Decision 4B is the best all-rounder.', '#/models', 'Models'],
    [st?.models.some((m) => m.worker?.status === 'ready'), 'Load it into memory', 'Seconds for small models, a minute or two for the largest.', '#/models', 'Models'],
    [L.ran, 'Run your first decision', 'Start from an example, then change the text and watch the probabilities move.', '#/playground', 'Playground'],
    [L.evaluated, 'Measure it on your own examples', 'Score a model on labelled examples, fix overconfidence and choose a safe threshold.', '#/evaluate', 'Evaluate'],
    [L.code, 'Call it from your own code', 'Copy a ready-made snippet for the exact request you built.', '#/api', 'API'],
  ];
  box.innerHTML = steps.map(([done, t, d, href, cta], i) => `<li class="${done ? 'done' : ''}"><span class="n">${done ? icon('check') : i + 1}</span><div><b>${t}</b><span>${d}</span></div><a class="btn sm ${done ? 'btn-quiet' : ''}" href="${href}">${cta}</a></li>`).join('');
}
