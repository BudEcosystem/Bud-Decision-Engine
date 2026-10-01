"""CLM 8B (Contrastive LM): two projection heads on a frozen Qwen3-8B embedder.

Best practice for this family (training_research/models/clm/ANALYSIS.md):
- heads only (sections 1 and 4.1): the publisher trains `state_head` and `action_head` (19M parameters) and nothing
  else; there is no LoRA path, and a frozen encoder means every text is embedded exactly once;
- embeddings come from the serving adapter's own embedder (`LocalQwenEmbedder`: Qwen3-8B, no special tokens, left
  truncation and padding to 2,048 tokens, last token, L2-normalised) and are cached by text hash for the whole job:
  embedding dominates the cost (section 7), the heads take milliseconds;
- the texts are the engine's own (`clm_pairs` -> `clm.schema.build_pairs`): the state with the question's instructions
  appended, and one text per option;
- per question, softmax(scale * cos(state_head(s), action_head(a))) over its own options: the publisher's `--loss
  softce` (section 4.1; 0.660 on typed-decisions, against 0.685 for InfoNCE); AdamW 5e-4, no weight decay, warm start
  from the released heads (section 4.2);
- `logit_scale` is not trained: exp(4.6132) = 100.8 is above the cap of 100, so it has no gradient (section 4.4 #1).
  Calibration is the studio's fitted temperature;
- a dual encoder is blind to option order, so options are never shuffled.
"""
from __future__ import annotations

import hashlib

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, approx_tokens, freeze, unfreeze

