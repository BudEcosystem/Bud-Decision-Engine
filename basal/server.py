"""Bud Decision Studio's main server.

    python -m basal.server            # http://127.0.0.1:8420

Serves the web UI and three groups of endpoints:

* The decision API, compatible with TypeSafe's Jev:  POST /v1/systemone, GET /v1/models
* The studio API: /v1/studio/... templates, decisions with history, feedback, examples, files (basal/studio_api.py)
* Studio management: /api/state, /api/models/{id}/download|load|eject, /api/uploads, /api/compare, /api/usage
* The UI itself at /

Models run in separate worker processes (basal.workers); this process never
touches the GPU, so it stays responsive while a 24 GB model loads.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, api_compat, blobs, db, decisions, guard, history, studio_api, sysinfo
from . import config as runtime_config
from . import template_store
from .catalog import BY_ID, CATALOG
from .errors import ApiError
from .hub import META, Downloader, delete_model_files, hub_cache, model_status, repo_likes, repo_size
from .ids import new_id
from .paths import DATA, LOGS, UI, UPLOADS
from .workers import Workers, log_tail

API_KEY = os.environ.get("BASAL_API_KEY")
AUTH_LOCAL = os.environ.get("BASAL_AUTH_LOCAL") == "1"          # enforce the key for this machine too (used by tests)
NO_DOWNLOADS = os.environ.get("BASAL_NO_DOWNLOADS") == "1"      # a secondary instance must never touch the download queue
ALIASES = {"jev-latest", "jev-preview", "typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.13",
           "typesafe-ai/jev", "jev-1.13", "jev-1.13.0", "kev-latest", "default", "auto", ""}
workers = Workers()
downloader: Downloader | None = None
ACTIVITY_FILE = DATA / "activity.jsonl"      # the request log before history; imported once by migration 0002


def client_of(request: Request) -> str:
    """A readable name for whoever sent a request, from headers the official SDKs and common tools send."""
    h = request.headers
    if h.get("x-basal-client"):
        return "Studio"
    rt = (h.get("x-typesafe-runtime") or "").lower()
    if h.get("x-typesafe-sdk"):
        return "TypeSafe Python SDK" if rt.startswith("python") else "TypeSafe JS SDK"
    ua = (h.get("user-agent") or "").lower()
    for key, name in (("curl", "curl"), ("python-requests", "Python"), ("python-httpx", "Python"), ("httpx", "Python"),
                      ("aiohttp", "Python"), ("node", "Node.js"), ("undici", "Node.js"), ("bun", "Bun"), ("deno", "Deno"),
                      ("postman", "Postman"), ("mozilla", "Browser")):
        if key in ua:
            return name
    return "Other"
SETTINGS_FILE = DATA / "settings.json"
SETTINGS = {"auto_load": True, "idle_eject_minutes": 0}
try:
    SETTINGS.update({k: v for k, v in json.loads(SETTINGS_FILE.read_text()).items() if k in SETTINGS})
except Exception:
    pass


async def _poll_loop():
    while True:
        try:
            await workers.poll()
            idle = SETTINGS.get("idle_eject_minutes") or 0
            if idle:
                for mid, h in list(workers.handles.items()):
                    if h.status == "ready" and time.time() - max(h.last_used, h.started) > idle * 60:
                        print(f"[idle] ejecting {mid}: unused for {idle} min", flush=True)
                        await workers.stop(mid)
        except Exception as e:  # never let monitoring die
            print("[poll]", e, flush=True)
        await asyncio.sleep(0.8)


async def _watch_parent(pid: int):
    """Started by the desktop app: if the app goes away without stopping us (force quit, crash), shut down cleanly
    so no model keeps holding memory."""
    import psutil
    while True:
        await asyncio.sleep(2)
        if not psutil.pid_exists(pid):
            print(f"[desktop] the app (pid {pid}) has exited; shutting down", flush=True)
            import signal
            signal.raise_signal(signal.SIGINT if os.name == "nt" else signal.SIGTERM)   # the normal, graceful shutdown
            return


async def _sweep_loop():
    """Retention: 60 s after startup, then hourly (and after a settings change, from the settings endpoint)."""
    await asyncio.sleep(60)
    while True:
        try:
            r = await asyncio.to_thread(history.sweep)
            if r.get("deleted") or r.get("files"):
                print(f"[history] swept {r}", flush=True)
        except Exception as e:  # noqa: BLE001 - never let the sweeper die
            history.SWEEP["error"] = str(e)
            print("[history] sweep failed:", e, flush=True)
        await asyncio.sleep(3600)


def _open_history():
    try:
        d = db.open(DATA / "studio.db")
    except Exception as e:  # noqa: BLE001 - decisions keep working without history
        print(f"[history] could not open {DATA / 'studio.db'}: {e}. Decisions still work; nothing is saved.", flush=True)
        return
    if not d.available or d.read_only:
        print(f"[history] {d.reason}", flush=True)
        return
    if d.applied:
        print(f"[history] database ready (migrations {d.applied} applied)", flush=True)
    try:
        n = template_store.seed_builtins()
        if n:
            print(f"[templates] {n} starter template(s) added or updated", flush=True)
    except Exception as e:  # noqa: BLE001
        print("[templates] could not seed starter templates:", e, flush=True)
    n = decisions.recover()
    if n:
        print(f"[history] {n} background decision(s) interrupted by the last shutdown were marked failed", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global downloader
    await asyncio.to_thread(_open_history)
    sweeper = asyncio.create_task(_sweep_loop())
    cutoff = time.time() - 24 * 3600
    for p in UPLOADS.glob("*"):
        try:
            if p.stat().st_mtime < cutoff: p.unlink()
        except OSError:
            pass
    await asyncio.to_thread(META.refresh, sorted({r.id for m in CATALOG if m.adapter != "fake" for r in m.repos()}))
    downloader = None if NO_DOWNLOADS else Downloader()
    task = asyncio.create_task(_poll_loop())
    parent = int(os.environ.get("BASAL_PARENT_PID") or 0)
    watch = asyncio.create_task(_watch_parent(parent)) if parent else None
    yield
    task.cancel()
    sweeper.cancel()
    if watch:
        watch.cancel()
    await workers.stop_all()
    if db.available():
        db.get().close()


app = FastAPI(title="Bud Decision Studio", version=__version__, lifespan=lifespan,
              description="Local runtime for System One decision models. `POST /v1/systemone` is compatible with TypeSafe's Jev API.")
# Browsers on other sites must not drive the studio. No CORS by default (opt in with BASAL_CORS_ORIGINS, comma-separated).
CORS_ORIGINS = [o.strip() for o in os.environ.get("BASAL_CORS_ORIGINS", "").split(",") if o.strip()]
if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
PUBLIC_API_PREFIXES = ("/v1/", "/api/v1/", "/api/alpha/", "/typesafe/v1/")
BIND = {"host": "127.0.0.1", "port": 8420}


STUDIO = "/v1/studio"
REMOTE_OPEN = {("POST", "/v1/studio/decisions"), ("POST", "/v1/studio/decisions/preview"), ("GET", "/v1/studio/models")}


def _wire_format(path: str) -> str:
    return {"/api/v1/systemone": "openrouter", "/api/alpha/decisions": "openrouter", "/typesafe/v1/systemone": "vercel",
            "/v1/evaluate": "evaluate"}.get(path, "typesafe")


def _refuse(path: str, status: int, code: str, message: str, rid: str) -> JSONResponse:
    """An error before any handler ran, in the shape the path's clients expect."""
    if path.startswith(STUDIO):
        resp = JSONResponse(ApiError(status, code, message).body(rid), status)
        resp.headers["x-request-id"] = rid
    elif path.startswith(PUBLIC_API_PREFIXES):
        resp = JSONResponse(api_compat.error_body(_wire_format(path), status, message), status)
    else:
        resp = JSONResponse({"detail": message}, status)
    resp.headers["x-typesafe-request-id"] = rid
    return resp


