// Small shared helpers: escaping, formatting, icons, toasts, tooltips, dialogs.

import { ICONS } from './phosphor.js';

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function fmtBytes(b) {
  if (b == null) return 'unknown';
  if (b >= 1e9) return `${(b / 1e9).toFixed(b >= 10e9 ? 1 : 2)} GB`;
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`;
  return `${Math.max(1, Math.round(b / 1e3))} KB`;
}
export const fmtGB = (g) => (g == null ? 'unknown' : `${g >= 10 ? g.toFixed(0) : g.toFixed(1)} GB`);
export function fmtPct(p, digits = 0) {
  if (p == null || Number.isNaN(p)) return 'n/a';
  const v = p * 100;
  if (v > 0 && v < 1 && digits === 0) return '<1%';
  if (v < 100 && v > 99 && digits === 0) return '>99%';
  return `${v.toFixed(digits)}%`;
}
export function fmtMs(ms) {
  if (ms == null) return 'n/a';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}
export function fmtDuration(s) {
  if (s == null || !isFinite(s)) return '';
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
export function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(+v)) return String(v ?? '');
  const n = +v;
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

// Plain-language certainty for the top probability. The same four words appear everywhere in the studio.
export function certainty(top) {
  if (top >= 0.9) return { word: 'Very sure', tone: 'violet' };
  if (top >= 0.75) return { word: 'Fairly sure', tone: 'violet' };
  if (top >= 0.55) return { word: 'Leaning', tone: '' };
  return { word: 'Unsure', tone: 'amber' };
}

export const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 32) || 'question';
export function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function copy(text, label = 'Copied') {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch {
    // Clipboard API needs a secure context; fall back to a hidden textarea on plain http from another host.
    const ta = Object.assign(document.createElement('textarea'), { value: text });
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.append(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove();
    toast(ok ? label : 'Copy failed. Select the text and copy it manually.', ok ? '' : 'error');
  }
}

export function toast(msg, kind = '') {
  let box = $('.toasts');
  if (!box) { box = document.createElement('div'); box.className = 'toasts'; box.setAttribute('role', 'status'); document.body.append(box); }
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = msg;
  box.append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3200);
}

// ------------------------------------------------------------------ icons (Phosphor, regular weight, vendored)
const ALIAS = {
  learn: 'book-open', try: 'flask', models: 'cube', code: 'brackets-curly', system: 'cpu', download: 'download-simple',
  alert: 'warning', ask: 'user', history: 'clock-counter-clockwise', wand: 'magic-wand', external: 'arrow-square-out',
  up: 'arrow-up', down: 'arrow-down', sliders: 'sliders-horizontal', act: 'check-circle', table: 'table', compare: 'columns',
  choice: 'radio-button', score: 'gauge', noul: 'toggle-right', multi: 'check-square', rank: 'list-numbers', number: 'ruler',
  text: 'text-t', audio: 'speaker-high', video: 'film-strip', api: 'terminal-window', registry: 'squares-four',
};
export const icon = (name, cls = '') => `<svg class="i ${cls}" viewBox="0 0 256 256" aria-hidden="true">${ICONS[ALIAS[name] || name] || ''}</svg>`;

// ------------------------------------------------------------------ tooltips for glossary terms and [data-tip]
let tipEl, tipTarget;
export function initTips(glossary) {
  setInterval(() => { if (tipEl?.isConnected && !tipTarget?.isConnected) tipEl.remove(); }, 400);
  const show = (target) => {
    tipTarget = target;
    const key = target.dataset.term;
    const g = key ? glossary[key] : null;
    const text = g ? `<b>${esc(g.term)}</b>${esc(g.def)}` : esc(target.dataset.tip || '');
    if (!text) return;
    tipEl ??= Object.assign(document.createElement('div'), { className: 'tip', role: 'tooltip' });
    tipEl.innerHTML = text;
    document.body.append(tipEl);
    const r = target.getBoundingClientRect();
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    const x = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
    let y = r.bottom + 8;
    if (y + h > innerHeight - 8) y = r.top - h - 8;
    tipEl.style.left = `${x}px`; tipEl.style.top = `${y}px`;
  };
  const hide = () => tipEl?.remove();
  document.addEventListener('mouseover', (e) => { const t = e.target.closest('[data-term],[data-tip]'); if (t) show(t); });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('[data-term],[data-tip]')) hide(); });
  document.addEventListener('focusin', (e) => { const t = e.target.closest('[data-term],[data-tip]'); if (t) show(t); });
  document.addEventListener('focusout', hide);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
}
// Wrap a glossary term: term('calibration') or term('calibration', 'calibrated')
export const term = (key, text) => `<span class="term" tabindex="0" data-term="${esc(key)}">${esc(text || key)}</span>`;

export function confirmDialog({ title, body, confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const d = document.createElement('dialog');
    d.innerHTML = `<form method="dialog"><div class="dialog-body"><h2 style="font-size:17px">${esc(title)}</h2><div class="muted">${body}</div></div>
      <div class="dialog-foot"><button class="btn" value="cancel">Cancel</button><button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" value="ok">${esc(confirm)}</button></div></form>`;
    document.body.append(d);
    d.addEventListener('close', () => { resolve(d.returnValue === 'ok'); d.remove(); });
    d.showModal();
  });
}

export function askText({ title, label = '', value = '', pattern, confirm = 'Save' }) {
  return new Promise((resolve) => {
    const d = document.createElement('dialog');
    d.innerHTML = `<form method="dialog"><div class="dialog-body"><h2 style="font-size:17px">${esc(title)}</h2>
      <label class="field"><span class="help">${esc(label)}</span><input class="input code" name="v" value="${esc(value)}" ${pattern ? `pattern="${esc(pattern)}"` : ''} required></label></div>
      <div class="dialog-foot"><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn btn-primary" value="ok">${esc(confirm)}</button></div></form>`;
    document.body.append(d);
    const inp = d.querySelector('input');
    d.addEventListener('close', () => { resolve(d.returnValue === 'ok' ? inp.value.trim() : null); d.remove(); });
    d.showModal(); inp.select();
  });
}

// A small anchored menu (model picker, type picker, examples). Closes on outside click or Escape.
export function popMenu(anchor, html, { align = 'left', onPick } = {}) {
  document.querySelectorAll('.menu[data-pop]').forEach((m) => m.remove());
  const m = document.createElement('div');
  m.className = 'menu'; m.dataset.pop = '1'; m.setAttribute('role', 'menu');
  m.innerHTML = html;
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, h = m.offsetHeight;
  let x = align === 'right' ? r.right - w : r.left;
  x = Math.min(Math.max(8, x), innerWidth - w - 8);
  let y = r.bottom + 6 + scrollY;
  if (r.bottom + 6 + h > innerHeight - 8 && r.top - h - 6 > 8) y = r.top - h - 6 + scrollY;
  m.style.left = `${x}px`; m.style.top = `${y}px`;
  const close = () => { m.remove(); document.removeEventListener('mousedown', outside, true); document.removeEventListener('keydown', key, true); };
  const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) close(); };
  const key = (e) => {
    if (e.key === 'Escape') { close(); anchor.focus(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const items = $$('.menu-item', m); const i = items.indexOf(document.activeElement);
      const n = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
      items[n]?.focus(); e.preventDefault();
    }
  };
  document.addEventListener('mousedown', outside, true);
  document.addEventListener('keydown', key, true);
  m.addEventListener('click', (e) => { const it = e.target.closest('[data-pick]'); if (it) { close(); onPick?.(it.dataset.pick, it); } });
  setTimeout(() => $('.menu-item', m)?.focus(), 0);
  return close;
}
