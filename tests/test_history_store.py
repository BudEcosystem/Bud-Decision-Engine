"""History storage (basal/db.py, basal/history.py): a temporary database per test.

    .venv/bin/python -m pytest tests/test_history_store.py -q
"""
import json
import shutil
import sqlite3
import time

import pytest

from basal import db, history
from basal.decisions import install_secret
from basal.errors import ApiError
from basal.ids import new_id
from basal.templates import secret_hash, sha


@pytest.fixture
def store(tmp_path):
    d = db.open(tmp_path / "studio.db")
    yield d
    d.close()


def rec(**kw) -> history.Record:
    q = kw.pop("questions", {"team": {"type": "choice", "criteria": ["billing", "tech"]}, "refund": {"type": "noul"}})
    answers = kw.pop("answers", {
        "team": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.95, "tech": 0.05}, "confidence": 0.9,
                 "decision": "billing", "top_probability": 0.95},
        "refund": {"type": "noul", "noul": 0.3, "probabilities": {"false": 0.7, "true": 0.3}, "decision": "no",
                   "top_probability": 0.7}})
    per = {k: {"act_threshold": 0.9} for k in answers or {}}
    act, _, low = history.gate_answers(answers, per, {}, set()) if answers else (None, [], None)
    base = dict(id=new_id("dec"), created_ms=db.now_ms(), completed_ms=db.now_ms(), status="completed", storage="full",
                source={"surface": "api", "endpoint": "/v1/systemone", "format": "typesafe", "client": "curl", "attempt": 0},
                template=None, model="laya", model_requested="laya", questions=q, input_hash=sha("x"), state={"ticket": "billed twice"},
                rendered_state="ticket: billed twice", settings={"act_threshold": 0.9, "temperature": 1.0, "questions": {}, "sources": {}},
                answers=answers, raw_probabilities={"team": {"billing": 0.9, "tech": 0.1}, "refund": {"false": 0.6, "true": 0.4}},
                act=act, min_certainty=low, metadata={})
    base.update(kw)
    return history.Record(**base)


def put(r):
    db.write(lambda c: history.insert(c, r))
    return r.id


NO_TEMPLATE = lambda ref: (_ for _ in ()).throw(AssertionError("no templates here"))   # noqa: E731


def ids(f):
    return [d["id"] for d in history.list_decisions(f, resolve_template=NO_TEMPLATE)["data"]]


def test_full_and_answers_only(store):
    a = put(rec())
    b = put(rec(storage="answers_only"))
    full = history.decision_object(a, {"input", "input.rendered_state", "answers.raw_probabilities"})
    assert full["input"]["state"] == {"ticket": "billed twice"}
    assert full["answers"]["team"]["raw_probabilities"] == {"billing": 0.9, "tech": 0.1}
    lean = history.decision_object(b, {"input"})
    assert lean["input"] is None and lean["answers"]["team"]["decision"] == "billing"
    row = db.read().execute("SELECT state, rendered_state, variables FROM decision_bodies b JOIN decisions d ON d.seq = b.decision_seq "
                            "WHERE d.id = ?", (b,)).fetchone()
    assert tuple(row) == (None, None, None)


def test_versions_and_outputs_are_immutable(store):
    a = put(rec())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.write(lambda c: c.execute("UPDATE decision_bodies SET answers = '{}' WHERE decision_seq = "
                                     "(SELECT seq FROM decisions WHERE id = ?)", (a,)))
    now = db.now_ms()
    db.write(lambda c: (c.execute("INSERT INTO templates (workspace_id, id, name, created_at, updated_at, created_by) "
                                  "VALUES ('ws_local','t','t',?,?,'x')", (now, now)),
                        c.execute("INSERT INTO template_versions (template_seq, number, definition, content_hash, questions_hash, "
                                  "question_set_key, source, changes, created_at, created_by) VALUES (1,1,'{}','h','h','h','api','{}',?,'x')",
                                  (now,))))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.write(lambda c: c.execute("UPDATE template_versions SET note = 'changed'"))