@app.middleware("http")
async def auth_and_timing(request: Request, call_next):
    t = time.perf_counter()
    path = request.url.path
    rid = request.headers.get("x-typesafe-request-id") or api_compat.request_id()
    request.state.request_id = rid
    # DNS-rebinding guard: when listening on this machine only, the Host header must name this machine.
    if BIND["host"] in ("127.0.0.1", "localhost", "::1"):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") else (request.headers.get("host") or "").split("]")[0] + "]"
        if host and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": f"Requests must be addressed to localhost, not '{host}'."}, 403)
    # Other websites in the person's browser may not write to the studio (history would record whatever they send).
    refused = guard.check(request)
    if refused:
        return _refuse(path, *refused, rid)
    public_api = path.startswith(PUBLIC_API_PREFIXES)
    # Management calls that change something must carry a header a plain web form can't send.
    if (path.startswith("/api/") and not public_api and request.method in ("POST", "PUT", "DELETE", "PATCH")
            and not request.headers.get("x-basal-client")):
        return JSONResponse({"detail": "Management requests need the header 'X-Basal-Client: 1' (it protects the studio from other websites)."}, 403)
    remote = request.client and request.client.host not in ("127.0.0.1", "::1")
    studio = path.startswith(STUDIO)
    if API_KEY and (public_api or path.startswith("/api/")) and (remote or AUTH_LOCAL):
        auth = request.headers.get("authorization", "")
        if studio and not hmac.compare_digest(auth, f"Bearer {API_KEY}"):
            return _refuse(path, 401, "missing_api_key" if not auth else "invalid_api_key",
                           "Send Authorization: Bearer <BASAL_API_KEY>." if not auth else "The API key is not valid.", rid)
        if not auth:   # TypeSafe answers a missing key with 403 and a wrong one with 401, before validating the body
            resp = JSONResponse({"detail": {"error_type": "authentication_error", "message": "Must supply an API key! Check your request and try again."}}, 403)
        elif not hmac.compare_digest(auth, f"Bearer {API_KEY}"):
            resp = JSONResponse({"detail": {"error_type": "authentication_error", "message": "Cannot authenticate with the server. Please check your API key and try again."}}, 401)
        else:
            resp = None
        if resp is not None:
            resp.headers["x-typesafe-request-id"] = rid
            return resp
    if studio and remote and not API_KEY and (request.method, path.rstrip("/")) not in REMOTE_OPEN:
        return _refuse(path, 403, "remote_access_requires_key",
                       "History, templates and settings are only reachable from this computer unless the studio runs with "
                       "BASAL_API_KEY set.", rid)
    resp = await call_next(request)
    resp.headers["server-timing"] = f"app;dur={(time.perf_counter() - t) * 1000:.1f}"
    resp.headers["x-typesafe-request-id"] = rid
    if studio:
        resp.headers["x-request-id"] = rid
    return resp


