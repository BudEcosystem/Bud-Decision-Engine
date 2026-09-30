"""The one decision pipeline behind every entry point: /v1/studio/decisions, the Playground, and (without templates)
the stateless wire routes such as /v1/systemone.

    resolve   template reference -> one version; variables -> state; extra questions merged; model and settings chosen
    check     model limits and modalities, before any model loads
    run       load the model if needed, ask it (per-question temperatures), gate each answer (certainty >= threshold)
    record    write the decision at its storage level, unless the caller opted out

docs/studio-api.md sections 4.3, 4.5, 4.7 and 4.9.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from . import blobs, db, history
from . import template_store as TS
from . import templates as T
from .catalog import BY_ID
from .contract import render as render_state
from .errors import ApiError, Problems, param_path
from .ids import new_id
from .paths import DATA

# Filled in by server.py: how to route a model name, make sure it is loaded, and ask it.
RUNTIME = SimpleNamespace(route=None, ensure_ready=None, decide=None, status_of=None, is_ready=None)
BACKGROUND: dict[str, dict] = {}        # decision id -> {"task", "cancel", "started"}
KNOWN_FIELDS = {"template", "variables", "state", "questions", "add_options", "skip", "media", "model", "settings", "metadata",
                "store", "background", "stream", "include", "user", "session_id"}
INCLUDES = {"input", "input.rendered_state", "answers.raw_probabilities", "answers.model_extras"}
_secret: bytes | None = None


def install_secret() -> bytes:
    """Per-install key for the HMACs of sensitive values (DATA/secret, 0600)."""
    global _secret
    if _secret is None:
        p = DATA / "secret"
        if not p.exists():
            p.write_bytes(secrets.token_bytes(32))
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        _secret = p.read_bytes()
    return _secret


@dataclass
class Source:
    surface: str = "api"
    endpoint: str = "/v1/studio/decisions"
    format: str = "studio"
    client: str | None = None
    request_id: str | None = None
    attempt: int = 0

    def as_dict(self) -> dict:
        return {"surface": self.surface, "endpoint": self.endpoint, "format": self.format, "client": self.client,
                "request_id": self.request_id, "attempt": self.attempt}


@dataclass
class Resolved:
    template: dict | None
    definition: dict | None
    model: str | None
    model_requested: str | None
    variables: dict | None            # stored form
    variables_hash: str | None
    secrets: dict
    state: Any
    stored_state: Any
    questions: dict
    origins: dict
    dynamic: set
    extensions: dict
    media_items: list                 # request media, with media variables first
    per: dict
    settings: dict
    storage: str                      # full | answers_only | none
    metadata: dict
    warnings: list
    include: set
    background: bool = False
    rerun_of: str | None = None
    group: dict | None = None
    request_extras: dict | None = None


def _warn(ws: list, code: str, message: str, param: str | None = None):
    if not any(w["code"] == code and w.get("param") == param for w in ws):
        ws.append({"code": code, "message": message, "param": param})


def studio_store() -> str:
    return history.setting("history.store") if db.available() else "none"


def default_act_threshold() -> float:
    return float(history.setting("decisions.default_act_threshold")) if db.available() else 0.9


def request_metadata(body_meta, header_meta: str | None, body: dict, warnings: list, *, lenient: bool) -> dict:
    """Body metadata (validated; ignored with a warning on wire routes), X-Basal-Metadata, OpenRouter user/session_id."""
    meta: dict = {}
    if header_meta:
        from urllib.parse import unquote
        for part in header_meta.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                meta[k.strip()] = unquote(v.strip())
    if body_meta is not None:
        try:
            history.check_metadata(body_meta)
            meta.update(body_meta)
        except ApiError:
            if not lenient:
                raise
            _warn(warnings, "metadata_ignored", "metadata must be an object of up to 16 string values; it was ignored.", "metadata")
    for k in ("user", "session_id"):
        v = body.get(k)
        if isinstance(v, str) and 0 < len(v) <= 256:
            meta[f"openrouter.{k}"] = v
    try:
        history.check_metadata(meta)
    except ApiError:
        if not lenient:
            raise
        _warn(warnings, "metadata_ignored", "The metadata header was not valid and was ignored.", "metadata")
        meta = {k: v for k, v in meta.items() if k.startswith("openrouter.")}
    return meta


def storage_level(body_store, header_store: str | None, template_storage: str | None, warnings: list, *, lenient: bool) -> str:
    asked = None
    if body_store is not None:
        asked = history.parse_store(body_store)
        if asked is None:
            if not lenient:
                raise ApiError(400, "invalid_field", "store is true, false, \"full\", \"answers_only\" or \"none\".", "store")
            _warn(warnings, "store_ignored", "store must be true, false, full, answers_only or none; it was ignored.", "store")
    hdr = history.parse_store(header_store) if header_store else None
    requested = history.private(asked, hdr) if (asked or hdr) else "full"
    level = history.private(requested, template_storage, studio_store())
    if level != requested:
        _warn(warnings, "store_downgraded", f"This decision is stored as '{level}' (the studio or template allows no more).", "store")
    return level


# ----------------------------------------------------------------------------------------------------------------------
# Resolve (steps 3 to 8)


def resolve(body: dict, source: Source, *, header_store: str | None = None, header_meta: str | None = None,
            preview: bool = False) -> Resolved:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "The request body is a JSON object.")
    warnings: list = []
    for k in body:
        if k not in KNOWN_FIELDS:
            _warn(warnings, "unknown_field_ignored", f"'{k}' is not a field of this endpoint and was ignored.", k)
    if body.get("stream"):
        raise ApiError(400, "invalid_field", "Streaming is planned for a later version. Use \"background\": true and then "
                                             "GET /v1/studio/decisions/{id}?wait=60.", "stream")
    inc = body.get("include") or []
    if isinstance(inc, str):
        inc = [x.strip() for x in inc.split(",") if x.strip()]
    if not isinstance(inc, list) or not all(x in INCLUDES for x in inc):
        raise ApiError(400, "invalid_field", f"include takes {', '.join(sorted(INCLUDES))}.", "include")
    p = Problems()
    tpl = defn = None
    template_storage = None
    if body.get("template") is not None:
        h, n, resolved_from = TS.resolve(body["template"])
        defn = TS.definition(h["seq"], n)
        tpl = {"id": h["id"], "version": n, "ref": body["template"], "resolved_from": resolved_from,
               "attribution": "explicit", "seq": h["seq"]}
        draft = (body.get("metadata") or {}).get("basal.draft_of") if isinstance(body.get("metadata"), dict) else None
        if draft:
            tpl["attribution"] = "draft"
        template_storage = h["storage"]
        if h["archived_at"]:
            _warn(warnings, "template_archived", f"{h['id']} is archived; it still works, but check whether a newer template "
                                                 "replaced it.", "template")
        rendered = T.render(defn, body.get("variables"), body.get("state"), install_secret(), p)
        if rendered is None:
            p.raise_if_any()
        merged = T.merge_questions(rendered.questions, rendered.dynamic, defn["extensions"], body.get("questions"),
                                   body.get("add_options"), body.get("skip"), p)
        # extra questions may use the template's variables in their text
        if merged.extensions["questions"]:
            extra = T.render_questions({k: merged.questions[k] for k in merged.extensions["questions"]}, rendered.values,
                                       defn["variables"])
            merged.questions.update(extra)
        media_items = [{"type": m["type"], "variable": m["variable"],
                        **({"file_id": m["value"]} if str(m["value"]).startswith("file_") else {"data": m["value"]}),
                        "_param": param_path("variables", m["variable"])} for m in rendered.media]
        state, stored_state = rendered.state, rendered.stored_state
        stored_vars = rendered.stored_variables if defn["variables"] else None
        secrets_ = rendered.secrets
        questions, origins, dynamic, extensions = merged.questions, merged.origins, merged.dynamic, merged.extensions
    else:
        draft_of = (body.get("metadata") or {}).get("basal.draft_of") if isinstance(body.get("metadata"), dict) else None
        if draft_of:
            # the Playground trying unsaved edits to a template: stored against that version, hidden from its stats
            try:
                h, n, _ = TS.resolve(draft_of)
                tpl = {"id": h["id"], "version": n, "ref": draft_of, "resolved_from": "pinned", "attribution": "draft",
                       "seq": h["seq"]}
                template_storage = h["storage"]
            except ApiError:
                tpl = None
        if body.get("variables") is not None:
            p.add("variables_need_template", "variables", "variables need a template: send `template`, or put the situation "
                                                          "in `state`.")
        for k in ("add_options", "skip"):
            if body.get(k):
                p.add("requires_template", k, f"{k} applies to template questions; this decision has no template.")
        state = body.get("state")
        if state is None:
            p.add("state_required", "state", "state is required: the situation to decide about (text, an object or a list).")
        elif not isinstance(state, (str, dict, list)):
            p.add("invalid_field", "state", "state is text, an object or a list.")
        qs = body.get("questions")
        questions = {}
        if not isinstance(qs, dict) or not qs:
            p.add("missing_field" if qs is None else "invalid_field", "questions",
                  "questions is an object of key to question, with at least one question.")
        else:
            if len(qs) > T.MAX_QUESTIONS:
                p.add("invalid_field", "questions", f"A decision has at most {T.MAX_QUESTIONS} questions.")
            for k, q in qs.items():
                try:
                    T._QUESTION.validate_python(q)
                    questions[k] = copy.deepcopy(q)
                except ValidationError as err:
                    for x in err.errors(include_url=False):
                        p.add("invalid_field", param_path("questions", k, *[str(l) for l in x["loc"][1:]]), f"Question '{k}': {x['msg']}")
        stored_state = state
        stored_vars, secrets_ = None, {}
        origins = {k: "adhoc" for k in questions}
        dynamic, extensions = set(), {"questions": [], "options": {}, "skipped": []}
        media_items = []
    req_media = body.get("media") or []
    if not isinstance(req_media, list):
        p.add("invalid_field", "media", "media is a list of {type?, file_id | data, name?}.")
        req_media = []
    media_items = media_items + [dict(m, _param=param_path("media", i)) if isinstance(m, dict) else m for i, m in enumerate(req_media)]
    req_settings = T.check_settings(body.get("settings"), p, "settings", question_types={k: q["type"] for k, q in questions.items()})
    p.raise_if_any()
    metadata = request_metadata(body.get("metadata"), header_meta, body, warnings, lenient=False)
    storage = storage_level(body.get("store"), header_store, template_storage, warnings, lenient=False)
    if body.get("background") and storage == "none":
        raise ApiError(400, "background_requires_store", "A background decision must be stored so it can be fetched; "
                                                         "use store \"answers_only\" if you do not want its input kept.", "background")
    # model (step 6)
    requested = body.get("model")
    model = None
    if requested not in (None, ""):
        if not isinstance(requested, str):
            raise ApiError(400, "invalid_field", "model is a model id such as \"laya\".", "model")
        model = _route(requested, required=True)
    elif defn and defn.get("model"):
        model = defn["model"]
    else:
        model = _route(None, required=not preview)
        if model is None:
            _warn(warnings, "no_model_loaded", "No model is loaded, so this preview has no model; load one or name `model`.", "model")
    per, settings_out = T.resolve_settings(questions, model, (defn or {}).get("settings"), req_settings, default_act_threshold())
    if model:
        types = [m.get("type") for m in media_items if isinstance(m, dict) and m.get("type")]
        probs = T.model_problems(questions, types, BY_ID[model])
        if probs:
            raise ApiError(400, "model_incompatible", f"{BY_ID[model].name} cannot run this decision: {probs[0]['message']}",
                           probs[0]["param"], probs)
        est = (len(render_state(state)) + len(T.canonical(questions))) // 4
        if est > BY_ID[model].context_tokens:
            _warn(warnings, "state_may_be_truncated", f"The input is about {est:,} tokens; {BY_ID[model].name} reads "
                                                      f"{BY_ID[model].context_tokens:,}, so the end may be cut off.", "state")
    vh = None
    if defn is not None and defn["variables"]:
        vh = T.sha({"variables": stored_vars, "media": [m.get("file_id") or hashlib.sha256(str(m.get("data")).encode()).hexdigest()
                                                         for m in media_items if isinstance(m, dict)]})
    return Resolved(tpl, defn, model, requested if requested not in (None, "") else None, stored_vars, vh, secrets_, state,
                    stored_state, questions, origins, dynamic, extensions, media_items, per, settings_out, storage, metadata,
                    warnings, set(inc), bool(body.get("background")))


def _route(name: str | None, required: bool) -> str | None:
    try:
        return RUNTIME.route(name)
    except HTTPException as e:
        if e.status_code == 404:
            raise ApiError(404, "model_not_found", str(e.detail), "model")
        if not required:
            return None
        raise ApiError(409, "model_not_loaded", str(e.detail), "model")


def preview_object(r: Resolved) -> dict:
    ws = T.worker_settings(r.per, r.settings)
    sys_req = {"model": r.model, "state": r.state, "questions": r.questions}
    temps = {s["temperature"] for s in r.per.values()}
    if len(temps) > 1:
        for k, v in r.settings.get("questions", {}).items():
            if "temperature" in v:
                _warn(r.warnings, "per_question_settings_not_portable",
                      f"/v1/systemone takes one temperature per request; systemone_request uses {r.settings['temperature']} "
                      "for every question.", f"settings.questions.{k}.temperature")
    if r.settings["temperature"] != 1.0:
        sys_req["settings"] = {"temperature": r.settings["temperature"]}
    return {"object": "decision.preview", "template": _public_template(r.template), "model": r.model, "state": r.state,
            "rendered_state": render_state(r.state), "questions": r.questions, "extensions": r.extensions,
            "media": [{k: v for k, v in m.items() if k != "_param" and k != "data"} for m in r.media_items if isinstance(m, dict)],
            "settings": r.settings, "usage": {"input_tokens": max(1, (len(render_state(r.state)) + len(T.canonical(r.questions))) // 4)},
            "systemone_request": sys_req, "worker_settings": ws, "store": r.storage, "warnings": r.warnings}


def _public_template(t: dict | None) -> dict | None:
    return None if t is None else {k: v for k, v in t.items() if k != "seq"}


# ----------------------------------------------------------------------------------------------------------------------
# Run and record (steps 9 and 10)


@dataclass
class Outcome:
    status: int                   # HTTP status of the response
    body: dict                    # the decision object (or error envelope)
    stored: str                   # full | answers_only | none | failed
    decision_id: str
    record: history.Record | None = None


def _map_load_error(e: HTTPException) -> tuple[int, str]:
    d = str(e.detail)
    if e.status_code == 409:
        return 409, "model_not_downloaded" if "download" in d else "model_not_loaded"
    if e.status_code == 504:
        return 504, "model_load_timeout"
    if "ejected" in d:
        return 503, "model_ejected"
    return 503, "model_load_failed"


async def execute(r: Resolved, source: Source, *, did: str | None = None, created_ms: int | None = None) -> Outcome:
    did = did or new_id("dec")
    created = created_ms or db.now_ms()
    t0 = time.perf_counter()
    keep_media = r.storage == "full" and (not db.available() or history.setting("history.store_media"))
    media: list = []
    status, err, res = 200, None, None
    load_ms = 0.0
    try:
        media = await asyncio.to_thread(blobs.resolve, r.media_items, keep=keep_media,
                                        allowed=(r.definition or {}).get("modalities") if r.definition else None)
        for m in media:
            if r.model and m.type not in BY_ID[r.model].modalities:
                raise ApiError(400, "model_incompatible", f"{BY_ID[r.model].name} cannot read {m.type} input.", "media",
                               [{"code": "modality_not_supported", "param": "media", "message": f"{BY_ID[r.model].name} cannot read {m.type} input."}])
        was_ready = RUNTIME.is_ready(r.model)
        tl = time.perf_counter()
        try:
            await RUNTIME.ensure_ready(r.model)
        except HTTPException as e:
            status, code = _map_load_error(e)
            err = {"type": "model_error" if status >= 500 else "conflict_error", "code": code, "message": str(e.detail)}
        load_ms = 0.0 if was_ready else round((time.perf_counter() - tl) * 1000, 1)
        if err is None:
            wbody = {"model": r.model, "state": r.state, "questions": r.questions, "media": [m.worker() for m in media],
                     "settings": T.worker_settings(r.per, r.settings)}
            BACKGROUND.get(did, {})["started"] = True
            code, res = await RUNTIME.decide(r.model, wbody)
            if code != 200:
                detail = res.get("detail", res) if isinstance(res, dict) else res
                if code == 422:
                    status, err = 400, {"type": "invalid_request_error", "code": "model_rejected_input", "message": str(detail)}
                elif code == 504:
                    status, err = 504, {"type": "model_error", "code": "model_timeout", "message": str(detail)}
                else:
                    status, err = 503, {"type": "model_error", "code": "model_crashed", "message": str(detail)}
                res = None
    except ApiError:
        blobs.cleanup(media)
        raise
    finally:
        pass
    blobs.cleanup(media)
    total = round((time.perf_counter() - t0) * 1000, 1)
    answers = raw = None
    act, lowest, needs = None, None, []
    if res is not None:
        answers = res["answers"]
        raw = res.get("raw_probabilities")
        act, needs, lowest = history.gate_answers(answers, r.per, r.origins, r.dynamic)
    rec = history.Record(
        id=did, created_ms=created, completed_ms=db.now_ms(), status="completed" if err is None else "failed",
        storage=r.storage if r.storage != "none" else "full", source=source.as_dict(), template=r.template, model=r.model,
        model_requested=r.model_requested, questions=r.questions,
        input_hash=T.sha({"state": render_state(r.stored_state), "media": [m.sha256 for m in media]}),
        variables_hash=r.variables_hash, variables=_stored_variables(r, media), state=r.stored_state,
        rendered_state=render_state(r.stored_state), media=media, extensions=r.extensions, settings=r.settings,
        answers=answers, raw_probabilities=raw, act=act, min_certainty=lowest,
        timing={"queue_ms": 0.0, "load_ms": load_ms, "model_ms": (res or {}).get("latency_ms"), "total_ms": total},
        usage=(res or {}).get("usage") or {}, passes=(res or {}).get("passes"), notes=(res or {}).get("notes") or [],
        warnings=r.warnings, metadata=r.metadata, secrets=r.secrets, error=err, http_status=status,
        rerun_of=r.rerun_of, group=r.group)
    stored = r.storage
    if r.storage != "none":
        stored = await save(rec)
    history.count_usage(model=r.model, template_seq=(r.template or {}).get("seq"), surface=source.surface, fmt=source.format,
                        status=status, stored=stored, model_ms=rec.timing.get("model_ms"), total_ms=total)
    body = response_object(rec, r.include, stored)
    if err is not None:
        e = ApiError(status, err["code"], err["message"], None, type_=err["type"],
                     decision_id=did if stored in ("full", "answers_only") else None)
        return Outcome(status, e.body(source.request_id), stored, did, rec)
    return Outcome(200, body, stored, did, rec)


def _stored_variables(r: Resolved, media: list) -> dict | None:
    if r.variables is None:
        return None
    out = dict(r.variables)
    for m in media:
        if m.variable and m.variable in out:
            out[m.variable] = m.file_id if (m.file_id and m.file_seq) else f"[{m.type} not kept]"
    return out


async def save(rec: history.Record) -> str:
    """Write a decision; a failed write never changes the answer unless history.on_store_error is 'fail'."""
    try:
        await asyncio.to_thread(db.write, lambda c: history.insert(c, rec))
        return rec.storage
    except Exception as e:   # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        print(f"[history] could not save {rec.id}: {msg}", flush=True)
        history.STORE_ERRORS.appendleft({"time": time.time(), "decision_id": rec.id, "error": msg})
        if db.available() and history.setting("history.on_store_error") == "fail":
            raise ApiError(503, "store_failed", "The decision could not be saved to history, and this studio is set to refuse "
                                                "unsaved decisions.", type_="api_error")
        _warn(rec.warnings, "history_write_failed", "The answer is correct, but it could not be saved to history.")
        return "failed"


def response_object(rec: history.Record, include: set, stored: str) -> dict:
    answers = copy.deepcopy(rec.answers)
    if answers:
        for k, a in answers.items():
            if "answers.raw_probabilities" in include and rec.raw_probabilities and k in rec.raw_probabilities:
                a["raw_probabilities"] = rec.raw_probabilities[k]
            if "answers.model_extras" not in include:
                a.pop("model_extras", None)
    out = {"id": rec.id, "object": "decision", "status": rec.status, "created_at": rec.created_ms // 1000,
           "completed_at": rec.completed_ms // 1000 if rec.completed_ms else None,
           "template": _public_template(rec.template), "model": rec.model, "model_requested": rec.model_requested,
           "model_revision": None}
    if include & {"input", "input.rendered_state"}:
        inp = {"variables": rec.variables, "state": rec.state,
               "media": [{"type": m.type, "file_id": m.file_id if m.file_seq else None, "name": m.name,
                          "content_type": m.content_type, "bytes": m.bytes, "variable": m.variable,
                          "sha256": "sha256:" + m.sha256, "available": bool(m.file_seq)} for m in rec.media],
               "questions": rec.questions}
        if "input.rendered_state" in include:
            inp["rendered_state"] = rec.rendered_state
        out["input"] = inp if stored in ("full", "none", "failed") else None
    st = history.settings() if db.available() else history.DEFAULT_SETTINGS
    days = None
    if rec.template and rec.template.get("seq") and db.available():
        row = db.read().execute("SELECT retention_days FROM templates WHERE seq = ?", (rec.template["seq"],)).fetchone()
        days = row[0] if row else None
    days = days if days is not None else st["history.retention_days"]
    out.update({"extensions": rec.extensions, "answers": answers, "act": rec.act,
                "needs_review": [k for k, a in (answers or {}).items() if not a.get("act")],
                "settings": rec.settings, "usage": rec.usage, "timing": rec.timing, "passes": rec.passes, "notes": rec.notes,
                "warnings": rec.warnings, "source": {**rec.source, "retry_of": None}, "group": rec.group, "batch": None,
                "eval": None, "rerun_of": rec.rerun_of, "metadata": rec.metadata, "pinned": False, "feedback": {},
                "store": stored if stored != "failed" else "none",
                "expires_at": int(rec.created_ms / 1000 + days * 86400) if (days and stored in ("full", "answers_only")) else None,
                "error": rec.error})
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Background decisions


async def start_background(r: Resolved, source: Source) -> dict:
    did = new_id("dec")
    created = db.now_ms()
    rec = history.Record(id=did, created_ms=created, completed_ms=None, status="queued", storage=r.storage,
                         source=source.as_dict(), template=r.template, model=r.model, model_requested=r.model_requested,
                         questions=r.questions, input_hash=T.sha({"state": render_state(r.stored_state), "media": []}),
                         variables_hash=r.variables_hash, variables=r.variables, state=r.stored_state,
                         rendered_state=render_state(r.stored_state), extensions=r.extensions, settings=r.settings,
                         warnings=r.warnings, metadata=r.metadata, secrets=r.secrets, rerun_of=r.rerun_of)
    await asyncio.to_thread(db.write, lambda c: history.insert(c, rec))
    BACKGROUND[did] = {"cancel": False, "started": False}

    async def run():
        try:
            if BACKGROUND[did]["cancel"]:
                return
            await asyncio.to_thread(db.write, lambda c: c.execute("UPDATE decisions SET status = 'in_progress' WHERE id = ?", (did,)))
            out = await execute(r, source, did=did, created_ms=created)
            await asyncio.to_thread(db.write, lambda c: _replace(c, out.record))
        except ApiError as e:
            error = {"type": e.type, "code": e.code, "message": e.message}
            await asyncio.to_thread(db.write, lambda c: _fail(c, did, error))
        except Exception as e:   # noqa: BLE001
            error = {"type": "api_error", "code": "internal_error", "message": f"{type(e).__name__}: {e}"}
            await asyncio.to_thread(db.write, lambda c: _fail(c, did, error))
        finally:
            BACKGROUND.pop(did, None)
    BACKGROUND[did]["task"] = asyncio.create_task(run())
    body = response_object(rec, r.include, r.storage)
    body["status"] = "queued"
    return body


def _replace(c, rec: history.Record):
    """A finished background decision: the queued row becomes the full record (same id, same creation time)."""
    old = c.execute("SELECT seq, pinned, metadata FROM decisions WHERE id = ?", (rec.id,)).fetchone()
    if not old:
        return
    pinned, meta = old["pinned"], json.loads(old["metadata"] or "{}")
    c.execute("DELETE FROM decisions WHERE seq = ?", (old["seq"],))
    rec.metadata = {**rec.metadata, **meta}
    seq = history.insert(c, rec)
    if pinned:
        c.execute("UPDATE decisions SET pinned = 1 WHERE seq = ?", (seq,))


def _fail(c, did: str, error: dict):
    row = c.execute("SELECT seq, status FROM decisions WHERE id = ?", (did,)).fetchone()
    if not row or row["status"] not in ("queued", "in_progress"):
        return
    c.execute("UPDATE decision_bodies SET error = ? WHERE decision_seq = ?", (json.dumps(error), row["seq"]))
    c.execute("UPDATE decisions SET status = 'failed', error_code = ?, completed_at = ? WHERE seq = ?",
              (error["code"], db.now_ms(), row["seq"]))


def cancel(did: str) -> dict:
    row = history._row(did)
    if row["status"] not in ("queued", "in_progress"):
        raise ApiError(409, "decision_finished", f"{did} has already finished ({row['status']}).")
    job = BACKGROUND.get(did)
    if job and job.get("started"):
        raise ApiError(409, "decision_finished", f"{did} is already with the model and will finish in a moment.")
    if job:
        job["cancel"] = True
        t = job.get("task")
        if t:
            t.cancel()
    db.write(lambda c: c.execute("UPDATE decisions SET status = 'cancelled', completed_at = ? WHERE id = ?", (db.now_ms(), did)))
    return history.decision_object(did)


def recover() -> int:
    """At startup: background decisions that were queued or running when the studio stopped are marked failed."""
    if not db.available() or db.get().read_only:
        return 0
    rows = db.read().execute("SELECT id FROM decisions WHERE status IN ('queued','in_progress')").fetchall()
    for r in rows:
        db.write(lambda c, did=r[0]: _fail(c, did, {"type": "api_error", "code": "interrupted",
                                                    "message": "The studio stopped before this decision finished; create it again."}))
    return len(rows)


# ----------------------------------------------------------------------------------------------------------------------
# Rerun (section 3.3)


def rerun_body(did: str, body: dict) -> dict:
    row = history._row(did)
    c = db.read()
    if row["storage"] != "full":
        raise ApiError(409, "input_unavailable", "This decision kept only its answers, so it cannot be rerun.")
    b = c.execute("SELECT * FROM decision_bodies WHERE decision_seq = ?", (row["seq"],)).fetchone()
    if b["redacted"]:
        raise ApiError(409, "input_unavailable", "Parts of this decision's input were redacted, so it cannot be rerun.")
    qs = json.loads(c.execute("SELECT definition FROM question_sets WHERE hash = ?", (row["questions_hash"],)).fetchone()[0])
    ext = json.loads(b["extensions"])
    media = [{"type": m["type"], "file_id": m["id"]} for m in c.execute(
        "SELECT m.type, m.variable, f.id FROM decision_media m LEFT JOIN files f ON f.seq = m.file_seq WHERE m.decision_seq = ? "
        "AND m.variable IS NULL ORDER BY m.position", (row["seq"],))]
    if any(m["file_id"] is None for m in media):
        raise ApiError(409, "input_unavailable", "This decision's media files are no longer kept.")
    new: dict = {}
    tref = body.get("template")
    if row["template_seq"] and row["attribution"] in ("explicit", "draft") or tref:
        tid = c.execute("SELECT id FROM templates WHERE seq = ?", (row["template_seq"],)).fetchone()
        new["template"] = tref or f"{tid[0]}@{row['version_number']}"
        variables = json.loads(b["variables"]) if b["variables"] else None
        if variables is not None:
            variables = {**variables, **(body.get("variables") or {})}
            missing = [k for k, v in variables.items() if isinstance(v, dict) and "$redacted" in v]
            if missing:
                raise ApiError(409, "input_unavailable", f"Sensitive values are never kept; send them again in variables: "
                                                         f"{', '.join(missing)}.", f"variables.{missing[0]}")
            if any(isinstance(v, str) and v.endswith(" not kept]") for v in variables.values()):
                raise ApiError(409, "input_unavailable", "This decision's media files are no longer kept.")
            new["variables"] = variables
        else:
            new["state"] = json.loads(b["state"])
        if ext.get("questions"):
            new["questions"] = {k: qs[k] for k in ext["questions"] if k in qs}
        if ext.get("options"):
            new["add_options"] = {}
            for k, names in ext["options"].items():
                crit = qs.get(k, {}).get("criteria")
                if qs.get(k, {}).get("type") == "number":
                    new["add_options"][k] = [float(x) if "." in str(x) else int(float(x)) for x in names]
                else:
                    new["add_options"][k] = {n: (crit or {}).get(n) if isinstance(crit, dict) else None for n in names}
        if ext.get("skipped"):
            new["skip"] = ext["skipped"]
    else:
        new["state"] = json.loads(b["state"])
        new["questions"] = qs
    if media:
        new["media"] = media
    st = json.loads(b["settings"] or "{}")
    src = st.get("sources") or {}
    req_settings = {k: st[k] for k in ("act_threshold", "temperature") if src.get(k) == "request"}
    for path, s in src.items():
        if s == "request.questions":
            _, q, key = path.split(".", 2)
            req_settings.setdefault("questions", {}).setdefault(q, {})[key] = st["questions"][q][key]
    new["settings"] = body.get("settings", req_settings) or None
    new["model"] = body.get("model") or row["model"]
    if "store" in body:
        new["store"] = body["store"]
    new["metadata"] = {k: v for k, v in json.loads(row["metadata"] or "{}").items() if not k.startswith("openrouter.")}
    if "include" in body:
        new["include"] = body["include"]
    return {k: v for k, v in new.items() if v is not None}


# ----------------------------------------------------------------------------------------------------------------------
# The wire routes (/v1/systemone and the gateway formats): stateless answers, recorded on the side


@dataclass
class WireExtensions:
    store: str
    metadata: dict
    act_threshold: float | None
    warnings: list = field(default_factory=list)


def wire_extensions(raw, headers) -> WireExtensions:
    """Studio-only request keys, read leniently: nothing here ever turns a valid TypeSafe request into an error."""
    warnings: list = []
    raw = raw if isinstance(raw, dict) else {}
    store = storage_level(raw.get("store"), headers.get("x-basal-store"), None, warnings, lenient=True)
    meta = request_metadata(raw.get("metadata"), headers.get("x-basal-metadata"), raw, warnings, lenient=True)
    at = (raw.get("settings") or {}).get("act_threshold") if isinstance(raw.get("settings"), dict) else None
    if not (isinstance(at, (int, float)) and not isinstance(at, bool) and 0 < at <= 1):
        at = None
    warnings = [w for w in warnings if w["code"] != "store_downgraded"]
    return WireExtensions(store, meta, at, warnings)


def wire_attribution(ref: str, questions: dict) -> tuple[dict | None, dict, list]:
    """X-Basal-Template: label a stateless call with a template version when every template question was sent
    unchanged. -> (template dict or None, origins, warnings)."""
    try:
        h, n, resolved_from = TS.resolve(ref)
    except ApiError as e:
        return None, {k: "adhoc" for k in questions}, [{"code": "template_mismatch", "message": e.message, "param": "X-Basal-Template"}]
    defn = TS.definition(h["seq"], n)
    tq = defn["questions"]
    has_ph = any(T.placeholders(s) for q in tq.values() for _, s in T._walk_strings(q)) or any(T.is_dynamic(q) for q in tq.values())
    if has_ph or any(k not in questions or _same_form(questions[k]) != _same_form(q) for k, q in tq.items()):
        return None, {k: "adhoc" for k in questions}, [{"code": "template_mismatch", "param": "X-Basal-Template",
                                                        "message": f"The request's questions differ from {h['id']}@{n}, so it was "
                                                                   "stored without a template."}]
    tpl = {"id": h["id"], "version": n, "ref": ref, "resolved_from": resolved_from, "attribution": "header", "seq": h["seq"]}
    return tpl, {k: ("template" if k in tq else "extra") for k in questions}, []


def _same_form(q: dict) -> str:
    """A question as the wire parser leaves it, so a template question and a parsed request compare equal."""
    try:
        return T.canonical(clean_question(T._QUESTION.validate_python(q).model_dump()))
    except ValidationError:
        return T.canonical(q)


def clean_question(q: dict) -> dict:
    return {k: v for k, v in q.items() if v is not None}


def wire_record(norm: dict, res: dict | None, status: int, error: dict | None, source: Source, ext: WireExtensions, *,
                model: str | None, media: list, template: dict | None, origins: dict, timing: dict, did: str,
                created_ms: int, group: dict | None = None) -> history.Record:
    questions = {k: clean_question(q) for k, q in (norm.get("questions") or {}).items()}
    thr = ext.act_threshold if ext.act_threshold is not None else default_act_threshold()
    per = {k: {"act_threshold": thr} for k in questions}
    answers = raw = None
    act = lowest = None
    if res is not None:
        answers = copy.deepcopy(res.get("answers"))
        raw = res.get("raw_probabilities")
        act, _, lowest = history.gate_answers(answers, per, origins, set())
    temp = (norm.get("settings") or {}).get("temperature")
    settings = {"act_threshold": thr, "temperature": temp or 1.0, "questions": {},
                "sources": {"act_threshold": "request" if ext.act_threshold is not None else "studio",
                            "temperature": "request" if temp else "studio"}}
    state = norm.get("state")
    return history.Record(
        id=did, created_ms=created_ms, completed_ms=db.now_ms(), status="completed" if error is None else "failed",
        storage=ext.store if ext.store != "none" else "full", source=source.as_dict(), template=template, model=model,
        model_requested=norm.get("model"), questions=questions,
        input_hash=T.sha({"state": render_state(state), "media": [m.sha256 for m in media]}), state=state,
        rendered_state=render_state(state), media=media, settings=settings, answers=answers, raw_probabilities=raw, act=act,
        min_certainty=lowest, timing=timing, usage=(res or {}).get("usage") or {}, passes=(res or {}).get("passes"),
        notes=(res or {}).get("notes") or [], warnings=ext.warnings, metadata=ext.metadata, error=error, http_status=status,
        group=group)


# ----------------------------------------------------------------------------------------------------------------------
# Idempotency keys (section 4.9)


_memory_keys: dict[str, dict] = {}


def request_hash(method: str, path: str, body_bytes: bytes) -> str:
    try:
        canon = T.canonical(json.loads(body_bytes or b"null"))
    except ValueError:
        canon = body_bytes.decode(errors="replace")
    return hashlib.sha256(f"{method} {path}\n{canon}".encode()).hexdigest()


def idem_begin(key: str, method: str, path: str, body_bytes: bytes, in_memory: bool):
    """-> None (run it), or a dict {status, headers, body} to replay. Raises ApiError 409 on conflicts."""
    if len(key) > 255 or not key.isprintable():
        raise ApiError(400, "invalid_parameter", "Idempotency-Key is up to 255 printable characters.", "Idempotency-Key")
    h = request_hash(method, path, body_bytes)
    now = db.now_ms()
    mp = f"{method} {path}"
    if in_memory or not db.available() or db.get().read_only:
        for k in [k for k, v in _memory_keys.items() if v["at"] < now - 600_000]:
            _memory_keys.pop(k, None)
        cur = _memory_keys.get(key)
        if cur is None:
            _memory_keys[key] = {"hash": h, "state": "in_progress", "at": now}
            return None
        return _idem_existing(cur["hash"], cur["state"], h, cur.get("response"))

    def tx(c):
        c.execute("DELETE FROM idempotency_keys WHERE workspace_id = ? AND key = ? AND created_at < ?", (db.WS, key, now - 86400_000))
        ins = c.execute("INSERT OR IGNORE INTO idempotency_keys (workspace_id, key, method_path, request_hash, state, created_at) "
                        "VALUES (?,?,?,?,'in_progress',?)", (db.WS, key, mp, h, now))
        if ins.rowcount:
            return None
        return dict(c.execute("SELECT * FROM idempotency_keys WHERE workspace_id = ? AND key = ?", (db.WS, key)).fetchone())
    row = db.write(tx)
    if row is None:
        return None
    stored = None
    if row["state"] == "done":
        stored = {"status": row["status_code"], "headers": json.loads(row["response_headers"] or "{}"), "body": row["response_body"]}
    if row["state"] == "in_progress" and row["created_at"] < now - 15 * 60_000:
        db.write(lambda c: c.execute("UPDATE idempotency_keys SET created_at = ? WHERE workspace_id = ? AND key = ?", (now, db.WS, key)))
        return None
    return _idem_existing(row["request_hash"], row["state"], h, stored)


def _idem_existing(old_hash: str, state: str, h: str, response):
    if old_hash != h:
        raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request. Use a "
                                                      "new key for each logical call.", "Idempotency-Key")
    if state == "in_progress":
        raise ApiError(409, "idempotency_in_progress", "A request with this Idempotency-Key is still running; retry in a moment.",
                       "Idempotency-Key")
    return response


def idem_finish(key: str, status: int, headers: dict, body: bytes, object_id: str | None, in_memory: bool):
    if in_memory or not db.available() or db.get().read_only:
        if 200 <= status < 300:
            _memory_keys[key] = {**_memory_keys.get(key, {}), "state": "done",
                                 "response": {"status": status, "headers": headers, "body": body}}
        else:
            _memory_keys.pop(key, None)
        return
    if 200 <= status < 300:
        db.write(lambda c: c.execute("UPDATE idempotency_keys SET state = 'done', status_code = ?, response_headers = ?, "
                                     "response_body = ?, object_id = ? WHERE workspace_id = ? AND key = ?",
                                     (status, json.dumps(headers), body, object_id, db.WS, key)))
    else:
        db.write(lambda c: c.execute("DELETE FROM idempotency_keys WHERE workspace_id = ? AND key = ?", (db.WS, key)))
