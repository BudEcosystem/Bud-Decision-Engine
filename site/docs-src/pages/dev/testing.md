---
title: Testing
description: The test suites of Bud Decision Studio, what each one covers, how to run them, and what runs in CI.
lead: Most of the studio can be tested in under half a minute without a GPU or a model, because a deterministic test model stands in for the real ones. The slower checks run real models through the API and through the interface in a browser.
---

## The suites

| Suite | What it checks |
|---|---|
| `test_templates.py` | 62 tests of template logic: variable types, substitution, sensitive values, extensions, the settings ladder, version change classes (including narrowed variables), model compatibility, the JSON Schema export, starter templates in sync |
| `test_contract.py` | 9 tests of answers: per-question temperature, raw probabilities, certainty and the act gate for every type |
| `test_history_store.py` | 11 tests of history in a real SQLite file: storage levels, immutability, filters, feedback, statistics, retention, erasure, redaction, search, migration safety |
| `test_studio_api.py` | 24 tests against a live studio: the main calls end to end, wire routes unchanged, idempotency, retries, the cross-site guard, background decisions and cancelling one while its model loads, sensitive files leaving nothing on disk, the error envelope |
| `test_doctor.py` | 4 tests of the environment check: each PyTorch and GPU failure is reported as itself |
| `test_conformance.py` | 14 checks of the API against TypeSafe's published schema, both official SDKs and OpenRouter's schema. Needs a running studio and a model |
| `test_installer.py` | 3 regression tests of the engine installer |
| `training/test_dataformat.py`, `test_metrics_and_engine.py`, `test_device_policy.py`, `test_job.py` | 34 tests of the trainer that need no PyTorch: the training file importers and splits, metrics and temperature fitting, the release gate, replay and out-of-memory handling, export and import of trained models, the device policy for NVIDIA, AMD, Intel, Apple and the processor, the training queue and the supervisor |
| `training/test_<family>.py` | 21 contract tests, one file per model family: real tokenizers (and, where cached, real weights) on the processor; scored, trained, exported, attached to a fresh adapter, and checked to answer like the trained model |
| `scripts/e2e.py` | Every page through a real browser, with real models |
| `scripts/e2e_train.py` | The Train page through a real browser: the example file, review, a real training on the GPU, the result, **Use it now**, and an answer in the Playground |
| `scripts/ui_checks.py` | Interface regressions through a real browser with the test model: leaving the Playground mid-start, the id fields' validation, Save as template in a fresh window, Evaluate's threshold range. Starts its own studio; needs Playwright |
| `scripts/site_release_checks.py` | The website's release handling through a real browser, with GitHub's answers stood in for: a newer release, the API refusing, nothing answering, and the docs following along. Serves `site/` itself; needs Playwright |

The test files are in `tests/`. The first five are the **studio tests**. They need only the server's own libraries, not PyTorch: `tests/conftest.py` gives each run a temporary data folder and turns on `BASAL_FAKE_MODEL=1`, the deterministic test model described on [Run from source](/docs/dev/source).

## Run the studio tests

The command CI runs works from a clean checkout with nothing installed but uv: it fetches Python 3.12 and the few libraries the tests need. From a checkout set up with `install.sh`, `.venv` already has them once you add `requirements-dev.txt`.

`test_studio_api.py` starts a studio of its own on a free port with a temporary data folder, so it never touches your history or a studio you have open. `test_templates.py` also runs `node scripts/export-builtins.mjs --check`, so it needs Node.

:::console Terminal
@@ uv
```bash
uv run --no-project --python 3.12 \
  --with fastapi --with 'uvicorn[standard]' --with httpx --with psutil \
  --with 'pydantic>=2.12' --with python-multipart --with 'huggingface_hub>=1.0' \
  --with jsonschema --with pytest \
  pytest tests/test_templates.py tests/test_contract.py \
         tests/test_history_store.py tests/test_studio_api.py tests/test_doctor.py -q
```
@@ .venv
```bash
.venv/bin/python -m pytest tests/test_templates.py tests/test_contract.py \
  tests/test_history_store.py tests/test_studio_api.py tests/test_doctor.py -q
```
@@ Output
```text
........................................................................ [ 65%]
......................................                                   [100%]
110 passed in 35.41s
```
:::

## API conformance

`tests/test_conformance.py` checks that code written for TypeSafe's Jev works against the studio unchanged. It runs against a live studio, loading the model it is told to use if needed, and compares the studio with the publishers' own sources:

