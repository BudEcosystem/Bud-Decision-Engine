"""Julia 1 (Supersonic Labs): mmBERT-small encoder + decision head, one row per question.

Best practice for this family (training_research/models/julia-1/ANALYSIS.md):
- LoRA r16 on the encoder's attention and MLP projections (ModernBERT `Wqkv`, `Wo`, `Wi`) at 5e-5; the decision head
  stays frozen. Measured on capsotu topics, three seeds each (docs/trainer/RESULTS.md):
    LoRA 1e-4 + head trained      51 -> 69%, guard -4.0: rejected (forgot)
    LoRA 1e-4, head frozen        51 -> 69%, guard -4.3: rejected; gentle retry 63%, guard -2.3: rejected
    LoRA 5e-5, head frozen        +19.3 / +14.8 / +16.8 points, guard -0.3 / -1.3 / -0.7: all accepted first time
    attention-only LoRA, 1e-4     +16.1 / +16.1 / +12.9 points, guard -0.7 / -1.3 / -1.3: one needed the retry
  The head is shared by every question type, so moving it moves every answer; at 1e-4 the encoder LoRA moved general
  answers too far (held-out divergence from the released model 0.2-0.4 nats, against 0.1-0.17 at 5e-5);
- Julia memorises easily (96.5% on typed-decisions' training split vs 73.2% on its test split), so replay with
  distillation and early stopping matter more here than anywhere;
- rows are built by the serving adapter's own function (JSON for structured states), choice options are shuffled
  during training because the head scores option markers by position in the sequence.
"""
from __future__ import annotations

import math

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, approx_tokens, autocast, freeze, inject_lora, shuffled, unfreeze, unpermute


class JuliaTrainer(FamilyTrainer):
    family = "julia"
    head_names = ("head", "type_emb", "scorer")
    lora_root = "encoder"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        return Recipe(lora_targets=["Wqkv", "Wo", "Wi"], lora_r=16, lora_alpha=32, lr=5e-5, head_lr=5e-5,
                      max_epochs=4, effective_batch=16, token_budget=12000, max_tokens=2048, replay_ratio=1.0,
                      kl_weight=1.0, shuffle_options=True, train_head=False)

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if not 2 <= len(q.keys) <= 20:
                return "Julia 1 answers questions with 2 to 20 options"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        return 4.0          # measured: 1.05 GB (LoRA, spike), 5.1 GB full fine-tuning at batch 8

    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        import torch
        from ...adapters.julia_adapter import JuliaAdapter
        stage("Loading the encoder and decision head")
        a = JuliaAdapter(spec, {"device": dev.kind, "max_length": 8192}, lambda *x: None)
        a.load()
        self.adapter = a
        self.root = a.engine.model
        self.collate = a.engine.collate
        self.device = dev.kind
        self.autocast = getattr(torch, dev.autocast) if dev.autocast else None
        freeze(self.root)
        if recipe.method == "full":
            unfreeze(self.root.encoder)
        else:
            inject_lora(self.root.encoder, recipe.lora_targets, recipe.lora_r, recipe.lora_alpha, recipe.lora_dropout)
        if recipe.train_head:
            for h in self.head_names:
                unfreeze(getattr(self.root, h))
        for n, p in self.root.named_parameters():
            if p.requires_grad:
                p.data = p.data.float()

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        from ...adapters.julia_adapter import julia_state
        from ...contract import render
        state = julia_state(ex.request, render(ex.request.state))
        size = approx_tokens(state if isinstance(state, str) else render(state))
        out = []
        for i, q in enumerate(ex.qs):
            perm = shuffled(len(q.keys), rng) if (train and rng is not None and q.type == "choice") else None
            opts_len = sum(approx_tokens(o) for o in (q.labels + [d for d in q.descriptions if d]))
            out.append(Unit(ex, [i], [perm], cost=size + opts_len + approx_tokens(q.instructions) + 8))
        return out

    def _rows(self, units: list[Unit]) -> list[dict]:
        from ...adapters.julia_adapter import julia_option_texts, julia_row, julia_state
        from ...contract import render
        rows = []
        for u in units:
            q = u.example.qs[u.qi[0]]
            opts = julia_option_texts(q)
            perm = u.perm[0] if u.perm else None
            if perm is not None:
                opts = [opts[k] for k in perm]
            rows.append(julia_row(q, julia_state(u.example.request, render(u.example.request.state)), opts))
        return rows

    def score(self, units: list[Unit]):
        import torch
        rows = self._rows(units)
        batch = {k: v.to(self.device) for k, v in self.collate(rows, include_targets=False).items()}
        with autocast(self.device, self.autocast):
            logits = self.root(**batch)
        out = []
        for u, z, r in zip(units, logits, rows):
            lp = torch.log_softmax(z[:len(r["options"])].float(), -1)
            out.append([unpermute(lp, u.perm[0] if u.perm else None)])
        return out

    @staticmethod
    def attach(adapter, delta) -> None:
        from .base import attach_delta
        attach_delta(adapter.engine.model, delta, adapter.engine.device)


TRAINER = JuliaTrainer
del math
