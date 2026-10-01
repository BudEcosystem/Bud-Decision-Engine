/* Bud Decision Studio product page: hardware-aware downloads, the particle field, the Playground replay, and the
 * interactive figures. No dependencies. Content lives in data.js (window.BUD). */
(() => {
  'use strict';
  const D = window.BUD;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)').matches;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pct = (p) => (p >= 0.995 ? '>99%' : p < 0.005 ? '<1%' : `${Math.round(p * 100)}%`);
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  const I = (name, size = 20) => window.budIcon(name, size);
  const ICON = {
    mac: I('apple-logo'), windows: I('windows-logo'), linux: I('linux-logo'), device: I('desktop'),
    dl: I('download-simple', 18), act: I('check-circle', 13), ask: I('user', 13),
  };

  // ================================================================== copy to clipboard
  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = Object.assign(document.createElement('textarea'), { value: text });
      ta.style.cssText = 'position:fixed;opacity:0'; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch { /* nothing else to try */ }
      ta.remove();
    }
    if (btn) {
      const lab = (btn.matches('span') ? btn : $('span', btn)) || btn;
      const was = lab.textContent; lab.textContent = 'Copied'; btn.classList.add('ok');
      setTimeout(() => { lab.textContent = was; btn.classList.remove('ok'); }, 1600);
    }
  }

  function toast(html) {
    let t = $('.toast');
    if (!t) {
      t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status');
      document.body.appendChild(t);
    }
    t.innerHTML = html; t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 7000);
  }

  // ================================================================== hardware detection
  function gpuInfo() {
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return null;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const raw = String((ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) || '');
      const vend = String((ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)) || '');
      const lose = gl.getExtension('WEBGL_lose_context'); if (lose) lose.loseContext();
      let name = raw;
      const angle = raw.match(/^ANGLE \((.*)\)$/);
      if (angle) { const parts = angle[1].split(', '); name = parts.length > 1 ? parts[1] : parts[0]; }
      name = name.replace(/ANGLE Metal Renderer:\s*/i, '').replace(/\s*\(0x[0-9a-f]+\)/ig, '').replace(/\s*Direct3D.*$/i, '')
        .replace(/\/PCIe.*$/i, '').replace(/\s*OpenGL.*$/i, '').replace(/,?\s*Unspecified Version/i, '').replace(/^Mesa\s+/i, '')
        .replace(/\s*\([^)]*(LLVM|DRM|radeonsi)[^)]*\)/ig, '').replace(/\((R|TM|tm)\)/g, '').replace(/^NVIDIA Tegra (?=NVIDIA)/i, '').replace(/\s*\([A-Z0-9]{2,5}\)$/, '').replace(/\s+/g, ' ').trim();
      const all = `${raw} ${vend}`;
      const software = /SwiftShader|llvmpipe|softpipe|Software|Basic Render/i.test(all);
      let vendor = null;
      if (/NVIDIA|GeForce|Quadro|RTX|Tesla|GB10/i.test(all)) vendor = 'nvidia';
      else if (/Apple/i.test(all)) vendor = 'apple';
      else if (/AMD|Radeon|\bATI\b/i.test(all)) vendor = 'amd';
      else if (/Intel/i.test(all)) vendor = 'intel';
      if (/^Apple GPU$/i.test(name)) name = '';   // Safari hides the model
      return { raw, name, vendor: software ? null : vendor, software };
    } catch { return null; }
  }

  async function detect() {
    const ua = navigator.userAgent;
    const uad = navigator.userAgentData;
    let os = 'unknown'; let arch = null; let archSure = false;
    let mobile = /iPhone|iPad|iPod|Android/i.test(ua) || !!(uad && uad.mobile) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (/Windows/i.test(ua)) os = 'windows';
    else if (/Macintosh|Mac OS X/i.test(ua)) os = 'mac';
    else if (/Linux|X11|CrOS/i.test(ua) && !/Android/i.test(ua)) os = 'linux';
    if (uad && uad.getHighEntropyValues) {
      try {
        const h = await uad.getHighEntropyValues(['architecture', 'bitness', 'platform']);
        if (h.platform === 'Windows') os = 'windows'; else if (h.platform === 'macOS') os = 'mac';
        else if (h.platform === 'Linux' || h.platform === 'Chrome OS') os = 'linux';
        if (h.architecture === 'arm') { arch = 'arm64'; archSure = true; } else if (h.architecture === 'x86') { arch = 'x64'; archSure = true; }
      } catch { /* hints refused; fall back to the user agent */ }
    }
    if (!arch) {
      if (/aarch64|arm64|armv8/i.test(ua)) { arch = 'arm64'; archSure = true; }
      // Chromium freezes Linux user agents at x86_64, so only Firefox's is trustworthy there.
      else if (/x86_64|x64|Win64|amd64|WOW64/i.test(ua)) { arch = 'x64'; archSure = os !== 'linux' || /Firefox/.test(ua); }
    }
    const gpu = gpuInfo();
    if (os === 'mac') {
      // Safari reports "Apple GPU" on every Mac; only an explicit Intel or AMD renderer means an Intel Mac.
      if (gpu && /Intel|AMD|Radeon/i.test(gpu.raw) && !/Apple/i.test(gpu.raw)) { arch = 'x64'; archSure = true; } else { arch = 'arm64'; archSure = true; }
    }
    if (os === 'linux' && gpu && /GB10|Tegra|Orin|Mali|Adreno|V3D|Asahi/i.test(gpu.raw)) { arch = 'arm64'; archSure = true; }
    const distro = /Ubuntu|Debian|Mint|Pop!_OS|elementary/i.test(ua) ? 'deb' : /Fedora|Red Hat|openSUSE|SUSE|CentOS|Rocky|Alma/i.test(ua) ? 'rpm' : null;
    if (os === 'unknown' && !mobile) os = 'unknown';
    return { os, arch: arch || (os === 'mac' ? 'arm64' : 'x64'), archSure, mobile, gpu, distro };
  }

  function engineFor(d) {
    const g = d.gpu; const v = g && g.vendor;
    if (d.mobile || d.os === 'unknown') return { kind: null, text: '' };
    if (d.os === 'mac' && d.arch !== 'arm64') return { kind: null, text: '' };
    if (d.os === 'mac' && d.arch === 'arm64') return { kind: 'apple', text: 'Models will run on the Apple GPU through Metal.' };
    if (v === 'nvidia') return { kind: 'nvidia', text: 'Models will run on the GPU through CUDA.' };
    if (v === 'intel' && /Arc/i.test(g.raw)) return { kind: 'intel', text: 'Models will run on the Intel GPU.' };
    if (v === 'amd' && d.os === 'linux') return { kind: 'amd', text: 'Models can run on the GPU through ROCm, which is experimental.' };
    if (!g || !v) return { kind: null, text: 'On first run the app checks your graphics and picks where models run.' };
    return { kind: 'cpu', text: 'Models will run on the processor; small ones answer in about a second.' };
  }

  // ================================================================== release data
  const KINDS = [
    { id: 'dmg', os: 'mac', arch: 'arm64', re: /\.dmg$/i, name: 'Disk image', ext: '.dmg', after: 'dmg' },
    { id: 'exe', os: 'windows', arch: 'x64', re: /x64-setup\.exe$/i, name: 'Installer', ext: '.exe', after: 'exe' },
    { id: 'msi', os: 'windows', arch: 'x64', re: /x64.*\.msi$/i, name: 'MSI package', ext: '.msi', after: 'msi' },
    { id: 'deb-x64', os: 'linux', arch: 'x64', re: /_amd64\.deb$/i, name: 'Ubuntu, Debian', ext: '.deb', after: 'deb' },
    { id: 'rpm-x64', os: 'linux', arch: 'x64', re: /\.x86_64\.rpm$/i, name: 'Fedora, openSUSE', ext: '.rpm', after: 'rpm' },
    { id: 'appimage-x64', os: 'linux', arch: 'x64', re: /_(amd64|x86_64)\.AppImage$/i, name: 'Any Linux', ext: 'AppImage', after: 'appimage' },
    { id: 'deb-arm64', os: 'linux', arch: 'arm64', re: /_arm64\.deb$/i, name: 'Ubuntu, Debian', ext: '.deb', after: 'deb' },
    { id: 'rpm-arm64', os: 'linux', arch: 'arm64', re: /\.aarch64\.rpm$/i, name: 'Fedora, openSUSE', ext: '.rpm', after: 'rpm' },
    { id: 'appimage-arm64', os: 'linux', arch: 'arm64', re: /_(aarch64|arm64)\.AppImage$/i, name: 'Any Linux', ext: 'AppImage', after: 'appimage' },
  ];
  const GROUPS = [
    { title: 'macOS', sub: 'Apple Silicon', icon: 'mac', ids: ['dmg'] },
    { title: 'Windows', sub: '10 and 11', icon: 'windows', ids: ['exe', 'msi'] },
    { title: 'Linux', sub: 'x64', icon: 'linux', ids: ['deb-x64', 'rpm-x64', 'appimage-x64'] },
    { title: 'Linux', sub: 'ARM64, NVIDIA GB10', icon: 'linux', ids: ['deb-arm64', 'rpm-arm64', 'appimage-arm64'] },
  ];
  const AFTER = {
    dmg: ['Open the .dmg and drag Bud Decision Studio to Applications.', 'Open the app. If macOS says the developer is unidentified, right-click it and choose Open once.'],
    exe: ['Run the installer. If SmartScreen warns you, choose More info, then Run anyway.', 'Open Bud Decision Studio from the Start menu.'],
    msi: ['Run the package, or deploy it with your management tools.', 'Open Bud Decision Studio from the Start menu.'],
    deb: ['Install it: <code>sudo apt install ./Bud*.deb</code>', 'Open Bud Decision Studio from your applications menu, or run <code>bud-decision-studio</code>.'],
    rpm: ['Install it: <code>sudo dnf install ./Bud*.rpm</code>', 'Open Bud Decision Studio from your applications menu, or run <code>bud-decision-studio</code>.'],
    appimage: ['Make it runnable: <code>chmod +x Bud*.AppImage</code>', 'Open it. It adds itself to your applications menu.'],
  };
  const SETUP_STEPS = ['Setup checks your computer, asks where models should run, and installs the matching engine by itself.', 'Tick the models you want. They download one at a time in the background.'];

  const mb = (b) => `${Math.round(b / 1e6)} MB`;

  function fallbackRelease() {
    const fb = D.RELEASE_FALLBACK;
    return {
      tag: fb.tag, date: fb.date, html: `https://github.com/${D.repo}/releases/tag/${fb.tag}`,
      assets: fb.assets.map(([name, size]) => ({ name, size, url: `https://github.com/${D.repo}/releases/download/${fb.tag}/${name}` })),
    };
  }
  function cachedRelease() {
    try { const c = sessionStorage.getItem('bud-release'); return c ? JSON.parse(c) : null; } catch { return null; }
  }
  // The latest release, from GitHub. The page never waits for it: it renders from the built-in copy first.
  async function fetchRelease() {
    try {
      const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 8000);
      const r = await fetch(`https://api.github.com/repos/${D.repo}/releases/latest`, { signal: ctl.signal, headers: { Accept: 'application/vnd.github+json' } });
      clearTimeout(timer);
      if (!r.ok) return null;
      const j = await r.json();
      const rel = { tag: j.tag_name, date: (j.published_at || '').slice(0, 10), html: j.html_url,
        assets: (j.assets || []).map((a) => ({ name: a.name, size: a.size, url: a.browser_download_url })) };
      if (!KINDS.some((k) => rel.assets.some((a) => k.re.test(a.name)))) return null;
      try { sessionStorage.setItem('bud-release', JSON.stringify(rel)); } catch { /* storage blocked */ }
      return rel;
    } catch { return null; }
  }

  const assetFor = (rel, id) => { const k = KINDS.find((x) => x.id === id); const a = k && rel.assets.find((x) => k.re.test(x.name)); return a ? { ...a, kind: k } : null; };

  function plan(d, rel) {
    const osName = { mac: 'macOS', windows: 'Windows', linux: 'Linux' }[d.os];
    const g = d.gpu && d.gpu.name && d.gpu.vendor ? d.gpu.name : '';
    const eng = engineFor(d);
    if (d.mobile) {
      return { mobile: true, title: 'Bud Decision Studio is a desktop app', detail: 'Open this page on a Mac, Windows or Linux computer to download it, or pick a file from the list.', button: 'Copy the link to this page', icon: ICON.device, eng };
    }
    if (d.os === 'mac' && d.arch === 'x64') {
      return { unsupported: true, title: 'Intel Macs aren\'t supported', detail: 'PyTorch no longer supports Intel Macs. Use a Mac with Apple Silicon, or a Windows or Linux computer.', icon: ICON.mac, eng };
    }
    let primary; let alts = [];
    if (d.os === 'mac') primary = 'dmg';
    else if (d.os === 'windows') { primary = 'exe'; alts = ['msi']; }
    else if (d.os === 'linux') {
      const a = d.arch === 'arm64' ? 'arm64' : 'x64';
      const pkgs = d.distro === 'rpm' ? [`rpm-${a}`, `deb-${a}`] : [`deb-${a}`, `rpm-${a}`];
      primary = pkgs[0]; alts = [pkgs[1], `appimage-${a}`];
    }
    const asset = primary && assetFor(rel, primary);
    if (!asset) return { title: 'Choose your computer', detail: 'Pick a download from the list.', icon: ICON.device, eng };
    const archLabel = d.os === 'linux' ? (d.arch === 'arm64' ? 'ARM64' : 'x64') : d.os === 'mac' ? 'Apple Silicon' : 'x64';
    let who;
    if (d.os === 'mac') who = g ? `a Mac with ${g.replace(/^Apple\s+/, 'Apple ')}` : 'a Mac with Apple Silicon';
    else who = `${osName} on ${archLabel}${g ? ` with ${(/Graphics$/i.test(g) ? '' : /^(NVIDIA|Intel|AMD|Apple|[AEIOU])/i.test(g) ? 'an ' : 'a ') + g}` : ''}`;
    const title = d.os === 'mac' ? 'macOS, Apple Silicon' : d.os === 'windows' ? 'Windows 10 and 11' : `Linux, ${archLabel}`;
    let note = '';
    if (d.os === 'windows' && d.arch === 'arm64') note = ' This is an x64 build.';
    if (d.os === 'linux' && !d.archSure) note = ' On an ARM computer such as an NVIDIA GB10, use the ARM64 files.';
    return {
      osName, archLabel, who, title, asset, alts: alts.map((id) => assetFor(rel, id)).filter(Boolean), eng, note,
      icon: ICON[d.os] || ICON.device, cmdTab: d.os === 'windows' ? 'win' : 'unix',
    };
  }

  function renderDownloads(d, rel) {
    const p = plan(d, rel);
    const version = rel.tag.replace(/^v/, '');
    // release pill
    const pill = $('[data-release-text]');
    if (pill) pill.textContent = `Version ${version} for macOS, Windows and Linux`;
    $$('[data-release-pill], [data-release-notes]').forEach((a) => { a.href = rel.html; });
    const ver = $('[data-dl-version]'); if (ver) ver.textContent = `Version ${version}${rel.date ? `, ${new Date(`${rel.date}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}` : ''}`;

    // hero, nav and final buttons
    const heroBtn = $('[data-dl-primary]'); const finalBtn = $('[data-dl-final]'); const navBtn = $('[data-dl-nav]');
    const setBtn = (btn, label, href, icon) => {
      if (!btn) return;
      const l = $('[data-dl-label]', btn); if (l) l.textContent = label;
      const i = $('[data-dl-icon]', btn); if (i) i.innerHTML = icon || '';
      btn.href = href;
    };
    if (p.asset) {
      const label = `Download for ${p.osName}`;
      setBtn(heroBtn, label, p.asset.url, p.icon); setBtn(finalBtn, label, p.asset.url, p.icon);
      [heroBtn, finalBtn].forEach((b) => b && b.setAttribute('data-download', p.asset.kind.after));
      const meta = $('[data-dl-meta]');
      if (meta) meta.innerHTML = `Version ${esc(version)} for ${esc(p.archLabel)}, ${esc(p.asset.kind.ext)}, ${mb(p.asset.size)}. <a href="#all-downloads">Other downloads</a>`;
    } else {
      setBtn(heroBtn, p.mobile ? 'Get it for your computer' : 'Choose your download', '#download', ICON.dl);
      setBtn(finalBtn, p.mobile ? 'Get it for your computer' : 'Choose your download', '#download', ICON.dl);
      const meta = $('[data-dl-meta]'); if (meta) meta.textContent = p.mobile ? 'Bud Decision Studio runs on macOS, Windows and Linux.' : p.detail;
    }
    if (navBtn) navBtn.textContent = 'Download';

    // download section, main card
    $('[data-dl-icon-lg]').innerHTML = p.icon;
    $('[data-dl-title]').textContent = p.title;
    $('[data-dl-detail]').textContent = p.asset ? `${p.who.charAt(0).toUpperCase()}${p.who.slice(1)}. ${p.eng.text}${p.note}` : p.detail;
    const main = $('[data-dl-main]'); const mainLabel = $('[data-dl-main-label]');
    if (p.asset) {
      main.href = p.asset.url; main.setAttribute('data-download', p.asset.kind.after);
      mainLabel.textContent = `Download ${p.asset.kind.ext === 'AppImage' ? 'the AppImage' : `the ${p.asset.kind.ext} ${p.asset.kind.id === 'exe' ? 'installer' : 'file'}`}`;
      $('[data-dl-file]').textContent = `${p.asset.name.replace(/\./g, '.​')}, ${mb(p.asset.size)}`;
    } else if (p.mobile) {
      mainLabel.textContent = p.button; main.href = '#';
      main.onclick = (e) => { e.preventDefault(); copyText(location.href.split('#')[0], mainLabel); };
    } else {
      mainLabel.textContent = 'See all downloads'; main.href = '#all-downloads';
    }
    const alt = $('[data-dl-alt]');
    alt.innerHTML = p.alts && p.alts.length
      ? `<span>Also for this computer:</span>${p.alts.map((a) => `<a href="${esc(a.url)}" data-download="${a.kind.after}">${esc(a.kind.name)} (${esc(a.kind.ext)}), ${mb(a.size)}</a>`).join('')}`
      : '';

    // after-download steps
    const afterKind = p.asset ? p.asset.kind.after : 'dmg';
    renderAfter(afterKind, !p.asset);

    // one-line command tab
    selectOneLine(p.cmdTab || 'unix');

    // all downloads, one column per platform
    $('[data-dl-list]').innerHTML = GROUPS.map((g) => {
      const rows = g.ids.map((id) => assetFor(rel, id)).filter(Boolean);
      if (!rows.length) return '';
      return `<div class="dl-col"><h3>${ICON[g.icon]}${esc(g.title)} <small>${esc(g.sub)}</small></h3>${rows.map((a) => `
        <a class="dl-row${p.asset && p.asset.kind.id === a.kind.id ? ' you' : ''}" href="${esc(a.url)}" data-download="${a.kind.after}">
          <span><b>${esc(a.kind.name)}</b><small>${esc(a.kind.ext)}</small></span>
          <span class="sz">${mb(a.size)}</span>${ICON.dl}
        </a>`).join('')}</div>`;
    }).join('');

    // the hardware strip and the machine diagram name this computer
    $$('[data-hw-kind].you').forEach((r) => r.classList.remove('you'));
    if (p.eng && p.eng.kind) { const row = $(`[data-hw-kind="${p.eng.kind}"]`); if (row) row.classList.add('you'); }
    const gpuLabel = $('[data-machine-gpu]');
    if (gpuLabel && d.gpu && d.gpu.vendor && d.gpu.name && !d.mobile) gpuLabel.textContent = d.gpu.name;
    return p;
  }

  function renderAfter(kind, generic) {
    const ol = $('[data-after]'); if (!ol) return;
    const steps = generic ? ['Open the file you downloaded and install it the usual way for your computer.'] : AFTER[kind] || [];
    ol.innerHTML = [...steps, ...SETUP_STEPS].map((x) => `<li>${x}</li>`).join('');
  }

  const ONE_LINE = {
    unix: { cmd: `curl -fsSL https://raw.githubusercontent.com/${'BudEcosystem/Bud-Decision-Engine'}/main/get.sh | sh`,
      note: 'Downloads the right build, installs it and opens the app. On Linux it uses your package manager when it can, otherwise the AppImage for your user only.' },
    win: { cmd: `irm https://raw.githubusercontent.com/${'BudEcosystem/Bud-Decision-Engine'}/main/get.ps1 | iex`,
      note: 'Run it in PowerShell. It installs for your user only, with no administrator rights, adds a Start menu shortcut and opens the app.' },
  };
  function selectOneLine(which) {
    $$('[data-ol]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.ol === which)));
    $('[data-ol-cmd]').textContent = ONE_LINE[which].cmd;
    $('[data-ol-note]').textContent = ONE_LINE[which].note;
  }
  $$('[data-ol]').forEach((b) => b.addEventListener('click', () => selectOneLine(b.dataset.ol)));
  $('[data-copy-ol]').addEventListener('click', (e) => copyText($('[data-ol-cmd]').textContent, e.currentTarget));

  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-download]');
    if (!a || !/^https:/.test(a.href)) return;
    renderAfter(a.dataset.download, false);
    toast(`Downloading <b>${esc(decodeURIComponent(a.href.split('/').pop()))}</b>. <a href="#download">What to do next</a>`);
  });

  // ================================================================== particles
  // The Bud mark: seven blocks on a 32-unit grid, in the logo's three violets.
  const MARK = [[0, 0, 10, 10, 0], [12, 0, 9, 10, 0], [23, 0, 9, 21, 1], [0, 12, 10, 20, 1], [12, 12, 9, 9, 1], [12, 23, 9, 9, 2], [23, 23, 9, 9, 2]];
  const COLORS = ['#D9BAF9', '#B785F4', '#8C33EF', '#F4F1FA'];
  const DEPTH = [{ s: 1.3, a: 0.32 }, { s: 2.1, a: 0.6 }, { s: 3, a: 0.95 }];
  function markPoints(step) {
    const pts = [];
    for (const [x, y, w, h, c] of MARK) {
      for (let yy = y + step / 2; yy < y + h; yy += step) for (let xx = x + step / 2; xx < x + w; xx += step) pts.push([xx / 32, yy / 32, c]);
    }
    return pts;
  }
  const flow = (x, y, t) => (Math.sin(x * 0.0016 + t * 0.00021) * 1.7 + Math.cos(y * 0.0021 - t * 0.00017) * 1.3 + Math.sin((x - y) * 0.0009 + t * 0.0001)) * 1.25;

  function Field(canvas, opts) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const o = { markStep: 0.8, ambient: 1 / 5200, intro: false, homeMark: false, ...opts };
    let W = 0; let H = 0; let dpr = 1; let n = 0; let m = 0;
    let x; let y; let vx; let vy; let tx; let ty; let sx; let sy; let delay; let dur; let col; let dep; let swirl;
    let mode = 'free'; let modeAt = 0; let raf = 0; let visible = true; let last = 0;
    let mark = { cx: 0, cy: 0, size: 0 };
    const mouse = { x: -1e4, y: -1e4 };
    let pts = [];
    let flash = 0;

    function layoutMark() {
      const r = o.markBox();
      mark = r;
      for (let i = 0; i < m; i++) {
        tx[i] = r.cx + (pts[i][0] - 0.5) * r.size;
        ty[i] = r.cy + (pts[i][1] - 0.5) * r.size;
      }
    }
    function size() {
      const r = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 1.75);
      W = r.width; H = r.height;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    function init() {
      size();
      const small = W < 700;
      pts = (o.intro || o.homeMark) ? markPoints(small ? o.markStep * 1.45 : o.markStep) : [];
      m = pts.length;
      const amb = Math.round(clamp(W * H * o.ambient, 60, small ? 180 : 520));
      n = m + amb;
      x = new Float32Array(n); y = new Float32Array(n); vx = new Float32Array(n); vy = new Float32Array(n);
      tx = new Float32Array(n); ty = new Float32Array(n); sx = new Float32Array(n); sy = new Float32Array(n);
      delay = new Float32Array(n); dur = new Float32Array(n); swirl = new Float32Array(n);
      col = new Uint8Array(n); dep = new Uint8Array(n);
      for (let i = 0; i < n; i++) {
        x[i] = Math.random() * W; y[i] = Math.random() * H;
        vx[i] = (Math.random() - 0.5) * 0.4; vy[i] = (Math.random() - 0.5) * 0.4;
        const r = Math.random();
        col[i] = i < m ? pts[i][2] : r < 0.3 ? 0 : r < 0.66 ? 1 : r < 0.9 ? 2 : 3;
        dep[i] = Math.random() < 0.45 ? 0 : Math.random() < 0.65 ? 1 : 2;
      }
      if (m) layoutMark();
    }
    function startIntro(now) {
      mode = 'form'; modeAt = now;
      const R = Math.max(W, H) * 0.62;
      for (let i = 0; i < m; i++) {
        const a = Math.random() * Math.PI * 2; const rr = R * (0.55 + Math.random() * 0.6);
        sx[i] = mark.cx + Math.cos(a) * rr; sy[i] = mark.cy + Math.sin(a) * rr;
        delay[i] = Math.random() * 300 + (pts[i][1] * 120); dur[i] = 700 + Math.random() * 350;
        swirl[i] = (Math.random() - 0.5) * 220;
        x[i] = sx[i]; y[i] = sy[i];
      }
    }
    function release(now) {
      mode = 'free'; modeAt = now; flash = 1;
      for (let i = 0; i < m; i++) {
        const dx = x[i] - mark.cx; const dy = y[i] - mark.cy; const d = Math.hypot(dx, dy) || 1;
        const sp = 3 + Math.random() * 9;
        vx[i] = (dx / d) * sp + (Math.random() - 0.5) * 2; vy[i] = (dy / d) * sp + (Math.random() - 0.5) * 2;
      }
      if (o.onRelease) o.onRelease();
    }

    function step(now) {
      raf = 0;
      if (!visible || document.hidden) { last = 0; return; }
      const dt = last ? clamp((now - last) / 16.67, 0.2, 2.5) : 1; last = now;
      const t = now;
      ctx.clearRect(0, 0, W, H);

      if (mode === 'form') {
        let doneAll = true;
        for (let i = 0; i < m; i++) {
          const p = clamp((t - modeAt - delay[i]) / dur[i], 0, 1);
          if (p < 1) doneAll = false;
          const e = easeOut(p);
          const dx = tx[i] - sx[i]; const dy = ty[i] - sy[i]; const d = Math.hypot(dx, dy) || 1;
          const bend = Math.sin(Math.PI * e) * swirl[i];
          x[i] = sx[i] + dx * e + (-dy / d) * bend; y[i] = sy[i] + dy * e + (dx / d) * bend;
        }
        if (doneAll) { mode = 'hold'; modeAt = t; }
      } else if (mode === 'hold') {
        for (let i = 0; i < m; i++) { x[i] = tx[i] + Math.sin(t * 0.004 + i) * 0.35; y[i] = ty[i] + Math.cos(t * 0.0035 + i * 1.3) * 0.35; }
        if (t - modeAt > o.hold) release(t);
      }

      // physics for everything that is free
      const start = (mode === 'free' || o.homeMark) ? 0 : m;
      const R = 150; const R2 = R * R;
      for (let i = start; i < n; i++) {
        const home = o.homeMark && i < m;
        let ax; let ay;
        if (home) {
          ax = (tx[i] - x[i]) * 0.018; ay = (ty[i] - y[i]) * 0.018;
          ax += Math.sin(t * 0.0012 + i * 0.7) * 0.004; ay += Math.cos(t * 0.001 + i) * 0.004;
        } else {
          const a = flow(x[i], y[i], t);
          ax = Math.cos(a) * 0.03; ay = Math.sin(a) * 0.03 - 0.003;
        }
        const dx = x[i] - mouse.x; const dy = y[i] - mouse.y; const d2 = dx * dx + dy * dy;
        if (d2 < R2 && d2 > 0.01) {
          const d = Math.sqrt(d2); const f = (1 - d / R) * (home ? 1.6 : 0.7);
          ax += (dx / d) * f + (-dy / d) * f * 0.35; ay += (dy / d) * f + (dx / d) * f * 0.35;
        }
        const damp = home ? 0.88 : 0.965;
        vx[i] = (vx[i] + ax * dt) * Math.pow(damp, dt); vy[i] = (vy[i] + ay * dt) * Math.pow(damp, dt);
        x[i] += vx[i] * dt; y[i] += vy[i] * dt;
        if (!home) {
          if (x[i] < -20) x[i] = W + 20; else if (x[i] > W + 20) x[i] = -20;
          if (y[i] < -20) y[i] = H + 20; else if (y[i] > H + 20) y[i] = -20;
        }
      }

      // light behind the mark as it releases
      if (flash > 0.01 && m) {
        const g = ctx.createRadialGradient(mark.cx, mark.cy, 0, mark.cx, mark.cy, mark.size * 1.6);
        g.addColorStop(0, `rgba(183,133,244,${0.35 * flash})`); g.addColorStop(1, 'rgba(140,51,239,0)');
        ctx.fillStyle = g; ctx.fillRect(0, 0, W, H); flash *= Math.pow(0.93, dt);
      }

      // draw, one path per color and depth
      ctx.globalCompositeOperation = 'lighter';
      ctx.lineCap = 'square';
      const markDot = Math.max(2, (mark.size / 32) * (W < 700 ? o.markStep * 1.45 : o.markStep) * 0.62);
      for (let c = 0; c < 4; c++) {
        for (let k = 0; k < 4; k++) {
          ctx.beginPath(); let any = false;
          for (let i = 0; i < n; i++) {
            if (col[i] !== c) continue;
            const inMark = i < m && (mode !== 'free' || o.homeMark);
            if (k === 3 ? !inMark : (inMark || dep[i] !== k)) continue;
            const sp = Math.abs(vx[i]) + Math.abs(vy[i]);
            const tail = inMark && mode !== 'free' ? 0 : Math.min(sp * 2.2, 26);
            const nx = sp > 0.001 ? vx[i] / sp : 0; const ny = sp > 0.001 ? vy[i] / sp : 0;
            ctx.moveTo(x[i] - nx * tail, y[i] - ny * tail); ctx.lineTo(x[i] + 0.01, y[i]);
            any = true;
          }
          if (!any) continue;
          if (k === 3) { ctx.lineWidth = markDot; ctx.globalAlpha = 1; } else { ctx.lineWidth = DEPTH[k].s; ctx.globalAlpha = DEPTH[k].a; }
          ctx.strokeStyle = COLORS[c]; ctx.stroke();
        }
      }
      ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
      raf = requestAnimationFrame(step);
    }
    function kick() { if (!raf && visible && !document.hidden) raf = requestAnimationFrame(step); }

    init();
    if (o.intro) startIntro(performance.now());
    const ro = new ResizeObserver(() => { const w = W; size(); if (m) layoutMark(); if (Math.abs(W - w) > 200) { /* keep particles */ } });
    ro.observe(canvas);
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; kick(); }, { rootMargin: '100px' }).observe(canvas);
    document.addEventListener('visibilitychange', kick);
    const host = o.pointerHost || canvas.parentElement;
    if (finePointer) {
      host.addEventListener('pointermove', (e) => { const r = canvas.getBoundingClientRect(); mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; });
      host.addEventListener('pointerleave', () => { mouse.x = -1e4; mouse.y = -1e4; });
    }
    kick();
    return { release: () => { if (mode !== 'free') release(performance.now()); }, relayout: () => { if (m) layoutMark(); } };
  }

  // A burst of particles that flies from one element to others, on the canvas laid over the app window.
  function Sparks(canvas) {
    const ctx = canvas.getContext('2d');
    let W = 0; let H = 0; let parts = []; let raf = 0;
    function size() {
      const r = canvas.getBoundingClientRect(); const dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = r.width; H = r.height; canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    new ResizeObserver(size).observe(canvas);
    function frame(now) {
      raf = 0; ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'lighter'; ctx.lineCap = 'round';
      parts = parts.filter((p) => now - p.t0 < p.dur + p.delay + 260);
      for (const p of parts) {
        const tt = (now - p.t0 - p.delay) / p.dur;
        if (tt < 0) continue;
        if (tt >= 1) { // a small flash where it lands
          const f = 1 - (now - p.t0 - p.delay - p.dur) / 260;
          ctx.globalAlpha = Math.max(0, f) * 0.7; ctx.fillStyle = p.c;
          ctx.beginPath(); ctx.arc(p.ex, p.ey, 2 + (1 - f) * 6, 0, Math.PI * 2); ctx.fill();
          continue;
        }
        const e = easeInOut(tt); const e0 = easeInOut(Math.max(0, tt - 0.09));
        const bx = (u) => (1 - u) * (1 - u) * p.sx + 2 * (1 - u) * u * p.cx + u * u * p.ex;
        const by = (u) => (1 - u) * (1 - u) * p.sy + 2 * (1 - u) * u * p.cy + u * u * p.ey;
        ctx.globalAlpha = 0.95; ctx.strokeStyle = p.c; ctx.lineWidth = p.s;
        ctx.beginPath(); ctx.moveTo(bx(e0), by(e0)); ctx.lineTo(bx(e), by(e)); ctx.stroke();
      }
      ctx.globalAlpha = 1; ctx.globalCompositeOperation = 'source-over';
      if (parts.length) raf = requestAnimationFrame(frame);
    }
    return {
      burst(fromEl, toEls) {
        if (!W) size();
        const cr = canvas.getBoundingClientRect(); const f = fromEl.getBoundingClientRect();
        const now = performance.now();
        toEls.forEach((el, j) => {
          const r = el.getBoundingClientRect();
          for (let k = 0; k < 26; k++) {
            const sx = f.left - cr.left + f.width * (0.2 + Math.random() * 0.6); const sy = f.top - cr.top + f.height * 0.5;
            const ex = r.left - cr.left + r.width * (0.25 + Math.random() * 0.6); const ey = r.top - cr.top + r.height * (0.3 + Math.random() * 0.4);
            const mx = (sx + ex) / 2; const my = Math.min(sy, ey) - 60 - Math.random() * 90;
            parts.push({ sx, sy, ex, ey, cx: mx + (Math.random() - 0.5) * 160, cy: my, t0: now, delay: j * 70 + Math.random() * 140, dur: 520 + Math.random() * 260,
              c: COLORS[Math.floor(Math.random() * 3)], s: 1.6 + Math.random() * 1.8 });
          }
        });
        if (!raf) raf = requestAnimationFrame(frame);
      },
    };
  }

  // ================================================================== hero
  const hero = $('.hero');
  const heroTitle = $('.hero-title');
  let heroReady = false;
  function markHeroReady() { if (heroReady) return; heroReady = true; hero.classList.add('ready'); setTimeout(() => replay.start(0), reduce ? 0 : 1000); }

  let heroField = null;
  if (!reduce) {
    heroField = Field($('.hero-canvas'), {
      intro: true, hold: 300, markStep: 0.82, ambient: 1 / 4200, pointerHost: hero,
      markBox: () => {
        const c = $('.hero-canvas').getBoundingClientRect(); const t = heroTitle.getBoundingClientRect();
        const size = Math.min(250, c.width * 0.46, Math.max(150, t.height * 0.95));
        return { cx: t.left - c.left + t.width / 2, cy: t.top - c.top + t.height / 2, size };
      },
      onRelease: markHeroReady,
    });
    if (!heroField) markHeroReady();
    // Scrolling or clicking during the intro skips it.
    const skip = () => heroField && heroField.release();
    addEventListener('wheel', skip, { once: true, passive: true }); addEventListener('touchstart', skip, { once: true, passive: true });
    addEventListener('keydown', skip, { once: true });
    setTimeout(markHeroReady, 3500);
  } else {
    markHeroReady();
  }

  const mock = $('.mock');

  // ================================================================== the Playground replay
  function certainty(top) {
    if (top >= 0.9) return { word: 'Very sure', cls: '' };
    if (top >= 0.75) return { word: 'Fairly sure', cls: '' };
    if (top >= 0.55) return { word: 'Leaning', cls: 'plain' };
    return { word: 'Unsure', cls: 'amber' };
  }
  const gateHTML = (top, th) => (top >= th
    ? `<span class="act">${ICON.act}Act automatically</span>`
    : `<span class="ask">${ICON.ask}Ask a human</span><span class="faint">below your ${Math.round(th * 100)}% threshold</span>`);

  function questionHTML(q) {
    let opts = '';
    if (q.type === 'choice') opts = q.options.map((o) => `<span>${esc(o)}</span>`).join('');
    if (q.type === 'score') opts = q.options.map((o) => `<span>${esc(o)}</span>`).join('<span class="sep">›</span>');
    if (q.type === 'noul') opts = '<span>yes</span><span>no</span>';
    return `<div class="mq"><div class="mq-h"><span class="qt" data-t="${q.type}">${esc(q.label)}</span><span class="mq-q">${esc(q.q)}</span></div><div class="mq-o">${opts}</div></div>`;
  }

  function figHTML(q, i, th = 0.9) {
    const c = certainty(q.top); const act = q.top >= th;
    let word; let p; let plot;
    if (q.type === 'choice') {
      const rows = q.options.map((o) => [o, q.probs[o]]).sort((a, b) => b[1] - a[1]);
      [word, p] = rows[0];
      plot = `<div class="hrows">${rows.map(([o, v], k) => `<div class="hrow${k === 0 ? ' top' : ''}"><span class="l">${esc(o)}</span><span class="track"><i class="bar" data-sx="${v}" ${k === 0 && !act ? 'style="background:var(--a-orange)"' : ''}></i><u class="thr" style="left:${th * 100}%"></u></span><span class="v">${pct(v)}</span></div>`).join('')}</div>`;
    } else if (q.type === 'score') {
      const top = q.probs.indexOf(Math.max(...q.probs)); word = q.options[top]; p = q.probs[top];
      const n = q.options.length; const left = ((q.score + 0.5) / n) * 100;
      plot = `<div class="cols" style="--n:${n}">${q.probs.map((v, k) => `<div class="col${k === top ? ` top${act ? ' v' : ''}` : ''}"><i data-sy="${v}"></i></div>`).join('')}<span class="ring" data-left="${left}%"></span></div>
        <div class="clabels" style="--n:${n}">${q.options.map((o, k) => `<span class="${k === top ? 'top' : ''}">${esc(o)}<b>${pct(q.probs[k])}</b></span>`).join('')}</div>`;
    } else {
      const yes = q.yes; word = yes >= 0.5 ? 'yes' : 'no'; p = Math.max(yes, 1 - yes);
      plot = `<div class="split${yes < 0.5 ? ' no-wins' : ''}" data-yes="${yes}"><span class="y"><em>yes ${pct(yes)}</em></span><span class="n"><em>no ${pct(1 - yes)}</em></span></div>`;
    }
    return `<article class="fig" data-fig>
      <div class="fig-h"><span class="fig-no">Fig. 1${'abc'[i]}</span><span class="fig-q">${esc(q.q)}</span><span class="sure ${c.cls}">${c.word}</span></div>
      <div class="fig-a"><b>${esc(word)}</b><span data-count="${Math.round(p * 100)}">0%</span></div>
      ${plot}
      <div class="gate"><span class="faint">Confidence ${q.top.toFixed(2)}</span>${gateHTML(q.top, th)}</div>
    </article>`;
  }

  function drawFig(el, instant) {
    const apply = () => {
      $$('[data-sx]', el).forEach((b) => { b.style.transform = `scaleX(${b.dataset.sx})`; });
      $$('[data-sy]', el).forEach((b) => { b.style.transform = `scaleY(${Math.max(0.012, +b.dataset.sy)})`; });
      $$('[data-left]', el).forEach((r) => { r.style.left = r.dataset.left; r.classList.add('on'); });
      $$('[data-yes]', el).forEach((s) => {
        const y = +s.dataset.yes; $('.y', s).style.flexBasis = `${y * 100}%`; $('.n', s).style.flexBasis = `${(1 - y) * 100}%`;
      });
    };
    const cnt = $('[data-count]', el);
    if (instant) { apply(); if (cnt) cnt.textContent = `${cnt.dataset.count}%`; return; }
    requestAnimationFrame(() => requestAnimationFrame(apply));
    if (cnt) countUp(cnt, +cnt.dataset.count, 800, '%');
  }
  function countUp(el, to, ms, suffix = '') {
    const t0 = performance.now();
    const tick = (now) => { const p = clamp((now - t0) / ms, 0, 1); el.textContent = `${Math.round(to * easeOut(p))}${suffix}`; if (p < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }

  const replay = (() => {
    const stateEl = $('[data-state]'); const stateBox = $('.m-state'); const qs = $('[data-questions]'); const figs = $('[data-figs]');
    const meta = $('[data-meta]'); const decide = $('[data-decide]'); const decideLabel = $('[data-decide-label]'); const cursor = $('.cursor');
    const tabs = $$('.scn'); const sparks = reduce ? null : Sparks($('.fx-canvas'));
    let token = 0; let onScreen = true; let started = false;
    new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; }, { threshold: 0.15 }).observe(mock);

    function selectTab(i, ms) {
      tabs.forEach((t, k) => {
        t.setAttribute('aria-selected', String(k === i)); t.tabIndex = k === i ? 0 : -1;
        const bar = $('i', t); bar.style.transition = 'none'; bar.style.transform = 'scaleX(0)';
        if (k === i && ms) { void bar.offsetWidth; bar.style.transition = `transform ${ms}ms linear`; bar.style.transform = 'scaleX(1)'; }
      });
    }
    function metaHTML(s, ms) {
      return `<b>Intern-Decision 4B</b> answered ${s.questions.length} questions in <span class="ms" data-ms>${ms}</span><span class="ms"> ms</span>, one pass, ${s.tokens} tokens`;
    }
    function placeCursor(el, dx = 0.5, dy = 0.5) {
      const m = mock.getBoundingClientRect(); const r = el.getBoundingClientRect();
      cursor.style.left = `${r.left - m.left + r.width * dx}px`; cursor.style.top = `${r.top - m.top + r.height * dy}px`;
    }
    function showStatic(i) {
      const s = D.SCENARIOS[i]; token++; selectTab(i, 0);
      stateEl.textContent = s.state; stateBox.classList.add('done');
      qs.innerHTML = s.questions.map(questionHTML).join(''); $$('.mq', qs).forEach((q) => q.classList.add('in'));
      figs.innerHTML = s.questions.map((q, k) => figHTML(q, k)).join('');
      $$('.fig', figs).forEach((f) => { f.classList.add('in'); drawFig(f, true); });
      meta.innerHTML = metaHTML(s, s.latency);
    }

    async function run(i) {
      const my = ++token; const alive = () => my === token;
      const s = D.SCENARIOS[i];
      const TOTAL = 9800;
      selectTab(i, TOTAL);
      // clear the previous answer
      $$('.fig', figs).forEach((f) => f.classList.remove('in'));
      await sleep(260); if (!alive()) return;
      figs.innerHTML = ''; meta.innerHTML = '&nbsp;'; qs.innerHTML = ''; stateEl.textContent = ''; stateBox.classList.remove('done');
      cursor.classList.remove('show');
      // the situation types itself
      for (let c = 0; c <= s.state.length; c += 3) { stateEl.textContent = s.state.slice(0, c); await sleep(14); if (!alive()) return; }
      stateEl.textContent = s.state;
      // the questions arrive
      for (const q of s.questions) {
        qs.insertAdjacentHTML('beforeend', questionHTML(q));
        const el = qs.lastElementChild; requestAnimationFrame(() => el.classList.add('in'));
        await sleep(190); if (!alive()) return;
      }
      stateBox.classList.add('done');
      // the pointer goes to Decide and presses it
      placeCursor(stateBox, 0.8, 0.7); cursor.style.transition = 'none'; void cursor.offsetWidth; cursor.style.transition = '';
      cursor.classList.add('show');
      await sleep(60); placeCursor(decide, 0.56, 0.62);
      await sleep(820); if (!alive()) return;
      cursor.classList.add('click'); decide.classList.add('press'); decideLabel.textContent = 'Deciding';
      await sleep(150); cursor.classList.remove('click');
      await sleep(120); decide.classList.remove('press'); if (!alive()) return;
      // answers: rendered first so the sparks know where to fly
      figs.innerHTML = s.questions.map((q, k) => figHTML(q, k)).join('');
      const figEls = $$('.fig', figs);
      if (sparks) sparks.burst(decide, figEls);
      meta.innerHTML = metaHTML(s, 0); countUp($('[data-ms]', meta), s.latency, 420);
      await sleep(430); if (!alive()) return;
      decideLabel.textContent = 'Decide';
      for (const f of figEls) { f.classList.add('in'); drawFig(f, false); await sleep(110); if (!alive()) return; }
      setTimeout(() => cursor.classList.remove('show'), 700);
      await sleep(5400); if (!alive()) return;
      while ((!onScreen || document.hidden) && alive()) await sleep(400);
      if (alive()) run((i + 1) % D.SCENARIOS.length);
    }

    tabs.forEach((t, k) => {
      t.addEventListener('click', () => (reduce ? showStatic(k) : run(k)));
      t.addEventListener('keydown', (e) => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return; e.preventDefault(); const j = (k + d + tabs.length) % tabs.length; tabs[j].focus(); tabs[j].click();
      });
    });
    return { start(i) { if (started) return; started = true; if (reduce) showStatic(i); else run(i); } };
  })();

  // ================================================================== makers strip
  (() => {
    const one = D.MAKERS.map((mk) => `<span class="maker"><img src="${mk.logo}" alt="" width="34" height="34" loading="lazy">${esc(mk.name)}</span>`).join('');
    $('[data-marquee]').innerHTML = one + `<span class="dup" style="display:contents">${one}</span>`;
    $('[data-makers-list]').innerHTML = D.MAKERS.map((mk) => `<li>${esc(mk.name)}</li>`).join('');
  })();

  // ================================================================== chat model vs decision model
  (() => {
    const box = $('[data-versus]'); const stream = $('[data-stream]'); const bars = $('[data-vsbars]');
    const s = D.SCENARIOS[0].questions[0];
    const rows = s.options.map((o) => [o, s.probs[o]]).sort((a, b) => b[1] - a[1]);
    bars.innerHTML = rows.map(([o, p], k) => `<div class="vbar${k === 0 ? ' top' : ''}"><span class="l">${esc(o)}</span><span class="t"><i data-s="${p}"></i></span><span class="v">${pct(p)}</span></div>`).join('');
    const text = 'This looks like a billing issue: the customer says they were charged twice for March on invoice #4411 and wants the duplicate refunded. They also mention cancelling their plan, so it may be worth looping in the account team as well. I would suggest routing it to billing first, and then';
    const words = text.split(' ');
    let played = false;
    const show = async () => {
      if (played) return; played = true;
      if (reduce) { stream.textContent = `${text}…`; stream.classList.add('done'); $$('i', bars).forEach((b) => { b.style.transform = `scaleX(${b.dataset.s})`; }); return; }
      stream.textContent = '';
      setTimeout(() => { $$('i', bars).forEach((b, k) => setTimeout(() => { b.style.transform = `scaleX(${b.dataset.s})`; }, k * 40)); countUp($('[data-vslat]'), 98, 380); }, 350);
      for (let k = 0; k < words.length; k++) { stream.textContent += (k ? ' ' : '') + words[k]; await sleep(95 + Math.random() * 90); }
      stream.textContent += '…'; stream.classList.add('done');
    };
    new IntersectionObserver(([e]) => { if (e.isIntersecting) show(); }, { threshold: 0.35 }).observe(box);
  })();

  // ================================================================== how it works: steps and the act threshold
  (() => {
    const hv = $('[data-hv]'); const steps = $$('.step'); const rowsEl = $('[data-gate-rows]'); const sum = $('[data-gate-sum]');
    const qs = D.SCENARIOS[0].questions;
    const rows = [
      { l: 'billing', p: qs[0].top, q: 'department' },
      { l: 'today', p: qs[1].top, q: 'urgency' },
      { l: 'yes', p: qs[2].top, q: 'cancel threat' },
    ];
    function paint(th) {
      rowsEl.innerHTML = rows.map((r) => {
        const act = r.p >= th;
        return `<div class="grow${act ? '' : ' ask'}"><span class="l">${esc(r.l)}</span><span class="t"><i style="width:${r.p * 100}%"></i><u style="left:${th * 100}%"></u></span><span class="v">${pct(r.p)}</span><span class="g ${act ? 'act' : 'ask'}">${act ? 'Act automatically' : 'Ask a human'}</span></div>`;
      }).join('');
      const a = rows.filter((r) => r.p >= th).length;
      sum.innerHTML = a === rows.length ? `<b>All ${rows.length}</b> answers act automatically.` : a === 0 ? `<b>None</b> act automatically; all ${rows.length} go to a person.` : `<b>${a} of ${rows.length}</b> act automatically; ${rows.length - a} ${rows.length - a === 1 ? 'goes' : 'go'} to a person.`;
      $$('[data-th-out]').forEach((o) => { o.textContent = `${Math.round(th * 100)}%`; });
    }
    const slider = $('[data-thresh]');
    const setP = () => slider.style.setProperty('--p', `${((slider.value - slider.min) / (slider.max - slider.min)) * 100}%`);
    slider.addEventListener('input', () => { paint(slider.value / 100); setP(); });
    slider.addEventListener('focus', () => setAt(3));
    paint(0.9); setP();
    function setAt(k) { hv.dataset.at = String(k); steps.forEach((s, j) => s.classList.toggle('on', j === k)); }
    const wide = matchMedia('(min-width: 901px)');
    const io = new IntersectionObserver((es) => {
      if (!wide.matches) return;
      es.forEach((e) => { if (e.isIntersecting) setAt(+e.target.dataset.step); });
    }, { rootMargin: '-45% 0px -45% 0px' });
    steps.forEach((s) => io.observe(s));
    const sync = () => { if (!wide.matches) setAt(3); else setAt(+(hv.dataset.at || 0)); };
    wide.addEventListener('change', sync); hv.dataset.at = '0'; sync();
  })();

  // ================================================================== six question types: small multiples on one plate
  (() => {
    const hb = (rows, opts = {}) => rows.map(([l, p, top], k) => `<div class="tb${top ? ' top' : ''}${opts.rank ? ' rank' : ''}"><span>${opts.rank ? `<span class="n">${k + 1}</span>` : ''}${esc(l)}</span><span class="t"><i style="--s:${p}"></i>${opts.cut ? '<span class="cut"></span>' : ''}</span><span class="v">${pct(p)}</span></div>`).join('');
    const scale = [['calm', 0.02], ['mild', 0.08], ['annoyed', 0.21], ['angry', 0.52], ['furious', 0.17]];
    const avg = scale.reduce((a, [, p], k) => a + p * k, 0);
    const mu = 40; const sd = 11; const X = (v) => (v / 100) * 300;
    let line = ''; let area = 'M0 92';
    for (let v = 0; v <= 100; v += 2) { const yv = 92 - 80 * Math.exp(-((v - mu) ** 2) / (2 * sd * sd)); line += `${v ? 'L' : 'M'}${X(v).toFixed(1)} ${yv.toFixed(1)}`; area += `L${X(v).toFixed(1)} ${yv.toFixed(1)}`; }
    area += 'L300 92Z';
    const TYPES = [
      { name: 'Pick one', native: true, q: 'Which team owns this ticket?', define: 'a list of named options', get: 'a probability per option, and the winner',
        fig: hb([['billing', 0.92, 1], ['technical', 0.05], ['sales', 0.03]]) },
      { name: 'Rate on a scale', native: true, q: 'How upset is the customer?', define: '2 to 10 ordered levels', get: 'a probability per level, and the average position',
        fig: `<div class="tcols">${scale.map(([, p], k) => `<i class="${k === 3 ? 'top' : ''}" style="height:100%;--s:${p}"></i>`).join('')}<span class="ring" style="left:${((avg + 0.5) / 5) * 100}%"></span></div><div class="tcl">${scale.map(([l]) => `<span>${l}</span>`).join('')}</div>` },
      { name: 'Yes or no', native: true, q: 'The customer is asking for a refund.', define: 'a statement', get: 'the probability it is true',
        fig: '<div class="tyn" style="--yes:94%;--no:6%"><span class="y">yes 94%</span><span class="n"></span></div><div class="tyn-l"><span>true, 94%</span><span>false, 6%</span></div>' },
      { name: 'Pick any', native: false, q: 'Which topics does this message raise?', define: 'options and a cut-off', get: 'every option above the cut-off',
        fig: hb([['billing', 0.91, 1], ['cancellation', 0.88, 1], ['bug report', 0.64, 1], ['pricing', 0.12]], { cut: true }) },
      { name: 'Put in order', native: false, q: 'Which fix should we try first?', define: 'a list of options', get: 'the options from most to least likely',
        fig: hb([['restart', 0.58, 1], ['roll back', 0.27], ['scale up', 0.11], ['wait', 0.04]], { rank: true }) },
      { name: 'Estimate a number', native: false, q: 'How many seats will they buy?', define: 'the values it could take', get: 'a best estimate and an 80% range',
        fig: `<div class="tnum"><svg viewBox="0 0 300 96" preserveAspectRatio="none" aria-hidden="true"><rect class="range" x="${X(26)}" y="0" width="${X(54) - X(26)}" height="92"/><path class="area" d="${area}"/><path class="line" d="${line}"/><line class="best" x1="${X(mu)}" x2="${X(mu)}" y1="4" y2="92"/></svg></div><div class="tnum-l"><span>0</span><span>best <b>40</b>, 80% range <b>26 to 54</b></span><span>100</span></div>` },
    ];
    const grid = $('[data-types]');
    grid.innerHTML = TYPES.map((t, k) => `<figure class="mult">
      <div class="mult-h"><span class="fig-n">Fig. 2${'abcdef'[k]}</span><h3>${t.name}</h3><span class="origin${t.native ? ' native' : ''}">${t.native ? 'Native' : 'Studio-built'}</span></div>
      <p class="mult-q">${esc(t.q)}</p>
      <div class="mult-fig">${t.fig}</div>
      <figcaption>You define <b>${t.define}</b>. You get back <b>${t.get}</b>.</figcaption>
    </figure>`).join('');
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('drawn'); io.unobserve(e.target); } }), { threshold: 0.35 });
    $$('.mult', grid).forEach((t) => io.observe(t));
  })();

  // ================================================================== tour: one frame, one segmented control
  (() => {
    const tabsEl = $('[data-tour-tabs]'); const media = $('[data-shots]'); const cap = $('[data-tour-cap]'); const title = $('[data-shot-title]');
    const video = $('[data-video]'); const playBtn = $('[data-video-toggle]');
    video.dataset.for = 'demo';
    D.TOUR.forEach((t) => { if (!t.video) media.insertAdjacentHTML('beforeend', `<img src="${t.img}" alt="${esc(t.alt)}" width="2160" height="1350" loading="lazy" data-for="${t.id}">`); });
    tabsEl.innerHTML = D.TOUR.map((t, k) => `<button role="tab" id="tt-${t.id}" aria-controls="shots" aria-selected="${k === 0}" tabindex="${k === 0 ? 0 : -1}">${esc(t.title)}</button>`).join('');
    media.id = 'shots';
    const tabs = $$('[role="tab"]', tabsEl);
    let cur = 0; let userPaused = false; let inView = false;
    const syncPlay = () => {
      const on = !video.paused; media.classList.toggle('playing', on);
      playBtn.setAttribute('aria-label', on ? 'Pause the demo' : 'Play the demo');
      playBtn.innerHTML = on ? I('pause', 26) : I('play', 28);
    };
    video.addEventListener('play', syncPlay); video.addEventListener('pause', syncPlay);
    const tryPlay = () => { if (D.TOUR[cur].video && inView && !userPaused && !reduce) video.play().catch(() => {}); };
    function select(k) {
      cur = k; const t = D.TOUR[k];
      tabs.forEach((b, j) => { b.setAttribute('aria-selected', String(j === k)); b.tabIndex = j === k ? 0 : -1; });
      $$('[data-for]', media).forEach((el) => el.classList.toggle('on', el.dataset.for === t.id));
      media.setAttribute('aria-labelledby', `tt-${t.id}`);
      title.textContent = t.video ? 'Bud Decision Studio' : t.title;
      cap.innerHTML = `<span class="fig-n">Fig. 3</span>${esc(t.body)}`;
      playBtn.hidden = !t.video;
      if (t.video) tryPlay(); else video.pause();
    }
    tabs.forEach((b, k) => {
      b.addEventListener('click', () => select(k));
      b.addEventListener('keydown', (e) => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return; e.preventDefault(); const j = (k + d + tabs.length) % tabs.length; tabs[j].focus(); select(j);
      });
    });
    playBtn.addEventListener('click', () => { if (video.paused) { userPaused = false; video.play().catch(() => {}); } else { userPaused = true; video.pause(); } });
    video.addEventListener('click', () => playBtn.click());
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; if (inView) tryPlay(); else video.pause(); }, { threshold: 0.45 }).observe(media);
    $$('[data-play-demo]').forEach((a) => a.addEventListener('click', () => { userPaused = false; select(0); setTimeout(() => video.play().catch(() => {}), 700); }));
    select(0); syncPlay();
  })();

  // ================================================================== the machine diagram runs only while it is on screen
  (() => {
    const m = $('[data-machine]');
    if (!reduce) new IntersectionObserver(([e]) => m.classList.toggle('live', e.isIntersecting), { threshold: 0.3 }).observe(m);
  })();

  // ================================================================== models: a table and an inspector, after the app's own Models page
  const models = (() => {
    const list = $('[data-models]'); const table = $('[data-mtable]'); const insp = $('[data-insp]'); const capLabel = $('[data-cap-label]');
    const SCALE = 28;   // GB; the memory bars share one axis, a little past the largest model (26 GB)
    const SPEED_WORD = { instant: 'Instant', fast: 'Fast', moderate: 'Moderate', heavy: 'Heavy' };
    const READS_ICON = { text: 'text-t', images: 'image', audio: 'microphone', video: 'video-camera' };
    const gb = (v) => (v < 1 ? `${Math.round(v * 1000)} MB` : `${v % 1 ? v.toFixed(1) : v} GB`);
    list.innerHTML = D.MODELS.map((m, k) => `<button class="mrow" role="option" aria-selected="false" tabindex="-1" data-k="${k}" aria-label="${esc(`${m.name} by ${m.maker}, ${gb(m.mem)} of memory, reads ${m.reads.join(' and ')}`)}">
      <span class="mname"><img src="${m.logo}" alt="" width="28" height="28" loading="lazy"><span><b>${esc(m.name)}</b><small>${esc(m.maker)}</small></span></span>
      <span class="num">${esc(m.params)}</span>
      <span class="num">${gb(m.size)}</span>
      <span class="mmem"><span class="mbar"><i style="width:${(m.mem / SCALE) * 100}%"></i></span><span class="val">${gb(m.mem)}</span></span>
      <span class="mreads">${m.reads.map((r) => `<span class="${r !== 'text' ? 'm' : ''}">${I(READS_ICON[r], 17)}</span>`).join('')}</span>
    </button>`).join('');
    const rows = $$('.mrow', list);
    let filter = 'all'; let mem = 32; let sel = D.MODELS.findIndex((m) => m.id === 'intern-decision-4b');
    const fits = (m) => m.mem <= mem * 0.92;
    const test = {
      all: () => true,
      cpu: (m) => m.mem <= 2 && !m.gpu,
      media: (m) => m.reads.length > 1,
      lang: (m) => /Multilingual|100\+|major/i.test(m.langs),
      options: (m) => m.options >= 250,
    };
    function renderInsp() {
      const m = D.MODELS[sel];
      const fitText = fits(m) ? `Fits in the ${mem} GB you picked${m.gpu ? ', on a GPU' : ''}.` : `Needs about ${m.mem} GB, more than the ${mem} GB you picked.`;
      insp.innerHTML = `
        <div class="insp-top"><img src="${m.logo}" alt="" width="32" height="32"><span>${esc(m.maker)}</span>${m.badge ? `<span class="badge">${esc(m.badge)}</span>` : ''}</div>
        <h3>${esc(m.name)}</h3>
        <p class="tag">${esc(m.tagline)}</p>
        <p class="sum">${esc(m.summary)}</p>
        <dl>
          <div><dt>Parameters</dt><dd>${esc(m.params)}</dd></div>
          <div><dt>Download</dt><dd>${gb(m.size)}</dd></div>
          <div><dt>Memory when loaded</dt><dd>${gb(m.mem)}</dd></div>
          <div><dt>Speed</dt><dd>${SPEED_WORD[m.speed]}</dd></div>
          <div><dt>Options per question</dt><dd>Up to ${m.options}</dd></div>
          <div><dt>Languages</dt><dd title="${esc(m.langs)}">${esc(m.langs)}</dd></div>
        </dl>
        <p class="claim"><span>The maker's published result</span>${esc(m.metric)}</p>
        <p class="fitnote${fits(m) ? '' : ' no'}">${esc(fitText)}</p>
        <a class="card-link" href="https://huggingface.co/${esc(m.hf)}">Model card on Hugging Face ${I('arrow-square-out', 16)}</a>`;
    }
    function select(k, focus) {
      sel = k;
      rows.forEach((r, j) => { r.setAttribute('aria-selected', String(j === k)); r.tabIndex = j === k ? 0 : -1; });
      if (focus) rows[k].focus();
      renderInsp();
    }
    function apply() {
      rows.forEach((r, k) => { const m = D.MODELS[k]; r.hidden = !test[filter](m); r.classList.toggle('nofit', !fits(m)); });
      if (mem >= SCALE) { table.setAttribute('data-cap-off', ''); capLabel.textContent = ''; }
      else { table.removeAttribute('data-cap-off'); table.style.setProperty('--cap', `${(mem / SCALE) * 100}%`); capLabel.textContent = `against your ${mem} GB`; }
      if (rows[sel].hidden) { const f = rows.findIndex((r) => !r.hidden); if (f >= 0) sel = f; }
      select(sel, false);
    }
    rows.forEach((r, k) => {
      r.addEventListener('click', () => { select(k, false); if (matchMedia('(max-width: 1080px)').matches) insp.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' }); });
      r.addEventListener('keydown', (e) => {
        const d = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
        if (!d) return; e.preventDefault();
        const vis = rows.map((x, j) => j).filter((j) => !rows[j].hidden); const at = vis.indexOf(k);
        const next = vis[clamp(at + d, 0, vis.length - 1)]; select(next, true);
      });
    });
    $$('[data-filter]').forEach((b) => b.addEventListener('click', () => {
      filter = b.dataset.filter; $$('[data-filter]').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', String(x === b)); }); apply();
    }));
    const setMem = (v) => { mem = v; $$('[data-mem]').forEach((x) => { const on = +x.dataset.mem === v; x.classList.toggle('on', on); x.setAttribute('aria-pressed', String(on)); }); apply(); };
    $$('[data-mem]').forEach((b) => b.addEventListener('click', () => setMem(+b.dataset.mem)));
    setMem(32);
    return { setMem };
  })();

  // ================================================================== code examples
  function highlight(src, rules) {
    const re = new RegExp(rules.map((r) => `(${r[0].source})`).join('|'), 'gm');
    let out = ''; let last = 0; let m;
    while ((m = re.exec(src))) {
      if (!m[0]) { re.lastIndex++; continue; }
      out += esc(src.slice(last, m.index));
      const gi = m.slice(1).findIndex((g) => g !== undefined);
      out += `<span class="${rules[gi][1]}">${esc(m[0])}</span>`;
      last = m.index + m[0].length;
    }
    return out + esc(src.slice(last));
  }
  const STR = /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/;
  const KEY = /"(?:[^"\\\n]|\\.)*"(?=\s*:)/;
  const NUM = /\b\d+(?:\.\d+)?\b/;
  const RULES = {
    curl: [[/#.*$/, 'c'], [KEY, 'k'], [/"(?:[^"\\\n]|\\.)*"/, 's'], [/\bcurl\b/, 'f'], [/(?:^|\s)-{1,2}[a-zA-Z]+\b/, 'w'], [/https?:\/\/[^\s']+/, 's'], [NUM, 'n']],
    python: [[/#.*$/, 'c'], [STR, 's'], [/\b(?:from|import|print|None|True|False)\b/, 'w'], [/\b[A-Za-z_]\w*(?=\()/, 'f'], [NUM, 'n']],
    js: [[/\/\/.*$/, 'c'], [STR, 's'], [/\b(?:const|await|async|new|return)\b/, 'w'], [/\b[A-Za-z_]\w*(?=\s*:)/, 'k'], [/\b[A-Za-z_]\w*(?=\()/, 'f'], [NUM, 'n']],
    json: [[KEY, 'k'], [STR, 's'], [/\b(?:true|false|null)\b/, 'w'], [NUM, 'n']],
  };
  (() => {
    const code = $('[data-code]'); const tabs = $$('[data-code-tabs] [role="tab"]');
    let lang = 'curl';
    const show = (l) => { lang = l; code.innerHTML = highlight(D.CODE[l], RULES[l]); tabs.forEach((t) => { t.setAttribute('aria-selected', String(t.dataset.lang === l)); t.tabIndex = t.dataset.lang === l ? 0 : -1; }); };
    tabs.forEach((t, k) => {
      t.addEventListener('click', () => show(t.dataset.lang));
      t.addEventListener('keydown', (e) => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return; const j = (k + d + tabs.length) % tabs.length; tabs[j].focus(); show(tabs[j].dataset.lang); });
    });
    $('[data-copy-code]').addEventListener('click', (e) => copyText(D.CODE[lang], e.currentTarget));
    show('curl');
    $('[data-resp]').innerHTML = highlight(D.RESPONSE, RULES.json);
  })();

  // ================================================================== navigation
  (() => {
    const nav = $('#nav'); const btn = $('.menu-btn'); const menu = $('#menu');
    const top = document.createElement('div'); top.setAttribute('aria-hidden', 'true');
    top.style.cssText = 'position:absolute;top:0;left:0;width:1px;height:12px;pointer-events:none';
    document.body.prepend(top);
    new IntersectionObserver(([e]) => nav.classList.toggle('scrolled', !e.isIntersecting)).observe(top);
    btn.addEventListener('click', () => { const open = btn.getAttribute('aria-expanded') !== 'true'; btn.setAttribute('aria-expanded', String(open)); menu.hidden = !open; btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu'); });
    $$('a', menu).forEach((a) => a.addEventListener('click', () => { btn.setAttribute('aria-expanded', 'false'); menu.hidden = true; }));
  })();

  // ================================================================== final call: the mark, held together by springs
  if (!reduce) {
    const fc = $('.final-canvas');
    Field(fc, {
      homeMark: true, markStep: 0.95, ambient: 1 / 9000, pointerHost: $('.final'),
      markBox: () => { const r = fc.getBoundingClientRect(); const size = Math.min(170, r.width * 0.36); return { cx: r.width / 2, cy: 70 + size / 2, size }; },
    });
  }

  // ================================================================== downloads, once we know the computer
  (async () => {
    const d = await detect();
    let rel = cachedRelease() || fallbackRelease();
    const p = renderDownloads(d, rel);
    if (d.gpu && /GB10/i.test(d.gpu.raw)) models.setMem(128);
    else if (p && p.eng && p.eng.kind === 'cpu') models.setMem(16);
    const live = await fetchRelease();
    if (live && (live.tag !== rel.tag || live.assets.length !== rel.assets.length)) { rel = live; renderDownloads(d, rel); }
  })();
})();
