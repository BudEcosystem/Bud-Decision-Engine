# Test results

## 0.2.0: templates, history and the studio API

Run on 2026-10-01, NVIDIA GB10 (DGX Spark), Ubuntu 24.04.

| What | How | Result |
|---|---|---|
| Template logic | `tests/test_templates.py`: every variable type and save error, the substitution rules (typed whole values, dropped keys, single pass, escaping), sensitive values, options variables, extensions, the settings ladder and its sources, hashing, version comparability and change classes, the JSON Schema export, the starter templates in sync | 49 of 49 pass |
| Answer contract | `tests/test_contract.py`: per-question temperature (including pick-any options), raw probabilities, certainty and the act gate for every type | 9 of 9 pass |
| History store | `tests/test_history_store.py`: storage levels, immutable versions and outputs, answer projections, filters and cursors under inserts, retry folding, feedback and statistics, retention exemptions, erasure by a sensitive value, redaction, search, and migration safety (activity import, backups, changed-checksum refusal, read-only when newer) | 11 of 11 pass |
| The studio API, live | `tests/test_studio_api.py` against a studio with the deterministic test model: the five calls of the design end to end, the wire routes' bodies unchanged with and without storage, lenient `store` and `metadata`, `X-Basal-Template`, idempotency, SDK retries, the cross-site guard, safe file serving, storage levels, background decisions, the template lifecycle, starter templates, preview, settings, and the error envelope | 20 of 20 pass |
| API conformance | `tests/test_conformance.py` against the test model and against Laya | 14 of 14 pass on each |
| Through the interface | `scripts/e2e.py --models laya --skip-download`: Playground (six question types, a decision from scratch), Evaluate, **History** (listed and inspectable), **Templates** (Save as template, Decide in template mode, found in the template's history), the API page's curl example, switching to the processor and back, no JavaScript errors on any page | 10 of 10 pass |

The first four also run on macOS, Windows and Linux in CI (`.github/workflows/studio.yml`), with no model libraries installed.

## 0.1.x: every model

Run on 2026-09-30 21:13, NVIDIA GB10 (DGX Spark), Ubuntu 24.04, while other programs shared its memory. 21 of 21 end-to-end checks passed. API conformance: 14 of 14 checks pass (TypeSafe's OpenAPI schema, the official Python and JavaScript SDKs, OpenRouter's schema).

How to run them yourself:

```bash
pip install -r requirements-dev.txt && pip install playwright && playwright install chromium
./run.sh &                                   # or open the desktop app
pytest tests/test_conformance.py -q          # API conformance: TypeSafe, OpenRouter, both official SDKs
python scripts/e2e.py --report e2e.json      # every model and page, through the interface
```

## End to end, through the interface

`scripts/e2e.py` drives the studio in a browser the way a person would. On the Playground, every downloaded model answers the *Every question type at once* example (pick one, rate on a scale, yes or no, pick any, put in order, estimate a number), and the models that read images also answer the receipt-photo example. Each answer has to appear as a chart with no page errors. Times include loading the model.

| Area | Check | Result | Details | Seconds |
|---|---|---|---|---|
| playground | kev-4b: tour (6 answers) | pass | Kev 4B: 6 answers in 207 ms | 59.6 |
| playground | lev: tour (6 answers) | pass | Lev: 6 answers in 980 ms | 56.5 |
| playground | intern-decision-4b: tour (6 answers) | pass | Intern-Decision 4B: 6 answers in 182 ms | 53.4 |
| playground | intern-decision-4b: image (3 answers) | pass | Intern-Decision 4B: 3 answers in 240 ms | 1.1 |
| playground | clm-v0.1-8b: tour (6 answers) | pass | CLM 8B: 6 answers in 24 ms | 1.3 |
| playground | jev-omni: tour (6 answers) | pass | Jev-Omni: 6 answers in 2191 ms | 3.4 |
| playground | jev-omni: image (3 answers) | pass | Jev-Omni: 3 answers in 1408 ms | 2.1 |
| evaluate | leaderboard for julia-1, laya-multilingual | pass | 2 rows | 26.3 |
| activity | requests listed and inspectable | pass | 64 requests shown |  |
| api | quick-start curl from the page | pass | 3 answers |  |
| models | delete and re-download julia-1 from the page | pass |  | 85.0 |
| playground | julia-1: support (1 answers) | pass | Julia 1: 1 answers in 2054 ms | 21.8 |
| system | switch to the processor and answer there | pass | worker device: cpu |  |
| system | switch back | pass | cuda |  |
| ui | no JavaScript errors on any page | pass |  |  |
| playground | julia-1: tour (6 answers) | pass | Julia 1: 6 answers in 165 ms | 19.7 |
| playground | laya-multilingual: tour (6 answers) | pass | Laya Multilingual: 6 answers in 120 ms | 10.2 |
| playground | laya: tour (6 answers) | pass | Laya: 6 answers in 140 ms | 7.2 |
| playground | laya-typed-decisions: tour (6 answers) | pass | Laya Typed-Decisions: 6 answers in 150 ms | 8.1 |
| playground | kev-0.5b: tour (6 answers) | pass | Kev 0.5B: 6 answers in 34 ms | 17.3 |
| playground | gliner2.5-decide: tour (6 answers) | pass | GLiNER2.5 Decide: 6 answers in 535 ms | 13.3 |
| desktop | first-run setup from the AppImage: hardware check, PyTorch 2.11.0 for CUDA 13.0, device check, studio start (33 s with a warm download cache) | pass | | |
| desktop | CPU-only install, then switching the same install to the GPU build | pass | | |
| desktop | adds itself to the applications menu with its icon (entry validated with desktop-file-validate) | pass | | |
| desktop | closing or force-quitting the app stops the engine and ejects every model | pass | | |
| desktop | setup screens: every step, error and recovery state, light and dark, previewed with simulated Mac, Intel and CPU-only computers | pass | | |
| desktop | macOS and Windows builds (GitHub Actions); their platform-only code is type-checked for both targets | built in CI (all four platforms) | | |
