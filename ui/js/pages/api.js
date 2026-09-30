// API: a developer page after LM Studio's. The server, the endpoints for each published format and for the studio API
// (templates and history), one quick-start example, and the reference folded into rows. History has its own page.

import { EXAMPLES } from '../examples.js';
import { codeBlock, jsonTree } from '../format.js';
import { setSub } from '../shell.js';
import { FORMATS, LANGS, base, snippet, studioSnippet } from '../snippets.js';
import { markLearned, readyModels } from '../store.js';
import { $, $$, copy, esc, icon, term } from '../util.js';

let root, lang = 'python', format = 'studio';

const ENDPOINTS = {
  typesafe: [['POST', '/v1/systemone', 'Make a decision, in exactly TypeSafe\'s shape. The official TypeSafe SDKs call this.', true], ['GET', '/v1/models', 'The models you can name in "model".']],
  openrouter: [['POST', '/api/alpha/decisions', 'OpenRouter\'s Decisions API. Adds id, provider and usage.cost.', true], ['POST', '/api/v1/systemone', 'OpenRouter\'s System One route, the same shape.', true]],
  vercel: [['POST', '/typesafe/v1/systemone', 'Vercel AI Gateway\'s TypeSafe route. Adds provider_metadata.', true], ['GET', '/typesafe/v1/models', 'The model list on the Vercel route.'], ['POST', '/v1/evaluate', 'Vercel\'s evaluation API, with boolean questions and camelCase usage.', true]],
  studio: [['POST', '/v1/studio/decisions', 'Make a decision, with a template ("template": "support-triage", "variables") or without ("state", "questions"). Returns the decision: its id, answers, and whether to act or ask a human.', true],
    ['GET', '/v1/studio/decisions', 'History: filter by template, version, model, answer, time, feedback; paginate with after.'],
    ['GET', '/v1/studio/decisions/{id}', 'One decision: its input, answers, settings and where each setting came from.'],
    ['POST', '/v1/studio/decisions/{id}/feedback', 'Label the right answers. Labels give accuracy per template version.'],
    ['PUT', '/v1/studio/templates/{id}', 'Create or update a template. Every change is a new version.'],
    ['GET', '/v1/studio/templates/{id}/compare', 'Compare two versions on real traffic, including the same inputs.'],
    ['GET', '/v1/studio/templates/{id}/stats', 'Act rate, answers, accuracy and latency, by version.'],
    ['POST', '/v1/studio/templates/{id}/examples', 'Test examples: inputs with the right answers.'],
    ['GET, PATCH', '/v1/studio/settings', 'What History keeps, and for how long.']],
};
const STUDIO_NOTE = 'The studio\'s own API: decisions that are kept, with reusable templates and a history you can search, label and compare. Beside it, the TypeSafe, OpenRouter and Vercel formats work unchanged. Full reference: docs/studio-api.md, and the interactive reference.';
const STRICT = {
  model: 'laya',
  answers: {
    team: { type: 'choice', choice: 'billing', probabilities: { billing: 0.8952, technical: 0.0785, sales: 0.0263 }, confidence: 0.8428 },
    urgency: { type: 'score', score: 1.4468, legend: { 0: 'low', 1: 'medium', 2: 'high' }, probabilities: { 0: 0.0551, 1: 0.443, 2: 0.5019 }, confidence: 0.1702 },
    refund: { type: 'noul', noul: 0.9486 },
  },
  usage: { input_tokens: 456, output_tokens: 0 },
};
const EXTENDED = {
  topics: { type: 'multi', selected: ['billing', 'crash'], probabilities: { billing: 0.9495, crash: 0.9953, shipping: 0.0677 }, threshold: 0.5 },
  first: { type: 'rank', ranking: ['double charge', 'login crash', 'ui colour'], probabilities: { 'double charge': 0.8964, 'login crash': 0.094, 'ui colour': 0.0096 } },
  days_left: { type: 'number', estimate: 1.08, most_likely: 0, range: [0, 3], unit: 'days', probabilities: { 0: 0.6731, 1: 0.2108, 3: 0.0717, 7: 0.0295, 30: 0.0149 } },
};

