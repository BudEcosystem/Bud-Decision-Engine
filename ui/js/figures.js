// Decision charts. One renderer per answer type, all on shared 0-100% axes, each with a one-sentence caption
// that says in words what the chart shows and a single verdict: act automatically or ask a human.

import { certainty, esc, fmtNum, fmtPct, icon } from './util.js';

export const TYPE_META = {
  choice: { name: 'Pick one', icon: 'choice', api: 'choice' },
  score: { name: 'Rate on a scale', icon: 'score', api: 'score' },
  noul: { name: 'Yes or no', icon: 'noul', api: 'noul' },
  multi: { name: 'Pick any', icon: 'multi', api: 'multi' },
  rank: { name: 'Put in order', icon: 'rank', api: 'rank' },
  number: { name: 'Estimate a number', icon: 'number', api: 'number' },
};

const pct = (p) => Math.round((p || 0) * 100);
// "1 day", "2 days": a unit written in the plural loses its s for exactly one.
export const unitFor = (v, unit) => (!unit ? '' : ` ${+v === 1 && /[^s]s$/.test(unit) ? unit.slice(0, -1) : unit}`);
const nice = (s) => String(s).replace(/_/g, ' ');

// ------------------------------------------------------------------ rolling digits
// The number rolls into place digit by digit, like a mechanical counter. Non-digits sit still.
export function odo(text) {
  return `<span class="odo" aria-label="${esc(text)}">${[...String(text)].map((ch) => (/\d/.test(ch)
    ? `<span class="d" aria-hidden="true"><span data-digit="${ch}">${'0123456789'.split('').map((n) => `<span>${n}</span>`).join('')}</span></span>`
    : `<span aria-hidden="true">${ch === ' ' ? '&nbsp;' : esc(ch)}</span>`)).join('')}</span>`;
}

// Starts the data motion after the figures are in the DOM: bars grow from the baseline, digits roll.
export function animate(root, { instant = false } = {}) {
  const apply = () => {
    root.querySelectorAll('[data-sx]').forEach((el) => { el.style.transform = `scaleX(${el.dataset.sx})`; });
    root.querySelectorAll('[data-sy]').forEach((el) => { el.style.transform = `scaleY(${el.dataset.sy})`; });
    root.querySelectorAll('[data-left]').forEach((el) => { el.style.left = el.dataset.left; });
    root.querySelectorAll('[data-digit]').forEach((el) => { el.style.transform = `translateY(${-1.2 * +el.dataset.digit}em)`; });
  };
  if (instant || matchMedia('(prefers-reduced-motion: reduce)').matches) { apply(); return; }
  requestAnimationFrame(() => requestAnimationFrame(apply));
}

// ------------------------------------------------------------------ gate: act automatically or ask a human
// Decisions made through the studio API carry their own verdict (certainty and act, gated at each question's
// threshold when the decision was made). A threshold passed here instead previews "what if" at that value.
function gateOf(a, threshold, server = false) {
  if (server && a.act != null && a.certainty != null) return { top: a.certainty, act: a.act };
  let top = a.certainty ?? a.top_probability;
  if (a.type === 'multi') top = Math.min(...Object.values(a.probabilities || {}).map((p) => Math.max(p, 1 - p)));
  if (top == null) top = Math.max(...Object.values(a.probabilities || { x: 0 }));
  return { top, act: top >= threshold };
}
const gateHTML = (g, threshold) => (g.act
  ? `<span class="gate act">${icon('act')}Act automatically</span>`
  : `<span class="gate ask">${icon('ask')}Ask a human</span><span class="faint">below ${pct(threshold)}%, the act threshold</span>`);

function extrasHTML(a) {
  const x = a.model_extras;
  if (!x) return '';
  return Object.entries(x).filter(([k]) => !k.endsWith('_help') && k !== 'act_probability').slice(0, 3)
    .map(([k, v]) => `<span class="faint">${esc(nice(k))} ${typeof v === 'number' ? fmtNum(v, 3) : esc(JSON.stringify(v))}</span>`).join('');
}

