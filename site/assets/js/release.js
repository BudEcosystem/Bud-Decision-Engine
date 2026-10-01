/* The latest release of Bud Decision Studio, always read from GitHub in the visitor's browser. No version number is
 * written into the site: the product page and the docs both ask here.
 *
 * Two sources are asked at the same time:
 *   - GitHub's API: live and authoritative, but anonymous use is limited to 60 requests an hour per network address;
 *   - site/release.json in the repository, written by the release workflow the moment a release is published and
 *     read through raw.githubusercontent.com, which has no such limit.
 * The API's answer wins when it arrives; otherwise the newest of what the repository and this browser's memory hold.
 * The last answer is remembered here, so a page can show it at once and then refresh it. With no answer at all the
 * release is `null`, and pages link to GitHub's own "latest release" address, which GitHub resolves itself. */
(() => {
  const REPO = 'BudEcosystem/Bud-Decision-Engine';
  const KEY = 'bud-release-v2';
  const SOURCES = {
    api: `https://api.github.com/repos/${REPO}/releases/latest`,
    repo: `https://raw.githubusercontent.com/${REPO}/main/site/release.json`,
  };

  const parts = (tag) => String(tag || '').replace(/^v/, '').split(/[.+-]/).map((x) => parseInt(x, 10) || 0);
  // > 0 when a is the newer version, < 0 when b is, 0 when they are the same
  const compare = (a, b) => { const x = parts(a), y = parts(b); for (let i = 0; i < 3; i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d; } return 0; };
  const valid = (r) => !!r && typeof r.tag === 'string' && /^v?\d+\.\d+/.test(r.tag) && Array.isArray(r.assets) && r.assets.length > 0
    && r.assets.every((a) => a && typeof a.name === 'string' && typeof a.url === 'string');
  const tidy = (r) => ({ tag: r.tag, version: r.tag.replace(/^v/, ''), date: r.date || '',
    html: r.html || `https://github.com/${REPO}/releases/tag/${r.tag}`,
    assets: r.assets.map((a) => ({ name: a.name, size: Number(a.size) || 0, url: a.url })) });
  const SHAPE = {
    api: (j) => ({ tag: j.tag_name, date: (j.published_at || '').slice(0, 10), html: j.html_url,
      assets: (j.assets || []).map((a) => ({ name: a.name, size: a.size, url: a.browser_download_url })) }),
    repo: (j) => j,
  };

  function remembered() {
    try { const r = JSON.parse(localStorage.getItem(KEY) || 'null'); return valid(r) ? tidy(r) : null; } catch { return null; }
  }
  function remember(r) { try { localStorage.setItem(KEY, JSON.stringify(r)); } catch { /* storage blocked */ } }

  async function ask(source) {
    const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 8000);
    try {
      // no-cache: always check with GitHub. An unchanged answer costs nothing against the API's hourly limit.
      const r = await fetch(SOURCES[source], { signal: ctl.signal, cache: 'no-cache',
        headers: source === 'api' ? { Accept: 'application/vnd.github+json' } : {} });
      if (!r.ok) return null;
      const rel = SHAPE[source](await r.json());
      return valid(rel) ? tidy(rel) : null;
    } catch { return null; } finally { clearTimeout(timer); }
  }

  /* load(show): show(release, from) is called at once with what this browser remembers (or null), then again each
   * time GitHub gives a different answer. `from` is 'memory', 'repo' or 'api'. Resolves with the final release. */
  async function load(show) {
    let shown = remembered();
    const put = (rel, from) => {
      remember(rel);
      if (shown && rel.tag === shown.tag && rel.assets.length === shown.assets.length) return;   // nothing new to draw
      shown = rel;
      try { show(rel, from); } catch (e) { console.error(e); }
    };
    try { show(shown, 'memory'); } catch (e) { console.error(e); }
    let apiAnswered = false;
    const repo = ask('repo').then((rel) => {
      // the repository's copy never replaces something newer (it can be a few minutes behind), nor the API's answer
      if (rel && !apiAnswered && (!shown || compare(rel.tag, shown.tag) > 0)) put(rel, 'repo');
      return rel;
    });
    const api = ask('api').then((rel) => {
      if (rel) { apiAnswered = true; put(rel, 'api'); }      // authoritative: also when it is older (a withdrawn release)
      return rel;
    });
    await Promise.all([repo, api]);
    return shown;
  }

  window.BudRelease = { repo: REPO, latestPage: `https://github.com/${REPO}/releases/latest`,
    allPage: `https://github.com/${REPO}/releases`, load, compare };
})();
