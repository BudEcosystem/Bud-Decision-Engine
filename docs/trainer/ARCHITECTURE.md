# Trainer module: architecture

**Status:** implemented, 2026-10-01. The sections below are the design as proposed. "Implementation status", right
after the terms, lists where the built trainer differs from it and why. The user and developer guide is
[README.md](README.md).
**Scope:** fine-tuning the studio's decision models on the user's own labelled examples, locally, on the machine
the studio runs on (an NVIDIA GB10, a discrete NVIDIA or AMD GPU, an Intel Core Ultra or Arc GPU, an Apple Silicon
Mac). The trainer is switched off on machines that only have a CPU.
**Evidence base:** `training_research/` (per-model analyses, 20-step fine-tunes of every model family on the GB10,
and `training_research/trainer_design/device_research/FINDINGS.md` for Intel, Apple and AMD).

Terms used below:

- **Decision model**: reads a situation (the *state*) plus typed questions and returns a probability for every
  allowed answer.
- **Family**: the models that share an adapter in `basal/adapters/` (`julia`, `laya`, `kev`, `gliner`, `intern`, `lev`,
  `clm`, `jev_omni`).
- **Fine-tuning**: continuing to train released weights on new examples.
- **LoRA** (low-rank adaptation): the released weights stay frozen and small added matrices are trained beside chosen
  layers. A LoRA file is megabytes where the full model is gigabytes, and training needs far less memory.
- **Head**: the small layer that turns the model's internal vectors into one score per option.
- **Delta**: what a fine-tune changes, stored separately from the base model. Here that is a LoRA adapter, a head,
  and a calibration temperature.
- **Accelerator**: a GPU usable by PyTorch: `cuda` (NVIDIA, and AMD through ROCm), `xpu` (Intel), `mps` (Apple).

---

## Implementation status (2026-10-01)

The trainer is built as designed in its main lines:

- one engine with a thin plugin per family;
- the option log-probability contract;
- LoRA deltas on a cached base, attached unmerged;
- one job process at a time;
- device gating;
- a release gate with a parity check.

Where the build differs from the sections below:

| Design | Built | Why |
|---|---|---|
| Modules `data.py`, `capability.py`, `planner.py`, `calibrate.py`, `export.py` | `dataformat.py`, `device.py`, `manager.py`, `metrics.py`; export is `FamilyTrainer.export` | fewer files; the planner shrank (next rows) |
| A planner choosing micro-batch, accumulation and sequence cap per device | per-family recipes, a token budget per micro-batch, and recovery from out-of-memory errors: free the operating system's file cache, retry, then halve the budget | measured peaks were far under the GB10's memory; recovering at run time covers devices nobody measured |
| Presets Quick / Standard / Thorough | none: passes scale with the data (`engine.epochs_for`) and early stopping decides | the user is not expected to know what a pass is |
| Kev and Lev: train their published LoRA further | Kev: the published LoRA is merged at load, exactly as Kev's own loader serves it, and a new zero-initialised LoRA is trained on top. Lev: its published LoRA is trained further in place, unmerged, and the whole adapter is the delta (170 MB, the release's own size) | the training starting point must be the model users run: Kev serves merged, while merging Lev's adapter in bf16 moved its answers by up to 0.053 (6 of 120 flipped) |
| Forgetting: replay with distillation; a guard drop flagged, not blocked | replay with distillation, plus a **drift monitor**: held-out replay examples whose divergence from the released model is added to the checkpoint score, so the kept step learned the task while moving general answers least. A guard drop **blocks** release when it is clear, judged by a paired bootstrap on the same guard questions: ≥ 2 points with probability ≥ 0.8, ≥ 3 points in any case, or ≥ 1 point (clear) when the task gain is less than three times the drop | the first Julia run learned the task (51% to 69%) and lost 4 points on the guard; the user should never receive that model. When the gate finds forgetting, the job trains once more from the released model with half the learning rate and twice the distillation and drift weights before giving up |
| Temperature applied in `contract.build_answers` | applied in `basal/worker.py` through `finetunes.calibrate`, after the adapter answers | keeps `contract.py` untouched; same effect |
| Parity: 32 inputs through `/decide` on a worker | the delta is loaded with `finetunes.load_adapter` (the worker's code path) in the job process, and 12 test examples are compared; the reference is computed per example, the way the studio serves | scoring many examples per batch under bf16 changes probabilities by up to 0.04 through padding alone, which is not what parity tests (a per-example reference matches to 0.0) |
| Julia: LoRA plus head, 1e-4 | LoRA only, at 5e-5; the decision head stays frozen | with the head trained, or at 1e-4, Julia lost 2 to 4 points on the guard and was rejected; at 5e-5 with the head frozen, five of six runs on two tasks were accepted with gains of 15 to 19 points ([RESULTS.md](RESULTS.md)) |
| A guard of a few hundred questions | every guard question (about 750) for the models that answer one question per pass and evaluate in milliseconds; 160 or 80 examples for the slower ones | with 300 questions the paired change had a standard error of about 1.2 points, enough to flip the gate between two runs of the same recipe |
| Replay for every family alike | a family can cap its replay (`Recipe.max_replay`); CLM caps it at 1,500 | each CLM replay question costs one pass of the 8B encoder, and its many cheap passes over the data asked for as many replay questions as training questions |
| Delta: LoRA, head and temperature | heads the recipe kept frozen are not saved; `result.json` records the job's peak GPU memory | a frozen Laya head added ~100 MB of unchanged weights to every fine-tune |
| Replay share per batch | the replay share accumulates across micro-batches | rounded per micro-batch, one-example micro-batches (the large models) got no replay at all |
| Watchdog: pause and exit | pause in place: training waits, then continues by itself after 30 s of healthy memory (gives up after an hour); pause and cancel from the UI write marker files; `resume.pt` lets a stopped job continue | on a shared-memory machine the pressure usually comes from another program and passes |
| Replay pack: a fixed sample | the replay set scales with the run (each replay question comes round about twice at most), from a pack of 2,470 examples and 8,114 questions | with 500 replay questions seen 8 times each, the model fitted them exactly and still drifted elsewhere: Julia lost 2.7 guard points even at four times the distillation weight |
| One job at a time | one job at a time, first come first served (a ticket queue beside the lock file) | a plain file lock goes to whichever waiter polls first |
| (not in the design) | the job process is a supervisor holding the lock; the engine runs in its child and touches a heartbeat file from its main thread. Silence for 15 minutes (30 while loading or verifying) ends the child and starts it again, up to twice. Weight snapshots during training stay on the GPU, and the final copy of the weights to host memory goes through page-locked buffers | on the GB10 under heavy swapping, a plain GPU-to-host copy of the trained weights hung twice with the GPU idle (once mid-training, once while saving Kev 4B after 2.5 hours); a hung job would otherwise hold the lock forever |
| Out-of-memory: halve the micro-batch once, then fail | every job gets a hard GPU memory cap (twice its estimate, or 8 GB more, never into the last 4 GB of the machine). Past it, batches are halved; if a single example still doesn't fit, the model switches to activation checkpointing once, and an example that doesn't fit even then is left out of training (more than 10% left out stops the job with a plain message) | on the GB10's shared memory, nothing stopped GLiNER from growing to 66 GB on long texts (estimate 12 GB), and every other program on the computer stalled; with the cap the same run trained in about 5 GB |
| (not in the design) | `device.reclaim`: before loading and on an out-of-memory error, briefly allocate and free host memory to make Linux drop its file cache, stopping as soon as the kernel starts swapping other programs out instead | on the GB10, CUDA allocations failed with 40 GB "available" because most of it was file cache |
| (not in the design) | a GPU self-test before every job: the same small network on the GPU and the CPU must agree | a broken driver or backend must not produce a broken model |

---

## 1. Decisions in one page

1. **One training engine, eight thin family plugins.** A single PyTorch loop in `basal/training/` owns data, batching,
   precision, the optimizer, the schedule, checkpointing, evaluation, calibration and export. Each family
   contributes about 150–250 lines: how to build a trainable model, how to encode an example, and how to get
   *differentiable* per-option scores. No publisher training stack is used: no XTuner, Modal, `kev.train`,
   `gliner2` `ExtractorTrainer`, Lev's `run_training` or CLM's `finetune.py`. The publishers' *model code* (the modules
   that define each network and its input format) is reused, because it defines exactly what the studio serves.
