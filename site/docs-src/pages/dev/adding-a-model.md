---
title: Add a model
description: Add a decision model to Bud Decision Studio with a catalog entry and an adapter, show it in the interface, and test it.
lead: A model joins the studio through two pieces of Python. A catalog entry says what the model is and where its weights are; an adapter says how to run it. Downloads, loading, every API format, history and the interface follow from those two.
---

## What a new model needs

- **A catalog entry** in `basal/catalog.py`. Always.
- **An adapter** in `basal/adapters/<family>_adapter.py`, registered in `basal/adapters/__init__.py`, when the model's family has none yet.
- **Its library** in `requirements.txt`, or in `requirements-nodeps.txt` when its declared dependencies conflict with the rest, when the adapter imports a package the studio does not install yet.
- **Details and published results** in `ui/registry.json`, for the Models page.
- **What it is made for, and its examples**, in `ui/js/model-guides.js` and `ui/js/examples.js`, for the Playground.
- **A line in the environment check**, `basal/doctor.py`, when it brings a new library.

A fine-tuned checkpoint of a model the studio already runs needs only the catalog entry and the interface details. Laya, Laya Multilingual and Laya Typed-Decisions, for example, share one adapter.

## The catalog entry

Each model is one `ModelSpec` in the `CATALOG` list. The runtime uses the facts; the interface shows the plain-language text. The entry for Laya:

