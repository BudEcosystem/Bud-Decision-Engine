#!/usr/bin/env python3
"""Bud Decision Studio engine installer: detects the hardware, then installs a Python environment with the right
PyTorch build and every model library for it. Standard library only, so it runs before anything is installed:

    uv run --no-project --python 3.12 installer/engine.py detect --json
    uv run --no-project --python 3.12 installer/engine.py install --device cuda --venv ~/.bud/venv --data ~/.bud/data
    uv run --no-project --python 3.12 installer/engine.py setup          # interactive, for terminal users

`install` prints one JSON object per line (type: step | log | done | error) so the desktop app can show progress.

Supported targets
    NVIDIA GPU   Linux x86_64 and ARM64 (GB10, Grace), Windows x86_64   PyTorch CUDA wheels (cu126/cu128/cu130 by driver)
    Apple GPU    macOS on Apple Silicon                                PyTorch with Metal (MPS)
    Intel GPU    Core Ultra / Arc on Linux or Windows x86_64           PyTorch XPU wheels
    AMD GPU      Linux x86_64 with ROCm (experimental)                 PyTorch ROCm wheels
    CPU          everything above, plus any other x86_64 / ARM64 CPU   PyTorch CPU wheels
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The desktop app starts this script without a console on Windows; its own children must not open one either.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
PROJECT = HERE.parent                      # holds basal/, ui/, requirements*.txt
TORCH, TORCHVISION = "2.11.0", "0.26.0"
PY = "3.12"


# ------------------------------------------------------------------------------------------------------------------
# small helpers

def run(cmd: list[str], timeout: float = 20, stdout_only: bool = False) -> tuple[int, str]:
    """Run a command and return (exit code, output). Output is stdout plus stderr, or only stdout when a caller
    parses it: libraries such as PyTorch print warnings to stderr, and those must never be read as data."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **NO_WINDOW)
        return p.returncode, (p.stdout or "") + ("" if stdout_only else (p.stderr or ""))
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def torch_version(py: Path) -> str:
    """The installed PyTorch version (e.g. 2.11.0+cu130), or "" when PyTorch is missing."""
    if not py.exists():
        return ""
    code, out = run([str(py), "-c", "import torch; print(torch.__version__)"], timeout=180, stdout_only=True)
    found = [l.strip() for l in out.splitlines() if re.fullmatch(r"\d+\.\d+(\.\d+)?[\w.+-]*", l.strip())]
    return found[-1] if code == 0 and found else ""


def os_name() -> str:
    s = platform.system().lower()
    return {"darwin": "macos", "windows": "windows"}.get(s, "linux" if s == "linux" else s)


def arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64" if os_name() == "macos" else "aarch64"}.get(m, m)


