---
title: The trainer
description: How the studio fine-tunes its models - one PyTorch engine with a small plugin per model family, the job process and its supervisor, the release gate, the stored delta, and how to add a family or run training from the command line.
lead: The trainer is one PyTorch training loop shared by every model, plus a short plugin per model family that says how to load the model, turn an example into its input and read option probabilities back. It runs as its own process, one job at a time, and a fine-tune it keeps is a small file of changes that the studio's workers attach to the released model.
---

## The parts

| Module | What it does |
|---|---|
| `basal/training/dataformat.py` | the standard training format, the importers for tables and Evaluate files, quality checks, and the grouped, stratified train/calibration/test split. No PyTorch |
| `basal/training/families/base.py` | the contract every family meets (`FamilyTrainer`, `Unit`, `Recipe`), LoRA injection, delta export and attach |
| `basal/training/families/<family>.py` | one plugin per family: `julia`, `laya`, `gliner`, `kev`, `intern`, `lev`, `clm`, `jev_omni` |
| `basal/training/engine.py` | the loop: replay with distillation, the drift monitor, early stopping, out-of-memory recovery, calibration, the release gate, publishing and the serving parity check |
| `basal/training/metrics.py` | accuracy, log loss, calibration error, temperature fitting, the paired bootstrap, collapse detection |
| `basal/training/device.py` | which GPUs may train and in what precision, memory probes and caps, the self-test, the memory watchdog |
| `basal/training/job.py` | the job process, its supervisor and the machine-wide queue |
| `basal/training/manager.py`, `api.py`, `probe.py` | the server side: datasets, the model recommendation, starting and controlling jobs. The server never imports PyTorch |
| `basal/finetunes.py` | the registry: each saved fine-tune becomes a catalog entry; export and import |
| `basal/training/assets/general.jsonl` | the general examples every run replays and is checked against, built by `scripts/build_training_pack.py` |
| `ui/js/pages/train.js` | the Train page |

## The family contract

A family plugin turns examples into **units** (one forward pass: one question for models that answer one at a time, or a whole example for models that answer every question in one pass) and returns, for each question in a unit, differentiable log-probabilities over the question's options in the studio's key order. Everything else (the loss, replay, evaluation, calibration, the gate) is written once in the engine.

```python
class FamilyTrainer:
    def recipe(self, spec, dev, n_train_questions) -> Recipe: ...    # this family's best-practice settings
    def supports(self, spec, example) -> str | None: ...           # a reason, when the model can't take this example
    def estimate_gb(self, spec, recipe) -> float: ...               # memory to train, measured
    def load(self, spec, dev, recipe, stage) -> None: ...           # load through the serving adapter, freeze, add LoRA
    def units(self, example, train, rng=None) -> list[Unit]: ...    # shuffle choice options when train is true
    def score(self, units) -> list[list[Tensor]]: ...                # log-probabilities per question, with gradients
    @staticmethod
    def attach(adapter, delta) -> None: ...                         # serving: put a saved delta onto a loaded model
```

Plugins load the model through the studio's own adapter and build inputs with the same functions the adapter's `decide()` uses (`julia_row`, `laya_questions`, `gliner_tasks`, `kev_record`, `intern_request`, `lev_questions`, `clm_pairs`, `jev_question`), so a fine-tune is trained on exactly what the studio will send it.

## The recipes

| Family | What trains | Learning rate | Notes |
|---|---|---|---|
| Julia 1 | LoRA r16 on `Wqkv`, `Wo`, `Wi`; head frozen | 5e-5 | at 1e-4, or with the head trained, it forgot general decisions |
| Laya (all three) | LoRA r16 on `Wqkv`, `Wo`, `Wi`; head frozen | 1e-4 | activation checkpointing when the fast path doesn't fit in free memory |
| GLiNER2.5 Decide | LoRA r16 on DeBERTa's attention and dense layers, plus the classifier | 1e-4 | fp32 |
| Kev 0.5B, Kev 4B | a new LoRA on the released targets, over the released adapter merged as Kev serves it; the pointer head | 5e-5 (head 2e-5) | evaluation scores one example at a time, as served |
| Intern-Decision 4B | LoRA r16 on the language model | 5e-5 | one pass; replay 25% |
| Lev | its released LoRA, trained further in place, unmerged | 5e-5 | merging it in bf16 changed its answers by up to 0.053 |
| CLM 8B | its two heads only, on embeddings of the frozen 8B encoder computed once per text | 5e-4 | replay capped at 1,500 |
| Jev-Omni | LoRA r16 on the language model and the head's linear layer | 5e-5 | NVIDIA only |

