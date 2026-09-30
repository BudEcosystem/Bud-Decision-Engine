// Setup screens. First launch: Welcome (hardware check), Where models run, Install, Ready, then the studio opens
// and asks which models to download. Later launches skip straight to starting the studio.
//
// Opened in a plain browser (no Tauri), the page simulates the app so the screens can be previewed:
// ?hw=gb10 | mac | intel | cpu picks the simulated computer, ?installed=1 shows the launch splash.

import { ICONS } from './assets/phosphor.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n) => `<svg class="i" viewBox="0 0 256 256" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const T = window.__TAURI__ || mockTauri();
const invoke = (cmd, args) => T.core.invoke(cmd, args);
const msg = (e) => String(e?.message ?? e);

const OS = { linux: 'Linux', macos: 'macOS', windows: 'Windows' };
const ARCH = { x86_64: 'Intel or AMD 64-bit', aarch64: 'ARM 64-bit' };
const KIND = { nvidia: 'NVIDIA GPU', apple: 'Apple GPU', intel: 'Intel GPU', amd: 'AMD GPU', cpu: 'Processor' };

const S = { step: 'welcome', info: null, hw: null, hwError: null, device: null, log: [], progress: 0, stepId: null, stepsSeen: [], error: null, result: null, started: 0 };
const again = new URLSearchParams(location.search).get('setup') === 'again';

// ------------------------------------------------------------------ start
(async function start() {
  S.info = await invoke('app_info');
  $('#version').textContent = `Version ${S.info.version}`;
  if (S.info.installed && !again) return launch();
  $('#setup').hidden = false;
  go('welcome');
  try { S.hw = await invoke('detect_hardware'); S.device = S.hw.recommended; } catch (e) { S.hwError = msg(e); }
  if (S.step === 'welcome') go('welcome');
  // Unattended setup (BUD_STUDIO_DEVICE): the same screens, advancing on their own.
  const auto = S.info.unattended;
  if (auto && S.hw) {
    if (auto !== 'recommended' && S.hw.accelerators.some((a) => a.id === auto)) S.device = auto;
    setTimeout(() => go('device'), 1500);
    setTimeout(async () => { await runInstall(); if (S.step === 'ready') setTimeout(openStudio, 2500); }, 3500);
  }
})();

async function launch() {
  $('#splash').hidden = false;
  $('#splashmsg').textContent = 'Starting the engine';
  try { await invoke('start_studio'); } catch (e) { splashError(msg(e)); }
}

function splashError(msg) {
  const [head, ...rest] = msg.split('\n\n');
  $('#splash .bar').hidden = true;
  $('#splashmsg').textContent = `${head} Try again, or run setup again to repair the engine.`;
  const box = $('#splasherr');
  box.hidden = false;
  box.innerHTML = `${rest.length ? `<pre>${esc(rest.join('\n\n'))}</pre>` : ''}
    <div class="acts">${rest.length ? '<button class="btn" id="copyerr">Copy details</button>' : ''}<button class="btn" id="resetup">Run setup again</button><button class="btn btn-primary" id="retry">Try again</button></div>`;
  $('#copyerr')?.addEventListener('click', () => navigator.clipboard?.writeText(msg));
  $('#retry').onclick = () => { box.hidden = true; $('#splash .bar').hidden = false; launch(); };
  $('#resetup').onclick = () => { location.search = '?setup=again'; };
}

// ------------------------------------------------------------------ navigation
const ORDER = ['welcome', 'device', 'install', 'ready'];
function go(step) {
  S.step = step;
  const at = ORDER.indexOf(step);
  document.querySelectorAll('#steps li').forEach((li) => {
    const i = ORDER.indexOf(li.dataset.step);
    li.className = i < at ? 'done' : i === at ? 'current' : '';
    li.toggleAttribute('aria-current', i === at);
  });
  ({ welcome, device, install, ready })[step]();
  $('#body').firstElementChild?.classList.add('fade');
}

function foot({ note = '', back = null, next = null, nextLabel = 'Continue', nextDisabled = false }) {
  $('#footnote').innerHTML = note;
  const b = $('#back'), n = $('#next');
  b.hidden = !back; b.onclick = back;
  n.hidden = !next; n.onclick = next; n.textContent = nextLabel; n.disabled = nextDisabled;
}

