"""Intern-Decision (InternLM): a Qwen3.5 vision-language model that answers every question of a record in one forward
pass, reading each answer from the language-model logits at the row before that question's `<decision>` marker.

Best practice for this family (training_research/models/intern-decision/ANALYSIS.md):
- the objective is the publisher's: the answer symbol predicted at the row before each `<decision>` marker (section 3,
  reproduced token for token in smoke/train_hf_masked.py). The trainer reads the log-softmax over the question's own
  symbols, which is exactly the probability `inference.py` serves (section 2), so the loss, the metrics and the
  studio's answers are one quantity;
- LoRA on the language model's q/k/v/o, gate/up/down and the linear-attention in_proj_qkv / in_proj_z / out_proj; the
  vision tower and projector stay frozen, as in the publisher's recipe (sections 2 and 3). There is no head: the
  readout is the language-model head, tied to the embeddings and frozen, which is also the strongest protection
  against forgetting (section 8);
- LoRA r16 / alpha 32 without dropout (the research's smoke/train_hf_masked.py), LR 5e-5 with warm-up and decay,
  replay at 25% with distillation towards the released model (sections 6 and 8: LoRA, 20-30% replay, a low LR and
  1-3 epochs keep general ability). 5e-5 rather than the research's 1e-4: at 1e-4 Julia's LoRA cost 4 guard points
  that 5e-5 did not. One pass by default (the engine gives small datasets up to two): training runs at a few hundred
  tokens per second on the GB10, so passes are the main cost of a run;
- small micro-batches (2,048 tokens, records of similar length): one record already saturates the GPU, so a padded
  batch gains nothing and pays for its padding (16 random records in one batch took 1.5x as long as one at a time);
- no option shuffling: the answers are positional letters and the studio always sends options in the user's order
  (sections 5 and 11.6), so training shows them in that order;
- one unit is one record: up to 16 questions, 62 options per question, 8,192 tokens including image tokens, rejected
  rather than truncated (section 11.8);
- the model input is built by the serving adapter's `intern_request` and the checkpoint's own `validate_request` and
  `HFBackend.encode` (section 4.2: token-identical to the publisher's training renderer);
- evaluation runs the frozen base in its native bf16 without autocast and the LoRA in fp32, exactly as the studio
  serves it, and makes the very forward call `inference.py` makes, so parity is exact. Training runs under bf16
  autocast (the LoRA weights stay fp32), which is 8% faster per step;
- the checkpoint's `DEFAULT_TEMPERATURE` (1.99) is neutralised in `attach`: the studio applies the fitted temperature
  (docs/trainer/ARCHITECTURE.md section 10).
"""
from __future__ import annotations

import json
import sys

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, _get, approx_tokens, attach_delta, autocast, freeze, inject_lora

TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
           "in_proj_qkv", "in_proj_z", "out_proj"]
MAX_LENGTH = 8192           # the checkpoint rejects longer inputs (image tokens included)
MARKER = "<decision>"


