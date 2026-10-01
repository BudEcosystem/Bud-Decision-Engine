"""Starts, watches and stops the per-model worker processes (see basal.worker)."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from .catalog import BY_ID
from .paths import DETACHED, LOGS, ROOT


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Handle:
    model_id: str
    port: int
    proc: subprocess.Popen
    options: dict
    started: float = field(default_factory=time.time)
    health: dict = field(default_factory=lambda: {"status": "starting", "stage": "Starting the worker process"})
    exited: int | None = None
    error: str | None = None
    detail: str | None = None
    ejecting: bool = False
    was_ready: bool = False          # reached "ready" at least once (a later death is a crash, not a load failure)
    auto: bool = False               # a request started this load (auto-load), not the user pressing Load
    failed_at: float | None = None
    last_used: float = 0.0
    log: object = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def status(self) -> str:
        if self.error: return "error"
        if self.ejecting: return "ejecting"
        return self.health.get("status", "starting")

    def public(self) -> dict:
        h = self.health
        return {"model_id": self.model_id, "status": self.status, "stage": h.get("stage"), "progress": h.get("progress"),
                "error": self.error or h.get("error"), "detail": self.detail or h.get("detail"), "warning": h.get("warning"),
                "pid": self.proc.pid,
                "port": self.port, "options": self.options, "started": self.started,
                "load_seconds": h.get("load_seconds"), "requests": h.get("requests", 0), "memory": h.get("memory", {}),
                "last_used": self.last_used or None,
                "elapsed": round(time.time() - self.started, 1)}


class Workers:
    def __init__(self):
        self.handles: dict[str, Handle] = {}
        self.lock = threading.Lock()   # load requests arrive from threadpool and event loop alike
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0))
        self.load_order: deque[str] = deque()

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def start(self, model_id: str, options: dict) -> Handle:
        with self.lock:
            return self._start(model_id, options)

    def _start(self, model_id: str, options: dict) -> Handle:
        h = self.handles.get(model_id)
        if h and h.status not in ("error", "ejecting"):
            return h
        if h:
            self._reap(h)
        spec = BY_ID[model_id]
        opts = {o["key"]: o["default"] for o in spec.options()}
        opts.update({k: v for k, v in (options or {}).items() if v is not None})
        port = free_port()
        log = open(LOGS / f"worker-{model_id}.log", "w")
        # MPS fallback: an operation Apple's Metal backend lacks runs on the CPU instead of failing the load.
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "basal.worker", "--model", model_id, "--port", str(port),
                                 "--options", json.dumps(opts)],
                                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT, **DETACHED)
        h = Handle(model_id, port, proc, opts, log=log)
        self.handles[model_id] = h
        if model_id in self.load_order: self.load_order.remove(model_id)
        self.load_order.append(model_id)
        return h

    async def stop(self, model_id: str) -> bool:
        h = self.handles.get(model_id)
        if not h:
            return False
        if h.ejecting:
            return True
        h.ejecting = True
        asked = False
        try:
            await self.client.post(f"{h.url}/shutdown", timeout=2.0)
            asked = True
        except Exception:
            pass             # not listening yet (it has only just started) or hung: there is nothing to wait for
        for _ in range(50 if asked else 0):
            if h.proc.poll() is not None: break
            await asyncio.sleep(0.1)
        await asyncio.to_thread(self._reap, h)
        if self.handles.get(model_id) is h:
            self.handles.pop(model_id, None)
        if model_id in self.load_order and model_id not in self.handles: self.load_order.remove(model_id)
        return True

    def _reap(self, h: Handle):
        if h.proc.poll() is None:
            # The worker's whole process group on Linux and macOS (it may have helper processes); the process on Windows.
            kill = (lambda sig: os.killpg(h.proc.pid, sig)) if hasattr(os, "killpg") else (lambda sig: h.proc.terminate() if sig == 15 else h.proc.kill())
            try:
                kill(15)
                h.proc.wait(timeout=5)
            except Exception:
                try: kill(9)
                except Exception: pass
        try:
            h.log and h.log.close()
        except Exception:
            pass

    async def stop_all(self):
        for mid in list(self.handles):
            await self.stop(mid)

    def kill_all_sync(self):
        for h in list(self.handles.values()):
            self._reap(h)

    # -- monitoring ---------------------------------------------------------------------------------------------------
    async def poll(self):
        """Refresh every worker's health; notice crashes and turn them into readable errors."""
        for h in list(self.handles.values()):
            code = h.proc.poll()
            if code is not None and not h.ejecting and not h.error:
                h.exited, h.failed_at = code, time.time()
                tail = log_tail(h.model_id, 40)
                h.error = h.health.get("error") or explain_exit(code, tail)
                h.detail = h.health.get("detail") or tail
                continue
            if code is not None:
                continue
            try:
                r = await self.client.get(f"{h.url}/health", timeout=2.0)
                h.health = r.json()
                if h.health.get("status") == "ready":
                    h.was_ready = True
                if h.health.get("status") == "error" and not h.error:
                    h.error, h.detail, h.failed_at = h.health.get("error"), h.health.get("detail"), time.time()
                    await asyncio.to_thread(self._reap, h)   # a failed load keeps whatever it allocated; free it now
            except Exception:
                pass   # still booting: uvicorn not listening yet

    # -- routing ------------------------------------------------------------------------------------------------------
    def ready(self) -> list[str]:
        return [m for m in self.load_order if m in self.handles and self.handles[m].status == "ready"]

    async def decide(self, model_id: str, body: dict) -> tuple[int, dict]:
        h = self.handles.get(model_id)
        if h is None or h.ejecting:
            return 503, {"detail": f"{BY_ID[model_id].name} was ejected while this request was waiting. Load it again and retry."}
        try:
            r = await self.client.post(f"{h.url}/decide", json=body)
        except httpx.TimeoutException:
            return 504, {"detail": f"{BY_ID[model_id].name} took longer than 10 minutes to answer. Try a shorter input."}
        except httpx.HTTPError:
            code = h.proc.poll()
            if code is not None:
                return 503, {"detail": explain_exit(code, log_tail(model_id, 40))}
            return 503, {"detail": f"Lost contact with {BY_ID[model_id].name}'s process. Eject and load it again."}
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"detail": r.text}


def log_tail(model_id: str, n: int = 40) -> str:
    p = LOGS / f"worker-{model_id}.log"
    if not p.exists(): return ""
    return "\n".join(p.read_text(errors="replace").splitlines()[-n:])


def explain_exit(code: int, tail: str) -> str:
    low = tail.lower()
    if code in (-9, 137) or "killed" in low:
        return "The model process was killed, most likely because the system ran out of memory. Eject other models and try again."
    if "out of memory" in low:
        return "The GPU ran out of memory while loading. Eject other models and try again."
    return f"The model process stopped unexpectedly (exit code {code}). Open 'Details' to see its log."
