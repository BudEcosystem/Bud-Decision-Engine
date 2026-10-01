"""Lev (Interfaze): a LoRA on Qwen3.5-4B that reads each answer from the next-token scores of one-token option codes
("Mode A"), one prompt row per question, choice questions averaged over two option orders.

Best practice for this family (training_research/models/lev/ANALYSIS.md):
- the released Lev is itself a LoRA (r32 on q/k/v/o of the 8 full-attention layers and gate/up/down of all 32 MLPs,
  section 2). A fine-tune continues training that adapter in place, unmerged, so training starts exactly at the model
  the studio serves (docs/trainer/ARCHITECTURE.md section 4, warm-start rule). Merging it into the bf16 backbone first
  is not an option: the rounding moved the released model's probabilities by up to 0.053 and changed 6 of 120 answers
  (GB10, T=1). The delta is the whole trained adapter (170 MB in fp32, the size of the release's own file); `attach`
  loads it over the served release's adapter;
- LR 5e-5, the publisher's value, lowered from 1e-4 "to preserve the instruct backbone's abilities" (section 4.3);
- replay at 50% with distillation towards the released model: the one narrow Lev fine-tune fell below the frozen
  backbone (0.489 against 0.719) and the released run lost 55 points on one subset (sections 4.4 and 11), and the
  analysis asks for at least 50% replay (section 6). One pass by default (the engine gives small datasets up to two):
  a 4B trains at a few hundred tokens per second on the GB10, so passes are the main cost of a run;
- choice options are shuffled in training, as Lev's own training does, because the option order is part of what the
  model reads (sections 3 and 4.4); a shuffled question is trained in one order, and evaluation uses the served
  two-order average;
- yes/no is Lev's 0-8 rating, read out as p(yes) = sum(i/8 p_i) (section 2.3); its log-probabilities over
  (no, yes) are computed from the rating distribution, differentiably;
- training is limited to 68 options: above that Lev trains its Mode B head but serves Mode A (sections 2.2 and 4.4),
  so a fine-tune there would not be what the studio serves. The Mode B head stays frozen;
- the model input comes from Lev's own serving functions (`serving_routes`, `DecisionEngine._render`, `prompt.build`,
  the Mode A label ids) on the adapter's `lev_questions`; evaluation runs the frozen base in its native bf16 and the
  LoRA in fp32, as served, with an example's rows in one batch, as `system_one` does; training runs under bf16
  autocast (the LoRA weights stay fp32);
- Lev's shipped `calibration.json` temperatures are switched off in training and in `attach`: the studio applies the
  fitted temperature (docs/trainer/ARCHITECTURE.md section 10).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, _get, attach_delta, autocast, freeze, shuffled, unpermute

MAX_OPTIONS = 68            # contiguous one-token codes; above this training and serving use different readouts
MAX_LEVELS = 10             # lev.types.Score


def release_lora(spec) -> dict:
    """The released adapter's shape (rank, alpha, dropout, target modules), read from its adapter_config.json."""
    from ...adapters.lev_adapter import LevAdapter
    cfg = json.loads((Path(LevAdapter.snapshot(spec.repo.id)) / "adapter_config.json").read_text())
    targets = cfg["target_modules"]
    return {"r": int(cfg["r"]), "alpha": int(cfg["lora_alpha"]), "dropout": float(cfg.get("lora_dropout") or 0.0),
            "targets": sorted(targets) if isinstance(targets, list) else targets}


def lev_served(engine):
    """The served backbone with the released LoRA beside its frozen weights (as PEFT serves it), with Lev's own
    temperatures switched off. Used by training and by `attach`, so both compute exactly what the studio serves."""
    from lev.calibrate import CalibrationProfile
    engine.calibration = CalibrationProfile()          # identity: temperature 1 for every bucket
    engine._candidate_cache.clear()
    return engine.model.base_model.model               # Qwen3_5ForCausalLM, the release's LoRA as adapter "default"


