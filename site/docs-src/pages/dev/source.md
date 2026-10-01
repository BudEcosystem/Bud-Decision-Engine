---
title: Run from source
description: Clone Bud Decision Studio, install the engine with install.sh, run the server, and configure it with environment variables.
lead: A source checkout runs the same server as the desktop app. One script detects your hardware and installs everything into a private environment in the checkout; another starts the studio.
---

## What you need

- **git**, to clone the repository.
- **uv**, the Python package manager the studio installs with. `install.sh` installs it for you when it is missing. uv also fetches Python 3.12, so you do not need a system Python.
- **Disk space** for PyTorch (a 1.5 GB download for the processor build, 4.4 GB for CUDA 13.0) and the model libraries, plus the models you download (0.6 GB for Julia 1, about 24 GB for Jev-Omni).
- **Node 18 or newer**, only for the JavaScript SDK conformance test and for regenerating the starter templates.
- **Rust and Node**, only to build the desktop app ([Desktop app and releases](/docs/dev/desktop)).

The studio runs on Linux (x86-64 and ARM64, including the NVIDIA GB10), macOS on Apple Silicon and Windows 10 or 11.

## Clone and install

`install.sh` runs `installer/engine.py setup`, the same installer the desktop app runs with buttons. It describes your computer's processors, asks where models should run, then installs into `./.venv`:

:::steps
1. **Python 3.12**, in a new virtual environment.
2. **PyTorch 2.11** for the device you chose: CUDA 12.6, 12.8 or 13.0 (picked from your NVIDIA driver), Metal on Apple Silicon, Intel XPU, ROCm, or the CPU.
3. **The model libraries** from `requirements.txt`, plus the fast linear-attention kernels from `requirements-cuda.txt` on NVIDIA GPUs.
4. **The model packages** from `requirements-nodeps.txt`, installed without their declared dependencies, which pin versions that conflict with the rest.
5. **A check** that PyTorch can use the device, then your choice is recorded in `data/config.json`.
:::

Running it again is safe. To move models to a different processor, run it again with another `--device`; it replaces the PyTorch build and keeps everything else.

:::console Terminal
@@ macOS and Linux
```bash
git clone https://github.com/BudEcosystem/Bud-Decision-Engine.git
cd Bud-Decision-Engine

# asks where models should run
./install.sh

# or choose up front: cuda, mps, xpu, rocm or cpu
./install.sh --device cpu
```
@@ Windows
```powershell
git clone https://github.com/BudEcosystem/Bud-Decision-Engine.git
cd Bud-Decision-Engine

# asks where models should run
powershell -ExecutionPolicy Bypass -File install.ps1

# or choose up front: cuda, xpu or cpu
powershell -ExecutionPolicy Bypass -File install.ps1 --device cpu
```
:::

### See what setup would do

The installer also answers questions without installing anything. `detect` describes the computer, with the recommended device marked `*`; `plan` shows what one device would install.

:::console Terminal
@@ Request
```bash
uv run --no-project --python 3.12 installer/engine.py detect
```
@@ Output
```text
linux aarch64, 121.6 GB memory, 20.2 GB free disk
  * NVIDIA GPU: NVIDIA GB10, 122 GB shared
    CPU: Cortex-X925 + Cortex-A725, 122 GB
```
:::

:::console Terminal
@@ Request
```bash
uv run --no-project --python 3.12 installer/engine.py plan --device cuda
```
@@ Output
```json
{
  "device": "cuda",
  "backend": "cuda",
  "device_name": "NVIDIA GB10",
  "memory_gb": 121.6,
  "unified_memory": true,
  "torch_index": "https://download.pytorch.org/whl/cu130",
  "torch_label": "PyTorch 2.11.0 for CUDA 13.0",
  "download_gb": 4.4,
  "extra_requirements": ["requirements-cuda.txt"]
}
```
:::

## Run the studio

`run.sh` starts the server from `.venv` and passes its options through. Open `http://127.0.0.1:8420` in a browser; the interface is the same one the desktop app shows. Stop the server with Ctrl+C, which ejects every loaded model first.

| Option | What it does |
|---|---|
| `--port` | The port to listen on. Default `8420`, or `BASAL_PORT` |
| `--host` | Default `127.0.0.1`: this computer only. `0.0.0.0` makes the studio reachable from your network; set `BASAL_API_KEY` first |

On Linux, `scripts/open.sh` starts the studio in the background if it is not running and opens it in your browser, and `scripts/install-desktop-launcher.sh` adds it to your applications menu.

On its first start the studio creates its database and the starter templates, as the output shows.

`python -m basal.doctor` checks the environment: that PyTorch can use the GPU and that every model library imports. It prints one line per library and `All good` when nothing is missing. Each GPU failure is named for what it is: no GPU, the GPU out of memory at that moment, or a test calculation that failed.

:::console Terminal
@@ macOS and Linux
```bash
# http://127.0.0.1:8420
./run.sh

# another port; or reachable from your network, with a key
./run.sh --port 8500
BASAL_API_KEY=change-me ./run.sh --host 0.0.0.0

# check the environment
.venv/bin/python -m basal.doctor
```
@@ Windows
```powershell
# http://127.0.0.1:8420
.venv\Scripts\python -m basal.server

# another port; or reachable from your network, with a key
.venv\Scripts\python -m basal.server --port 8500
$env:BASAL_API_KEY = "change-me"
.venv\Scripts\python -m basal.server --host 0.0.0.0

# check the environment
.venv\Scripts\python -m basal.doctor
```
@@ Output
```text
  Bud Decision Studio 0.3.0  at  http://localhost:8420

[history] database ready (migrations [1, 2, 3] applied)
[templates] 30 starter template(s) added or updated
```
:::

