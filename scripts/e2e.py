"""End-to-end test of Bud Decision Studio through its user interface, with real models.

Drives a headless browser against a running studio, the way a person would:
  * Playground: every downloaded model answers all six question types ("Every question type at once"), and the models
    that read images answer the receipt-photo example; each answer must render as figures with no page errors.
  * Evaluate: a leaderboard over the sample examples with two models.
  * Activity: the requests just made appear in the log, and the inspector opens.
  * API: the quick-start curl command shown on the page runs and returns answers.
  * Models: a small model's files are deleted and downloaded again from the page.
  * System: switch to the processor, answer on it, switch back.

    python scripts/e2e.py --url http://127.0.0.1:8420 [--models laya,kev-4b] [--skip-download] [--report e2e.json]

Needs Playwright (pip install playwright; playwright install chromium). Models are loaded one at a time, smallest
first, and ejected afterwards; a model that needs more memory than is free waits for the others, then is skipped
with a note if memory never frees up.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import subprocess
import time

import httpx
from playwright.async_api import async_playwright

HDR = {"X-Basal-Client": "1"}


class Run:
    def __init__(self, url: str, report: str = ""):
        self.url = url.rstrip("/") + "/"
        self.api = httpx.Client(base_url=self.url, headers=HDR, timeout=60)
        self.results: list[dict] = []
        self.page_errors: list[str] = []
        self.report = report
        # results from earlier runs of the same report are kept, and replaced when a test is run again
        self.saved = json.load(open(report))["results"] if report and __import__("os").path.exists(report) else []

    def state(self) -> dict:
        return self.api.get("/api/state").json()

    def record(self, area: str, name: str, ok: bool, detail: str = "", seconds: float | None = None):
        self.results.append({"area": area, "test": name, "ok": ok, "detail": detail, "seconds": seconds})
        print(f"{'PASS' if ok else 'FAIL'}  {area:10} {name:44} {detail}", flush=True)
        if self.report:   # written after every check, so an interrupted run keeps what it finished
            keep = [r for r in self.saved if (r["area"], r["test"]) not in {(x["area"], x["test"]) for x in self.results}]
            json.dump({"url": self.url, "results": keep + self.results, "when": time.strftime("%Y-%m-%d %H:%M")},
                      open(self.report, "w"), indent=2)

    def eject(self, mid: str):
        self.api.post(f"/api/models/{mid}/eject")
        for _ in range(60):
            if not any(m["id"] == mid and m["worker"] for m in self.state()["models"]):
                return
            time.sleep(1)


async def wait_answers(pg, want: int, timeout_s: int) -> tuple[bool, str]:
    """Wait until the Playground shows `want` answer figures, or its error view."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        figs = await pg.locator("#out .fig").count()
        if figs >= want and not await pg.locator("#decide[disabled]").count():
            return True, (await pg.locator("#stamp").inner_text()).replace("\n", " ").strip()
        if await pg.locator("#out h2:has-text('The decision did not run')").count():
            return False, (await pg.locator("#out").inner_text()).replace("\n", " ")[:300]
        await pg.wait_for_timeout(1000)
    return False, f"no answers after {timeout_s} s"