def test_projections_multi_and_number(store):
    q = {"topics": {"type": "multi", "criteria": ["bug", "billing", "praise"]},
         "days": {"type": "number", "criteria": [1, 7, 30]}, "none": {"type": "multi", "criteria": ["a"]}}
    ans = {"topics": {"type": "multi", "selected": ["bug", "billing"], "probabilities": {"bug": 0.9, "billing": 0.8, "praise": 0.1},
                      "threshold": 0.5, "decision": ["bug", "billing"], "top_probability": 0.9},
           "days": {"type": "number", "estimate": 9.2, "most_likely": 7, "range": [1, 30], "probabilities": {"1": 0.2, "7": 0.6, "30": 0.2},
                    "decision": 7, "top_probability": 0.6},
           "none": {"type": "multi", "selected": [], "probabilities": {"a": 0.1}, "threshold": 0.5, "decision": [], "top_probability": 0.9}}
    a = put(rec(questions=q, answers=ans))
    rows = db.read().execute("SELECT question_key, ordinal, answer, value FROM decision_answers ORDER BY position, ordinal").fetchall()
    assert [tuple(r) for r in rows] == [("topics", 0, "bug", 0.9), ("topics", 1, "billing", 0.8), ("days", 0, "7", 9.2),
                                        ("none", 0, None, None)]
    s = history.list_decisions({}, resolve_template=NO_TEMPLATE)["data"][0]
    assert s["id"] == a and s["answers"]["topics"]["decision"] == ["bug", "billing"] and s["answers"]["days"]["decision"] == "7"
    assert ids({"answer.topics": "billing"}) == [a]


def test_filters_and_cursors(store):
    first = put(rec(metadata={"ticket_id": "T-1"}))
    put(rec(model="kev-4b"))
    put(rec(source={"surface": "eval", "endpoint": "/x", "format": "studio", "attempt": 0}))
    assert len(ids({})) == 2                                       # eval runs are hidden by default
    assert len(ids({"surface": "all"})) == 3
    assert ids({"metadata.ticket_id": "T-1"}) == [first]
    assert len(ids({"model": "laya,kev-4b"})) == 2
    assert ids({"act": "true"}) == []                              # refund 0.7 < 0.9, so no decision acts
    assert len(ids({"needs_review": "refund"})) == 2
    assert ids({"certainty_below.team": "0.5"}) == []
    page1 = history.list_decisions({"limit": 1}, resolve_template=NO_TEMPLATE)
    put(rec())                                                     # a new decision arrives between pages
    page2 = history.list_decisions({"limit": 5, "after": page1["last_id"]}, resolve_template=NO_TEMPLATE)
    assert page1["has_more"] and [d["id"] for d in page2["data"]] == [first]
    tail = history.list_decisions({"order": "asc", "after": first}, resolve_template=NO_TEMPLATE)
    assert len(tail["data"]) == 2
    with pytest.raises(ApiError) as e:
        history.list_decisions({"created_after": "yesterday-ish"}, resolve_template=NO_TEMPLATE)
    assert e.value.code == "invalid_parameter"


def test_retry_folding(store):
    first = put(rec(source={"surface": "api", "endpoint": "/v1/systemone", "format": "typesafe", "client": "sdk", "attempt": 0}))
    retry = put(rec(source={"surface": "api", "endpoint": "/v1/systemone", "format": "typesafe", "client": "sdk", "attempt": 1}))
    assert ids({}) == [retry]
    assert ids({"fold_retries": "false"}) == [retry, first]
    assert history.decision_object(retry)["source"]["retry_of"] == first


def test_feedback_and_stats(store):
    a = put(rec())
    put(rec())
    fb = history.add_feedback(a, {"expected": {"team": "tech", "refund": True}})
    assert [(f["question"], f["expected"], f["correct"]) for f in fb] == [("team", "tech", False), ("refund", "yes", False)]
    with pytest.raises(ApiError) as e:
        history.add_feedback(a, {"expected": {"team": "sales"}})
    assert e.value.code == "invalid_expected"
    st = history.stats({}, resolve_template=NO_TEMPLATE)
    g = st["groups"][0]
    assert g["count"] == 2 and g["questions"]["team"]["distribution"] == {"billing": 1.0}
    assert g["questions"]["team"]["labelled"] == 1 and g["questions"]["team"]["accuracy"] == 0.0
    wi = history.stats({"what_if.act_threshold": "0.6"}, resolve_template=NO_TEMPLATE)["groups"][0]["what_if"]
    assert wi["act_rate"] == 1.0
    assert history.decision_object(a)["feedback"]["team"]["expected"] == "tech"