MAX_TOKENS = 2048            # the embedder keeps the last 2,048 tokens of every text (basal/adapters/clm_adapter.py)


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _root(state_head, action_head, logit_scale: float):
    """One module owning everything the delta holds: the two heads (trained) and the logit scale (stored, frozen)."""
    import torch

    class Scale(torch.nn.Module):
        def __init__(self, v: float):
            super().__init__()
            self.register_buffer("logit_scale", torch.tensor(float(v)))

    class Heads(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.state_head, self.action_head, self.scale = state_head, action_head, Scale(logit_scale)

    return Heads()


class ClmTrainer(FamilyTrainer):
    family = "clm"
    head_names = ("state_head", "action_head", "scale")
    lora_root = None

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        return Recipe(method="heads", lora_targets=[], lr=5e-4, head_lr=5e-4, weight_decay=0.0, warmup=0.1,
                      max_epochs=20, effective_batch=128, token_budget=16 * MAX_TOKENS, max_tokens=MAX_TOKENS,
                      replay_ratio=0.5, kl_weight=1.0, shuffle_options=False, patience=4, max_replay=1500,
                      notes=["heads only: the Qwen3-8B encoder is frozen and every text is embedded once"])

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if len(q.keys) < 2:
                return "CLM needs at least 2 options per question"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        # Qwen3-8B in bf16 is 16.4 GB; the research's embedding pass peaked at 15.9 GB resident with batch 16 x 2,048
        # tokens (training_research/models/clm/lead_step3_clm_typed.log), the heads at 2.0 GB.
        return 20.0

    # -- model ------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        import torch
        from ...adapters.clm_adapter import HEAD_NAME, ClmAdapter
        stage("Loading Qwen3-8B and the projection heads")
        a = ClmAdapter(spec, {"device": dev.kind}, lambda *x: None)
        a.load()
        hp = a.engine.heads[HEAD_NAME].ensure()
        self.adapter = a
        self.embedder = a.engine.embedder
        self.scale = float(hp.scale)                       # min(exp(logit_scale), 100), exactly as served
        logit_scale = float(torch.as_tensor(torch.load(hp.path, map_location="cpu", weights_only=True)["logit_scale"]))
        self.root = _root(hp.state_head, hp.action_head, logit_scale)
        self.device = str(next(hp.state_head.parameters()).device)
        self.autocast = None                               # the served heads run in fp32; so do the trained ones
        freeze(self.root)
        unfreeze(self.root.state_head)
        unfreeze(self.root.action_head)
        for p in self.root.parameters():
            if p.requires_grad:
                p.data = p.data.float()
        self.cache: dict[str, object] = {}                 # sha1(text) -> [4096] fp32 embedding on the device

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        from ...adapters.clm_adapter import clm_pairs
        pairs = clm_pairs(ex.request, ex.qs)
        out = []
        for i, q in enumerate(ex.qs):
            st, _, texts = pairs[q.id]
            # Options are embedded once per distinct text and shared by every question, so the state dominates. Any
            # length is accepted: the embedder keeps the last 2,048 tokens, as it does when serving.
            out.append(Unit(ex, [i], [None], cost=min(MAX_TOKENS, approx_tokens(st))))
        return out

    def _embed(self, texts: list[str]) -> None:
        """Embed every text not cached yet, with the serving embedder (each text as if alone: never padded)."""
        import numpy as np
        import torch
        todo = list(dict.fromkeys(t for t in texts if _key(t) not in self.cache))
        if not todo:
            return
        with torch.inference_mode():
            vecs, _ = self.embedder.embed(todo)
        self.embedder.cache.clear()                        # our own cache holds them; don't keep a second copy
        # Outside inference mode: the heads must be able to save these tensors for their backward pass.
        with torch.inference_mode(False), torch.no_grad():
            t = torch.from_numpy(np.ascontiguousarray(vecs, dtype=np.float32)).to(self.device)
            for i, s in enumerate(todo):
                self.cache[_key(s)] = t[i].clone()

    def score(self, units: list[Unit]):
        import torch
        from ...adapters.clm_adapter import clm_pairs
        rows = []
        for u in units:
            q = u.example.qs[u.qi[0]]
            st, keys, texts = clm_pairs(u.example.request, [q])[q.id]
            rows.append((q, st, keys, texts))
        self._embed([t for _, st, _, texts in rows for t in (st, *texts)])
        opts = list(dict.fromkeys(t for *_, texts in rows for t in texts))
        where = {t: i for i, t in enumerate(opts)}
        S = torch.stack([self.cache[_key(st)] for _, st, _, _ in rows])
        A = torch.stack([self.cache[_key(t)] for t in opts])
        zs = torch.nn.functional.normalize(self.root.state_head(S), dim=-1)
        za = torch.nn.functional.normalize(self.root.action_head(A), dim=-1)
        out = []
        for j, (q, _, keys, texts) in enumerate(rows):
            idx = torch.tensor([where[t] for t in texts], device=za.device)
            lp = torch.log_softmax(self.scale * (za[idx] @ zs[j]), -1)
            if keys != q.keys:                             # the engine's key order -> the studio's
                lp = lp[torch.tensor([keys.index(k) for k in q.keys], device=lp.device)]
            out.append([lp])
        return out

    def unload(self) -> None:
        cache = getattr(self, "cache", None)
        if cache:
            cache.clear()
        super().unload()

    # -- serving ----------------------------------------------------------------------------------------------------
    @staticmethod
    def attach(adapter, delta) -> None:
        """Load the trained heads into the serving engine's released heads. The logit scale is the model's own
        similarity scale, not a calibration temperature; the engine is always called at temperature 1, and the
        studio applies the fitted temperature itself."""
        import math
        from ...adapters.clm_adapter import HEAD_NAME
        from .base import attach_delta
        hp = adapter.engine.heads[HEAD_NAME].ensure()
        root = _root(hp.state_head, hp.action_head, 0.0)
        root.to(next(hp.state_head.parameters()).device)
        attach_delta(root, delta, next(hp.state_head.parameters()).device)
        hp.scale = float(min(math.exp(float(root.scale.logit_scale)), 100.0))
        hp.generation += 1                                 # invalidate any projections cached under the old heads


TRAINER = ClmTrainer
