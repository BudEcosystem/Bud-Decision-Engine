"""Kev (Jared Palmer): a pointer head on a Qwen backbone that carries a LoRA adapter. The state is read once and every
question is its own branch off it, so one example (all its questions) is one scoring unit.

Best practice for this family (training_research/models/kev/ANALYSIS.md):
- start exactly at the released model (sections 4 and 8: a fine-tune that did not start from the released adapter fell
  from 0.83 to 0.33 on Kev's suite, PR #9). The released LoRA is merged into the base in memory by kev's own loader, in
  bf16, exactly as the studio serves it (section 9); a NEW LoRA, zero at the start, goes on top on the released targets
  (attention and MLP projections, and on Qwen3.5 the Gated DeltaNet projections, section 2.5), with the pointer head
  trainable;
- keep general ability with a low learning rate, few passes and replay (section 8: kev's deltas use lr 2e-5, one epoch
  and 2,000 replay records). A fresh LoRA starts at zero rather than at a trained adapter, so it needs a higher rate
  than kev's to move at all: 5e-5 (on Julia, a fresh LoRA at 1e-4 forgot general decisions and 5e-5 did not); the
  head, which is the released one, keeps kev's rate;
- log-probabilities at temperature 1 (section 8: the head divides by its stored temperature only at inference, and the
  studio applies the temperature the engine fits);
- choice options shuffled while training, as kev.train does: Kev is position-sensitive (section 11.7, ~7% argmax flips
  under reordering);
- text built by the serving adapter's `kev_record` and encoded at kev's serving limits, so a long state is never cut to
  kev.train's 384-token default (section 4, issue #155);
- Qwen3.5 (Kev 4B) is a hybrid whose recurrent layers cannot take the block-causal mask, so each question is a row
  continuing the state (section 2.3). Training runs kev's differentiable forms (packed with the branch mask on Qwen2.5,
  one row per question on Qwen3.5); evaluation runs the serving computation itself, one example at a time (on Qwen3.5
  the state once, then its question rows from the state's cache), so measured and served answers are the same numbers.
  In bf16 the two forms differ by up to 0.03 in probability at the same weights (measured, Kev 4B); kev's shared-prefix
  training form differed by 0.05, so it is not used.
"""
from __future__ import annotations

from pathlib import Path

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, attach_delta, freeze, inject_lora, shuffled, unfreeze, unpermute

KEV_MAX_OPTIONS = 255            # kev.api.MAX_OPTIONS


def _released(spec):
    """The released checkpoint (adapter config and head.pt), read from the local Hugging Face cache."""
    from kev.checkpoint import Checkpoint  # type: ignore
    return Checkpoint(spec.repo.id)


def _served_dtype(spec) -> str:
    """The number precision the studio serves this model in by default (the "Number precision" load option)."""
    for o in spec.load_options:
        if o.get("key") == "dtype":
            return o.get("default") or "bf16"
    return "bf16"


