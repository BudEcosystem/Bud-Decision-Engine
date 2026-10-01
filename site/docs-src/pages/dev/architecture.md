---
title: Architecture
description: How Bud Decision Studio is built: the server, one worker process per model, adapters, the decision pipeline, templates, history and the interface.
lead: The studio is a small Python server that never touches the GPU, plus one separate process for each loaded model. This page follows a decision from the request to the history row and names the module that owns each step.
---

## The parts

Bud Decision Studio has four layers, and a fifth process while a model is being trained. Each one can fail or restart without taking the others down.

- **The interface** (`ui/`) is plain HTML, CSS and JavaScript modules with no build step. It runs in the desktop app's window or in any browser, and talks to the server over HTTP like any other client.
- **The studio server** (`basal/server.py`, FastAPI) serves the interface, every API format, the management endpoints and the studio API. It owns the database and the download queue. It never imports PyTorch, so it stays responsive while a 24 GB model loads.
- **Worker processes** (`basal/worker.py`), one per loaded model. A worker loads exactly one model through its **adapter** (the small class that drives that model's own library) and answers decisions over HTTP on `127.0.0.1`.
- **A training job** (`basal/training/job.py`), at most one at a time: a separate process that fine-tunes a model on the GPU while the server reads its progress files. It is described on [The trainer](/docs/dev/trainer).
- **The desktop shell** (`desktop/`, Tauri) installs the engine on first run, starts the server, points its window at it, and stops it on quit.

Model weights are not part of the studio. They live in the standard Hugging Face cache, shared with your other tools.

The Python modules in the diagram live in `basal/`.

:::diagram How the parts connect
# Desktop app | desktop/ · Tauri | The first run installs the engine; later runs start the server and show it.
> shows
# Interface | ui/ | HTML, CSS and JavaScript modules with no build step.
> HTTP
# Studio server | basal/server.py · FastAPI
- API formats | api_compat.py
- Studio API | studio_api.py
- Decision pipeline | decisions.py
- Templates | templates.py, template_store.py
- History | history.py, db.py
- Media files | blobs.py
- Downloads | hub.py, fetch.py
- Worker manager | workers.py
> HTTP on 127.0.0.1, one port per model
# Worker, one per loaded model | basal/worker.py | Loads one model through its adapter and runs it on the GPU or the processor.
- Adapter | adapters/
:::

## How a decision flows

Every entry point, from the Playground to the official TypeSafe SDK, goes through the same steps. The stateless wire routes (`/v1/systemone` and the gateway formats) and the studio API (`/v1/studio/decisions`) differ only in steps 2 and 7.

:::steps
1. **The request is checked before any handler runs.** The middleware in `server.py` refuses a `Host` header that is not this machine (a guard against DNS rebinding), writes from other websites (`guard.py`), management calls without `X-Basal-Client: 1`, and, when `BASAL_API_KEY` is set, calls from other machines without the key. Without a key, other machines may make decisions but cannot reach history, templates or settings.
2. **The request is read.** Wire routes parse the body in their published shape (`api_compat.parse`). The studio API resolves it (`decisions.resolve`): the template reference becomes one version, variables fill in the situation, extra questions are merged, and the model and settings are chosen, each setting with the layer it came from.
3. **A model is chosen and made ready.** A named model is used; with no name, the most recently loaded one. If it is downloaded but not loaded, it loads now (auto-load, on by default).
4. **The worker answers.** The server posts the request to the model's worker. The worker checks the model's limits (question types, options, questions, media), turns the six question types into the three the models know, and calls the adapter.
5. **Probabilities become answers.** `contract.build_answers` applies the calibration temperature and computes each answer's confidence, so an answer means the same thing on every model.
6. **Each answer is gated.** An answer *acts* when its certainty reaches the act threshold; the others are listed in `needs_review`.
7. **The decision is recorded, then answered.** Unless the caller opted out (`"store": false`), `history.insert` writes the decision in one transaction. A content-free usage counter is written either way. Wire routes return exactly the published response shape and add only `x-basal-*` headers; the studio API returns the full decision object.
:::

The pipeline is described at the top of `basal/decisions.py` as four words: resolve, check, run, record.

## One process per model

Loading a model starts a worker, `python -m basal.worker`, with the model's id, a free port and the load options, and its own log file, `DATA/logs/worker-<id>.log`. The worker starts a small web server at once and loads the model in a background thread, reporting each stage ("Importing the model's code", "Warming up") through `GET /health`. The server polls every worker's health about once a second; that is what the sidebar and the Models page show.

Ejecting a model sends `POST /shutdown` and then ends the worker's whole process group. This is the only reliable way to hand back every byte of GPU memory: PyTorch caches allocations and libraries keep references, so unloading inside a long-lived process leaks. It also means a crash in one model cannot take down the studio. When a worker dies, `workers.explain_exit` turns the exit code and the last lines of its log into a readable reason, such as running out of memory.

A worker answers one request at a time. Workers never open the database and never reach the network: they start with `HF_HUB_OFFLINE=1`, because the studio downloads weights before a model loads.

:::console GET /health
@@ Request
```bash
# worker.port in GET /api/models/<id>
curl -s http://127.0.0.1:46667/health
```
@@ Response 200
```json
{
  "model": "fake-decider",
  "pid": 2289261,
  "status": "ready",
  "stage": "Ready",
  "progress": 1.0,
  "error": null,
  "detail": null,
  "warning": null,
  "uptime_s": 149.7,
  "load_seconds": 3.9,
  "requests": 11,
  "busy_ms": 0.5,
  "memory": {},
  "options": {}
}
```
:::

## Adapters and the answer contract

`basal/contract.py` defines one request format for every model. It accepts the six question types and **normalises** them into the three that decision models answer natively:

| Question type | What the adapter receives |
|---|---|
| `choice`, `score`, `noul` | the same question |
| `multi` (pick any) | one yes-or-no question per option |
| `rank` (put in order) | one `choice` question; options are ordered by probability |
| `number` (estimate a number) | one `score` question over the allowed values |

An adapter receives these primitive questions and returns, for each one, a probability per option in a fixed order. It never formats answers. `build_answers` recomposes the extension types and builds the answer objects, so every model's answers have the same fields and the same meaning. The interface to implement is on [Add a model](/docs/dev/adding-a-model).

Adapters live in `basal/adapters/` and are imported lazily, so a worker loads only the library its model needs. A model with a different library, such as Laya's `laya` package or GLiNER's `gliner2`, gets its own adapter; models of one family share one.

## Models and downloads

`basal/catalog.py` lists the eleven models: their Hugging Face repositories, the adapter that runs them, their limits (question types, options, questions, context length) and the plain-language text the interface shows.

`basal/hub.py` answers "what is on disk" by reading the Hugging Face cache directly, so files downloaded by other tools count. Downloads run **one at a time**, smallest remaining download first, ties going to the model with more likes on the Hub. Each download is its own process (`python -m basal.fetch <repo>`): cancelling is immediate, a failed download cannot disturb the server, and a download keeps going across a studio restart, which picks it up again. The queue is saved in `DATA/download_queue.json`.

`basal/config.py` holds where models run: the device chosen during setup (in `DATA/config.json`), the devices this computer offers, and whether a model fits in its memory.

## Templates and history

**Templates** are split in two. `basal/templates.py` is pure logic with no database: it checks a definition, fills in variables, merges a caller's extra questions, picks settings per question, classifies how a new version differs from the last, and exports a JSON Schema. `basal/template_store.py` stores heads, immutable versions, aliases and test examples, and seeds the 30 starter templates (`builtin/<scenario>`) from `basal/builtin_templates.json` at every start.

**History** is one SQLite file, `DATA/studio.db`, opened by `basal/db.py` in WAL mode: one writer connection guarded by a lock, and a read-only connection per thread. A decision is written once, in one transaction, as a small row for lists and filters, a body read only when one decision is opened, and one row per answer for filters and statistics. `basal/history.py` writes and queries it, computes statistics and version comparisons, and runs the retention sweeper. Media files are stored once per content in `DATA/blobs/` by `basal/blobs.py`. Details on [Data and storage](/docs/dev/storage).

If history cannot open, decisions keep working and nothing is saved; the reason is printed at startup and the studio API answers `503 history_unavailable` for history calls.

## The interface

The interface is `ui/index.html` plus ES modules in `ui/js/`, loaded directly by the browser. `app.js` draws the shell (sidebar, title bar, status bar) and routes between pages in `ui/js/pages/`; `store.js` talks to the server and keeps one shared, regularly refreshed copy of `GET /api/state`. Model details and published results come from `ui/registry.json`; each model's examples from `ui/js/model-guides.js` and `ui/js/examples.js`.

The server sends the interface's files with `cache-control: no-cache`, so after an update the window never runs yesterday's scripts. The interface sends the header `X-Basal-Client` on every call, which is how the server tells its own pages from other clients; it refreshes the state about once a second while the window is visible.

## Where things live

| Path | What it owns |
|---|---|
| `basal/server.py` | The FastAPI app: request checks, management endpoints (`/api/...`), the wire routes, the interface |
| `basal/studio_api.py` | The studio API at `/v1/studio`, with one error envelope for every problem |
| `basal/studio_docs.py` | Request bodies and examples for the interactive reference at `/docs` |
| `basal/api_compat.py` | The published wire formats: TypeSafe, OpenRouter, Vercel AI Gateway |
| `basal/decisions.py` | The decision pipeline, background decisions, idempotency keys |
| `basal/contract.py` | The request format, question normalisation, answers, certainty and the act gate |
| `basal/templates.py` | Template logic: checks, variables, extensions, settings, versions, diffs |
| `basal/template_store.py` | Templates, versions, aliases, test examples and starter templates in the database |
| `basal/history.py` | Recording and querying decisions, statistics, feedback, export, retention |
| `basal/db.py` | The SQLite connection, migrations, backups and read-only mode |
| `basal/blobs.py` | Images, audio and video, stored once per content |
| `basal/guard.py` | The cross-site guard |
| `basal/errors.py`, `basal/ids.py` | The studio API's error envelope; `dec_...`-style ids |
| `basal/workers.py`, `basal/worker.py` | Starting, watching and stopping workers; the worker process itself |
| `basal/training/` | The trainer: the training file format, one plugin per model family, the training loop and its checks, the job process; see [The trainer](/docs/dev/trainer) |
| `basal/finetunes.py` | Trained models: each becomes a catalog entry; a worker attaches its changes to the released model; export and import |
| `basal/adapters/` | One adapter per model family, the base class, and the test model |
| `basal/catalog.py`, `basal/hub.py`, `basal/fetch.py` | The model list, what is on disk, the download queue |
| `basal/config.py`, `basal/sysinfo.py` | Devices and memory fit; live GPU, memory and disk figures |
| `basal/paths.py` | Where the studio keeps its files (`BASAL_DATA`) |
| `basal/doctor.py` | `python -m basal.doctor`, the environment check |
| `basal/migrations/` | Database migrations, applied in order at startup |
| `installer/engine.py` | Hardware detection and the one-step engine install, shared by the app and `install.sh` |
| `desktop/` | The desktop app: setup screens and the Rust shell |
| `ui/` | The interface |
| `tests/`, `scripts/` | Test suites, the end-to-end check and maintenance scripts |
