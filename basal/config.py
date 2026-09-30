"""Where models run. The installer (installer/engine.py) records the device the user chose in data/config.json;
the runtime uses it as the default for every model and offers the other devices this computer has.

Runtime device names follow PyTorch: "cuda" (NVIDIA, and AMD through ROCm), "mps" (Apple Silicon), "xpu" (Intel GPUs),
"cpu". Without a config file (a source checkout set up before the installer existed) the device is inferred from
nvidia-smi and the platform, without importing PyTorch in the server process.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from functools import lru_cache

from .paths import DATA, NO_WINDOW

CONFIG_FILE = DATA / "config.json"
LABEL = {"cuda": "GPU", "mps": "Apple GPU", "xpu": "Intel GPU", "cpu": "CPU"}


def load() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save(patch: dict) -> dict:
    cfg = {**load(), **patch}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    available.cache_clear()
    return cfg


@lru_cache(maxsize=1)
def available() -> list[dict]:
    """Devices this computer offers, best first: [{"id": "cuda", "name": "NVIDIA GB10", "memory_gb": ...}, ...]."""
    cfg = load()
    out = []
    # Only the device PyTorch was installed for (plus the CPU, which every build supports) can run models.
    chosen = cfg.get("backend") or cfg.get("device")
    for a in cfg.get("accelerators") or []:
        runtime = {"cuda": "cuda", "rocm": "cuda", "mps": "mps", "xpu": "xpu", "cpu": "cpu"}.get(a.get("id"))
        if a.get("id") not in (chosen, "cpu"):
            continue
        if runtime and runtime not in [o["id"] for o in out]:
            out.append({"id": runtime, "name": a.get("name") or LABEL[runtime], "memory_gb": a.get("memory_gb"),
                        "unified_memory": a.get("unified_memory", False)})
    if not out:
        if shutil.which("nvidia-smi"):
            try:
                name = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                                      text=True, timeout=5, **NO_WINDOW).stdout.strip().splitlines()[0]
                out.append({"id": "cuda", "name": name, "memory_gb": None, "unified_memory": "GB10" in name})
            except (OSError, IndexError, subprocess.TimeoutExpired):
                pass
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            out.append({"id": "mps", "name": "Apple GPU", "memory_gb": None, "unified_memory": True})
        out.append({"id": "cpu", "name": platform.processor() or "CPU", "memory_gb": None, "unified_memory": False})
    if "cpu" not in [o["id"] for o in out]:
        out.append({"id": "cpu", "name": "CPU", "memory_gb": None, "unified_memory": False})
    return out


def default_device() -> str:
    cfg = load()
    ids = [d["id"] for d in available()]
    if cfg.get("device") in ids:
        return cfg["device"]
    return ids[0]


def device_name(dev: str | None = None) -> str:
    dev = dev or default_device()
    return next((d["name"] for d in available() if d["id"] == dev), LABEL.get(dev, dev))


def fit(memory_gb: float, needs_gpu: bool) -> dict:
    """Whether a model that needs `memory_gb` once loaded can run on this computer's default device."""
    devs = available()
    gpu = next((d for d in devs if d["id"] != "cpu"), None)
    if needs_gpu and not gpu:
        return {"ok": False, "reason": "Needs a GPU. This computer runs models on the CPU."}
    dev = gpu if needs_gpu else next(d for d in devs if d["id"] == default_device())
    cap = dev.get("memory_gb")
    # Leave room for the operating system; Apple Silicon lets the GPU use about three quarters of memory.
    usable = cap * (0.72 if dev["id"] == "mps" else 0.85) if cap else None
    if usable and memory_gb > usable:
        return {"ok": False, "reason": f"Needs about {memory_gb:g} GB of memory; {dev['name']} has {cap:g} GB."}
    return {"ok": True, "reason": ""}


def summary() -> dict:
    cfg = load()
    dev = default_device()
    return {"device": dev, "device_name": device_name(dev), "available": available(), "configured": bool(cfg),
            "torch": cfg.get("torch"), "installed": cfg.get("installed"), "os": cfg.get("os") or platform.system().lower(),
            "arch": cfg.get("arch") or platform.machine()}


def device_option() -> dict:
    """The "Run on" load option, built from this computer's devices."""
    choices = []
    for d in available():
        label = f"{LABEL[d['id']]} ({d['name']})" if d["id"] != "cpu" else "CPU (slower)"
        choices.append([d["id"], label])
    return {"key": "device", "label": "Run on", "type": "select", "default": default_device(), "choices": choices,
            "help": "Where the model's maths runs. A GPU is much faster; the CPU works everywhere, best for small models."}