2. **The contract every family meets: option log-probabilities.** For a batch of questions, return, per question, the
   log-probability of each allowed answer, in the studio's key order (`basal/contract.Q.keys`). Every family already
   has a differentiable function that produces this (section 4). The loss, the metrics and the calibration are
   therefore written once.
3. **LoRA by default, everywhere it applies.** Every family trains a LoRA adapter plus its small head. The exception is
   CLM, whose 8B backbone stays frozen and whose two small heads (19M parameters) are the whole trainable part. Full
   fine-tuning exists only as an advanced option for the three small encoders (Julia, Laya, GLiNER). Frozen base
   weights are held in bf16, half the memory of fp32; the trainable weights are kept in fp32. GLiNER's DeBERTa stays
   in fp32 until bf16 is validated (section 8).
4. **A fine-tune is a delta on a cached base.** It is stored under `DATA/finetunes/<id>/`: a LoRA adapter, head
   weights, a calibration temperature and a manifest, usually 5–150 MB. The base weights stay in the Hugging Face
   cache and are shared with the original model. At load time the family plugin attaches the delta to the base
   model the studio already knows how to load. By default the LoRA stays *unmerged*: a spike on Julia 1
   (Appendix A) showed that merging it into the weights and then serving in bf16 shifts probabilities by up to 0.06,
   while unmerged serving reproduces the trained model exactly.
5. **Serving and training use the same input encoding.** Each adapter's "request to model input" step becomes a
   function that both `decide()` and the trainer call. A fine-tune is then trained on exactly the text the studio will
   send it. The research found two real mismatches this removes: Julia is served indented text while it was trained
   on JSON (73% becomes 56%), and GLiNER's training prompts must mirror the adapter's by hand.
6. **Calibration moves into the studio.** A fitted temperature per question type (`choice`, `score`, `noul`) is
   stored in the manifest and applied by `contract.build_answers`, the same place the per-request temperature is
   applied today. Every family gets calibration the same way, including the three that have none today (Julia,
   GLiNER, CLM).
7. **The trainer runs as its own process, one job at a time.** This mirrors the model workers and the download
   queue: a training job is `python -m basal.training.job`, in its own process group, reporting progress through an
   events file. Cancel stops the group. A memory watchdog *pauses* the job (checkpoint, then exit) instead of letting
   a shared-memory machine swap. Resume continues from the checkpoint.
8. **Gated by hardware, sized by a planner.** The trainer is enabled only when the installed PyTorch reports an
   accelerator. It is off on `cpu`, per the requirement, and on NPUs, which are inference-only.
   Each device class is On, Experimental or Off (section 8):
   - **On**: NVIDIA Ampere or newer, including the GB10, and Apple M2 or newer.
   - **Experimental** (opt-in): Intel Arc and the newer Core Ultra iGPUs, AMD on Linux, and NVIDIA Turing.
   - **Off**: older GPUs and Meteor Lake graphics.

   A planner estimates memory and time for each model, method and device from measured numbers, then picks
   micro-batch, gradient accumulation, gradient checkpointing and sequence cap to fit. It refuses, with a reason,
   what cannot fit.
9. **Every fine-tune is proven before it is offered.** The job reports the untouched model and the fine-tune on the
   same held-out split, a forgetting check on a general guard set, calibration before and after, and a *serving
   parity check*: the finished delta is loaded through the real worker, and its probabilities must match the
   engine's on sample inputs.

---

## 2. Where it fits

```
UI: Train page (ui/js/pages/train.js)            also: "Improve with training" from the Evaluate page
      | HTTP
      v
studio server (basal/server.py)
  +- /api/training/*  ---------------------->  TrainingManager (basal/training/manager.py)
  |                                               queue (one job at a time), events, cancel / pause / resume
  |                                               planner: will it fit now? (basal/training/planner.py)
  |                                               |
  |                                               |  spawns
  |                                               v
  |                                      training job process (python -m basal.training.job <dir>)
  |                                               engine.py  --- the loop, precision, checkpoints, watchdog
  |                                               data.py    --- canonical records, splits, replay, augmentation
  |                                               families/<family>.py --- build, encode, option_logprobs, export
  |                                               calibrate.py / metrics --- before/after, temperature, forgetting
  |                                               export.py  --- DATA/finetunes/<id>/ (delta + manifest)
  |
  +- basal/finetunes.py: scans DATA/finetunes, adds derived ModelSpecs to the catalog
  +- workers (unchanged process model): basal.worker loads base + attaches the delta via the family plugin
```

The trainer never runs inside the server process, as with model workers. A crash or an out-of-memory error ends one
job and leaves the studio untouched.

---

## 3. The family contract

