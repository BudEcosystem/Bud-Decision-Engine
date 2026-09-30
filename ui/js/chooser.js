// "Choose models to download": nothing downloads until the person picks. The sheet opens by itself the first time
// the studio runs with no models on disk, and from the Models page afterwards. Chosen models join the download queue,
// which fetches them one at a time, smallest first, in the background.

import { api, refresh, registry, store } from './store.js';
import { $, $$, esc, fmtBytes, fmtGB, icon, toast } from './util.js';

const READS = { text: 'text', image: 'images', audio: 'audio', video: 'video' };
const list = (xs) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

// Two models to start with, picked for the hardware: one small encoder that answers instantly, and one capable
// model sized to the device. Only models that fit are suggested.
export function recommended(st = store.state) {
  const by = (id) => st.models.find((m) => m.id === id);
  const fits = (id) => by(id)?.fit?.ok;
  const gpu = (st.runtime?.device || 'cuda') !== 'cpu';
  const second = gpu ? ['intern-decision-4b', 'kev-0.5b'].find(fits) : 'gliner2.5-decide';
  return ['laya', second].filter((id) => id && fits(id));
}

// Bytes still to fetch for a set of models; a base model shared by several adapters counts once.
function bytesFor(models) {
  const bases = new Map();
  let total = 0;
  for (const m of models) {
    if (m.downloaded) continue;
    const left = m.download.remaining || m.download_bytes;
    if (m.base) { bases.set(m.base.id, m.base_bytes); total += Math.max(0, left - m.base_bytes); } else total += left;
  }
  return total + [...bases.values()].reduce((a, b) => a + b, 0);
}

export async function openChooser({ firstRun = false } = {}) {
  const st = store.state;
  if (!st) return;
  const reg = await registry();
  const queued = new Set([st.downloads?.active?.model_id, ...(st.downloads?.queue || [])].filter(Boolean));
  const pick = new Set(firstRun ? recommended(st) : []);
  const rec = new Set(recommended(st));
  const rt = st.runtime || {};
  const d = document.createElement('dialog');
  d.className = 'chooser';
  d.setAttribute('aria-labelledby', 'chtitle');
  const where = rt.device === 'cpu' ? `the processor (${esc(rt.device_name)})` : esc(rt.device_name || st.system.gpu_name);

  const rows = () => st.models.slice().sort((a, b) => (rec.has(b.id) - rec.has(a.id)) || (a.download_bytes - b.download_bytes)).map((m) => {
    const r = reg.models[m.id] || {};
    const mk = reg.makers[r.maker_id] || {};
    const src = mk.avatar || r.logo || mk.logo;
    const have = m.downloaded, inq = queued.has(m.id), blocked = !m.fit?.ok;
    const state = have ? '<span class="chip ok">On this computer</span>' : inq ? '<span class="chip violet">In the queue</span>'
      : blocked ? `<span class="chip">${esc(m.fit.reason.split('.')[0])}</span>` : `<span class="c num">${fmtBytes(m.download.remaining || m.download_bytes)}</span>`;
    const on = have || inq || pick.has(m.id);
    return `<label class="ch-row ${blocked && !have ? 'blocked' : ''}">
      <input type="checkbox" data-id="${m.id}" ${on ? 'checked' : ''} ${have || inq || (blocked && !have) ? 'disabled' : ''}>
      <span class="logo">${src ? `<img src="/ui/${esc(src)}" alt="">` : icon('cube')}</span>
      <span class="nm"><b>${esc(m.name)}${rec.has(m.id) ? '<span class="chip violet">Recommended</span>' : ''}</b>
        <span class="tag">${esc(m.tagline)}</span><span class="meta">Reads ${list(m.modalities.map((k) => READS[k]))}. ${esc(m.params)} parameters.</span></span>
      <span class="st">${state}</span></label>`;
  }).join('');

  const foot = () => {
    const chosen = st.models.filter((m) => pick.has(m.id) && !m.downloaded && !queued.has(m.id));
    const bytes = bytesFor(chosen);
    const free = (st.system.disk_free_gb || 0) * 1e9;
    const tooBig = free && bytes > free * 0.95;
    $('#chsum', d).innerHTML = chosen.length
      ? `${chosen.length} ${chosen.length === 1 ? 'model' : 'models'}, <b>${fmtBytes(bytes)}</b>. ${tooBig ? `<span class="warn">Only ${fmtGB(st.system.disk_free_gb)} free on this computer.</span>` : `${fmtGB(st.system.disk_free_gb)} free on this computer.`}`
      : 'Tick the models you want.';
    const go = $('#chgo', d);
    go.disabled = !chosen.length || tooBig;
    go.innerHTML = `${icon('cloud-arrow-down')}${chosen.length ? `Download ${chosen.length === 1 ? chosen[0].name : `${chosen.length} models`}` : 'Download'}`;
  };

  d.innerHTML = `<div class="ch-head">
      <h2 id="chtitle">${firstRun ? 'Choose models to download' : 'Download models'}</h2>
      <p>Models run on ${where}. Pick the ones you want; they download one at a time, smallest first, and keep going in the background while you work. You can add or remove models later on the Models page.</p>
      <div class="ch-presets" role="group" aria-label="Quick selection"><span class="small muted">Select</span>
        <button type="button" class="btn btn-plain sm" data-preset="rec">Recommended</button><button type="button" class="btn btn-plain sm" data-preset="fit">Everything that fits</button><button type="button" class="btn btn-plain sm" data-preset="none">None</button></div>
    </div>
    <div class="ch-list" id="chlist">${rows()}</div>
    <div class="ch-foot"><span id="chsum" class="small"></span>
      <button class="btn" id="chlater">${firstRun ? 'Not now' : 'Cancel'}</button><button class="btn btn-primary" id="chgo"></button></div>`;
  document.body.append(d);

  const sync = () => $$('input[data-id]', d).forEach((c) => { if (!c.disabled) c.checked = pick.has(c.dataset.id); });
  $('#chlist', d).addEventListener('change', (e) => {
    const c = e.target.closest('input[data-id]');
    if (!c) return;
    if (c.checked) pick.add(c.dataset.id); else pick.delete(c.dataset.id);
    foot();
  });
  $$('[data-preset]', d).forEach((b) => b.addEventListener('click', () => {
    pick.clear();
    if (b.dataset.preset === 'rec') rec.forEach((id) => pick.add(id));
    if (b.dataset.preset === 'fit') st.models.forEach((m) => { if (m.fit?.ok) pick.add(m.id); });
    sync(); foot();
  }));
  $('#chlater', d).addEventListener('click', () => d.close());
  $('#chgo', d).addEventListener('click', async () => {
    const ids = [...pick].filter((id) => !queued.has(id) && !st.models.find((m) => m.id === id)?.downloaded);
    const go = $('#chgo', d);
    go.disabled = true; go.textContent = 'Queuing';
    try {
      await api('/api/downloads', { method: 'POST', body: { models: ids } });
      toast(ids.length === 1 ? 'Downloading now. It is ready to use as soon as it finishes.' : `${ids.length} models queued. The first is downloading now.`);
      refresh();
      d.close();
    } catch (e) { toast(e.message, 'error'); foot(); }
  });
  d.addEventListener('close', () => d.remove());
  foot();
  d.showModal();
  // Focus the list, not Download: Enter must never start a multi-gigabyte download by accident.
  $('input[data-id]:not(:disabled)', d)?.focus();
}