class LevTrainer(FamilyTrainer):
    family = "lev"
    head_names = ()
    lora_root = "model"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        rel = release_lora(spec)          # the released adapter is trained further, so its shape is fixed
        return Recipe(lora_targets=rel["targets"], lora_r=rel["r"], lora_alpha=rel["alpha"], lora_dropout=rel["dropout"],
                      lr=5e-5, head_lr=0.0,
                      weight_decay=0.0, warmup=0.06, max_epochs=1, effective_batch=16, token_budget=2048,
                      max_tokens=4096, replay_ratio=0.5, kl_weight=1.0, shuffle_options=True, patience=3,
                      grad_checkpointing=True, train_head=False,
                      notes=["continues the released adapter (unmerged); its rank and targets are fixed",
                             "Mode B head frozen; choice questions limited to 68 options"])

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if q.type == "choice" and not 2 <= len(q.keys) <= MAX_OPTIONS:
                return (f"{spec.name} trains on choice questions with 2 to {MAX_OPTIONS} options (larger lists are "
                        "answered by a part of the model that training would not reach)")
            if q.type == "score" and not 2 <= len(q.keys) <= MAX_LEVELS:
                return f"{spec.name} answers scales with 2 to {MAX_LEVELS} levels"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        # Measured on the GB10: 12.3 GB (research, LoRA r32 + head, batch 16, checkpointing); the model loads straight
        # onto the GPU (LevAdapter), so loading peaks at the model's 8.8 GB. Margin for long states.
        return 16.0

    # -- model ------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        from lev.model import _QUESTIONS, serving_routes
        from lev.prompt import Style, build, label_prefix
        from lev.readout.mode_a import LabelTokenReadout
        from ...adapters.lev_adapter import LevAdapter
        if dev.kind not in ("cuda", "cpu"):               # cpu: mechanics tests only (the device layer's test flag)
            raise RuntimeError("Lev trains only on NVIDIA (or AMD) GPUs: its library runs the model on CUDA or on the "
                               "processor.")
        stage("Loading Qwen3.5-4B and the released Lev adapter")
        a = LevAdapter(spec, {"device": dev.kind}, lambda *x: None)
        a.load()                                   # lev.load: the serving path
        eng = a.engine
        pc = eng.model.peft_config["default"]
        if (pc.r, pc.lora_alpha) != (recipe.lora_r, recipe.lora_alpha):
            raise RuntimeError("Lev's fine-tunes continue its released adapter, so its rank can't be changed")
        self.root = lev_served(eng)                # Qwen3_5ForCausalLM (bf16) with the released LoRA (fp32), unmerged
        self.adapter, self.engine = a, eng
        self.tokenizer = eng.tokenizer
        self.cfg = eng.config
        self.style = Style(eng.config.prompt_style)
        self.readout = LabelTokenReadout(self.tokenizer, prefix=label_prefix(self.style))
        self._questions, self._routes, self._build = _QUESTIONS, serving_routes, build
        self.pad_id = self.tokenizer.pad_token_id or 0             # as lev's _forward_single pads
        self.device = next(self.root.parameters()).device.type
        import torch
        self.autocast = getattr(torch, dev.autocast) if dev.autocast else None    # training only (see _forward)
        self._cache: dict[int, tuple] = {}
        freeze(self.root)
        lora = [(n, p) for n, p in _get(self.root, self.lora_root).named_parameters() if "lora_" in n]
        if not lora:
            raise RuntimeError("the released Lev adapter was not found on the loaded model")
        for _, p in lora:
            p.data = p.data.float()
            p.requires_grad_(True)
        if recipe.grad_checkpointing:
            self.root.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        self.root.config.use_cache = False

    # -- units ------------------------------------------------------------------------------------------------------
    def _parsed(self, ex: Example):
        """(prefix token ids, {question id: (Question, Route)}), as system_one builds them for this request."""
        key = id(ex)
        if key not in self._cache:
            from ...adapters.lev_adapter import lev_questions
            from lev.router import Mode
            questions = self._questions.validate_python(lev_questions(ex.qs))
            routes = self._routes(questions, self.tokenizer, self.cfg)
            for name, r in routes.items():
                if r.mode is not Mode.LABEL_TOKEN:
                    raise RuntimeError(f"question '{name}' would be answered by Lev's large-list head, which is not "
                                       "trained")
            prefix, _ = self.engine._render(ex.request.state, questions, routes)
            pre_ids = self.tokenizer.encode(prefix, add_special_tokens=True)
            self._cache[key] = (pre_ids, {n: (questions[n], routes[n]) for n in questions})
        return self._cache[key]

    def _rows(self, ex: Example, i: int, perm):
        """[(order, token ids)] for question i: the served option orders when perm is None, else that one order."""
        pre_ids, qr = self._parsed(ex)
        name = ex.qs[i].id
        question, route = qr[name]
        if perm is None:
            _, variants = self.engine._render(ex.request.state, {name: question}, {name: route})
            return [(v.order, pre_ids + v.suffix_ids) for v in variants]
        r = self._build(ex.request.state, name, question, route.codes, self.cfg.layout, None, perm, self.style)
        return [(perm, pre_ids + self.tokenizer.encode(r.suffix, add_special_tokens=False))]

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        out = []
        for i, q in enumerate(ex.qs):
            if train and ex.targets[i] is None:
                continue
            perm = shuffled(len(q.keys), rng) if (train and rng is not None and q.type == "choice") else None
            rows = self._rows(ex, i, perm)
            u = Unit(ex, [i], [perm], cost=sum(len(r) for _, r in rows))
            u.rows = rows
            out.append(u)
        return out

    # -- scoring ----------------------------------------------------------------------------------------------------
    def _forward(self, rows: list[list[int]]):
        """Right-padded rows -> label-position hidden states -> LM head on those rows only: (rows, vocabulary)."""
        import torch
        width = max(len(r) for r in rows)
        ids = torch.tensor([r + [self.pad_id] * (width - len(r)) for r in rows], device=self.device)
        mask = torch.tensor([[1] * len(r) + [0] * (width - len(r)) for r in rows], device=self.device)
        # Evaluation runs in native bf16 with the LoRA in fp32, as served; training under bf16 autocast (8% faster).
        with autocast(self.device, self.autocast if torch.is_grad_enabled() else None):
            hidden = self.root.model(input_ids=ids, attention_mask=mask, use_cache=False).last_hidden_state
            last = torch.tensor([len(r) - 1 for r in rows], device=hidden.device)
            return self.root.lm_head(hidden[torch.arange(len(rows), device=hidden.device), last])

    def _readout(self, u: Unit, logits):
        """Log-probabilities over q.keys from this unit's rows (the served readout at temperature 1)."""
        import torch
        from lev.router import BINARY_NOUL
        q = u.example.qs[u.qi[0]]
        _, qr = self._parsed(u.example)
        question, route = qr[q.id]
        cand = self.readout.candidate_ids(route.codes)
        lps = [unpermute(torch.log_softmax(z[cand].float(), dim=-1), order) for (order, _), z in zip(u.rows, logits)]
        lp = lps[0] if len(lps) == 1 else torch.logsumexp(torch.stack(lps), 0) - math.log(len(lps))  # mean of probs
        if q.type != "noul":
            return lp                                              # choice keys / score levels, in q.keys order
        if route.reason == BINARY_NOUL:                            # untrained serving only: [yes, no]
            return torch.stack([lp[1], lp[0]])
        # The 0-8 rating: p(yes) = sum(i/8 p_i), p(no) = sum((1 - i/8) p_i), both as stable log-sum-exps.
        top = lp.numel() - 1
        w = torch.arange(top + 1, device=lp.device, dtype=lp.dtype) / top
        log_yes = torch.logsumexp(lp[1:] + torch.log(w[1:]), 0)
        log_no = torch.logsumexp(lp[:-1] + torch.log(1 - w[:-1]), 0)
        return torch.stack([log_no, log_yes])                      # q.keys = ["false", "true"]

    def score(self, units: list[Unit]):
        import torch
        if torch.is_grad_enabled():
            return self._score_rows(units)                 # training: one right-padded batch
        # Evaluation: the rows of one example's questions form one batch, as system_one batches a request (when all of
        # an example's questions are scored together, in order, this is exactly the served forward pass).
        groups: dict[int, list[int]] = {}
        for j, u in enumerate(units):
            groups.setdefault(id(u.example), []).append(j)
        out: list = [None] * len(units)
        for idx in groups.values():
            for j, row in zip(idx, self._score_rows([units[j] for j in idx])):
                out[j] = row
        return out

    def _score_rows(self, units: list[Unit]):
        logits = self._forward([r for u in units for _, r in u.rows])
        out, k = [], 0
        for u in units:
            n = len(u.rows)
            out.append([self._readout(u, logits[k:k + n])])
            k += n
        return out

    @staticmethod
    def attach(adapter, delta) -> None:
        eng = adapter.engine
        root = lev_served(eng)                     # Lev's temperatures off
        # The delta is the whole trained adapter: attach_delta re-creates the released adapter's layers (same name,
        # rank and targets) and loads the trained tensors into them, unmerged, as in training.
        attach_delta(root, delta, next(root.parameters()).device)


TRAINER = LevTrainer