// ------------------------------------------------------------------ 1. welcome and hardware check
function welcome() {
  const hw = S.hw;
  const rows = !hw && !S.hwError
    ? [1, 2, 3].map(() => `<div class="row"><span class="ic"></span><span class="t"><span class="skel" style="width:40%"></span><span class="skel" style="width:62%;margin-top:7px"></span></span></div>`).join('')
    : hw ? [
      `<div class="row"><span class="ic">${icon('desktop')}</span><span class="t"><b>This computer</b><span>${esc(OS[hw.os] || hw.os)}, ${esc(ARCH[hw.arch] || hw.arch)}${hw.memory_gb ? `, ${Math.round(hw.memory_gb)} GB memory` : ''}</span></span></div>`,
      ...hw.accelerators.map((a) => `<div class="row"><span class="ic ${a.kind !== 'cpu' ? 'gpu' : ''}">${icon(a.kind === 'cpu' ? 'cpu' : 'lightning')}</span>
        <span class="t"><b>${esc(a.name)}</b><span>${esc(kindLine(a))}</span></span>${a.experimental ? '<span class="chip orange">Experimental</span>' : ''}</div>`),
      `<div class="row"><span class="ic">${icon('hard-drives')}</span><span class="t"><b>Free disk space</b><span>Models take 1 to 24 GB each; the engine about ${planFor(hw.recommended)?.download_gb ?? 3} GB</span></span><span class="v">${hw.disk_free_gb != null ? `${Math.round(hw.disk_free_gb)} GB` : ''}</span></div>`,
    ].join('') : '';
  $('#body').innerHTML = `<div>
    <h1>${again ? 'Set up again' : 'Welcome to Bud Decision Studio'}</h1>
    <p class="lede">${again
      ? `Models currently run on ${esc(S.info.config?.device_name || 'this computer')}. Choose again to move them to another processor, or to repair the engine.`
      : 'Run open decision models on this computer. Give a model a situation and a few questions, and it returns a probability for every answer, usually in milliseconds.'}</p>
    <p class="lede" style="margin-top:8px">Setup checks this computer, installs the engine that runs the models, and then lets you pick which models to download. There is nothing else to install.</p>
    ${S.info.can_move_to_applications ? `<div class="note move">${icon('arrow-square-out')}<span><b>Move to Applications first?</b> The app is running from ${/Volumes/.test(location.href) ? 'the disk image' : 'your downloads'}. Moving it keeps it in Launchpad and the Dock.</span><button class="btn" id="moveapp">Move to Applications</button></div>` : ''}
    <div class="section-title">${hw ? 'Found on this computer' : S.hwError ? 'Checking this computer failed' : 'Checking this computer'}</div>
    ${S.hwError ? `<div class="note error">${icon('warning')}<span>${esc(S.hwError)}</span></div>` : `<div class="group">${rows}</div>`}
    ${(hw?.notes || []).map((n) => `<div class="note warn">${icon('warning')}<span>${esc(n)}</span></div>`).join('')}
  </div>`;
  $('#moveapp')?.addEventListener('click', async (e) => {
    e.currentTarget.disabled = true; e.currentTarget.textContent = 'Moving';
    try { await invoke('move_to_applications'); } catch (err) { e.currentTarget.textContent = msg(err); }
  });
  foot({
    note: hw ? '' : S.hwError ? '' : 'Checking the processors and graphics',
    next: S.hwError ? () => location.reload() : () => go('device'),
    nextLabel: S.hwError ? 'Try again' : 'Continue',
    nextDisabled: !hw && !S.hwError,
  });
}

function kindLine(a) {
  const parts = [KIND[a.kind] || 'Processor'];
  if (a.kind === 'nvidia' && a.cuda) parts.push(`CUDA ${a.cuda}`);
  if (a.kind === 'cpu' && a.cores) parts.push(`${a.cores} cores`);
  if (a.memory_gb) parts.push(`${Math.round(a.memory_gb)} GB ${a.unified_memory ? 'shared ' : ''}memory`);
  return parts.join(', ');
}
const planFor = (id) => S.hw?.plans?.[id];

// ------------------------------------------------------------------ 2. where models run
function what(a) {
  const big = (a.memory_gb || 0) >= 30;
  switch (a.kind) {
    case 'nvidia': return big ? 'Fastest. Runs every model, including Jev-Omni for images, audio and video.' : `Fastest. Runs every model that fits in its ${Math.round(a.memory_gb || 0)} GB.`;
    case 'apple': return big ? 'Fast. Uses the Mac\'s GPU through Metal and runs every model, including Jev-Omni.' : 'Fast. Uses the Mac\'s GPU through Metal; the largest models need more memory than this Mac has.';
    case 'intel': return 'Faster than the processor alone. Uses the Intel GPU through oneAPI.';
    case 'amd': return 'Uses ROCm. Experimental: some models may not load.';
    default: return 'Works on any computer. Small models answer in about a second; large models are slow, and Jev-Omni needs a GPU.';
  }
}

