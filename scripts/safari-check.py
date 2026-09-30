"""Checks the studio's interface in real Safari (the engine the macOS app uses) through Apple's safaridriver.

Run on a Mac with a studio that has no models downloaded (so the first-run chooser opens):
    sudo safaridriver --enable            # once
    python scripts/safari-check.py --url http://127.0.0.1:8420 --out safari-shots

It measures the "Choose models to download" list (the bug reported on macOS: the list collapsed to its padding),
once as shipped and once with the old CSS rule forced back to prove the check can see the bug, then screenshots every
page and dialog and reports script errors.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

OLD_RULE = ".ch-list { flex: 1 1 0% !important; }"      # the rule shipped in 0.1.0 and 0.1.1
PAGES = ["playground", "templates", "models", "evaluate", "history", "api", "learn", "system"]
COLLECT = "window.__errs = window.__errs || []; if (!window.__hooked) { window.__hooked = 1; " \
          "window.addEventListener('error', e => window.__errs.push(String(e.message))); " \
          "window.addEventListener('unhandledrejection', e => window.__errs.push('rejection: ' + String(e.reason))); }"


def list_height(d) -> float:
    return d.execute_script("const l = document.querySelector('#chlist'); return l ? l.getBoundingClientRect().height : -1;")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420")
    ap.add_argument("--out", default="safari-shots")
    ap.add_argument("--size", default="1325x810")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    url = a.url.rstrip("/") + "/"
    w, h = (int(x) for x in a.size.split("x"))
    report = {"size": a.size, "checks": [], "errors": []}

    def check(name, ok, detail=""):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)

    d = webdriver.Safari()
    try:
        d.set_window_size(w, h)
        report["browser"] = d.execute_script("return navigator.userAgent")
        print("browser:", report["browser"], flush=True)

        # First run: the chooser opens by itself.
        d.get(url + "#/playground")
        d.execute_script(COLLECT)
        time.sleep(5)
        d.save_screenshot(str(out / "first-run-chooser.png"))
        shipped = list_height(d)
        check("first-run chooser: model list is visible (as shipped)", shipped >= 120, f"{shipped:.0f} px tall")
        d.execute_script(f"const s = document.createElement('style'); s.id = 'old'; s.textContent = '{OLD_RULE}'; document.head.append(s);")
        time.sleep(0.5)
        old = list_height(d)
        d.save_screenshot(str(out / "first-run-chooser-old-rule.png"))
        report["old_rule_height"] = old
        print(f"info  with the old rule forced back, the list is {old:.0f} px tall "
              f"({'reproduces the reported bug' if old < 40 else 'this Safari does not collapse it'})", flush=True)
        d.execute_script("document.getElementById('old')?.remove()")
        rows = d.execute_script("return document.querySelectorAll('#chlist .ch-row').length")
        check("first-run chooser: every model is listed", rows >= 11, f"{rows} rows")
        d.find_element(By.ID, "chlater").click()
        time.sleep(0.5)

        # Every page, and the dialogs
        for name in PAGES:
            d.execute_script(f"location.hash = '#/{name}'")
            time.sleep(2.5)
            d.save_screenshot(str(out / f"page-{name}.png"))
            empty = d.execute_script("const c = document.querySelector('#content'); return !c || c.getBoundingClientRect().height < 200 || !c.innerText.trim();")
            check(f"{name} page renders", not empty)
        d.execute_script("location.hash = '#/models'")
        time.sleep(2)
        d.find_element(By.ID, "dlall").click()
        time.sleep(1)
        d.save_screenshot(str(out / "download-sheet.png"))
        hgt = list_height(d)
        check("Models > Download models: list is visible", hgt >= 120, f"{hgt:.0f} px tall")
        d.find_element(By.ID, "chlater").click()
        time.sleep(0.4)
        d.find_element(By.ID, "help").click()
        time.sleep(0.8)
        d.save_screenshot(str(out / "help-me-choose.png"))
        dlg = d.execute_script("const x = document.querySelector('dialog[open]'); return x ? x.getBoundingClientRect().height : -1;")
        check("Help me choose dialog opens", dlg > 150, f"{dlg:.0f} px tall")
        ActionChains(d).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.4)
        d.execute_script("location.hash = '#/playground'")
        time.sleep(2)
        d.find_element(By.ID, "mchip").click()
        time.sleep(0.8)
        d.save_screenshot(str(out / "model-loader.png"))
        pal = d.execute_script("const x = document.querySelector('#plist'); return x ? x.getBoundingClientRect().height : -1;")
        check("model loader lists the models", pal >= 120, f"{pal:.0f} px tall")
        report["errors"] = d.execute_script("return window.__errs || []")
        check("no script errors", not report["errors"], "; ".join(report["errors"])[:300])
    finally:
        d.quit()
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return 0 if all(c["ok"] for c in report["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
