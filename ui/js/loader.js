// The model loader, after LM Studio's Ctrl+L palette: filter field, sort switch, one aligned row per model,
// and a footer switch to choose load settings before loading. Choosing a downloaded model loads it straight away.

import { download, loadModel, registry, store } from './store.js';
import { $, $$, esc, fmtBytes, fmtGB, icon, toast } from './util.js';

const INPUT_ICON = { text: 'text-t', image: 'image', audio: 'speaker-high', video: 'film-strip' };
const INPUT = { text: 'Text', image: 'Images', audio: 'Audio', video: 'Video' };
const paramsNum = (p) => { const m = String(p).match(/([\d.]+)\s*([MB])/i); return m ? +m[1] * (m[2].toUpperCase() === 'B' ? 1 : 0.001) : 0; };
let manual = false;
let sortBy = 'ready';

export async function openLoader({ current } = {}) {
  const reg = await registry();
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'palette-back';
    back.innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-label="Choose a model">
      <label class="pal-search">${icon('magnifying-glass')}<input id="pq" placeholder="Type to filter models" autocomplete="off" aria-label="Filter models"></label>
      <div class="pal-bar"><span>Models</span><span class="seg" role="group" aria-label="Sort">${[['ready', 'Ready first'], ['size', 'Size'], ['name', 'Name']].map(([k, l]) => `<button data-sort="${k}" aria-pressed="${sortBy === k}">${l}</button>`).join('')}</span></div>
      <div class="pal-list" id="plist" role="listbox"></div>
      <div class="pal-foot"><label class="switch"><input type="checkbox" id="pmanual" ${manual ? 'checked' : ''}>Choose load settings first <span class="muted">(or hold <kbd>Alt</kbd>)</span></label>
        <span class="right"><span><kbd>Enter</kbd> choose</span><span><kbd>Esc</kbd> close</span></span></div>
    </div>`;
    document.body.append(back);
    const q = $('#pq', back), list = $('#plist', back);
    let active = 0, rows = [], altHeld = false;
    const close = (v) => { back.remove(); document.removeEventListener('keydown', key, true); resolve(v); };

    const draw = () => {
      const ms = (store.state?.models || []).slice();
      const f = q.value.trim().toLowerCase();
      rows = ms.filter((m) => !f || `${m.name} ${m.maker} ${m.tagline} ${m.modalities.join(' ')}`.toLowerCase().includes(f));
      const rank = (m) => (m.worker?.status === 'ready' ? 0 : m.worker ? 1 : m.downloaded ? 2 : 3);
      rows.sort(sortBy === 'size' ? (a, b) => paramsNum(a.params) - paramsNum(b.params) : sortBy === 'name' ? (a, b) => a.name.localeCompare(b.name) : (a, b) => rank(a) - rank(b) || paramsNum(a.params) - paramsNum(b.params));
      active = Math.min(active, Math.max(0, rows.length - 1));
      list.innerHTML = rows.map((m, i) => {
        const r = reg.models[m.id] || {};
        const mk = reg.makers[r.maker_id] || {};
        const src = mk.avatar || r.logo || mk.logo;
        const st = m.fit && !m.fit.ok && !m.worker ? `<span class="chip" title="${esc(m.fit.reason)}">${m.needs_gpu && store.state.runtime?.device === 'cpu' ? 'Needs a GPU' : 'Too large here'}</span>`
          : m.worker?.status === 'ready' ? '<span class="chip ok">Loaded</span>'
          : m.worker ? '<span class="chip amber">Loading</span>'
          : m.downloaded ? ''
          : store.state.downloads?.active?.model_id === m.id ? '<span class="chip violet">Downloading</span>'
          : `<span class="chip">${icon('download-simple')}${fmtBytes(m.download.remaining || m.download_bytes)}</span>`;
        return `<div class="pal-row ${i === active ? 'active' : ''} ${m.id === current ? 'cur' : ''}" role="option" aria-selected="${i === active}" data-i="${i}">
          <span class="logo">${src ? `<img src="/ui/${esc(src)}" alt="">` : icon('cube')}</span>
          <span class="nm"><b>${esc(m.name)}</b><span>${esc(m.tagline)}</span></span>
          <span class="c">${esc(m.maker)}</span>
          <span class="c num">${esc(m.params)}</span>
          <span class="c num">${fmtGB(m.memory_gb)}</span>
          <span class="ins" title="${m.modalities.map((k) => INPUT[k]).join(', ')}">${m.modalities.map((k) => icon(INPUT_ICON[k])).join('')}</span>
          <span class="st">${st}</span></div>`;
      }).join('') || '<div class="empty" style="padding:24px"><p>No model matches.</p></div>';
    };

    const choose = (i, alt) => {
      const m = rows[i];
      if (!m) return;
      if (m.fit && !m.fit.ok && !m.worker) { toast(m.fit.reason, 'error'); return; }
      if (!m.downloaded && !m.worker) { download(m.id); close(null); return; }
      if (m.worker || !(manual || alt)) { if (!m.worker) loadModel(m.id); close(m.id); return; }
      config(m);
    };

    // Load settings for one model, shown inside the palette (LM Studio's manual load parameters).
    const config = (m) => {
      const opts = m.load_options || [];
      const pal = $('.palette', back);
      pal.innerHTML = `<div class="pal-search"><b style="font-weight:600">Load ${esc(m.name)}</b><span class="muted small" style="margin-left:auto">about ${fmtGB(m.memory_gb)} of memory</span></div>
        <form class="pal-config" id="pform">${opts.map((o) => {
          if (o.type === 'select') return `<div class="setting"><div class="top"><label for="po-${o.key}">${esc(o.label)}</label></div><select class="select" id="po-${o.key}" name="${o.key}">${o.choices.map(([v, l]) => `<option value="${esc(v)}" ${v === o.default ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select><span class="help">${esc(o.help)}</span></div>`;
          if (o.type === 'number') return `<div class="setting"><div class="top"><label for="po-${o.key}">${esc(o.label)}</label><input class="input num-field" type="number" id="po-${o.key}" name="${o.key}" value="${o.default}" min="${o.min}" max="${o.max}" step="${o.step}"></div><input type="range" data-for="po-${o.key}" min="${o.min}" max="${o.max}" step="${o.step}" value="${o.default}" aria-label="${esc(o.label)}"><span class="help">${esc(o.help)}</span></div>`;
          if (o.type === 'toggle') return `<div class="setting"><label class="switch" style="justify-content:space-between"><span style="font-weight:500">${esc(o.label)}</span><input type="checkbox" name="${o.key}" ${o.default ? 'checked' : ''}></label><span class="help">${esc(o.help)}</span></div>`;
          return '';
        }).join('')}</form>
        <div class="pal-foot"><span class="right"><button class="btn" id="pcancel">Cancel</button><button class="btn btn-primary" id="pload">${icon('play')}Load model</button></span></div>`;
      const fill = (r) => r.style.setProperty('--fill-pct', `${((r.value - r.min) / (r.max - r.min || 1)) * 100}%`);
      $$('input[type=range][data-for]', pal).forEach((r) => { const n = $(`#${r.dataset.for}`, pal); fill(r); r.addEventListener('input', () => { n.value = r.value; fill(r); }); n.addEventListener('input', () => { r.value = n.value; fill(r); }); });
      $('#pcancel', pal).addEventListener('click', () => close(null));
      const submit = () => {
        const o = {};
        for (const el of $('#pform', pal).elements) if (el.name) o[el.name] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? +el.value : el.value;
        loadModel(m.id, o); close(m.id);
      };
      $('#pload', pal).addEventListener('click', submit);
      $('#pform', pal).addEventListener('submit', (e) => { e.preventDefault(); submit(); });
      $('select, input', pal)?.focus();
    };

    const key = (e) => {
      altHeld = e.altKey;
      if (e.key === 'Escape') { e.preventDefault(); close(null); return; }
      if (!$('#plist', back)) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); active = (active + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % Math.max(1, rows.length); draw(); $('.pal-row.active', list)?.scrollIntoView({ block: 'nearest' }); }
      if (e.key === 'Enter') { e.preventDefault(); choose(active, e.altKey); }
    };
    document.addEventListener('keydown', key, true);
    q.addEventListener('input', () => { active = 0; draw(); });
    list.addEventListener('click', (e) => { const r = e.target.closest('[data-i]'); if (r) choose(+r.dataset.i, e.altKey || altHeld); });
    list.addEventListener('mousemove', (e) => { const r = e.target.closest('[data-i]'); if (r && +r.dataset.i !== active) { active = +r.dataset.i; $$('.pal-row', list).forEach((x, i) => x.classList.toggle('active', i === active)); } });
    $$('[data-sort]', back).forEach((b) => b.addEventListener('click', () => { sortBy = b.dataset.sort; $$('[data-sort]', back).forEach((x) => x.setAttribute('aria-pressed', x === b)); draw(); }));
    $('#pmanual', back).addEventListener('change', (e) => { manual = e.target.checked; });
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(null); });
    draw();
    q.focus();
  });
}
