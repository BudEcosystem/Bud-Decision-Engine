"""Which accelerator trains, with what precision and how much memory, and a watchdog that keeps a job from pushing a
shared-memory machine into swap. Imported only inside the training job process (it imports PyTorch).

Policy (docs/trainer/ARCHITECTURE.md section 8, training_research/trainer_design/device_research/FINDINGS.md):
- the trainer never runs on the CPU (product requirement) or an NPU;
- precision comes from hardware capability, never from `is_bf16_supported()`, which reports emulated bf16 as
  supported (Turing, every ROCm and every Intel GPU);
- usable memory on unified-memory machines comes from the operating system, because `torch.cuda.mem_get_info()`
  reports only MemFree on the GB10 (one reading: 38 GiB "free" with 65 GiB available).
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil

GB = 1e9


@dataclass
class DeviceProfile:
    kind: str                     # "cuda" | "xpu" | "mps"
    backend: str                  # what the installer set up: "cuda" | "rocm" | "xpu" | "mps"
    name: str
    unified: bool
    tier: str                     # "on" | "experimental" | "off"
    reason: str
    autocast: str | None          # "bfloat16" | "float16" | None (fp32)
    base_dtype: str               # dtype for frozen base weights
    grad_scaler: bool
    optimizer: dict = field(default_factory=dict)    # extra AdamW kwargs (fused / foreach)
    fast_kernels: bool = False    # Triton kernels for Qwen3.5 linear attention (flash-linear-attention)
    max_single_alloc_gb: float | None = None
    capability: tuple | None = None

    @property
    def torch_device(self) -> str:
        return self.kind

    def public(self) -> dict:
        return {k: getattr(self, k) for k in ("kind", "backend", "name", "unified", "tier", "reason", "autocast",
                                              "base_dtype", "fast_kernels", "max_single_alloc_gb")}


def _installed_backend() -> str | None:
    from .. import config
    cfg = config.load()
    return cfg.get("backend") or cfg.get("device")


def profile(allow_cpu_for_tests: bool | None = None) -> DeviceProfile:
    """The device this job trains on. Raises RuntimeError(plain words) when training isn't possible here."""
    import torch
    allow_cpu = allow_cpu_for_tests if allow_cpu_for_tests is not None else os.environ.get("BASAL_TRAIN_ALLOW_CPU") == "1"
    backend = _installed_backend()
    osn = platform.system().lower()
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        name = props.name
        hip = bool(getattr(torch.version, "hip", None))
        cap = (props.major, props.minor)
        unified = bool(getattr(props, "is_integrated", False)) or "GB10" in name or _config_unified()
        if hip:
            tier, reason = ("experimental", "AMD GPUs train in experimental mode.") if osn == "linux" else \
                           ("off", "Training on AMD GPUs needs Linux.")
            ac = "bfloat16"
            return DeviceProfile("cuda", "rocm", name, unified, tier, reason, ac, "bfloat16", False,
                                 {"fused": True}, fast_kernels=False, capability=cap)
        if cap >= (8, 0):
            tier, reason, ac, sc = "on", "", "bfloat16", False
        elif cap >= (7, 5):
            tier, reason, ac, sc = "experimental", "This GPU is an older generation (Turing); training is experimental.", "float16", True
        else:
            tier, reason, ac, sc = "off", f"{name} is too old to train these models (needs an NVIDIA GPU from 2020 or later).", None, False
        fast = osn == "linux" and _importable("fla")
        return DeviceProfile("cuda", backend or "cuda", name, unified, tier, reason, ac,
                             "bfloat16" if ac == "bfloat16" else "float16" if ac else "float32", sc,
                             {"fused": True}, fast_kernels=fast, capability=cap)
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        props = torch.xpu.get_device_properties(0)
        name = props.name
        xmx = bool(getattr(props, "has_subgroup_matrix_multiply_accumulate", False))
        lower = name.lower()
        integrated = _config_unified() or not ("arc" in lower and any(x in lower for x in ("a3", "a5", "a7", "b5", "b6", "b7", "pro")))
        if not xmx:
            tier, reason = "off", f"{name} has no matrix engines, so training would be far too slow."
        else:
            tier, reason = "experimental", "Intel GPUs train in experimental mode."
        return DeviceProfile("xpu", "xpu", name, integrated, tier, reason, "bfloat16" if xmx else None,
                             "bfloat16" if xmx else "float32", False, {"foreach": False},
                             fast_kernels=False, max_single_alloc_gb=4.0)
    if torch.backends.mps.is_available():
        chip = _apple_chip()
        mac_ok = _macos_version() >= (14, 0)
        m1 = "M1" in chip
        total = psutil.virtual_memory().total / GB
        if not mac_ok:
            return DeviceProfile("mps", "mps", chip, True, "off", "Training on a Mac needs macOS 14 or newer.", None,
                                 "float32", False, {"fused": False, "foreach": False})
        tier = "experimental" if (m1 or total < 12) else "on"
        reason = "Training on this Mac is experimental (M1, or less than 16 GB of memory)." if tier == "experimental" else ""
        ac = None if m1 else "bfloat16"
        return DeviceProfile("mps", "mps", chip, True, tier, reason, ac, "float32" if m1 else "bfloat16", False,
                             {"fused": False, "foreach": False}, fast_kernels=False)
    if allow_cpu:
        return DeviceProfile("cpu", "cpu", platform.processor() or "CPU", False, "on", "tests only", None, "float32",
                             False, {"foreach": False})
    raise RuntimeError("Training needs a GPU. This computer runs models on the processor.")


