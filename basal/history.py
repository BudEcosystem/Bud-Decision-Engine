"""Decision history: recording every decision, and reading it back.

A decision is written once, after the model answers, in one transaction: a small "hot" row (decisions) for lists and
filters, a "cold" body (decision_bodies) read only when one decision is opened, one row per answer
(decision_answers) for filters and statistics, plus metadata, media links and the hashes of sensitive values.
docs/studio-api.md sections 2.3, 3.3 to 3.5, 4.5 to 4.9 and 5.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from . import __version__, blobs, db
from .contract import certainty as answer_certainty
from .contract import recalibrate, r4
from .errors import ApiError
from .ids import ULID_RE, new_id
from .templates import canonical, question_set_key, questions_hash

HIDDEN_SURFACES = ("eval", "order_test", "replay")
LEVELS = ("full", "answers_only", "none")
DEFAULT_SETTINGS = {
    "history.store": "full", "history.retention_days": 30, "history.max_storage_gb": 20, "history.store_media": True,
    "history.keep_labelled": True, "history.on_store_error": "serve", "history.notice_acknowledged_at": None,
    "decisions.default_act_threshold": 0.9,
}
RECENT: deque = deque(maxlen=500)      # every decision, stored or not, for the live activity charts
SWEEP = {"last_at": None, "last_deleted": 0, "pending": 0, "error": None}
STORE_ERRORS: deque = deque(maxlen=20)
DEC_ID = re.compile(rf"^dec_{ULID_RE}$")


def private(*levels) -> str:
    """The most private of several storage levels."""
    vals = [l for l in levels if l]
    return max(vals, key=LEVELS.index) if vals else "full"


def parse_store(v) -> str | None:
    """Body `store` or header X-Basal-Store -> a level, or None when the value means nothing."""
    if v is True or v in ("1", "true", "full", "True"):
        return "full"
    if v is False or v in ("0", "false", "none", "False"):
        return "none"
    if v == "answers_only":
        return "answers_only"
    return None


# ----------------------------------------------------------------------------------------------------------------------
# Settings (per workspace)


def settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    if db.available():
        for r in db.read().execute("SELECT key, value FROM settings WHERE workspace_id = ?", (db.WS,)):
            if r["key"] in out:
                out[r["key"]] = json.loads(r["value"])
    return out


def setting(key: str):
    return settings()[key]


def save_settings(changes: dict):
    now = db.now_ms()

    def tx(c):
        for k, v in changes.items():
            c.execute("INSERT INTO settings (workspace_id, key, value, updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(workspace_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                      (db.WS, k, json.dumps(v), now))
        audit(c, "settings.changed", None, {"keys": sorted(changes)})
    db.write(tx)


def audit(c, action: str, object_id: str | None, data: dict | None = None, actor: str = "local"):
    c.execute("INSERT INTO audit_events (workspace_id, at, actor, action, object_id, data) VALUES (?,?,?,?,?,?)",
              (db.WS, db.now_ms(), actor, action, object_id, json.dumps(data) if data else None))


# ----------------------------------------------------------------------------------------------------------------------
# Answers: canonical labels, gates, projections


def label_of(a: dict):
    """An answer's canonical `decision` value (section 2.3): the vocabulary of feedback, filters, stats and export."""
    t = a.get("type")
    if t == "multi":
        return list(a.get("decision") or a.get("selected") or [])
    d = a.get("decision")
    if t == "number":
        return d
    return None if d is None else str(d)


def label_text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def gate_answers(answers: dict, per: dict, origins: dict, dynamic: set) -> tuple[bool, list[str], float | None]:
    """Adds certainty, act, origin (and dynamic_options) to each answer. -> (decision act, needs_review, min certainty)."""
    review, lowest = [], None
    for k, a in answers.items():
        c = answer_certainty(a)
        a["certainty"] = c
        a["act"] = bool(c >= per.get(k, {}).get("act_threshold", 0.9))
        a["origin"] = origins.get(k, "adhoc")
        if k in dynamic:
            a["dynamic_options"] = True
        if not a["act"]:
            review.append(k)
        lowest = c if lowest is None else min(lowest, c)
    return not review, review, lowest


def canonical_label(q: dict, value, where: str):
    """A label as a caller wrote it -> the canonical form for that question, or ApiError(400 invalid_expected)."""
    t = q["type"]

    def bad(msg):
        raise ApiError(400, "invalid_expected", msg, where)
    if t == "choice" or t == "rank":
        names = list(q["criteria"]) if isinstance(q.get("criteria"), dict) else [str(x) for x in q.get("criteria") or []]
        if isinstance(value, list) and t == "rank":
            value = value[0] if value else None
        if not isinstance(value, str) or value not in names:
            bad(f"Expected one of {', '.join(names[:20])}{'...' if len(names) > 20 else ''}; got {json.dumps(value)}.")
        return value
    if t == "noul":
        if value in (True, "true", "yes", "True"):
            return "yes"
        if value in (False, "false", "no", "False"):
            return "no"
        bad(f"Expected yes or no; got {json.dumps(value)}.")
    if t == "score":
        levels = q.get("criteria") or []
        if isinstance(value, bool):
            bad("Expected a level key such as \"0\", or the level's text.")
        if isinstance(value, int) and 0 <= value < len(levels):
            return str(value)
        if isinstance(value, str):
            if value.isdigit() and 0 <= int(value) < len(levels):
                return value
            texts = [x if isinstance(x, str) else json.dumps(x) for x in levels]
            if value in texts:
                return str(texts.index(value))
        bad(f"Expected a level from \"0\" to \"{len(levels) - 1}\", or a level's text; got {json.dumps(value)}.")
    if t == "multi":
        names = list(q["criteria"]) if isinstance(q.get("criteria"), dict) else [str(x) for x in q.get("criteria") or []]
        if not isinstance(value, list) or not all(isinstance(x, str) and x in names for x in value):
            bad(f"Expected a list of options from: {', '.join(names)}.")
        return sorted(set(value), key=names.index)
    if t == "number":
        try:
            v = float(value)
        except (TypeError, ValueError):
            bad(f"Expected a number; got {json.dumps(value)}.")
        return int(v) if v.is_integer() else v
    bad("Unknown question type.")


def is_correct(q: dict, answer: dict | None, expected) -> bool | None:
    if expected is None or not answer:
        return None
    t = q["type"]
    if t == "multi":
        return set(expected) == set(label_of(answer) or [])
    if t == "number":
        lo, hi = (answer.get("range") or [None, None])
        if lo is None:
            return float(answer.get("decision")) == float(expected)
        return float(lo) <= float(expected) <= float(hi)
    return str(label_of(answer)) == str(expected)


# ----------------------------------------------------------------------------------------------------------------------
# Recording


@dataclass
class Record:
    """Everything one decision stores; built by decisions.py."""
    id: str
    created_ms: int
    completed_ms: int | None
    status: str
    storage: str                      # full | answers_only (none is never recorded)
    source: dict                      # surface, endpoint, format, client, request_id, attempt
    template: dict | None             # {id, version, ref, resolved_from, attribution, seq}
    model: str | None
    model_requested: str | None
    questions: dict                   # effective questions, in the order the model saw them
    input_hash: str
    variables_hash: str | None = None
    variables: dict | None = None
    state: Any = None
    rendered_state: str | None = None
    media: list = field(default_factory=list)   # blobs.MediaRef
    extensions: dict = field(default_factory=lambda: {"questions": [], "options": {}, "skipped": []})
    settings: dict = field(default_factory=dict)
    answers: dict | None = None
    raw_probabilities: dict | None = None
    act: bool | None = None
    min_certainty: float | None = None
    timing: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    passes: int | None = None
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    error: dict | None = None
    http_status: int | None = None
    request_extras: dict | None = None
    rerun_of: str | None = None
    group: dict | None = None


