"""End-to-end smoke test through the studio's HTTP API: load -> decide -> eject, per model.

    .venv/bin/python scripts/smoke.py julia-1 laya ...      (default: every downloaded model)
    --keep     leave models loaded afterwards
    --image P  also send an image to models that read images
"""
import argparse
import base64
import json
import mimetypes
import sys
import time

import httpx

BASE = "http://127.0.0.1:8420"
REQ = {
    "state": "Hi, we were billed twice for March on invoice #4411. Please refund the duplicate today or we will cancel our plan. This is the second time this has happened.",
    "questions": {
        "department": {"type": "choice", "instructions": "Which department should handle this request?",
                       "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                    "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
        "urgency": {"type": "score", "instructions": "How urgent is this request?",
                    "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
        "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--image")
    a = ap.parse_args()
    c = httpx.Client(base_url=BASE, timeout=900, headers={"X-Basal-Client": "smoke"})
    state = c.get("/api/state").json()
    ids = a.models or [m["id"] for m in state["models"] if m["downloaded"]]
    results = {}
    for mid in ids:
        t0 = time.time()
        r = c.post(f"/api/models/{mid}/load", json={})
        if r.status_code != 200:
            print(f"[{mid}] load refused: {r.text}"); results[mid] = "load refused"; continue
        last = None
        while True:
            m = next(x for x in c.get("/api/state").json()["models"] if x["id"] == mid)
            w = m["worker"] or {}
            if w.get("stage") != last:
                last = w.get("stage"); print(f"[{mid}] {w.get('status')}: {last}")
            if w.get("status") in ("ready", "error"):
                break
            time.sleep(1)
        if w["status"] == "error":
            print(f"[{mid}] LOAD FAILED: {w.get('error')}\n{(w.get('detail') or '')[-2500:]}")
            results[mid] = "load failed"
            c.post(f"/api/models/{mid}/eject")
            continue
        load_s = time.time() - t0
        body = {**REQ, "model": mid}
        if a.image and "image" in m["modalities"]:
            mt = mimetypes.guess_type(a.image)[0] or "image/png"
            body = {**body, "state": "A customer attached this photo to a support ticket.",
                    "media": [{"type": "image", "data": f"data:{mt};base64,{base64.b64encode(open(a.image, 'rb').read()).decode()}"}],
                    "questions": {"what": {"type": "choice", "instructions": "What does the image mostly show?",
                                           "criteria": {"text_or_document": None, "a_person": None, "an_animal": None, "a_product": None, "a_landscape": None}}}}
        lat = []
        for i in range(3):
            r = c.post("/v1/systemone", json=body, headers={"X-Basal-Extensions": "1"})
            if r.status_code != 200:
                print(f"[{mid}] DECIDE FAILED {r.status_code}: {r.text[:1500]}"); break
            res = r.json(); lat.append(res["latency_ms"])
        if r.status_code == 200:
            summary = {}
            for q, ans in res["answers"].items():
                if ans["type"] == "noul": summary[q] = f"p(yes)={ans['noul']:.2f}"
                elif ans["type"] == "choice": summary[q] = f"{ans['choice']} ({ans['top_probability']:.2f})"
                else: summary[q] = f"{ans['legend'][ans['decision']]} (avg {ans['score']:.2f})"
            mem = next(x for x in c.get("/api/state").json()["models"] if x["id"] == mid)["worker"]
            print(f"[{mid}] OK  load {load_s:.0f}s  latency {lat} ms  mem {mem.get('gpu_gb')} GB  {json.dumps(summary)}"
                  + (f"  notes={res['notes']}" if res.get("notes") else ""))
            results[mid] = {"load_s": round(load_s), "latency_ms": lat, "answers": res["answers"], "gpu_gb": mem.get("gpu_gb")}
        else:
            results[mid] = "decide failed"
        if not a.keep:
            c.post(f"/api/models/{mid}/eject")
    json.dump(results, open("data/smoke_results.json", "w"), indent=1)
    bad = [k for k, v in results.items() if isinstance(v, str)]
    print("\nFAILED:" if bad else "\nALL PASSED", bad or "")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
