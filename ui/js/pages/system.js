// System: where models run (chosen during setup), memory, what each loaded model is using, and memory settings.

import { showLogs } from './models.js';
import { api, ejectModel, phaseTrack, store } from '../store.js';
import { setSub } from '../shell.js';
import { $, $$, esc, fmtGB, icon, term, toast } from '../util.js';

let root;

export function mount(el) {
  root = el;
  setSub('Where models run, memory, and what each model uses');
  root.innerHTML = `<div class="view scroll"><div class="pad" style="display:grid;gap:16px">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><p class="muted" style="max-width:80ch" id="sysintro"></p>
      <button class="btn" id="ejectall" style="margin-left:auto">${icon('eject')}Eject all models</button></div>
    <div id="runson"></div>
    <div id="sys" style="display:grid;gap:16px"></div>
    <div id="sysset"></div>
  </div></div>`;
  $('#ejectall', root).addEventListener('click', async () => {
    try { await api('/api/eject-all', { method: 'POST' }); toast('All models ejected. Their memory is free again.'); } catch (e) { toast(e.message, 'error'); }
  });
  update();
}

export function onState() { if (root?.isConnected) update(); }

function update() {
  const st = store.state;
  const box = $('#sys', root);
  if (!st) { box.innerHTML = '<p class="faint">Connecting to the studio.</p>'; return; }
  const s = st.system;
  const loaded = st.models.filter((m) => m.worker);
  const modelGB = loaded.reduce((a, m) => a + (m.worker.gpu_gb || 0), 0);
  const other = Math.max(0, s.mem_used_gb - modelGB);
  const pct = (g) => `${Math.max(0, (g / s.mem_total_gb) * 100).toFixed(2)}%`;
  const rt = st.runtime || { device: 'cuda', device_name: s.gpu_name, available: [] };
  const dev = rt.available.find((d) => d.id === rt.device) || {};
  $('#sysintro', root).innerHTML = rt.device === 'cpu'
    ? `Models run on the processor (${esc(rt.device_name)}) and use the computer's memory. Small models answer in about a second; large ones are slow.`
    : dev.unified_memory
      ? `Models run on ${esc(rt.device_name)}. Its GPU and CPU share one pool of ${term('unified_memory', 'unified memory')}, so loaded models and other programs draw from the same memory.`
      : `Models run on ${esc(rt.device_name)}.`;
  renderRunsOn(rt);
  const detail = rt.device === 'cuda' ? `CUDA ${esc(s.cuda || 'unknown')}, driver ${esc(s.driver || 'unknown')}`
    : rt.device === 'mps' ? 'Apple GPU through Metal' : rt.device === 'xpu' ? 'Intel GPU through oneAPI' : `${s.cpu_count || ''} cores`;
  const html = `
    <section class="card card-pad" style="display:grid;gap:10px">
      <div class="stats">
        <div class="stat"><span>Runs on</span><div class="big" style="font-size:${rt.device_name.length > 18 ? 16 : 20}px;letter-spacing:-.01em;font-variant-numeric:normal">${esc(rt.device_name)}</div><span>${detail}</span></div>
        ${rt.device !== 'cpu' && s.gpu_util != null ? `<div class="stat"><span>GPU busy</span><div class="big">${Math.round(s.gpu_util)}%</div><span>jumps while a model answers</span></div>` : `<div class="stat"><span>Processor busy</span><div class="big">${s.cpu_percent != null ? Math.round(s.cpu_percent) : 'n/a'}%</div><span>jumps while a model answers</span></div>`}
        ${rt.device !== 'cpu' && s.gpu_temp != null ? `<div class="stat"><span>Temperature and power</span><div class="big">${Math.round(s.gpu_temp)} °C</div><span>${s.gpu_power != null ? `${s.gpu_power.toFixed(1)} W` : ''}</span></div>` : ''}
        <div class="stat"><span>Free disk for models</span><div class="big">${fmtGB(s.disk_free_gb)}</div><span>of ${fmtGB(s.disk_total_gb)}</span></div>
      </div>
    </section>
    <section class="card card-pad" style="display:grid;gap:10px">
      <h2 style="font-size:15px">Memory</h2>
      <p class="muted small" style="margin-top:4px">${fmtGB(s.mem_used_gb)} of ${fmtGB(s.mem_total_gb)} in use. <b style="color:var(--text)">${fmtGB(s.mem_available_gb)} available</b> for more models.</p>
      <div class="memmap" role="img" aria-label="Memory use: ${fmtGB(modelGB)} by models, ${fmtGB(other)} by other programs">
        ${loaded.map((m) => { const g = m.worker.gpu_gb || 0; return `<div style="width:${pct(g)}" data-tip="${esc(m.name)}: ${fmtGB(g)}">${g / s.mem_total_gb > 0.07 ? esc(m.name) : ''}</div>`; }).join('')}
        <div class="other" style="width:${pct(other)}" data-tip="Other programs on this machine: ${fmtGB(other)}">${other / s.mem_total_gb > 0.1 ? 'other programs' : ''}</div>
      </div>
      <div class="legend"><span><i style="background:var(--accent)"></i>Loaded models, ${fmtGB(modelGB)}</span><span><i style="background:var(--text-3)"></i>Other programs, ${fmtGB(other)}</span><span><i style="background:var(--fill)"></i>Free</span></div>
    </section>
    <section class="card" style="overflow:hidden">
      <div class="card-pad" style="padding-bottom:6px"><h2 style="font-size:15px">Loaded models</h2></div>
      ${loaded.length ? `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Model</th><th>Status</th><th class="num">Memory</th><th class="num">Requests</th><th class="num">Loaded in</th><th>Process</th><th></th></tr></thead><tbody>
        ${loaded.map((m) => `<tr><td><b>${esc(m.name)}</b></td><td><span style="display:inline-flex;gap:7px;align-items:center">${phaseTrack(m)}${m.worker.status === 'ready' ? (m.worker.warning ? `Ready <span class="chip amber" data-tip="${esc(m.worker.warning)}">On the processor</span>` : 'Ready') : m.worker.status === 'error' ? '<span style="color:var(--red-text)">Error</span>' : esc(m.worker.stage || m.worker.status)}</span></td>
          <td class="num">${fmtGB(m.worker.gpu_gb || 0)}</td><td class="num">${m.worker.requests}</td><td class="num">${m.worker.load_seconds ? `${m.worker.load_seconds} s` : ''}</td>
          <td class="small faint">pid ${m.worker.pid}, ${Object.entries(m.worker.options || {}).map(([k, v]) => `${esc(k)} ${esc(String(v))}`).join(', ')}</td>
          <td style="white-space:nowrap"><button class="btn btn-quiet sm" data-logs="${m.id}">${icon('scroll')}Log</button><button class="btn sm" data-eject="${m.id}">${icon('eject')}Eject</button></td></tr>`).join('')}
      </tbody></table></div>` : '<p class="plate-pad muted">No models loaded. Load one from the Models page, or press Decide in the Playground.</p>'}
    </section>`;
  if (box.dataset.html !== html) {
    box.dataset.html = html; box.innerHTML = html;
    $$('[data-eject]', box).forEach((b) => b.addEventListener('click', () => ejectModel(b.dataset.eject)));
    $$('[data-logs]', box).forEach((b) => b.addEventListener('click', () => showLogs(b.dataset.logs)));
  }
  renderSettings(st);
}