def insert(c, r: Record, search: bool | None = None) -> int:
    """Write one decision (section 5.4). Runs inside the caller's transaction. -> the decision's seq."""
    qh, qk = questions_hash(r.questions), question_set_key(r.questions)
    c.execute("INSERT OR IGNORE INTO question_sets (hash, set_key, definition, created_at) VALUES (?,?,?,?)",
              (qh, qk, canonical(r.questions), r.created_ms))
    t = r.template or {}
    src = r.source
    retry_of = None
    if src.get("attempt", 0) >= 1:
        prev = c.execute("SELECT seq FROM decisions WHERE workspace_id = ? AND client IS ? AND endpoint = ? AND input_hash = ? "
                         "AND questions_hash = ? AND attempt = ? AND created_at >= ? ORDER BY seq DESC LIMIT 1",
                         (db.WS, src.get("client"), src.get("endpoint"), r.input_hash, qh, src["attempt"] - 1,
                          r.created_ms - 120_000)).fetchone()
        if prev:
            retry_of = prev[0]
            c.execute("UPDATE decisions SET superseded = 1 WHERE seq = ?", (retry_of,))
            c.execute("UPDATE decision_answers SET counted = 0 WHERE decision_seq = ?", (retry_of,))
    timing = r.timing or {}
    cur = c.execute(
        "INSERT INTO decisions (id, workspace_id, actor, created_at, completed_at, status, storage, surface, endpoint, format, "
        "client, request_id, attempt, retry_of_seq, template_seq, version_number, template_ref, resolved_from, attribution, "
        "model, model_requested, studio_version, questions_hash, question_set_key, input_hash, variables_hash, extended, "
        "dynamic_options, n_questions, n_media, act, min_certainty, queue_ms, load_ms, model_ms, total_ms, input_tokens, "
        "http_status, error_code, group_type, group_id, rerun_of_seq, metadata, body_bytes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r.id, db.WS, "local", r.created_ms, r.completed_ms, r.status, r.storage, src.get("surface", "api"),
         src.get("endpoint", ""), src.get("format", "studio"), src.get("client"), src.get("request_id"), src.get("attempt", 0),
         retry_of, t.get("seq"), t.get("version"), t.get("ref"), t.get("resolved_from"), t.get("attribution"),
         r.model, r.model_requested, __version__, qh, qk, r.input_hash, r.variables_hash,
         int(bool(r.extensions.get("questions") or r.extensions.get("options") or r.extensions.get("skipped"))),
         int(any((a or {}).get("dynamic_options") for a in (r.answers or {}).values())), len(r.questions), len(r.media),
         None if r.act is None else int(r.act), r.min_certainty, timing.get("queue_ms"), timing.get("load_ms"),
         timing.get("model_ms"), timing.get("total_ms"), (r.usage or {}).get("input_tokens"), r.http_status,
         (r.error or {}).get("code"), (r.group or {}).get("type"), (r.group or {}).get("id"),
         _seq_of(c, r.rerun_of) if r.rerun_of else None, json.dumps(r.metadata or {}), 0))
    seq = cur.lastrowid
    full = r.storage == "full"
    body = (json.dumps(r.variables) if full and r.variables is not None else None,
            json.dumps(r.state) if full else None, r.rendered_state if full else None,
            json.dumps(r.extensions), json.dumps(r.settings), json.dumps(r.answers) if r.answers is not None else None,
            json.dumps(r.raw_probabilities) if r.raw_probabilities is not None else None,
            json.dumps({"usage": r.usage, "passes": r.passes, "notes": r.notes, "warnings": r.warnings, "timing": r.timing}),
            json.dumps(r.error) if r.error else None, json.dumps(r.request_extras) if (full and r.request_extras) else None)
    c.execute("INSERT INTO decision_bodies (decision_seq, variables, state, rendered_state, extensions, settings, answers, "
              "raw_probabilities, extras, error, request_extras) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (seq,) + body)
    c.execute("UPDATE decisions SET body_bytes = ? WHERE seq = ?", (sum(len(x) for x in body if x), seq))
    _project_answers(c, seq, r)
    for k, v in (r.metadata or {}).items():
        c.execute("INSERT INTO decision_metadata (decision_seq, key, value) VALUES (?,?,?)", (seq, k, str(v)))
    for i, m in enumerate(r.media):
        c.execute("INSERT INTO decision_media (decision_seq, position, file_seq, type, name, variable, bytes, sha256) "
                  "VALUES (?,?,?,?,?,?,?,?)", (seq, i, m.file_seq if full else None, m.type, m.name if full else None,
                                              m.variable, m.bytes, m.sha256))
        if m.file_seq and full:
            c.execute("UPDATE files SET last_ref_at = ? WHERE seq = ?", (r.created_ms, m.file_seq))
    for var, h in (r.secrets or {}).items():
        c.execute("INSERT INTO decision_secrets (decision_seq, variable, hmac) VALUES (?,?,?)", (seq, var, h))
    if full and (db.get().search if search is None else search):
        text = search_text(r)
        if text:
            c.execute("INSERT INTO decision_fts (rowid, text) VALUES (?, ?)", (seq, text))
    if t.get("seq"):
        c.execute("UPDATE templates SET last_used_at = ? WHERE seq = ?", (r.created_ms, t["seq"]))
    return seq


def search_text(r: Record) -> str:
    parts = [r.rendered_state or ""]
    for k, v in (r.variables or {}).items():
        if isinstance(v, str) and not v.startswith("file_") and not v.startswith("data:"):
            parts.append(v)
    return "\n".join(p for p in parts if p)[:200_000]


def _counted(r: Record) -> int:
    if r.source.get("surface") in HIDDEN_SURFACES:
        return 0
    if (r.template or {}).get("attribution") in ("draft", "inferred"):
        return 0
    return 1


def _project_answers(c, seq: int, r: Record):
    if not r.answers:
        return
    t = r.template or {}
    counted = _counted(r)
    for pos, (k, a) in enumerate(r.answers.items()):
        if a is None:
            continue
        typ = a.get("type")
        base = (seq, k, t.get("seq"), t.get("version"), r.model, r.created_ms, typ, a.get("certainty", 0.0),
                int(bool(a.get("act"))), a.get("origin", "adhoc"), int(bool(a.get("dynamic_options"))), counted, pos)
        sql = ("INSERT INTO decision_answers (decision_seq, question_key, template_seq, version_number, model, created_at, "
               "type, certainty, act, origin, dynamic_options, counted, position, ordinal, answer, value) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        if typ == "multi":
            sel = list(a.get("selected") or [])
            probs = a.get("probabilities") or {}
            if not sel:
                c.execute(sql, base + (0, None, None))
            for i, o in enumerate(sel):
                c.execute(sql, base + (i, o, probs.get(o)))
            continue
        value = {"noul": a.get("noul"), "score": a.get("score"), "number": a.get("estimate")}.get(typ, a.get("top_probability"))
        c.execute(sql, base + (0, label_text(label_of(a)), value))


def _seq_of(c, decision_id: str | None) -> int | None:
    if not decision_id:
        return None
    row = c.execute("SELECT seq FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return row[0] if row else None


def count_usage(*, model: str | None, template_seq: int | None, surface: str, fmt: str, status: int, stored: str,
                model_ms: float | None = None, total_ms: float | None = None, conn=None):
    """Content-free counters for every request, stored or not, accepted or rejected."""
    RECENT.appendleft({"time": time.time(), "model": model, "status": status, "surface": surface, "format": fmt,
                       "total_ms": total_ms, "stored": stored})
    if not db.available() or db.get().read_only:
        return
    minute = int(time.time() // 60)

    def tx(c):
        c.execute("INSERT INTO usage_counters (workspace_id, minute, model, template_seq, surface, format, status, stored, count, "
                  "model_ms_sum, total_ms_sum) VALUES (?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT DO UPDATE SET count = count + 1, "
                  "model_ms_sum = model_ms_sum + excluded.model_ms_sum, total_ms_sum = total_ms_sum + excluded.total_ms_sum",
                  (db.WS, minute, model or "", template_seq or 0, surface, fmt, status, stored, model_ms or 0, total_ms or 0))
    try:
        if conn is not None:
            tx(conn)
        else:
            db.write(tx)
    except Exception as e:   # noqa: BLE001 - counters never fail a request
        print(f"[history] usage counter: {e}", flush=True)


# ----------------------------------------------------------------------------------------------------------------------
# Reading one decision


def _row(decision_id: str):
    c = db.read()
    row = c.execute("SELECT d.*, t.id AS template_id FROM decisions d LEFT JOIN templates t ON t.seq = d.template_seq "
                    "WHERE d.id = ? AND d.workspace_id = ?", (decision_id, db.WS)).fetchone()
    if not row:
        raise ApiError(404, "decision_not_found", f"No stored decision '{decision_id}'. It may have been deleted, removed by "
                                                   "retention, or made with store: none (never saved).")
    return row


def expires_at(row, st: dict | None = None, tpl_retention: int | None = None) -> int | None:
    st = st or settings()
    if row["pinned"] or row["eval_seq"] is not None:
        return None
    if row["has_feedback"] and st["history.keep_labelled"]:
        return None
    days = tpl_retention if tpl_retention is not None else st["history.retention_days"]
    if not days:
        return None
    return int(row["created_at"] / 1000 + days * 86400)


def _template_retention(c, seq: int | None) -> int | None:
    if not seq:
        return None
    r = c.execute("SELECT retention_days FROM templates WHERE seq = ?", (seq,)).fetchone()
    return r[0] if r else None


def decision_object(decision_id: str, include: set[str] = frozenset({"input"})) -> dict:
    row = _row(decision_id)
    return _full(row, include)


def _full(row, include: set[str]) -> dict:
    c = db.read()
    seq = row["seq"]
    b = c.execute("SELECT * FROM decision_bodies WHERE decision_seq = ?", (seq,)).fetchone()
    extras = json.loads(b["extras"]) if b and b["extras"] else {}
    answers = json.loads(b["answers"]) if b and b["answers"] else None
    raw = json.loads(b["raw_probabilities"]) if b and b["raw_probabilities"] else None
    if answers:
        for k, a in answers.items():
            if "answers.raw_probabilities" in include and raw and k in raw:
                a["raw_probabilities"] = raw[k]
            if "answers.model_extras" not in include:
                a.pop("model_extras", None)
    qdef = c.execute("SELECT definition FROM question_sets WHERE hash = ?", (row["questions_hash"],)).fetchone()
    questions = json.loads(qdef[0]) if qdef else {}
    out = {"id": row["id"], "object": "decision", "status": row["status"], "created_at": row["created_at"] // 1000,
           "completed_at": row["completed_at"] // 1000 if row["completed_at"] else None,
           "template": _template_ref(row), "model": row["model"], "model_requested": row["model_requested"],
           "model_revision": row["model_revision"]}
    if "input" in include or "input.rendered_state" in include:
        out["input"] = _input(row, b, questions, "input.rendered_state" in include)
    needs = [k for k, a in (answers or {}).items() if not a.get("act")]
    st = settings()
    out.update({
        "extensions": json.loads(b["extensions"]) if b else {"questions": [], "options": {}, "skipped": []},
        "answers": answers, "act": None if row["act"] is None else bool(row["act"]),
        "needs_review": needs if answers is not None else [],
        "settings": json.loads(b["settings"]) if b else {},
        "usage": extras.get("usage") or {}, "timing": extras.get("timing") or _timing(row),
        "passes": extras.get("passes"), "notes": extras.get("notes") or [], "warnings": extras.get("warnings") or [],
        "source": _source(c, row), "group": {"type": row["group_type"], "id": row["group_id"]} if row["group_id"] else None,
        "batch": None, "eval": None,
        "rerun_of": _id_of(c, row["rerun_of_seq"]),
        "metadata": json.loads(row["metadata"] or "{}"), "pinned": bool(row["pinned"]),
        "feedback": latest_feedback(c, seq), "store": row["storage"],
        "expires_at": expires_at(row, st, _template_retention(c, row["template_seq"])),
        "error": json.loads(b["error"]) if b and b["error"] else None,
    })
    return out


def _input(row, b, questions: dict, rendered: bool) -> dict | None:
    if row["storage"] != "full" or b is None:
        return None
    redacted = json.loads(b["redacted"]) if b["redacted"] else []
    media = []
    for m in db.read().execute("SELECT m.*, f.id AS file_id, f.content_type, b.stored FROM decision_media m "
                               "LEFT JOIN files f ON f.seq = m.file_seq LEFT JOIN blobs b ON b.sha256 = f.blob_sha256 "
                               "WHERE m.decision_seq = ? ORDER BY m.position", (row["seq"],)):
        media.append({"type": m["type"], "file_id": m["file_id"], "name": m["name"], "content_type": m["content_type"],
                      "bytes": m["bytes"], "variable": m["variable"], "sha256": "sha256:" + m["sha256"],
                      "available": bool(m["file_id"] and m["stored"])})
    out = {"variables": json.loads(b["variables"]) if b["variables"] else None,
           "state": json.loads(b["state"]) if b["state"] is not None else None,
           "media": media, "questions": questions}
    if rendered:
        out["rendered_state"] = b["rendered_state"]
    if redacted:
        out["redacted"] = redacted
    return out


def _timing(row) -> dict:
    return {"queue_ms": row["queue_ms"], "load_ms": row["load_ms"], "model_ms": row["model_ms"], "total_ms": row["total_ms"]}


def _template_ref(row) -> dict | None:
    if not row["template_seq"]:
        return None
    return {"id": row["template_id"], "version": row["version_number"], "ref": row["template_ref"],
            "resolved_from": row["resolved_from"], "attribution": row["attribution"]}


def _source(c, row) -> dict:
    return {"surface": row["surface"], "endpoint": row["endpoint"], "format": row["format"], "client": row["client"],
            "request_id": row["request_id"], "attempt": row["attempt"], "retry_of": _id_of(c, row["retry_of_seq"])}


def _id_of(c, seq):
    if not seq:
        return None
    r = c.execute("SELECT id FROM decisions WHERE seq = ?", (seq,)).fetchone()
    return r[0] if r else None


def latest_feedback(c, seq: int) -> dict:
    out = {}
    for f in c.execute("SELECT * FROM feedback WHERE decision_seq = ? ORDER BY updated_at", (seq,)):
        if f["question_key"] == "*":
            continue
        out[f["question_key"]] = {"expected": _json_label(f["expected"]), "correct": None if f["correct"] is None else bool(f["correct"]),
                                  "feedback_id": f["id"]}
    return out


def _json_label(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except ValueError:
        return v


def summary(row, answers_rows: list) -> dict:
    answers: dict = OrderedDict()
    for a in sorted(answers_rows, key=lambda x: (x["position"], x["ordinal"])):
        k = a["question_key"]
        if a["type"] == "multi":
            cur = answers.setdefault(k, {"type": "multi", "decision": [], "certainty": a["certainty"], "act": bool(a["act"])})
            if a["answer"] is not None:
                cur["decision"].append(a["answer"])
        else:
            answers[k] = {"type": a["type"], "decision": a["answer"], "certainty": a["certainty"], "act": bool(a["act"])}
    needs = [k for k, a in answers.items() if not a["act"]]
    tpl = {"id": row["template_id"], "version": row["version_number"]} if row["template_seq"] else None
    return {"id": row["id"], "object": "decision.summary", "status": row["status"], "created_at": row["created_at"] // 1000,
            "template": tpl, "model": row["model"],
            "source": {"surface": row["surface"], "client": row["client"], "attempt": row["attempt"]},
            "extended": bool(row["extended"]), "act": None if row["act"] is None else bool(row["act"]),
            "needs_review": needs, "answers": answers, "timing": {"total_ms": row["total_ms"]},
            "metadata": json.loads(row["metadata"] or "{}"), "pinned": bool(row["pinned"]),
            "labelled": bool(row["has_feedback"]),
            "error": {"code": row["error_code"]} if row["error_code"] else None}


# ----------------------------------------------------------------------------------------------------------------------
# Filters (section 3.3)


REL = re.compile(r"^-(\d+(?:\.\d+)?)([smhdw])$")


def parse_time(v: str | int | float | None, where: str) -> int | None:
    """Unix seconds, RFC 3339, or relative (-30m, -24h, -7d) -> Unix milliseconds."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(float(v) * 1000)
    s = str(v).strip()
    m = REL.match(s)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return int((time.time() - float(m.group(1)) * mult) * 1000)
    try:
        return int(float(s) * 1000)
    except ValueError:
        pass
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp() * 1000)
    except ValueError:
        raise ApiError(400, "invalid_parameter", f"{where} takes Unix seconds, an RFC 3339 time or a relative time such as -7d; "
                                                 f"got {s!r}.", where)


def csv_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            out += csv_list(x)
        return out
    return [x.strip() for x in str(v).split(",") if x.strip()]


def truthy(v) -> bool | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise ApiError(400, "invalid_parameter", f"Expected true or false; got {v!r}.")


@dataclass
class Query:
    where: list[str] = field(default_factory=list)
    args: list = field(default_factory=list)
    applied: dict = field(default_factory=dict)

    def add(self, sql: str, *args):
        self.where.append(sql)
        self.args.extend(args)


def build_filter(f: dict, *, resolve_template, fixed_template: str | None = None) -> Query:
    """Query parameters -> SQL over `decisions d`. resolve_template(ref) -> (template_seq, version or None)."""
    q = Query()
    q.add("d.workspace_id = ?", db.WS)
    tref = fixed_template or f.get("template")
    if tref:
        if tref == "none":
            q.add("d.template_seq IS NULL")
        else:
            tseq, version = resolve_template(tref)
            q.add("d.template_seq = ?", tseq)
            q.applied["template"] = tref
            if version is not None:
                q.add("d.version_number = ?", version)
        att = csv_list(f.get("attribution")) or ["explicit", "header"]
        if tref != "none" and "all" not in att:
            q.add(f"d.attribution IN ({','.join('?' * len(att))})", *att)
            q.applied["attribution"] = att
    versions = csv_list(f.get("version"))
    if versions:
        try:
            vs = [int(v) for v in versions]
        except ValueError:
            raise ApiError(400, "invalid_parameter", "version takes version numbers such as 2,3.", "version")
        q.add(f"d.version_number IN ({','.join('?' * len(vs))})", *vs)
        q.applied["version"] = vs
    for key, col in (("model", "model"), ("model_revision", "model_revision"), ("status", "status"), ("endpoint", "endpoint"),
                     ("format", "format"), ("client", "client"), ("request_id", "request_id"), ("questions_hash", "questions_hash"),
                     ("question_set_key", "question_set_key"), ("input_hash", "input_hash"), ("group", "group_id")):
        vals = csv_list(f.get(key))
        if vals:
            q.add(f"d.{col} IN ({','.join('?' * len(vals))})", *vals)
            q.applied[key] = vals
    surf = csv_list(f.get("surface"))
    if surf and "all" not in surf:
        q.add(f"d.surface IN ({','.join('?' * len(surf))})", *surf)
        q.applied["surface"] = surf
    elif not surf:
        q.add(f"d.surface NOT IN ({','.join('?' * len(HIDDEN_SURFACES))})", *HIDDEN_SURFACES)
    ca, cb = parse_time(f.get("created_after"), "created_after"), parse_time(f.get("created_before"), "created_before")
    if ca is not None:
        q.add("d.created_at >= ?", ca)
        q.applied["created_after"] = ca // 1000
    if cb is not None:
        q.add("d.created_at < ?", cb)
        q.applied["created_before"] = cb // 1000
    act = truthy(f.get("act"))
    if act is not None:
        q.add("d.act = ?", int(act))
    for k in csv_list(f.get("needs_review")):
        q.add("EXISTS (SELECT 1 FROM decision_answers a WHERE a.decision_seq = d.seq AND a.question_key = ? AND a.act = 0)", k)
    for key, vals in f.items():
        if key.startswith("answer."):
            qk = key[7:]
            vs = csv_list(vals)
            norm = [{"true": "yes", "false": "no"}.get(v.lower(), v) for v in vs]
            q.add(f"EXISTS (SELECT 1 FROM decision_answers a WHERE a.decision_seq = d.seq AND a.question_key = ? "
                  f"AND a.answer IN ({','.join('?' * len(norm))}))", qk, *norm)
        elif key.startswith("certainty_below.") or key.startswith("certainty_above."):
            below = key.startswith("certainty_below.")
            qk = key.split(".", 1)[1]
            try:
                x = float(vals if not isinstance(vals, list) else vals[0])
            except ValueError:
                raise ApiError(400, "invalid_parameter", f"{key} takes a number between 0 and 1.", key)
            q.add(f"EXISTS (SELECT 1 FROM decision_answers a WHERE a.decision_seq = d.seq AND a.question_key = ? "
                  f"AND a.certainty {'<' if below else '>'} ?)", qk, x)
        elif key.startswith("metadata."):
            q.add("EXISTS (SELECT 1 FROM decision_metadata m WHERE m.decision_seq = d.seq AND m.key = ? AND m.value = ?)",
                  key[9:], vals if not isinstance(vals, list) else vals[0])
    ext = truthy(f.get("extended"))
    if ext is not None:
        q.add("d.extended = ?", int(ext))
    lab = truthy(f.get("labelled"))
    if lab is not None:
        q.add("d.has_feedback = ?", int(lab))
    cor = truthy(f.get("correct"))
    if cor is False:
        q.add("EXISTS (SELECT 1 FROM feedback x WHERE x.decision_seq = d.seq AND x.correct = 0)")
    elif cor is True:
        q.add("d.has_feedback = 1 AND NOT EXISTS (SELECT 1 FROM feedback x WHERE x.decision_seq = d.seq AND x.correct = 0)")
    pin = truthy(f.get("pinned"))
    if pin is not None:
        q.add("d.pinned = ?", int(pin))
    if f.get("rerun_of"):
        q.add("d.rerun_of_seq = (SELECT seq FROM decisions WHERE id = ?)", f["rerun_of"])
    if truthy(f.get("fold_retries")) is not False:
        q.add("d.superseded = 0")
    if f.get("q"):
        if not db.get().search:
            raise ApiError(400, "search_unavailable", "Full-text search needs an SQLite build with FTS5, which this one lacks.", "q")
        q.add("d.seq IN (SELECT rowid FROM decision_fts WHERE decision_fts MATCH ?)", _fts_query(f["q"]))
    sens = f.get("sensitive_hash")
    if isinstance(sens, dict) and sens:
        from .decisions import install_secret
        from .templates import secret_hash
        for var, value in sens.items():
            q.add("EXISTS (SELECT 1 FROM decision_secrets s WHERE s.decision_seq = d.seq AND s.variable = ? AND s.hmac = ?)",
                  var, secret_hash(install_secret(), value))
    return q


def _fts_query(text: str) -> str:
    words = re.findall(r"\w+", text)
    return " ".join(f'"{w}"' for w in words) or '""'


def list_decisions(f: dict, *, resolve_template, fixed_template: str | None = None) -> dict:
    limit = _limit(f.get("limit"))
    order = (f.get("order") or "desc").lower()
    if order not in ("asc", "desc"):
        raise ApiError(400, "invalid_parameter", "order is asc or desc.", "order")
    q = build_filter(f, resolve_template=resolve_template, fixed_template=fixed_template)
    c = db.read()
    for key, op in (("after", "<" if order == "desc" else ">"), ("before", ">" if order == "desc" else "<")):
        if f.get(key):
            s = c.execute("SELECT seq FROM decisions WHERE id = ?", (f[key],)).fetchone()
            if not s:
                raise ApiError(400, "invalid_parameter", f"{key} must be the id of a decision from an earlier page.", key)
            q.add(f"d.seq {op} ?", s[0])
    sql = (f"SELECT d.*, t.id AS template_id FROM decisions d LEFT JOIN templates t ON t.seq = d.template_seq "
           f"WHERE {' AND '.join(q.where)} ORDER BY d.seq {'DESC' if order == 'desc' else 'ASC'} LIMIT ?")
    rows = c.execute(sql, q.args + [limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    view = f.get("view") or "summary"
    if view == "full":
        inc = set(csv_list(f.get("include")))
        data = [_full(r, inc) for r in rows]
    else:
        seqs = [r["seq"] for r in rows]
        ans: dict = {}
        if seqs:
            for a in c.execute(f"SELECT * FROM decision_answers WHERE decision_seq IN ({','.join('?' * len(seqs))})", seqs):
                ans.setdefault(a["decision_seq"], []).append(a)
        data = [summary(r, ans.get(r["seq"], [])) for r in rows]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": more}


def _limit(v) -> int:
    if v in (None, ""):
        return 20
    try:
        n = int(v)
    except ValueError:
        raise ApiError(400, "invalid_parameter", "limit is a whole number from 1 to 100.", "limit")
    if not 1 <= n <= 100:
        raise ApiError(400, "invalid_parameter", "limit is a whole number from 1 to 100.", "limit")
    return n


def matching_seqs(f: dict, *, resolve_template, fixed_template: str | None = None, cap: int | None = None) -> list[int]:
    q = build_filter(f, resolve_template=resolve_template, fixed_template=fixed_template)
    sql = f"SELECT d.seq FROM decisions d WHERE {' AND '.join(q.where)} ORDER BY d.seq"
    if cap:
        sql += f" LIMIT {int(cap)}"
    return [r[0] for r in db.read().execute(sql, q.args)]


# ----------------------------------------------------------------------------------------------------------------------
# Statistics (sections 3.3 and 3.5)


def _pct(xs: list[float], p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


GROUPS = {"version": "d.version_number", "template": "(SELECT id FROM templates t WHERE t.seq = d.template_seq)", "model": "d.model", "model_revision": "d.model_revision", "surface": "d.surface",
          "client": "d.client", "format": "d.format", "status": "d.status",
          "day": "strftime('%Y-%m-%d', d.created_at / 1000, 'unixepoch')",
          "hour": "strftime('%Y-%m-%dT%H:00', d.created_at / 1000, 'unixepoch')"}


def stats(f: dict, *, resolve_template, fixed_template: str | None = None, default_group: list[str] | None = None,
          comparability=None) -> dict:
    group_by = csv_list(f.get("group_by")) or (default_group or [])
    for g in group_by:
        if g not in GROUPS:
            raise ApiError(400, "invalid_parameter", f"group_by takes {', '.join(GROUPS)}; got {g!r}.", "group_by")
    only = set(csv_list(f.get("questions")))
    include_ext = truthy(f.get("include_extended")) is True
    wi_t = f.get("what_if.temperature")
    wi_a = f.get("what_if.act_threshold")
    try:
        wi_t = float(wi_t) if wi_t not in (None, "") else None
        wi_a = float(wi_a) if wi_a not in (None, "") else None
    except ValueError:
        raise ApiError(400, "invalid_parameter", "what_if values are numbers.", "what_if")
    q = build_filter(f, resolve_template=resolve_template, fixed_template=fixed_template)
    c = db.read()
    cols = ", ".join(f"{GROUPS[g]} AS g_{g}" for g in group_by)
    rows = c.execute(f"SELECT d.seq, d.status, d.act, d.total_ms{', ' + cols if cols else ''} FROM decisions d "
                     f"WHERE {' AND '.join(q.where)} ORDER BY d.seq", q.args).fetchall()
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for r in rows:
        key = tuple(r[f"g_{g}"] for g in group_by)
        groups.setdefault(key, []).append(r)
    if group_by == ["version"]:
        groups = OrderedDict(sorted(groups.items(), key=lambda kv: (kv[0][0] is None, kv[0][0] or 0)))
    out_groups = []
    first_version = None
    for key, rs in groups.items():
        seqs = [r["seq"] for r in rs]
        done = [r for r in rs if r["status"] == "completed"]
        g = {"key": dict(zip(group_by, key)), "count": len(rs), "failed": sum(r["status"] == "failed" for r in rs),
             "act_rate": r4(sum(1 for r in done if r["act"]) / len(done)) if done else None,
             "latency_ms": {"p50": _pct([r["total_ms"] for r in done], 0.5), "p95": _pct([r["total_ms"] for r in done], 0.95)},
             "questions": _question_stats(c, seqs, only, include_ext)}
        if group_by == ["version"] and comparability is not None:
            if first_version is None:
                first_version = key[0]
                for qs in g["questions"].values():
                    qs["comparability"] = "reference"
            else:
                comp = comparability(first_version, key[0])
                for qk, qs in g["questions"].items():
                    info = comp.get(qk, {"comparability": "added"})
                    qs["comparability"] = info["comparability"]
                    if info["comparability"] == "options_changed" and qs.get("distribution"):
                        shared = [o for o in qs["distribution"] if o not in info.get("added_options", [])]
                        tot = sum(qs["distribution"][o] for o in shared)
                        if tot:
                            qs["shared_distribution"] = {o: r4(qs["distribution"][o] / tot) for o in shared}
        if wi_a is not None or wi_t is not None:
            g["what_if"] = _what_if(c, seqs, wi_t, wi_a)
        out_groups.append(g)
    return {"object": "decision.stats", "group_by": group_by, "filters": q.applied,
            "what_if": {"temperature": wi_t, "act_threshold": wi_a} if (wi_a is not None or wi_t is not None) else None,
            "groups": out_groups}


def _chunks(xs, n=900):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _question_stats(c, seqs: list[int], only: set, include_ext: bool) -> dict:
    per: "OrderedDict[str, dict]" = OrderedDict()
    for ch in _chunks(seqs):
        for a in c.execute(f"SELECT * FROM decision_answers WHERE decision_seq IN ({','.join('?' * len(ch))}) "
                           f"ORDER BY position", ch):
            k = a["question_key"]
            if only and k not in only:
                continue
            s = per.setdefault(k, {"type": a["type"], "decisions": {}, "excluded": set(), "dist": Counter(), "dynamic": False})
            dseq = a["decision_seq"]
            excluded = (not a["counted"]) or (not include_ext and a["origin"] not in ("template", "adhoc"))
            if excluded:
                s["excluded"].add(dseq)
                continue
            d = s["decisions"].setdefault(dseq, {"certainty": a["certainty"], "act": a["act"], "labels": []})
            if a["answer"] is not None:
                d["labels"].append(a["answer"])
            if a["dynamic_options"]:
                s["dynamic"] = True
    fb: dict = {}
    for ch in _chunks(seqs):
        for x in c.execute(f"SELECT question_key, correct FROM feedback WHERE decision_seq IN ({','.join('?' * len(ch))}) "
                           f"AND correct IS NOT NULL", ch):
            fb.setdefault(x["question_key"], []).append(x["correct"])
    out = {}
    for k, s in per.items():
        n = len(s["decisions"])
        if not n and not include_ext and s["excluded"]:
            continue            # a question callers only added for single calls
        dist = Counter()
        for d in s["decisions"].values():
            if s["type"] == "multi":
                dist.update(d["labels"])
            elif d["labels"]:
                dist[d["labels"][0]] += 1
        labels = fb.get(k, [])
        out[k] = {"runs": n, "excluded_runs": len(s["excluded"] - set(s["decisions"])),
                  "mean_certainty": r4(statistics.fmean(d["certainty"] for d in s["decisions"].values())) if n else None,
                  "act_rate": r4(sum(1 for d in s["decisions"].values() if d["act"]) / n) if n else None,
                  "distribution": None if s["dynamic"] else {o: r4(v / n) for o, v in dist.most_common()} if n else {},
                  "labelled": len(labels), "accuracy": r4(sum(labels) / len(labels)) if labels else None}
    return out


def _what_if(c, seqs: list[int], temp: float | None, thr: float | None) -> dict:
    """Recompute the act gate from stored raw probabilities (and stored temperatures) at another threshold or temperature."""
    acts = 0
    n = 0
    right_acting = labelled_acting = 0
    fb = {}
    for ch in _chunks(seqs):
        for x in c.execute(f"SELECT decision_seq, question_key, correct FROM feedback WHERE decision_seq IN "
                           f"({','.join('?' * len(ch))}) AND correct IS NOT NULL", ch):
            fb[(x["decision_seq"], x["question_key"])] = x["correct"]
        for b in c.execute(f"SELECT b.decision_seq, b.answers, b.raw_probabilities, b.settings FROM decision_bodies b "
                           f"JOIN decisions d ON d.seq = b.decision_seq WHERE d.status = 'completed' AND "
                           f"b.decision_seq IN ({','.join('?' * len(ch))})", ch):
            if not b["answers"]:
                continue
            answers = json.loads(b["answers"])
            raw = json.loads(b["raw_probabilities"]) if b["raw_probabilities"] else {}
            st = json.loads(b["settings"] or "{}")
            all_act = True
            for k, a in answers.items():
                at_used = ((st.get("questions") or {}).get(k) or {}).get("act_threshold", st.get("act_threshold", 0.9))
                cert = a.get("certainty", answer_certainty(a))
                if temp is not None and k in raw:
                    probs = recalibrate(a["type"], raw[k], temp)
                    fake = {"type": a["type"], "probabilities": probs,
                            "top_probability": max(probs.values()) if probs else None}
                    cert = answer_certainty(fake)
                act = cert >= (thr if thr is not None else at_used)
                all_act = all_act and act
                if act and (b["decision_seq"], k) in fb:
                    labelled_acting += 1
                    right_acting += fb[(b["decision_seq"], k)]
            n += 1
            acts += all_act
    return {"act_threshold": thr, "temperature": temp, "act_rate": r4(acts / n) if n else None,
            "accuracy_when_acting": r4(right_acting / labelled_acting) if labelled_acting else None,
            "labelled_acting": labelled_acting}


# ----------------------------------------------------------------------------------------------------------------------
# Version comparison (section 3.5)


def compare_versions(tseq: int, versions: list[int], f: dict, comp: dict, paired_by: str, *, resolve_template,
                     template_id: str) -> dict:
    """comp: {question: {comparability, added_options, removed_options}} between the two versions."""
    c = db.read()
    base = {k: v for k, v in f.items() if k not in ("version", "template")}
    by_version: dict = {}
    oper: dict = {}
    latest: dict = {}
    for v in versions:
        ff = dict(base, version=str(v))
        q = build_filter(ff, resolve_template=resolve_template, fixed_template=template_id)
        rows = c.execute(f"SELECT d.seq, d.id, d.status, d.total_ms, d.{paired_by} AS pk FROM decisions d "
                         f"WHERE {' AND '.join(q.where)} ORDER BY d.seq", q.args).fetchall()
        seqs = [r["seq"] for r in rows]
        by_version[v] = _question_stats(c, seqs, set(), False)
        done = [r for r in rows if r["status"] == "completed"]
        oper[str(v)] = {"n": len(rows), "failed": sum(r["status"] == "failed" for r in rows),
                        "latency_ms": {"p50": _pct([r["total_ms"] for r in done], 0.5), "p95": _pct([r["total_ms"] for r in done], 0.95)}}
        latest[v] = {r["pk"]: (r["seq"], r["id"]) for r in done if r["pk"]}
    a, b = versions[0], versions[1]
    shared_keys = set(latest[a]) & set(latest[b])
    labels: dict = {}
    seqs = [latest[v][k][0] for v in (a, b) for k in shared_keys]
    for ch in _chunks(seqs):
        for x in c.execute(f"SELECT decision_seq, question_key, answer FROM decision_answers WHERE decision_seq IN "
                           f"({','.join('?' * len(ch))}) ORDER BY ordinal", ch):
            labels.setdefault((x["decision_seq"], x["question_key"]), []).append(x["answer"])
    questions = {}
    for qk in list(by_version[a]) + [k for k in by_version[b] if k not in by_version[a]]:
        info = comp.get(qk, {"comparability": "added"})
        entry = {"comparability": info["comparability"]}
        if info["comparability"] == "options_changed":
            entry["added_options"] = info.get("added_options", [])
            entry["removed_options"] = info.get("removed_options", [])
        bv = {}
        for v in (a, b):
            s = by_version[v].get(qk)
            if not s:
                continue
            d = {"n": s["runs"], "distribution": s["distribution"], "mean_certainty": s["mean_certainty"], "act_rate": s["act_rate"],
                 "feedback": {"labelled": s["labelled"], "accuracy": s["accuracy"]}}
            if info["comparability"] == "options_changed" and s["distribution"]:
                keep = [o for o in s["distribution"] if o not in info.get("added_options", []) + info.get("removed_options", [])]
                tot = sum(s["distribution"][o] for o in keep)
                if tot:
                    d["shared_distribution"] = {o: r4(s["distribution"][o] / tot) for o in keep}
            bv[str(v)] = d
        entry["by_version"] = bv
        if info["comparability"] not in ("added", "removed", "incomparable"):
            flips = Counter()
            agree = n = 0
            sample = []
            for k in shared_keys:
                sa, ida = latest[a][k]
                sb, idb = latest[b][k]
                la, lb = labels.get((sa, qk)), labels.get((sb, qk))
                if la is None or lb is None:
                    continue
                va, vb = ",".join(x or "" for x in la), ",".join(x or "" for x in lb)
                n += 1
                if va == vb:
                    agree += 1
                else:
                    flips[(va, vb)] += 1
                if len(sample) < 20:
                    sample.append([ida, idb])
            entry["paired"] = {"n": n, "agreement": r4(agree / n) if n else None,
                               "flips": [{"from": x, "to": y, "n": m} for (x, y), m in flips.most_common(20)], "sample": sample}
        questions[qk] = entry
    return {"object": "template.comparison", "template": template_id, "versions": versions,
            "filters": {k: v for k, v in build_filter(base, resolve_template=resolve_template, fixed_template=template_id).applied.items()},
            "paired_by": "variables_hash" if paired_by == "variables_hash" else "input_hash",
            "questions": questions, "evals": {}, "operational": oper}


# ----------------------------------------------------------------------------------------------------------------------
# Changing stored decisions


def patch_decision(decision_id: str, body: dict) -> None:
    for k in body:
        if k not in ("metadata", "pinned"):
            raise ApiError(400, "read_only_field", f"'{k}' cannot change: a decision's answers, input and settings are kept "
                                                   "as they were made. Only metadata and pinned can be updated.", k)
    row = _row(decision_id)

    def tx(c):
        if "pinned" in body:
            if not isinstance(body["pinned"], bool):
                raise ApiError(400, "invalid_field", "pinned is true or false.", "pinned")
            c.execute("UPDATE decisions SET pinned = ? WHERE seq = ?", (int(body["pinned"]), row["seq"]))
        if "metadata" in body:
            meta = merge_patch(json.loads(row["metadata"] or "{}"), body["metadata"])
            check_metadata(meta, "metadata")
            c.execute("UPDATE decisions SET metadata = ? WHERE seq = ?", (json.dumps(meta), row["seq"]))
            c.execute("DELETE FROM decision_metadata WHERE decision_seq = ?", (row["seq"],))
            for k, v in meta.items():
                c.execute("INSERT INTO decision_metadata (decision_seq, key, value) VALUES (?,?,?)", (row["seq"], k, str(v)))
    db.write(tx)


def merge_patch(target, patch):
    """RFC 7396: maps merge, null removes, everything else replaces."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


META_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def check_metadata(meta, where: str = "metadata") -> dict:
    if not isinstance(meta, dict):
        raise ApiError(400, "invalid_metadata", "metadata is an object of string keys to string values.", where)
    own = [k for k in meta if not (k.startswith("basal.") or k.startswith("openrouter."))]
    if len(own) > 16:
        raise ApiError(400, "invalid_metadata", f"metadata holds at most 16 keys; got {len(own)}.", where)
    for k, v in meta.items():
        if not META_KEY.match(str(k)):
            raise ApiError(400, "invalid_metadata", f"Metadata key {k!r}: up to 64 letters, digits and _ . : -", f"{where}.{k}")
        if not isinstance(v, str) or len(v) > 512:
            raise ApiError(400, "invalid_metadata", f"Metadata value for {k!r} must be a string of up to 512 characters.", f"{where}.{k}")
    return meta


def delete_decision(decision_id: str) -> dict:
    row = _row(decision_id)

    def tx(c):
        c.execute("DELETE FROM decisions WHERE seq = ?", (row["seq"],))
        audit(c, "decisions.deleted", decision_id)
    db.write(tx)
    return {"id": decision_id, "object": "decision.deleted", "deleted": True}


REDACTABLE = re.compile(r"^(input\.state|input\.rendered_state|input\.variables(\.[a-z_][a-z0-9_]*)?|input\.media|metadata\.[A-Za-z0-9_.:-]+)$")


def _redact_tx(c, seq: int, fields: list[str]):
    b = c.execute("SELECT * FROM decision_bodies WHERE decision_seq = ?", (seq,)).fetchone()
    if not b:
        return
    variables = json.loads(b["variables"]) if b["variables"] else None
    state, rendered = b["state"], b["rendered_state"]
    done = json.loads(b["redacted"]) if b["redacted"] else []
    meta_row = c.execute("SELECT metadata FROM decisions WHERE seq = ?", (seq,)).fetchone()
    meta = json.loads(meta_row[0] or "{}")
    for fpath in fields:
        if fpath == "input.state":
            state = json.dumps("[redacted]")
            rendered = "[redacted]"
        elif fpath == "input.rendered_state":
            rendered = "[redacted]"
        elif fpath == "input.variables":
            variables = {k: "[redacted]" for k in (variables or {})}
            state, rendered = json.dumps("[redacted]"), "[redacted]"
        elif fpath.startswith("input.variables."):
            name = fpath.split(".", 2)[2]
            if variables and name in variables:
                variables[name] = "[redacted]"
            state, rendered = json.dumps("[redacted]"), "[redacted]"
        elif fpath == "input.media":
            c.execute("UPDATE decision_media SET file_seq = NULL, name = NULL WHERE decision_seq = ?", (seq,))
        elif fpath.startswith("metadata."):
            meta.pop(fpath[9:], None)
            c.execute("DELETE FROM decision_metadata WHERE decision_seq = ? AND key = ?", (seq, fpath[9:]))
        if fpath not in done:
            done.append(fpath)
    c.execute("UPDATE decision_bodies SET variables = ?, state = ?, rendered_state = ?, redacted = ? WHERE decision_seq = ?",
              (json.dumps(variables) if variables is not None else None, state, rendered, json.dumps(done), seq))
    c.execute("UPDATE decisions SET metadata = ? WHERE seq = ?", (json.dumps(meta), seq))
    if db.get().search:
        c.execute("DELETE FROM decision_fts WHERE rowid = ?", (seq,))


def check_redact_fields(fields) -> list[str]:
    if not isinstance(fields, list) or not fields or not all(isinstance(x, str) and REDACTABLE.match(x) for x in fields):
        raise ApiError(400, "invalid_field", "fields lists what to remove: input.state, input.variables, "
                                             "input.variables.<name>, input.media, input.rendered_state or metadata.<key>.", "fields")
    return fields


def redact_decision(decision_id: str, fields: list[str]) -> None:
    fields = check_redact_fields(fields)
    row = _row(decision_id)

    def tx(c):
        _redact_tx(c, row["seq"], fields)
        audit(c, "decisions.redacted", decision_id, {"fields": fields})
    db.write(tx)


def bulk(body: dict, action: str, *, resolve_template) -> dict:
    f = body.get("filter") or {}
    if not isinstance(f, dict):
        raise ApiError(400, "invalid_field", "filter is an object of the history list's filters.", "filter")
    if not {k: v for k, v in f.items() if v not in (None, "", [], {})} and body.get("all") is not True:
        raise ApiError(400, "filter_required", "Send a filter, or \"all\": true to act on every decision.", "filter")
    fields = check_redact_fields(body.get("fields")) if action == "redact" else None
    f = dict(f, fold_retries="false")
    if "surface" not in f:
        f["surface"] = "all"
    seqs = matching_seqs(f, resolve_template=resolve_template)
    pinned = set()
    if seqs and not body.get("include_pinned"):
        for ch in _chunks(seqs):
            pinned |= {r[0] for r in db.read().execute(
                f"SELECT seq FROM decisions WHERE pinned = 1 AND seq IN ({','.join('?' * len(ch))})", ch)}
    todo = [s for s in seqs if s not in pinned]
    dry = bool(body.get("dry_run"))
    if not dry and todo:
        def tx(c):
            for ch in _chunks(todo):
                if action == "delete":
                    c.execute(f"DELETE FROM decisions WHERE seq IN ({','.join('?' * len(ch))})", ch)
                else:
                    for s in ch:
                        _redact_tx(c, s, fields)
            audit(c, f"decisions.{'deleted' if action == 'delete' else 'redacted'}", None,
                  {"filter": {k: v for k, v in f.items() if k != "sensitive_hash"}, "count": len(todo)})
        db.write(tx)
    key = "deleted" if action == "delete" else "redacted"
    return {"object": f"decision.bulk_{action}", "matched": len(seqs), key: 0 if dry else len(todo),
            "skipped_pinned": len(pinned), "dry_run": dry}


# ----------------------------------------------------------------------------------------------------------------------
# Feedback (section 3.4)


def add_feedback(decision_id: str, body: dict) -> list[dict]:
    row = _row(decision_id)
    exp = body.get("expected")
    rating = body.get("rating")
    note = body.get("note") or ""
    if exp is None and rating is None:
        raise ApiError(400, "missing_field", "Send `expected` (the right answers) and/or `rating` (1 or -1).", "expected")
    if exp is not None and not isinstance(exp, dict):
        raise ApiError(400, "invalid_field", "expected is an object of question key to the right answer.", "expected")
    if rating not in (None, 1, -1):
        raise ApiError(400, "invalid_field", "rating is 1, -1 or null.", "rating")
    if not isinstance(note, str) or len(note) > 2000:
        raise ApiError(400, "invalid_field", "note is text of up to 2,000 characters.", "note")
    c = db.read()
    qdef = c.execute("SELECT definition FROM question_sets WHERE hash = ?", (row["questions_hash"],)).fetchone()
    questions = json.loads(qdef[0]) if qdef else {}
    b = c.execute("SELECT answers FROM decision_bodies WHERE decision_seq = ?", (row["seq"],)).fetchone()
    answers = json.loads(b[0]) if b and b[0] else {}
    items = []
    for k, v in (exp or {}).items():
        if k not in questions:
            raise ApiError(400, "invalid_expected", f"'{k}' is not one of this decision's questions.", f"expected.{k}")
        label = canonical_label(questions[k], v, f"expected.{k}")
        pred = label_of(answers.get(k) or {}) if answers.get(k) else None
        items.append((k, label, pred, is_correct(questions[k], answers.get(k), label)))
    if not items:
        items.append(("*", None, None, None))
    now = db.now_ms()
    ids = []

    def tx(c):
        for k, label, pred, corr in items:
            prev = c.execute("SELECT id FROM feedback WHERE decision_seq = ? AND question_key = ? AND actor = 'local'",
                             (row["seq"], k)).fetchone()
            vals = (json.dumps(label) if label is not None else None, json.dumps(pred) if pred is not None else None,
                    None if corr is None else int(corr), rating, note)
            if prev:
                c.execute("UPDATE feedback SET expected = ?, predicted = ?, correct = ?, rating = ?, note = ?, updated_at = ? "
                          "WHERE id = ?", vals + (now, prev[0]))
                ids.append(prev[0])
            else:
                fid = new_id("fb", now)
                c.execute("INSERT INTO feedback (id, decision_seq, template_seq, version_number, model, question_key, expected, "
                          "predicted, correct, rating, note, actor, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (fid, row["seq"], row["template_seq"], row["version_number"], row["model"], k) + vals + ("local", now, now))
                ids.append(fid)
    db.write(tx)
    return [feedback_object(i) for i in ids]


def feedback_object(fid: str, row=None) -> dict:
    c = db.read()
    f = row or c.execute("SELECT f.*, d.id AS decision_id FROM feedback f JOIN decisions d ON d.seq = f.decision_seq "
                         "WHERE f.id = ?", (fid,)).fetchone()
    if not f:
        raise ApiError(404, "feedback_not_found", f"No feedback '{fid}'.")
    return {"id": f["id"], "object": "feedback", "decision_id": f["decision_id"], "question": f["question_key"],
            "expected": _json_label(f["expected"]), "predicted": _json_label(f["predicted"]),
            "correct": None if f["correct"] is None else bool(f["correct"]), "rating": f["rating"], "note": f["note"],
            "actor": f["actor"], "created_at": f["created_at"] // 1000, "updated_at": f["updated_at"] // 1000}


def list_feedback(decision_id: str) -> dict:
    row = _row(decision_id)
    rows = db.read().execute("SELECT f.*, d.id AS decision_id FROM feedback f JOIN decisions d ON d.seq = f.decision_seq "
                             "WHERE f.decision_seq = ? ORDER BY f.created_at", (row["seq"],)).fetchall()
    data = [feedback_object(r["id"], r) for r in rows]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": False}


def delete_feedback(fid: str) -> dict:
    feedback_object(fid)
    db.write(lambda c: c.execute("DELETE FROM feedback WHERE id = ?", (fid,)))
    return {"id": fid, "object": "feedback.deleted", "deleted": True}


# ----------------------------------------------------------------------------------------------------------------------
# Export


def export(f: dict, fmt: str, *, resolve_template, fixed_template: str | None = None):
    f = {k: v for k, v in f.items() if k != "format"}   # here `format` is the file format, not the wire-format filter
    if f.get("wire_format"):
        f["format"] = f.pop("wire_format")
    seqs = matching_seqs(f, resolve_template=resolve_template, fixed_template=fixed_template)
    inc = set(csv_list(f.get("include")))
    c = db.read()

    def ids():
        for ch in _chunks(seqs, 200):
            for r in c.execute(f"SELECT d.*, t.id AS template_id FROM decisions d LEFT JOIN templates t ON t.seq = d.template_seq "
                               f"WHERE d.seq IN ({','.join('?' * len(ch))}) ORDER BY d.seq", ch):
                yield r
    db.write(lambda cc: audit(cc, "decisions.exported", None, {"format": fmt, "count": len(seqs)}))
    if fmt == "jsonl":
        for r in ids():
            yield json.dumps(_full(r, inc), ensure_ascii=False) + "\n"
        return
    qkeys: list[str] = []
    mkeys: list[str] = []
    rows = []
    for r in ids():
        ans = c.execute("SELECT * FROM decision_answers WHERE decision_seq = ?", (r["seq"],)).fetchall()
        s = summary(r, ans)
        for k in s["answers"]:
            if k not in qkeys:
                qkeys.append(k)
        for k in s["metadata"]:
            if k not in mkeys:
                mkeys.append(k)
        rows.append(s)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "template", "version", "model", "status", "act"]
               + [x for k in qkeys for x in (k, f"{k}.certainty", f"{k}.act")] + [f"metadata.{k}" for k in mkeys])
    yield buf.getvalue()
    for s in rows:
        buf = io.StringIO()
        w = csv.writer(buf)
        line = [s["id"], s["created_at"], (s["template"] or {}).get("id") or "", (s["template"] or {}).get("version") or "",
                s["model"] or "", s["status"], "" if s["act"] is None else str(s["act"]).lower()]
        for k in qkeys:
            a = s["answers"].get(k)
            if a is None:
                line += ["", "", ""]
            else:
                d = a["decision"]
                line += [";".join(d) if isinstance(d, list) else ("" if d is None else d), a["certainty"], str(a["act"]).lower()]
        line += [s["metadata"].get(k, "") for k in mkeys]
        w.writerow(line)
        yield buf.getvalue()


# ----------------------------------------------------------------------------------------------------------------------
# Retention (section 4.6 and 5.6)


def pending_deletion(st: dict | None = None) -> int:
    st = st or settings()
    c = db.read()
    total = 0
    for tseq, days in _retention_rules(c, st):
        if not days:
            continue
        cutoff = db.now_ms() - days * 86400_000
        total += c.execute(_sweep_sql("COUNT(*)", tseq, st), _sweep_args(tseq, cutoff)).fetchone()[0]
    return total


def _retention_rules(c, st) -> list[tuple[int | None, int]]:
    rules = [(None, st["history.retention_days"])]
    for r in c.execute("SELECT seq, retention_days FROM templates WHERE retention_days IS NOT NULL"):
        rules.append((r[0], r[1]))
    return rules


def _sweep_sql(what: str, tseq, st) -> str:
    base = (f"SELECT {what} FROM decisions d LEFT JOIN batches b ON b.seq = d.batch_seq WHERE d.workspace_id = ? "
            f"AND d.created_at < ? AND d.pinned = 0 AND d.eval_seq IS NULL "
            f"{'AND d.has_feedback = 0 ' if st['history.keep_labelled'] else ''}"
            f"AND (b.seq IS NULL OR b.status NOT IN ('queued','in_progress')) ")
    if tseq is None:
        return base + "AND (d.template_seq IS NULL OR d.template_seq NOT IN (SELECT seq FROM templates WHERE retention_days IS NOT NULL))"
    return base + "AND d.template_seq = ?"


def _sweep_args(tseq, cutoff):
    return (db.WS, cutoff) + ((tseq,) if tseq is not None else ())


def sweep() -> dict:
    """Delete decisions past retention, keep storage under its cap, collect unused files. Runs in chunks so live
    decisions keep committing between them."""
    if not db.available() or db.get().read_only:
        return {}
    st = settings()
    deleted = 0
    c = db.read()
    for tseq, days in _retention_rules(c, st):
        if not days:
            continue
        cutoff = db.now_ms() - days * 86400_000
        while True:
            seqs = [r[0] for r in db.read().execute(_sweep_sql("d.seq", tseq, st) + " ORDER BY d.seq LIMIT 2000",
                                                     _sweep_args(tseq, cutoff))]
            if not seqs:
                break
            db.write(lambda cc: cc.execute(f"DELETE FROM decisions WHERE seq IN ({','.join('?' * len(seqs))})", seqs))
            deleted += len(seqs)
            time.sleep(0.01)
    cap = float(st["history.max_storage_gb"] or 0) * 1e9
    if cap:
        while _usage_bytes() > cap:
            seqs = [r[0] for r in db.read().execute(
                _sweep_sql("d.seq", None, st).replace("AND d.created_at < ? ", "") + " ORDER BY d.seq LIMIT 500",
                (db.WS,))]
            if not seqs:
                break
            db.write(lambda cc: cc.execute(f"DELETE FROM decisions WHERE seq IN ({','.join('?' * len(seqs))})", seqs))
            deleted += len(seqs)
            if _usage_bytes() <= cap * 0.9:
                break
    now = db.now_ms()

    def house(cc):
        files = blobs.sweep_files(cc, now)
        cc.execute("DELETE FROM question_sets WHERE NOT EXISTS (SELECT 1 FROM decisions d WHERE d.questions_hash = question_sets.hash)")
        cc.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (now - 24 * 3600_000,))
        cc.execute("DELETE FROM usage_counters WHERE minute < ?", (int(time.time() // 60) - 400 * 1440,))
        cc.execute("DELETE FROM templates WHERE deleted_at IS NOT NULL AND NOT EXISTS "
                   "(SELECT 1 FROM decisions d WHERE d.template_seq = templates.seq)")
        audit(cc, "history.swept", None, {"deleted": deleted, **files})
        return files
    files = db.write(house)
    try:
        with db.get().lock:
            db.get().writer.execute("PRAGMA incremental_vacuum(4000)")
            db.get().writer.execute("PRAGMA wal_checkpoint(PASSIVE)")
            db.get().writer.execute("PRAGMA optimize")
    except Exception:   # noqa: BLE001
        pass
    SWEEP.update(last_at=time.time(), last_deleted=deleted, error=None)
    return {"deleted": deleted, **files}


def _usage_bytes() -> int:
    d = db.get()
    size = 0
    for p in (d.path, d.path.with_name(d.path.name + "-wal")):
        try:
            size += p.stat().st_size
        except OSError:
            pass
    return size + blobs.disk_bytes()


def storage_info() -> dict:
    d = db.get()
    c = db.read()
    n = c.execute("SELECT COUNT(*), MIN(created_at) FROM decisions WHERE workspace_id = ?", (db.WS,)).fetchone()
    size = 0
    for p in (d.path, d.path.with_name(d.path.name + "-wal")):
        try:
            size += p.stat().st_size
        except OSError:
            pass
    return {"db_bytes": size, "blob_bytes": blobs.disk_bytes(), "decisions": n[0], "oldest_at": n[1] // 1000 if n[1] else None,
            "last_sweep_at": int(SWEEP["last_at"]) if SWEEP["last_at"] else None, "last_sweep_deleted": SWEEP["last_deleted"],
            "pending_deletion": pending_deletion(), "search_available": d.search, "read_only": d.read_only,
            "store_errors": list(STORE_ERRORS)}


def usage(since_minutes: int = 24 * 60, bucket: str = "hour") -> dict:
    """Content-free request counts per bucket, from usage_counters (includes calls that were not stored)."""
    c = db.read()
    since = int(time.time() // 60) - since_minutes
    size = {"minute": 1, "hour": 60, "day": 1440}.get(bucket, 60)
    rows = c.execute("SELECT (minute / ?) * ? AS b, model, status, stored, SUM(count) n, SUM(total_ms_sum) t FROM usage_counters "
                     "WHERE workspace_id = ? AND minute >= ? GROUP BY b, model, status, stored ORDER BY b",
                     (size, size, db.WS, since)).fetchall()
    return {"bucket": bucket, "since": since * 60,
            "rows": [{"t": r["b"] * 60, "model": r["model"] or None, "status": r["status"], "stored": r["stored"], "count": r["n"],
                      "total_ms_sum": r["t"]} for r in rows]}