def ram_gb() -> float | None:
    try:
        if os_name() == "linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
        if os_name() == "macos":
            code, out = run(["sysctl", "-n", "hw.memsize"])
            if code == 0:
                return round(int(out.strip()) / 1024 ** 3, 1)
        if os_name() == "windows":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            return round(m.ullTotalPhys / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def cpu_name() -> str:
    try:
        if os_name() == "macos":
            code, out = run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if code == 0 and out.strip():
                return out.strip()
        if os_name() == "linux":
            code, out = run(["lscpu"])
            names = re.findall(r"^Model name:\s*(.+)$", out, re.M) if code == 0 else []
            if names:
                return " + ".join(dict.fromkeys(n.strip() for n in names))
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        if os_name() == "windows":
            code, out = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
            if code == 0 and out.strip():
                return out.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        pass
    return platform.processor() or platform.machine()


def disk_free_gb(path: Path) -> float | None:
    try:
        p = path
        while not p.exists():
            p = p.parent
        return round(shutil.disk_usage(p).free / 1024 ** 3, 1)
    except OSError:
        return None


def windows_gpus() -> list[str]:
    code, out = run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
    return [l.strip() for l in out.splitlines() if l.strip()] if code == 0 else []


def linux_pci_gpus() -> list[tuple[str, str]]:
    """(vendor id, name) of display controllers, from lspci if present, else sysfs vendor ids."""
    out = []
    code, text = run(["lspci", "-nn"])
    if code == 0:
        for line in text.splitlines():
            if re.search(r"VGA|3D controller|Display controller", line):
                vid = re.search(r"\[(\w{4}):\w{4}\]", line)
                out.append(((vid.group(1).lower() if vid else ""), line.split(": ", 1)[-1]))
        return out
    for dev in Path("/sys/class/drm").glob("card[0-9]*/device"):
        try:
            out.append((dev.joinpath("vendor").read_text().strip().lower().replace("0x", ""), ""))
        except OSError:
            pass
    return out


# ------------------------------------------------------------------------------------------------------------------
# detection

def detect_nvidia() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    code, out = run([exe, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    if code != 0 or not out.strip():
        return None
    first = [x.strip() for x in out.strip().splitlines()[0].split(",")]
    name = first[0]
    mem = None
    try:
        mem = round(float(first[1]) / 1024, 1)
    except (ValueError, IndexError):
        pass
    _, head = run([exe])
    cuda = re.search(r"CUDA Version:\s*([\d.]+)", head)
    unified = mem is None     # GB10 and Grace share system memory and report memory.total as [N/A]
    return {"id": "cuda", "backend": "cuda", "kind": "nvidia", "name": name, "memory_gb": ram_gb() if unified else mem,
            "unified_memory": unified, "driver": first[2] if len(first) > 2 else None,
            "cuda": cuda.group(1) if cuda else None, "compute_capability": first[3] if len(first) > 3 else None}


def detect_apple() -> dict | None:
    if os_name() != "macos" or arch() != "arm64":
        return None
    chip = cpu_name()
    return {"id": "mps", "backend": "mps", "kind": "apple", "name": f"{chip} GPU", "memory_gb": ram_gb(), "unified_memory": True}


def detect_intel() -> dict | None:
    if arch() != "x86_64" or os_name() not in ("linux", "windows"):
        return None
    names = windows_gpus() if os_name() == "windows" else [n for v, n in linux_pci_gpus() if v == "8086" or "Intel" in n]
    cpu = cpu_name()
    arc = [n for n in names if re.search(r"\bArc\b", n)]
    ultra = re.search(r"Core\(TM\) Ultra|Core Ultra", cpu)
    if not (arc or (ultra and names)):
        return None   # older Intel graphics (UHD, Iris Xe) are not supported by PyTorch XPU
    name = arc[0] if arc else (names[0] if names else "Intel graphics")
    return {"id": "xpu", "backend": "xpu", "kind": "intel", "name": re.sub(r"^.*?:\s*", "", name).strip(), "memory_gb": None,
            "unified_memory": not bool(re.search(r"Arc.*A\d{3}|Arc.*B\d{3}", name))}


def detect_amd() -> dict | None:
    if os_name() != "linux" or arch() != "x86_64":
        return None
    if not (shutil.which("rocminfo") or Path("/opt/rocm").exists()):
        return None
    gpus = [n for v, n in linux_pci_gpus() if v == "1002"]
    if not gpus:
        return None
    return {"id": "rocm", "backend": "rocm", "kind": "amd", "name": gpus[0], "memory_gb": None, "unified_memory": False, "experimental": True}


def detect() -> dict:
    accs = [a for a in (detect_nvidia(), detect_apple(), detect_intel(), detect_amd()) if a]
    cpu = {"id": "cpu", "backend": "cpu", "kind": "cpu", "name": cpu_name(), "memory_gb": ram_gb(), "cores": os.cpu_count()}
    notes = []
    if os_name() == "macos" and arch() == "x86_64":
        notes.append("Intel Macs are not supported by current PyTorch releases; the CPU option may fail to install.")
    if os_name() == "windows" and arch() == "aarch64":
        notes.append("Windows on ARM runs on the CPU only.")
    for a in accs:
        if a["kind"] == "nvidia" and a.get("cuda") and _ver(a["cuda"]) < (12, 6):
            notes.append(f"Your NVIDIA driver supports CUDA {a['cuda']}; update the driver to 560 or newer for the best results.")
    # The first accelerator with enough memory is recommended; the CPU works everywhere but is slow for large models.
    rec = next((a["id"] for a in accs if not a.get("experimental")), "cpu")
    return {"os": os_name(), "arch": arch(), "os_version": platform.platform(terse=True), "memory_gb": ram_gb(),
            "disk_free_gb": disk_free_gb(Path.home()), "accelerators": accs + [cpu], "recommended": rec, "notes": notes}


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:2]) or (0,)


# ------------------------------------------------------------------------------------------------------------------
# install plan: which PyTorch build and which extra libraries

def plan(det: dict, device: str) -> dict:
    acc = next((a for a in det["accelerators"] if a["id"] == device), None)
    if not acc:
        raise SystemExit(f"'{device}' is not available on this computer. Choose one of: {', '.join(a['id'] for a in det['accelerators'])}")
    osn, ar = det["os"], det["arch"]
    index, label, size = None, "", 1.0
    if device == "cuda":
        if osn == "macos":
            raise SystemExit("NVIDIA GPUs are not supported on macOS.")
        v = _ver(acc.get("cuda") or "12.6")
        tag = "cu130" if v >= (13, 0) else "cu128" if v >= (12, 8) else "cu126"
        index, label, size = f"https://download.pytorch.org/whl/{tag}", f"PyTorch {TORCH} for CUDA {tag[2:4]}.{tag[4:]}", 3.2
    elif device == "rocm":
        index, label, size = "https://download.pytorch.org/whl/rocm6.4", f"PyTorch {TORCH} for AMD ROCm", 3.5
    elif device == "xpu":
        index, label, size = "https://download.pytorch.org/whl/xpu", f"PyTorch {TORCH} for Intel GPUs (XPU)", 1.8
    elif device == "mps":
        index, label, size = None, f"PyTorch {TORCH} with Apple Metal (MPS)", 0.4
    else:
        index = None if osn == "macos" else "https://download.pytorch.org/whl/cpu"
        label, size = f"PyTorch {TORCH} for the CPU", 0.3
    extra = []
    if device == "cuda" and osn == "linux":
        extra.append("requirements-cuda.txt")    # Triton kernels: ~20% faster Qwen3.5-based models on NVIDIA GPUs
    runtime = {"cuda": "cuda", "rocm": "cuda", "xpu": "xpu", "mps": "mps", "cpu": "cpu"}[device]
    return {"device": runtime, "backend": device, "device_name": acc["name"], "memory_gb": acc.get("memory_gb"),
            "unified_memory": acc.get("unified_memory", False), "torch_index": index, "torch_label": label,
            "download_gb": round(size + 1.2, 1), "extra_requirements": extra}


# ------------------------------------------------------------------------------------------------------------------
# install

HUMAN = False   # `setup` prints readable progress; `install` prints JSON lines for the desktop app


def emit(**kw) -> None:
    if not HUMAN:
        print(json.dumps(kw), flush=True)
    elif kw["type"] == "step":
        print(f"\n[{kw['index']}/{kw['total']}] {kw['label']}", flush=True)
    elif kw["type"] == "log":
        print("    " + kw["line"][:160], flush=True)
    elif kw["type"] == "error":
        print(f"\nSetup failed: {kw['message']}", flush=True)
    elif kw["type"] == "done":
        c = kw["config"]
        print(f"\nDone in {kw['seconds']} s. Models will run on {c['device_name']} with PyTorch {c['torch']}.", flush=True)
        print("Start the studio with ./run.sh, then open http://127.0.0.1:8420", flush=True)


def stream(cmd: list[str], env: dict | None = None) -> int:
    """Run a command, relaying its output as log events."""
    emit(type="log", line="$ " + (subprocess.list2cmdline(cmd) if os.name == "nt" else shlex.join(cmd)))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, bufsize=1, **NO_WINDOW)
    assert p.stdout
    for line in p.stdout:
        line = line.rstrip()
        if line:
            emit(type="log", line=line[-400:])
    return p.wait()


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os_name() == "windows" else "bin/python")


