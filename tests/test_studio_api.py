"""The studio API (/v1/studio) and history on the wire routes, end to end against a live studio.

Starts its own studio on a free port with a temporary data folder and the deterministic test model
(BASAL_FAKE_MODEL=1), so it needs no GPU and never touches real history.

    .venv/bin/python -m pytest tests/test_studio_api.py -q
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL = "fake-decider"

TEMPLATE = {
    "name": "Support triage", "description": "Route inbound tickets, rate urgency, flag churn risk.",
    "metadata": {"owner_team": "support-eng"}, "note": "First version", "modalities": ["text", "image"],
    "variables": {
        "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000},
        "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"},
        "open_invoices": {"type": "integer", "minimum": 0, "required": False},
        "customer_email": {"type": "string", "max_length": 320, "required": False, "sensitive": True},
        "screenshot": {"type": "image", "required": False},
    },
    "state": {"customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
              "message": "{{customer_message}}"},
    "questions": {
        "department": {"type": "choice", "instructions": "Which department should handle this request?",
                       "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                    "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
        "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                    "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
        "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    },
    "model": MODEL,
    "settings": {"act_threshold": 0.9, "temperature": 1.1, "questions": {"churn_risk": {"act_threshold": 0.7}},
                 "models": {MODEL: {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}},
    "extensions": {"questions": True, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
}
WIRE = {"model": MODEL, "state": {"ticket": "I was charged twice and want a refund today, or I cancel."},
        "questions": {"team": {"type": "choice", "instructions": "Which team?", "criteria": {"billing": "payments", "technical": None}},
                      "urgency": {"type": "score", "criteria": ["low", "high"]},
                      "refund": {"type": "noul", "instructions": "Refund?"}}}
MSG = "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel."


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start(extra_env: dict | None = None):
    port = _free_port()
    data = tempfile.mkdtemp(prefix="basal-api-test-")
    env = {**os.environ, "BASAL_DATA": data, "BASAL_FAKE_MODEL": "1", "BASAL_NO_DOWNLOADS": "1", **(extra_env or {})}
    log = open(Path(data) / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "basal.server", "--port", str(port)], cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{base}/v1/studio/models", timeout=2).status_code in (200, 401):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError((Path(data) / "server.log").read_text())
    return proc, base


@pytest.fixture(scope="module")
def api():
    proc, base = _start()
    c = httpx.Client(base_url=base, timeout=60)
    c.post(f"/api/models/{MODEL}/load", headers={"x-basal-client": "1"}).raise_for_status()
    for _ in range(100):
        if any(m["id"] == MODEL and (m["worker"] or {}).get("status") == "ready" for m in c.get("/api/state").json()["models"]):
            break
        time.sleep(0.2)
    yield c
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def studio(api):
    return httpx.Client(base_url=str(api.base_url) + "/v1/studio", timeout=60)


def err(r) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def png() -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)   # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


# ---------------------------------------------------------------------------------------------------- the five calls


def test_call1_upsert_is_idempotent(studio):
    r = studio.put("/templates/support-triage", json=TEMPLATE)
    assert r.status_code == 201 and r.headers["etag"] == '"1"' and r.headers["location"] == "/v1/studio/templates/support-triage"
    body = r.json()
    assert body["change"] == "created" and body["version"] == 1 and body["aliases"] == {"latest": 1}
    assert body["variables"]["customer_message"]["required"] is True
    again = studio.put("/templates/support-triage", json=TEMPLATE)
    assert again.status_code == 200 and again.json()["change"] == "unchanged" and again.headers["etag"] == '"1"'


def test_call2_everyday_decision(studio):
    r = studio.post("/decisions", json={"template": "support-triage", "variables": {"customer_message": MSG, "account_tier": "enterprise"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert r.headers["x-basal-stored"] == "full" and r.headers["x-basal-decision-id"] == d["id"] and d["id"].startswith("dec_")
    assert d["template"] == {"id": "support-triage", "version": 1, "ref": "support-triage", "resolved_from": "latest", "attribution": "explicit"}
    assert d["settings"]["sources"] == {"act_threshold": "template", "temperature": f"template.models.{MODEL}",
                                        "questions.urgency.temperature": f"template.models.{MODEL}.questions",
                                        "questions.churn_risk.act_threshold": "template.questions"}
    for k, a in d["answers"].items():
        assert a["origin"] == "template" and a["act"] == (a["certainty"] >= (0.7 if k == "churn_risk" else 0.9))
    assert d["needs_review"] == [k for k, a in d["answers"].items() if not a["act"]]
    got = studio.get(f"/decisions/{d['id']}?include=input.rendered_state").json()
    assert got["input"]["state"] == {"customer": {"tier": "enterprise"}, "message": MSG}
    assert got["input"]["rendered_state"].startswith("customer:\n  tier: enterprise")
    assert "open_invoices" not in json.dumps(got["input"]["state"])


def test_call2_errors_list_everything(studio):
    r = studio.post("/decisions", json={"template": "support-triage", "variables": {"acount_tier": "enterprise"}})
    assert err(r) == (400, "unknown_variable")
    e = r.json()["error"]
    assert e["param"] == "variables.acount_tier" and "Did you mean 'account_tier'?" in e["message"]
    assert {d["code"] for d in e["details"]} == {"unknown_variable", "missing_variable"}


def test_call3_patch_versions_and_aliases(studio):
    r = studio.patch("/templates/support-triage", headers={"If-Match": '"1"'},
                     json={"note": "Ask about refunds", "questions": {"wants_refund": {"type": "noul", "instructions": "Money back?"}},
                           "settings": {"act_threshold": 0.85}})
    assert r.status_code == 200 and r.json()["change"] == "new_version" and r.headers["etag"] == '"2"'
    assert r.json()["settings"]["models"][MODEL]["temperature"] == 1.3          # merge patch kept the rest
    stale = studio.patch("/templates/support-triage", headers={"If-Match": '"1"'}, json={"note": "x", "settings": {"act_threshold": 0.8}})
    assert err(stale) == (412, "version_conflict") and stale.json()["error"]["current_version"] == 2
    a = studio.put("/templates/support-triage/aliases/production", json={"version": 2}).json()
    assert a == {**a, "alias": "production", "version": 2, "previous_version": None}
    v = studio.get("/templates/support-triage/versions/production").json()
    assert v["version"] == 2 and v["changes"]["class"] == "extended" and v["changes"]["questions"]["wants_refund"] == "added"
    d = studio.get("/templates/support-triage/versions/2/diff").json()
    assert {"op": "replace", "path": "/settings/act_threshold", "value": 0.85} in d["changes"]


def test_call4_every_option(studio):
    f = studio.post("/files", files={"file": ("cracked.png", png(), "image/png")}).json()
    assert f["id"].startswith("file_") and f["type"] == "image"
    body = {"template": "support-triage@2", "model": MODEL,
            "variables": {"customer_message": "The screen arrived cracked.", "account_tier": "pro",
                          "customer_email": "ana@example.com", "screenshot": f["id"]},
            "questions": {"damage_visible": {"type": "noul", "instructions": "Visible damage?"}},
            "add_options": {"department": {"returns": "replacements"}}, "skip": ["churn_risk"],
            "settings": {"act_threshold": 0.8}, "metadata": {"ticket_id": "T-4412"}, "include": ["input"]}
    key = {"Idempotency-Key": "6c1b8f2e-2f0a-4b8e-9d51-1c6a4c3f7a10"}
    r = studio.post("/decisions", json=body, headers=key)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["template"]["resolved_from"] == "pinned"
    assert d["extensions"] == {"questions": ["damage_visible"], "options": {"department": ["returns"]}, "skipped": ["churn_risk"]}
    assert {k: a["origin"] for k, a in d["answers"].items()} == {"department": "extended", "urgency": "template",
                                                                "wants_refund": "template", "damage_visible": "extra"}
    assert d["settings"]["act_threshold"] == 0.8 and d["settings"]["sources"]["act_threshold"] == "request"
    assert d["input"]["variables"]["customer_email"]["$redacted"].startswith("hmac-sha256:")
    assert d["input"]["state"]["customer"]["email"] == "[redacted:customer_email]"
    assert d["input"]["media"][0]["variable"] == "screenshot" and d["input"]["media"][0]["available"]
    stored = studio.get(f"/decisions/{d['id']}").text
    assert "ana@example.com" not in stored
    replay = studio.post("/decisions", json=body, headers=key)
    assert replay.headers["x-basal-idempotent-replayed"] == "true" and replay.json() == d
    assert err(studio.post("/decisions", json={**body, "skip": []}, headers=key)) == (409, "idempotency_key_reused")
    rr = studio.post(f"/decisions/{d['id']}/rerun", json={})
    assert err(rr) == (409, "input_unavailable")                      # the sensitive value must be sent again
    rr = studio.post(f"/decisions/{d['id']}/rerun", json={"variables": {"customer_email": "ana@example.com"}}).json()
    assert rr["rerun_of"] == d["id"] and rr["source"]["surface"] == "rerun" and rr["extensions"] == d["extensions"]
    wrong = studio.post("/decisions", json={**body, "model": "laya"})
    assert err(wrong) == (400, "model_incompatible")                  # refused before any model loads
    assert wrong.json()["error"]["details"][0]["code"] == "modality_not_supported"


def test_call5_history_stats_feedback_examples(studio):
    for m in ("Billed twice, refund now", "App crashes on invoices", "Discount for 50 seats?"):
        for v in (1, 2):
            studio.post("/decisions", json={"template": f"support-triage@{v}", "variables": {"customer_message": m}}).raise_for_status()
    page = studio.get("/templates/support-triage/decisions", params={"act": "false", "created_after": "-7d", "limit": 2}).json()
    assert page["object"] == "list" and len(page["data"]) == 2 and page["data"][0]["object"] == "decision.summary"
    nxt = studio.get("/templates/support-triage/decisions", params={"act": "false", "limit": 2, "after": page["last_id"]}).json()
    assert not {x["id"] for x in nxt["data"]} & {x["id"] for x in page["data"]}
    st = studio.get("/templates/support-triage/stats").json()
    v1, v2 = st["groups"][0], st["groups"][1]
    assert v1["key"] == {"version": 1} and v1["questions"]["department"]["comparability"] == "reference"
    assert v2["questions"]["wants_refund"]["comparability"] == "added"
    assert v2["questions"]["department"]["excluded_runs"] >= 1           # the call-4 decision extended it
    cmp = studio.get("/templates/support-triage/compare", params={"versions": "1,2"}).json()
    assert cmp["questions"]["urgency"]["paired"]["n"] >= 3 and cmp["questions"]["urgency"]["paired"]["agreement"] == 1.0
    one = page["data"][0]["id"]
    fb = studio.post(f"/decisions/{one}/feedback", json={"expected": {"urgency": 3, "department": "billing"}}).json()
    assert {f["question"]: f["expected"] for f in fb["data"]} == {"urgency": "3", "department": "billing"}
    assert err(studio.post(f"/decisions/{one}/feedback", json={"expected": {"urgency": "very"}})) == (400, "invalid_expected")
    ex = studio.post("/templates/support-triage/examples", json={"from_decision": one, "expected": {"churn_risk": "yes"}, "tags": ["regression"]}).json()
    assert ex["expected"] == {"urgency": "3", "department": "billing", "churn_risk": "yes"} and ex["revision"] == 1
    dup = studio.post("/templates/support-triage/examples", json={"from_decision": one})
    assert err(dup) == (409, "example_exists") and dup.json()["error"]["existing_id"] == ex["id"]
    ed = studio.patch(f"/templates/support-triage/examples/{ex['id']}", json={"tags": ["edited"]}).json()
    assert ed["revision"] == 2 and ed["id"] == ex["id"]
    assert studio.get(f"/templates/support-triage/examples?revision=1").json()["data"][0]["tags"] == ["regression"]


# ---------------------------------------------------------------------------------------------------- the wire routes


def test_wire_body_unchanged_and_recorded(api, studio):
    kept = api.post("/v1/systemone", json=WIRE)
    none = api.post("/v1/systemone", json={**WIRE, "store": False})
    assert kept.status_code == none.status_code == 200
    assert kept.content == none.content                                  # storage never changes the answer
    assert set(kept.json()) == {"model", "answers", "usage"}
    assert kept.headers["x-basal-stored"] == "full" and none.headers["x-basal-stored"] == "none"
    assert "x-basal-decision-id" not in none.headers
    d = studio.get(f"/decisions/{kept.headers['x-basal-decision-id']}").json()
    assert d["template"] is None and d["source"]["endpoint"] == "/v1/systemone" and d["source"]["format"] == "typesafe"
    assert studio.get(f"/decisions/{kept.headers['x-basal-decision-id']}?format=typesafe").json()["answers"] == kept.json()["answers"]


def test_wire_lenient_extensions(api):
    r = api.post("/v1/systemone", json={**WIRE, "store": "maybe", "metadata": [1], "settings": {"act_threshold": 7}})
    assert r.status_code == 200 and r.headers["x-basal-warning"] == "store_ignored,metadata_ignored"
    assert api.post("/v1/systemone", json=WIRE, headers={"X-Basal-Store": "0"}).headers["x-basal-stored"] == "none"
    bad = api.post("/v1/systemone", json={"model": MODEL, "state": None, "questions": {}})
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"][:2] == ["body", "state"]


def test_wire_template_attribution(api, studio):
    starter = studio.get("/templates/builtin/support").json()
    body = {"model": MODEL, "state": "billed twice", "questions": {**starter["questions"], "extra": {"type": "noul"}}}
    r = api.post("/v1/systemone", json=body, headers={"X-Basal-Template": "builtin/support"})
    assert r.headers["x-basal-template-status"] == "attributed"
    d = studio.get(f"/decisions/{r.headers['x-basal-decision-id']}").json()
    assert d["template"]["attribution"] == "header" and d["answers"]["extra"]["origin"] == "extra"
    r = api.post("/v1/systemone", json=WIRE, headers={"X-Basal-Template": "builtin/support"})
    assert r.headers["x-basal-template-status"] == "mismatch"


def test_wire_idempotency(api):
    h = {"Idempotency-Key": "wire-key-1"}
    a = api.post("/v1/systemone", json=WIRE, headers=h)
    b = api.post("/v1/systemone", json=WIRE, headers=h)
    assert b.headers["x-basal-idempotent-replayed"] == "true" and a.content == b.content
    c = api.post("/v1/systemone", json={**WIRE, "state": "other"}, headers=h)
    assert c.status_code == 409 and "detail" in c.json()                 # the route's own error shape


def test_sdk_retry_is_folded(api, studio):
    body = {**WIRE, "state": "retry me"}
    first = api.post("/v1/systemone", json=body, headers={"X-TypeSafe-Retry-Count": "0"}).headers["x-basal-decision-id"]
    second = api.post("/v1/systemone", json=body, headers={"X-TypeSafe-Retry-Count": "1"}).headers["x-basal-decision-id"]
    assert studio.get(f"/decisions/{second}").json()["source"]["retry_of"] == first
    listed = {d["id"] for d in studio.get("/decisions", params={"limit": 100}).json()["data"]}
    assert second in listed and first not in listed


# ---------------------------------------------------------------------------------------------------- safety


def test_cross_site_guard(api):
    assert api.post("/v1/systemone", content=json.dumps(WIRE), headers={"Origin": "https://evil.example", "Content-Type": "text/plain"}).status_code == 403
    r = api.post("/v1/studio/decisions", json=WIRE, headers={"Sec-Fetch-Site": "same-site"})
    assert err(r) == (403, "cross_site_request")
    same = f"http://127.0.0.1:{api.base_url.port}"
    assert api.post("/v1/studio/decisions", json=WIRE, headers={"Origin": same}).status_code == 200
    r = api.post("/v1/studio/decisions", content=json.dumps(WIRE), headers={"Origin": same, "Content-Type": "text/plain"})
    assert err(r) == (415, "unsupported_media_type")
    assert api.post("/v1/systemone", content=json.dumps(WIRE), headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 200


def test_file_is_served_safely(studio):
    f = studio.post("/files", files={"file": ("x.png", png(), "image/png")}).json()
    r = studio.get(f"/files/{f['id']}/content")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert studio.delete(f"/files/{f['id']}").json()["deleted"]
    assert studio.get(f"/files/{f['id']}").status_code == 404


def test_storage_levels(studio):
    d = studio.post("/decisions", json={**WIRE, "store": "answers_only"}).json()
    assert d["store"] == "answers_only"
    got = studio.get(f"/decisions/{d['id']}").json()
    assert got["input"] is None and got["answers"]
    assert err(studio.get(f"/decisions/{d['id']}/input")) == (409, "input_unavailable")
    n = studio.post("/decisions", json={**WIRE, "store": False})
    assert n.headers["x-basal-stored"] == "none" and n.json()["store"] == "none"
    assert err(studio.get(f"/decisions/{n.json()['id']}")) == (404, "decision_not_found")
    assert err(studio.post("/decisions", json={**WIRE, "store": False, "background": True})) == (400, "background_requires_store")


def test_background_and_wait(studio):
    q = studio.post("/decisions", json={**WIRE, "background": True}).json()
    assert q["status"] == "queued"
    done = studio.get(f"/decisions/{q['id']}", params={"wait": 20}).json()
    assert done["status"] == "completed" and done["answers"]


def test_template_lifecycle(studio):
    t = {"variables": {"msg": {"type": "string"}}, "state": "{{msg}}", "questions": {"q": {"type": "noul", "instructions": "Ok?"}}}
    assert studio.post("/templates", json={"id": "tmp", **t}).status_code == 201
    assert err(studio.post("/templates", json={"id": "tmp", **t, "questions": {"z": {"type": "noul"}}})) == (409, "template_exists")
    studio.post("/decisions", json={"template": "tmp", "variables": {"msg": "hello"}}).raise_for_status()
    studio.post("/templates/tmp/archive").raise_for_status()
    warn = studio.post("/decisions", json={"template": "tmp", "variables": {"msg": "hello"}}).json()["warnings"]
    assert [w["code"] for w in warn] == ["template_archived"]
    assert err(studio.patch("/templates/tmp", json={"questions": {"r": {"type": "noul"}}})) == (409, "template_archived")
    assert err(studio.delete("/templates/tmp")) == (400, "confirmation_required")
    gone = studio.delete("/templates/tmp", params={"confirm": "tmp"}).json()
    assert gone["versions_kept"] == 1
    assert err(studio.post("/decisions", json={"template": "tmp", "variables": {"msg": "x"}})) == (404, "template_not_found")
    assert len(studio.get("/decisions", params={"template": "tmp"}).json()["data"]) == 2    # history is kept
    assert err(studio.post("/templates", json={"id": "tmp", **t})) == (409, "template_id_reserved")


def test_starter_templates(studio):
    lib = studio.get("/templates", params={"origin": "builtin", "limit": 100}).json()["data"]
    assert len(lib) >= 20 and all(t["id"].startswith("builtin/") for t in lib)
    assert err(studio.patch("/templates/builtin/support", json={"name": "x"})) == (403, "template_read_only")
    ex = studio.get("/templates/builtin/support/examples").json()["data"]
    assert ex and ex[0]["state"]
    clone = studio.post("/templates", json={"id": "my-support", "from": {"template": "builtin/support@1"}}).json()
    assert clone["change"] == "created" and list(clone["questions"]) == list(studio.get("/templates/builtin/support").json()["questions"])
    d = studio.post("/decisions", json={"template": "builtin/support", "state": ex[0]["state"], "model": MODEL}).json()
    assert d["template"]["id"] == "builtin/support"


def test_preview_matches_create(studio):
    body = {"template": "support-triage@production", "variables": {"customer_message": MSG, "account_tier": "enterprise"}}
    p = studio.post("/decisions/preview", json=body).json()
    d = studio.post("/decisions", json=body).json()
    assert p["object"] == "decision.preview" and p["settings"] == d["settings"] and p["template"] == d["template"]
    assert p["systemone_request"]["settings"] == {"temperature": 1.3}
    assert [w["code"] for w in p["warnings"]] == ["per_question_settings_not_portable"]


def test_settings_and_legacy_history(api, studio):
    s = studio.patch("/settings", json={"history": {"retention_days": 14}}).json()
    assert s["history"]["retention_days"] == 14 and s["storage"]["decisions"] > 0
    assert err(studio.patch("/settings", json={"history": {"retention_days": -1}})) == (400, "invalid_field")
    old = api.get("/api/history", params={"limit": 3}).json()
    assert len(old) == 3 and {"id", "time", "model", "request", "response"} <= set(old[0])


def test_studio_auth_envelope():
    proc, base = _start({"BASAL_API_KEY": "sekrit", "BASAL_AUTH_LOCAL": "1"})
    try:
        r = httpx.post(f"{base}/v1/studio/decisions", json=WIRE)
        assert err(r) == (401, "missing_api_key")
        assert err(httpx.post(f"{base}/v1/studio/decisions", json=WIRE, headers={"Authorization": "Bearer nope"})) == (401, "invalid_api_key")
        assert httpx.post(f"{base}/v1/systemone", json=WIRE).status_code == 403     # TypeSafe's own shape is unchanged
    finally:
        proc.terminate()
        proc.wait(10)
