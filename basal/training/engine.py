"""The training engine: one loop for every model family.

    measure the released model  ->  train (LoRA + head, replay distillation, early stopping)  ->  calibrate
    ->  release gate (better on unseen examples? nothing forgotten? no collapse?)  ->  export delta  ->  parity check

Safeguards against making a model worse, all automatic:
- a warm start that is exactly the released model (the LoRA starts at zero), low learning rates, warm-up and decay;
- replay: general decision examples mixed into every step with a distillation loss towards the released model's own
  answers (Learning without Forgetting), so skills the user's data doesn't cover are kept;
- early stopping on the calibration split's *calibrated* log loss, and restoring the best step (never the last);
- training-time option shuffling for choice questions, so the model can't learn "the answer is usually first";
- the release gate: the fine-tune is published only if it beats the released model on examples it never saw, keeps
  its accuracy on a general guard set, and does not collapse to one answer. Otherwise nothing changes.

Safeguards for the machine: a self-test before loading anything, a memory estimate that refuses jobs which can't
fit, micro-batches that halve themselves after an out-of-memory error, and a watchdog that pauses the job when
other programs take the memory or the GPU gets too hot.
"""
from __future__ import annotations

import gc
import json
import math
import random
import time
import traceback
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import dataformat, metrics
from .dataformat import Example
from .metrics import Pred

PACK = Path(__file__).resolve().parent / "assets" / "general.jsonl"


@dataclass
class Job:
    model_id: str
    data: str                                     # path to the uploaded file
    out: str                                      # job directory
    questions: dict | None = None                 # question definitions for simple tables
    name: str = ""                                # the fine-tune's display name
    seed: int = 0
    overrides: dict = field(default_factory=dict) # advanced: recipe fields
    publish_dir: str = ""                         # where accepted fine-tunes go (DATA/finetunes)
    replay: bool = True
    parity: bool = True


class Stopped(Exception):
    """Raised when the job is cancelled or paused by the watchdog."""