@app.exception_handler(ApiError)
async def _api_error(request: Request, e: ApiError):
    rid = getattr(request.state, "request_id", None)
    headers = {"retry-after": "1"} if e.code == "idempotency_in_progress" else None
    if request.url.path.startswith(STUDIO):
        return JSONResponse(e.body(rid), e.status, headers=headers)
    return JSONResponse({"detail": e.message}, e.status, headers=headers)


@app.exception_handler(db.HistoryUnavailable)
async def _history_off(request: Request, e: db.HistoryUnavailable):
    return JSONResponse(ApiError(503, "history_unavailable", str(e)).body(getattr(request.state, "request_id", None)), 503)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, e: StarletteHTTPException):
    if request.url.path.startswith(STUDIO):
        code = {404: "not_found", 405: "method_not_allowed", 409: "conflict"}.get(e.status_code, "error")
        return JSONResponse(ApiError(e.status_code, code, str(e.detail)).body(getattr(request.state, "request_id", None)),
                            e.status_code)
    return JSONResponse({"detail": e.detail}, e.status_code, headers=getattr(e, "headers", None))


# ------------------------------------------------------------------------------------------------------------------
# State for the UI


def model_view(spec) -> dict:
    st = model_status(spec)
    h = workers.handles.get(spec.id)
    d = spec.to_dict()
    d.update({
        "likes": repo_likes(spec.repo),
        "download_bytes": sum(repo_size(r) for r in spec.repos()),
        "base_bytes": repo_size(spec.base) if spec.base else 0,
        "downloaded": st["complete"],
        "download": {"have": st["have"], "partial": st["partial"], "total": st["total"], "remaining": st["remaining"]},
        "worker": h.public() if h else None,
    })
    return d


@app.get("/api/state")
def state():
    s = sysinfo.live(str(hub_cache()))
    models = [model_view(m) for m in CATALOG]
    for m in models:
        w = m["worker"]
        if w:
            w["gpu_gb"] = round(s["gpu_processes"].get(w["pid"], 0.0), 2) or sysinfo.process_tree_rss_gb(w["pid"])
    return {"version": __version__, "models": models, "downloads": downloader.snapshot() if downloader else {},
            "hf_signed_in": _hf_signed_in(), "runtime": runtime_config.summary(),
            "system": s, "settings": SETTINGS, "loaded": workers.ready(),
            "hf_cache": str(hub_cache())}


def _hf_signed_in() -> bool:
    try:
        from huggingface_hub import get_token
        return bool(get_token())
    except Exception:
        return False


@app.get("/api/models/{model_id}")
def model_detail(model_id: str):
    spec = _spec(model_id)
    return model_view(spec)


def _spec(model_id: str):
    spec = BY_ID.get(model_id)
    if not spec:
        raise HTTPException(404, f"Unknown model '{model_id}'. Known: {', '.join(BY_ID)}")
    return spec


# ------------------------------------------------------------------------------------------------------------------
# Downloads


@app.post("/api/models/{model_id}/download")
def download(model_id: str):
    _spec(model_id)
    downloader.enqueue(model_id)
    return downloader.snapshot()


@app.post("/api/downloads")
def download_many(body: dict = Body(...)):
    """Queue the models the person chose; they download one at a time, smallest first."""
    ids = [i for i in body.get("models") or [] if i in BY_ID]
    for i in ids:
        downloader.enqueue(i)
    return downloader.snapshot()