export function mount(el) {
  root = el;
  setSub('The studio API, plus the TypeSafe, OpenRouter and Vercel formats');
  root.innerHTML = `<div class="view scroll"><div class="pad" style="display:grid;gap:18px">
    <div class="group">
      <div class="grow"><span class="chip ok">${icon('check-circle')}Running</span><span class="k" style="display:flex;gap:8px;align-items:center">Base URL <code>${esc(base())}</code><button class="icon-btn" id="cpbase" aria-label="Copy base URL" data-tip="Copy">${icon('copy')}</button></span>
        <span class="v"><a class="btn sm" href="/docs" target="_blank" rel="noopener">${icon('book-open')}Interactive reference</a></span></div>
      <div class="grow"><span class="k">Authentication</span><span class="v">None on this machine. Set <code>BASAL_API_KEY</code> to require a key from other machines.</span></div>
      <div class="grow"><span class="k">Model names</span><span class="v">A studio id such as <code>laya</code>, or <code>jev-latest</code> for the most recently loaded model</span></div>
      <div class="grow"><span class="k">Ready now</span><span class="v" id="ready"></span></div>
      <div class="grow"><span class="k">History</span><span class="v">Every decision, from any endpoint, is saved to <a href="#/history">History</a>. Send <code>"store": false</code> to keep nothing for a call.</span></div>
    </div>

    <div class="two" style="grid-template-columns:minmax(0,1.15fr) minmax(0,0.85fr)">
      <div style="display:grid;gap:18px">
        <section class="card card-pad" style="display:grid;gap:12px">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><h2 style="font-size:15px">Endpoints</h2>
            <span class="seg" role="group" aria-label="API format" style="margin-left:auto"><button data-fmt="studio" aria-pressed="${format === 'studio'}">Studio API</button>${Object.entries(FORMATS).map(([k, f]) => `<button data-fmt="${k}" aria-pressed="${k === format}">${esc(f.name.replace(' Jev API', '').replace(' Decisions API', '').replace(' AI Gateway', ''))}</button>`).join('')}</span></div>
          <p class="help" id="fmtnote"></p>
          <div id="eps" style="display:grid;gap:12px"></div>
        </section>
        <section class="card card-pad" style="display:grid;gap:12px">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><h2 style="font-size:15px">Quick start</h2>
            <span class="seg" role="group" aria-label="Language" style="margin-left:auto">${Object.entries(LANGS).map(([k, v]) => `<button data-lang="${k}" aria-pressed="${k === lang}">${esc(v.name)}</button>`).join('')}</span></div>
          <div id="snip"></div>
          <p class="help" id="sniphelp"></p>
        </section>
      </div>
      <section class="card card-pad" style="display:grid;gap:4px;align-content:start">
        <h2 style="font-size:15px;margin-bottom:8px">Reference</h2>
        <details class="disclose"><summary>Response format${icon('caret-right', 'chev')}</summary><div class="body">
          <p class="help">Pick one, scale and yes or no answers carry exactly TypeSafe's fields. The request id travels in the <code>x-typesafe-request-id</code> header.</p>
          ${jsonTree(STRICT, { file: '200 OK', openDepth: 3 })}</div></details>
        <details class="disclose"><summary>Studio extensions${icon('caret-right', 'chev')}</summary><div class="body">
          <p class="help">Three more question types, <code>multi</code>, <code>rank</code> and <code>number</code>, plus <code>media</code> for images, audio and video, and <code>settings.temperature</code> for a ${term('temperature', 'calibration temperature')}. Send <code>X-Basal-Extensions: 1</code> to also receive <code>decision</code>, <code>top_probability</code> and <code>latency_ms</code>. Official SDKs skip what they do not know.</p>
          ${jsonTree({ answers: EXTENDED }, { file: 'Extension answers', openDepth: 2 })}</div></details>
        <details class="disclose"><summary>History and opting out${icon('caret-right', 'chev')}</summary><div class="body">
          <p class="help">Every decision is saved to History on this computer, from any endpoint, for 30 days by default: the situation, the questions, the answers, the model and the timing. Responses say what was kept in <code>x-basal-stored</code> and give the id in <code>x-basal-decision-id</code>. To keep less:</p>
          <div class="group">
            <div class="grow stack"><span class="k"><code>"store": false</code> in the body</span><span class="v small">Nothing is saved for this call. <code>"answers_only"</code> keeps the answers but not the situation. TypeSafe's own servers ignore the field, so the same code runs against both.</span></div>
            <div class="grow stack"><span class="k"><code>X-Basal-Store: 0</code> header</span><span class="v small">The same, for clients that cannot change the body. The more private of the two wins.</span></div>
            <div class="grow stack"><span class="k">A template's <b>storage</b></span><span class="v small">A ceiling for every decision made with it.</span></div>
            <div class="grow stack"><span class="k">History settings</span><span class="v small">What the studio keeps, and for how long, on the History page or <code>PATCH /v1/studio/settings</code>.</span></div>
          </div>
          <p class="help">Variables marked <code>sensitive</code> are used for the decision but never written to disk. Send <code>Idempotency-Key</code> to make retries safe.</p></div></details>
        <details class="disclose"><summary>Errors${icon('caret-right', 'chev')}</summary><div class="body"><div class="group">
          <div class="grow stack"><span class="k"><b>422</b> The request is invalid</span><span class="v small"><code>{"detail": [{"type", "loc", "msg", "input"}]}</code>. OpenRouter and Vercel formats answer 400 in their own shape.</span></div>
          <div class="grow stack"><span class="k"><b>403</b> No API key, when one is required</span><span class="v small"><code>{"detail": {"error_type": "authentication_error", "message"}}</code></span></div>
          <div class="grow stack"><span class="k"><b>401</b> Wrong API key</span><span class="v small">The same shape as 403.</span></div>
          <div class="grow stack"><span class="k"><b>404</b> Unknown model or path</span><span class="v small"><code>{"detail": "Not Found"}</code> or a message naming the model.</span></div>
          <div class="grow stack"><span class="k"><b>503, 504</b> The model is loading or timed out</span><span class="v small">Retry after a moment.</span></div></div></div></details>
        <details class="disclose"><summary>Using it from other machines${icon('caret-right', 'chev')}</summary><div class="body">
          <p class="help">The studio listens on this machine only. Start it with <code>./run.sh --host 0.0.0.0</code> and set <code>BASAL_API_KEY</code>; clients then send <code>Authorization: Bearer &lt;key&gt;</code>. Management calls from web pages need the header <code>X-Basal-Client: 1</code>.</p></div></details>
        <details class="disclose"><summary>OpenAI Decisions API${icon('caret-right', 'chev')}</summary><div class="body">
          <p class="help">OpenAI announced a Decisions API on 29 September 2026 as a limited preview, without a public endpoint, schema or SDK. The studio does not guess at it; it will be added when the specification is published. OpenRouter's Decisions API, above, is the closest published format.</p></div></details>
      </section>
    </div>
  </div></div>`;
  $('#cpbase', root).addEventListener('click', () => copy(base(), 'Base URL copied'));
  $$('[data-fmt]', root).forEach((b) => b.addEventListener('click', () => { format = b.dataset.fmt; $$('[data-fmt]', root).forEach((x) => x.setAttribute('aria-pressed', x === b)); renderEndpoints(); renderSnippet(); }));
  $$('[data-lang]', root).forEach((b) => b.addEventListener('click', () => { lang = b.dataset.lang; $$('[data-lang]', root).forEach((x) => x.setAttribute('aria-pressed', x === b)); markLearned('code'); renderSnippet(); }));
  renderReady(); renderEndpoints(); renderSnippet();
}
export function onState() { if (root?.isConnected) renderReady(); }