def find_uv(given: str | None) -> str:
    uv = given or os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise SystemExit("uv was not found. The desktop app bundles it; from a terminal, install it from https://docs.astral.sh/uv/.")
    return uv


def install(device: str, venv: Path, data: Path, uv: str) -> dict:
    det = detect()
    p = plan(det, device)
    steps = [("python", f"Preparing Python {PY}", 0.05), ("torch", f"Installing {p['torch_label']}", 0.55),
             ("libs", "Installing the model libraries", 0.28), ("models", "Installing the model packages", 0.07),
             ("verify", f"Checking {p['device_name']}", 0.05)]
    done = 0.0

    def step(i: int):
        nonlocal done
        sid, label, w = steps[i]
        emit(type="step", id=sid, label=label, index=i + 1, total=len(steps), progress=round(done, 3))
        return w

    env = {**os.environ, "UV_HTTP_TIMEOUT": "300", "VIRTUAL_ENV": str(venv)}
    py = venv_python(venv)
    uvpip = [uv, "pip", "install", "--python", str(py)]

    w = step(0)
    if not py.exists():
        if stream([uv, "venv", str(venv), "--python", PY, "--seed"], env) != 0:
            raise RuntimeError("Could not create the Python environment.")
    done += w

    w = step(1)
    pins = [f"torch=={TORCH}", f"torchvision=={TORCHVISION}"]
    idx = (["--index-url", p["torch_index"], "--extra-index-url", "https://pypi.org/simple", "--index-strategy", "unsafe-best-match"]
           if p["torch_index"] else [])
    # "torch==2.11.0" is satisfied by any build of 2.11.0 (+cpu, +cu130, ...), so when setup runs again for a different
    # device the installed build must be replaced explicitly.
    want = "+" + p["torch_index"].rstrip("/").rsplit("/", 1)[-1] if p["torch_index"] else ""
    have = torch_version(py)
    if have and (not have.endswith(want) if want else "+" in have):
        emit(type="log", line=f"Replacing PyTorch {have} with the build for {p['device_name']}.")
        pins += ["--reinstall-package", "torch", "--reinstall-package", "torchvision"]
    if stream(uvpip + pins + idx, env) != 0:
        emit(type="log", line=f"PyTorch {TORCH} is not available for this build; installing the newest compatible release instead.")
        if stream(uvpip + ["torch", "torchvision"] + idx, env) != 0:
            raise RuntimeError("PyTorch could not be installed. Check the internet connection and try again.")
    done += w

    w = step(2)
    reqs = ["-r", str(PROJECT / "requirements.txt")]
    for extra in p["extra_requirements"]:
        reqs += ["-r", str(PROJECT / extra)]
    # Keep the PyTorch build chosen above by naming it in the same install. (Not with a constraints file: uv splits
    # --constraint paths at spaces, and the app's data folder is "Application Support" on macOS.)
    keep = [f"torch=={v}" for v in [torch_version(py)] if v]
    if stream(uvpip + keep + reqs + idx, env) != 0:
        if p["extra_requirements"]:
            emit(type="log", line="The optional fast kernels could not be installed; continuing without them.")
            if stream(uvpip + keep + ["-r", str(PROJECT / "requirements.txt")] + idx, env) != 0:
                raise RuntimeError("The model libraries could not be installed.")
        else:
            raise RuntimeError("The model libraries could not be installed.")
    done += w

    w = step(3)
    if stream(uvpip + ["--no-deps", "-r", str(PROJECT / "requirements-nodeps.txt")], env) != 0:
        raise RuntimeError("The model packages could not be installed.")
    done += w

    w = step(4)
    ok, info = verify(py, p["device"])
    if not ok:
        raise RuntimeError(f"{p['device_name']} could not be used: {info}")
    done += w

    cfg = {"device": p["device"], "backend": p["backend"], "device_name": p["device_name"], "memory_gb": p["memory_gb"],
           "unified_memory": p["unified_memory"], "accelerators": det["accelerators"], "torch": info, "python": str(py),
           "os": det["os"], "arch": det["arch"], "installed": _dt.datetime.now().isoformat(timespec="seconds")}
    write_config(data, cfg)
    return cfg