def _config_unified() -> bool:
    from .. import config
    return any(a.get("unified_memory") for a in (config.load().get("accelerators") or []) if a.get("id") != "cpu")


def _importable(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def _apple_chip() -> str:
    try:
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=3).stdout
        return out.strip() or "Apple GPU"
    except Exception:  # noqa: BLE001
        return "Apple GPU"


def _macos_version() -> tuple:
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except Exception:  # noqa: BLE001
        return (0, 0)


# ----------------------------------------------------------------------------------------------------------------
# Memory


def available_bytes(dev: DeviceProfile, reserve_gb: float | None = None) -> int:
    """Memory this job may still use, per the platform rules above."""
    import torch
    reserve = (reserve_gb if reserve_gb is not None else (6.0 if dev.unified else 2.0)) * GB
    avail = psutil.virtual_memory().available - reserve
    if dev.kind == "cuda":
        if dev.unified and dev.backend != "rocm":
            return int(max(0, avail))            # GB10: mem_get_info reports only MemFree (far too low)
        try:
            free, _ = torch.cuda.mem_get_info()
        except Exception:  # noqa: BLE001 - the GPU is busy or out of memory right now
            return int(max(0, avail if dev.unified else 0))
        if dev.unified:
            return int(max(0, min(avail, free)))
        return int(max(0, free - 1 * GB))
    if dev.kind == "mps":
        rec = torch.mps.recommended_max_memory()
        return int(max(0, min(rec - torch.mps.driver_allocated_memory(), avail)))
    if dev.kind == "xpu":
        try:
            free, total = torch.xpu.mem_get_info()
        except RuntimeError:
            free = total = torch.xpu.get_device_properties(0).total_memory
        if dev.unified:
            cap = 0.5 * psutil.virtual_memory().total if sys.platform == "linux" else total
            return int(max(0, min(free, cap, avail)))
        return int(max(0, free - 1 * GB))
    return int(max(0, avail))