@app.post("/api/downloads/all")
def download_all():
    downloader.enqueue_all()
    return downloader.snapshot()


@app.post("/api/models/{model_id}/download/cancel")
def cancel_download(model_id: str):
    downloader.cancel(model_id)
    return downloader.snapshot()


@app.delete("/api/models/{model_id}/files")
async def delete_files(model_id: str):
    spec = _spec(model_id)
    if model_id in workers.handles:
        await workers.stop(model_id)
    downloader.cancel(model_id)
    downloaded = {m.id for m in CATALOG if model_status(m)["complete"]}
    removed = delete_model_files(spec, downloaded)
    return {"removed": removed}


# ------------------------------------------------------------------------------------------------------------------
# Load / eject


@app.post("/api/models/{model_id}/load")
def load(model_id: str, body: dict = Body(default={})):
    spec = _spec(model_id)
    if not model_status(spec)["complete"]:
        raise HTTPException(409, f"{spec.name} isn't downloaded yet. Download it first.")
    h = workers.start(model_id, body.get("options") or {})
    return h.public()


@app.post("/api/models/{model_id}/eject")
async def eject(model_id: str):
    _spec(model_id)
    ok = await workers.stop(model_id)
    return {"ejected": ok}


@app.post("/api/eject-all")
async def eject_all():
    await workers.stop_all()
    return {"ok": True}


@app.get("/api/models/{model_id}/logs", response_class=PlainTextResponse)
def logs(model_id: str, lines: int = 200):
    _spec(model_id)
    return log_tail(model_id, lines)


@app.post("/api/settings")
def settings(body: dict = Body(...)):
    if "auto_load" in body:
        SETTINGS["auto_load"] = bool(body["auto_load"])
    if "idle_eject_minutes" in body:
        SETTINGS["idle_eject_minutes"] = max(0, int(body["idle_eject_minutes"] or 0))
    try:
        SETTINGS_FILE.write_text(json.dumps(SETTINGS))
    except OSError:
        pass
    return SETTINGS


@app.get("/api/config")
def get_config():
    """Where models run on this computer, as chosen during setup."""
    return runtime_config.summary()


@app.post("/api/config")
def set_config(body: dict = Body(...)):
    dev = body.get("device")
    if dev is not None:
        if dev not in [d["id"] for d in runtime_config.available()]:
            raise HTTPException(400, f"This computer cannot run models on {dev!r}. Run setup again to install support for it.")
        runtime_config.save({"device": dev})
    return runtime_config.summary()


# ------------------------------------------------------------------------------------------------------------------
# Media uploads (images / audio / video for multimodal models). Kept in the file store (basal/blobs.py); this is the
# Playground's older spelling of POST /v1/studio/files.


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        if not db.available():
            raise db.HistoryUnavailable("no store")
        row = await asyncio.to_thread(blobs.create, data, file.filename, file.content_type)
    except ApiError as e:
        raise HTTPException(e.status, e.message)
    except db.HistoryUnavailable:
        # without the database, fall back to a plain temporary file (removed after a day)
        kind = blobs.media_type(file.filename or "file", file.content_type)
        fid = uuid.uuid4().hex + blobs.extension(kind, file.filename, file.content_type)
        if len(data) > blobs.MAX_BYTES:
            raise HTTPException(413, "Files up to 200 MB are supported.")
        (UPLOADS / fid).write_bytes(data)
        return {"id": fid, "type": kind, "name": file.filename, "bytes": len(data), "url": f"/api/uploads/{fid}"}
    return {"id": row["id"], "type": row["type"], "name": row["name"], "bytes": row["bytes"], "url": f"/api/uploads/{row['id']}"}


@app.get("/api/uploads/{fid}")
def get_upload(fid: str):
    if blobs.FILE_ID.match(fid) and db.available():
        row = blobs.get(fid)
        if not row:
            raise HTTPException(404, "Upload not found (it may have been cleaned up); upload the file again.")
        try:
            return blobs.content_response(row)
        except ApiError as e:
            raise HTTPException(e.status, e.message)
    if not blobs.LEGACY_ID.match(fid):
        raise HTTPException(400, "Bad upload id")
    p = UPLOADS / fid
    if not p.exists():
        raise HTTPException(404, "Upload not found (it may have been cleaned up); upload the file again.")
    return FileResponse(p, headers={"x-content-type-options": "nosniff", "content-security-policy": "sandbox; default-src 'none'"})


# ------------------------------------------------------------------------------------------------------------------
# The decision API