function renderReady() {
  const box = $('#ready', root);
  if (!box) return;
  const r = readyModels();
  const html = r.length ? r.map((m) => `<code>${esc(m.id)}</code>`).join(' ') : 'No model loaded. A request that names a downloaded model loads it first.';
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

function renderEndpoints() {
  $('#fmtnote', root).textContent = format === 'studio' ? STUDIO_NOTE : FORMATS[format].note;
  $('#eps', root).innerHTML = ENDPOINTS[format].map(([m, p, d, saved]) => `<div class="ep"><span class="m ${['POST', 'PUT'].includes(m) ? 'post' : ''}">${m}</span><code>${esc(p)}</code>${saved ? `<a class="chip violet ep-saved" href="#/history" data-tip="Each call is saved to History; the x-basal-decision-id response header names it">${icon('clock-counter-clockwise')}Saved to History</a>` : ''}<span class="d">${esc(d)}</span></div>`).join('');
}

function renderSnippet() {
  const ex = EXAMPLES.find((e) => e.id === 'support');
  const req = { model: readyModels()[0]?.id || 'laya', state: ex.state, questions: ex.questions };
  const L = LANGS[lang];
  $('#sniphelp', root).innerHTML = format === 'studio' && !L.sdk
    ? 'The support ticket through the starter template <code>builtin/support</code>. The response is the saved decision: open it in <a href="#/history">History</a> by its id. Use the Playground\'s Code tab for your own decisions.'
    : `The official TypeSafe SDKs speak the TypeSafe format${format === 'studio' ? ', so this example uses <code>/v1/systemone</code>' : ''}; the call is saved to <a href="#/history">History</a> all the same. Use the Playground's Code tab for your own decisions.`;
  if (format === 'studio' && !L.sdk) {
    const l = lang.startsWith('js') || lang === 'javascript' ? 'javascript' : lang === 'curl' ? 'curl' : 'python';
    $('#snip', root).innerHTML = codeBlock(studioSnippet(l, { template: 'builtin/support', model: req.model, state: ex.state }), { lang: l === 'curl' ? 'bash' : l, file: l === 'curl' ? 'decide.sh' : l === 'python' ? 'decide.py' : 'decide.mjs', maxHeight: 420 });
    return;
  }
  $('#snip', root).innerHTML = codeBlock(snippet(lang, req, { format: L.sdk || format === 'studio' ? 'typesafe' : format }), { lang: L.lang, file: L.file, maxHeight: 420 });
}