```python
# basal/training/families/base.py
class FamilyTrainer(Protocol):
    family: str                                   # matches ModelSpec.adapter

    # What this family can do on this device (drives the UI and the planner).
    def capabilities(self, spec: ModelSpec, device: DeviceProfile) -> FamilyCaps: ...
        # methods: ("lora", "head", "full"?), default method, max options/questions/tokens, modalities,
        # warm_start ("continue-released-adapter" for Kev and Lev), needs ("cuda-only" for Jev-Omni), memory model

    # Build the base model (from the Hugging Face cache, frozen, bf16) with LoRA injected and the head trainable.
    def build(self, spec: ModelSpec, device: DeviceProfile, method: MethodConfig) -> TrainableModel: ...

    # One training item per primitive question, or per record for families that score several questions in one pass.
    # MUST call the same encoding function the serving adapter uses (principle 5).
    def encode(self, rec: NormalisedRecord) -> list[Item]: ...
    def collate(self, items: list[Item]) -> Batch: ...

    # The heart of the contract: per question, log-probabilities over q.keys, differentiable.
    def option_logprobs(self, model: TrainableModel, batch: Batch) -> list[Tensor]: ...

    # Optional family-specific extras, each off by default: extra loss terms, augmentations (option shuffling,
    # none-of-the-above insertion), a precompute phase (CLM embeds every text once).
    def hooks(self) -> FamilyHooks: ...

    # Write the delta (adapter + head) in this family's layout; attach it to a loaded serving object.
    def export(self, model: TrainableModel, out: Path) -> DeltaFiles: ...
    def attach(self, adapter: "Adapter", delta: Path) -> None: ...
```

Why log-probabilities rather than logits: a few readouts are not a plain softmax. Lev's yes/no answer is the expected
value of a 9-level rating, and Jev-Omni masks unused slots. Log-probabilities cover every family. They also line up
with calibration: the studio already applies temperatures as `p ** (1/T)` (`contract.apply_temperature`), which is
the same as dividing log-probabilities by T. So a temperature fitted on the engine's outputs is exactly the one
the studio applies.

Loss (written once): soft cross-entropy `−Σ_k target_k · logp_k`, averaged over questions. A hard label is a one-hot
target; a soft label (for example the averaged answers of several labellers) is a distribution. An optional ordinal
term for `score` questions is available as a hook.

---

## 4. The eight family plugins

Each row names the differentiable entry point the plugin calls. Each was verified by reading the installed or
cloned code. Each was also exercised by a real fine-tune on the GB10 during the research, through the publisher's or
the research's own training script, which calls the same entry point. The plugin wraps these entry points; it does
not reuse the publishers' training loops.

| Family (models) | Differentiable entry point | Trainable by default | LoRA targets | Head (trained in full) | Delta format and `attach` |
|---|---|---|---|---|---|
| `julia` (Julia 1, 144M) | `julia/model.py:36` `JuliaDecisionModel.forward`, inputs from `julia/data.py:72` `sequence` + `Collator` (shipped in the model repo) | LoRA r16 + head | ModernBERT `Wqkv`, `Wo`, `Wi` | decision head, type embeddings, scorer; `act_head` frozen (untrained, unused) | PEFT adapter + head tensors; attach to `engine.model` (unmerged by default; verified in Appendix A) |
| `laya` (Laya, Laya Multilingual, Laya Typed-Decisions) | `laya/common.py:353` `DecisionModel.forward`, inputs from `common.py:135` `build_sequence` | LoRA r16 + head | ModernBERT / mmBERT `Wqkv`, `Wo`, `Wi` | 2-layer head, type embedding, scorer; act head frozen | same; attach to `agent.model` |
| `gliner` (GLiNER2.5 Decide) | `gliner2/models/span/model.py:169` `forward` (classification logits at `:383`) | LoRA r16 + classifier | DeBERTa `query_proj`, `key_proj`, `value_proj`, dense layers | classifier | same; attach to the extractor's encoder. Full fine-tuning is a strong alternative here: LoRA saved no memory at batch 8 (14.2 GB vs 12.8 GB) |
| `kev` (Kev 0.5B, Kev 4B) | `kev/model.py:362` `DecisionModel.forward` / `forward_batch`, pointer head `:193` | **continue the released LoRA** (same rank, same targets) + pointer head | as released (`all`: attention, MLP, Gated DeltaNet projections) | pointer head | Kev's own checkpoint layout (`adapter_*`, `head.pt`); attach by pointing `Checkpoint` at the delta folder |
| `intern` (Intern-Decision 4B, and 0.8B/2B if added) | Hugging Face `Qwen3_5ForConditionalGeneration` forward; logits at the row before each `<decision>` marker, restricted to the question's option letters | LoRA r16 | language-model `q/k/v/o/gate/up/down` + linear-attention `in_proj_qkv`, `in_proj_z`, `out_proj`; vision tower frozen | none (the readout is the language-model head, tied to the embeddings, frozen) | PEFT adapter; attach to `engine.model` (unmerged by default) |
| `lev` (Lev) | `lev/train/loop.py:124` `candidate_logits(model, batch, head)` with `build_model` `:29`, `build_head` `:180` | **continue the released LoRA** (r32) + Mode-B head | as released (q/k/v/o in full-attention layers, MLP everywhere) | Mode-B head (used above 68 options) | Lev's release layout; attach with `lev.load(delta)`. Training restricted to 68 options or fewer by default (above that, training and serving use different paths) |
| `clm` (CLM 8B) | the two projection heads (`clm/heads.py`), on embeddings from the frozen Qwen3-8B | **heads only** (19M parameters); no LoRA, so embeddings can be cached | none | state head, action head | a heads file; attach by giving the engine that file |
| `jev_omni` (Jev-Omni 12B) | `decision_features` + positional head, following `jev_omni.py` (reconstructed and trained in `training_research/models/jev-omni/smoke/train_jev_omni.py`) | LoRA r16 + head | Gemma 4 language-model `q/k/v/o/gate/up/down` (48 layers) | linear head; its stored mean and spread stay frozen | PEFT adapter + head; attach to the loaded model **without merging to disk** (a merged export would be another 24 GB) |

Warm-start rule. Kev and Lev ship *as* LoRA adapters, so their fine-tunes continue training those adapters rather
than stacking a second one. Kev's own evidence: a fine-tune that did not start from the released adapter fell from 0.83
to 0.33 on Kev's general suite. The other families ship full weights, and a new LoRA starts at exactly zero change,
that is, at the released model.

Family hooks carried over from the research, each small:

- Kev: none-of-the-above minimal pairs; state length must be raised past its 384-token default, or long records are dropped silently.
- Lev: yes/no as a 0–8 rating read out as p(yes); option shuffling.
- Laya and Julia: choice option shuffling, because the published scripts do not shuffle.
- GLiNER: the adapter's prompt rules, with unique task names that are never prefixes of each other.
- CLM: the embedding precompute phase and an embedding cache keyed by text hash.
- Intern-Decision: no option shuffling by default, because its answers are positional letters and production order must match.