CHECK = r"""
import json, sys, torch
dev = sys.argv[1]
ok = {"cuda": torch.cuda.is_available(), "mps": hasattr(torch.backends, "mps") and torch.backends.mps.is_available(),
      "xpu": hasattr(torch, "xpu") and torch.xpu.is_available(), "cpu": True}.get(dev, False)
if ok:
    x = torch.ones(64, 64, device=dev)
    ok = float((x @ x).sum()) == 64.0 * 64 * 64
import transformers
print(json.dumps({"ok": bool(ok), "torch": torch.__version__, "transformers": transformers.__version__}))
"""


def verify(py: Path, device: str) -> tuple[bool, str]:
    code, out = run([str(py), "-c", CHECK, device], timeout=600, stdout_only=True)
    for line in reversed(out.strip().splitlines()):
        try:
            res = json.loads(line)
            return res["ok"], res["torch"] if res["ok"] else f"PyTorch {res['torch']} cannot see the device"
        except (ValueError, KeyError, TypeError):
            continue
    _, full = run([str(py), "-c", CHECK, device], timeout=600)   # the error text, for the details log
    return False, full.strip()[-300:] or f"exit code {code}"


def write_config(data: Path, cfg: dict) -> None:
    data.mkdir(parents=True, exist_ok=True)
    path = data / "config.json"
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except ValueError:
            old = {}
    path.write_text(json.dumps({**old, **cfg}, indent=2))