## Environment variables

The server reads these when it starts. A checkout keeps its data in `./data`; the desktop app sets `BASAL_DATA` to its own folder.

| Variable | What it does |
|---|---|
| `BASAL_DATA` | Where the studio keeps its database, settings, logs and media files. Default `./data` |
| `BASAL_PORT` | The port, when `--port` is not given. Default `8420` |
| `BASAL_HOST` | The address to listen on, when `--host` is not given. Default `127.0.0.1` |
| `BASAL_API_KEY` | Callers from other machines must send `Authorization: Bearer <key>`. Without a key, other machines can make decisions but cannot reach history, templates or settings |
| `BASAL_AUTH_LOCAL` | `1` requires the key from this computer too. The tests use it |
| `BASAL_CORS_ORIGINS` | Comma-separated websites allowed to call the studio from a browser |
| `BASAL_NO_DOWNLOADS` | `1` turns the download queue off, for a second studio that shares the model cache with a first one. Its download requests answer `409` and say why |
| `BASAL_FAKE_MODEL` | `1` adds the deterministic test model, `fake-decider` |
| `BASAL_FAKE_LOAD_SECONDS` | Makes the test model take that many seconds to load, for tests of what happens during a load |
| `BASAL_PARENT_PID` | Set by the desktop app: the server shuts down cleanly when that process is gone |
| `HF_HOME` | Read by the Hugging Face library: where model weights are cached. Default `~/.cache/huggingface` |

Two more are read by the tests: `BASAL_TEST_URL`, the studio to test (default `http://127.0.0.1:8420`), and `BASAL_TEST_MODEL` (default `laya`).

The studio sets a few variables itself for the processes it starts. Workers run with `HF_HUB_OFFLINE=1`, so loading a model never reaches the network, and downloads run with `HF_HUB_OFFLINE=0`. You do not need to set these.

:::console Terminal
```bash
# A second studio beside the app: its own history,
# the same models, no downloads of its own
BASAL_DATA=~/studio-b BASAL_PORT=8421 BASAL_NO_DOWNLOADS=1 ./run.sh

# Serve your network with a key, and allow one web app
# to call it from the browser
BASAL_API_KEY=change-me \
BASAL_CORS_ORIGINS=http://localhost:3000 \
./run.sh --host 0.0.0.0

# Models on another disk
HF_HOME=/data/huggingface ./run.sh
```
:::

## Work without a GPU or models

`BASAL_FAKE_MODEL=1` adds **Fake Decider** (`fake-decider`) to the model list. It loads instantly, reads every kind of media, needs no download, and computes its probabilities from a hash of the situation, the question and each option, so the same request always gets the same answer. The test suites and CI use it to exercise the whole API, history included, without PyTorch.

Run it with a throwaway data folder so its decisions stay out of your real history.

:::console Terminal
@@ Request
```bash
BASAL_FAKE_MODEL=1 BASAL_DATA=/tmp/studio-dev ./run.sh --port 8479 &

curl -s http://127.0.0.1:8479/v1/systemone -H 'content-type: application/json' -d '{
  "model": "fake-decider",
  "state": "The package arrived crushed.",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "team": {"type": "choice", "instructions": "Who should handle this?",
             "criteria": {"returns": "refunds and replacements",
                          "shipping": "carriers and delivery"}}
  }
}'
```
@@ Response 200
```json
{
  "model": "fake-decider",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.467},
    "team": {
      "type": "choice",
      "choice": "returns",
      "probabilities": {"returns": 0.8975, "shipping": 0.1025},
      "confidence": 0.7949
    }
  },
  "usage": {"input_tokens": 7, "output_tokens": 0}
}
```
:::

## Change the interface

The interface has no build step. Edit the files in `ui/`, then reload the page: the server sends them with `cache-control: no-cache`, so the browser always checks for the newer version. `DESIGN.md` describes its design system.

The 30 starter templates (`builtin/<scenario>`) are generated from the Playground's examples. After changing `ui/js/examples.js` or `ui/js/model-guides.js`, regenerate `basal/builtin_templates.json`; the template tests fail when the two are out of sync.

:::console Terminal
@@ Regenerate
```bash
node scripts/export-builtins.mjs
```
@@ Check
```bash
node scripts/export-builtins.mjs --check
```
@@ Output
```text
builtin_templates.json is in sync
```
:::

## Development dependencies

The test tools are not part of the engine install. Add them to `.venv` from `requirements-dev.txt`: pytest, jsonschema, PyYAML and the official TypeSafe Python SDK (`typesafe-sdk` 0.7.2) for the conformance test. The JavaScript SDK test needs `npm install` in `tests/js`, and the end-to-end check needs Playwright with Chromium.

:::console Terminal
```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
(cd tests/js && npm install)
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium
```
:::

What each suite covers and how to run it: [Testing](/docs/dev/testing).