---

## 5. The data layer (`basal/training/data.py`)

**Canonical record.** This is the studio request plus answers. It is the shape of the public
`LocalLLaMA/typed-decisions` dataset.

```json
{"id": "t-0001", "group": "optional: records sharing a state",
 "state": "text | object | array",
 "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?", "criteria": {"billing": "...", "other": "..."}},
               "urgent": {"type": "noul", "instructions": "Does this need attention within the hour?"}},
 "answers": {"team": "billing", "urgent": false},
 "media": [{"type": "image", "path": "uploads/abc.png"}]}
```

- **Answers**:
  - choice: the option name; noul: true or false; score: the level index.
  - Soft labels are written as `{"probabilities": {...}}`.
  - The studio extension types map onto primitives through `contract.normalise`: pick-any becomes a list of names, one yes/no per option; rank uses its top option; estimate-a-number snaps a value to the nearest level.
- **Importers**:
  - Evaluate's formats: tab-separated `text<TAB>label`, CSV with `text` and `label` columns, JSON lines with `text`/`state` and `label`/`answer`. These carry one question, which the UI defines, so a user can train on the file they just evaluated with.
  - Multi-question JSON lines in the canonical shape.
  - The typed-decisions parquet shape.
- **Validation, reported and never silent**:
  - every label is one of its question's options;
  - option counts and question counts are within the model's limits;
  - each item fits the model's token limit (measured with the family's own encoder);
  - media types are ones the model reads.
  
  Items that fail are listed with the reason. The job refuses to start when more than a threshold is dropped. The research found three silent-drop traps in publisher code (Kev, GLiNER, Lev) that this rule prevents.
- **Splits**: by `group` or state hash, default 70/15/15 into train, calibration and test; user-supplied splits are
  honoured. Minimums: 100 calibration questions to fit a temperature (below that, T stays 1 with a warning, because a
  40-question fit made calibration worse in the Intern-Decision test), and 100 test questions to report accuracy.