# ------------------------------------------------------------------------------------------------------------------
# command line

def describe(a: dict) -> str:
    mem = f", {a['memory_gb']:.0f} GB{' shared' if a.get('unified_memory') else ''}" if a.get("memory_gb") else ""
    kind = {"nvidia": "NVIDIA GPU", "apple": "Apple GPU", "intel": "Intel GPU", "amd": "AMD GPU (experimental)", "cpu": "CPU"}[a["kind"]]
    return f"{kind}: {a['name']}{mem}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="describe this computer's hardware")
    d.add_argument("--json", action="store_true")
    pl = sub.add_parser("plan", help="show what would be installed for a device")
    pl.add_argument("--device", required=True)
    for name in ("install", "setup"):
        s = sub.add_parser(name)
        s.add_argument("--device", required=(name == "install"))
        s.add_argument("--venv", default=str(PROJECT / ".venv"))
        s.add_argument("--data", default=str(PROJECT / "data"))
        s.add_argument("--uv")
    c = sub.add_parser("configure", help="record the device choice for an existing environment")
    c.add_argument("--device", required=True)
    c.add_argument("--data", default=str(PROJECT / "data"))
    c.add_argument("--python", default=sys.executable)
    a = ap.parse_args()

    if a.cmd == "detect":
        det = detect()
        if a.json:
            det["plans"] = {}
            for acc in det["accelerators"]:
                try:
                    det["plans"][acc["id"]] = plan(det, acc["id"])
                except SystemExit as e:
                    det["plans"][acc["id"]] = {"error": str(e)}
            print(json.dumps(det, indent=2))
        else:
            print(f"{det['os']} {det['arch']}, {det['memory_gb']} GB memory, {det['disk_free_gb']} GB free disk")
            for acc in det["accelerators"]:
                print(("  * " if acc["id"] == det["recommended"] else "    ") + describe(acc))
            for n in det["notes"]:
                print("  note:", n)
        return 0
    if a.cmd == "plan":
        print(json.dumps(plan(detect(), a.device), indent=2))
        return 0
    if a.cmd == "configure":
        det = detect()
        p = plan(det, a.device)
        ok, info = verify(Path(a.python), p["device"])
        if not ok:
            print(f"{p['device_name']} cannot be used from {a.python}: {info}")
            return 1
        write_config(Path(a.data), {"device": p["device"], "backend": p["backend"], "device_name": p["device_name"], "memory_gb": p["memory_gb"],
                                    "unified_memory": p["unified_memory"], "accelerators": det["accelerators"], "torch": info,
                                    "python": a.python, "os": det["os"], "arch": det["arch"],
                                    "installed": _dt.datetime.now().isoformat(timespec="seconds")})
        print(f"Configured {p['device_name']} ({p['device']}).")
        return 0

    global HUMAN
    HUMAN = a.cmd == "setup"
    uv = find_uv(a.uv)
    device = a.device
    if a.cmd == "setup" and not device:
        det = detect()
        print("\nBud Decision Studio setup\n")
        opts = det["accelerators"]
        for i, acc in enumerate(opts, 1):
            print(f"  {i}. {describe(acc)}{'   (recommended)' if acc['id'] == det['recommended'] else ''}")
        for n in det["notes"]:
            print("  note:", n)
        choice = input(f"\nWhere should models run? [1-{len(opts)}, Enter for recommended]: ").strip()
        device = opts[int(choice) - 1]["id"] if choice.isdigit() and 1 <= int(choice) <= len(opts) else det["recommended"]
    t0 = time.time()
    try:
        cfg = install(device, Path(a.venv), Path(a.data), uv)
    except (RuntimeError, SystemExit) as e:
        emit(type="error", message=str(e))
        return 1
    emit(type="done", ok=True, seconds=round(time.time() - t0), config=cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