class KevTrainer(FamilyTrainer):
    family = "kev"
    head_names = ("head",)
    lora_root = "lm"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        ck = _released(spec)
        targets = sorted(ck.adapter_config()["target_modules"])     # as released
        hybrid = ck.hybrid_base()                                    # Qwen3.5 (Kev 4B): one row per question
        # Gradient checkpointing for both: without it Kev 0.5B peaked at 23 GB on 4,800 training tokens (the fp32
        # LoRA branches keep fp32 copies of every projection's input), which pushed this shared machine into swap.
        return Recipe(lora_targets=targets, lora_r=16, lora_alpha=32, lora_dropout=0.05,
                      lr=5e-5, head_lr=2e-5, max_epochs=2.0, effective_batch=16,
                      token_budget=4096 if hybrid else 8192, max_tokens=8192, replay_ratio=0.5, kl_weight=1.0,
                      shuffle_options=True, grad_checkpointing=True,
                      notes=["released LoRA merged in memory; a new zero-initialised LoRA on the released targets",
                             "pointer head trained at kev's delta rate (2e-5)"])

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if len(q.keys) > KEV_MAX_OPTIONS:
                return f"{spec.name} takes at most {KEV_MAX_OPTIONS} options"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        # measured peaks on the GB10 (bf16 backbone, gradient checkpointing): Kev 4B 16.1 GB at 8,000 training tokens
        return 16.0 if spec.memory_gb > 4 else 6.0

    # -- model --------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        from ...adapters.kev_adapter import kev_load_options
        stage("Reading the adapter and pointer head")
        ck = _released(spec)
        opts = kev_load_options(dev.kind, {"dtype": _served_dtype(spec)}, finetune=True)
        stage(f"Loading {spec.base.id if spec.base else 'the base model'} and merging the released adapter")
        tok, model = ck.load(dev.kind, opts)
        if getattr(model, "backend", "torch") != "torch":
            raise RuntimeError("Kev trains with PyTorch only")
        model.head.temperature = 1.0            # training and evaluation read raw logits (T = 1)
        self.tok, self.model = tok, model
        self.root = model
        self.device = dev.kind
        self.autocast = None                    # the backbone runs in its served dtype; LoRA and head in fp32
        freeze(model)
        inject_lora(model.lm, recipe.lora_targets, recipe.lora_r, recipe.lora_alpha, recipe.lora_dropout)
        if recipe.train_head:
            unfreeze(model.head)
        for _, p in model.named_parameters():
            if p.requires_grad:
                p.data = p.data.float()
        if recipe.grad_checkpointing:
            model.lm.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.eval()

    def _encode(self, ex: Example, orders: dict | None = None) -> dict:
        from kev.model import SERVE_MAX_BRANCH, SERVE_MAX_STATE  # type: ignore
        from ...adapters.kev_adapter import kev_record
        rec = kev_record(ex.request, ex.qs, orders=orders)
        return self.model.encode(self.tok, rec, max_state=SERVE_MAX_STATE, max_branch=SERVE_MAX_BRANCH)

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        perms, orders = [], {}
        for q in ex.qs:
            if train and rng is not None and q.type == "choice" and len(q.keys) > 1:
                p = shuffled(len(q.keys), rng)
                orders[q.id] = [q.keys[k] for k in p]
                perms.append(p)
            else:
                perms.append(None)
        enc = self._encode(ex, orders)
        n_state = enc["seg"].count(0)
        n = len(enc["ids"])
        # tokens a training pass computes: the packed sequence, or on Qwen3.5 one row (state + branch) per question
        cost = n + (len(ex.qs) - 1) * n_state if self.model.hybrid else n
        u = Unit(ex, list(range(len(ex.qs))), perms, cost=cost)
        u.enc = enc
        return [u]

    def _served_logits(self, enc):
        """The studio's computation for one request (kev.serve.Server -> DecisionModel.probs_batch -> probs_one, the
        state not yet cached), as logits rather than probabilities:
        - rows form (Qwen3.5, or a very long record): the state once, then every question's row continuing from its
          cache (DecisionModel.probs_and_prefix);
        - a packed record whose state the prefix cache keeps: one packed pass that also fills a cache (same function);
        - a shorter state: the plain packed pass (DecisionModel.probs).
        Scoring examples one at a time, as served, matters under bf16: batching changes the padding, and padding alone
        moved Kev 0.5B's probabilities by up to 0.12."""
        import torch
        from kev.model import branch_mask_batch, rows_of  # type: ignore
        from transformers import DynamicCache
        m = self.model
        if m.rows_form([enc]):
            n_state, cache, _ = m.prefix(enc)
            _, _, rows = rows_of(enc)
            hs = m._rows_hidden([(r["ids"], r["pos"]) for r in rows], cache=cache, prefix_len=n_state)
            return [m.head(h[r["decide"]], h[torch.tensor(r["opts"], device=m.device)]) for h, r in zip(hs, rows)]
        if enc["seg"].count(0) < m.prefix_min_tokens:
            return m.forward_batch([enc])[0]
        ids = torch.tensor([enc["ids"]], device=m.device)
        pos = torch.tensor([enc["pos"]], device=m.device)
        mask = branch_mask_batch([enc["seg"]], m.device, dtype=next(m.lm.parameters()).dtype,
                                 opts=[enc["opt"]] if enc.get("option_isolation") else None)
        out = m.lm(input_ids=ids, position_ids=pos, attention_mask=mask, past_key_values=DynamicCache(config=m.lm.config),
                   use_cache=True)
        return m._readout(out.last_hidden_state[0].float(), enc)

    def score(self, units: list[Unit]):
        import torch
        if torch.is_grad_enabled():                  # training: kev's differentiable batch (rows form on Qwen3.5)
            logits = self.model.forward_batch([u.enc for u in units])
        else:                                        # evaluation: each example exactly as the studio serves it
            logits = [self._served_logits(u.enc) for u in units]
        out = []
        for u, zs in zip(units, logits):
            out.append([unpermute(torch.log_softmax(zs[i].float(), -1), u.perm[j] if u.perm else None)
                        for j, i in enumerate(u.qi)])
        return out

    # -- serving ------------------------------------------------------------------------------------------------------
    @staticmethod
    def attach(adapter, delta) -> None:
        srv = adapter.server
        model = srv.model
        if getattr(model, "backend", "torch") != "torch":
            raise RuntimeError("A fine-tuned Kev runs with PyTorch only")
        if any(hasattr(layer.mlp, "gate_up") for layer in getattr(model.lm, "layers", [])):
            raise RuntimeError("This Kev was loaded with its fused serving kernels, which cannot carry a fine-tune")
        delta = Path(delta)
        with srv.lock:
            attach_delta(model, delta, model.device)      # unmerged; the LoRA in fp32, as trained
            model.head.temperature = 1.0        # the studio applies the temperature the trainer fitted
            adapter.temperature = 1.0
            srv.prefix_cache.clear()            # nothing cached from the released model may be reused
            model.eval()


TRAINER = KevTrainer