// "Where models run": the device chosen during setup, switchable among the ones PyTorch was installed for.
function renderRunsOn(rt) {
  const box = $('#runson', root);
  const key = JSON.stringify([rt.device, rt.available.map((d) => d.id)]);
  if (!box || box.dataset.key === key) return;
  box.dataset.key = key;
  const label = (d) => (d.id === 'cpu' ? 'Processor (CPU)' : `${d.name} (GPU)`);
  const desktop = !!window.__TAURI__;
  const when = rt.installed ? new Date(rt.installed).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '';
  box.innerHTML = `<div class="group-title">Where models run</div><section class="group">
    <div class="grow"><span style="flex:1;min-width:0">Run models on<span class="help" style="display:block">Applies to models you load from now on. Loaded models keep running where they are.</span></span>
      <div class="seg" role="group" aria-label="Run models on">${rt.available.map((d) => `<button data-dev="${d.id}" aria-pressed="${d.id === rt.device}">${icon(d.id === 'cpu' ? 'cpu' : 'lightning')}${esc(label(d))}</button>`).join('')}</div></div>
    <div class="grow"><span style="flex:1;min-width:0">Installed engine<span class="help" style="display:block">${rt.torch ? `PyTorch ${esc(rt.torch)}${when ? `, set up on ${esc(when)}` : ''}.` : 'Set up from source.'} To run on a different processor, run setup again; it installs the matching engine.</span></span>
      ${desktop ? `<button class="btn" id="resetup">${icon('arrows-clockwise')}Run setup again</button>` : `<code class="small">./install.sh</code>`}</div>
  </section>`;
  $$('[data-dev]', box).forEach((b) => b.addEventListener('click', async () => {
    try {
      await api('/api/config', { method: 'POST', body: { device: b.dataset.dev } });
      $$('[data-dev]', box).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      toast(`New models will run on the ${b.dataset.dev === 'cpu' ? 'processor' : 'GPU'}.`);
    } catch (e) { toast(e.message, 'error'); }
  }));
  $('#resetup', box)?.addEventListener('click', () => window.__TAURI__.core.invoke('rerun_setup').catch((e) => toast(String(e), 'error')));
}