class Engine:
    def __init__(self, job: Job, emit: Callable[[dict], None], should_stop: Callable[[], str | None] = lambda: None):
        self.job, self.emit, self.should_stop = job, emit, should_stop
        self.out = Path(job.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.rng = random.Random(job.seed)
        self.t0 = time.time()

    # -- reporting ----------------------------------------------------------------------------------------------------
    def stage(self, key: str, text: str, progress: float | None = None, **extra):
        self._stage_now = (key, text, progress)
        self.emit({"type": "stage", "stage": key, "text": text, "progress": progress, **extra})

    # -- main ---------------------------------------------------------------------------------------------------------
    def run(self) -> dict:
        import torch
        from ..catalog import BY_ID
        from . import device as devmod
        from .families import get as get_family

        job = self.job
        spec = BY_ID[job.model_id]
        self.stage("check", "Checking this computer", 0.01)
        dev = devmod.profile()
        if dev.tier == "off":
            raise RuntimeError(dev.reason)
        devmod.prepare(dev)
        note = lambda t: self.emit({"type": "note", "text": t})
        devmod.reclaim(dev, 3.0)
        devmod.retry_device(lambda: devmod.self_test(dev), "checking the GPU", dev, 3.0, on_retry=note)
        self.dev = dev

        # data
        self.stage("data", "Reading your examples", 0.02)
        raw = Path(job.data).read_bytes()
        imp = dataformat.import_examples(raw, Path(job.data).name, job.questions)
        (self.out / "data_report.json").write_text(json.dumps(imp.report(), indent=1))
        if not imp.usable:
            raise RuntimeError(next(p.message for p in imp.problems if p.level == "error"))
        fam = get_family(spec.adapter)()
        usable, skipped = [], []
        for ex in imp.examples:
            why = fam.supports(spec, ex)
            (skipped if why else usable).append((ex, why))
        if skipped:
            self.emit({"type": "note", "text": f"{len(skipped)} examples were left out: {skipped[0][1]}."})
        examples = [ex for ex, _ in usable]
        self.examples = examples
        splits = dataformat.split(examples, seed=job.seed)
        n_train_q = sum(ex.labelled for ex in splits["train"])
        if n_train_q < 20 or sum(ex.labelled for ex in splits["test"]) < 8:
            raise RuntimeError("After keeping some examples aside to test the result, too few are left to train on. "
                               "Add more examples (a few hundred works best).")

        recipe = fam.recipe(spec, dev, n_train_q)
        for k, v in (job.overrides or {}).items():
            if hasattr(recipe, k):
                setattr(recipe, k, v)
        self.recipe = recipe
        need = fam.estimate_gb(spec, recipe)
        have = devmod.available_bytes(dev) / 1e9
        if need > have:
            raise RuntimeError(f"Training {spec.name} needs about {need:.0f} GB of memory and {have:.0f} GB is free "
                               f"right now. Eject loaded models or close other programs, then try again.")

        # Room to grow to twice the estimate (or 8 GB more), never into the last few GB the computer needs
        self.memory_cap = devmod.limit_memory(dev, max(need + 2, min(have - 4, max(2 * need, need + 8))))
        self.stage("load", f"Loading {spec.name}", 0.04)
        devmod.reclaim(dev, need + 2)

        def _load():
            try:
                fam.load(spec, dev, recipe, lambda t: self.stage("load", t, 0.05))
            except Exception:
                fam.unload()
                gc.collect()
                _empty_cache(dev.kind)
                devmod.reclaim(dev, need)
                raise
        devmod.retry_device(_load, f"loading {spec.name}", dev, need + 2, on_retry=note)
        self.fam, self.spec = fam, spec

        units = {k: [u for ex in v for u in fam.units(ex, train=False)] for k, v in splits.items()}
        units = {k: [u for u in us if u.cost <= recipe.max_tokens] for k, us in units.items()}
        replay_units, guard_units = self._pack_units(fam, spec, recipe, len(units["train"]), n_train_q) \
            if job.replay else ([], [])

        # 1. the released model, measured on the same data
        self.stage("baseline", "Measuring the original model on your examples", 0.07)
        base = {"calibration": self.evaluate(units["calibration"]), "test": self.evaluate(units["test"]),
                "guard": self.evaluate(guard_units)}
        teacher = self._teacher(replay_units)
        base_t = metrics.fit_temperatures(base["calibration"])
        self.emit({"type": "baseline", "test": metrics.summarise(base["test"], base_t),
                   "guard": metrics.summarise(base["guard"], base_t) if base["guard"] else None})

        # 2. training, then 3. calibration and the final measurements. If the result has started to forget general
        # decisions, it practises once more from the start, more gently (half the learning rate, twice the pull towards
        # the released model), so a refusal is the last resort rather than the first answer.
        init_state = fam.trainable_state()
        attempt_file = self.out / "attempt"
        attempt = int(attempt_file.read_text()) if attempt_file.exists() and (self.out / "resume.pt").exists() else 1
        while True:
            if attempt == 2:
                recipe.lr, recipe.head_lr = recipe.lr / 2, recipe.head_lr / 2
                recipe.kl_weight, recipe.drift_weight = recipe.kl_weight * 2, recipe.drift_weight * 2
            history, best = self.train(units["train"], units["calibration"], replay_units, teacher, base["calibration"])
            fam.load_trainable_state(best["state"])
            self.stage("check_result", "Checking the result on examples it has never seen", 0.9)
            tuned = {"calibration": self.evaluate(units["calibration"]), "test": self.evaluate(units["test"]),
                     "guard": self.evaluate(guard_units)}
            temps = metrics.fit_temperatures(tuned["calibration"])
            verdict = self.gate(base, tuned, base_t, temps)
            if verdict["outcome"] != "forgot" or attempt == 2:
                break
            attempt = 2
            attempt_file.write_text("2")
            fam.load_trainable_state(init_state)
            self.emit({"type": "attempt", "n": 2, "first": {k: verdict[k] for k in ("before", "after", "guard_before",
                                                                                   "guard_after", "guard_change")},
                       "text": "It started to forget some general decisions, so it is practising again, more gently."})
        attempt_file.unlink(missing_ok=True)
        self._save_predictions(base, tuned)
        result = {"model_id": spec.id, "model_name": spec.name, "family": spec.adapter, "verdict": verdict,
                  "attempts": attempt, "history": history, "best_step": best["step"], "temperature": temps,
                  "data": {"train": n_train_q, "calibration": sum(ex.labelled for ex in splits["calibration"]),
                           "test": sum(ex.labelled for ex in splits["test"]), "skipped": len(skipped),
                           "replay": len(replay_units), "guard": len(guard_units)},
                  "recipe": asdict(recipe), "device": dev.public(),
                  "seconds": round(time.time() - self.t0, 1), "peak_gb": _peak_gb(self.dev.kind)}
        if verdict["accepted"]:
            result["showcase"] = self._showcase(splits["test"], base["test"], tuned["test"])
            result["evaluate"] = self._evaluate_set(splits["test"])
            self.stage("save", "Saving the improved model", 0.95)
            ft_dir = self.publish(result, splits)
            result["finetune_dir"] = str(ft_dir)
            if self.job.parity:
                self.stage("verify", "Making sure the studio answers exactly like the trained model", 0.97)
                sample = self._parity_sample(units["test"])
                self.release_model()
                result["parity"] = self.parity(ft_dir, sample)
                if not result["parity"]["ok"]:
                    result["verdict"] = {**verdict, "accepted": False, "outcome": "parity_failed",
                                         "message": "The saved model did not answer exactly like the trained one, so it "
                                                    "was not added. This is a bug; the details are in the job log."}
                    import shutil
                    shutil.rmtree(self.out / "rejected-delta", ignore_errors=True)
                    shutil.move(str(ft_dir), self.out / "rejected-delta")      # kept with the job, for diagnosis
                    result.pop("finetune_dir", None)
                else:
                    (ft_dir / "manifest.json").write_text(json.dumps({**json.loads((ft_dir / "manifest.json").read_text()),
                                                                      "parity": result["parity"]}, indent=1))
        (self.out / "result.json").write_text(json.dumps(result, indent=1, default=str))
        (self.out / "resume.pt").unlink(missing_ok=True)
        return result

    # -- replay and guard -------------------------------------------------------------------------------------------
    def _pack_units(self, fam, spec, recipe, n_train_units: int, n_train_q: int):
        if not PACK.exists():
            return [], []
        imp = dataformat.import_examples(PACK.read_bytes(), PACK.name)
        # A situation that is also in the person's data is left out of both: replaying it would pull the model back
        # towards its old answer on the very examples it is learning, and in the guard it would count learning as
        # forgetting (or hide forgetting).
        theirs = {dataformat.state_hash(ex.request.state) for ex in self.examples}
        replay, guard, overlap = [], [], 0
        for ex in imp.examples:
            if dataformat.state_hash(ex.request.state) in theirs:
                overlap += 1
                continue
            if fam.supports(spec, ex):
                continue
            us = [u for u in fam.units(ex, train=False) if u.cost <= recipe.max_tokens]
            (guard if ex.record.get("split") == "guard" else replay).extend(us)
        if overlap:
            self.emit({"type": "note", "text": f"{overlap} of your examples are also in the studio's general practice "
                                               "set; they were left out of it for this training."})
        rng = random.Random(1234)
        rng.shuffle(replay)
        rng.shuffle(guard)
        # Enough replay that each example comes round about twice over the whole run at most. A small replay set
        # seen many times gets fitted exactly (its divergence from the released model goes to zero) while answers
        # everywhere else still drift: with 500 replay questions seen 8 times each, Julia lost 2.7 points on the guard
        # even at 4x the distillation weight. The teacher pass over them costs one forward each, a fraction of training.
        presented = recipe.replay_ratio * n_train_units * epochs_for(n_train_q, recipe.max_epochs)
        cap_r = max(200, int(presented / 2) + 100)            # + the drift monitor's held-out share
        if recipe.max_replay:
            cap_r = min(cap_r, recipe.max_replay)
        return replay[:cap_r], guard[:recipe_guard_cap(spec)]

    def _teacher(self, units):
        if not units:
            return {}
        self.stage("baseline", "Remembering what the original model already knows", 0.08)
        preds = self.evaluate(units, keep_units=True)
        out = {}
        for u, lps in preds:
            for i, lp in zip(u.qi, lps):
                out[(id(u.example), i)] = [math.exp(x) for x in lp]
        return out

    # -- evaluation -------------------------------------------------------------------------------------------------
    def evaluate(self, units, keep_units: bool = False):
        import torch
        fam = self.fam
        fam.train_mode(False)
        preds, kept = [], []
        budget = self.recipe.token_budget * 2
        done, shown = 0, time.time()
        stage = getattr(self, "_stage_now", None)
        with torch.inference_mode():
            for batch in _batches(sorted(units, key=lambda u: u.cost), budget, shuffle=False):
                if stage and time.time() - shown > 15 and stage[0] != "train":
                    # long measurements (large models, or CLM embedding every text) show how far they have got
                    shown = time.time()
                    self.emit({"type": "stage", "stage": stage[0], "text": f"{stage[1]} ({done} of {len(units)})",
                               "progress": stage[2]})
                done += len(batch)
                out = self._guarded(lambda: fam.score(batch), batch)
                for u, lps in zip(batch, out):
                    row = []
                    for i, lp in zip(u.qi, lps):
                        v = lp.float().cpu().tolist()
                        row.append(v)
                        t = u.example.targets[i]
                        q = u.example.qs[i]
                        if t is not None:
                            preds.append(Pred(u.example.index, q.parent or q.id, q.type, v, t, len(q.keys), q.id,
                                              "|".join(map(str, q.keys))))
                    kept.append((u, row))
            self._check_stop()
        return kept if keep_units else preds

    # -- training ---------------------------------------------------------------------------------------------------
    def train(self, train_units, cal_units, replay_units, teacher, base_cal):
        import torch
        fam, recipe, dev = self.fam, self.recipe, self.dev
        n_q = sum(len([i for i in u.qi if u.example.targets[i] is not None]) for u in train_units)
        epochs = epochs_for(n_q, recipe.max_epochs)
        steps_per_epoch = max(1, math.ceil(n_q / recipe.effective_batch))
        total = max(1, int(epochs * steps_per_epoch))
        eval_every = max(5, min(steps_per_epoch, total // 8 or 1))
        # Drift monitor: a slice of replay examples never trained on; how far the model's answers on them move away
        # from the released model's (mean KL) is added to the checkpoint score, so the chosen step is the one that
        # learned the task while changing general behaviour least. (The guard set stays untouched for the gate.)
        n_drift = min(300 if getattr(self.spec, "speed", "") == "instant" else 100, len(replay_units) // 6)
        self._budget0 = recipe.token_budget
        self._n_train_examples = len({id(u.example) for u in train_units})
        drift_units, replay_units = replay_units[:n_drift], replay_units[n_drift:]
        replay_iter = _cycle(replay_units, self.rng)
        groups = fam.param_groups(recipe)
        opt = torch.optim.AdamW([{k: v for k, v in g.items() if k != "name"} for g in groups], betas=(0.9, 0.999),
                                **_optim_kwargs(dev))
        warm = max(1, int(recipe.warmup * total))
        sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: (s + 1) / warm if s < warm else
                                                  0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total - warm))))
        scaler = torch.amp.GradScaler(dev.kind) if dev.grad_scaler else None
        params = [p for g in groups for p in g["params"]]
        best_score = self._select_score(base_cal)
        best = {"step": 0, "score": best_score, "state": fam.trainable_state()}
        history = [{"step": 0, "cal_loss": round(best_score, 4), "cal_accuracy": round(metrics.summarise(base_cal)["accuracy"], 4),
                    "drift": 0.0}]
        bad_evals, step = 0, 0
        resume_path = self.out / "resume.pt"
        if resume_path.exists():
            ck = torch.load(resume_path, map_location="cpu", weights_only=False)
            fam.load_trainable_state(ck["state"])
            opt.load_state_dict(ck["opt"])
            sched.load_state_dict(ck["sched"])
            step, best, history, bad_evals = ck["step"], ck["best"], ck["history"], ck["bad_evals"]
            recipe.token_budget = ck.get("token_budget", recipe.token_budget)
            self.emit({"type": "note", "text": f"Continuing where it left off (step {step} of {total})."})
        self.stage("train", f"Learning from your examples (reading them up to {max(1, round(epochs))} times)", 0.1,
                   total_steps=total)
        from . import device as devmod
        try:
            with devmod.MemoryWatchdog(dev) as dog:
                self.dog = dog
                done = False
                for epoch in range(math.ceil(epochs)):
                    exs = _examples(train_units, self.rng)
                    skipped = self.__dict__.get("_skipped", set())
                    flat = [a for ex in exs for a in (fam.units(ex, train=True, rng=self.rng) if recipe.shuffle_options
                                                      else [u for u in train_units if u.example is ex])
                            if a.cost <= recipe.max_tokens and id(ex) not in skipped]
                    pending_q, acc_loss = 0, 0.0
                    fam.train_mode(True)
                    for batch in _batches(flat, recipe.token_budget, shuffle=True, rng=self.rng, max_units=recipe.effective_batch):
                        lval, nq = self._micro_step(batch, replay_iter, teacher, scaler)
                        if nq == 0:
                            continue
                        acc_loss += lval * nq
                        pending_q += nq
                        if pending_q >= recipe.effective_batch:
                            if scaler:
                                scaler.unscale_(opt)
                            torch.nn.utils.clip_grad_norm_(params, 1.0)
                            (scaler.step(opt), scaler.update()) if scaler else opt.step()
                            opt.zero_grad(set_to_none=True)
                            sched.step()
                            step += 1
                            self.emit({"type": "step", "step": step, "total": total, "loss": round(acc_loss / pending_q, 4),
                                       "progress": 0.1 + 0.78 * step / total})
                            pending_q, acc_loss = 0, 0.0
                            if step % eval_every == 0 or step >= total:
                                cal = self.evaluate(cal_units)
                                drift = self._drift(drift_units, teacher)
                                fam.train_mode(True)
                                task_score = self._select_score(cal)
                                score = task_score + recipe.drift_weight * drift
                                s = metrics.summarise(cal)
                                history.append({"step": step, "cal_loss": round(task_score, 4), "cal_accuracy": round(s["accuracy"], 4),
                                                "drift": round(drift, 4)})
                                self.emit({"type": "eval", **history[-1]})
                                if score < best["score"] - 1e-4:
                                    best = {"step": step, "score": score, "state": fam.trainable_state()}
                                    bad_evals = 0
                                else:
                                    bad_evals += 1
                                if bad_evals >= recipe.patience:
                                    self.emit({"type": "note", "text": "Stopped early: more practice was no longer helping."})
                                    done = True
                            if step >= total:
                                done = True
                        if done:
                            break
                    if done:
                        break
        except Stopped:
            # Paused (by the person or the memory watchdog) or cancelled: keep everything needed to continue.
            torch.save({"state": fam.trainable_state(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                        "step": step, "best": best, "history": history, "bad_evals": bad_evals,
                        "token_budget": recipe.token_budget}, resume_path)
            raise
        fam.train_mode(False)
        opt.zero_grad(set_to_none=True)
        del opt
        resume_path.unlink(missing_ok=True)          # finished: a later attempt must not continue from it
        return history, best

    def _batch_loss(self, batch, replay_iter, teacher):
        import torch
        fam, recipe = self.fam, self.recipe
        lps = fam.score(batch)
        losses, nq = [], 0
        eps = recipe.label_smoothing
        for u, row in zip(batch, lps):
            for i, lp in zip(u.qi, row):
                t = u.example.targets[i]
                if t is None:
                    continue
                tt = torch.tensor(t, device=lp.device, dtype=torch.float32)
                if eps:
                    tt = (1 - eps) * tt + eps / tt.numel()
                losses.append(-(tt * lp.float()).sum())
                nq += 1
        if not losses:
            return None, 0
        loss = torch.stack(losses).mean()
        # The replay share accumulates across micro-batches: rounding it per micro-batch gave none at all for one-unit
        # micro-batches (round(0.5) == 0), which is how the large models train.
        n_replay = 0
        if teacher:
            self._replay_owed = getattr(self, "_replay_owed", 0.0) + recipe.replay_ratio * len(batch)
            n_replay = int(self._replay_owed)
            self._replay_owed -= n_replay
        if n_replay:
            rb = [next(replay_iter) for _ in range(n_replay)]
            rl = []
            for u, row in zip(rb, fam.score(rb)):
                for i, lp in zip(u.qi, row):
                    p = teacher.get((id(u.example), i))
                    if p is None:
                        continue
                    pt = torch.tensor(p, device=lp.device, dtype=torch.float32).clamp_min(1e-8)
                    rl.append((pt * (pt.log() - lp.float())).sum())
            if rl:
                loss = loss + recipe.kl_weight * torch.stack(rl).mean()
        return loss, nq

    def _drift(self, units, teacher) -> float:
        """Mean KL(released || current) over held-out replay questions: how much general answers have moved."""
        if not units or not teacher:
            return 0.0
        tot, n = 0.0, 0
        for u, row in self.evaluate(units, keep_units=True):
            for i, lp in zip(u.qi, row):
                p = teacher.get((id(u.example), i))
                if p is None:
                    continue
                tot += sum(a * (math.log(max(a, 1e-8)) - b) for a, b in zip(p, lp))
                n += 1
        return tot / max(1, n)

    def _select_score(self, preds: list[Pred]) -> float:
        """Calibrated log loss: the log loss after fitting a temperature, so a checkpoint isn't preferred just
        because it is less confident. Lower is better."""
        if not preds:
            return 0.0
        t = metrics.fit_temperatures(preds, min_per_type=10**9)
        return metrics.summarise(preds, t)["log_loss"]

    # -- memory safety ----------------------------------------------------------------------------------------------
    def _guarded(self, fn, batch, train: bool = False):
        import torch
        for attempt in range(4):
            self._check_stop()
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if not _is_oom(e):
                    raise
                self.fam.root.zero_grad(set_to_none=True)
                gc.collect()
                _empty_cache(self.dev.kind)
                from . import device as devmod
                if devmod.reclaim(self.dev, 6.0 * (attempt + 1)) > 1:
                    self.emit({"type": "note", "text": "Freed memory held by the operating system's file cache."})
                    if attempt == 0:
                        continue                  # retry the same batch once before shrinking it
                if len(batch) > 1 and self.recipe.token_budget > 64:   # one example can't be made smaller this way
                    old = self.recipe.token_budget
                    self.recipe.token_budget = max(64, old // 2)
                    self.emit({"type": "note", "text": f"Memory was tight; using smaller batches ({old} -> "
                                                       f"{self.recipe.token_budget} tokens)."})
                if train:
                    raise _Retry()
                if len(batch) > 1:
                    half = len(batch) // 2
                    return self._guarded(lambda: self.fam.score(batch[:half]), batch[:half]) + \
                        self._guarded(lambda: self.fam.score(batch[half:]), batch[half:])
        raise RuntimeError("The GPU ran out of memory even with the smallest batches. Eject loaded models or close "
                           "other programs, then try again.")

    def _micro_step(self, batch, replay_iter, teacher, scaler) -> tuple[float, int]:
        """Forward and backward for one micro-batch. After an out-of-memory error the budget is halved and the batch is
        split, each half doing its own backward (so the halves never hold memory at the same time)."""
        def run(b, with_replay):
            loss, nq = self._batch_loss(b, replay_iter if with_replay else iter(()), teacher if with_replay else {})
            if nq:
                (scaler.scale(loss) if scaler else loss).backward()
                return float(loss.detach()), nq
            return 0.0, 0
        try:
            return self._guarded(lambda: run(batch, True), batch, train=True)
        except _Retry:
            if len(batch) == 1:
                try:
                    return self._guarded(lambda: run(batch, False), batch, train=True)
                except _Retry:
                    if self._lighter():
                        return self._micro_step(batch, replay_iter, teacher, scaler)
                    self._skip(batch[0])
                    return 0.0, 0
            half = len(batch) // 2
            l1, n1 = self._micro_step(batch[:half], replay_iter, teacher, scaler)
            l2, n2 = self._micro_step(batch[half:], replay_iter, teacher, scaler)
            tot = n1 + n2
            return ((l1 * n1 + l2 * n2) / tot if tot else 0.0), tot

    def _lighter(self) -> bool:
        """One example didn't fit: switch the model to activation checkpointing (it keeps far less in memory and
        recomputes the rest; slower, up to 2.5x for Laya), once, and go back to the batch size the run started with."""
        if self.recipe.grad_checkpointing or not self.fam.enable_checkpointing():
            return False
        self.recipe.grad_checkpointing = True
        self.recipe.token_budget = max(self.recipe.token_budget, getattr(self, "_budget0", self.recipe.token_budget))
        self.emit({"type": "note", "text": "Some examples are long, so it is training in a slower way that needs much "
                                           "less memory."})
        return True

    def _skip(self, unit) -> None:
        """An example that doesn't fit even so is left out of training (it still counts in the measurements)."""
        skipped = self.__dict__.setdefault("_skipped", set())
        skipped.add(id(unit.example))
        n = self.__dict__.get("_n_train_examples", 0)
        if len(skipped) > max(5, 0.1 * n):
            raise RuntimeError("Too many of your examples are too long to learn from with this computer's memory. "
                               "Shorter examples (split long documents), or a smaller model, usually work.")
        if len(skipped) == 1:
            self.emit({"type": "note", "text": "An example too long to fit in memory was left out of training."})

    def _check_stop(self):
        now = time.time()
        if now - getattr(self, "_beat", 0) > 5:
            self._beat = now
            try:
                (self.out / "alive").touch()          # read by the job supervisor (job.py): the main thread is working
            except OSError:
                pass
        why = self.should_stop()
        if why:
            raise Stopped(why)
        dog = getattr(self, "dog", None)
        if dog is not None and dog.tripped:
            self._wait_out(dog)

    def _wait_out(self, dog):
        """Other programs took the memory, or the GPU is too hot: wait here (holding nothing new) until things
        recover, then carry on by itself. After an hour, stop with a resume point so the person can continue later."""
        reason = dog.tripped
        _empty_cache(self.dev.kind)               # give back what this job holds but isn't using before waiting
        self.stage("paused", reason.split(",")[0] + ". It will continue by itself when there is room.", None)
        t0, ok_since = time.time(), None
        while time.time() - t0 < 3600:
            (self.out / "alive").touch()
            why = self.should_stop()
            if why:
                raise Stopped(why)
            if dog.healthy():
                ok_since = ok_since or time.time()
                if time.time() - ok_since >= 30:
                    self.emit({"type": "note", "text": "Memory is free again; continuing."})
                    self.stage("train", "Learning from your examples", None)
                    dog.reset()
                    return
            else:
                ok_since = None
            time.sleep(5)
        raise Stopped(reason)

    # -- release gate -------------------------------------------------------------------------------------------------
    def gate(self, base, tuned, base_t, temps) -> dict:
        bt, tt = base["test"], tuned["test"]
        sb, st = metrics.summarise(bt, base_t), metrics.summarise(tt, temps)
        gain = metrics.paired_accuracy_gain(bt, tt, seed=self.job.seed)
        g_base = metrics.summarise(base["guard"], base_t) if base["guard"] else None
        g_tuned = metrics.summarise(tuned["guard"], temps) if tuned["guard"] else None
        n_test = max(1, len(tt))
        better = (gain["gain"] * n_test >= 1 and gain["p_better"] >= 0.8) or \
                 (gain["gain"] >= 0 and st["log_loss"] <= sb["log_loss"] - 0.01)
        forgot, guard_change = False, None
        if g_base and g_tuned:
            # Paired: the same general questions answered before and after. Forgetting = a clear drop on the same
            # questions (resampled over examples), not a difference that noise alone would produce.
            guard_change = metrics.paired_accuracy_gain(base["guard"], tuned["guard"], seed=self.job.seed)
            drop = -guard_change["gain"]
            # Forgetting that blocks release: a clear drop of 2 points or more, any drop of 3 or more, or a clear drop
            # of 1 point or more that the gain on the person's task does not outweigh three times over. A specialist
            # that gains 15 points on its task and gives up ~1.5 on unrelated general questions is kept (measured on
            # Julia with the full guard set; the stricter 1.5-point rule rejected half of those runs on noise-level
            # differences).
            clear = guard_change["p_worse"] >= 0.8
            forgot = drop >= 0.03 or (clear and drop >= 0.02) or (clear and drop >= 0.01 and gain["gain"] < 3 * drop)
        collapsed = [q for q in metrics.collapse(tt) if q not in metrics.collapse(bt)]
        accepted = better and not forgot and not collapsed
        pct = lambda x: f"{100 * x:.0f}%"
        if accepted:
            outcome = "improved"
            msg = (f"Better on examples it had never seen: {pct(sb['accuracy'])} → {pct(st['accuracy'])} correct.")
        elif collapsed:
            outcome = "collapsed"
            msg = ("Training made the model give the same answer to almost everything, so it was not saved. This usually "
                   "means one answer dominates your examples; add more examples of the other answers.")
        elif forgot:
            outcome = "forgot"
            msg = (f"The model got better at your examples but worse at general decisions ({pct(g_base['accuracy'])} → "
                   f"{pct(g_tuned['accuracy'])}), so it was not saved. More varied examples usually fix this.")
        elif sb["accuracy"] >= 0.85:
            outcome = "not_improved"
            msg = (f"The original model already gets {pct(sb['accuracy'])} of these right, and training didn't improve on "
                   "that, so it stays as it is. You can use it for these decisions as it is.")
        else:
            outcome = "not_improved"
            msg = (f"Training didn't beat the original model on examples it hadn't seen ({pct(sb['accuracy'])} before, "
                   f"{pct(st['accuracy'])} after), so the original stays as it is. More examples, or answers that are "
                   "easier to tell apart, usually help.")
        return {"accepted": accepted, "outcome": outcome, "message": msg, "before": sb, "after": st, "gain": gain,
                "guard_before": g_base, "guard_after": g_tuned, "guard_change": guard_change, "collapsed": collapsed}

    @staticmethod
    def _evaluate_set(test_examples) -> dict | None:
        """The held-out examples of the first single-answer question, in the Evaluate page's format (JSON lines of text
        and label), so "Compare on Evaluate" opens with the original and the fine-tune side by side on data neither
        was trained on. Only for text situations, the form the Evaluate page sends."""
        for ex in test_examples:
            for q in ex.qs:
                if q.parent is None and q.type in ("choice", "score", "noul") and q.role not in ("multi", "rank", "number"):
                    qid = q.id
                    break
            else:
                continue
            break
        else:
            return None
        lines = []
        for ex in test_examples:
            if not isinstance(ex.request.state, str) or ex.request.media:
                return None
            i = next((k for k, q in enumerate(ex.qs) if q.id == qid), None)
            if i is None or ex.targets[i] is None:
                continue
            q, gold = ex.qs[i], ex.gold(i)
            label = ("yes" if gold == 1 else "no") if q.type == "noul" else q.keys[gold]
            lines.append(json.dumps({"text": ex.request.state, "label": label}, ensure_ascii=False))
        qdef = next(ex.record["questions"].get(qid) for ex in test_examples if qid in ex.record["questions"])
        return {"question_id": qid, "question": qdef, "text": "\n".join(lines)} if lines else None

    def _save_predictions(self, base, tuned) -> None:
        """Every test and guard answer before and after, for diagnosis (which questions changed, and how)."""
        def rows(preds):
            return [[p.example, p.pid, p.qtype, p.gold, max(range(len(p.logp)), key=p.logp.__getitem__),
                     round(max(p.logp), 4)] for p in preds]
        try:
            (self.out / "predictions.json").write_text(json.dumps(
                {"columns": ["example", "question", "type", "gold", "answer", "top_logp"],
                 **{f"{k}_{split}": rows(d[split]) for k, d in (("base", base), ("tuned", tuned))
                    for split in ("test", "guard")}}))
        except OSError:
            pass

    def _showcase(self, test_examples, base_test, tuned_test) -> dict | None:
        """A held-out example the released model got wrong and the fine-tune gets right (every question), for the result
        page and for "Use it now", which opens it in the Playground."""
        from ..contract import render

        def top(p):
            return max(range(len(p.logp)), key=p.logp.__getitem__)

        def by_ex(preds):
            out: dict[int, dict] = {}
            for p in preds:
                out.setdefault(p.example, {})[p.pid] = p
            return out
        def margin(p):                    # how far the right answer leads the next one, in probability
            ps = sorted((math.exp(x) for x in p.logp), reverse=True)
            return ps[0] - (ps[1] if len(ps) > 1 else 0.0)

        b, t = by_ex(base_test), by_ex(tuned_test)
        best = None
        for ex in test_examples:
            tp = t.get(ex.index)
            if not tp or ex.request.media or any(top(p) != p.gold for p in tp.values()):
                continue
            # Prefer examples the new model gets right clearly: one decided by a hair can come out the other way when
            # the studio scores it on its own instead of in a batch, and this is the example "Use it now" opens.
            key = (min(min(margin(p) for p in tp.values()), 0.3), sum(top(p) != p.gold for p in b.get(ex.index, {}).values()))
            if best is None or key > best[0]:
                best = (key, ex)
        if best is None:
            return None
        (_, fixed), ex = best
        rows = []
        for q in ex.qs:
            bp, tp = b.get(ex.index, {}).get(q.id), t[ex.index].get(q.id)
            if q.parent or tp is None:
                continue
            rows.append({"question": q.instructions or q.id, "right": q.labels[tp.gold],
                         "before": q.labels[top(bp)] if bp else None, "after": q.labels[top(tp)]})
        return {"text": render(ex.request.state)[:600], "questions": rows, "fixed": fixed,
                "request": {"state": ex.record["state"], "questions": ex.record["questions"]}}

    # -- publishing ---------------------------------------------------------------------------------------------------
    def publish(self, result: dict, splits) -> Path:
        import hashlib
        spec, job = self.spec, self.job
        base_id = getattr(spec, "base_id", "") or spec.id
        stamp = time.strftime("%Y%m%d-%H%M%S")
        ft_id = f"{base_id}-ft-{stamp}"
        d = Path(job.publish_dir) / ft_id
        self.fam.export(d, self.recipe)
        v = result["verdict"]
        manifest = {
            "id": ft_id, "name": job.name or f"{spec.name} (fine-tuned)", "base_model": base_id, "family": spec.adapter,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": self.recipe.method,
            "lora": {"r": self.recipe.lora_r, "alpha": self.recipe.lora_alpha, "targets": self.recipe.lora_targets},
            "temperature": result["temperature"], "accuracy_before": v["before"]["accuracy"],
            "accuracy_after": v["after"]["accuracy"], "guard_before": (v["guard_before"] or {}).get("accuracy"),
            "guard_after": (v["guard_after"] or {}).get("accuracy"), "data": result["data"],
            "questions": sorted({q.parent or q.id for ex in splits["train"] for q in ex.qs}),
            "dataset_sha1": hashlib.sha1(Path(job.data).read_bytes()).hexdigest(), "job": str(self.out),
            "example": (result.get("showcase") or {}).get("request"),
            "versions": _versions(), "device": self.dev.public(),
        }
        (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
        return d

    def _parity_sample(self, test_units, n: int = 12):
        """The trained model's answers on a few test examples, each scored on its own as the studio serves it (all its
        questions in one batch). Evaluation batches many examples together, and under bf16 the padding that brings
        changes probabilities by up to a few hundredths; that is not what the parity check is for (it checks that
        the saved delta reproduces the trained model), so the reference is computed the serving way."""
        import torch
        by_ex: dict[int, list] = {}
        for u in test_units:
            by_ex.setdefault(u.example.index, []).append(u)
        sample = []
        self.fam.train_mode(False)
        with torch.inference_mode():
            for us in list(by_ex.values())[:n]:
                want = {}
                for u, row in zip(us, self._guarded(lambda: self.fam.score(us), us)):
                    for i, lp in zip(u.qi, row):
                        want[u.example.qs[i].id] = lp.float().cpu().tolist()
                sample.append((us[0].example, want))
        return sample

    def release_model(self):
        self.fam.unload()
        self.fam = None
        gc.collect()
        _empty_cache(self.dev.kind)

    def parity(self, ft_dir: Path, sample) -> dict:
        """Load the fine-tune exactly as the studio's worker does and compare its answers with the trained model's."""
        from .. import finetunes
        from ..adapters.base import DecideInput
        from ..contract import render
        spec = finetunes.spec_from_dir(ft_dir)
        adapter = finetunes.load_adapter(spec, {"device": self.dev.kind})
        worst, n = 0.0, 0
        for ex, want in sample:
            out = adapter.decide(DecideInput(ex.request, ex.qs, render(ex.request.state),
                                             [{"type": m.type, "path": m.path} for m in ex.request.media]))
            for q, p in zip(ex.qs, out.probs):
                ref = want.get(q.id)
                if not ref:
                    continue
                r = [math.exp(x) for x in ref]
                worst = max(worst, max(abs(a - b) for a, b in zip(p, r)))
                n += 1
        del adapter
        gc.collect()
        _empty_cache(self.dev.kind)
        return {"ok": worst < 0.02 and n > 0, "max_difference": round(worst, 6), "questions": n}


# ----------------------------------------------------------------------------------------------------------------
# Helpers


def recipe_guard_cap(spec) -> int:
    """Guard questions to check for forgetting: more where evaluation is cheap (less noise in the check)."""
    # Units, not examples: for the models that answer one question per pass, 300 units were 300 of the ~750 guard
    # questions, and the paired change then had a standard error of ~1.2 points, enough to flip the gate between two
    # runs of the same recipe. Fast models evaluate every guard question.
    return 1200 if spec.speed == "instant" else 160 if spec.speed == "fast" else 80


def epochs_for(n_questions: int, max_epochs: float) -> float:
    """More passes for small datasets (early stopping guards against over-fitting), fewer for large ones."""
    if n_questions >= 3000:
        return max(1.0, max_epochs / 2)
    if n_questions >= 1000:
        return max_epochs
    return min(max_epochs * 2, max(max_epochs, max_epochs * 1000 / max(1, n_questions)))


def _optim_kwargs(dev) -> dict:
    kw = dict(dev.optimizer or {})
    if kw.get("fused"):
        kw.pop("foreach", None)
    return kw


def _batches(units, budget: int, shuffle: bool, rng=None, max_units: int | None = None):
    """Micro-batches under a token budget (padding counted as the longest unit x batch size) and, when training, no
    more units than one optimizer step should see; similar lengths are grouped to waste less padding."""
    units = list(units)
    if shuffle:
        rng.shuffle(units)
        chunks = [sorted(units[i:i + 64], key=lambda u: u.cost) for i in range(0, len(units), 64)]
        units = [u for c in chunks for u in c]
    batch, longest = [], 0
    out = []
    for u in units:
        c = max(1, u.cost)
        if batch and (max(longest, c) * (len(batch) + 1) > budget or (max_units and len(batch) >= max_units)):
            out.append(batch)
            batch, longest = [], 0
        batch.append(u)
        longest = max(longest, c)
    if batch:
        out.append(batch)
    if shuffle:
        rng.shuffle(out)
    return out


class _Retry(Exception):
    """Internal: a training micro-batch ran out of memory; retry it in halves."""


def _examples(units, rng) -> list[Example]:
    seen, out = set(), []
    for u in units:
        if id(u.example) not in seen:
            seen.add(id(u.example))
            out.append(u.example)
    rng.shuffle(out)
    return out


def _cycle(units, rng):
    if not units:
        while True:
            yield None
    while True:
        order = list(units)
        rng.shuffle(order)
        yield from order


def _is_oom(e: BaseException) -> bool:
    s = f"{type(e).__name__}: {e}".lower()
    return "out of memory" in s or "outofmemory" in s or "ur_result_error_out_of_device_memory" in s


def _peak_gb(kind: str) -> float | None:
    """The most GPU memory PyTorch held at once during this job (tensors only, not the driver's own share)."""
    import torch
    try:
        if kind == "cuda":
            return round(torch.cuda.max_memory_reserved() / 1e9, 2)
        if kind == "xpu":
            return round(torch.xpu.max_memory_reserved() / 1e9, 2)
        if kind == "mps":
            return round(torch.mps.driver_allocated_memory() / 1e9, 2)
    except Exception:  # noqa: BLE001
        pass
    return None


def _empty_cache(kind: str):
    import torch
    try:
        if kind == "cuda":
            torch.cuda.empty_cache()
        elif kind == "mps":
            torch.mps.empty_cache()
        elif kind == "xpu":
            torch.xpu.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _versions() -> dict:
    import importlib.metadata as md
    out = {}
    for pkg in ("torch", "transformers", "peft"):
        try:
            out[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            pass
    from .. import __version__
    out["studio"] = __version__
    return out