| Field | What it holds |
|---|---|
| `id` | The stable local id, also the `model` value in API requests. Never change it once released |
| `name`, `maker` | What the interface shows |
| `adapter` | The adapter key in `basal/adapters/__init__.py` |
| `repo` | The Hugging Face repository, with optional `include` and `exclude` file patterns, and a fallback `size_gb` and `likes` for when the Hub is unreachable |
| `base` | A second repository downloaded alongside, for adapters trained on a frozen base model (Kev's LoRA adapters on Qwen) |
| `params`, `memory_gb` | Size in parameters, and the memory it needs once loaded. `memory_gb` decides whether the model fits this computer |
| `types` | The question types it answers: `choice`, `score`, `noul` by default |
| `modalities` | What it reads: `text`, plus `image`, `audio` or `video` |
| `max_options`, `max_questions` | Limits checked before the model is called |
| `context_tokens` | The longest input it accepts |
| `one_pass` | `True` when it answers every question in one forward pass |
| `speed` | `instant`, `fast`, `moderate` or `heavy` |
| `needs_gpu` | `True` for models that cannot run on the processor; they are never offered there |
| `load_options` | The settings offered under **Advanced** when loading: `DEVICE` (Run on), `DTYPE` (number precision) and `_ctx(default, maximum)` (max input length) |
| `tagline`, `summary`, `good_for`, `watch_out`, `languages`, `badge`, `headline_metric`, `license` | The text on the Models page. Facts come from the model's own card, and numbers are the publisher's |

The server reads the Hub's file list, sizes and likes for every catalog repository at startup and caches them in `DATA/hub_meta.json`, so the new model's download size appears without further work.

:::console basal/catalog.py
```python
ModelSpec(
    id="laya", name="Laya", maker="ConvAI Innovations", adapter="laya",
    repo=Repo("convaiinnovations/laya",
              exclude=("multilingual/*", "typed-decisions/*", "assets/*", "eval/*"),
              size_gb=0.85, likes=4491),
    params="421M", memory_gb=1.2, speed="instant",
    tagline="The most popular open decision model.",
    summary="The English flagship of the Laya family: a ModernBERT-large encoder "
            "plus a small decision head with an act-or-escalate gate. The most-liked "
            "open alternative to TypeSafe's Jev.",
    good_for=("English email and ticket triage", "Guardrails and content moderation",
              "Fast yes/no checks"),
    watch_out=("Fails confidently on non-Latin scripts (use Laya Multilingual for those)",
               "Zero-shot accuracy is close to chance on unfamiliar tasks; best after fine-tuning",
               "512-token context: long documents get cut off"),
    languages="English", max_options=20, context_tokens=512,
    headline_metric="0.86 on English XNLI; 39 ms per question on a T4",
    badge="Most liked",
    load_options=(DEVICE, _ctx(512, 512)),
),
```
:::

## The adapter

An adapter runs inside a worker process and owns exactly one loaded model. It subclasses `Adapter` from `basal/adapters/base.py` and implements two methods:

| Method | What it does |
|---|---|
| `load()` | Load the weights onto `self.device`. Report progress with `self.stage("Loading the encoder", 0.3)`; the text appears in the interface while the model loads |
| `decide(x)` | Answer one request. Return a `DecideOutput` whose `probs` holds, for each question in `x.questions`, one probability per option in the question's `keys` order |

Two more have defaults you can override. `warmup()` runs one tiny request after loading, so the first real request is not slow. `effective_device()` reports where the model really ended up, when its library can move it; returning `"cpu"` after a GPU load makes the studio tell the person it is running on the processor.

The base class gives you helpers: `self.snapshot(repo_id)` returns the local path of a downloaded repository without touching the network, `self.torch_dtype()` picks bfloat16, float16 or float32 for the device, and `Adapter.probs_from_map(q, mapping, aliases)` turns a library's `{option: probability}` result into the ordered list, accepting `yes` for `true` with `NOUL_ALIASES`.

The Julia 1 adapter is a complete example, shown here without its comments. Julia's repository ships its own Python package, so the adapter imports it from the downloaded files.

:::console basal/adapters/julia_adapter.py
```python
import math
import sys

from .base import Adapter, DecideInput, DecideOutput


class JuliaAdapter(Adapter):
    def load(self):
        path = self.snapshot(self.spec.repo.id)
        if path not in sys.path:
            sys.path.insert(0, path)
        self.stage("Loading the encoder and decision head", 0.3)
        from julia.inference import TransformerEngine  # shipped in the repository
        self.engine = TransformerEngine(
            path, device=self.device,
            max_length=int(self.options.get("max_length") or 8192),
            head_length=512)

    def decide(self, x: DecideInput) -> DecideOutput:
        rows = []
        for q in x.questions:
            if q.type == "noul":
                opts = [q.descriptions[0] or "false", q.descriptions[1] or "true"]
            elif q.type == "choice":
                opts = [d or l for l, d in zip(q.labels, q.descriptions)]
            else:
                opts = list(q.labels)
            rows.append({"state": x.state_text,
                         "question": q.instructions or "Which option fits best?",
                         "type": q.type, "options": opts})
        logits = self.engine.logits(rows)
        probs = []
        for z in logits:
            m = max(z)
            e = [math.exp(v - m) for v in z]
            s = sum(e)
            probs.append([v / s for v in e])
        return DecideOutput(probs)


ADAPTER = JuliaAdapter
```
:::

### What the adapter receives

`decide` gets a `DecideInput`. Its questions are already normalised: pick-any, put-in-order and estimate-a-number questions arrive as `noul`, `choice` and `score` questions, and the studio recomposes the answers afterwards. Your adapter only ever sees three types.

| Field | What it holds |
|---|---|
| `x.request` | The validated request, including `state` as the caller sent it (a string, object or list) |
| `x.state_text` | The state rendered as text, for libraries that take a string |
| `x.media` | Images, audio and video as `{"type", "path", "name"}`, with an absolute path to the file |
| `q.id` | The question's key |
| `q.type` | `choice`, `score` or `noul` |
| `q.instructions` | The question, always filled in |
| `q.keys` | The probability keys, in the order `probs` must follow: option names, `"0"` to `"n"` for levels, or `"false"`, `"true"` |
| `q.labels`, `q.descriptions` | Short option names and their descriptions, where given |

`DecideOutput` also carries `input_tokens`, `passes` (forward passes used), `notes` (sentences shown with the answer, such as "image ignored"), and `extras` (model-specific signals per question; Laya passes its own act-or-escalate probability there).

Probabilities do not have to sum to one; the studio normalises them. Do not apply a temperature or compute confidence in the adapter: `contract.build_answers` does that the same way for every model. If an input cannot be answered, raise `ValueError` with a sentence a person can act on; the caller receives it as a rejected input, not a crash.

## Register it

Add the module to `MODULES` in `basal/adapters/__init__.py`, keyed by the name your catalog entry uses in `adapter=`. Each adapter module ends with `ADAPTER = <YourClass>`. Modules are imported only when a worker loads a model of that family, so a library one model needs is never imported for the others.

Put the library in `requirements.txt`. When its package metadata pins versions that conflict with the rest of the stack (GLiNER2 pins an older transformers, for example), put it in `requirements-nodeps.txt` instead; it is installed without its declared dependencies. Add a line for it to the list in `basal/doctor.py`, then run `./install.sh` again to install it.

:::console basal/adapters/__init__.py
```python
MODULES = {
    "kev": "kev_adapter", "laya": "laya_adapter", "julia": "julia_adapter",
    "gliner": "gliner_adapter", "lev": "lev_adapter", "intern": "intern_adapter",
    "jev_omni": "jev_omni_adapter", "clm": "clm_adapter",
    "fake": "fake_adapter",
}
```
:::

## Show it in the interface

The Models page reads `ui/registry.json`: under `models.<id>`, the maker's logo, links, parameter count, architecture, base model, training, and a list of `benchmarks`, each with the suite, metric, the model's and Jev's figures, the conditions and the source. Publishers' numbers are labelled as theirs; write what the source says, including its caveats.

The Playground reads `ui/js/model-guides.js`: one line on what the model is made for, its own placeholders, and the ids of its signature examples in `ui/js/examples.js`, best first. Each example exists to show the model at its best, so check that the model answers it correctly before you list it.

The starter templates record which models each example suits, so regenerate them with `node scripts/export-builtins.mjs` after changing either file.

## Test it

:::steps
1. **Load and ask.** Start the studio, download the model on the Models page, and press Decide in the Playground. The worker's log (**View log**, or `DATA/logs/worker-<id>.log`) shows each loading stage and any error.
2. **Smoke test it through the API.** `scripts/smoke.py <id>` loads the model, asks a support ticket's three questions (pick one, a scale, yes or no) three times, prints the answers, the latency and the memory used, and ejects it. `--image <file>` asks models that read images about a picture instead; `--keep` leaves the model loaded.
3. **Check its examples.** `scripts/model-examples.py <id>` asks the model its signature examples and checks the key answers.
4. **Run the conformance suite on it.** `BASAL_TEST_MODEL=<id>` runs the TypeSafe, SDK and gateway checks with your model answering.
5. **Run it through the interface.** `scripts/e2e.py --models <id>` answers all six question types in a real browser.
:::

What each suite covers: [Testing](/docs/dev/testing).

:::console Terminal
```bash
./run.sh &

.venv/bin/python scripts/smoke.py my-model
.venv/bin/python scripts/model-examples.py my-model

BASAL_TEST_MODEL=my-model .venv/bin/python -m pytest tests/test_conformance.py -q

.venv/bin/python scripts/e2e.py --models my-model --skip-download
```
:::
