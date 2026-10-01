/* Bud Decision Studio documentation: copy buttons, tabs (a choice of language or system applies to every tab group
 * and console of the same kind, and is remembered), the parameter trace (a parameter in the prose lights its line in
 * the console beside it), the page outline in the contents, the contents drawer on small screens, and search over
 * window.DOCS_INDEX (built by docs-src/build.py). No dependencies. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private window */ } } };
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const still = matchMedia('(prefers-reduced-motion: reduce)');
  $$('[data-kbd]').forEach((k) => { k.textContent = mac ? '⌘ K' : 'Ctrl K'; });

  // ------------------------------------------------------------------ copy
  // A console's code is one block per line, so its text is joined here rather than read from the screen.
  const codeText = (code) => {
    const lines = $$(':scope > .ln', code);
    return (lines.length ? lines.map((l) => l.textContent).join('\n') : code.innerText).replace(/\n$/, '');
  };
  async function copy(text, btn) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = Object.assign(document.createElement('textarea'), { value: text });
      ta.style.cssText = 'position:fixed;opacity:0'; document.body.append(ta); ta.select();
      try { document.execCommand('copy'); } catch { /* nothing else to try */ }
      ta.remove();
    }
    const label = $('span', btn);
    btn.classList.add('ok'); if (label) label.textContent = 'Copied';
    setTimeout(() => { btn.classList.remove('ok'); if (label) label.textContent = 'Copy'; }, 1600);
  }
  $$('[data-copy]').forEach((b) => b.addEventListener('click', () => {
    const box = b.closest('.code-block, .dc');
    const code = box.classList.contains('dc') ? $('.dc-panel:not([hidden]) code', box) : $('code', box);
    copy(codeText(code), b);
  }));

  // ------------------------------------------------------------------ tabs
  // A tab set is any [data-tabs] element: a :::tabs block or a console. Its own tabs and panels are the ones whose
  // nearest tab set is itself, so tab sets can sit inside each other.
  const own = (set, sel) => $$(sel, set).filter((el) => el.closest('[data-tabs]') === set);
  function select(set, label, remember, animate) {
    const btns = own(set, '[role="tab"]');
    const idx = btns.findIndex((b) => b.dataset.tab === label);
    if (idx < 0) return false;
    const panels = own(set, '[role="tabpanel"]');
    if (panels[idx]?.hidden === false && btns[idx].getAttribute('aria-selected') === 'true') return true;
    btns.forEach((b, i) => { b.setAttribute('aria-selected', String(i === idx)); b.tabIndex = i === idx ? 0 : -1; });
    panels.forEach((p, i) => {
      p.hidden = i !== idx;
      if (i === idx && animate && !still.matches) { p.classList.remove('enter'); void p.offsetWidth; p.classList.add('enter'); }
    });
    if (remember && set.dataset.group) store.set(`docs.tab.${set.dataset.group}`, label);
    return true;
  }
  const guessOS = () => (/Win/.test(navigator.platform || navigator.userAgent) ? 'Windows' : mac ? 'macOS' : 'Linux');
  $$('[data-tabs]').forEach((set) => {
    const g = set.dataset.group;
    const saved = g && (store.get(`docs.tab.${g}`) || (g === 'os' ? guessOS() : null));
    if (saved) select(set, saved, false, false);
    const list = own(set, '[role="tablist"]')[0]; if (!list) return;
    list.addEventListener('click', (e) => {
      const b = e.target.closest('[role="tab"]'); if (!b) return;
      const label = b.dataset.tab;
      const top = b.getBoundingClientRect().top;
      if (g) $$(`[data-tabs][data-group="${CSS.escape(g)}"]`).forEach((t) => select(t, label, true, t === set));
      else select(set, label, false, true);
      window.scrollBy(0, b.getBoundingClientRect().top - top);   // switching every group keeps the clicked tab in place
    });
    list.addEventListener('keydown', (e) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      const btns = own(set, '[role="tab"]'); const i = btns.indexOf(document.activeElement);
      const n = e.key === 'Home' ? btns[0] : e.key === 'End' ? btns[btns.length - 1]
        : btns[(i + (e.key === 'ArrowRight' ? 1 : -1) + btns.length) % btns.length];
      n.click(); n.focus(); e.preventDefault();
    });
  });

  // ------------------------------------------------------------------ the parameter trace
  // Pointing at (or focusing) a parameter row lights the lines of the section's console that set or return it:
  // "store": in JSON, store= in Python, store: in JavaScript, X-Basal-Template: in a header.
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const fieldRe = (param) => {
    const name = param.replace(/\[\]/g, '').split('.').pop().replace(/^-+/, '');
    return name ? new RegExp(`(["'])${reEsc(name)}\\1\\s*:|["']${reEsc(name)}\\s*:|(^|[\\s,{(])${reEsc(name)}\\s*[:=]|\\b${reEsc(name)}=`, 'i') : null;
  };
  $$('.dsec.has-code').forEach((row) => {
    const panes = $$('.dc pre.hl', row);
    // a row whose field appears in none of the section's examples (any language, or the response) is plain text
    $$('tr[data-param]', row).forEach((tr) => {
      const re = fieldRe(tr.dataset.param);
      if (!re || !panes.some((p) => $$('.ln', p).some((l) => re.test(l.textContent)))) {
        tr.removeAttribute('data-param'); tr.removeAttribute('tabindex');
      }
    });
    const clear = () => {
      panes.forEach((p) => { p.classList.remove('tracing'); $$('.traced', p).forEach((l) => l.classList.remove('traced')); });
      $$('tr.on', row).forEach((r) => r.classList.remove('on'));
    };
    const trace = (tr) => {
      clear();
      const re = fieldRe(tr.dataset.param); if (!re) return;
      let hit = false;
      // only the panes on screen: the selected language and the response
      panes.filter((p) => !p.closest('[hidden]')).forEach((pre) => {
        const lines = $$('.ln', pre).filter((l) => re.test(l.textContent));
        if (!lines.length) return;
        hit = true; pre.classList.add('tracing'); lines.forEach((l) => l.classList.add('traced'));
        // bring the first lit line into view inside its own scrolling pane (never the page)
        const y = lines[0].getBoundingClientRect().top - pre.getBoundingClientRect().top + pre.scrollTop;
        if (y < pre.scrollTop || y > pre.scrollTop + pre.clientHeight - 28) pre.scrollTo({ top: Math.max(0, y - pre.clientHeight / 3), behavior: still.matches ? 'auto' : 'smooth' });
      });
      if (hit && tr.tagName === 'TR') tr.classList.add('on');
    };
    $$('tr[data-param]', row).forEach((tr) => {
      tr.addEventListener('mouseenter', () => trace(tr));
      tr.addEventListener('focus', () => trace(tr));
      tr.addEventListener('mouseleave', clear);
      tr.addEventListener('blur', clear);
    });
    // a field named in a sentence lights its line too (pointer only; the table rows carry the keyboard path)
    $$('.dsec-text :is(p, li) > code', row).forEach((c) => {
      const name = c.textContent.trim();
      if (!/^[A-Za-z_][\w.-]{1,40}$/.test(name)) return;
      const re = fieldRe(name);
      if (!panes.some((p) => $$('.ln', p).some((l) => re.test(l.textContent)))) return;
      c.dataset.param = name; c.classList.add('trace-ref');
      c.addEventListener('mouseenter', () => trace(c));
      c.addEventListener('mouseleave', clear);
    });
  });

  // ------------------------------------------------------------------ the page outline follows the reading position
  const tocLinks = $$('.side-toc a');
  if (tocLinks.length) {
    const targets = tocLinks.map((a) => document.getElementById(decodeURIComponent(a.hash.slice(1)))).filter(Boolean);
    let ticking = false;
    const update = () => {
      ticking = false;
      const y = 120;
      let cur = null;
      for (const t of targets) { if (t.getBoundingClientRect().top - y <= 0) cur = t; else break; }
      if (innerHeight + scrollY >= document.body.scrollHeight - 4) cur = targets[targets.length - 1];
      tocLinks.forEach((a) => a.classList.toggle('on', !!cur && a.hash === `#${cur.id}`));
    };
    addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    update();
  }

  // ------------------------------------------------------------------ contents drawer (small screens)
  const side = $('#docs-side'); const sideBtn = $('.side-btn');
  if (side && sideBtn) {
    const scrim = $('.side-scrim');
    const setOpen = (open) => {
      side.classList.toggle('open', open); sideBtn.setAttribute('aria-expanded', String(open));
      if (scrim) scrim.hidden = !open;
    };
    scrim?.addEventListener('click', () => setOpen(false));
    sideBtn.addEventListener('click', () => setOpen(!side.classList.contains('open')));
    side.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
    document.addEventListener('click', (e) => { if (side.classList.contains('open') && !side.contains(e.target) && !sideBtn.contains(e.target)) setOpen(false); });
    const cur = $('a[aria-current="page"]', side);
    if (cur) side.scrollTop = Math.max(0, cur.offsetTop - side.clientHeight / 3);
  }

  // ------------------------------------------------------------------ the latest release, from GitHub
  // These pages are written for one version (window.DOCS_VERSION). The version beside the name, and the installer file
  // names and sizes in the text, follow the latest release as soon as GitHub answers (release.js), so the docs never
  // point at an old download. The changelog is history and stays as written.
  const built = window.DOCS_VERSION;
  let showing = built;
  // long inline code may wrap at its own seams, as the build marks it
  const setCode = (el, text) => {
    el.textContent = '';
    if (text.length < 25) { el.append(text); return; }
    text.replace(/([/._-])(?=[^/._-])/g, '$1\u0000').split('\u0000').forEach((part, i) => { if (i) el.append(document.createElement('wbr')); el.append(part); });
  };
  function applyRelease(rel) {
    if (!rel || !built) return;
    const v = rel.version;
    $$('[data-version]').forEach((el) => {
      el.textContent = v;
      el.title = v === built ? `Documentation for version ${v}` : `The latest release is ${v}. These pages were written for ${built}.`;
    });
    const prose = $('.prose');
    if (!prose || /(^|\/)changelog\.html$/.test(location.pathname)) return;
    if (v !== showing) {
      const old = reEsc(showing);
      const inCode = [[new RegExp(`(Bud\\.Decision\\.Studio[_-])${old}(?![0-9])`, 'g'), `$1${v}`], [new RegExp(`(--version v)${old}(?![0-9])`, 'g'), `$1${v}`]];
      $$('code', prose).filter((c) => !c.closest('pre')).forEach((c) => {
        const before = c.textContent; let after = before;
        for (const [re, to] of inCode) after = after.replace(re, to);
        if (after !== before) setCode(c, after);
      });
      const sentence = new RegExp(`(For version )${old}(?![0-9])`, 'g');
      const walker = document.createTreeWalker(prose, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue.includes(showing)) n.nodeValue = n.nodeValue.replace(sentence, `$1${v}`);
      showing = v;
    }
    // a table row that names a release file shows that file's size
    const sizes = new Map(rel.assets.map((a) => [a.name, a.size]));
    $$('tr', prose).forEach((tr) => {
      const name = $('td code', tr)?.textContent; const cell = tr.cells[1];
      if (name && sizes.get(name) && cell && /^[\d.]+\s*MB$/.test(cell.textContent.trim())) cell.textContent = `${(sizes.get(name) / 1e6).toFixed(1)} MB`;
    });
  }
  if (window.BudRelease) window.BudRelease.load(applyRelease);

  // ------------------------------------------------------------------ search
  const box = $('#search'); if (!box) return;
  const input = $('input', box); const results = $('.search-results', box);
  const root = window.DOCS_ROOT || './';
  let active = 0, shown = [], lastFocus = null;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const norm = (s) => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');
  function mark(text, words) {
    let out = esc(text);
    for (const w of words) if (w.length > 1) out = out.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig'), '<mark>$1</mark>');
    return out;
  }
  function snippet(text, words) {
    const t = norm(text); let at = -1;
    for (const w of words) { at = t.indexOf(w); if (at >= 0) break; }
    if (at < 0) return text.slice(0, 160);
    const start = Math.max(0, at - 60);
    return (start ? '… ' : '') + text.slice(start, start + 170);
  }
  // a section found by a field it defines says so first, since its opening text may not mention the field
  function why(e, words) {
    const t = norm(e.x), f = norm(e.f || '').split(' ');
    const w = words.find((x) => f.includes(x) && !t.includes(x));
    return w ? `<span class="sr-field">Field <code>${esc(w)}</code></span> ` : '';
  }
  function run(q) {
    const words = norm(q).split(/\s+/).filter(Boolean);
    if (!words.length) { results.innerHTML = ''; shown = []; return; }
    const scored = [];
    for (const e of window.DOCS_INDEX || []) {
      const t = norm(e.t), h = norm(e.h), x = norm(e.x), k = norm(e.k || '').split(' '), f = norm(e.f || '').split(' ');
      let s = 0, all = true;
      for (const w of words) {
        const inT = t.includes(w), inH = h.includes(w), inX = x.includes(w);
        if (!inT && !inH && !inX && !k.includes(w) && !f.includes(w)) { all = false; break; }
        const whole = new RegExp(`(^|[^a-z0-9_])${reEsc(w)}s?($|[^a-z0-9_])`);
        // a section that documents a field of that exact name (in the reference above all) outranks prose mentioning
        // it; a plain word such as "template" gets a smaller lift, so the page about it still comes first
        const ident = /[_.-]/.test(w);
        const named = f.includes(w) ? 12 : k.includes(w) ? (e.u.startsWith('api/') ? (ident ? 14 : 7) : (ident ? 8 : 3)) : 0;
        const seen = (x.match(new RegExp(`(^|[^a-z0-9_])${reEsc(w)}s?(?=$|[^a-z0-9_])`, 'g')) || []).length;
        s += named + Math.min(4, seen) * 1.5 + (inH ? (whole.test(h) ? 9 : 2) : 0) + (inT ? (whole.test(t) ? 7 : 2) : 0) + (inX ? (whole.test(x) ? 3 : 0.5) : 0)
          + (h.startsWith(w) || t.startsWith(w) ? 2 : 0);
      }
      if (all) scored.push({ e, s: s + (e.h ? 0 : 1) });
    }
    scored.sort((a, b) => b.s - a.s);
    shown = scored.slice(0, 12).map((x) => x.e);
    active = 0;
    results.innerHTML = shown.length ? shown.map((e, i) => `<a class="sr-item" role="option" aria-selected="${i === 0}" href="${root}${e.u}">
        <span class="sr-top"><span class="sr-t">${mark(e.h || e.t, words)}</span><span class="sr-s">${esc(e.h && e.t !== e.s ? `${e.t} · ${e.s}` : e.s)}</span></span>
        <span class="sr-x">${why(e, words)}${mark(snippet(e.x, words), words)}</span></a>`).join('')
      : `<p class="sr-empty">Nothing matches “${esc(q)}”. Try a shorter word, such as <b>template</b>, <b>history</b> or <b>install</b>.</p>`;
  }
  function move(d) {
    const items = $$('.sr-item', results); if (!items.length) return;
    active = (active + d + items.length) % items.length;
    items.forEach((it, i) => it.setAttribute('aria-selected', String(i === active)));
    items[active].scrollIntoView({ block: 'nearest' });
  }
  function open() { lastFocus = document.activeElement; box.hidden = false; input.value = ''; results.innerHTML = ''; setTimeout(() => input.focus(), 0); document.body.style.overflow = 'hidden'; }
  function close() { box.hidden = true; document.body.style.overflow = ''; lastFocus?.focus?.(); }
  $$('[data-search-open]').forEach((b) => b.addEventListener('click', open));
  input.addEventListener('input', () => run(input.value));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { move(1); e.preventDefault(); }
    if (e.key === 'ArrowUp') { move(-1); e.preventDefault(); }
    if (e.key === 'Enter') { const it = $$('.sr-item', results)[active]; if (it) { location.href = it.href; close(); } }
  });
  box.addEventListener('mousedown', (e) => { if (e.target === box) close(); });
  results.addEventListener('mousemove', (e) => {
    const it = e.target.closest('.sr-item'); if (!it) return;
    const items = $$('.sr-item', results); active = items.indexOf(it);
    items.forEach((x, i) => x.setAttribute('aria-selected', String(i === active)));
  });
  results.addEventListener('click', (e) => { if (e.target.closest('.sr-item')) setTimeout(close, 0); });
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
    if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) { e.preventDefault(); box.hidden ? open() : close(); }
    else if (e.key === '/' && !typing && box.hidden) { e.preventDefault(); open(); }
    else if (e.key === 'Escape' && !box.hidden) close();
  });
})();