- TypeSafe's OpenAPI file (`api.typesafe.ai/openapi.json`): the response schema exactly, extension fields only when asked for, FastAPI-style 422 validation errors, 404 for unknown paths, the model list and its aliases, extra request fields ignored, and the authentication errors.
- The official SDKs: `typesafe-sdk` 0.7.2 for Python and `@typesafe-ai/sdk` 0.6.0 for JavaScript, each making real calls.
- OpenRouter's OpenAPI file, for its two routes, and Vercel AI Gateway's TypeSafe route and evaluation API.

The publishers' files are downloaded on the first run and cached in `tests/.cache/`. The JavaScript SDK test needs `npm install` in `tests/js`. Choose the studio and the model with `BASAL_TEST_URL` and `BASAL_TEST_MODEL`; the default is `laya` on `http://127.0.0.1:8420`. The authentication test starts a second studio of its own with `BASAL_API_KEY` set.

Without a GPU, run it against the test model, as in the output shown. The same suite can be started from a running studio with `POST /api/conformance/run`; `GET /api/conformance` returns the last result.

:::console Terminal
@@ Test model
```bash
BASAL_FAKE_MODEL=1 BASAL_DATA=/tmp/studio-test ./run.sh --port 8479 &
BASAL_TEST_URL=http://127.0.0.1:8479 BASAL_TEST_MODEL=fake-decider \
  .venv/bin/python -m pytest tests/test_conformance.py -q
```
@@ Laya
```bash
./run.sh &
.venv/bin/python -m pytest tests/test_conformance.py -q
```
@@ Output
```text
..............                                                           [100%]
14 passed in 12.62s
```
:::

## End to end, through the interface

`scripts/e2e.py` drives the studio in a headless browser the way a person would, against a running studio with real models:

- **Playground:** every downloaded model answers all six question types, and the models that read images answer a receipt photo. Each answer must appear as a chart with no page errors.
- **Evaluate:** a leaderboard over the sample examples with two models.
- **History and Templates:** the decisions just made are listed and open; a Playground decision is saved as a template, run in template mode, and found in the template's history.
- **API:** the quick-start curl command shown on the page runs and returns answers.
- **Models and System:** a small model's files are deleted and downloaded again from the page; models switch to the processor, answer there, and switch back.

It loads models one at a time, keeping 6 GB of memory free beyond each model's own (`--headroom`). `--models` limits it to some models, `--skip-download` skips the download check, and `--no-models` or `--no-pages` runs half of it. `--report` writes the results as JSON; `scripts/test-report.py` turns that file into `docs/testing.md`.

:::console Terminal
```bash
uv pip install --python .venv/bin/python playwright
.venv/bin/python -m playwright install chromium

./run.sh &
.venv/bin/python scripts/e2e.py --models laya,kev-4b \
  --skip-download --report e2e.json
.venv/bin/python scripts/test-report.py e2e.json \
  --conformance "14 passed" --machine "NVIDIA GB10, Ubuntu 24.04"
```
:::

## Other checks

| Script | What it checks |
|---|---|
| `scripts/smoke.py` | Through the API, per model: load, three decisions, latency and memory, eject. `--image` adds a picture for models that read images |
| `scripts/model-examples.py` | Each model answers its own signature examples (`ui/js/model-guides.js`) as intended. A wrong answer there is a bug |
| `scripts/webkit-sweep.py` | Every page, dialog and setup screen in WebKit, the engine of Safari and of the macOS and Linux apps, at several window sizes |
| `scripts/safari-check.py` | The interface in real Safari through Apple's `safaridriver`, including the first-run model chooser |
| `tests/test_installer.py` | The installer reads PyTorch's version and the device check without being confused by warnings, and never passes paths to uv options that split them at spaces |

## Continuous integration

Four GitHub Actions workflows run on the repository. The studio tests need no GPU, so they run on every operating system on each change.

| Workflow | When and what |
|---|---|
| `studio.yml` | When `basal/`, `tests/`, the examples or the export script change: the studio tests and the trainer's PyTorch-free tests on macOS 14, Windows and Ubuntu 24.04 |
| `installer.yml` | When `installer/` or the requirements change: the installer tests, then a real first-run install on the processor into a folder named `Application Support`, on macOS, Windows and Ubuntu |
| `safari.yml` | When `ui/` changes: the interface in Safari on macOS 14 and 15, with screenshots kept as a build artifact |
| `desktop.yml` | When a `v*` tag is pushed, or by hand: the desktop app for four platforms, published as a release ([Desktop app and releases](/docs/dev/desktop)) |

Conformance, the end-to-end checks and real training runs need models and a GPU, so they are run on a real machine before each release; the results are in `docs/testing.md`, and every training run in `docs/trainer/RESULTS.md`.