const WHERE = { nvidia: 'NVIDIA GPU', apple: 'Apple GPU', intel: 'Intel GPU', amd: 'AMD GPU', cpu: 'Processor only' };
function hardwareLine(a) {
  const parts = [a.name];
  if (a.kind === 'nvidia' && a.cuda) parts.push(`CUDA ${a.cuda}`);
  if (a.kind === 'cpu' && a.cores) parts.push(`${a.cores} cores`);
  if (a.memory_gb) parts.push(`${Math.round(a.memory_gb)} GB ${a.unified_memory ? 'shared ' : ''}memory`);
  return parts.join(', ');
}

function device() {
  const hw = S.hw;
  $('#body').innerHTML = `<div>
    <h1>Where should models run?</h1>
    <p class="lede">A GPU answers much faster. The processor works everywhere and is fine for the small models. Setup installs the engine built for your choice; you can change it later by running setup again.</p>
    <div class="choices" role="radiogroup" aria-label="Where models run">
      ${hw.accelerators.map((a) => {
        const p = planFor(a.id) || {};
        return `<button class="choice" role="radio" aria-checked="${a.id === S.device}" data-id="${a.id}" ${p.error ? 'disabled' : ''}>
          <span class="radio"></span><span class="ic ${a.kind !== 'cpu' ? 'gpu' : ''}">${icon(a.kind === 'cpu' ? 'cpu' : 'lightning')}</span>
          <span><span class="name">${WHERE[a.kind] || 'GPU'}${a.id === hw.recommended ? '<span class="chip violet">Recommended</span>' : ''}${a.experimental ? '<span class="chip orange">Experimental</span>' : ''}</span>
            <span class="kind">${esc(hardwareLine(a))}</span>
            <span class="what">${esc(what(a))}</span>
            <span class="size">${p.error ? esc(p.error) : `Installs ${esc(p.torch_label)} and the model libraries, about ${p.download_gb} GB.`}</span></span></button>`;
      }).join('')}
    </div>
  </div>`;
  const pick = (id) => { S.device = id; document.querySelectorAll('.choice').forEach((c) => c.setAttribute('aria-checked', String(c.dataset.id === id))); };
  document.querySelectorAll('.choice').forEach((c) => {
    c.onclick = () => pick(c.dataset.id);
    c.onkeydown = (e) => {
      if (!['ArrowDown', 'ArrowUp'].includes(e.key)) return;
      const all = [...document.querySelectorAll('.choice:not([disabled])')];
      const n = all[(all.indexOf(c) + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length];
      pick(n.dataset.id); n.focus(); e.preventDefault();
    };
  });
  const free = hw.disk_free_gb;
  foot({ note: free != null ? `${Math.round(free)} GB free on this computer` : '', back: () => go('welcome'), next: () => runInstall(), nextLabel: 'Install' });
}

// ------------------------------------------------------------------ 3. install
const STEPS = [['python', 'Prepare Python'], ['torch', 'Install PyTorch'], ['libs', 'Install the model libraries'], ['models', 'Install the model packages'], ['verify', 'Check the device']];
let unlisten = null, ticker = null;

async function runInstall() {
  Object.assign(S, { log: [], progress: 0, stepId: 'python', stepsSeen: [], error: null, result: null, started: Date.now(), label: 'Preparing' });
  go('install');
  unlisten ??= await T.event.listen('install', (e) => onEvent(e.payload));
  clearInterval(ticker); ticker = setInterval(() => { if (S.step === 'install' && !S.error) $('#elapsed') && ($('#elapsed').textContent = elapsed()); }, 1000);
  try {
    S.result = await invoke('install_engine', { device: S.device });
    S.progress = 1; clearInterval(ticker);
    go('ready');
  } catch (e) {
    S.error = msg(e); clearInterval(ticker);
    install();
  }
}

function onEvent(ev) {
  if (ev.type === 'step') { S.stepId = ev.id; S.label = ev.label; S.progress = ev.progress; if (!S.stepsSeen.includes(ev.id)) S.stepsSeen.push(ev.id); }
  if (ev.type === 'log' || ev.type === 'error') { S.log.push(ev.line ?? ev.message); if (S.log.length > 400) S.log.splice(0, S.log.length - 400); }
  if (S.step === 'install') paintInstall();
}

const elapsed = () => { const s = Math.round((Date.now() - S.started) / 1000); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`; };

function install() {
  const p = planFor(S.device) || {};
  $('#body').innerHTML = `<div>
    <h1>${S.error ? 'Setup did not finish' : 'Installing the engine'}</h1>
    <p class="lede">${S.error ? 'Nothing was changed outside the app\'s own folder. Check the details below, then try again.' : `Downloading ${esc(p.torch_label || 'PyTorch')} and the model libraries, about ${p.download_gb || 3} GB. This takes a few minutes on a fast connection; you can leave it running.`}</p>
    <div class="progress-head"><b id="plabel"></b><span id="elapsed">${elapsed()}</span></div>
    <span class="bar ${S.error ? 'failed' : 'working'}" id="pbar"><i></i></span>
    <ul class="checklist" id="checks"></ul>
    ${S.error ? `<div class="note error">${icon('warning')}<span>${esc(S.error)}</span></div>` : ''}
    <details class="log" ${S.error ? 'open' : ''}><summary>Details</summary><pre id="log"></pre></details>
  </div>`;
  foot(S.error
    ? { back: () => go('device'), next: () => runInstall(), nextLabel: 'Try again', note: `<button class="btn" id="copylog">${icon('copy')}Copy details</button>` }
    : { note: 'Keep the app open until setup finishes.' });
  // The log already ends with the error when the installer reported it; add it only when it came from elsewhere.
  $('#copylog')?.addEventListener('click', () => navigator.clipboard?.writeText([...S.log, ...(S.log.at(-1) === S.error ? [] : [S.error])].join('\n')));
  paintInstall();
}

function paintInstall() {
  const at = STEPS.findIndex(([id]) => id === S.stepId);
  $('#plabel').textContent = S.error ? 'Stopped' : S.label || 'Preparing';
  $('#pbar i').style.width = `${Math.max(3, Math.round(S.progress * 100))}%`;
  $('#checks').innerHTML = STEPS.map(([id, label], i) => `<li class="${i < at ? 'done' : i === at ? (S.error ? 'failed' : 'active') : ''}"><span class="mark"></span>${label}</li>`).join('');
  const log = $('#log');
  if (log) {
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.textContent = S.log.slice(-250).join('\n');
    if (stick) log.scrollTop = log.scrollHeight;
  }
}

// ------------------------------------------------------------------ 4. ready
function ready() {
  const c = S.result || {};
  $('#body').innerHTML = `<div>
    <div class="done-mark" aria-hidden="true"></div>
    <h1>Bud Decision Studio is ready</h1>
    <p class="lede">Installed in ${elapsed()}.</p>
    <div class="section-title">Set up for</div>
    <div class="group">
      <div class="row"><span class="ic ${c.device !== 'cpu' ? 'gpu' : ''}">${icon(c.device === 'cpu' ? 'cpu' : 'lightning')}</span><span class="t"><b>Models run on ${esc(c.device_name)}</b><span>${c.device === 'cpu' ? 'The processor' : 'The GPU'}, with PyTorch ${esc(c.torch)}</span></span></div>
    </div>
    <div class="next-up"><span class="ic">${icon('cloud-arrow-down')}</span><span><b>Next, choose your models</b>
      <p>The studio opens with a short list of models picked for this computer. Tick the ones you want; they download one at a time in the background while you explore.</p></span></div>
  </div>`;
  foot({ next: openStudio, nextLabel: 'Open Bud Decision Studio' });
}

async function openStudio() {
  const n = $('#next');
  n.disabled = true; n.textContent = 'Starting';
  try { await invoke('start_studio'); } catch (e) {
    $('#setup').hidden = true; $('#splash').hidden = false; splashError(msg(e));
  }
}

// ------------------------------------------------------------------ preview without Tauri
function mockTauri() {
  const q = new URLSearchParams(location.search);
  const HW = {
    gb10: { os: 'linux', arch: 'aarch64', memory_gb: 121.6, disk_free_gb: 96, recommended: 'cuda', notes: [],
      accelerators: [{ id: 'cuda', kind: 'nvidia', name: 'NVIDIA GB10', memory_gb: 121.6, unified_memory: true, cuda: '13.0' }, { id: 'cpu', kind: 'cpu', name: 'Cortex-X925 + Cortex-A725', memory_gb: 121.6, cores: 20 }],
      plans: { cuda: { torch_label: 'PyTorch 2.11.0 for CUDA 13.0', download_gb: 4.4 }, cpu: { torch_label: 'PyTorch 2.11.0 for the CPU', download_gb: 1.5 } } },
    mac: { os: 'macos', arch: 'aarch64', memory_gb: 36, disk_free_gb: 212, recommended: 'mps', notes: [],
      accelerators: [{ id: 'mps', kind: 'apple', name: 'Apple M3 Pro', memory_gb: 36, unified_memory: true }, { id: 'cpu', kind: 'cpu', name: 'Apple M3 Pro', memory_gb: 36, cores: 12 }],
      plans: { mps: { torch_label: 'PyTorch 2.11.0 with Apple Metal (MPS)', download_gb: 1.6 }, cpu: { torch_label: 'PyTorch 2.11.0 for the CPU', download_gb: 1.5 } } },
    intel: { os: 'windows', arch: 'x86_64', memory_gb: 32, disk_free_gb: 310, recommended: 'xpu', notes: [],
      accelerators: [{ id: 'xpu', kind: 'intel', name: 'Intel Arc Graphics', memory_gb: 32, unified_memory: true }, { id: 'cpu', kind: 'cpu', name: 'Intel Core Ultra 7 155H', memory_gb: 32, cores: 22 }],
      plans: { xpu: { torch_label: 'PyTorch 2.11.0 for Intel GPUs (XPU)', download_gb: 3 }, cpu: { torch_label: 'PyTorch 2.11.0 for the CPU', download_gb: 1.5 } } },
    cpu: { os: 'windows', arch: 'x86_64', memory_gb: 16, disk_free_gb: 120, recommended: 'cpu', notes: [],
      accelerators: [{ id: 'cpu', kind: 'cpu', name: 'Intel Core i5-1235U', memory_gb: 16, cores: 12 }],
      plans: { cpu: { torch_label: 'PyTorch 2.11.0 for the CPU', download_gb: 1.5 } } },
  };
  const hw = HW[q.get('hw')] || HW.gb10;
  const listeners = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  return {
    event: { listen: async (_n, fn) => { listeners.push(fn); return () => {}; } },
    core: {
      invoke: async (cmd, args) => {
        if (cmd === 'app_info') return { version: '0.1.0', installed: q.get('installed') === '1', config: { device_name: hw.accelerators[0].name }, unattended: q.get('auto'), can_move_to_applications: q.get('hw') === 'mac' };
        if (cmd === 'detect_hardware') { await sleep(+(q.get('delay') || 900)); return hw; }
        if (cmd === 'install_engine') {
          const emit = (ev) => listeners.forEach((f) => f({ payload: ev }));
          const p = hw.plans[args.device];
          const steps = [['python', 'Preparing Python 3.12', 0], ['torch', `Installing ${p.torch_label}`, 0.05], ['libs', 'Installing the model libraries', 0.6], ['models', 'Installing the model packages', 0.88], ['verify', `Checking ${hw.accelerators.find((a) => a.id === args.device).name}`, 0.95]];
          const stopAt = q.get('stop');
          for (const [id, label, progress] of steps) {
            emit({ type: 'step', id, label, progress });
            for (let k = 0; k < 4; k++) { emit({ type: 'log', line: `Resolved ${12 + k} packages in ${40 + k * 7}ms` }); await sleep(+(q.get('speed') || 350)); }
            if (stopAt === id) throw new Error(q.get('fail') || { python: 'Could not create the Python environment.', torch: 'PyTorch could not be installed. Check the internet connection and try again.', libs: 'The model libraries could not be installed.', models: 'The model packages could not be installed.', verify: 'The device could not be used.' }[id]);
          }
          const a = hw.accelerators.find((x) => x.id === args.device);
          return { device: args.device === 'rocm' ? 'cuda' : args.device, device_name: a.name, torch: '2.11.0' };
        }
        if (cmd === 'start_studio') { await sleep(1200); if (q.get('startfail')) throw new Error('The studio did not start.\n\nTraceback (most recent call last):\n  ...\nModuleNotFoundError: No module named \'torch\''); return 'http://127.0.0.1:8420/'; }
        return null;
      },
    },
  };
}
