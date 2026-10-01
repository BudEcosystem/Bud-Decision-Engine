# Teaching a model your decisions (the trainer)

The **Train** page fine-tunes one of the studio's decision models on your own examples, on this computer. You give it
a file of situations and the answers you would have given. It picks a model, trains it, checks the result on examples
it never saw, and adds the improved model to the studio only if it is clearly better and has not got worse at
anything else.

This guide has two halves. The first is for anyone using the Train page or preparing data. The second is for
developers. The design rationale and the research behind every default are in [ARCHITECTURE.md](ARCHITECTURE.md) and
`training_research/FINE_TUNING_GUIDE.md` (the research folder is kept locally and is not part of the repository).

Terms used below:

- **Fine-tuning**: continuing to train a released model on new examples so that it answers more like them.
- **LoRA** (low-rank adaptation): the released weights stay frozen and small extra matrices are trained beside some
  layers. A fine-tune is then a file of a few megabytes, and training needs far less memory.
- **Forgetting** (also "catastrophic forgetting"): a fine-tune that gets better at the new task but worse at what the
  model could already do.
- **Held-out examples**: examples set aside before training and never trained on, used only to measure the result.

---

## Part 1: using it

### The three steps on the Train page

1. **Add examples.** Drop a file, or click "Try it with an example file" to use a ready-made set of 420 support
   tickets. The studio reads the file, works out the questions and their possible answers, and says in plain words
   what it found and anything it had to skip.
2. **Check and start.** Each question is shown with how often each answer appears. You can reword a question's
   instruction, which is how the model will be asked it later. The studio recommends a model and estimates the time.
   "Change model" lists the others with a reason when one can't be used. Press **Start teaching**.
3. **Use it.** A progress screen shows the practice score rising. At the end you see before and after accuracy on
   held-out examples and one example it used to get wrong. If the model improved, it appears in the model list as a
   new model, such as "Julia 1 for support tickets". **Use it now** opens that example in the Playground, and
   **Compare on Evaluate** opens Evaluate with the held-out examples and both models selected. If it didn't improve,
   nothing is added, and the page says why and offers to teach another model instead.

Trained models are listed under **Your trained models** on the Train page. **Export** saves one as a .zip (its small
file of changes and its record, not the whole model), and **Import a trained model** adds such a .zip on another
computer, where the original model downloads as usual.

You can pause, continue or cancel at any time, and you can close the page: training runs in its own process and
carries on.

### What a good file looks like

The easiest file is a spreadsheet saved as CSV: one column with the text, and one column per answer.

```
ticket,team,urgent
"Can't sign in since the password reset this morning",identity,yes
"Refund for a double charge on invoice 4412",payments,no
...
```

From this file the studio builds two questions:

- **team**: pick one of the team names it found;
- **urgent**: a yes or no question, asked as "Is this urgent?".

Several text columns become one structured situation, so the model sees every field with its name.

How much you need:

| Examples | What to expect |
|---|---|
| fewer than 30 answered questions | refused: too few to learn from or to test on |
| 30 to 300 | works, but only large improvements can be told apart from luck; the result says so |
| 300 to 2,000 | the sweet spot for most tasks |
| more than 2,000 | better, with diminishing returns; training takes longer |

What helps most:

- **Every answer represented.** A good file has at least 10 to 20 examples of each answer. The studio warns when one
  answer is rare and refuses when every example has the same answer.
- **Real examples, as messy as real life.** Copies of the same text are found and kept on the same side of the
  train/test split, so they can't inflate the score.
- **Consistent answers.** If two people would label the same ticket differently, the model learns that uncertainty.
  That is fine: its probabilities will reflect it.

### The standard format (for full control)

For several question types, scales, pick-all-that-apply or shared question definitions, use JSON Lines: one example
per line, written as the studio's own request plus the right answers.

```json
{"state": "Order 1182 arrived with a cracked screen",
 "questions": {"team":   {"type": "choice", "instructions": "Which team should handle this?",
                          "criteria": {"returns": "damaged or wrong items", "billing": "charges and refunds"}},
               "urgent": {"type": "noul", "instructions": "Does this need attention today?"},
               "impact": {"type": "score", "instructions": "How bad is it for the customer?",
                          "criteria": ["minor", "moderate", "severe"]}},
 "answers":   {"team": "returns", "urgent": false, "impact": "moderate"},
 "group": "customer-0412"}
```

| Field | Required | Meaning |
|---|---|---|
| `state` | yes | the situation: text, or an object or list (sent to the model as JSON) |
| `questions` | yes, unless given once for the whole file | the same question definitions the API takes (`docs/studio-api.md`) |
| `answers` | yes | the right answer per question; leave a question out when its answer is unknown |
| `id` | no | your own reference, kept in reports |
| `group` | no | examples that share one situation (the same customer, the same document); a group is never split between training and testing |
| `media` | no | images, audio or video for the models that read them (Jev-Omni): `[{"type": "image", "path": "/full/path/photo.jpg"}]`, files on this computer |
| `split` | no | `"train"`, `"calibration"` or `"test"` to fix where an example goes |

