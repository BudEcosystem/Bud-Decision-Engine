"""Bud Decision Studio's main server.

    python -m basal.server            # http://127.0.0.1:8420

Serves the web UI and three groups of endpoints:

* The decision API, compatible with TypeSafe's Jev:  POST /v1/systemone, GET /v1/models
* Studio management: /api/state, /api/models/{id}/download|load|eject, /api/uploads, /api/compare, /api/history
* The UI itself at /

Models run in separate worker processes (basal.workers); this process never
touches the GPU, so it stays responsive while a 24 GB model loads.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hmac
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, api_compat, sysinfo
from . import config as runtime_config
from .catalog import BY_ID, CATALOG
from .contract import SystemOneRequest
from .hub import META, Downloader, delete_model_files, hub_cache, model_status, repo_likes, repo_size
from .paths import DATA, LOGS, UI, UPLOADS
from .workers import Workers, log_tail

API_KEY = os.environ.get("BASAL_API_KEY")
AUTH_LOCAL = os.environ.get("BASAL_AUTH_LOCAL") == "1"          # enforce the key for this machine too (used by tests)
NO_DOWNLOADS = os.environ.get("BASAL_NO_DOWNLOADS") == "1"      # a secondary instance must never touch the download queue
ALIASES = {"jev-latest", "jev-preview", "typesafe/jev-latest", "~typesafe/jev-latest", "typesafe/jev-1.13",
           "typesafe-ai/jev", "jev-1.13", "jev-1.13.0", "kev-latest", "default", "auto", ""}
MEDIA_EXT = {"image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
             "audio": {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"},
             "video": {".mp4", ".mov", ".webm", ".mkv", ".avi"}}

workers = Workers()
downloader: Downloader | None = None
history: deque = deque(maxlen=500)
ACTIVITY_FILE = DATA / "activity.jsonl"


def _load_activity():
    """The request log survives restarts: keep the newest 500 entries from data/activity.jsonl."""
    try:
        lines = ACTIVITY_FILE.read_text().splitlines()
    except OSError:
        return
    if len(lines) > 5000:      # keep the file bounded
        lines = lines[-2000:]
        ACTIVITY_FILE.write_text("\n".join(lines) + "\n")
    for line in lines[-500:]:
        try:
            history.appendleft(json.loads(line))
        except ValueError:
            pass


def _record(entry: dict):
    history.appendleft(entry)
    try:
        with ACTIVITY_FILE.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global downloader
    _load_activity()
    cutoff = time.time() - 24 * 3600
    for p in UPLOADS.glob("*"):
        try:
            if p.stat().st_mtime < cutoff: p.unlink()
        except OSError:
            pass
    await asyncio.to_thread(META.refresh, sorted({r.id for m in CATALOG for r in m.repos()}))
    downloader = None if NO_DOWNLOADS else Downloader()
    task = asyncio.create_task(_poll_loop())
    parent = int(os.environ.get("BASAL_PARENT_PID") or 0)
    watch = asyncio.create_task(_watch_parent(parent)) if parent else None
    yield
    task.cancel()
    if watch:
        watch.cancel()
    await workers.stop_all()


app = FastAPI(title="Bud Decision Studio", version=__version__, lifespan=lifespan,
              description="Local runtime for System One decision models. `POST /v1/systemone` is compatible with TypeSafe's Jev API.")
# Browsers on other sites must not drive the studio. No CORS by default (opt in with BASAL_CORS_ORIGINS, comma-separated).
CORS_ORIGINS = [o.strip() for o in os.environ.get("BASAL_CORS_ORIGINS", "").split(",") if o.strip()]
if CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
PUBLIC_API_PREFIXES = ("/v1/", "/api/v1/", "/api/alpha/", "/typesafe/v1/")
BIND = {"host": "127.0.0.1", "port": 8420}


@app.middleware("http")
async def auth_and_timing(request: Request, call_next):
    t = time.perf_counter()
    path = request.url.path
    # DNS-rebinding guard: when listening on this machine only, the Host header must name this machine.
    if BIND["host"] in ("127.0.0.1", "localhost", "::1"):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (request.headers.get("host") or "").startswith("[") else (request.headers.get("host") or "").split("]")[0] + "]"
        if host and host not in LOCAL_HOSTS:
            return JSONResponse({"detail": f"Requests must be addressed to localhost, not '{host}'."}, 403)
    public_api = path.startswith(PUBLIC_API_PREFIXES)
    # Cross-site request guard: management calls that change something must carry a header a plain web form can't send.
    if (path.startswith("/api/") and not public_api and request.method in ("POST", "PUT", "DELETE", "PATCH")
            and not request.headers.get("x-basal-client")):
        return JSONResponse({"detail": "Management requests need the header 'X-Basal-Client: 1' (it protects the studio from other websites)."}, 403)
    rid = request.headers.get("x-typesafe-request-id") or api_compat.request_id()
    request.state.request_id = rid
    remote = request.client and request.client.host not in ("127.0.0.1", "::1")
    if API_KEY and (public_api or path.startswith("/api/")) and (remote or AUTH_LOCAL):
        auth = request.headers.get("authorization", "")
        if not auth:   # TypeSafe answers a missing key with 403 and a wrong one with 401, before validating the body
            resp = JSONResponse({"detail": {"error_type": "authentication_error", "message": "Must supply an API key! Check your request and try again."}}, 403)
        elif not hmac.compare_digest(auth, f"Bearer {API_KEY}"):
            resp = JSONResponse({"detail": {"error_type": "authentication_error", "message": "Cannot authenticate with the server. Please check your API key and try again."}}, 401)
        else:
            resp = None
        if resp is not None:
            resp.headers["x-typesafe-request-id"] = rid
            return resp
    resp = await call_next(request)
    resp.headers["server-timing"] = f"app;dur={(time.perf_counter() - t) * 1000:.1f}"
    resp.headers["x-typesafe-request-id"] = rid
    return resp


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
# Media uploads (images / audio / video for multimodal models)


def _media_type(filename: str, content_type: str | None) -> str:
    ext = Path(filename).suffix.lower()
    for kind, exts in MEDIA_EXT.items():
        if ext in exts:
            return "video" if ext == ".webm" and (content_type or "").startswith("video") else kind
    ct = (content_type or "").split("/")[0]
    if ct in MEDIA_EXT:
        return ct
    raise HTTPException(415, f"Unsupported file type '{ext or content_type}'. Use an image, audio or video file.")


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    kind = _media_type(file.filename or "file", file.content_type)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MEDIA_EXT[kind]:
        ext = mimetypes.guess_extension(file.content_type or "") or ""
        ext = ext if ext in MEDIA_EXT[kind] else ""
    fid = uuid.uuid4().hex + ext
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "Files up to 200 MB are supported.")
    (UPLOADS / fid).write_bytes(data)
    return {"id": fid, "type": kind, "name": file.filename, "bytes": len(data), "url": f"/api/uploads/{fid}"}


@app.get("/api/uploads/{fid}")
def get_upload(fid: str):
    p = _upload_path(fid)
    return FileResponse(p)


def _upload_path(fid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}(\.[a-z0-9]{1,5})?", fid):
        raise HTTPException(400, "Bad upload id")
    p = UPLOADS / fid
    if not p.exists():
        raise HTTPException(404, "Upload not found (it may have been cleaned up); upload the file again.")
    return p


def _resolve_media(body: dict) -> dict:
    """Turn data: URLs and upload ids into files the worker can read. Arbitrary server paths are never accepted."""
    out = []
    for m in body.get("media") or []:
        m = dict(m)
        if m.get("data"):
            mt = re.match(r"data:([\w/+.-]+);base64,(.*)$", m["data"], re.S)
            if not mt:
                raise HTTPException(422, "media.data must be a base64 data: URL")
            try:
                raw = base64.b64decode(mt.group(2), validate=False)
            except binascii.Error:
                raise HTTPException(422, "media.data is not valid base64")
            ext = mimetypes.guess_extension(mt.group(1)) or ""
            ext = ext if any(ext in v for v in MEDIA_EXT.values()) else ""
            p = UPLOADS / (uuid.uuid4().hex + ext)
            p.write_bytes(raw)
            m = {"type": m.get("type") or _media_type(p.name, mt.group(1)), "path": str(p), "name": m.get("name")}
        elif m.get("path"):
            p = _upload_path(Path(m["path"]).name)
            m = {"type": m.get("type") or _media_type(p.name, None), "path": str(p), "name": m.get("name") or p.name}
        out.append(m)
    return {**body, "media": out}


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


async def _run(body: dict, meta: dict | None = None) -> tuple[int, dict]:
    """Route, load if needed, and ask the model. `body` is an already-validated TypeSafe-shaped request.
    -> (status, full studio response with every answer field)."""
    model_id = _route(body.get("model"))
    await _ensure_ready(model_id)
    body = _resolve_media(body)
    t = time.perf_counter()
    if model_id in workers.handles:
        workers.handles[model_id].last_used = time.time()
    code, res = await workers.decide(model_id, body)
    wall = round((time.perf_counter() - t) * 1000, 1)
    if code == 200:
        res["model"] = model_id
        res["wall_ms"] = wall
    _record({"id": uuid.uuid4().hex[:10], "time": time.time(), "model": model_id, "status": code,
             "questions": len(body.get("questions") or {}), "latency_ms": res.get("latency_ms") if code == 200 else None,
             "wall_ms": wall, "media": len(body.get("media") or []), **(meta or {}),
             "request": {k: v for k, v in body.items() if k != "media"}, "response": res})
    return code, res


async def _wire(request: Request, fmt: str):
    """One decision in the wire format `fmt` (see basal.api_compat)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(api_compat.error_body(fmt, api_compat.validation_status(fmt),
                                                  [{"type": "json_invalid", "loc": ["body"], "msg": "Request body must be valid JSON", "input": None}]
                                                  if fmt == "typesafe" else "Request body must be valid JSON"),
                            api_compat.validation_status(fmt))
    norm, errs = api_compat.parse(body, fmt)
    if errs:
        return JSONResponse(api_compat.error_body(fmt, api_compat.validation_status(fmt), errs), api_compat.validation_status(fmt))
    extended = request.headers.get(api_compat.EXT_HEADER) == "1"
    meta = {"path": request.url.path, "format": fmt, "client": client_of(request),
            "request_id": getattr(request.state, "request_id", None)}
    try:
        code, res = await _run(norm, meta)
    except HTTPException as e:
        return JSONResponse(api_compat.error_body(fmt, e.status_code, e.detail), e.status_code)
    if code != 200:
        return JSONResponse(api_compat.error_body(fmt, code, res.get("detail", res)), code)
    return api_compat.shape(fmt, res, norm, extended, res["model"])


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
async def compare(body: dict = Body(...)):
    """Run one request against several models at once (each model runs in its own process, so truly in parallel)."""
    ids = body.get("models") or workers.ready()
    request = body.get("request") or {}
    if not ids:
        raise HTTPException(409, "Load at least one model to compare.")

    async def one(mid):
        try:
            norm, errs = api_compat.parse({**request, "model": mid}, "typesafe")
            if errs:
                return {"model": mid, "status": 422, "response": {"detail": api_compat._as_text(errs)}}
            code, res = await _run(norm, {"path": "/api/compare", "format": "typesafe", "client": "Studio (compare)"})
            if code == 200:
                res = api_compat.shape("typesafe", res, norm, True, mid)
            return {"model": mid, "status": code, "response": res}
        except HTTPException as e:
            return {"model": mid, "status": e.status_code, "response": {"detail": e.detail}}
        except Exception as e:  # noqa: BLE001
            return {"model": mid, "status": 500, "response": {"detail": f"{type(e).__name__}: {e}"}}

    return {"results": await asyncio.gather(*[one(m) for m in ids])}


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
    return list(history)[:max(1, min(limit, 500))]


@app.delete("/api/history")
def clear_history():
    history.clear()
    try:
        ACTIVITY_FILE.write_text("")
    except OSError:
        pass
    return {"cleared": True}


# ------------------------------------------------------------------------------------------------------------------
# UI


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(UI / "index.html", headers={"cache-control": "no-cache"})


app.mount("/ui", StaticFiles(directory=UI), name="ui")


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
