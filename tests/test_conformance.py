"""Conformance of Bud Decision Studio's API with TypeSafe's Jev API and its gateway formats.

Runs against a live studio (BASAL_TEST_URL, default http://127.0.0.1:8420) with a small model
(BASAL_TEST_MODEL, default "laya"), which it loads if needed.

    .venv/bin/python -m pytest tests/test_conformance.py -q

Sources of truth (fetched from the publishers on first run and cached in tests/.cache/):
* TypeSafe's live OpenAPI 3.1 file: https://api.typesafe.ai/openapi.json
* The official SDKs: typesafe-sdk 0.7.2 (Python, requirements-dev.txt) and @typesafe-ai/sdk 0.6.0 (JavaScript, tests/js)
* OpenRouter's OpenAPI file (DecisionsResponse): https://openrouter.ai/openapi.yaml
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("BASAL_TEST_URL", "http://127.0.0.1:8420")
MODEL = os.environ.get("BASAL_TEST_MODEL", "laya")
def _spec(name: str, url: str) -> str:
    """A publisher's API specification, downloaded once into tests/.cache/."""
    cached = ROOT / "tests" / ".cache" / name
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(httpx.get(url, timeout=60, follow_redirects=True).raise_for_status().text)
    return cached.read_text()


TS_OPENAPI = json.loads(_spec("typesafe-openapi.json", "https://api.typesafe.ai/openapi.json"))
OR_OPENAPI = yaml.safe_load(_spec("openrouter-openapi.yaml", "https://openrouter.ai/openapi.yaml"))
REQ_ID = re.compile(r"^req_[0-9a-f]{32}$")

REQUEST = {
    "model": MODEL,
    "state": {"ticket": "I was charged twice for my order and I want a refund today, or I cancel."},
    "questions": {
        "team": {"type": "choice", "instructions": "Which team should handle this?",
                 "criteria": {"billing": "payments, refunds", "technical": {"covers": ["bugs", "crashes"]}, "other": None}},
        "urgency": {"type": "score", "instructions": "How urgent is it?",
                    "criteria": ["low", {"level": "medium"}, "high"]},
        "refund": {"type": "noul", "instructions": "Does the customer ask for a refund?"},
        "churn": {"type": "noul", "instructions": "Might they leave?", "criteria": {"true": "they threaten to cancel"}},
    },
}


def schema_validate(instance, openapi: dict, name: str):
    root = {k: v for k, v in openapi.items() if k in ("components",)}
    jsonschema.Draft202012Validator({**root, "$ref": f"#/components/schemas/{name}"}).validate(instance)


@pytest.fixture(scope="session")
def client():
    c = httpx.Client(base_url=BASE, timeout=600)
    c.post(f"/api/models/{MODEL}/load", json={}, headers={"X-Basal-Client": "tests"})
    for _ in range(600):
        m = next(x for x in c.get("/api/state").json()["models"] if x["id"] == MODEL)
        if (m.get("worker") or {}).get("status") == "ready":
            break
        time.sleep(1)
    else:
        pytest.fail(f"{MODEL} did not load")
    return c


# ------------------------------------------------------------------------------------------------ POST /v1/systemone

def test_response_matches_typesafe_schema_exactly(client):
    r = client.post("/v1/systemone", json=REQUEST)
    assert r.status_code == 200, r.text
    body = r.json()
    schema_validate(body, TS_OPENAPI, "SystemOneResponse")
    assert set(body) == {"model", "answers", "usage"}
    assert set(body["usage"]) == {"input_tokens", "output_tokens"}
    assert set(body["answers"]) == set(REQUEST["questions"])
    a = body["answers"]
    assert set(a["team"]) == {"type", "choice", "probabilities", "confidence"}
    assert set(a["team"]["probabilities"]) == set(REQUEST["questions"]["team"]["criteria"])
    assert abs(sum(a["team"]["probabilities"].values()) - 1) < 0.02
    assert set(a["urgency"]) == {"type", "score", "legend", "probabilities", "confidence"}
    assert a["urgency"]["legend"] == {"0": "low", "1": {"level": "medium"}, "2": "high"}   # echoes criteria values verbatim
    assert set(a["urgency"]["probabilities"]) == {"0", "1", "2"}
    assert set(a["refund"]) == {"type", "noul"} and 0 <= a["refund"]["noul"] <= 1
    assert REQ_ID.match(r.headers["x-typesafe-request-id"])