// ------------------------------------------------------------------ plots
function hplot(rows, { threshold, ranked = false, limit = 8 } = {}) {
  const shown = rows.slice(0, limit);
  const more = rows.length - shown.length;
  const th = threshold != null ? `<span class="thresh" style="left:${(threshold * 100).toFixed(1)}%" aria-hidden="true"></span>` : '';
  const body = (list, off = 0) => list.map((r, i) => `<div class="row ${r.cls || ''}">
      <span class="lab" title="${esc(r.label)}">${ranked ? `<span class="rank">${i + 1 + off}</span>` : ''}${esc(nice(r.label))}</span>
      <span class="track"><span class="bar" data-sx="${Math.max(0.004, r.p).toFixed(4)}"></span>${th}</span>
      <span class="val">${fmtPct(r.p)}</span></div>`).join('');
  return `<div class="hplot">${body(shown)}
    ${more > 0 ? `<button class="btn btn-quiet sm more" data-more>${icon('caret-down')}Show all ${rows.length}</button><div class="hidden" data-rest style="display:contents">${body(rows.slice(limit), limit)}</div>` : ''}
    <span></span><span class="axis" aria-hidden="true"><span>0</span><span>25</span><span>50</span><span>75</span><span>100%</span></span><span></span>
  </div>`;
}

function choiceFig(a) {
  const rows = Object.entries(a.probabilities).map(([label, p]) => ({ label, p })).sort((x, y) => y.p - x.p);
  rows.forEach((r, i) => { r.cls = i === 0 ? 'row-win' : i === 1 ? 'row-2' : ''; });
  const [w, r2] = rows;
  return {
    answer: nice(a.choice), p: w.p,
    plot: hplot(rows),
    say: r2 ? `${esc(nice(w.label))} takes ${fmtPct(w.p)} of the model's belief; ${esc(nice(r2.label))} is next at ${fmtPct(r2.p)}.` : '',
  };
}

function rankFig(a) {
  const rows = (a.ranking || Object.keys(a.probabilities)).map((label) => ({ label, p: a.probabilities[label] }));
  rows.forEach((r, i) => { r.cls = i === 0 ? 'row-win' : i === 1 ? 'row-2' : ''; });
  return {
    answer: `1. ${nice(rows[0].label)}`, p: rows[0].p,
    plot: hplot(rows, { ranked: true, limit: 10 }),
    say: `Ordered from most to least likely. ${rows.length > 1 ? `The gap between first and second is ${Math.round((rows[0].p - rows[1].p) * 100)} points.` : ''}`,
  };
}

function multiFig(a, q) {
  const th = a.threshold ?? q?.threshold ?? 0.5;
  const sel = new Set(a.selected || []);
  const rows = Object.entries(a.probabilities).map(([label, p]) => ({ label, p, cls: sel.has(label) ? 'row-sel' : '' })).sort((x, y) => y.p - x.p);
  return {
    answer: sel.size ? [...sel].map(nice).join(', ') : 'None apply', p: null,
    plot: hplot(rows, { threshold: th, limit: 12 }),
    say: `${sel.size} of ${rows.length} apply. Each option is judged on its own; the dashed line is the ${pct(th)}% cut-off.`,
  };
}

function noulFig(a, q) {
  const y = a.noul;
  const yes = y >= 0.5;
  const yesLabel = q?.criteria?.true ? `Yes: ${q.criteria.true}` : 'Yes';
  return {
    answer: yes ? 'Yes' : 'No', p: yes ? y : 1 - y,
    plot: `<div class="ygauge">
      <div class="track" role="img" aria-label="No ${fmtPct(1 - y)}, yes ${fmtPct(y)}">
        <span class="bar no ${yes ? '' : 'win'}" data-sx="${Math.max(0.004, 1 - y).toFixed(4)}"></span>
        <span class="bar yes ${yes ? 'win' : ''}" data-sx="${Math.max(0.004, y).toFixed(4)}"></span><span class="mid"></span></div>
      <div class="axis"><span class="${yes ? '' : 'win'}">No <span class="num">${fmtPct(1 - y)}</span></span><span>50%</span><span class="${yes ? 'win' : ''}">Yes <span class="num">${fmtPct(y)}</span></span></div></div>`,
    say: `The probability that the statement is true is ${fmtPct(y, 1)}. ${esc(yesLabel === 'Yes' ? '' : yesLabel)}`,
  };
}