def _route(requested: str | None) -> str:
    """Which loaded model serves a request. Accepts a studio id, a Hub repo id, or a generic alias."""
    ready = workers.ready()
    r = (requested or "").strip()
    if r and r not in ALIASES:
        for spec in CATALOG:
            if r in (spec.id, spec.repo.id, spec.name):
                return spec.id
        raise HTTPException(404, f"Unknown model '{r}'. Use one of: {', '.join(BY_ID)}")
    if ready:
        return ready[-1]
    raise HTTPException(409, "No model is loaded. Load one on the Models page (or POST /api/models/{id}/load), "
                             "or name a downloaded model in the request's `model` field.")


async def _ensure_ready(model_id: str, timeout: float = 900) -> None:
    spec = BY_ID[model_id]
    h = workers.handles.get(model_id)
    if h is not None and h.status == "error" and not h.was_ready and h.failed_at and time.time() - h.failed_at < 60:
        # A load that just failed will fail the same way again; say why instead of retrying on every request.
        raise HTTPException(503, f"{spec.name} failed to load: {h.error} Fix the cause, then load it again.")
    if h is None or h.status in ("error", "ejecting"):
        if not SETTINGS["auto_load"]:
            raise HTTPException(409, f"{spec.name} isn't loaded. Load it first (auto-load is off).")
        if not model_status(spec)["complete"]:
            raise HTTPException(409, f"{spec.name} isn't downloaded. Download it on the Models page first.")
        h = workers.start(model_id, {})
    t = time.time()
    while True:
        cur = workers.handles.get(model_id)
        if cur is not h or h.ejecting:
            raise HTTPException(503, f"{spec.name} was ejected while this request was waiting for it to load.")
        if h.status == "ready":
            return
        if h.status == "error":
            raise HTTPException(503, f"{spec.name} failed to load: {h.error}")
        if time.time() - t > timeout:
            raise HTTPException(504, f"{spec.name} is still loading; try again in a moment.")
        await asyncio.sleep(0.3)


def _status_of(spec) -> str:
    h = workers.handles.get(spec.id)
    if h is not None and h.status == "ready":
        return "loaded"
    if h is not None and h.status not in ("error", "ejecting"):
        return "loading"
    return "downloaded" if model_status(spec)["complete"] else "not_downloaded"


async def _decide(model_id: str, body: dict) -> tuple[int, dict]:
    if model_id in workers.handles:
        workers.handles[model_id].last_used = time.time()
    return await workers.decide(model_id, body)


decisions.RUNTIME.route = _route
decisions.RUNTIME.ensure_ready = _ensure_ready
decisions.RUNTIME.decide = _decide
decisions.RUNTIME.status_of = _status_of
decisions.RUNTIME.is_ready = lambda mid: bool(mid) and getattr(workers.handles.get(mid), "status", None) == "ready"
decisions.RUNTIME.client_of = lambda request: client_of(request)


def _surface(request: Request, default: str = "api") -> str:
    """X-Basal-Surface is honoured only from the studio's own pages (which send X-Basal-Client)."""
    h = request.headers
    if h.get("x-basal-client"):
        return h.get("x-basal-surface") or "playground"
    return default


def _attempt(request: Request) -> int:
    try:
        return max(0, int(request.headers.get("x-typesafe-retry-count") or 0))
    except ValueError:
        return 0


