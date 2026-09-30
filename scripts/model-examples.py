"""Checks that every model answers its own signature examples (ui/js/model-guides.js) the way they are meant to
show it off. Each example exists to demonstrate what a model is good at, so a wrong answer there is a bug.

    python scripts/model-examples.py [--url http://127.0.0.1:8420] [model ...]

Loads each model in turn (smallest first), asks its examples, prints every answer and checks the key ones below,
then ejects it. Needs node (to read the examples) and a running studio with the models downloaded.
"""
import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

# The answers each example must produce (question id -> option, or True/False for yes/no questions).
EXPECT = {
    "agent-browser": {"next_action": "set_date", "if_full": "ask_user"},
    "tool-call": {"first": "Reschedule the existing meeting to Thursday"},
    "text-game": {"move": "Take the key from the table", "then": "Unlock the north door with the key"},
    "de-routing": {"team": "account", "mood": "frustrated", "urgent": True},
    "multilingual": {"department": "technical"},
    "ja-support": {"department": "delivery", "urgent": True},
    "support": {"department": "billing", "churn_risk": True},
    "injection": {"injection": True},
    "agent-trace": {"outcome": "harmful", "needs_review": True},
    "invoice-match": {"disposition": "manual_review", "duplicate": False, "matches_order": False},
    "security-alert": {"true_positive": True, "credential_compromise": True},
    "banking77": {"intent": "cash_withdrawal_charge"},
    "news-topic": {"topic": "sports"},
    "log-line": {"severity": "error", "component": "payments"},
    "tags": {"needs_reply": True},
    "tour": {"team": "billing"},
    "image": {"doc_type": "receipt", "shows_item": True},
    "chart": {"trend": "rising", "best_month_recent": True},
    "policy-refund": {"rule": "rule_2", "manager": True},
    "code": {"area": "security"},
    "verify": {"hallucination": True},
}


def load_ui_data():
    js = ("import('%s/ui/js/examples.js').then(e => import('%s/ui/js/model-guides.js').then(g => "
          "console.log(JSON.stringify({ examples: e.EXAMPLES, guides: g.GUIDES }))))") % (ROOT.as_posix(), ROOT.as_posix())
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    return {e["id"]: e for e in data["examples"]}, data["guides"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420")
    ap.add_argument("models", nargs="*")
    a = ap.parse_args()
    examples, guides = load_ui_data()
    c = httpx.Client(base_url=a.url, headers={"X-Basal-Client": "1", "X-Basal-Extensions": "1"}, timeout=1200)
    state = c.get("/api/state").json()
    size = {m["id"]: m["memory_gb"] for m in state["models"] if m["downloaded"]}
    todo = [m for m in (a.models or guides) if m in size]
    failures = 0
    for mid in sorted(todo, key=lambda m: size[m]):
        print(f"\n== {mid}: {guides[mid]['madeFor']}", flush=True)
        for exid in guides[mid]["examples"]:
            ex = examples[exid]
            req = {"model": mid, "state": ex["state"], "questions": ex["questions"]}
            if ex.get("sample"):
                data = base64.b64encode((ROOT / "ui" / "samples" / ex["sample"]).read_bytes()).decode()
                req["media"] = [{"type": "image", "data": f"data:image/png;base64,{data}"}]
            r = c.post("/v1/systemone", json=req)
            if r.status_code != 200:
                print(f"  FAIL {exid}: HTTP {r.status_code} {r.text[:160]}")
                failures += 1
                continue
            answers = r.json()["answers"]
            for qid, want in EXPECT.get(exid, {}).items():
                ans = answers[qid]
                got = ans.get("noul", 0) >= 0.5 if isinstance(want, bool) else ans.get("choice")
                ok = got == want
                failures += not ok
                print(f"  {'PASS' if ok else 'FAIL'} {exid}.{qid}: {got}" + ("" if ok else f" (expected {want})"))
        c.post(f"/api/models/{mid}/eject")
        time.sleep(3)
    print(f"\n{'all examples answer as intended' if not failures else f'{failures} check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