How each answer is written:

| Question type | Answer |
|---|---|
| `choice` | the option's name, or its description (case does not matter) |
| `noul` (yes or no) | `true`/`false`, or `"yes"`/`"no"` |
| `score` | the level's number from 0, or its text |
| `multi` (pick all that apply) | a list of option names |
| `rank` | the top option's name |
| `number` | a number; the nearest listed value is used |
| any type | `{"probabilities": {"a": 0.7, "b": 0.3}}` for a soft label, when the honest answer is "probably a" |

The studio also reads the files the Evaluate page reads: CSV, TSV, JSON Lines, and `text<TAB>label` lines. A
`typed-decisions` style `gold` field is understood too.

### What the trainer does so a fine-tune is never worse

None of this needs a setting. It is listed so you can trust the result.

1. **It measures before it trains.** The released model answers your held-out examples first, and that is the bar to
   beat.
2. **It keeps practising old skills.** Every training batch mixes in general decision examples from two public
   datasets. On those, the model is pulled back towards the released model's own answers (a method called
   distillation, "Learning without Forgetting").
3. **It watches for drift.** At every check, it measures how far the model's answers to general questions it never
   trains on have moved. The step it keeps is the one that learned your task while moving those least.
4. **It stops when practice stops helping**, and goes back to the best step it saw.
5. **It calibrates.** A single temperature is fitted so that "80% sure" is right about 80% of the time on your
   examples.
6. **It releases only a clear win.** The fine-tune is kept only if all of these hold:
   - it is better on held-out examples, and that is unlikely to be luck;
   - it has not become worse at general decisions: a separate set of general questions, never trained on, is
     answered before and after. A clear drop of 2 points or more blocks it, and so does a smaller clear drop that
     the gain on your examples doesn't outweigh three times over;
   - it doesn't answer everything the same way.

   If the only problem is that it got worse on the general questions, it starts again from the released model once,
   more gently: half the learning rate, and twice the pull towards the original answers.
7. **It checks the saved model.** The studio loads the saved fine-tune the way it serves models and checks that it
   answers exactly like the trained one.

### Results on every model

Each model trained by the studio itself on an NVIDIA GB10 shared with other programs, measured on examples set aside
before training. "General" is the change, in points, on general questions the run never trained on.

| Model | Task | Before → after | General | Minutes |
|---|---|---|---|---|
| Julia 1 | support tickets (the example file) | 52% → 91% | −0.4 | 6 |
| Julia 1 | policy topics | 51% → 68% | −1.5 | 13 |
| Julia 1 | emotions in conversations | 33% → 50% | −1.5 | 16 |
| Laya | policy topics | 59% → 80% | +0.2 | 29 |
| Laya | business workflows | 36% → 63% | +12.1 | 76 |
| Laya Multilingual | business workflows | 34% → 62% | +12.7 | 37 |
| Laya Typed-Decisions | policy topics | 61% → 81% | +0.2 | 21 |
| GLiNER2.5 Decide | policy topics | 66% → 75% | +0.7 | 82 |
| Kev 0.5B | policy topics | 65% → 79% | +0.6 | 39 |
| Kev 4B | policy topics | 77% → 82% | +0.2 | 65 |
| Intern-Decision 4B | emotions in conversations | 61% → 76% | +0.2 | 74 |
| Lev | policy topics | 75% → 82% | +2.5 | 72 |
| CLM 8B | business workflows | 39% → 68% | +11.2 | 65 |
| Jev-Omni | business workflows (160 records) | 62% → 77% | 0.0 | 161 |

Policy topics: 1,034 sentences from US State of the Union addresses with Comparative Agendas Project topic codes (16).
Emotions: 1,088 stories from EmpatheticDialogues (16 emotions; non-commercial licence). Business workflows: 1,280
typed-decisions cases. Every run, including the experiments that set each model's recipe, is in
[RESULTS.md](RESULTS.md).

### Hardware

| Computer | Training |
|---|---|
| NVIDIA RTX 30 series or newer, data-centre GPUs, DGX Spark / GB10 | on |
| Apple Silicon M2 or newer, macOS 14 or newer | on |
| Apple M1, or a Mac with less than 12 GB | experimental (slower, full precision) |
| NVIDIA RTX 20 series / T4 | experimental |
| AMD GPUs on Linux (ROCm) | experimental |
| Intel Arc, Core Ultra 200V / 200H and newer | experimental |
| CPU only, older GPUs, Core Ultra Series 1 graphics, NPUs | off |

