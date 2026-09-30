"""The studio API, mounted at /v1/studio: templates, decisions with history, feedback, test examples, files, models and
settings. It sits beside the stateless, TypeSafe-compatible /v1/systemone the way OpenAI's Responses API sits beside
Chat Completions. docs/studio-api.md is the reference.

Handlers read their path and query parameters themselves, so every error, including a bad parameter, comes back in
the one error envelope: {"error": {type, code, message, param, details, request_id, decision_id}}.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import api_compat, blobs, db, decisions, history
from . import template_store as TS
from . import templates as T
from .catalog import BY_ID, CATALOG
from .errors import ApiError

router = APIRouter()


# ----------------------------------------------------------------------------------------------------------------------
# Plumbing


def route(method: str, path: str, op: str, summary: str = ""):
    """Register a handler; template routes are registered again for starter templates (ids like builtin/support)."""
    def deco(fn):
        router.add_api_route(path, fn, methods=[method], operation_id=f"studio.{op}", summary=summary or None,
                             name=f"studio.{op}")
        if "{tid}" in path:
            router.add_api_route(path.replace("{tid}", "builtin/{bname}"), fn, methods=[method], include_in_schema=False,
                                 name=f"studio.{op}.builtin")
        return fn
    return deco


def tid_of(request: Request) -> str:
    pp = request.path_params
    return f"builtin/{pp['bname']}" if "bname" in pp else pp["tid"]


async def body_of(request: Request, required: bool = True):
    raw = await request.body()
    if not raw.strip():
        if required:
            raise ApiError(400, "invalid_json", "The request needs a JSON body.")
        return {}
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ApiError(400, "invalid_json", f"The body is not valid JSON: {e}.")


def query(request: Request) -> dict:
    out: dict = {}
    for k, v in request.query_params.multi_items():
        out[k] = v if k not in out else (out[k] if isinstance(out[k], list) else [out[k]]) + [v]
    return out


def ok(obj, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(obj, status, headers=headers)


async def run(fn, *args, **kw):
    return await asyncio.to_thread(fn, *args, **kw)


def need_history():
    if not db.available():
        raise ApiError(503, "history_unavailable", db.get().reason if db._db else "History is not available.", type_="api_error")


def source_of(request: Request, surface: str | None = None) -> decisions.Source:
    h = request.headers
    surf = surface or ((h.get("x-basal-surface") or "playground") if h.get("x-basal-client") else "api")
    try:
        attempt = max(0, int(h.get("x-typesafe-retry-count") or 0))
    except ValueError:
        attempt = 0
    return decisions.Source(surface=surf, endpoint=request.url.path, format="studio", client=decisions.RUNTIME.client_of(request),
                            request_id=getattr(request.state, "request_id", None), attempt=attempt)


def decision_headers(out: decisions.Outcome) -> dict:
    h = {"x-basal-stored": out.stored}
    if out.stored in ("full", "answers_only"):
        h["x-basal-decision-id"] = out.decision_id
        h["location"] = f"/v1/studio/decisions/{out.decision_id}"
    return h


# ----------------------------------------------------------------------------------------------------------------------
# Decisions


async def _create(request: Request, body: dict, source: decisions.Source, *, rerun_of: str | None = None) -> Response:
    key = request.headers.get("idempotency-key")
    raw = json.dumps(body).encode() if rerun_of else await request.body()
    in_mem = history.parse_store(body.get("store") if isinstance(body, dict) else None) == "none" or \
        history.parse_store(request.headers.get("x-basal-store")) == "none"
    if key:
        replay = await run(decisions.idem_begin, key, "POST", request.url.path, raw, in_mem)
        if replay:
            return Response(replay["body"], replay["status"], headers={**replay["headers"], "x-basal-idempotent-replayed": "true"},
                            media_type="application/json")
    try:
        r = await run(decisions.resolve, body, source, header_store=request.headers.get("x-basal-store"),
                      header_meta=request.headers.get("x-basal-metadata"))
        r.rerun_of = rerun_of
        if r.background:
            obj = await decisions.start_background(r, source)
            resp = ok(obj, 200, {"location": f"/v1/studio/decisions/{obj['id']}", "x-basal-decision-id": obj["id"],
                                 "x-basal-stored": r.storage})
        else:
            out = await decisions.execute(r, source)
            resp = ok(out.body, out.status, decision_headers(out))
    except ApiError as e:
        history.count_usage(model=None, template_seq=None, surface=source.surface, fmt="studio", status=e.status, stored="none")
        if key:
            await run(decisions.idem_finish, key, e.status, {}, b"", None, in_mem)
        raise
    if key:
        hdrs = {k: v for k, v in resp.headers.items() if k.startswith("x-basal") or k == "location"}
        await run(decisions.idem_finish, key, resp.status_code, hdrs, resp.body, resp.headers.get("x-basal-decision-id"), in_mem)
    return resp


@route("POST", "/decisions", "decisions.create", "Make a decision (with or without a template); stored in history")
async def create_decision(request: Request):
    body = await body_of(request)
    return await _create(request, body, source_of(request))


@route("POST", "/decisions/preview", "decisions.preview", "Resolve and validate a decision without running it")
async def preview_decision(request: Request):
    body = await body_of(request)
    r = await run(decisions.resolve, body, source_of(request), header_store=request.headers.get("x-basal-store"),
                  header_meta=request.headers.get("x-basal-metadata"), preview=True)
    return ok(decisions.preview_object(r))


@route("GET", "/decisions", "decisions.list", "History: filter and paginate decisions")
async def list_decisions(request: Request):
    need_history()
    return ok(await run(history.list_decisions, query(request), resolve_template=TS.resolve_for_filter))


@route("GET", "/decisions/stats", "decisions.stats", "Aggregates over any history filter")
async def decision_stats(request: Request):
    need_history()
    f = query(request)
    return ok(await run(history.stats, f, resolve_template=TS.resolve_for_filter, comparability=_comparability_for(f)))


def _comparability_for(f: dict):
    ref = f.get("template")
    if not ref or ref == "none":
        return None
    try:
        seq, _ = TS.resolve_for_filter(ref)
    except ApiError:
        return None
    return lambda a, b: {k: T.compare_question(TS.definition(seq, a)["questions"].get(k), v)
                         for k, v in {**TS.definition(seq, a)["questions"], **TS.definition(seq, b)["questions"]}.items()} \
        if a and b else {}


@route("GET", "/decisions/export", "decisions.export", "Every matching decision as JSONL or CSV")
async def export_decisions(request: Request):
    need_history()
    f = query(request)
    fmt = f.get("format", "jsonl")
    if fmt not in ("jsonl", "csv"):
        raise ApiError(400, "invalid_parameter", "format is jsonl or csv.", "format")
    gen = await run(lambda: list(history.export(f, fmt, resolve_template=TS.resolve_for_filter)))
    return StreamingResponse(iter(gen), media_type="application/x-ndjson" if fmt == "jsonl" else "text/csv",
                             headers={"content-disposition": f'attachment; filename="decisions.{fmt}"'})


@route("POST", "/decisions/delete", "decisions.bulk_delete", "Delete every decision matching a filter")
async def bulk_delete(request: Request):
    need_history()
    return ok(await run(history.bulk, await body_of(request), "delete", resolve_template=TS.resolve_for_filter))


@route("POST", "/decisions/redact", "decisions.bulk_redact", "Remove input fields from every decision matching a filter")
async def bulk_redact(request: Request):
    need_history()
    return ok(await run(history.bulk, await body_of(request), "redact", resolve_template=TS.resolve_for_filter))


def _includes(f: dict, default: set) -> set:
    inc = set(history.csv_list(f.get("include")))
    bad = inc - decisions.INCLUDES
    if bad:
        raise ApiError(400, "invalid_parameter", f"include takes {', '.join(sorted(decisions.INCLUDES))}.", "include")
    return inc or default


@route("GET", "/decisions/{did}", "decisions.retrieve", "One decision (?wait=, ?format=)")
async def get_decision(request: Request):
    need_history()
    did = request.path_params["did"]
    f = query(request)
    wait = f.get("wait")
    if wait:
        try:
            deadline = time.time() + min(60.0, max(0.0, float(wait)))
        except ValueError:
            raise ApiError(400, "invalid_parameter", "wait is a number of seconds, up to 60.", "wait")
        while time.time() < deadline:
            row = await run(history._row, did)
            if row["status"] not in ("queued", "in_progress"):
                break
            await asyncio.sleep(0.25)
    obj = await run(history.decision_object, did, _includes(f, {"input"}) | {"input"})
    fmt = f.get("format")
    if fmt:
        if fmt not in ("typesafe", "openrouter", "vercel", "evaluate"):
            raise ApiError(400, "invalid_parameter", "format is typesafe, openrouter, vercel or evaluate.", "format")
        if obj["answers"] is None:
            raise ApiError(409, "decision_finished", "This decision has no answers to render.")
        qs = (obj.get("input") or {}).get("questions") or {}
        res = {"answers": obj["answers"], "usage": obj["usage"], "latency_ms": (obj["timing"] or {}).get("model_ms"),
               "passes": obj["passes"], "notes": obj["notes"]}
        return ok(api_compat.shape(fmt, res, {"questions": qs, "model": obj["model_requested"] or obj["model"]},
                                   history.truthy(f.get("extended")) is True, obj["model"]))
    return ok(obj)


@route("GET", "/decisions/{did}/input", "decisions.input", "The stored input of a decision")
async def get_input(request: Request):
    need_history()
    did = request.path_params["did"]
    obj = await run(history.decision_object, did, {"input", "input.rendered_state"})
    if obj.get("input") is None or (obj["input"].get("redacted")):
        raise ApiError(409, "input_unavailable", "This decision's input was not stored (answers only) or was redacted.")
    return ok({"object": "decision.input", "decision_id": did, **obj["input"]})


@route("PATCH", "/decisions/{did}", "decisions.update", "Change a decision's metadata or pin it")
async def patch_decision(request: Request):
    need_history()
    did = request.path_params["did"]
    body = await body_of(request)
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "The body is an object.")
    await run(history.patch_decision, did, body)
    return ok(await run(history.decision_object, did))


@route("DELETE", "/decisions/{did}", "decisions.delete", "Delete a decision and everything stored with it")
async def delete_decision(request: Request):
    need_history()
    return ok(await run(history.delete_decision, request.path_params["did"]))


@route("POST", "/decisions/{did}/redact", "decisions.redact", "Remove input fields from one decision, keep its answers")
async def redact_decision(request: Request):
    need_history()
    did = request.path_params["did"]
    body = await body_of(request)
    await run(history.redact_decision, did, (body or {}).get("fields"))
    return ok(await run(history.decision_object, did))


@route("POST", "/decisions/{did}/cancel", "decisions.cancel", "Cancel a queued background decision")
async def cancel_decision(request: Request):
    need_history()
    return ok(await run(decisions.cancel, request.path_params["did"]))


@route("POST", "/decisions/{did}/rerun", "decisions.rerun", "The same input again, on another model, version or settings")
async def rerun_decision(request: Request):
    need_history()
    did = request.path_params["did"]
    body = await body_of(request, required=False)
    new = await run(decisions.rerun_body, did, body or {})
    return await _create(request, new, source_of(request, "rerun"), rerun_of=did)


@route("POST", "/decisions/{did}/feedback", "decisions.feedback.create", "Label a decision's answers, or rate it")
async def add_feedback(request: Request):
    need_history()
    did = request.path_params["did"]
    data = await run(history.add_feedback, did, await body_of(request))
    return ok({"object": "list", "data": data, "first_id": data[0]["id"], "last_id": data[-1]["id"], "has_more": False})


@route("GET", "/decisions/{did}/feedback", "decisions.feedback.list", "All feedback on a decision")
async def list_feedback(request: Request):
    need_history()
    return ok(await run(history.list_feedback, request.path_params["did"]))


@route("DELETE", "/feedback/{fid}", "feedback.delete", "Delete one feedback entry")
async def delete_feedback(request: Request):
    need_history()
    return ok(await run(history.delete_feedback, request.path_params["fid"]))


# ----------------------------------------------------------------------------------------------------------------------
# Templates


def _tpl_headers(obj: dict, created: bool = False) -> dict:
    h = {"etag": f'"{obj["aliases"]["latest"]}"'}
    if created:
        h["location"] = f"/v1/studio/templates/{obj['id']}"
    return h


@route("POST", "/templates", "templates.create", "Create a template at version 1")
async def create_template(request: Request):
    need_history()
    obj, status = await run(TS.create, await body_of(request))
    return ok(obj, status, _tpl_headers(obj, status == 201))


@route("GET", "/templates", "templates.list", "The template library")
async def list_templates(request: Request):
    need_history()
    return ok(await run(TS.list_templates, query(request)))


@route("GET", "/templates/{tid}", "templates.retrieve", "A template's head and one version's definition")
async def get_template(request: Request):
    need_history()
    tid, f = tid_of(request), query(request)

    def go():
        h = TS.head(tid, include_deleted=history.truthy(f.get("include_deleted")) is True)
        n = TS.version_number(h, f["version"]) if f.get("version") else h["latest_version"]
        obj = TS.template_object(h, n)
        if "compatibility" in (f.get("include") or ""):
            obj["compatibility"] = T.compatibility(TS.definition(h["seq"], n), decisions.RUNTIME.status_of)
        return obj
    obj = await run(go)
    return ok(obj, 200, _tpl_headers(obj))


@route("PUT", "/templates/{tid}", "templates.upsert", "Create or replace a template (idempotent)")
async def put_template(request: Request):
    need_history()
    obj, status = await run(TS.upsert, tid_of(request), await body_of(request), if_match=request.headers.get("if-match"))
    return ok(obj, status, _tpl_headers(obj, status == 201))


@route("PATCH", "/templates/{tid}", "templates.update", "Merge-patch a template; a new version only if the definition changes")
async def patch_template(request: Request):
    need_history()
    obj = await run(TS.patch, tid_of(request), await body_of(request), if_match=request.headers.get("if-match"))
    return ok(obj, 200, _tpl_headers(obj))


@route("DELETE", "/templates/{tid}", "templates.delete", "Delete a template (?confirm=<id>&history=keep|delete)")
async def delete_template(request: Request):
    need_history()
    f = query(request)
    return ok(await run(TS.delete, tid_of(request), f.get("confirm"), f.get("history", "keep")))


@route("POST", "/templates/{tid}/archive", "templates.archive", "Hide a template; it stays callable")
async def archive_template(request: Request):
    need_history()
    return ok(await run(TS.set_archived, tid_of(request), True))


@route("POST", "/templates/{tid}/unarchive", "templates.unarchive", "Show an archived template again")
async def unarchive_template(request: Request):
    need_history()
    return ok(await run(TS.set_archived, tid_of(request), False))


@route("GET", "/templates/{tid}/versions", "templates.versions.list", "Versions, newest first")
async def list_versions(request: Request):
    need_history()
    return ok(await run(TS.list_versions, tid_of(request), query(request)))


@route("POST", "/templates/{tid}/versions", "templates.versions.create", "Save a new version from a full definition")
async def create_version(request: Request):
    need_history()
    obj, created = await run(TS.new_version, tid_of(request), await body_of(request), if_match=request.headers.get("if-match"))
    return ok(obj, 200, {"x-basal-version-created": "true" if created else "false", "etag": f'"{obj["version"]}"'})


@route("GET", "/templates/{tid}/versions/{n}", "templates.versions.retrieve", "One version (a number, an alias or latest)")
async def get_version(request: Request):
    need_history()
    tid, n = tid_of(request), request.path_params["n"]

    def go():
        h = TS.head(tid, include_deleted=True)
        return TS.version_object(h, TS.version_row(h["seq"], TS.version_number(h, n)))
    return ok(await run(go))


@route("GET", "/templates/{tid}/versions/{n}/diff", "templates.versions.diff", "What changed between two versions")
async def diff_version(request: Request):
    need_history()
    tid, n, f = tid_of(request), request.path_params["n"], query(request)

    def go():
        h = TS.head(tid, include_deleted=True)
        to = TS.version_number(h, n)
        frm = TS.version_number(h, f["against"]) if f.get("against") else to - 1
        if frm < 1:
            raise ApiError(400, "invalid_parameter", "Version 1 has no earlier version; pass ?against=<n>.", "against")
        d = T.diff(TS.definition(h["seq"], frm), TS.definition(h["seq"], to))
        return {"object": "template.diff", "template": tid, "from": frm, "to": to, **d}
    return ok(await run(go))


@route("POST", "/templates/{tid}/versions/{n}/restore", "templates.versions.restore", "A new version with an old version's definition")
async def restore_version(request: Request):
    need_history()
    tid = tid_of(request)
    body = await body_of(request, required=False)

    def go():
        h = TS.head(tid)
        return TS.restore(tid, TS.version_number(h, request.path_params["n"]), (body or {}).get("note"))
    obj = await run(go)
    return ok(obj, 200, _tpl_headers(obj))


@route("PUT", "/templates/{tid}/aliases/{alias}", "templates.aliases.set", "Point an alias (such as production) at a version")
async def set_alias(request: Request):
    need_history()
    body = await body_of(request)
    return ok(await run(TS.set_alias, tid_of(request), request.path_params["alias"], (body or {}).get("version")))


@route("DELETE", "/templates/{tid}/aliases/{alias}", "templates.aliases.delete", "Remove an alias")
async def delete_alias(request: Request):
    need_history()
    return ok(await run(TS.delete_alias, tid_of(request), request.path_params["alias"]))


@route("GET", "/templates/{tid}/schema", "templates.schema", "JSON Schema of a version's variables")
async def template_schema(request: Request):
    need_history()
    tid, f = tid_of(request), query(request)

    def go():
        h = TS.head(tid)
        n = TS.version_number(h, f["version"]) if f.get("version") else h["latest_version"]
        return T.schema(TS.definition(h["seq"], n), f"{tid}@{n}")
    return ok(await run(go))


@route("GET", "/templates/{tid}/compatibility", "templates.compatibility", "Which models can run a version")
async def template_compat(request: Request):
    need_history()
    tid, f = tid_of(request), query(request)

    def go():
        h = TS.head(tid)
        n = TS.version_number(h, f["version"]) if f.get("version") else h["latest_version"]
        return {"object": "template.compatibility", "template": tid, "version": n,
                "models": T.compatibility(TS.definition(h["seq"], n), decisions.RUNTIME.status_of)}
    return ok(await run(go))


@route("GET", "/templates/{tid}/decisions", "templates.decisions.list", "This template's history")
async def template_decisions(request: Request):
    need_history()
    tid = tid_of(request)
    f = query(request)
    f.pop("template", None)
    return ok(await run(history.list_decisions, f, resolve_template=TS.resolve_for_filter, fixed_template=tid))


@route("GET", "/templates/{tid}/stats", "templates.stats", "This template's aggregates, by version by default")
async def template_stats(request: Request):
    need_history()
    tid = tid_of(request)
    f = query(request)
    f.pop("template", None)
    seq, _ = await run(TS.resolve_for_filter, tid)

    def comp(a, b):
        da, db_ = TS.definition(seq, a)["questions"], TS.definition(seq, b)["questions"]
        return {k: T.compare_question(da.get(k), db_.get(k)) for k in list(da) + [k for k in db_ if k not in da]}
    return ok(await run(history.stats, f, resolve_template=TS.resolve_for_filter, fixed_template=tid,
                        default_group=["version"], comparability=comp))


@route("GET", "/templates/{tid}/compare", "templates.compare", "Compare two versions over history, including paired inputs")
async def template_compare(request: Request):
    need_history()
    tid = tid_of(request)
    f = query(request)

    def go():
        h = TS.head(tid, include_deleted=True)
        vs = history.csv_list(f.get("versions"))
        if len(vs) != 2:
            if h["latest_version"] < 2:
                raise ApiError(400, "invalid_parameter", f"{tid} has one version; save another to compare.", "versions")
            vs = [str(h["latest_version"] - 1), str(h["latest_version"])]
        nums = [TS.version_number(h, v) for v in vs]
        da, db_ = TS.definition(h["seq"], nums[0]), TS.definition(h["seq"], nums[1])
        comp = {k: T.compare_question(da["questions"].get(k), db_["questions"].get(k))
                for k in list(da["questions"]) + [k for k in db_["questions"] if k not in da["questions"]]}
        by = "variables_hash" if (da["variables"] and db_["variables"]) else "input_hash"
        ff = {k: v for k, v in f.items() if k not in ("versions",)}
        return history.compare_versions(h["seq"], nums, ff, comp, by, resolve_template=TS.resolve_for_filter, template_id=tid)
    return ok(await run(go))


@route("GET", "/templates/{tid}/examples", "templates.examples.list", "Test examples")
async def list_examples(request: Request):
    need_history()
    return ok(await run(TS.list_examples, tid_of(request), query(request)))


@route("POST", "/templates/{tid}/examples", "templates.examples.create", "Add a test example (or promote a decision)")
async def add_example(request: Request):
    need_history()
    return ok(await run(TS.add_example, tid_of(request), await body_of(request)))


@route("POST", "/templates/{tid}/examples/import", "templates.examples.import_", "Add up to 5,000 examples")
async def import_examples(request: Request):
    need_history()
    return ok(await run(TS.import_examples, tid_of(request), await body_of(request)))


@route("GET", "/templates/{tid}/examples/export", "templates.examples.export", "Current examples as JSONL")
async def export_examples(request: Request):
    need_history()
    tid = tid_of(request)
    lines = await run(lambda: list(TS.export_examples(tid)))
    return Response("".join(lines), media_type="application/x-ndjson",
                    headers={"content-disposition": f'attachment; filename="{tid.replace("/", "-")}-examples.jsonl"'})


@route("GET", "/templates/{tid}/examples/{eid}", "templates.examples.retrieve", "One example (?revision=)")
async def get_example(request: Request):
    need_history()
    f = query(request)
    return ok(await run(TS.get_example, tid_of(request), request.path_params["eid"],
                        int(f["revision"]) if f.get("revision") else None))


@route("PATCH", "/templates/{tid}/examples/{eid}", "templates.examples.update", "Edit an example (writes a new revision)")
async def patch_example(request: Request):
    need_history()
    return ok(await run(TS.patch_example, tid_of(request), request.path_params["eid"], await body_of(request)))


@route("DELETE", "/templates/{tid}/examples/{eid}", "templates.examples.delete", "Retire an example")
async def delete_example(request: Request):
    need_history()
    return ok(await run(TS.delete_example, tid_of(request), request.path_params["eid"]))


# ----------------------------------------------------------------------------------------------------------------------
# Files, models, settings


@route("POST", "/files", "files.create", "Upload an image, audio or video file (multipart `file`, or raw bytes)")
async def create_file(request: Request):
    need_history()
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise ApiError(400, "missing_field", "Send the file in a multipart field named `file`.", "file")
        data, name, mime = await f.read(), f.filename, f.content_type
        purpose = form.get("purpose") or "media"
    else:
        data = await request.body()
        name = request.headers.get("x-filename") or request.query_params.get("name")
        mime = ctype or None
        purpose = request.query_params.get("purpose") or "media"
    if purpose != "media":
        raise ApiError(400, "invalid_field", "Only purpose=media is available in this version.", "purpose")
    if not data:
        raise ApiError(400, "missing_field", "The file is empty.", "file")
    row = await run(blobs.create, data, name, mime)
    return ok(blobs.file_object({**row, "ext": blobs.extension(row["type"], name, mime), "stored": 1}))


@route("GET", "/files/{fid}", "files.retrieve", "A file's metadata")
async def get_file(request: Request):
    need_history()
    row = await run(blobs.get, request.path_params["fid"])
    if not row:
        raise ApiError(404, "file_not_found", f"No file '{request.path_params['fid']}'.")
    return ok(blobs.file_object(row))


@route("GET", "/files/{fid}/content", "files.content", "A file's bytes")
async def get_file_content(request: Request):
    need_history()
    row = await run(blobs.get, request.path_params["fid"])
    if not row:
        raise ApiError(404, "file_not_found", f"No file '{request.path_params['fid']}'.")
    return blobs.content_response(row)


@route("DELETE", "/files/{fid}", "files.delete", "Delete a file's bytes, even when decisions used it")
async def delete_file(request: Request):
    need_history()
    fid = request.path_params["fid"]
    row = await run(blobs.get, fid)
    if not row:
        raise ApiError(404, "file_not_found", f"No file '{fid}'.")
    await run(db.write, lambda c: (blobs.delete(c, row), history.audit(c, "file.deleted", fid)))
    return ok({"id": fid, "object": "file.deleted", "deleted": True})


def model_object(spec) -> dict:
    return {"id": spec.id, "object": "model", "name": spec.name, "maker": spec.maker, "modalities": list(spec.modalities),
            "types": list(spec.types), "max_options": spec.max_options, "max_questions": spec.max_questions,
            "context_tokens": spec.context_tokens, "status": decisions.RUNTIME.status_of(spec), "revision": None}


@route("GET", "/models", "models.list", "Every model the studio knows, with its limits and status")
async def list_models(request: Request):
    data = await run(lambda: [model_object(s) for s in CATALOG])
    return ok({"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
               "last_id": data[-1]["id"] if data else None, "has_more": False})


@route("GET", "/models/{mid}", "models.retrieve", "One model")
async def get_model(request: Request):
    spec = T.find_model(request.path_params["mid"])
    if not spec:
        raise ApiError(404, "model_not_found", f"Unknown model '{request.path_params['mid']}'. Use one of: {', '.join(BY_ID)}.")
    return ok(await run(model_object, spec))


def settings_object() -> dict:
    s = history.settings()
    return {"object": "settings",
            "history": {k.split(".", 1)[1]: s[k] for k in s if k.startswith("history.")},
            "decisions": {"default_act_threshold": s["decisions.default_act_threshold"]},
            "storage": history.storage_info()}


@route("GET", "/settings", "settings.retrieve", "History and decision settings, and storage use")
async def get_settings(request: Request):
    need_history()
    return ok(await run(settings_object))


@route("PATCH", "/settings", "settings.update", "Change history and decision settings")
async def patch_settings(request: Request):
    need_history()
    body = await body_of(request)
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "The body is an object: {history: {...}, decisions: {...}}.")
    changes = {}
    rules = {
        "history.store": lambda v: v in ("full", "answers_only", "none"),
        "history.retention_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
        "history.max_storage_gb": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0,
        "history.store_media": lambda v: isinstance(v, bool),
        "history.keep_labelled": lambda v: isinstance(v, bool),
        "history.on_store_error": lambda v: v in ("serve", "fail"),
        "history.notice_acknowledged_at": lambda v: v is None or isinstance(v, (int, float)),
        "decisions.default_act_threshold": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1,
    }
    for group, vals in body.items():
        if group == "storage":
            raise ApiError(400, "read_only_field", "storage is read-only.", "storage")
        if group not in ("history", "decisions") or not isinstance(vals, dict):
            raise ApiError(400, "invalid_field", f"'{group}' is not a settings group; use history or decisions.", group)
        for k, v in vals.items():
            key = f"{group}.{k}"
            if key not in rules:
                raise ApiError(400, "invalid_field", f"'{key}' is not a setting.", key)
            if key == "history.notice_acknowledged_at" and v is True:
                v = int(time.time())
            if not rules[key](v):
                raise ApiError(400, "invalid_field", f"{key} got an invalid value: {json.dumps(v)}.", key)
            changes[key] = v
    await run(history.save_settings, changes)
    if any(k in changes for k in ("history.retention_days", "history.max_storage_gb", "history.keep_labelled")):
        asyncio.get_running_loop().run_in_executor(None, history.sweep)
    return ok(await run(settings_object))