function renderSettings(st) {
  const box = $('#sysset', root);
  if (!box || box.dataset.done) return;
  box.dataset.done = '1';
  box.innerHTML = `<section class="card card-pad" style="display:grid;gap:12px">
      <h2 style="font-size:15px">Memory management</h2>
      <label class="field" style="max-width:520px"><span class="label">Eject models that have not been used for</span>
        <select class="select" id="idle" style="max-width:240px">${[[0, 'Never (keep them loaded)'], [15, '15 minutes'], [60, '1 hour'], [240, '4 hours']].map(([v, l]) => `<option value="${v}" ${+st.settings.idle_eject_minutes === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span class="help">Frees memory for other programs automatically. An ejected model loads again the next time it is used.</span></label>
      <label class="switch"><input type="checkbox" id="autoload" ${st.settings.auto_load ? 'checked' : ''}>Load models on demand</label>
      <span class="help" style="margin-top:-6px">When an API request names a downloaded model that is not loaded, load it and then answer; the request waits. Off: such requests are refused.</span>
      <h2 style="font-size:15px;margin-top:8px">Training</h2>
      <label class="switch"><input type="checkbox" id="exptrain" ${st.settings.experimental_training ? 'checked' : ''}>Allow experimental training</label>
      <span class="help" style="margin-top:-6px">Lets the Train page use GPUs that work but haven't been fully tested for training: Intel Arc and Core Ultra graphics, AMD on Linux, older NVIDIA cards and Apple M1. Training on NVIDIA RTX 30 series or newer and Apple M2 or newer is always on.</span>
    </section>`;
  $('#idle', box).addEventListener('change', (e) => saveSetting({ idle_eject_minutes: +e.target.value }));
  $('#autoload', box).addEventListener('change', (e) => saveSetting({ auto_load: e.target.checked }));
  $('#exptrain', box).addEventListener('change', (e) => saveSetting({ experimental_training: e.target.checked }));
}

async function saveSetting(patch) {
  try { await api('/api/settings', { method: 'POST', body: patch }); toast('Setting saved'); }
  catch (e) { toast(e.message, 'error'); }
}
