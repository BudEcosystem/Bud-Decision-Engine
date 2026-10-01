"""Checks that the website takes the release from GitHub in every situation, with GitHub's answers stood in for.

The product page and the docs hold no version number: site/assets/js/release.js reads the latest release from GitHub's
API and from site/release.json in the repository. This serves site/ on a free port and drives a headless browser
through: a newer release appearing, the API refusing (its hourly limit) while the repository file answers, nothing
answering with and without a remembered release, the API reporting an older release, and the docs' version badge and
installation page following along while the changelog stays as written.

    python scripts/site_release_checks.py [--browser /path/to/chromium]

Needs Playwright (pip install playwright; playwright install chromium).
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent / "site"
API = "https://api.github.com/repos/BudEcosystem/Bud-Decision-Engine/releases/latest"
RAW = "https://raw.githubusercontent.com/BudEcosystem/Bud-Decision-Engine/main/site/release.json"
REPO = "https://github.com/BudEcosystem/Bud-Decision-Engine"
SITE = ""
EXE = ""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve() -> str:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def names(v):
    return [f"Bud.Decision.Studio_{v}_aarch64.dmg", f"Bud.Decision.Studio_{v}_x64-setup.exe", f"Bud.Decision.Studio_{v}_x64_en-US.msi",
            f"Bud.Decision.Studio_{v}_amd64.deb", f"Bud.Decision.Studio-{v}-1.x86_64.rpm", f"Bud.Decision.Studio_{v}_amd64.AppImage",
            f"Bud.Decision.Studio_{v}_arm64.deb", f"Bud.Decision.Studio-{v}-1.aarch64.rpm", f"Bud.Decision.Studio_{v}_aarch64.AppImage"]
def api_body(v, size=31_400_000):
    return {"tag_name": f"v{v}", "published_at": "2027-01-02T03:04:05Z", "html_url": f"{REPO}/releases/tag/v{v}",
            "assets": [{"name": n, "size": size + i, "browser_download_url": f"{REPO}/releases/download/v{v}/{n}"} for i, n in enumerate(names(v))]}
def repo_body(v, size=31_400_000):
    a = api_body(v, size)
    return {"tag": a["tag_name"], "date": "2027-01-02", "html": a["html_url"], "assets": [{"name": x["name"], "size": x["size"], "url": x["browser_download_url"]} for x in a["assets"]]}

out = {}
async def new_page(b, api, repo, ctx=None):
    """api / repo: a JSON body to answer with, or an int status to fail with."""
    ctx = ctx or await b.new_context(viewport={"width": 1440, "height": 900},
                                     user_agent="Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36")
    await ctx.unroute_all()
    async def answer(route, what):
        if isinstance(what, int): await route.fulfill(status=what, body="{}", headers={"access-control-allow-origin": "*"})
        else: await route.fulfill(status=200, body=json.dumps(what), content_type="application/json", headers={"access-control-allow-origin": "*"})
    await ctx.route(API, lambda r: answer(r, api))
    await ctx.route(RAW, lambda r: answer(r, repo))
    pg = await ctx.new_page(); pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(str(e)))
    return ctx, pg

READ = """() => ({
  pill: (document.querySelector('[data-release-text]') || {}).textContent,
  hero: document.querySelector('[data-dl-primary]').href, heroLabel: document.querySelector('[data-dl-primary] [data-dl-label]').textContent,
  meta: document.querySelector('[data-dl-meta]').textContent,
  main: document.querySelector('[data-dl-main]').href, mainLabel: document.querySelector('[data-dl-main-label]').textContent,
  rows: document.querySelectorAll('[data-dl-list] .dl-row').length, none: !!document.querySelector('[data-dl-list] .dl-none'),
  rowHrefs: [...document.querySelectorAll('[data-dl-list] .dl-row')].map(a => a.href),
  notes: document.querySelector('[data-release-notes]').href, ver: document.querySelector('[data-dl-version]').textContent })"""

async def product(b, api, repo, ctx=None):
    ctx, pg = await new_page(b, api, repo, ctx)
    await pg.goto(SITE + "/index.html"); await pg.wait_for_timeout(1800)
    got = await pg.evaluate(READ); got["errs"] = pg.errs
    await pg.close()
    return ctx, got

async def main() -> int:
    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": EXE} if EXE else {}))
        # 1. a newer release appears on GitHub: no code changes, the page shows it
        ctx, g = await product(b, api_body("9.9.9"), repo_body("0.3.0"))
        out["API answers with a newer release: the page shows it"] = (
            "Version 9.9.9" in g["meta"] and "/v9.9.9/" in g["hero"] and g["rows"] == 9 and all("/v9.9.9/" in h for h in g["rowHrefs"]) and "9.9.9" in g["ver"] and g["notes"].endswith("/tag/v9.9.9") and not g["errs"], g)
        # 2. remembered, and GitHub cannot be reached: the remembered release is shown
        _, g = await product(b, 403, 404, ctx)
        out["GitHub unreachable later: the remembered release stays"] = ("Version 9.9.9" in g["meta"] and g["rows"] == 9 and not g["errs"], g)
        # 3. the API says an older release is the latest (a withdrawn release): the API is believed
        _, g = await product(b, api_body("0.3.0"), 404, ctx)
        out["API reports an older latest release: the page follows it"] = ("Version 0.3.0" in g["meta"] and "/v0.3.0/" in g["hero"] and not g["errs"], g)
        await ctx.close()
        # 4. API rate-limited (403), the repository's release.json answers
        ctx, g = await product(b, 403, repo_body("9.9.9"))
        out["API rate-limited: the repository's release.json is used"] = ("Version 9.9.9" in g["meta"] and "/v9.9.9/" in g["hero"] and g["rows"] == 9 and not g["errs"], g)
        await ctx.close()
        # 5. first visit, nothing answers: no version is invented; links go to GitHub's latest release
        ctx, g = await product(b, 403, 404)
        out["Nothing answers on a first visit: links go to GitHub's latest release, no version shown"] = (
            "Version" not in g["meta"] and "latest release on GitHub" in g["meta"] and g["notes"] == REPO + "/releases/latest" and g["hero"] == REPO + "/releases/latest" and g["main"] == REPO + "/releases/latest"
            and g["none"] and g["rows"] == 0 and g["ver"] == "" and g["heroLabel"].startswith("Download for") and not g["errs"], g)
        await ctx.close()
        # 6. the repository copy is behind the API: the API wins
        ctx, g = await product(b, api_body("9.9.9"), repo_body("9.9.8"))
        out["Repository copy behind the API: the API's release is shown"] = ("Version 9.9.9" in g["meta"] and "/v9.9.9/" in g["hero"], g)
        await ctx.close()

        # docs
        async def docs(page, api, repo):
            ctx, pg = await new_page(b, api, repo)
            await pg.goto(SITE + "/docs/" + page); await pg.wait_for_timeout(1500)
            got = await pg.evaluate("""() => ({ badge: document.querySelector('[data-version]').textContent, title: document.querySelector('[data-version]').title,
                text: document.querySelector('.prose').textContent, built: window.DOCS_VERSION,
                rows: [...document.querySelectorAll('.prose tr')].filter(r => r.cells.length === 2 && /MB/.test(r.cells[1].textContent)).map(r => [r.cells[0].textContent.trim(), r.cells[1].textContent.trim()]) })""")
            got["errs"] = pg.errs; await ctx.close(); return got
        g = await docs("install.html", api_body("9.9.9", 41_250_000), 404)
        t = g["text"]
        out["Docs install page follows a newer release (names, sizes, version)"] = (
            g["badge"] == "9.9.9" and "Bud.Decision.Studio_9.9.9_x64-setup.exe" in t and "Bud.Decision.Studio-9.9.9-1.x86_64.rpm" in t and "--version v9.9.9" in t
            and "For version 9.9.9" in t and g["built"] not in t.replace("9.9.9", "") and all(s == "41.3 MB" for _, s in g["rows"]) and len(g["rows"]) == 3 and not g["errs"],
            {k: g[k] for k in ("badge", "title", "rows", "errs")})
        g = await docs("install.html", 403, 404)
        out["Docs with GitHub unreachable keep the version they were built for"] = (g["badge"] == g["built"] and f"Bud.Decision.Studio_{g['built']}_x64-setup.exe" in g["text"] and not g["errs"], {k: g[k] for k in ("badge", "rows", "errs")})
        g = await docs("changelog.html", api_body("9.9.9"), 404)
        out["The changelog's history is left as written; only the badge changes"] = (g["badge"] == "9.9.9" and "9.9.9" not in g["text"] and not g["errs"], {k: g[k] for k in ("badge", "errs")})
        await b.close()
    bad = 0
    for k, (ok, detail) in out.items():
        print(("PASS  " if ok else "FAIL  ") + k + ("" if ok else f"\n        {json.dumps(detail)[:700]}"))
        bad |= (not ok)
    return int(bad)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="", help="path to a Chromium to use instead of Playwright's own")
    a = ap.parse_args()
    EXE, SITE = a.browser, serve()
    sys.exit(asyncio.run(main()))
