"""Turns the end-to-end report (scripts/e2e.py --report) into docs/testing.md.

    python scripts/test-report.py e2e.json [--conformance "14 passed"] [--machine "NVIDIA GB10, Ubuntu 24.04"]
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("report")
ap.add_argument("--conformance", default="")
ap.add_argument("--machine", default="")
ap.add_argument("--extra", action="append", default=[], help='extra rows as "area|test|result"')
a = ap.parse_args()
r = json.load(open(a.report))
rows = r["results"]
passed = sum(x["ok"] for x in rows)

out = ["# Test results", "",
       f"Run on {r['when']}{', ' + a.machine if a.machine else ''}. {passed} of {len(rows)} end-to-end checks passed."
       + (f" API conformance: {a.conformance}." if a.conformance else ""), "",
       "How to run them yourself:", "",
       "```bash",
       "pip install -r requirements-dev.txt && pip install playwright && playwright install chromium",
       "./run.sh &                                   # or open the desktop app",
       "pytest tests/test_conformance.py -q          # API conformance: TypeSafe, OpenRouter, both official SDKs",
       "python scripts/e2e.py --report e2e.json      # every model and page, through the interface",
       "```", "",
       "## End to end, through the interface", "",
       "`scripts/e2e.py` drives the studio in a browser the way a person would. On the Playground, every downloaded model "
       "answers the *Every question type at once* example (pick one, rate on a scale, yes or no, pick any, put in order, "
       "estimate a number), and the models that read images also answer the receipt-photo example. Each answer has to "
       "appear as a chart with no page errors. Times include loading the model.", "",
       "| Area | Check | Result | Details | Seconds |", "|---|---|---|---|---|"]
for x in rows:
    d = (x.get("detail") or "").replace("|", "/")
    out.append(f"| {x['area']} | {x['test']} | {'pass' if x['ok'] else '**fail**'} | {d[:140]} | {x.get('seconds') or ''} |")
for e in a.extra:
    area, test, res = (e.split("|") + ["", ""])[:3]
    out.append(f"| {area} | {test} | {res} | | |")
(ROOT / "docs" / "testing.md").write_text("\n".join(out) + "\n")
print(f"wrote docs/testing.md ({passed}/{len(rows)})")