function scoreFig(a, q) {
  const n = Object.keys(a.probabilities).length;
  const levels = Array.from({ length: n }, (_, i) => a.legend?.[String(i)] ?? q?.criteria?.[i] ?? String(i));
  const probs = levels.map((_, i) => a.probabilities[String(i)] || 0);
  const top = probs.indexOf(Math.max(...probs));
  const pos = n > 1 ? a.score / (n - 1) : 0;
  return {
    answer: nice(levels[top]), p: probs[top],
    plot: `<div class="hist" style="--n:${n}">
      <div class="plotwrap"><div class="cols">${probs.map((p, i) => `<span class="col ${i === top ? 'win' : ''}" title="${esc(levels[i])}: ${fmtPct(p)}"><i data-sy="${Math.max(0.006, p).toFixed(4)}"></i></span>`).join('')}</div>${yScale}</div>
      <div class="line"><span class="marker" style="left:0" data-left="calc(${(pos * 100).toFixed(2)}% * ${(n - 1) / n} + ${(50 / n).toFixed(3)}%)" title="Average position ${fmtNum(a.score, 2)}"></span></div>
      <div class="labels">${levels.map((l, i) => `<span class="${i === top ? 'win' : ''}">${esc(nice(l))}<span class="num">${fmtPct(probs[i])}</span></span>`).join('')}</div></div>`,
    say: `Most likely ${esc(nice(levels[top]))}. The ring marks the average position, ${fmtNum(a.score, 2)} on a 0 to ${n - 1} scale, which can land between levels.`,
  };
}

function numberFig(a) {
  const pts = Object.entries(a.probabilities).map(([v, p]) => [+v, p]).sort((x, y) => x[0] - y[0]);
  const lo = pts[0][0], hi = pts.at(-1)[0];
  const span = hi - lo || 1;
  const x = (v) => `${(((v - lo) / span) * 94 + 3).toFixed(2)}%`;
  const unit = unitFor(2, a.unit);
  const [r0, r1] = a.range || [lo, hi];
  return {
    answer: `${fmtNum(a.estimate, 1)}${unit}`, p: null,
    plot: `<div class="nplot"><div class="plotwrap">
      <div class="area" role="img" aria-label="Estimate ${fmtNum(a.estimate, 1)}${esc(unit)}, 80% range ${fmtNum(r0)} to ${fmtNum(r1)}">
        <span class="band80" style="left:${x(r0)};width:calc(${x(r1)} - ${x(r0)})"></span>
        ${pts.map(([v, p]) => `<span class="stem ${v === a.most_likely ? 'win' : ''}" style="left:${x(v)};height:100%" data-sy="${Math.max(0.01, p).toFixed(4)}" title="${fmtNum(v)}${esc(unitFor(v, a.unit))}: ${fmtPct(p)}"></span>`).join('')}
        <span class="est" style="left:${x(a.estimate)}"><span>${fmtNum(a.estimate, 1)}</span></span>
      </div>${yScale}</div>
      <div class="axis">${pts.map(([v]) => `<span style="left:${x(v)}">${fmtNum(v)}</span>`).join('')}</div></div>`,
    say: `Each stem is the chance of one value, on the 0 to 100% scale. Best estimate ${fmtNum(a.estimate, 1)}${esc(unit)}; most likely single value ${fmtNum(a.most_likely)}${esc(unitFor(a.most_likely, a.unit))}. The shaded band is the 80% range, ${fmtNum(r0)} to ${fmtNum(r1)}${esc(unit)}.`,
  };
}

const yScale = `<span class="yaxis" aria-hidden="true">${[100, 75, 50, 25, 0].map((t) => `<span style="bottom:${t}%">${t}${t === 100 ? '%' : ''}</span>`).join('')}</span>`;