def test_extensions_only_on_request(client):
    r = client.post("/v1/systemone", json=REQUEST, headers={"X-Basal-Extensions": "1"})
    assert r.status_code == 200
    body = r.json()
    schema_validate(body, TS_OPENAPI, "SystemOneResponse")         # still valid TypeSafe
    assert "latency_ms" in body and "probabilities" in body["answers"]["refund"]


def test_validation_errors_are_fastapi_style_422(client):
    cases = [
        ({**REQUEST, "model": None} if False else {k: v for k, v in REQUEST.items() if k != "model"}, ["body", "model"]),
        ({**REQUEST, "state": None}, ["body", "state"]),
        ({**REQUEST, "questions": {}}, ["body", "questions"]),
        ({**REQUEST, "questions": {"q": {"type": "maybe", "instructions": "?"}}}, ["body", "questions", "q"]),
        ({**REQUEST, "questions": {"q": {"type": "score", "instructions": "?"}}}, ["body", "questions", "q", "score", "criteria"]),
    ]
    for body, loc_prefix in cases:
        r = client.post("/v1/systemone", json=body)
        assert r.status_code == 422, (body, r.text)
        err = r.json()
        schema_validate(err, TS_OPENAPI, "HTTPValidationError")
        assert any(e["loc"][:len(loc_prefix)] == loc_prefix for e in err["detail"]), err
        assert REQ_ID.match(r.headers["x-typesafe-request-id"])


def test_unknown_path_is_404_not_found(client):
    r = client.get("/v1/nothing-here")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert REQ_ID.match(r.headers["x-typesafe-request-id"])