def reclaim(dev: DeviceProfile, need_gb: float) -> float:
    """On unified-memory Linux machines (the GB10), turn file-cache pages into free memory before a large GPU
    allocation. The GPU driver does not reclaim cache quickly enough, so allocations fail with "out of memory" while
    plenty is "available" (NVIDIA's DGX Spark known issues; its fix, dropping caches, needs root). Writing to a
    temporary block of ordinary memory makes the kernel reclaim cache; freeing it leaves that memory free. Takes at
    most the reclaimable cache (available minus free) less a margin, and stops as soon as the kernel starts moving
    other programs' memory to swap instead: on a busy machine that would slow everything down. Returns GB made free."""
    if not (dev.unified and sys.platform == "linux"):
        return 0.0
    vm = psutil.virtual_memory()
    free = vm.free
    want = min(need_gb * GB - free, (vm.available - free) - 2 * GB, vm.available - 6 * GB)
    if want <= 1 * GB:
        return 0.0
    import numpy as np
    swap0 = psutil.swap_memory().used
    blocks, got = [], 0
    try:
        while got < want:
            n = int(min(512 * 1024 ** 2, want - got))
            blocks.append(np.ones(n, dtype=np.uint8))       # written, so the pages are really committed
            got += n
            if psutil.virtual_memory().available < 4 * GB or psutil.swap_memory().used - swap0 > 256 * 1024 ** 2:
                break
    finally:
        del blocks
    import gc
    gc.collect()
    return (psutil.virtual_memory().free - free) / GB


