"""Interface regression checks with the test model: no GPU, no downloads, about a minute.

Starts its own studio on a free port with a temporary data folder and the deterministic test model (BASAL_FAKE_MODEL=1),
then drives a headless browser through the cases that once went wrong:
  * leaving the Playground before its first-visit run fires throws nothing;
  * the template id and alias fields have valid patterns that the browser enforces;
  * Save as template in a fresh window names and saves the model the Playground uses;
  * Evaluate recommends an act threshold only within 50% to 99%, and a saved threshold outside that range is repaired
    without disturbing the other saved preferences.

    python scripts/ui_checks.py [--browser /path/to/chromium] [--url http://127.0.0.1:8420]

Needs Playwright and httpx (pip install playwright httpx; playwright install chromium). With --url it checks a studio
that is already running and has the test model loaded, instead of starting one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
MODEL = "fake-decider"
out: dict = {}
URL = ""
API: httpx.Client


def start_studio() -> tuple[subprocess.Popen, str]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    data = tempfile.mkdtemp(prefix="basal-ui-check-")
    env = {**os.environ, "BASAL_DATA": data, "BASAL_FAKE_MODEL": "1", "BASAL_NO_DOWNLOADS": "1"}
    log = open(Path(data) / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "basal.server", "--port", str(port)], cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{base}/api/state", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    else:
        proc.kill()
        raise SystemExit((Path(data) / "server.log").read_text())
    c = httpx.Client(base_url=base, timeout=60, headers={"x-basal-client": "1"})
    c.post(f"/api/models/{MODEL}/load").raise_for_status()
    for _ in range(100):
        if any(m["id"] == MODEL and (m["worker"] or {}).get("status") == "ready" for m in c.get("/api/state").json()["models"]):
            break
        time.sleep(0.2)
    return proc, base


async def page(b, **kw):
    ctx = await b.new_context(viewport={"width": 1440, "height": 900}, **kw)
    pg = await ctx.new_page()
    pg.errs = []
    pg.on("pageerror", lambda e: pg.errs.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errs.append(f"console: {m.text}") if m.type in ("error", "warning") else None)
    return ctx, pg

async def main(browser: str = "") -> int:
    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": browser} if browser else {}))

        # leave the Playground before its first-visit run fires
        ctx, pg = await page(b)
        await pg.goto(URL + "#/playground")
        await pg.wait_for_function("sessionStorage.getItem('bud.autorun') === '1'", timeout=15000)
        await pg.evaluate("location.hash = '#/models'")
        await pg.wait_for_timeout(1200)
        bad = [e for e in pg.errs if "pageerror" in e]
        out["Leaving the Playground at once throws nothing"] = (not bad, bad[:2])
        await ctx.close()

        # the id and alias patterns are valid and enforced
        ctx, pg = await page(b)
        await pg.goto(URL + "#/playground"); await pg.wait_for_selector("#savetpl"); await pg.wait_for_timeout(1500)
        await pg.click("#savetpl"); await pg.wait_for_selector("dialog[open] input[name=id]")
        async def validity(sel, value):
            return await pg.evaluate("""([sel, v]) => { const i = document.querySelector(sel); i.value = v; return { mismatch: i.validity.patternMismatch, valid: i.validity.valid, title: i.title }; }""", [sel, value])
        r1 = await validity("dialog[open] input[name=id]", "Bad Id!"); r2 = await validity("dialog[open] input[name=id]", "good-id_1")
        await pg.keyboard.press("Escape")
        tid = next(t["id"] for t in API.get("/v1/studio/templates").json()["data"] if t["id"].startswith("builtin/"))
        await pg.goto(URL + "#/templates/" + tid); await pg.wait_for_timeout(1500)
        await pg.get_by_role("button", name=re.compile("Clone", re.I)).first.click()
        await pg.wait_for_selector("dialog[open] input[name=v]")
        r3 = await validity("dialog[open] input[name=v]", "Not Valid"); r4 = await validity("dialog[open] input[name=v]", "my-copy_2")
        await pg.keyboard.press("Escape")
        pat = [e for e in pg.errs if "attern" in e]
        ok = r1["mismatch"] and r2["valid"] and r3["mismatch"] and r4["valid"] and not pat and bool(r1["title"]) and bool(r3["title"])
        out["Id and alias patterns are valid and enforced"] = (ok, [r1, r2, r3, r4, pat[:1]])
        await ctx.close()

        # Save as template in a fresh window, before the model list has arrived
        ctx, pg = await page(b)
        state = {"n": 0}
        async def slow(route):
            state["n"] += 1
            if state["n"] <= 2:
                await asyncio.sleep(1.5)
            await route.continue_()
        await ctx.route("**/api/state", slow)
        await pg.goto(URL + "#/playground"); await pg.wait_for_selector("#savetpl")
        had_state = await pg.evaluate("import('/ui/js/store.js').then(m => !!m.store.state)")
        await pg.click("#savetpl"); await pg.wait_for_selector("dialog[open] input[name=id]", timeout=10000)
        text = await pg.inner_text("dialog[open] p")
        await pg.fill("dialog[open] input[name=id]", "fresh-window"); await pg.click("#dosave"); await pg.wait_for_timeout(1500)
        t = API.get("/v1/studio/templates/fresh-window")
        model = t.json().get("model") if t.status_code == 200 else f"not saved ({t.status_code})"
        out["Save as template names and saves the model, even before the model list arrives"] = (
            (not had_state) and "Fake Decider as the default model" in text and model == MODEL, [had_state, text[:90], model])
        await ctx.close()

        # Evaluate never recommends a threshold outside 50% to 99%
        ctx, pg = await page(b)
        await pg.goto(URL + "#/evaluate"); await pg.wait_for_selector("#go"); await pg.wait_for_timeout(1500)
        async def run_eval():
            for cb in await pg.locator("input[type=checkbox][value]").all():
                want = (await cb.get_attribute("value")) == MODEL
                if want != await cb.is_checked():
                    await cb.set_checked(want)
            await pg.click("#go")
            for _ in range(120):
                await pg.wait_for_timeout(500)
                if not await pg.locator("#go[disabled]").count():
                    break
            await pg.wait_for_timeout(600)
            return await pg.inner_text("#results")
        first = await run_eval()
        # relabel every example with what the model answered, so any threshold looks "safe"
        rows = [ln.split("\t") for ln in (await pg.input_value("#data")).splitlines() if ln.strip()]
        ids = [d["id"] for d in API.get("/v1/studio/decisions", params={"surface": "eval", "limit": 100}).json()["data"]]
        ds = [API.get(f"/v1/studio/decisions/{i}").json() for i in ids]
        pred = {}
        for d in ds:
            st = (d.get("input") or {}).get("state")
            st = st if isinstance(st, str) else json.dumps(st)
            if d.get("answers"):
                pred[st.strip()] = next(iter(d["answers"].values())).get("decision")
        relabelled = [f"{r[0]}\t{pred.get(r[0].strip(), r[1] if len(r) > 1 else '')}" for r in rows]
        await pg.evaluate("document.querySelector('#data').closest('details').open = true")
        await pg.fill("#data", "\n".join(relabelled)); await pg.dispatch_event("#data", "input"); await pg.wait_for_timeout(600)
        second = await run_eval()
        m = re.search(r"Act threshold (\d+(?:\.\d+)?)%", second)
        acc = re.search(r"(\d+)%", second)
        th = float(m.group(1)) if m else None
        ok = (th is not None and 50 <= th <= 99) or ("Not sure enough to act" in second)
        tops = sorted(next(iter(d["answers"].values())).get("certainty") or 0 for d in ds if d.get("answers"))
        out["Evaluate recommends a threshold within 50% to 99%"] = (ok, [f"recommended {th}", f"matched labels {len(pred)}/{len(rows)}",
                                                                             f"top probabilities {tops[0]:.2f} to {tops[-1]:.2f}" if tops else ""])
        if th is not None:
            await pg.click("[data-use-th]"); await pg.wait_for_timeout(300)
            saved = await pg.evaluate("JSON.parse(localStorage.getItem('basal.prefs')).threshold")
            out["Use this threshold saves a value the Playground slider can show"] = (0.5 <= saved <= 0.99, [saved])
        await ctx.close()
        # saved preferences: one below the range (left by the old bug) is brought into it; ordinary ones are kept
        async def prefs_after_load(seed):
            c2, p2 = await page(b)
            await c2.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); localStorage.setItem('basal.prefs', '%s'); }" % json.dumps(seed))
            await p2.goto(URL + "#/playground"); await p2.wait_for_selector("#play"); await p2.wait_for_timeout(1200)
            got = await p2.evaluate("import('/ui/js/store.js').then(m => m.store.prefs)")
            slider = await p2.evaluate("(document.querySelector('#th') || {}).value")
            await c2.close()
            return got, slider
        got, slider = await prefs_after_load({"threshold": 0.335})
        out["A saved threshold outside the range is repaired on load"] = (got["threshold"] == 0.5, [got["threshold"], slider])
        got, slider = await prefs_after_load({"threshold": 0.8, "theme": "dark", "lastModel": "laya"})
        out["Ordinary saved preferences are kept"] = (got["threshold"] == 0.8 and got["theme"] == "dark" and got["lastModel"] == "laya", [got, slider])
        await ctx.close()
        await b.close()
    worst = 0
    for k, (ok, detail) in out.items():
        print(("PASS  " if ok else "FAIL  ") + k + ("" if ok else f"\n        {detail}"))
        worst |= (not ok)
    return int(worst)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="check a running studio (test model loaded) instead of starting one")
    ap.add_argument("--browser", default="", help="path to a Chromium to use instead of Playwright's own")
    a = ap.parse_args()
    proc = None
    if a.url:
        URL = a.url.rstrip("/") + "/"
    else:
        proc, base = start_studio()
        URL = base + "/"
    API = httpx.Client(base_url=URL, timeout=60, headers={"x-basal-client": "1"})
    try:
        code = asyncio.run(main(a.browser))
    finally:
        if proc:
            proc.terminate()
            proc.wait(10)
    sys.exit(code)