On an experimental device the Train page offers "Try training on this GPU". The same switch is on the System page
("Allow experimental training").

Before each training, a short self-test compares the GPU's answers with the CPU's, so a broken
driver can't produce a broken model. Only one training runs at a time on a computer, and each one stays inside a memory limit set from what its model
needs: if your examples need more, it uses smaller batches, or a slower method that needs less memory, rather than
taking memory from other programs. The job lowers its own priority,
and a memory watchdog pauses it, rather than letting the computer swap or restart, if memory runs out. It continues by
itself once memory is free again. If the GPU stops responding (seen on a GB10 under very heavy memory pressure),
the training is restarted automatically, up to twice.

---

## Part 2: developers

### Where the code is

| File | What it does |
|---|---|
| `basal/training/dataformat.py` | the standard format, the importers (tables, Evaluate files, typed-decisions), quality checks, and grouped stratified splits; no PyTorch |
| `basal/training/families/base.py` | the family contract (`FamilyTrainer`, `Unit`, `Recipe`) and the shared LoRA and delta helpers |
| `basal/training/families/<family>.py` | one plugin per model family: how to load it, build scoring units, score options differentiably, and the family's recipe |
| `basal/training/engine.py` | the training loop, replay with distillation, the drift monitor, early stopping, out-of-memory recovery, the release gate, publishing and the parity check |
| `basal/training/metrics.py` | accuracy, log loss, Brier score, calibration error, temperature fitting, paired bootstrap, collapse detection |
| `basal/training/device.py` | the device policy, memory probes, page-cache reclaim, the self-test and the memory watchdog |
| `basal/training/job.py` | the job process and its command line |
| `basal/training/manager.py`, `api.py`, `probe.py` | the studio side: datasets, the model recommendation, launching and controlling jobs (the server never imports PyTorch) |
| `basal/finetunes.py` | the registry: each saved fine-tune becomes a catalog model, attached to its base at load time |
| `basal/training/assets/general.jsonl` | the replay and guard examples (`scripts/build_training_pack.py`) |
| `ui/js/pages/train.js` | the Train page |

### Running a training from the command line

```
python -m basal.training.job --model julia-1 --data examples.csv --out runs/julia-test
python -m basal.training.job --model kev-4b --data decisions.jsonl --out runs/kev --override '{"lr": 5e-5}'
```

The job writes four files to `--out`:

- `events.jsonl`: progress and evaluations;
- `status.json`: the current state;
- `result.json`: the full report, including the release gate's numbers;
- `stacks.log`: Python stacks on a crash, or after `kill -USR1 <pid>`.

An accepted fine-tune is saved to `DATA/finetunes/<base>-ft-<time>/` and shows up in the catalog on the next refresh.

Other flags:

- `--no-replay` and `--no-parity` exist for experiments;
- `--override` changes any `Recipe` field.

To test the Train page itself in a browser, use `scripts/e2e_train.py`.

### A fine-tune on disk

Every family uses the same layout:

```
DATA/finetunes/julia-1-ft-20261001-101500/
  lora.safetensors   LoRA tensors (zero at the start of training, so the untrained delta is the released model)
  lora.json          {"root", "r", "alpha", "dropout", "targets"}
  head.safetensors   the family's trained head, if any
  manifest.json      base model, temperature, before/after scores, data summary, versions, parity result
```

At load time, `basal/worker.py` loads the base model normally. `finetunes.attach` then injects the LoRA, unmerged, and
loads the head. `finetunes.calibrate` applies the fitted temperature to every answer.

Kev and Lev ship as a LoRA on a base model. Kev's released adapter is merged at load (as Kev serves it) and the
fine-tune is a new LoRA on top. Lev's released adapter is trained further unmerged, because merging it in bf16 changes
its answers, so a Lev fine-tune's `lora.safetensors` is the whole adapter. The layout is the same either way.

### Adding a model family

Write `basal/training/families/<family>.py` with a `FamilyTrainer` subclass and register it in `families/__init__.py`.
The engine needs these methods:

- `load`: load the model through the studio's own adapter, so training sees exactly what serving sees. Freeze it,
  then call `inject_lora`.
- `units(example, train)`: split an example into forward passes. Use one unit per question for models that answer one
  question at a time, or one unit per example for models that answer all of them in one pass. When `train` is true,
  shuffle the options of choice questions.
- `score(units)`: return per question the log-probabilities of its options, in `q.keys` order, with gradients.
- `recipe()`: the family's defaults. Also set `estimate_gb`.
- `supports(spec, example)`: say whether this model can answer this kind of example.

Then add a test in `tests/training/` that trains a tiny model for two steps and checks that the attached delta answers
like the trained model.