def retry_device(fn, what: str, dev: DeviceProfile | None = None, need_gb: float = 4.0, tries: int = 4,
                 wait: float = 10.0, on_retry=None):
    """Run `fn`; if the GPU reports out-of-memory while the machine has memory available (on the GB10 this is file
    cache the driver won't reclaim), free some cache and try again, asking for more each time."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            low = str(e).lower()
            if "out of memory" not in low or i == tries - 1:
                raise
            if on_retry:
                on_retry(f"Making room in memory while {what} (attempt {i + 2} of {tries}).")
            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache() if torch.cuda.is_available() else None
            except Exception:  # noqa: BLE001
                pass
            if dev is not None:
                reclaim(dev, need_gb * (1.5 ** (i + 1)))
            time.sleep(wait)


def prepare(dev: DeviceProfile) -> None:
    """Process-wide settings before any model is loaded."""
    import torch
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        os.nice(5)                                  # keep the desktop responsive while training
    except (AttributeError, OSError):
        pass
    torch.set_num_threads(max(2, min(8, (os.cpu_count() or 4) - 2)))
    if dev.kind == "mps":
        torch.mps.set_per_process_memory_fraction(1.0)     # raise out-of-memory instead of swapping the Mac
    if dev.kind == "xpu":
        try:
            torch.xpu.set_per_process_memory_fraction(1.0)  # catchable out-of-memory on Intel GPUs
        except Exception:  # noqa: BLE001
            pass
    if not dev.fast_kernels:
        sys.modules.setdefault("fla", None)                 # an importable but unusable fla crashes transformers
    from ..paths import DATA
    os.environ.setdefault("TRITON_CACHE_DIR", str(DATA / "cache" / "triton"))


def limit_memory(dev: DeviceProfile, cap_gb: float) -> float | None:
    """Caps this process's GPU memory. Past the cap, PyTorch raises an ordinary out-of-memory error, which the engine
    answers by halving its batches; without it, on a shared-memory machine (the GB10) a model whose real need was far
    above its estimate (GLiNER on long texts reached 66 GB against 12 estimated) kept taking memory until every
    other program on the computer stalled. Returns the cap applied, in GB."""
    import torch
    try:
        if dev.kind == "cuda":
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(max(0.01, min(1.0, cap_gb * 1e9 / total)))
            return cap_gb
        if dev.kind == "xpu":
            total = torch.xpu.get_device_properties(0).total_memory
            torch.xpu.set_per_process_memory_fraction(max(0.01, min(1.0, cap_gb * 1e9 / total)))
            return cap_gb
    except Exception:  # noqa: BLE001
        pass
    return None


def self_test(dev: DeviceProfile) -> None:
    """A tiny forward, backward and AdamW step in the chosen precision, checked against the CPU. Catches silent
    failures (wrong attention results, broken mixed-precision gradients, device hangs) before hours are spent."""
    import torch
    torch.manual_seed(0)
    layer = torch.nn.TransformerEncoderLayer(64, 4, 128, dropout=0.0, batch_first=True)
    x, w = torch.randn(2, 16, 64), torch.randn(2, 16, 64)
    ref = layer(x)
    (ref * w).sum().backward()
    g_ref = torch.cat([p.grad.flatten() for p in layer.parameters()])
    dev_layer = torch.nn.TransformerEncoderLayer(64, 4, 128, dropout=0.0, batch_first=True)
    dev_layer.load_state_dict(layer.state_dict())
    dev_layer.to(dev.kind)
    dtype = getattr(torch, dev.autocast) if dev.autocast else None
    with torch.autocast(device_type=dev.kind, dtype=dtype, enabled=dtype is not None):
        out = dev_layer(x.to(dev.kind)).float()
    (out * w.to(dev.kind)).sum().backward()
    g = torch.cat([p.grad.flatten().float().cpu() for p in dev_layer.parameters()])
    opt = torch.optim.AdamW(dev_layer.parameters(), lr=1e-3, **dev.optimizer)
    opt.step()
    if dev.kind != "cpu":
        getattr(torch, dev.kind).synchronize()
    out, ref = out.detach().cpu(), ref.detach()
    rel = float((out - ref).norm() / (ref.norm() + 1e-6))
    grel = float((g - g_ref).norm() / (g_ref.norm() + 1e-6))
    tol = 0.05 if dtype is not None else 1e-3
    finite = bool(torch.isfinite(out).all()) and all(bool(torch.isfinite(p).all()) for p in dev_layer.parameters())
    if not (finite and rel < tol and grel < 0.1):
        raise RuntimeError(f"The {dev.name} gave wrong results in a quick training self-test (output error {rel:.3f}, "
                           f"gradient error {grel:.3f}). Training was not started. Updating the graphics driver or "
                           "running setup again usually fixes this.")


# ----------------------------------------------------------------------------------------------------------------
# Watchdog


class MemoryWatchdog:
    """Sets `tripped` when available memory stays below `floor_gb` for `seconds`; the engine checkpoints and stops.
    On NVIDIA it also watches the GPU temperature and trips above `max_temp_c`."""

    def __init__(self, dev: DeviceProfile, floor_gb: float = 4.0, seconds: float = 6.0, max_temp_c: float = 92.0):
        self.dev, self.floor, self.seconds, self.max_temp = dev, floor_gb * GB, seconds, max_temp_c
        self.tripped: str | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def healthy(self) -> bool:
        """Memory comfortably above the floor and the GPU cool enough to continue."""
        if psutil.virtual_memory().available < self.floor + 2 * GB:
            return False
        t = _gpu_temp() if self.dev.kind == "cuda" and self.dev.backend != "rocm" else None
        return t is None or t < self.max_temp - 10

    def reset(self) -> None:
        self.tripped = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def __exit__(self, *a):
        self._stop.set()

    def _run(self):
        low_since = None
        while not self._stop.wait(2.0):
            avail = psutil.virtual_memory().available
            if avail < self.floor:
                low_since = low_since or time.time()
                if time.time() - low_since >= self.seconds:
                    self.tripped = (f"Other programs left only {avail / GB:.1f} GB of memory free, so training paused "
                                    "to keep the computer responsive. Close other programs (or eject models) and resume.")
                    return
            else:
                low_since = None
            # the temperature costs a process launch from a process holding a large GPU context: every 30 s is enough
            t = None
            if self.dev.kind == "cuda" and self.dev.backend != "rocm" and time.time() - getattr(self, "_temp_at", 0) > 30:
                self._temp_at = time.time()
                t = _gpu_temp()
            if t is not None and t >= self.max_temp:
                self.tripped = f"The GPU reached {t:.0f} °C, so training paused to let it cool down. Resume in a few minutes."
                return


def _gpu_temp() -> float | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
        return float(out[0]) if out else None
    except Exception:  # noqa: BLE001
        return None