- **Balance report**: questions per option, flagging any option under 30 examples.
- **Replay**: general decision data mixed in so the model keeps its general ability. The research found narrow
  fine-tunes erasing it (Lev's first narrow run fell below its untouched backbone).
  - The data is a small replay pack published by Bud as a Hugging Face dataset, built only from sources whose licence allows it (typed-decisions is Apache-2.0), and downloaded on first use.
  - Default ratio: 30% of the task data for most families, 50% for Lev.
  - The benchmark sets used for the forgetting check are never used for replay.
- **Augmentation** is per family and off where it would break parity (above).

Datasets are stored under `DATA/datasets/<id>/`, with the original file, the normalised records and the
validation report. They never leave the machine.

---

## 6. The engine (`basal/training/engine.py`)

A plain PyTorch loop. The Hugging Face `Trainer`, Accelerate and TRL are not used: they assume a language-model loss,
a data-centre environment, or both, and the loop needed here is short. PEFT, already a studio dependency, supplies
LoRA; it is pure PyTorch and device-agnostic.

- **Optimizer**: AdamW with separate learning rates for LoRA and head parameters, weight decay 0.01, gradient
  clipping at 1.0. The implementation variant comes from the device profile (fused, or `foreach=False` on Intel, or
  non-fused on Apple with torch 2.11).
- **Schedule**: linear warm-up (5%), then cosine decay to 10%.
- **Batching**: token-budget batches grouped by length. A micro-batch and gradient accumulation are chosen by the
  planner to reach the preset's effective batch size.
- **Precision** (`DeviceProfile.autocast_dtype`):
  - bf16 autocast where the device supports bf16 training; otherwise fp16 with a gradient scaler, or fp32.
  - Frozen base weights are loaded in bf16, halving memory. On the GB10 this also avoided a transient allocation
    failure that hit 5 of 6 fp32 loads of Kev 4B.
  - Trainable weights and optimizer state are fp32. With bf16 master weights, small updates are lost; the
    Intern-Decision analysis flagged this.
- **Memory**: gradient checkpointing when the planner asks. On an out-of-memory error during a step, the engine
  halves the micro-batch once, doubles accumulation, and retries before failing.
- **Evaluation** every N steps on the calibration split: log loss, accuracy, ECE. The best step by log loss is kept
  (early stopping with patience).
- **Checkpoints**: LoRA, head, optimizer and schedule state, plus data order, every N steps and on pause. This is
  small, because the base is never saved.
- **Watchdog**: a thread reads available memory every 2 s through the device profile. If it stays under the floor
  (default 4 GB) for 6 s, the engine checkpoints and exits with status `paused (memory)`. On the GB10, other programs
  took 37 GB within minutes during the research. An optional thermal check pauses above a temperature limit where the
  platform reports one.
- **Reproducibility**: the seed, the library versions and the device are recorded in the manifest.

**Presets** (the user picks one; Advanced exposes the fields):

| Preset | Passes over the data | LoRA rank | For |
|---|---|---|---|
| Quick | 1 | 8 | trying the data; minutes |
| Standard (default) | 3, early stopping | 16 | most tasks |
| Thorough | 5, early stopping | 32 | larger datasets (thousands of questions) |

Starting learning rates per family come from the research (Laya 2.5e-5 encoder in full mode, LoRA 1e-4; Kev 2e-5;
Lev 5e-5; Intern 1e-4 LoRA; heads 1e-4 to 5e-4). They are settings in each plugin, and Phase 1 (section 13) tunes them.

---

## 7. Hardware: gating, profiles and the planner

### 7.1 Gating (`basal/training/capability.py`)

```python
def training_enabled() -> tuple[bool, str]:
    backend = config.load().get("backend")            # what the installer set up: cuda | rocm | xpu | mps | cpu
    if backend in (None, "cpu"):
        return False, "Training needs a GPU. This computer runs models on the processor."
    probe = run_in_subprocess(DEVICE_PROBE)          # never import torch in the server; returns name, capability,
    if not probe.ok:                                  # matrix-engine flag, memory, is_integrated
        return False, f"The installed PyTorch cannot use the {device_name()}. Run setup again."
    tier = matrix.classify(backend, probe, os_name())   # section 8: "on" | "experimental" | "off"
    if tier == "off":
        return False, matrix.reason(backend, probe)      # e.g. "Intel Meteor Lake graphics can't train these models."
    if tier == "experimental" and not settings.get("experimental_training"):
        return False, "Training on this GPU is experimental. Turn it on in Settings to try it."
    return True, ""
```

The server never imports PyTorch (`basal/config.py` states this), so the probe runs once in a short subprocess and is
cached. With training disabled, the Train page explains why and the training endpoints return 409 with the same
reason. Nothing else in the studio changes on a CPU-only machine.

### 7.2 Device profile

Built once per job, inside the job process, where PyTorch is available.

- **kind**: `cuda`, `rocm`, `xpu` or `mps`.
- **unified**: whether memory is shared with the system (GB10, Apple, Intel integrated, AMD APUs).
- **total_gb** and **available_gb()**: the memory a job may plan on. Each platform's obvious API is wrong on
  shared-memory machines (research §6.3), so the rules are:
  - **NVIDIA discrete**: `torch.cuda.mem_get_info()` free.
  - **GB10 and other unified NVIDIA**: `psutil` available. `mem_get_info()` reports only MemFree here; one reading was
    38 GiB free while 65 GiB was available.
  - **AMD APU** (`is_integrated`): the minimum of `mem_get_info()` free and `psutil` available, because
    `mem_get_info()` can exceed the real total.
  - **Apple**: `recommended_max_memory() − driver_allocated_memory()`, capped by `psutil` available. Call
    `set_per_process_memory_fraction(1.0)` first so an overrun raises out-of-memory instead of swapping the Mac.
  - **Intel**: `torch.xpu.mem_get_info()` when it works (it raises on Meteor Lake), else the device total. On Linux
    iGPUs, cap at half of RAM. Call `set_per_process_memory_fraction(1.0)` so an overrun is a catchable error.
  - `nvidia-smi` is never used: it reports "Not Supported" on the GB10.
- **autocast_dtype**: chosen from hardware capability, never from `is_bf16_supported()`. That call returns True on
  Turing (emulated, about 6× slower), on every ROCm GPU and on every Intel GPU (section 8).
- **optimizer**: fused AdamW on NVIDIA and AMD; `foreach=False` on Intel (a field report of GPU hangs with
  `foreach=True`); non-fused on Apple with torch 2.11 (a fused-Adam bug fixed in 2.13).
- **fast_kernels**: whether the Triton kernels for Qwen3.5's linear-attention layers (flash-linear-attention, "fla")
  can run. Where they cannot, the job hides the package (`sys.modules["fla"] = None`) before importing transformers,
  because an importable-but-unusable fla crashes.
- **max_single_alloc_gb**: 4 GB on Intel client GPUs. Plugins keep every tensor below it; Intern-Decision, for example,
  computes logits only at the answer positions, never over the full 248k-token vocabulary for every token.

### 7.3 Device support matrix

Section 8 gives which devices train which models, at what precision.

### 7.4 The planner (`basal/training/planner.py`)

- **Inputs**:
  - family and model;
  - method and rank;
  - the dataset's token-length distribution, from the encoder;
  - the preset;
  - the device profile;
  - available memory now.
- **Memory model** per family:
  `memory = frozen base (bf16) + trainable params × 16 bytes + activations(micro-batch, tokens, checkpointing)`.
  It is seeded with the peaks measured on the GB10 during the research:

| Family, method (measured) | Peak | Setting |
|---|---|---|
| Julia full / LoRA / head-only | 5.1 / 3.7 / 0.84 GB | batch 8 |
| Laya 421M full / head-only | 8.3 / 2.8 GB | batch 8, ~2,300 tokens |
| GLiNER full / LoRA | 12.8 / 14.2 GB | batch 8 |
| Kev 0.5B LoRA | 4.7 GB | batch 2 × 2 |
| Kev 4B LoRA, bf16 base | 10.8 GB | batch 2 × 4 |
| Intern-Decision 4B LoRA r16 | 10.2 GB | 1 row × 8 |
| Lev 4B LoRA r32 + head | 12.3 GB | batch 16 |
| CLM embedding / heads | 15.9 / 2.0 GB | batch 16 / 512 |
| Jev-Omni 12B LoRA r16 | ~24.5 GB | 1 row × 8 |

- **Budget**: `min(device cap, available now − floor)`. The floor defaults to 4 GB, and 6 GB on unified-memory
  machines. The device cap is 85% of memory on discrete GPUs, about 70% on Apple Silicon, and the platform limit on
  Intel integrated GPUs (see the matrix).
- **Output**:
  - the plan: micro-batch, accumulation, checkpointing, sequence cap;
  - estimated peak memory and time, from measured tokens-per-second per family and device class;
  - a verdict of `fits`, `fits if you eject <models>` (the planner knows what each loaded worker uses), or
    `does not fit on this computer`, with the reason.
- **Calibration of the planner**: every finished job records its actual peak and speed, so the estimates improve on
  each machine over time.

---

## 8. Device support

From `training_research/trainer_design/device_research/FINDINGS.md` (180 saved sources, 2026-10-01), plus the
fine-tunes actually run on the GB10. Each device class is **On** (enabled by default), **Experimental** (enabled after
the user turns on "Experimental training" in Settings, with a warning), or **Off**. Model groups:

- **E**: the encoders (Julia 1, Laya, GLiNER2.5).
- **K0.5**: Kev 0.5B.
- **Q-S**: Qwen3.5 0.8B/2B, for future small Intern-Decision entries.
- **Q-4B**: Kev 4B, Lev, Intern-Decision 4B.
- **CLM**: the frozen 8B embedding pass (the heads themselves are tiny).
- **JEV**: Jev-Omni 12B.

| Device class | Trainer | Precision | Allowed by default | Experimental | Off |
|---|---|---|---|---|---|
| NVIDIA GB10 / unified (Linux) | **On** | bf16 (GLiNER fp32) | E, K0.5, Q-S, Q-4B, CLM, JEV (all trained here) | none | none |
| NVIDIA Ampere or newer, Linux | **On** | bf16 (GLiNER fp32) | by free memory: ≥ 8 GB E + K0.5; ≥ 12 GB + Q-S; ≥ 16 GB + Q-4B; ≥ 24 GB + CLM | JEV at ≥ 32 GB | JEV below 32 GB |
| NVIDIA Ampere or newer, Windows | **On** | bf16 (GLiNER fp32) | E, K0.5, CLM by the same tiers | Q-S, Q-4B (no Triton: 1.7–2.5× slower); JEV at ≥ 32 GB | none |
| NVIDIA Turing | Experimental | fp16 + gradient scaler | none | E, K0.5 | the rest |
| NVIDIA Pascal, Maxwell, Volta | **Off** | | | | all (current wheels ship no kernels for them) |
| Apple M2 or newer, macOS 14+, ≥ 24 GB | **On** | bf16, non-fused AdamW (GLiNER fp32) | E, K0.5 | Q-S, Q-4B (tens of tokens per second); CLM with torch ≥ 2.13 | JEV (its loader needs CUDA) |
| Apple, 16 GB | **On** | same | E, K0.5 | Q-S | Q-4B, CLM, JEV |
| Apple M1, or 8 GB | Experimental | fp32 on M1 | none | E | the rest |
| Intel Arc discrete, Linux | Experimental | bf16, no gradient scaler | none | E, K0.5; Q-S; Q-4B at ≥ 16 GB; CLM at ≥ 24 GB | JEV |
| Intel Arc discrete, Windows | Experimental | same | none | E, K0.5, Q-S | Q-4B, CLM, JEV |
| Intel Core Ultra iGPU: Lunar Lake, Arrow Lake-H, Panther Lake | Experimental | bf16, no gradient scaler | none | E, K0.5, Q-S (0.8B) | Q-4B, CLM, JEV |
| Intel Core Ultra iGPU: Meteor Lake, Arrow Lake-S/HX, 200U | **Off** | | | | all (no matrix engines, unreliable memory queries, not validated) |
| AMD RDNA3/RDNA4, Instinct, Strix Halo (Linux) | Experimental, **after the installer fix below** | bf16 | none | E, K0.5, Q-S, Q-4B; CLM (Strix Halo, or ≥ 24 GB) | JEV |
| AMD on Windows; RDNA2 and older | **Off** | | | | all |
| Any NPU (Intel, AMD, Apple Neural Engine) | **Off** | | | | inference-only runtimes |
| CPU only | **Off** | | | | product requirement |

The tiers are memory *floors*: the planner (section 7.4) still sizes every job against memory available at that
moment. Math-only attention devices (Apple, Intel, and AMD without the experimental AOTriton flag) get 30% extra
headroom in the planner, because their attention memory grows with the square of sequence length.

**Rules that ship with the matrix:**

1. **Precision from capability.**
   - NVIDIA compute capability ≥ 8.0 → bf16; Turing → fp16 with a gradient scaler.
   - Intel with matrix engines (`has_subgroup_matrix_multiply_accumulate`) → bf16, no scaler: the scaler needs FP64,
     which Arc A-series lacks.
   - AMD gfx11, gfx12 and Instinct → bf16.
   - Apple on macOS 14+ and not M1 → bf16.
   - GLiNER's DeBERTa → fp32 everywhere until bf16 is validated per device. It also has no SDPA and always runs
     eager attention.
2. **A start-up self-test in every job.** A tiny forward, backward and AdamW step with the chosen precision, compared
   against the CPU. It catches known silent failures before hours are spent:
   - SDPA corruption on custom sm_121 builds;
   - AMD's AOTriton returning zeros on Windows gfx1151;
   - the MPS mixed-precision gradient bugs in torch 2.11;
   - Intel DEVICE_LOST hangs.
3. **Persistent Triton cache** (`TRITON_CACHE_DIR` under `DATA/`). The first compile of the Qwen3.5 kernels took
   more than 140 s against 12.8 s warm on an AMD 890M.
4. **Always pass `torch_device` to PEFT when loading an adapter.** PEFT otherwise infers CUDA ahead of the model's
   real device (PEFT issue #3793, open).
5. **Slow paths are labelled, not hidden.** Where the Qwen3.5 fast kernels cannot run (Apple, Intel, AMD without
   tested fla, NVIDIA on Windows), the model shows as "slow on this computer" with the planner's time estimate.

**Prerequisites in the installer.** These are needed before the Experimental rows can work, and two fix bugs that
affect users today:

- **AMD installs a CUDA build today.** `installer/engine.py:248` points at the `rocm6.4` index, which stops at
  torch 2.9.1 (checked 2026-10-01), while the installer pins 2.11.0 (`:37`). The install resolves to PyPI's CUDA
  wheel. Use `rocm7.1`, which has 2.11.0 through 2.13.0 (checked), or AMD's multi-arch index for Strix Halo.
- **CUDA wheel choice ignores the GPU generation.** It is chosen from the driver's CUDA version, so Pascal, Maxwell and
  Volta cards on driver branch R580 get cu130 wheels that have no kernels for them. Choose from compute capability.
- **Record memory size and the shared-memory flag for Intel and AMD.** They are `None` today. The Intel regex also
  marks Arc Pro B50/B60/B70 as integrated.
- **Version floors for training**:
  - torch ≥ 2.12 on Intel, where `solve_triangular`, used by Qwen3.5's fallback, runs natively instead of on the CPU;
  - torch ≥ 2.13 on macOS, which fixes three MPS training-correctness bugs.
  
  These can be training-only floors if inference stays on 2.11.

---

## 9. Evaluation, calibration and the release gate (`basal/training/calibrate.py`)

After training, the job:

1. **Scores the untouched model and the fine-tune on the same test split**, both through the engine: accuracy, log
   loss, Brier score, ECE, and coverage at the user's error budget, per question type and per question. The formulas
   are the Evaluate page's (`ui/js/pages/evaluate.js:216-227`: multi-class Brier, 10-bin ECE), so the Train page's and
   the Evaluate page's numbers agree. Evaluate also already fits a best temperature by grid search (`:230-234`); the
   trainer's fit replaces that with a bounded log-loss fit on a separate calibration split.
2. **Fits the temperature** on the calibration split, per question type when each type has ≥200 items, else one global
   value. The fit is bounded to 0.25–10, and a fit at the bound is reported as a warning. The result is written to the
   manifest.
3. **Runs the forgetting check** on a small general guard set (for example 300 questions of held-out public decision
   data the replay never uses), and reports the change. Past a threshold (default: 2 points of accuracy), the result
   is flagged, not blocked.
4. **Runs the serving parity check.** The delta is loaded through the real worker path (`basal.worker` with the new
   catalog entry), 32 test inputs are sent through `/decide`, and the probabilities are compared with the engine's.
   The fine-tune is offered only if the largest difference is under 0.02. This catches train/serve drift: prompt
   differences, bf16 merge rounding, a missing attach step. The spike in Appendix A tripped this gate exactly as
   intended: a merged delta was off by 0.059, while an unmerged one matched.

The job's result page shows these four blocks. Nothing is hidden behind a single score.

---

## 10. Storing and serving fine-tunes

```
DATA/finetunes/<id>/
  manifest.json      id, name, base model id, family, method (rank, targets), preset, dataset id + hash + counts,
                     metrics (before / after / forgetting / parity), temperature per type, versions (studio, torch,
                     transformers, peft, family library), device, timings
  adapter/           PEFT adapter (adapter_config.json, adapter_model.safetensors)   (Kev and Lev: their native layout)
  head.safetensors   trainable head weights
  train/             events.jsonl, final checkpoint (deleted after success unless kept), plan.json
```

- **Registry** (`basal/finetunes.py`): at startup and after each job, scans `DATA/finetunes/` and adds one derived
  `ModelSpec` per fine-tune to the catalog. It is created with `dataclasses.replace(base_spec, id=..., name=...,
  base_id=..., finetune_dir=..., temperature=...)`.
- **Catalog**: the derived entries appear under their base model on the Models page and in `/v1/models`, so any API
  client can call `"model": "<fine-tune id>"`.
- **Download status**: a fine-tune is "downloaded" exactly when its base is (`hub.model_status` delegates).
- **Serving**: `Adapter.load()` loads the base as today, then calls the family plugin's `attach(self,
  spec.finetune_dir)`. Every adapter gets the same one-line hook. Kev and Lev load their native delta folders
  directly.
- **Merged or unmerged**: the LoRA is attached unmerged by default. Serving then computes exactly what the engine
  computed during training and evaluation (Appendix A: difference 2.4e-8). Merging into the weights saves the small
  extra matrix products, but under bf16 serving it rounds away part of the fine-tuned change (Appendix A: 0.059). It is
  therefore an opt-in speed setting per fine-tune. When it is on, the job's final evaluation and the parity check run
  on the merged form, so the reported numbers describe what is served.
- **Temperature**: `contract.build_answers` applies the spec's temperature before any per-request temperature. The two
  compose by multiplication, since both are powers of p. The same field lets the studio ship fitted temperatures for
  Julia, GLiNER and CLM, which have none today.
- **One temperature, not two**: the family's own temperature is neutralised, so the studio's is the only one and a
  fine-tune is never calibrated twice.
  - For the native layouts, the export does it:
    - Kev: `head.pt` temperature set to 1.
    - Lev: no `calibration.json`.
  - For deltas attached to a base folder, the `attach` step resets the loaded object's temperature, because the base
    folder's own configuration would otherwise apply:
    - Laya: the agent's temperatures set to 1 and `temperature_by_options` cleared. This also removes the research's
      Laya trap, where inherited buckets override a new fit.
    - Intern-Decision: `engine.temperature = 1`, overriding `DEFAULT_TEMPERATURE = 1.99` from the base's
      `inference.py:21`.
  
  The engine computes `option_logprobs` at temperature 1 for the same reason.
- **Deleting** a fine-tune removes its folder; the base model is untouched.

---

## 11. API and interface

| Method and path | Purpose |
|---|---|
| `GET /api/training/capabilities` | enabled or not (with reason), device, memory now, and per model: trainable here or not, default method, planner estimate |
| `POST /api/datasets` (file upload), `GET /api/datasets/{id}` | import, normalise, validate, split; returns the validation and balance report |
| `POST /api/training/plan` | planner verdict for a model, dataset and preset, without starting |
| `POST /api/training/jobs` | start (or queue) a job: `{base_model, dataset_id, preset, overrides}` |
| `GET /api/training/jobs`, `GET /api/training/jobs/{id}` | status, stage, progress, live curves (loss, calibration-split log loss and accuracy), memory, estimated time left |
| `POST /api/training/jobs/{id}/cancel`, `/pause`, `/resume` | job control |
| `GET /api/finetunes`, `DELETE /api/finetunes/{id}` | list and remove fine-tunes (they also appear in `/api/state` models) |

Every mutating route requires the header `X-Basal-Client: 1`, like downloads and loads today.

**Train page** (`ui/js/pages/train.js`), one job per screen, following the product's principles:

1. **Model**: only models trainable on this device, each with the planner's estimate. On a CPU-only computer, the
   page explains that training needs a GPU, and nothing else.
2. **Data**: upload, or reuse the file from Evaluate. Shows the validation and balance report and the split.
3. **Settings**: preset, with an Advanced section for rank, passes, learning rate and replay ratio.
4. **Run**: live curves, memory, time left; Cancel and Pause.
5. **Result**: the four blocks from section 9. Actions: Load, "Compare in Evaluate", Delete.

Evaluate gains an **Improve with training** action that carries its question and file into step 2.

---

## 12. Module layout and changes to existing code

New:

```
basal/training/__init__.py
basal/training/capability.py    gating + DeviceProfile
basal/training/data.py          canonical records, importers, validation, splits, replay, augmentation
basal/training/engine.py        the loop, precision, checkpoints, watchdog
basal/training/planner.py       memory and time estimates, batch plan
basal/training/calibrate.py     metrics, temperature fit, forgetting check, parity check
basal/training/export.py        delta writer + manifest
basal/training/job.py           job process entry point: python -m basal.training.job <job dir>
basal/training/manager.py       server side: queue, events, cancel / pause / resume
basal/training/families/{base,julia,laya,gliner,kev,intern,lev,clm,jev_omni}.py
basal/finetunes.py              registry of fine-tunes -> derived ModelSpecs
ui/js/pages/train.js
tests/training/                 see section 14
```

Changed:

- **`basal/catalog.py`**: `ModelSpec` gains `base_id`, `finetune_dir`, `temperature` (per type) and `trainable`.
- **`basal/adapters/*`**: each adapter's request-to-input step becomes a function shared with the trainer (principle
  5), and `Adapter.load()` gains the attach hook. Julia's adapter passes JSON for structured states, a fix worth making
  regardless.
- **`basal/contract.py`**: `build_answers` applies the spec temperature.
- **`basal/hub.py`**: `model_status` for derived specs.
- **`basal/server.py`**: training routes, `TrainingManager` in the app lifespan, derived models in `/api/state` and
  `/v1/models`, and the `experimental_training` setting.
- **`installer/engine.py`**: the prerequisites in section 8:
  - the ROCm index (a bug today);
  - CUDA wheel choice by compute capability;
  - memory and shared-memory flags for Intel and AMD;
  - the training version floors (torch ≥ 2.12 on Intel, ≥ 2.13 on macOS);
  - installing the fast-kernel packages where section 8 allows them.
- **Dependencies**: none new for the engine (torch, transformers, peft and safetensors are already required). The
  replay pack is a dataset download, not a package.

---

## 13. Implementation phases

| Phase | Scope | Done when |
|---|---|---|
| 0. Foundations | capability gating, device profile, data layer (canonical + Evaluate importers, validation, splits), engine, metrics and calibration, export, registry, attach hook, manager and job process, minimal Train page | the Laya family trains end to end on the GB10 from an Evaluate file, reproduces the published typed-decisions gain in direction (0.36 → above 0.70 with 6,000 questions), passes serving parity, and loads as a catalog model |
| 1. Encoders and Kev | Julia, GLiNER, Kev 0.5B and 4B plugins; shared encoding refactor for those adapters; LoRA-versus-full check for Laya, Julia and GLiNER; learning-rate defaults | each family: parity < 0.02, a before/after on a held-out split, forgetting check reported; LoRA within 1–2 points of full on the Laya benchmark, or the default revisited |
| 2. Decoders and embeddings | Intern-Decision, Lev (68-option guard), CLM (embedding cache and precompute stage) | each family as above; CLM reproduces 0.357 → 0.685 on typed-decisions (already reproduced by hand during the research) |
| 3. Big and other devices | Jev-Omni (CUDA only), media datasets; the installer prerequisites of section 8; validation on Apple Silicon, Intel Arc / Core Ultra and AMD Linux; the per-device self-test | the device matrix's "allowed" cells each have a passing smoke fine-tune on real hardware; planner estimates within 25% of measured |
| 4. Polish | pause and resume, planner self-calibration, Evaluate integration, replay pack publication | as specified in sections 7 and 11 |

---

## 14. Testing

- **Unit** (CI, CPU): data validation and splits, loss and metrics, the planner arithmetic, the temperature fit
  (including composition with request temperatures), and manifest round trips. CPU runs are allowed only with
  `BASAL_TRAIN_ALLOW_CPU=1`, which users never set.
- **Family contract tests** (CI, CPU, tiny random models): for each family, a tiny random model with the real
  tokenizer runs through build → encode → option_logprobs → 2 optimizer steps → export → attach. Then it checks that
  the engine's probabilities equal the serving adapter's on the same inputs. The research already wrote tiny-random
  harnesses for Intern-Decision and Jev-Omni in `training_research/models/*/smoke/`.
- **Smoke fine-tunes** (release QA, per device): 20 steps per family on real weights, the pattern of
  `training_research/_tools/lead_gpu_queue*.sh`, with the numbers compared against the measured table in section 7.4.
- **Parity regression**: the serving parity check runs as a test on a stored fine-tune per family whenever an
  adapter or a pinned model library changes.

---

## 15. Trade-offs made

- **One loop over the publishers' stacks.** We gain one code path to test, the same behaviour on every device, and no
  dependencies that pin old PyTorch (XTuner) or need cloud services (Modal). We lose each publisher's extras:
  - Laya's reward term: an independent comparison found plain soft cross-entropy equal or better.
  - Lev's per-bucket calibration: replaced by the studio's per-type temperature.
  - Kev's calibration scripts.
  - GLiNER's prompt randomisation.
  
  The ones that matter come back as small family hooks. We also take on keeping up with each library's model code,
  and pinning mitigates that. `kev` and `lev` are installed from repository archives at HEAD today and should be
  pinned to commits before this ships.
- **LoRA over full fine-tuning.** Far less memory, and 5–150 MB per fine-tune instead of gigabytes; LoRA also forgets
  less. It can learn slightly less than full fine-tuning on small encoders, so Phase 1 measures that and keeps full
  fine-tuning as an advanced option there. For GLiNER at batch 8, LoRA did not save memory; its gain is storage.
- **Deltas on a cached base over merged checkpoints.** Tiny on disk, the base stays shared, and deleting a fine-tune is
  instant. The cost is a few seconds at load time to attach, slightly slower serving while the LoRA stays unmerged,
  and a dependency on the exact base revision, which the manifest records and the registry checks.
- **Studio-level temperature over each family's own mechanism.** One consistent calibration path, which also fixes the
  three models that have none. The cost is less expressiveness than Lev's per-bucket temperatures.
- **One job at a time.** Simpler scheduling and predictable memory on shared-memory machines. A second job waits in
  the queue.
- **CPU disabled**, as required. CPU fine-tuning of the small encoders was measured as feasible but 30–60 times
  slower than the GB10's GPU. The test-only flag keeps the code path available for CI.

---

## 16. Risks and open questions

- **Library drift.** The plugins call publishers' model code. Mitigations: pinned versions, the family contract tests,
  and the parity regression.
- **Quality on small datasets.** Below about 300 questions, gains mostly sit inside the noise. The data report says
  so, and the result page reports confidence intervals.
- **Memory contention on unified-memory machines.** The planner, the watchdog, pause and resume, and "eject to make
  room" address it; see the research's Jev-Omni incident.
- **Slow Qwen3.5 kernels off NVIDIA.** Kev 4B, Lev and Intern-Decision rely on Triton kernels for speed. Where Triton
  is unavailable, they run on slow reference code and are marked "slow" or disabled by the matrix.
- **Licences.** The replay pack must contain only data whose licence allows redistribution and commercial use;
  each fine-tune's manifest records which replay sources it used.
- **Open**:
  - whether LoRA matches full fine-tuning on the encoders (Phase 1);
  - the right forgetting threshold per family;
  - whether to allow training while the base model is loaded for serving (currently allowed if the planner fits
    both);
  - how to expose soft labels in the UI.

---

## Appendix A: contract spike on Julia 1 (2026-10-01)

To check sections 3, 4 and 10 against real weights before any product code is written, a throwaway script
(`training_research/trainer_design/spike/spike_julia_contract.py`, results in `run_merge0.json` and `run_merge1.json`)
ran the contract end to end on the GB10. It used the studio's real `JuliaAdapter`, a plain PyTorch loop and PEFT:

| Check | Result |
|---|---|
| 1. The engine's differentiable option log-probabilities vs the serving adapter's `decide()`, same input | largest difference **2.6e-8**: identical, and differentiable |
| 2. LoRA r16 on the encoder (`Wqkv`, `Wo`, `Wi`) + head trained by a plain loop, soft cross-entropy on log-probabilities | loss 1.66 → 0.0002 in 8 steps on 6 records (deliberately tiny); **1.05 GB** peak GPU memory; 2.3M LoRA + 3.7M head parameters |
| 3. Delta export (PEFT adapter + head tensors + manifest) | **24 MB**, against 577 MB for the full model |
| 4a. Fresh serving adapter + `attach(delta)`, LoRA **unmerged** | matches the trained model to **2.4e-8**; moves the base's answers by up to 0.94, so the fine-tune is applied |
| 4b. Same, LoRA **merged** into the fp32 weights, served under bf16 autocast | off by **0.059**: merged weights are rounded to bf16 as a whole, losing part of the small fine-tuned change |

What it proves:

- **The contract holds for a real family.** The engine's option log-probabilities equal what the studio serves, they
  are differentiable, and a generic loop trains them through PEFT.
- **Deltas work as designed.** They are small, and they attach to an unchanged serving adapter.
- **The parity gate is necessary.** It caught the merge rounding that led to "unmerged by default".

What it does not prove: the other seven families. Phase 0 and 1 repeat this check per family as the family contract
test (section 14), and the research's real fine-tunes of every family (`training_research/FINE_TUNING_GUIDE.md`
section 11) already exercised their entry points.

