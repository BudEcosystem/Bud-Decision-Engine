"""Laya (ConvAI Innovations): a ModernBERT / mmBERT encoder plus an option-marker decision head, one row per question.
Covers Laya, Laya Multilingual and Laya Typed-Decisions.

Best practice for this family (training_research/models/laya/ANALYSIS.md):
- the publisher's recipe trains the whole encoder (2.5e-5) and the head (1e-4) with soft cross-entropy plus a
  policy-gradient term that measured no better than soft cross-entropy alone (section 3.2, issue #741), and has no
  replay, so it yields a specialist (section 8.5). Here: LoRA r16 at 1e-4 on the encoder's attention and MLP
  projections (ModernBERT and mmBERT both name them `Wqkv`, `Wo`, `Wi`) with the engine's soft cross-entropy and
  replay distillation. The decision head (two transformer layers, type embedding, scorer) is shared by every question
  and stays frozen by default (`train_head` trains it at 5e-5): on capsotu topics, LoRA 2e-4 with the head trained
  reached 76% from 59% but moved the general guard set down 1.7 points (p_worse 0.88). The act/escalate head is
  always frozen: it is untrained in every release and no fine-tune trains it (section 2);
- memory: the runtime's fp32 weights under bf16 autocast, LoRA, no checkpointing peak at 13.6 GB for a 4,096-token
  budget (23.7 GB at 8,192) on English Laya; activation checkpointing brings that to 3.1 GB (4.1 GB) at about 2.5x
  the step time on the shared GB10. `recipe` takes the fast path only when its `estimate_gb` fits in the memory free
  at that moment (`device.available_bytes`, minus 2 GB), otherwise it turns checkpointing on;
- the publisher's fine-tune scripts do not shuffle options, while the base model was trained with shuffled options and
  learns position habits without it (issue #131): choice options are shuffled during training through Laya's own
  `option_order`, so the sequence is built exactly as the runtime builds it;
- rows are built by the laya runtime's own functions (`_check_question`, `_to_internal`, `_encode_state`,
  `collate_items`) from the adapter's question dicts and state, with the adapter's `max_len` (512 for English Laya,
  1024 for the other two) and the checkpoint's `head_max_len`, so a fine-tune is trained on the sequences it is
  served;
- the released checkpoints carry their own temperatures (`temperature`, `temperature_by_options`); training reads
  the raw logits at T = 1 and `attach` neutralises them, because the studio applies the fitted temperature itself.
"""
from __future__ import annotations

import json

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, approx_tokens, autocast, freeze, inject_lora, shuffled, unfreeze, unpermute


def _serving_options(spec, dev) -> dict:
    """The load options the studio's worker uses for this model (the catalog defaults), on the training device."""
    opts = {o["key"]: o["default"] for o in spec.options()}
    opts["device"] = dev.kind
    return opts