class InternTrainer(FamilyTrainer):
    family = "intern"
    head_names = ()
    lora_root = "model.language_model"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        return Recipe(lora_targets=list(TARGETS), lora_r=16, lora_alpha=32, lora_dropout=0.0, lr=5e-5, head_lr=0.0,
                      weight_decay=0.01, warmup=0.06, max_epochs=1, effective_batch=16, token_budget=2048,
                      max_tokens=MAX_LENGTH, replay_ratio=0.25, kl_weight=1.0, shuffle_options=False, patience=3,
                      grad_checkpointing=True, train_head=False,
                      notes=["LoRA on the language model only; vision tower and LM head frozen",
                             "options in the user's order (positional answer letters)"])

    def supports(self, spec, ex: Example) -> str | None:
        if len(ex.qs) > spec.max_questions:
            return (f"{spec.name} answers at most {spec.max_questions} questions at once (a pick-all-that-apply "
                    "question counts once per option)")
        for q in ex.qs:
            if len(q.keys) < 1:
                return f"{spec.name} needs at least one option per question"
        if MARKER in json.dumps(ex.record, ensure_ascii=False):
            return f"{spec.name} reserves the text {MARKER}, so examples can't contain it"
        if approx_tokens(json.dumps(ex.record, ensure_ascii=False)) > MAX_LENGTH:
            return f"{spec.name} reads at most {MAX_LENGTH} tokens per example"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        # Measured on the GB10: 13.8 GB peak training 16 records (6.5k tokens) in one batch with checkpointing; the model
        # loads straight onto the GPU (InternAdapter), so loading peaks at the model's 9.1 GB. Margin for 8k-token
        # records and images.
        return 16.0

    # -- model ------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        from ...adapters.intern_adapter import InternAdapter
        stage("Loading Intern-Decision (Qwen3.5 language model and vision tower)")
        a = InternAdapter(spec, {"device": dev.kind, "max_length": MAX_LENGTH}, lambda *x: None)
        a.load()                                   # the serving adapter's own loading path (bf16 weights)
        eng = a.engine
        self.adapter = a
        self.backend = eng.backend
        self.inference = sys.modules[type(eng).__module__]    # the checkpoint's own inference.py
        self.tokenizer = eng.backend.tokenizer
        self.root = eng.backend.model              # Qwen3_5ForConditionalGeneration
        self.device = eng.backend.device.type
        import torch
        # Training only (evaluation runs in native bf16, as served): bf16 autocast also runs the LoRA path in bf16, 8%
        # faster per step on the GB10. The LoRA weights themselves stay fp32.
        self.autocast = getattr(torch, dev.autocast) if dev.autocast else None
        self.pad_id = self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else 0
        self.sym = {}
        for s in self.inference.ANSWER_SYMBOLS:
            ids = self.tokenizer.encode(s, add_special_tokens=False)
            if len(ids) != 1:
                raise RuntimeError("Intern-Decision's answer symbols must each be one token")
            self.sym[s] = ids[0]
        self._cost: dict[int, int] = {}
        self._enc: dict[int, tuple] = {}
        freeze(self.root)
        stage("Adding the trainable LoRA layers")
        inject_lora(_get(self.root, self.lora_root), recipe.lora_targets, recipe.lora_r, recipe.lora_alpha,
                    recipe.lora_dropout)
        for p in self.root.parameters():
            if p.requires_grad:
                p.data = p.data.float()
        if recipe.grad_checkpointing:
            self.root.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        self.root.config.use_cache = False

    # -- units ------------------------------------------------------------------------------------------------------
    def _encode(self, ex: Example):
        """The serving path: adapter request -> inference.validate_request -> HFBackend.encode (tokens, images).
        Text-only encodings are kept (a few KB each); examples with images are re-read each time (pixel data is large)."""
        hit = self._enc.get(id(ex))
        if hit is not None:
            return hit
        from ...adapters.intern_adapter import intern_request
        media = [{"type": m.type, "path": m.path} for m in ex.request.media]
        request, _ = intern_request(ex.request.state, ex.qs, media)
        row = self.inference.validate_request(request)
        compiled, batch, positions = self.backend.encode(row)
        if list(compiled.fields) != [q.id for q in ex.qs]:
            raise RuntimeError("Intern-Decision's prompt lists the questions in another order")
        out = (row, compiled, dict(batch), positions)
        if not media:
            self._enc[id(ex)] = out
        return out

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        key = id(ex)
        if key not in self._cost:
            try:
                self._cost[key] = int(self._encode(ex)[2]["input_ids"].shape[-1])
            except ValueError:                     # too long for the checkpoint: the engine drops units over max_tokens
                self._cost[key] = 10 ** 9
        return [Unit(ex, list(range(len(ex.qs))), [None] * len(ex.qs), cost=self._cost[key])]

    def _readout(self, ex: Example, row, compiled, logits_rows):
        """Per question: log-probabilities over the question's symbols (the served softmax), in q.keys order."""
        import torch
        from ...adapters.base import NOUL_ALIASES
        out = []
        for j, (q, field) in enumerate(zip(ex.qs, compiled.fields)):
            ids = [self.sym[s] for s in compiled.symbols[field]]
            lp = torch.log_softmax(logits_rows[j, ids].float(), dim=-1)
            values = [v for v, _ in self.inference._options(row["questions"][field])]
            if q.type == "noul":
                values = [NOUL_ALIASES.get(v, v) for v in values]
            order = [values.index(k) for k in q.keys]      # as Adapter.probs_from_map picks them
            out.append(lp[torch.tensor(order, device=lp.device)])
        return out

    def score(self, units: list[Unit]):
        import torch
        enc = [self._encode(u.example) for u in units]
        if not torch.is_grad_enabled():
            # Evaluation: the exact call inference.py makes (one record, logits only at the answer rows).
            out = []
            for u, (row, compiled, batch, positions) in zip(units, enc):
                b = {k: v.to(self.device) for k, v in batch.items()}
                logits = self.root(**b, use_cache=False, logits_to_keep=positions.to(self.device)).logits[0]
                out.append(self._readout(u.example, row, compiled, logits))
            return out
        # Training: right-padded batch (every answer row precedes the padding, so causal attention and the linear-
        # attention recurrence at those rows are unchanged), hidden states gathered at the answer rows, then the LM head
        # on those rows only (never the whole sequence x 248k vocabulary).
        L = max(int(b["input_ids"].shape[-1]) for _, _, b, _ in enc)
        ids = torch.full((len(enc), L), self.pad_id, dtype=torch.long)
        mask = torch.zeros((len(enc), L), dtype=torch.long)
        extra: dict[str, list] = {}
        b_idx, r_idx = [], []
        for i, (_, _, b, pos) in enumerate(enc):
            n = int(b["input_ids"].shape[-1])
            ids[i, :n] = b["input_ids"][0]
            mask[i, :n] = 1
            b_idx += [i] * len(pos)
            r_idx += pos.tolist()
            for k in ("pixel_values", "image_grid_thw"):
                if k in b:
                    extra.setdefault(k, []).append(b[k])
        kw = {"input_ids": ids.to(self.device), "attention_mask": mask.to(self.device), "use_cache": False}
        for k, v in extra.items():
            kw[k] = torch.cat(v).to(self.device)
        if any("mm_token_type_ids" in b for _, _, b, _ in enc):
            mm = torch.zeros((len(enc), L), dtype=torch.long)
            for i, (_, _, b, _) in enumerate(enc):
                if "mm_token_type_ids" in b:
                    mm[i, :b["mm_token_type_ids"].shape[-1]] = b["mm_token_type_ids"][0]
            kw["mm_token_type_ids"] = mm.to(self.device)
        with autocast(self.device, self.autocast):
            hidden = self.root.model(**kw).last_hidden_state
            rows = hidden[torch.tensor(b_idx, device=hidden.device), torch.tensor(r_idx, device=hidden.device)]
            logits = self.root.lm_head(rows)                               # (answer rows, vocabulary)
        out, k = [], 0
        for u, (row, compiled, _, pos) in zip(units, enc):
            out.append(self._readout(u.example, row, compiled, logits[k:k + len(pos)]))
            k += len(pos)
        return out

    @staticmethod
    def attach(adapter, delta) -> None:
        eng = adapter.engine
        attach_delta(eng.backend.model, delta, eng.backend.device)
        eng.temperature = 1.0      # the checkpoint's DEFAULT_TEMPERATURE; the studio applies the fitted one instead


TRAINER = InternTrainer