def test_retention_exemptions(store):
    old = db.now_ms() - 40 * 86400_000
    gone = put(rec(created_ms=old))
    pinned = put(rec(created_ms=old))
    labelled = put(rec(created_ms=old))
    fresh = put(rec())
    history.patch_decision(pinned, {"pinned": True})
    history.add_feedback(labelled, {"rating": 1})
    assert history.pending_deletion() == 1
    history.sweep()
    left = set(ids({"fold_retries": "false"}))
    assert gone not in left and {pinned, labelled, fresh} <= left


def test_erase_by_sensitive_value(store):
    h = secret_hash(install_secret(), "ana@example.com")
    a = put(rec(secrets={"email": h}, variables={"email": {"$redacted": h}}))
    put(rec())
    r = history.bulk({"filter": {"sensitive_hash": {"email": "ana@example.com"}}, "dry_run": True}, "delete", resolve_template=NO_TEMPLATE)
    assert (r["matched"], r["deleted"]) == (1, 0)
    history.bulk({"filter": {"sensitive_hash": {"email": "ana@example.com"}}}, "delete", resolve_template=NO_TEMPLATE)
    assert a not in ids({})
    with pytest.raises(ApiError):
        history.bulk({"filter": {}}, "delete", resolve_template=NO_TEMPLATE)


def test_redact(store):
    a = put(rec(metadata={"ticket_id": "T-9"}))
    history.redact_decision(a, ["input.state", "metadata.ticket_id"])
    d = history.decision_object(a)
    assert d["input"]["state"] == "[redacted]" and d["metadata"] == {} and d["answers"]["team"]["decision"] == "billing"


def test_search(store):
    if not store.search:
        pytest.skip("SQLite without FTS5")
    a = put(rec(rendered_state="invoice 4411 was billed twice"))
    put(rec(rendered_state="the app crashes"))
    assert ids({"q": "invoice 4411"}) == [a]


def test_migration_safety(tmp_path, monkeypatch):
    mig = tmp_path / "migrations"
    shutil.copytree(db.MIGRATIONS, mig)
    monkeypatch.setattr(db, "MIGRATIONS", mig)
    data = tmp_path / "data"
    data.mkdir()
    line = {"id": "abc123", "time": time.time() - 60, "model": "laya", "status": 200, "latency_ms": 12.0, "wall_ms": 15.0,
            "path": "/v1/systemone", "format": "typesafe", "client": "Studio", "request_id": "req_1",
            "request": {"model": "laya", "state": "billed twice", "questions": {"refund": {"type": "noul"}}},
            "response": {"answers": {"refund": {"type": "noul", "noul": 0.9, "probabilities": {"false": 0.1, "true": 0.9},
                                                "decision": "yes", "top_probability": 0.9}}}}
    (data / "activity.jsonl").write_text(json.dumps(line) + "\n")
    d = db.open(data / "studio.db")
    assert not (data / "activity.jsonl").exists() and (data / "activity.jsonl.imported").exists()
    got = history.list_decisions({"view": "full", "include": "input"}, resolve_template=NO_TEMPLATE)["data"]
    assert got[0]["source"]["surface"] == "playground" and got[0]["metadata"] == {"basal.legacy_id": "abc123"}
    assert got[0]["timing"]["model_ms"] == 12.0 and got[0]["answers"]["refund"]["act"] is True
    d.close()
    # a newer migration is backed up first
    (mig / "0099_extra.sql").write_text("CREATE TABLE extra (x INTEGER) STRICT;\n")
    d = db.open(data / "studio.db")
    assert d.applied == [99] and list((data / "backups").glob("studio-m0099-*.db"))
    d.close()
    # an applied migration that changed on disk stops startup
    (mig / "0099_extra.sql").write_text("CREATE TABLE extra (y INTEGER) STRICT;\n")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        db.open(data / "studio.db")
    # a database written by a newer studio opens read-only
    (mig / "0099_extra.sql").unlink()
    d = db.open(data / "studio.db")
    assert d.read_only
    with pytest.raises(db.HistoryUnavailable):
        db.write(lambda c: None)
    d.close()
