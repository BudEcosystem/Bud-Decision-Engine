"""End-to-end test of the Train page, as a person would use it: example file -> review -> start -> progress -> result
-> "Use it now" -> the Playground answers with the fine-tuned model.

Needs a running studio with a GPU and Playwright (pip install playwright; playwright install chromium):

    python scripts/e2e_train.py --url http://127.0.0.1:8431 --model julia-1 --shots /tmp/train-shots
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420")
    ap.add_argument("--model", default="julia-1", help="model to teach (small ones keep the test short)")
    ap.add_argument("--shots", default="train-shots")
    ap.add_argument("--timeout-min", type=float, default=40)
    ap.add_argument("--executable", default=None, help="Chromium binary, if Playwright's own isn't installed")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=a.executable) if a.executable else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1360, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"{a.url}/#/train")
        pg.wait_for_selector("#sample", timeout=20000)
        pg.screenshot(path=str(shots / "1-start.png"))
        pg.click("#sample")
        pg.wait_for_selector("#go", timeout=30000)
        pg.click("#change")
        pg.click(f'[data-pick="{a.model}"]')
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(shots / "2-review.png"))
        pg.click("#go")
        pg.wait_for_url("**/#/train/*", timeout=30000)
        t0 = time.time()
        shot_progress = False
        while time.time() - t0 < a.timeout_min * 60:
            pg.wait_for_timeout(5000)
            text = pg.inner_text(".train")
            if not shot_progress and "Practice score" in text:
                pg.screenshot(path=str(shots / "3-progress.png"))
                shot_progress = True
            if any(k in text for k in ("Use it now", "is unchanged", "Training stopped", "Cancelled", "Paused")):
                break
        pg.screenshot(path=str(shots / "4-result.png"))
        result = pg.inner_text(".train")
        ok = "Use it now" in result
        print("result:", result[:400].replace("\n", " | "))
        if ok:
            pg.click("[data-use]")
            pg.wait_for_url("**/#/playground*", timeout=20000)
            pg.wait_for_timeout(4000)
            pg.screenshot(path=str(shots / "5-playground.png"))
            # The studio serves the fine-tune: loads the base model, attaches the delta, and answers the example.
            pg.click("#decide")
            pg.wait_for_function("() => !document.body.innerText.includes('Press Decide')", timeout=600000)
            pg.wait_for_function("() => !/Loading |Starting the worker/.test(document.body.innerText)", timeout=600000)
            pg.wait_for_timeout(3000)
            pg.screenshot(path=str(shots / "6-answer.png"))
            text = pg.inner_text("body")
            ok = ok and "Which team fits best?" in text and "%" in text
            print("answered in the Playground:", ok)
        b.close()
    print("page errors:", errors[:5])
    return 0 if ok and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