async def _run_wire(norm: dict, fmt: str, source: decisions.Source, ext: decisions.WireExtensions, *, extended: bool,
                    template_ref: str | None = None, group: dict | None = None):
    """One stateless decision, recorded on the side. The response body is exactly api_compat.shape(...) as before;
    everything the studio adds goes into history and x-basal-* headers only.
    -> (status, body, headers)."""
    did, created, t0 = new_id("dec"), db.now_ms(), time.perf_counter()
    template, origins, warns = (None, {k: "adhoc" for k in norm["questions"]}, [])
    if template_ref and db.available():
        template, origins, warns = await asyncio.to_thread(decisions.wire_attribution, template_ref, norm["questions"])
        ext.warnings += warns
    headers: dict = {}
    try:
        model_id = _route(norm.get("model"))
    except HTTPException as e:
        if e.status_code == 404:   # an unknown model is a rejected request, not a decision
            history.count_usage(model=None, template_seq=None, surface=source.surface, fmt=fmt, status=404, stored="none")
            return e.status_code, api_compat.error_body(fmt, e.status_code, e.detail), headers
        model_id, code, res, err_detail = None, e.status_code, None, e.detail
    else:
        err_detail = None
    media: list = []
    load_ms = 0.0
    if err_detail is None:
        keep = ext.store == "full" and (not db.available() or history.setting("history.store_media"))
        try:
            media = await asyncio.to_thread(blobs.resolve, norm.get("media") or [], keep=keep)
        except ApiError as e:
            history.count_usage(model=model_id, template_seq=None, surface=source.surface, fmt=fmt, status=e.status, stored="none")
            return e.status, api_compat.error_body(fmt, 422 if e.status == 400 else e.status, e.message), headers
        was_ready = decisions.RUNTIME.is_ready(model_id)
        tl = time.perf_counter()
        try:
            await _ensure_ready(model_id)
            load_ms = 0.0 if was_ready else round((time.perf_counter() - tl) * 1000, 1)
            body = {**norm, "media": [m.worker() for m in media]} if media else dict(norm)
            code, res = await _decide(model_id, body)
            if code != 200:
                err_detail, res = (res.get("detail", res) if isinstance(res, dict) else res), None
        except HTTPException as e:
            code, res, err_detail = e.status_code, None, e.detail
        finally:
            blobs.cleanup(media)
    wall = round((time.perf_counter() - t0) * 1000, 1)
    if res is not None:
        res["model"] = model_id
        res["wall_ms"] = wall
    error = None if res is not None else {"type": "model_error" if code >= 500 else "invalid_request_error",
                                          "code": "model_rejected_input" if code == 422 else "model_error", "message": str(err_detail)}
    timing = {"queue_ms": 0.0, "load_ms": load_ms, "model_ms": (res or {}).get("latency_ms"), "total_ms": wall}
    stored = ext.store
    if ext.store != "none" and db.available():
        rec = decisions.wire_record(norm, res, code, error, source, ext, model=model_id, media=media, template=template,
                                    origins=origins, timing=timing, did=did, created_ms=created, group=group)
        try:
            stored = await decisions.save(rec)
        except ApiError as e:   # history.on_store_error = fail
            return 503, api_compat.error_body(fmt, 503, e.message), {"x-basal-stored": "failed"}
    elif not db.available():
        stored = "none"
    history.count_usage(model=model_id, template_seq=(template or {}).get("seq"), surface=source.surface, fmt=fmt, status=code,
                        stored=stored, model_ms=timing["model_ms"], total_ms=wall)
    headers["x-basal-stored"] = stored
    if stored in ("full", "answers_only"):
        headers["x-basal-decision-id"] = did
    if ext.warnings:
        headers["x-basal-warning"] = ",".join(dict.fromkeys(w["code"] for w in ext.warnings))
    if template_ref:
        headers["x-basal-template-status"] = "attributed" if template else "mismatch"
    if res is None:
        return code, api_compat.error_body(fmt, code, err_detail), headers
    res.pop("raw_probabilities", None)
    return 200, api_compat.shape(fmt, res, norm, extended, model_id), headers


async def _wire(request: Request, fmt: str):
    """One decision in the wire format `fmt` (see basal.api_compat)."""
    raw = await request.body()
    try:
        body = json.loads(raw)
    except Exception:
        return JSONResponse(api_compat.error_body(fmt, api_compat.validation_status(fmt),
                                                  [{"type": "json_invalid", "loc": ["body"], "msg": "Request body must be valid JSON", "input": None}]
                                                  if fmt == "typesafe" else "Request body must be valid JSON"),
                            api_compat.validation_status(fmt))
    ext = decisions.wire_extensions(body, request.headers)
    key = request.headers.get("idempotency-key")
    in_mem = ext.store == "none"
    if key:
        try:
            replay = await asyncio.to_thread(decisions.idem_begin, key, "POST", request.url.path, raw, in_mem)
        except ApiError as e:
            return JSONResponse(api_compat.error_body(fmt, e.status, e.message), e.status,
                                headers={"retry-after": "1"} if e.code == "idempotency_in_progress" else None)
        if replay:
            return Response(replay["body"], replay["status"], media_type="application/json",
                            headers={**replay["headers"], "x-basal-idempotent-replayed": "true"})
    norm, errs = api_compat.parse(body, fmt)
    source = decisions.Source(surface=_surface(request), endpoint=request.url.path, format=fmt, client=client_of(request),
                              request_id=getattr(request.state, "request_id", None), attempt=_attempt(request))
    if errs:
        history.count_usage(model=None, template_seq=None, surface=source.surface, fmt=fmt,
                            status=api_compat.validation_status(fmt), stored="none")
        if key:
            await asyncio.to_thread(decisions.idem_finish, key, 422, {}, b"", None, in_mem)
        return JSONResponse(api_compat.error_body(fmt, api_compat.validation_status(fmt), errs), api_compat.validation_status(fmt))
    extended = request.headers.get(api_compat.EXT_HEADER) == "1"
    status, out, headers = await _run_wire(norm, fmt, source, ext, extended=extended,
                                           template_ref=request.headers.get("x-basal-template"))
    resp = JSONResponse(out, status, headers=headers)
    if key:
        await asyncio.to_thread(decisions.idem_finish, key, status, headers, resp.body, headers.get("x-basal-decision-id"), in_mem)
    return resp