const RENDER = { choice: choiceFig, rank: rankFig, multi: multiFig, noul: noulFig, score: scoreFig, number: numberFig };

// ------------------------------------------------------------------ one figure
export function figure(key, a, q, { no = '1a', threshold = 0.9, server = false } = {}) {
  const r = (RENDER[a.type] || choiceFig)(a, q);
  const g = gateOf(a, threshold, server);
  const c = certainty(g.top);
  const title = q?.instructions || key;
  const conf = a.confidence != null ? `<span><span class="term" data-term="confidence" tabindex="0">Confidence</span> <span class="num">${fmtNum(a.confidence, 2)}</span></span>` : '';
  return `<figure class="fig ${g.act ? '' : 'unsure'}" style="margin:0" data-key="${esc(key)}">
    <div class="fig-head"><span class="fig-no">Fig. ${esc(no)}</span><span class="fig-title">${esc(title)}</span>
      <span class="chip ${g.act ? c.tone : 'amber'}">${esc(c.word)}</span></div>
    <div class="fig-answer"><b>${esc(r.answer)}</b>${r.p != null ? `<span class="num">${odo(fmtPct(r.p))}</span>` : ''}</div>
    ${r.plot}
    <figcaption class="fig-cap"><span>${r.say}</span>${conf}${extrasHTML(a)}${gateHTML(g, threshold)}</figcaption>
  </figure>`;
}

// All figures of one response, lettered in question order.
// threshold: a "what if" act threshold for every question; leave it out to show the verdict the decision was made
// with (each question at its own threshold, from response.settings).
export function figures(response, request, { run = 1, threshold } = {}) {
  const qs = request?.questions || {};
  const keys = Object.keys(response.answers || {});
  const st = response.settings || {};
  const server = threshold == null;
  const thr = (k) => (server ? st.questions?.[k]?.act_threshold ?? st.act_threshold ?? 0.9 : threshold);
  return `<div class="figs">${keys.map((k, i) => figure(k, response.answers[k], qs[k], { no: `${run}${String.fromCharCode(97 + (i % 26))}`, threshold: thr(k), server })).join('')}</div>`;
}

// "Show all N" buttons inside long plots.
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-more]');
  if (!b) return;
  const rest = b.parentElement.querySelector('[data-rest]');
  rest.classList.remove('hidden');
  b.remove();
  animate(rest, { instant: true });
});

// A compact single-line readout for tables (batch results, compare matrix, history).
export function mini(a) {
  if (!a) return '';
  if (a.type === 'noul') return `${a.noul >= 0.5 ? 'Yes' : 'No'} <span class="faint num">${fmtPct(a.noul >= 0.5 ? a.noul : 1 - a.noul)}</span>`;
  if (a.type === 'multi') return (a.selected || []).map(nice).join(', ') || '<span class="faint">none</span>';
  if (a.type === 'number') return `${fmtNum(a.estimate, 1)}${a.unit ? ` ${esc(a.unit)}` : ''} <span class="faint num">${fmtNum(a.range?.[0])} to ${fmtNum(a.range?.[1])}</span>`;
  if (a.type === 'score') {
    const top = Object.entries(a.probabilities).sort((x, y) => y[1] - x[1])[0];
    return `${esc(nice(a.legend?.[top[0]] ?? top[0]))} <span class="faint num">${fmtPct(top[1])}</span>`;
  }
  const w = a.type === 'rank' ? a.ranking?.[0] : a.choice;
  return `${esc(nice(w))} <span class="faint num">${fmtPct(a.probabilities?.[w])}</span>`;
}

// The value a decision "is", for agreement checks and CSV export.
export function decisionOf(a) {
  if (!a) return null;
  if (a.type === 'noul') return a.noul >= 0.5 ? 'yes' : 'no';
  if (a.type === 'multi') return (a.selected || []).join('|');
  if (a.type === 'number') return a.most_likely;
  if (a.type === 'score') return Object.entries(a.probabilities).sort((x, y) => y[1] - x[1])[0][0];
  if (a.type === 'rank') return a.ranking?.[0];
  return a.choice;
}