class LayaTrainer(FamilyTrainer):
    family = "laya"
    head_names = ("head", "type_emb", "scorer")
    lora_root = "encoder"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        r = Recipe(lora_targets=["Wqkv", "Wo", "Wi"], lora_r=16, lora_alpha=32, lora_dropout=0.05,
                   lr=1e-4, head_lr=5e-5, max_epochs=4, effective_batch=16, token_budget=4096,
                   max_tokens=8192, replay_ratio=0.5, kl_weight=1.0, shuffle_options=True, train_head=False)
        # Activation checkpointing cuts the peak from 13.6 to 3.1 GB (Laya, 4,096 tokens) at about 2.5x the step
        # time: use it only when the faster path does not fit in the memory free right now.
        try:
            from .. import device as devmod
            r.grad_checkpointing = self.estimate_gb(spec, r) > devmod.available_bytes(dev) / 1e9 - 2.0
        except Exception:  # noqa: BLE001 - no device memory reading: take the small path
            r.grad_checkpointing = True
        return r

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if not 2 <= len(q.keys) <= 20:
                return f"{spec.name} answers questions with 2 to 20 options"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        """Measured on the GB10 with Laya (ModernBERT-large, bf16 autocast, fp32 weights as the runtime keeps them),
        512-token rows, task + replay rows per micro-batch: 13.6 GB at a 4,096-token budget, 23.7 GB at 8,192; with
        activation checkpointing 3.1 and 4.1 GB. About 1.9 GB (0.19 checkpointed) per 1,000 tokens in a step, on
        ~1.9 GB of weights and optimizer state. mmBERT-base (Multilingual) is smaller per token."""
        large = spec.memory_gb >= 1.1
        per_k = (1.9 if large else 1.2) if not recipe.grad_checkpointing else (0.19 if large else 0.13)
        tokens_k = recipe.token_budget * (1 + recipe.replay_ratio) / 1000
        return round((1.9 if large else 1.4) + per_k * tokens_k + 1.5, 1)

    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        import torch
        from ...adapters.laya_adapter import LayaAdapter
        stage("Loading the encoder and decision head")
        a = LayaAdapter(spec, _serving_options(spec, dev), lambda *x: None)
        a.load()
        agent = a.agent
        if agent.device.type != dev.kind:
            raise RuntimeError(f"{spec.name} could not be placed on the GPU (the laya runtime fell back to the "
                               "processor). Eject loaded models or close other programs, then try again.")
        self.adapter, self.agent = a, agent
        self.max_len = int(a.max_len)
        self.head_max_len = int(agent.cfg.get("head_max_len", 192))
        self.root = agent.model
        self.device = dev.kind
        self.autocast = getattr(torch, dev.autocast) if dev.autocast else None
        if not agent.amp_enabled:
            self.autocast = None      # served in full precision here (an Intel GPU, the processor): train it that way
        # The runtime keeps fp32 weights and autocasts each forward; the frozen base stays that way (0.8-1.7 GB) so
        # training runs the exact arithmetic the studio serves.
        freeze(self.root)
        if recipe.method == "full":
            unfreeze(self.root.encoder)
        else:
            inject_lora(self.root.encoder, recipe.lora_targets, recipe.lora_r, recipe.lora_alpha, recipe.lora_dropout)
        if recipe.train_head:
            for h in self.head_names:
                unfreeze(getattr(self.root, h))
        else:
            self.head_names = ()          # the released head stays as it is and is not exported
        for _, p in self.root.named_parameters():
            if p.requires_grad:
                p.data = p.data.float()
        if recipe.grad_checkpointing:
            self.root.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
            self.root.head_checkpointing = True

    # -- encoding ---------------------------------------------------------------------------------------------------
    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        from ...adapters.laya_adapter import laya_state
        from ...contract import render
        st = laya_state(ex.request, render(ex.request.state))
        size = approx_tokens(st if isinstance(st, str) else json.dumps(st, ensure_ascii=False))
        max_len, head_max = getattr(self, "max_len", 1024), getattr(self, "head_max_len", 256)
        out = []
        for i, q in enumerate(ex.qs):
            perm = shuffled(len(q.keys), rng) if (train and rng is not None and q.type == "choice") else None
            head = approx_tokens(q.instructions) + 4 + sum(min(49, approx_tokens(o) + 1) for o in q.option_texts())
            out.append(Unit(ex, [i], [perm], cost=min(max_len, size + min(head, head_max) + 4)))
        return out

    def _items(self, units: list[Unit]) -> list[dict]:
        """One Laya row per unit, built by the runtime's own validation, normalisation and sequence builder from the
        adapter's question dict and state (the path `Agent.predict` takes). A shuffled choice uses the runtime's
        `option_order`: slot s shows option perm[s], the same convention as `base.unpermute`."""
        from ...adapters.laya_adapter import laya_questions, laya_state
        from ...contract import render
        agent = self.agent
        items = []
        for u in units:
            ex = u.example
            q = ex.qs[u.qi[0]]
            qdef = dict(laya_questions([q])[q.id])
            perm = u.perm[0] if u.perm else None
            if perm is not None:
                qdef["option_order"] = list(perm)
            agent._check_question(q.id, qdef)
            internal = {q.id: agent._to_internal(qdef)}
            state = laya_state(ex.request, render(ex.request.state))
            items.append(agent._encode_state(state, [q.id], internal, max_len=self.max_len)[0])
        return items

    def score(self, units: list[Unit]):
        import torch
        from laya.common import collate_items
        items = self._items(units)
        b = collate_items([[it] for it in items], self.agent.tok.pad_token_id)
        args = [b[k].to(self.device) for k in ("input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype")]
        with autocast(self.device, self.autocast):
            logits, _ = self.root(*args)
        out = []
        for u, z, it in zip(units, logits, items):
            lp = torch.log_softmax(z[:len(it["markers"])].float(), -1)
            out.append([unpermute(lp, u.perm[0] if u.perm else None)])
        return out

    # -- serving ----------------------------------------------------------------------------------------------------
    @staticmethod
    def attach(adapter, delta) -> None:
        from .base import attach_delta
        agent = adapter.agent
        attach_delta(agent.model, delta, agent.device)
        # One temperature, not two (docs/trainer/ARCHITECTURE.md section 10): the studio applies the fine-tune's
        # fitted temperature, so the runtime's own (per type, per option count, per language) must be 1.
        agent.temperature = [1.0, 1.0, 1.0]
        agent.temperature_by_options = {}
        agent.lang_temperatures = {}


TRAINER = LayaTrainer