def test_models_list_matches_schema(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    schema_validate(body, TS_OPENAPI, "ModelMetadataList")
    assert set(body) == {"models"}
    for m in body["models"]:
        assert set(m) == {"name", "description", "release_date"}
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", m["release_date"])
    assert MODEL in [m["name"] for m in body["models"]]


def test_alias_resolves_to_versioned_model(client):
    r = client.post("/v1/systemone", json={**REQUEST, "model": "jev-latest"})
    assert r.status_code == 200 and r.json()["model"] != "jev-latest"


def test_extra_request_fields_are_ignored(client):
    # Gateway users send extra top-level keys (OpenRouter provider/session_id/trace/user, Vercel providerOptions).
    body = {**REQUEST, "provider": {"order": ["x"]}, "session_id": "s1", "trace": {"a": 1}, "user": "u1",
            "providerOptions": {"gateway": {}}}
    assert client.post("/v1/systemone", json=body).status_code == 200


# ------------------------------------------------------------------------------------------------ official SDKs

def test_official_python_sdk():
    code = f"""
import json
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
from typesafe_sdk import TypeSafeNotFoundError, TypeSafeUnprocessableEntityError
c = TypeSafeClient(api_key="local", base_url="{BASE}", model="{MODEL}", timeout=120)
r = c.system_one(state="I was charged twice and want a refund.",
                 questions={{"team": Choice(instructions="Which team?", criteria={{"billing": "payments", "technical": None}}),
                            "urgency": Score(instructions="How urgent?", criteria=["low", "high"]),
                            "refund": Noul(instructions="Is a refund requested?")}})
out = {{"model": r.model, "choice": r.answers["team"].choice, "score": r.answers["urgency"].score,
        "noul": r.answers["refund"].noul, "request_id": r.request_id, "usage": [r.usage.input_tokens, r.usage.output_tokens]}}
ml = c.models.list(); out["models"] = [m.name for m in getattr(ml, "models", ml)]
try:
    c.system_one(model="no-such-model", state="x", questions={{"q": Noul(instructions="ok?")}})
    out["unknown"] = "none"
except TypeSafeNotFoundError:
    out["unknown"] = "TypeSafeNotFoundError"
print(json.dumps(out))
"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["model"] == MODEL and out["choice"] in ("billing", "technical")
    assert 0 <= out["score"] <= 1 and 0 <= out["noul"] <= 1
    assert REQ_ID.match(out["request_id"])
    assert out["unknown"] == "TypeSafeNotFoundError"
    if out["models"] is not None:
        assert MODEL in out["models"]


def test_official_javascript_sdk():
    js = ROOT / "tests/js"
    if not (js / "node_modules/@typesafe-ai/sdk").exists():
        pytest.skip("run: cd tests/js && npm install @typesafe-ai/sdk@0.6.0")
    p = subprocess.run(["node", "conformance.mjs"], cwd=js, capture_output=True, text=True, timeout=300,
                       env={**os.environ, "BASAL_TEST_URL": BASE, "BASAL_TEST_MODEL": MODEL})
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["ok"], out
    assert out["model"] == MODEL and out["teamChoice"] in ("billing", "technical", "other")
    assert out["teamProbKeys"] == ["billing", "other", "technical"]
    assert REQ_ID.match(out["requestId"])
    assert MODEL in out["modelNames"]
    assert out["unknownModelError"] == "NotFoundError"


# ------------------------------------------------------------------------------------------------ gateway formats

@pytest.mark.parametrize("path", ["/api/v1/systemone", "/api/alpha/decisions"])
def test_openrouter_format(client, path):
    r = client.post(path, json=REQUEST)
    assert r.status_code == 200, r.text
    body = r.json()
    schema_validate(body, OR_OPENAPI, "DecisionsResponse")
    assert body["id"].startswith("gen-dec-") and body["provider"] and body["usage"]["cost"] == 0.0
    bad = client.post(path, json={**REQUEST, "questions": {}})
    assert bad.status_code == 400 and set(bad.json()) == {"error"} and set(bad.json()["error"]) >= {"code", "message"}


def test_vercel_typesafe_route(client):
    r = client.post("/typesafe/v1/systemone", json=REQUEST)
    assert r.status_code == 200
    body = r.json()
    schema_validate({k: v for k, v in body.items() if k != "provider_metadata"}, TS_OPENAPI, "SystemOneResponse")
    assert "gateway" in body["provider_metadata"]
    bad = client.post("/typesafe/v1/systemone", json={**REQUEST, "questions": {"q": {"type": "maybe"}}})
    assert bad.status_code == 400 and set(bad.json()) == {"message", "error_type"}


def test_vercel_evaluate_boolean(client):
    body = {"model": MODEL, "state": "The build failed with exit code 1.",
            "questions": {"passed": {"type": "boolean", "instructions": "Did the build pass?"},
                          "kind": {"type": "choice", "criteria": {"test": None, "compile": None}}}}
    r = client.post("/v1/evaluate", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answers"]["passed"]["type"] == "boolean" and 0 <= out["answers"]["passed"]["probability"] <= 1
    assert set(out["usage"]) == {"inputTokens", "outputTokens"}


# ------------------------------------------------------------------------------------------------ auth semantics

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_auth_errors_match_typesafe():
    port = _free_port()
    env = {**os.environ, "BASAL_API_KEY": "sk-test-123", "BASAL_AUTH_LOCAL": "1", "BASAL_NO_DOWNLOADS": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "basal.server", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                httpx.get(url + "/v1/models", timeout=1); break
            except httpx.HTTPError:
                time.sleep(0.2)
        bad_body = {"state": None}
        r = httpx.post(url + "/v1/systemone", json=bad_body)
        assert r.status_code == 403
        assert r.json() == {"detail": {"error_type": "authentication_error", "message": "Must supply an API key! Check your request and try again."}}
        assert REQ_ID.match(r.headers["x-typesafe-request-id"])
        r = httpx.post(url + "/v1/systemone", json=bad_body, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401 and r.json()["detail"]["error_type"] == "authentication_error"
        r = httpx.post(url + "/v1/systemone", json=bad_body, headers={"Authorization": "Bearer sk-test-123"})
        assert r.status_code == 422      # authenticated, so the body is validated next
    finally:
        proc.terminate(); proc.wait(timeout=10)
