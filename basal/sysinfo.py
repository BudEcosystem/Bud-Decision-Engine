"""Machine facts for the System page: GPU, memory, disk.

On the GB10 (DGX Spark class) the CPU and GPU share one pool of LPDDR5X memory,
so "GPU memory" and "system memory" are the same thing. nvidia-smi can't report a
GPU memory total on this chip, so the total comes from the OS and per-process GPU
use from nvidia-smi's process list.
"""
from __future__ import annotations

import shutil
from pathlib import Path
import subprocess
import time

import psutil

from .paths import NO_WINDOW

_static: dict | None = None
_cache: tuple[float, dict] | None = None


def _smi(args: list[str]) -> list[list[str]]:
    try:
        out = subprocess.run(["nvidia-smi", *args, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, **NO_WINDOW)
        return [[c.strip() for c in line.split(",")] for line in out.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []


def _num(x: str):
    try: return float(x)
    except Exception: return None


def static_info() -> dict:
    global _static
    if _static is None:
        from .config import default_device, device_name, available
        rows = _smi(["--query-gpu=name,driver_version,compute_cap"])
        dev = default_device()
        name, driver, cc = (rows[0] + [None, None, None])[:3] if rows else (device_name(dev), None, None)
        if dev == "cpu":
            name = device_name("cpu")
        cuda = None
        try:
            out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5, **NO_WINDOW).stdout
            if "CUDA Version:" in out:
                cuda = out.split("CUDA Version:")[1].split()[0]
        except Exception:
            pass
        _static = {"gpu_name": name, "driver": driver, "compute_capability": cc, "cuda": cuda,
                   "unified_memory": any(d["id"] == dev and d.get("unified_memory") for d in available()) or bool(name and "GB10" in name),
                   "device": dev, "cpu_count": psutil.cpu_count()}
    return _static


def live(hf_cache: str | None = None) -> dict:
    """Cached for 1 s so polling UIs don't hammer nvidia-smi."""
    global _cache
    if _cache and time.time() - _cache[0] < 1.0:
        return _cache[1]
    g = _smi(["--query-gpu=utilization.gpu,temperature.gpu,power.draw"])
    util, temp, power = ([_num(x) for x in g[0]] + [None] * 3)[:3] if g else (None, None, None)
    procs = {}
    for row in _smi(["--query-compute-apps=pid,used_memory"]):
        if len(row) >= 2 and row[0].isdigit():
            procs[int(row[0])] = (_num(row[1]) or 0) / 1024  # MiB -> GiB
    vm = psutil.virtual_memory()
    # On a fresh install the model cache folder does not exist yet; measure the nearest folder that does.
    probe = Path(hf_cache or "/")
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    disk = shutil.disk_usage(probe)
    data = {**static_info(), "gpu_util": util, "gpu_temp": temp, "gpu_power": power,
            "mem_total_gb": round(vm.total / 1e9, 1), "mem_available_gb": round(vm.available / 1e9, 1),
            "mem_used_gb": round((vm.total - vm.available) / 1e9, 1),
            "disk_free_gb": round(disk.free / 1e9, 1), "disk_total_gb": round(disk.total / 1e9, 1),
            "gpu_processes": procs, "cpu_percent": psutil.cpu_percent(interval=None)}
    _cache = (time.time(), data)
    return data


def process_tree_rss_gb(pid: int) -> float:
    try:
        p = psutil.Process(pid)
        total = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(recursive=True))
        return round(total / 1e9, 2)
    except Exception:
        return 0.0