@app.post("/v1/systemone", tags=["Decision API (TypeSafe Jev compatible)"])
async def systemone(request: Request):
    """TypeSafe Jev `POST /v1/systemone`: state + typed questions in, typed answers out. Exact TypeSafe response shape;
    send `X-Basal-Extensions: 1` for the studio's extra answer fields."""
    return await _wire(request, "typesafe")


@app.post("/api/v1/systemone", tags=["Gateway formats"])
async def openrouter_systemone(request: Request):
    """OpenRouter's System One endpoint shape (adds id, provider, usage.cost; OpenRouter error envelope)."""
    return await _wire(request, "openrouter")


@app.post("/api/alpha/decisions", tags=["Gateway formats"])
async def openrouter_decisions(request: Request):
    """OpenRouter's "Decisions API" (alpha): same schema as its System One endpoint."""
    return await _wire(request, "openrouter")


@app.post("/typesafe/v1/systemone", tags=["Gateway formats"])
async def vercel_systemone(request: Request):
    """Vercel AI Gateway's TypeSafe-compatible route (adds provider_metadata; Vercel error envelope)."""
    return await _wire(request, "vercel")


@app.post("/v1/evaluate", tags=["Gateway formats"])
async def vercel_evaluate(request: Request):
    """Vercel AI Gateway's evaluation API: `boolean` questions answered with `probability`, camelCase usage."""
    return await _wire(request, "evaluate")


def _release_date(spec) -> str:
    m = META.get(spec.repo.id) or {}
    return m.get("release_date") or "2026-09-30"


def _models_list():
    """TypeSafe's GET /v1/models shape: {"models": [{name, description, release_date}]}."""
    out = []
    ready = workers.ready()
    if ready:
        out.append({"name": "jev-latest", "description": f"Alias for the most recently loaded model ({BY_ID[ready[-1]].name}).",
                    "release_date": _release_date(BY_ID[ready[-1]])})
    for spec in CATALOG:
        if model_status(spec)["complete"] or spec.id in workers.handles:
            out.append({"name": spec.id, "description": f"{spec.name} by {spec.maker}. {spec.tagline}",
                        "release_date": _release_date(spec)})
    return {"models": out}


@app.get("/v1/models", tags=["Decision API (TypeSafe Jev compatible)"])
def v1_models():
    return _models_list()


@app.get("/typesafe/v1/models", tags=["Gateway formats"])
def vercel_models():
    return _models_list()


@app.post("/api/compare")
async def compare(request: Request, body: dict = Body(...)):
    """Run one request against several models at once (each model runs in its own process, so truly in parallel).
    Every run is saved to history as one comparison group."""
    ids = body.get("models") or workers.ready()
    req = body.get("request") or {}
    if not ids:
        raise HTTPException(409, "Load at least one model to compare.")
    group = {"type": "comparison", "id": new_id("cmp")}
    ext = decisions.wire_extensions(req, request.headers)

    async def one(mid):
        try:
            norm, errs = api_compat.parse({**req, "model": mid}, "typesafe")
            if errs:
                return {"model": mid, "status": 422, "response": {"detail": api_compat._as_text(errs)}}
            source = decisions.Source(surface=_surface(request, "compare") if request.headers.get("x-basal-surface") else "compare",
                                      endpoint="/api/compare", format="typesafe", client=client_of(request),
                                      request_id=getattr(request.state, "request_id", None))
            e2 = decisions.WireExtensions(ext.store, dict(ext.metadata), ext.act_threshold, [])
            code, res, headers = await _run_wire(norm, "typesafe", source, e2, extended=True, group=group)
            return {"model": mid, "status": code, "response": res, "decision_id": headers.get("x-basal-decision-id")}
        except HTTPException as e:
            return {"model": mid, "status": e.status_code, "response": {"detail": e.detail}}
        except Exception as e:  # noqa: BLE001
            return {"model": mid, "status": 500, "response": {"detail": f"{type(e).__name__}: {e}"}}

    return {"results": await asyncio.gather(*[one(m) for m in ids]), "group": group}


# ------------------------------------------------------------------------------------------------------------------
# Conformance: run tests/test_conformance.py against this server and keep the latest result for the API page.

CONFORMANCE = DATA / "conformance.json"
_conformance = {"proc": None, "started": None}


@app.get("/api/conformance")
def conformance():
    last = None
    if CONFORMANCE.exists():
        try:
            last = json.loads(CONFORMANCE.read_text())
        except ValueError:
            last = None
    p = _conformance["proc"]
    return {"last": last, "running": bool(p and p.poll() is None), "started": _conformance["started"]}