async def playground(run: Run, pg, mid: str, example: str, want: int, timeout_s: int) -> bool:
    await pg.goto(f"{run.url}#/playground?model={mid}&example={example}")
    await pg.wait_for_timeout(2500)
    chip = (await pg.locator("#mchip b").inner_text()).strip()
    t0 = time.time()
    await pg.click("#decide")
    ok, detail = await wait_answers(pg, want, timeout_s)
    if ok:   # the model's own answer time, from the studio's request log
        hist = run.api.get("/api/history", params={"limit": 20}).json()
        items = hist if isinstance(hist, list) else hist.get("items", [])
        last = next((h for h in items if h.get("model") == mid), {})
        detail = f"{chip}: {want} answers in {(last.get("latency_ms") or 0):.0f} ms" + (f", {last['passes']} passes" if (last.get("passes") or 1) > 1 else "")
    run.record("playground", f"{mid}: {example} ({want} answers)", ok, detail, round(time.time() - t0, 1))
    return ok


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420")
    ap.add_argument("--models", default="")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--browser", default="")
    ap.add_argument("--no-models", action="store_true", help="skip the per-model Playground checks")
    ap.add_argument("--no-pages", action="store_true", help="skip Evaluate, Activity, API, Models and System")
    ap.add_argument("--headroom", type=float, default=6.0, help="GB of free memory to keep beyond the model's own")
    a = ap.parse_args()
    run = Run(a.url, a.report)
    st = run.state()
    models = sorted([m for m in st["models"] if m["downloaded"]], key=lambda m: m["memory_gb"])
    if a.models:
        models = [m for m in models if m["id"] in a.models.split(",")]

    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": a.browser} if a.browser else {}))
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: run.page_errors.append(str(e)))

        # ---- every model, every question type; images on the models that read them
        waiting = [] if a.no_models else list(models)
        deadline = time.time() + 45 * 60
        while waiting and time.time() < deadline:
            m = waiting.pop(0)
            free = run.state()["system"]["mem_available_gb"]
            if m["memory_gb"] + a.headroom > free and m["fit"]["ok"]:
                if all(x["memory_gb"] + a.headroom > free for x in waiting):
                    await asyncio.sleep(30)          # memory is shared with other programs; wait for it to free up
                waiting.append(m)
                continue
            if not m["fit"]["ok"]:
                run.record("playground", f"{m['id']}: skipped", True, m["fit"]["reason"])
                continue
            timeout = 900 if m["memory_gb"] > 15 else 600
            await playground(run, pg, m["id"], "tour", 6, timeout)
            if "image" in m["modalities"]:
                await playground(run, pg, m["id"], "image", 3, timeout)
            run.eject(m["id"])
        for m in waiting:
            run.record("playground", f"{m['id']}: not tested", False, "not enough free memory during the test window")

        if a.no_pages:
            await b.close()
            return 0 if all(r["ok"] for r in run.results) else 1

        # ---- Evaluate: two small models on the sample examples
        small = [m["id"] for m in models if m["memory_gb"] <= 1.3][:2]
        await pg.goto(run.url + "#/evaluate")
        await pg.wait_for_timeout(2000)
        for cb in await pg.locator("input[type=checkbox][value]").all():
            v = await cb.get_attribute("value")
            if (v in small) != await cb.is_checked():
                await cb.set_checked(v in small)
        t0 = time.time()
        await pg.click("#go")
        for _ in range(600):
            await pg.wait_for_timeout(1000)
            if not await pg.locator("#go[disabled]").count():
                break
        rows = await pg.locator(".board tbody tr").count()
        run.record("evaluate", f"leaderboard for {', '.join(small)}", rows >= len(small), f"{rows} rows", round(time.time() - t0, 1))
        for mid in small:
            run.eject(mid)

        # ---- Activity: the requests above are in the log and open in the inspector
        await pg.goto(run.url + "#/activity")
        await pg.wait_for_timeout(3000)
        n = await pg.locator("tr[data-req]").count()
        if n:
            await pg.locator("tr[data-req]").first.click()
            await pg.wait_for_timeout(500)
        insp = await pg.locator("#replay").count()
        run.record("activity", "requests listed and inspectable", n > 0 and insp > 0, f"{n} requests shown")

        # ---- Playground from scratch: New, a situation, one question typed in, Decide
        await pg.goto(run.url + "#/playground?model=laya")
        await pg.wait_for_timeout(2500)
        await pg.click("#newdec")
        await pg.wait_for_timeout(400)
        blank = await pg.locator("#state").input_value() == "" and await pg.locator("#qs .q").count() == 0
        await pg.fill("#state", "Our production database has been down for 20 minutes and customers cannot log in.")
        await pg.click('[data-add-type="choice"]')
        await pg.wait_for_timeout(300)
        await pg.keyboard.type("Which team should handle this?")
        add = pg.locator("#qs .q").first.locator(".opt-add")
        for name in ("infrastructure", "billing", "product"):
            await add.fill(name)
            await add.press("Enter")
        t0 = time.time()
        await pg.click("#decide")
        ok, detail = await wait_answers(pg, 1, 300)
        run.record("playground", "new decision from scratch", blank and ok, detail if not ok else "blank start, 1 question typed in, answered", round(time.time() - t0, 1))

        # ---- API page: run the curl quick start exactly as shown
        await pg.goto(run.url + "#/api")
        await pg.wait_for_timeout(2000)
        await pg.click('[data-lang="curl"]')
        await pg.wait_for_timeout(500)
        curl = (await pg.locator("#snip pre").first.inner_text()).strip()
        if curl:
            out = subprocess.run(["bash", "-c", curl], capture_output=True, text=True, timeout=600)
            try:
                ans = json.loads(out.stdout).get("answers") or {}
            except ValueError:
                ans = {}
            run.record("api", "quick-start curl from the page", bool(ans), f"{len(ans)} answers")
        else:
            run.record("api", "quick-start curl from the page", False, "no curl example found")
        for mm in run.state()["models"]:
            if mm["worker"]:
                run.eject(mm["id"])

        # ---- Models page: delete a small model's files, then download it again
        if not a.skip_download:
            target = "julia-1"
            await pg.goto(run.url + "#/models")
            await pg.wait_for_timeout(2000)
            run.api.delete(f"/api/models/{target}/files")        # the page's Delete asks for confirmation; same call
            await pg.wait_for_timeout(2500)
            await pg.locator(f'tr[data-id="{target}"] td').first.click()
            await pg.wait_for_timeout(500)
            t0 = time.time()
            await pg.locator(f'#insp [data-download="{target}"]').first.click()
            done = False
            for _ in range(900):
                await pg.wait_for_timeout(1000)
                if any(m["id"] == target and m["downloaded"] for m in run.state()["models"]):
                    done = True
                    break
            run.record("models", f"delete and re-download {target} from the page", done, "", round(time.time() - t0, 1))

        # ---- System page: move to the processor, answer there, move back
        await pg.goto(run.url + "#/system")
        await pg.wait_for_timeout(2000)
        if await pg.locator('[data-dev="cpu"]').count():
            await pg.click('[data-dev="cpu"]')
            await pg.wait_for_timeout(800)
            ok = await playground(run, pg, "julia-1", "support", 1, 300)
            dev = next((m["worker"]["options"].get("device") for m in run.state()["models"] if m["id"] == "julia-1" and m["worker"]), None)
            run.record("system", "switch to the processor and answer there", ok and dev == "cpu", f"worker device: {dev}")
            run.eject("julia-1")
            await pg.goto(run.url + "#/system")
            await pg.wait_for_timeout(1500)
            gpu = await pg.locator('[data-dev]:not([data-dev="cpu"])').first.get_attribute("data-dev")
            await pg.click(f'[data-dev="{gpu}"]')
            await pg.wait_for_timeout(800)
            run.record("system", "switch back", run.api.get("/api/config").json()["device"] == gpu, gpu)

        run.record("ui", "no JavaScript errors on any page", not run.page_errors, "; ".join(run.page_errors)[:300])
        await b.close()

    passed = sum(r["ok"] for r in run.results)
    print(f"\n{passed} of {len(run.results)} passed")
    return 0 if passed == len(run.results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