Frozen weights are held in bf16 (fp16 on older GPUs); trainable weights and their optimizer state are fp32.

## A job's life

The studio starts `python -m basal.training.job <job folder>`. That process is a **supervisor**: it waits its turn in the machine-wide queue (a lock file in `~/.cache/bud-decision-studio/` with first-come tickets beside it), then runs the engine in a child process and watches it. If the engine's main thread is silent for 15 minutes (30 while loading or verifying), the supervisor records its stacks and starts it again, up to twice; on an NVIDIA GB10 under heavy memory pressure a GPU-to-host copy was seen to hang with the GPU idle.

The engine, in order:

1. checks the GPU (`device.profile`, a self-test against the processor) and caps the job's GPU memory at twice its family's estimate;
2. reads and splits the examples, drops general examples whose situation also appears in them, and loads the model;
3. measures the released model on the test split and the general guard set, and records its answers on the replay examples;
4. trains: each micro-batch is the person's examples plus replay examples distilled towards the released model's answers; every few hundred questions it measures progress and drift and keeps the best step;
5. fits a temperature, runs the gate, and if forgetting was the only failure, trains once more from the start more gently;
6. saves the delta, reloads it through `finetunes.load_adapter` exactly as a worker would, and compares answers per example.

The job writes `events.jsonl` (progress), `status.json`, `result.json` (every number the gate used), `predictions.json` (every test and guard answer before and after) and `stacks.log`. A `pause` or `cancel` file in the folder stops it; a paused job keeps `resume.pt` and continues from it.

## The stored delta

```text
DATA/finetunes/laya-ft-policy-topics/
  lora.safetensors   the LoRA tensors (fp32)
  lora.json          {"root", "r", "alpha", "dropout", "targets"}
  head.safetensors   trained head weights, when the head was trained
  manifest.json      base model, name, temperatures, before/after scores, data summary, versions, parity result
```

`finetunes.refresh()` turns every manifest into a catalog entry: the released model's entry with the fine-tune's id, name and temperatures, so downloads, load options and limits follow the released model. A worker loads the released model, then `attach` injects the LoRA unmerged and loads the head; merging into bf16 weights would round the change away. `finetunes.calibrate` applies the fitted temperature to every answer.

`GET /api/finetunes/{id}/export` zips the folder without local paths; `POST /api/finetunes/import` accepts only those four file names, checks every tensor file's header and refuses a base model the studio doesn't have.

## Run a training from the command line

```bash
python -m basal.training.job --model julia-1 --data examples.csv --out runs/julia-test
python -m basal.training.job --model kev-4b --data decisions.jsonl --out runs/kev --override '{"lr": 5e-5}'
```

`--override` changes any `Recipe` field. `--no-replay` and `--no-parity` exist for experiments. An accepted fine-tune is saved to `DATA/finetunes/` and appears in a running studio's model list on its next refresh. `BASAL_TRAIN_LOCK` names another lock file, so a small development run can go beside a long one; the studio never sets it.

## Add a family

1. Write `basal/training/families/<family>.py` with a `FamilyTrainer` subclass and register it in `families/__init__.py` under the adapter's key.
2. Move the adapter's input building into a function that `decide()` and the plugin both call.
3. Add `tests/training/test_<family>.py`: a tiny random model with the real tokenizer that is scored, trained two steps, exported, attached to a fresh adapter, and must answer like the trained model.
4. Run a real job on a task the model starts weak on, with `--seed 0` and `--seed 1`, and check the guard change in `result.json`. `training_research/trainer_verification/guard_flips.py <run>` shows which general answers moved.
5. Add its memory and speed to `TRAIN_GB` and `SEC_PER_Q` in `manager.py`, measured from the job's own step times.

## Tests

`tests/training/` holds the trainer's tests. The data format, metrics, gate, device policy, queue and supervisor need no PyTorch and run in CI; each family's contract test needs PyTorch and the model's library. `scripts/e2e_train.py` drives the Train page in a browser from the example file to an answer in the Playground. See [Testing](/docs/dev/testing).