@app.post("/api/conformance/run")
def conformance_run():
    p = _conformance["proc"]
    if p and p.poll() is None:
        return {"running": True}
    root = Path(__file__).resolve().parent.parent
    xml = DATA / "conformance.xml"
    env = {**os.environ, "BASAL_TEST_URL": f"http://127.0.0.1:{BIND['port']}"}
    log = open(LOGS / "conformance.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "pytest", "tests/test_conformance.py", "-q", "-p", "no:cacheprovider",
                             f"--junitxml={xml}"], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    _conformance.update(proc=proc, started=time.time())

    def finish():
        proc.wait()
        log.close()
        import xml.etree.ElementTree as ET
        tests = []
        try:
            for tc in ET.parse(xml).getroot().iter("testcase"):
                outcome, msg = "passed", ""
                for tag, word in (("failure", "failed"), ("error", "failed"), ("skipped", "skipped")):
                    el = tc.find(tag)
                    if el is not None:
                        outcome, msg = word, (el.get("message") or "")[:600]
                tests.append({"name": tc.get("name"), "outcome": outcome, "message": msg, "seconds": round(float(tc.get("time") or 0), 2)})
        except (OSError, ET.ParseError):
            pass
        CONFORMANCE.write_text(json.dumps({
            "ran_at": time.time(), "exit_code": proc.returncode, "seconds": round(time.time() - _conformance["started"], 1),
            "passed": sum(t["outcome"] == "passed" for t in tests), "failed": sum(t["outcome"] == "failed" for t in tests),
            "skipped": sum(t["outcome"] == "skipped" for t in tests), "tests": tests}))

    threading.Thread(target=finish, daemon=True).start()
    return {"running": True}


@app.get("/api/history")
def get_history(limit: int = 50):
    """The old Activity log shape, now read from history (kept for one release; use GET /v1/studio/decisions)."""
    if not db.available():
        return []
    rows = db.read().execute("SELECT d.*, b.state, b.answers FROM decisions d LEFT JOIN decision_bodies b ON b.decision_seq = d.seq "
                             "WHERE d.workspace_id = ? ORDER BY d.seq DESC LIMIT ?", (db.WS, max(1, min(limit, 500)))).fetchall()
    out = []
    for r in rows:
        qs = db.read().execute("SELECT definition FROM question_sets WHERE hash = ?", (r["questions_hash"],)).fetchone()
        out.append({"id": r["id"], "time": r["created_at"] / 1000, "model": r["model"], "status": r["http_status"] or 200,
                    "questions": r["n_questions"], "latency_ms": r["model_ms"], "wall_ms": r["total_ms"], "media": r["n_media"],
                    "path": r["endpoint"], "format": r["format"], "client": r["client"], "request_id": r["request_id"],
                    "request": {"model": r["model_requested"], "state": json.loads(r["state"]) if r["state"] else None,
                                "questions": json.loads(qs[0]) if qs else {}},
                    "response": {"model": r["model"], "answers": json.loads(r["answers"]) if r["answers"] else None}})
    return out


@app.delete("/api/history")
def clear_history():
    """Delete every decision in history except pinned ones (the old "Clear" of the Activity page)."""
    if not db.available():
        return {"cleared": True}
    r = history.bulk({"all": True, "include_pinned": False}, "delete", resolve_template=template_store.resolve_for_filter)
    return {"cleared": True, "deleted": r["deleted"], "kept_pinned": r["skipped_pinned"]}


@app.get("/api/usage")
def usage(minutes: int = 1440, bucket: str = "hour"):
    """Request counts over time for the History page's chart, including calls that were not stored."""
    if not db.available():
        return {"bucket": bucket, "rows": [], "recent": list(history.RECENT)[:200]}
    return {**history.usage(max(1, min(minutes, 60 * 24 * 400)), bucket), "recent": list(history.RECENT)[:200]}


app.include_router(studio_api.router, prefix="/v1/studio", tags=["Studio API"])


# ------------------------------------------------------------------------------------------------------------------
# UI


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(UI / "index.html", headers={"cache-control": "no-cache"})


class _UIFiles(StaticFiles):
    """The interface's scripts and styles: always checked for a newer version (a quick 304 when unchanged), so an
    updated app or studio never runs yesterday's cached interface."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


app.mount("/ui", _UIFiles(directory=UI), name="ui")


def main():
    ap = argparse.ArgumentParser(description="Bud Decision Studio")
    ap.add_argument("--host", default=os.environ.get("BASAL_HOST", "127.0.0.1"),
                    help="127.0.0.1 = this machine only (default); 0.0.0.0 = reachable from your network (set BASAL_API_KEY)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("BASAL_PORT", "8420")))
    a = ap.parse_args()
    BIND["host"] = a.host
    BIND["port"] = a.port
    import uvicorn
    print(f"\n  Bud Decision Studio {__version__}  at  http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}\n", flush=True)
    try:
        uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    finally:
        workers.kill_all_sync()


if __name__ == "__main__":
    main()
