"""A worker process that holds exactly one model.

The studio starts one of these per loaded model (`python -m basal.worker ...`)
and talks to it over HTTP on 127.0.0.1. Ejecting a model means stopping its
worker, which is the only reliable way to hand every byte of GPU memory back:
PyTorch caches allocations and libraries keep references, so "unloading" inside
a long-lived process leaks. A crash here also can't take the studio down.

Endpoints: GET /health, POST /decide, POST /shutdown.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # weights are downloaded by the studio; never hit the network here
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("USE_TF", "0")                   # Laya: TensorFlow probing can deadlock model construction
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import adapters
from .adapters.base import DecideInput
from .catalog import BY_ID
from .contract import SystemOneRequest, approx_tokens, build_answers, normalise, raw_probabilities, render


class State:
    status = "starting"      # starting | loading | warming | ready | error | stopping
    stage = "Starting the worker process"
    progress: float | None = None
    error: str | None = None
    detail: str | None = None
    warning: str | None = None
    started = time.time()
    ready_at: float | None = None
    requests = 0
    busy_ms = 0.0
    adapter = None
    lock = threading.Lock()


S = State()
app = FastAPI(title="basal-worker")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def set_stage(text: str, progress: float | None = None) -> None:
    S.stage, S.progress = text, progress
    log(f"stage: {text}" + (f" ({progress:.0%})" if progress is not None else ""))


def gpu_memory() -> dict:
    try:
        import torch
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            return {"allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 2),
                    "reserved_gb": round(torch.cuda.memory_reserved() / 1e9, 2),
                    "peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2)}
        if hasattr(torch, "mps") and torch.backends.mps.is_available():
            return {"allocated_gb": round(torch.mps.current_allocated_memory() / 1e9, 2),
                    "reserved_gb": round(torch.mps.driver_allocated_memory() / 1e9, 2), "peak_gb": None}
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return {"allocated_gb": round(torch.xpu.memory_allocated() / 1e9, 2),
                    "reserved_gb": round(torch.xpu.memory_reserved() / 1e9, 2),
                    "peak_gb": round(torch.xpu.max_memory_allocated() / 1e9, 2)}
    except Exception:
        pass
    return {}


def friendly(e: BaseException) -> str:
    """Turn common low-level failures into something a person can act on."""
    s = f"{type(e).__name__}: {e}"
    low = s.lower()
    if "out of memory" in low or "cuda oom" in low:
        return "The GPU ran out of memory. Eject another model to free space, then try again."
    if "offline" in low and ("cannot find" in low or "not found" in low or "localentrynotfound" in low):
        return "Some model files are missing on disk. Open the Models page and download this model again."
    if isinstance(e, ModuleNotFoundError):
        return f"A Python package this model needs is not installed ({e.name}). Run ./install.sh again."
    return s


def load_model(model_id: str, options: dict) -> None:
    spec = BY_ID[model_id]
    try:
        S.status = "loading"
        set_stage("Importing the model's code", 0.05)
        cls = adapters.get(spec.adapter)
        S.adapter = cls(spec, options, set_stage)
        t = time.time()
        S.adapter.load()
        log(f"loaded in {time.time() - t:.1f}s; memory {gpu_memory()}")
        actual = S.adapter.effective_device()
        if actual == "cpu" and S.adapter.device != "cpu":
            S.warning = ("Running on the processor, about ten times slower: the GPU did not have enough free memory when "
                         "this model loaded. Eject other models (or close other GPU programs) and load it again.")
            log("warning: " + S.warning)
        S.status = "warming"
        set_stage("Warming up (first run compiles GPU kernels)", 0.95)
        S.adapter.warmup()
        S.status, S.ready_at = "ready", time.time()
        set_stage("Ready", 1.0)
    except BaseException as e:  # noqa: BLE001 — report everything, including import errors
        S.status, S.error, S.detail = "error", friendly(e), traceback.format_exc()
        log("load failed:\n" + S.detail)


@app.get("/health")
def health():
    return {"model": ARGS.model, "pid": os.getpid(), "status": S.status, "stage": S.stage, "progress": S.progress,
            "error": S.error, "detail": S.detail, "warning": S.warning, "uptime_s": round(time.time() - S.started, 1),
            "load_seconds": round(S.ready_at - S.started, 1) if S.ready_at else None,
            "requests": S.requests, "busy_ms": round(S.busy_ms, 1), "memory": gpu_memory(), "options": OPTIONS}


@app.post("/decide")
def decide(body: dict):
    if S.status != "ready":
        raise HTTPException(503, f"model is not ready ({S.status}: {S.stage})")
    try:
        req = SystemOneRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(422, str(e))
    qs = normalise(req)
    spec = S.adapter.spec
    problems = []
    for q in qs:
        if q.type not in spec.types:
            problems.append(f"'{q.display_id}': {spec.name} does not support {q.type} questions")
        if len(q.keys) > spec.max_options:
            problems.append(f"'{q.display_id}': {len(q.keys)} options, but {spec.name} accepts at most {spec.max_options}")
    if len(qs) > spec.max_questions:
        problems.append(f"{len(qs)} questions after expanding pick-all-that-apply options, but {spec.name} answers at most "
                        f"{spec.max_questions} per request" if any(q.role == "multi" for q in qs) else
                        f"{len(qs)} questions, but {spec.name} accepts at most {spec.max_questions} per request")
    media = [{"type": m.type, "path": m.path, "name": m.name} for m in req.media]
    for m in media:
        if m["type"] not in spec.modalities:
            problems.append(f"{spec.name} cannot read {m['type']} input; try a model that lists '{m['type']}'")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    x = DecideInput(req, qs, render(req.state), media)
    trivial = [i for i, q in enumerate(qs) if len(q.keys) == 1]
    if trivial:   # one option: nothing to decide
        live = [q for i, q in enumerate(qs) if i not in trivial]
        x = DecideInput(req, live, render(req.state), media)
    with S.lock:
        t = time.perf_counter()
        try:
            if trivial and not live:
                from .adapters.base import DecideOutput
                out = DecideOutput([])
            else:
                out = S.adapter.decide(x)
            if trivial:
                it = iter(out.probs)
                out.probs = [[1.0] if i in trivial else next(it) for i in range(len(qs))]
        except Exception as e:  # noqa: BLE001
            log("decide failed:\n" + traceback.format_exc())
            msg = friendly(e)
            status = 422 if isinstance(e, ValueError) else 500
            return JSONResponse({"detail": msg}, status_code=status)
        ms = (time.perf_counter() - t) * 1000
    S.requests += 1
    S.busy_ms += ms
    temps = {q: s.temperature for q, s in req.settings.questions.items() if s.temperature}
    answers = build_answers(qs, out.probs, req.settings.temperature, temps)
    for qid, extra in out.extras.items():
        if qid in answers and answers[qid].get("type") in ("choice", "score", "noul"): answers[qid]["model_extras"] = extra
    # raw_probabilities sits beside the answers, never inside them, so no wire format can pass it on by accident
    return {"model": ARGS.model, "answers": answers, "raw_probabilities": raw_probabilities(qs, out.probs),
            "usage": {"input_tokens": out.input_tokens or approx_tokens(req), "output_tokens": 0},
            "latency_ms": round(ms, 1), "passes": out.passes, "notes": out.notes + ([S.warning] if S.warning else [])}


@app.post("/shutdown")
def shutdown():
    S.status = "stopping"
    threading.Timer(0.2, lambda: os._exit(0)).start()
    return {"ok": True}


def main():
    global ARGS, OPTIONS
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(BY_ID))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--options", default="{}")
    ARGS = ap.parse_args()
    OPTIONS = json.loads(ARGS.options)
    log(f"worker for {ARGS.model} (pid {os.getpid()}) options={OPTIONS}")
    threading.Thread(target=load_model, args=(ARGS.model, OPTIONS), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=ARGS.port, log_level="warning")


ARGS = None
OPTIONS: dict = {}

if __name__ == "__main__":
    sys.exit(main())
