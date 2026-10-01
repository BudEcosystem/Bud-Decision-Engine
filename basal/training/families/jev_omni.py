"""Jev-Omni (Gemma 4 12B + a positional 256-slot decision head), one question per forward pass.

Best practice for this family (training_research/models/jev-omni/ANALYSIS.md):
- the publisher's method, at a small rank (sections 3 and 4, option B): LoRA on the seven projections of the 48
  language-model layers (`q/k/v/o/gate/up/down`), plus the head's linear layer; the head's stored mean and spread
  (`mu`, `sd`) stay frozen, and so do the embeddings, norms and the image/audio projections;
- rank 16 with alpha = rank and 5e-5 for both LoRA and head: the knob table's starting point for narrow tasks; the
  research's real run of this recipe went 0.55 -> 0.66 accuracy and 1.27 -> 0.79 log loss in 20 steps (8 questions
  each) at about 24.5 GB (training_research/models/jev-omni/smoke/lead_step8_jevomni_lora_r16.log);
- 1-2 passes, replay 0.5x (section 8), gradient checkpointing (bf16 base, fp32 trainable weights);
- the head is positional (slot k = option k+1), so choice options are shuffled during training; yes/no stays
  [No, Yes] and a scale stays low -> high (section 4, "Option order");
- the model input is JevOmni.predict's, rebuilt here step for step (media first, the checkpoint's own `_prompt`, the
  same chat-template call, bf16 floats, bf16 autocast) from the adapter's shared option/question/media functions,
  and read the same way: the loader's hook on the text model, last token, then the head. One row per pass, never
  padded, exactly as served. Only the first media file is used, as when serving;
- the delta stays unmerged (a merged export would be another 24 GB, and merging under bf16 rounds away part of the
  fine-tune: docs/trainer/ARCHITECTURE.md Appendix A).
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, approx_tokens, autocast, freeze, inject_lora, shuffled, unfreeze, unpermute

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
LANGUAGE_MODEL = "model.model.language_model"     # from the root below: JevOmni.model (Gemma4Unified...Generation)
MEDIA_TOKENS = {"image": 280, "audio": 750, "video": 4400}   # ANALYSIS.md section 2, step 6


def _root(model, head):
    import torch

    class Root(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model, self.head = model, head

    return Root()


def model_inputs(clf, state: str, question: str, options: list[str], media: str | None = None,
                 modality: str = "text", video_frames: int = 16) -> dict:
    """JevOmni.predict's model inputs (the checkpoint's jev_omni.py), built the same way: media first, then the
    checkpoint's `_prompt`; the chat template with the generation prompt and thinking off. Tensors stay on the CPU;
    the caller moves them to the device with floats in bf16, as predict does."""
    import subprocess
    import tempfile

    from PIL import Image
    jev = sys.modules[type(clf).__module__]
    content, temporary = [], None
    if modality == "image":
        content.append({"type": "image", "image": Image.open(media).convert("RGB")})
    elif modality == "video":
        content.extend({"type": "image", "image": frame} for frame in jev._video_frames(media, video_frames))
    elif modality == "audio":
        temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temporary.close()
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-t", "30", "-ac", "1", "-ar", "16000",
                        temporary.name], check=True)
        content.append({"type": "audio", "audio": temporary.name})
    content.append({"type": "text", "text": jev._prompt(state, question, options)})
    try:
        inputs = clf.processor.apply_chat_template(
            [{"role": "user", "content": content}], add_generation_prompt=True,
            tokenize=True, return_dict=True, return_tensors="pt", enable_thinking=False)
    finally:
        if temporary is not None:
            Path(temporary.name).unlink(missing_ok=True)
    return dict(inputs)


class JevOmniTrainer(FamilyTrainer):
    family = "jev_omni"
    head_names = ("head",)                         # its `linear` trains; the `mu`/`sd` buffers are stored unchanged
    lora_root = LANGUAGE_MODEL

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        return Recipe(lora_targets=list(TARGETS), lora_r=16, lora_alpha=16, lora_dropout=0.0, lr=5e-5, head_lr=5e-5,
                      weight_decay=0.0, warmup=0.1, max_epochs=2, effective_batch=8, token_budget=4096,
                      max_tokens=8192, replay_ratio=0.5, kl_weight=1.0, shuffle_options=True, patience=3,
                      grad_checkpointing=True)

    def supports(self, spec, ex: Example) -> str | None:
        from ...adapters.jev_omni_adapter import jev_options
        for q in ex.qs:
            if not 2 <= len(q.keys) <= 256:
                return "Jev-Omni answers questions with 2 to 256 options"
            opts = jev_options(q)
            if len(set(opts)) != len(opts):
                return "Jev-Omni needs every option of a question to read differently"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        # Measured: ~24.5 GB peak for LoRA r16 + head, 1 row x 8, gradient checkpointing (research, GB10). A micro-batch
        # keeps a few rows' checkpointed activations alive until its backward pass: a little more.
        return 27.0

    # -- model ------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        import torch
        from ...adapters.jev_omni_adapter import JevOmniAdapter
        stage("Loading Gemma 4 12B and the decision head (about 24 GB)")
        a = JevOmniAdapter(spec, {"device": dev.kind}, lambda *x: None)
        a.load()
        clf = a.clf
        self.adapter, self.clf = a, clf
        self.root = _root(clf.model, clf.head)
        self.device = dev.kind
        # JevOmni.predict runs under bf16 autocast whatever the device reports; so must training, for parity.
        self.autocast = torch.bfloat16 if dev.kind == "cuda" else (getattr(torch, dev.autocast) if dev.autocast else None)
        freeze(self.root)
        inject_lora(self.root.model.model.language_model, recipe.lora_targets, recipe.lora_r, recipe.lora_alpha,
                    recipe.lora_dropout)
        if recipe.train_head:
            unfreeze(clf.head.linear)
        for p in self.root.parameters():
            if p.requires_grad:
                p.data = p.data.float()
        if recipe.grad_checkpointing:
            clf.model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        self._inputs: dict = {}                    # tokenised text-only rows, reused by every evaluation

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        from ...adapters.jev_omni_adapter import jev_options, jev_question
        from ...contract import render
        state = render(ex.request.state)
        media = ex.request.media[0].type if ex.request.media else None
        extra = MEDIA_TOKENS.get(media, 0) + 40    # chat template and instructions around the prompt
        base = approx_tokens(state) + extra
        out = []
        for i, q in enumerate(ex.qs):
            perm = shuffled(len(q.keys), rng) if (train and rng is not None and q.type == "choice") else None
            size = base + approx_tokens(jev_question(q)) + sum(approx_tokens(o) + 2 for o in jev_options(q))
            out.append(Unit(ex, [i], [perm], cost=size))
        return out

    def _unit_inputs(self, u: Unit) -> tuple[dict, int]:
        import torch
        from ...adapters.jev_omni_adapter import jev_media, jev_options, jev_question
        from ...contract import render
        ex = u.example
        q = ex.qs[u.qi[0]]
        perm = u.perm[0] if u.perm else None
        options = jev_options(q)
        if perm is not None:
            options = [options[k] for k in perm]
        kw = jev_media([{"type": m.type, "path": m.path} for m in ex.request.media])
        key = (id(ex), u.qi[0], tuple(perm) if perm is not None else None)
        cpu = self._inputs.get(key) if not kw else None
        if cpu is None:
            cpu = model_inputs(self.clf, render(ex.request.state), jev_question(q), options, **kw)
            if not kw and perm is None:
                self._inputs[key] = cpu
        inputs = {k: v.to(self.device, dtype=torch.bfloat16) if torch.is_floating_point(v) else v.to(self.device)
                  for k, v in cpu.items()}
        return inputs, len(options)

    def score(self, units: list[Unit]):
        import torch
        clf = self.clf
        out = []
        for u in units:
            inputs, n = self._unit_inputs(u)
            with autocast(self.device, self.autocast):
                clf._capture.clear()
                clf.model(**inputs, use_cache=False, **clf._extra)
                z = clf.head(clf._capture.pop("hidden"), torch.tensor([n], device=self.device))[0, :n]
            lp = torch.log_softmax(z.float(), -1)
            out.append([unpermute(lp, u.perm[0] if u.perm else None)])
        return out

    def unload(self) -> None:
        clf = getattr(self, "clf", None)
        if clf is not None:
            clf._capture.clear()
        if getattr(self, "_inputs", None):
            self._inputs.clear()
        super().unload()

    # -- serving ----------------------------------------------------------------------------------------------------
    @staticmethod
    def attach(adapter, delta) -> None:
        """LoRA onto the loaded Gemma 4 language model and the trained head linear, unmerged. Jev-Omni has no
        temperature of its own (jev_omni.py applies none), so there is nothing to reset."""
        from .base import attach_delta
        clf = adapter.clf
        attach_delta(_root(clf.model, clf.head), delta, clf.device)


TRAINER = JevOmniTrainer
