"""GLiNER2.5 Decide (Fastino): a DeBERTa-v3-large GLiNER2 encoder; every question of a request is a classification task
in one prompt, and a small classifier scores each label's `[L]` marker. One forward pass answers all questions.

Best practice for this family (training_research/models/gliner2.5-decide/ANALYSIS.md):
- the publisher's trainer (`ExtractorTrainer`) is not used. Its per-label binary cross-entropy disagrees with the
  softmax the studio serves, its target builder takes the *first* task name a prompt starts with (a silent zero loss
  when one question's wording is a prefix of another's, section 4) and it skips out-of-memory batches silently.
  Instead the classifier logits come from the model's own modules on batches built by gliner2's *inference*
  processor, from the adapter's own task construction (`gliner_tasks`): instructions as task names, "no"/"yes" with
  descriptions for yes/no, "i: level" for scales, no label renaming, dropping or shuffling noise. Logits map to
  questions by position (the inference processor keeps task order), and the engine's soft cross-entropy runs on the
  softmax the studio serves;
- LoRA r16 on the encoder's attention and feed-forward projections (DeBERTa `query_proj`, `key_proj`, `value_proj`
  and the `dense` layers, gliner2's own "encoder" LoRA preset) and the classifier trained in full. The research
  measured LoRA saving no memory at batch 8 (14.2 vs 12.8 GB, section 7), but the delta is ~30 MB instead of a
  1.95 GB model and the frozen encoder limits forgetting; learning rates follow the publisher's range (LoRA task
  rate 1e-4 to 1e-3, full encoder 1e-5, section 4);
- DeBERTa-v3 trains in fp32 (docs/trainer/ARCHITECTURE.md section 8), which is also how the studio serves it;
- choice labels are shuffled during training, as gliner2's own training does; yes/no and scale labels never are;
- GLiNER span models have no internal temperature, so `attach` only adds the delta.
"""
from __future__ import annotations

from ..dataformat import Example
from .base import FamilyTrainer, Recipe, Unit, approx_tokens, freeze, inject_lora, shuffled, unfreeze, unpermute


class GlinerTrainer(FamilyTrainer):
    family = "gliner"
    head_names = ("classifier",)
    lora_root = "encoder"

    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        return Recipe(lora_targets=["query_proj", "key_proj", "value_proj", "dense"], lora_r=16, lora_alpha=32,
                      lora_dropout=0.05, lr=1e-4, head_lr=5e-5, max_epochs=4, effective_batch=16,
                      token_budget=4096, max_tokens=2048, replay_ratio=0.5, kl_weight=1.0, shuffle_options=True)

    def supports(self, spec, ex: Example) -> str | None:
        for q in ex.qs:
            if not 2 <= len(q.keys) <= spec.max_options:
                return f"{spec.name} answers questions with 2 to {spec.max_options} options"
        return super().supports(spec, ex)

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        return 12.0         # measured on the GB10 (see the report); fp32 weights 2 GB + optimizer + activations

    def load(self, spec, dev, recipe: Recipe, stage) -> None:
        import torch
        from ...adapters.gliner_adapter import GlinerAdapter
        stage("Loading the GLiNER2 encoder")
        a = GlinerAdapter(spec, {"device": dev.kind}, lambda *x: None)
        a.load()
        m = a.model
        if next(m.parameters()).device.type != dev.kind:
            raise RuntimeError(f"{spec.name} could not be placed on the GPU. Eject loaded models or close other "
                               "programs, then try again.")
        self.adapter, self.root = a, m
        self.device = dev.kind
        self.autocast = None              # DeBERTa-v3: fp32 until bf16 is validated (ARCHITECTURE.md section 8)
        freeze(m)
        if recipe.method == "full":
            unfreeze(m.encoder)
        else:
            inject_lora(m.encoder, recipe.lora_targets, recipe.lora_r, recipe.lora_alpha, recipe.lora_dropout)
        if recipe.train_head:
            for h in self.head_names:
                unfreeze(getattr(m, h))
        else:
            self.head_names = ()          # the released classifier stays as it is and is not exported
        for _, p in m.named_parameters():
            if p.requires_grad:
                p.data = p.data.float()
        if recipe.grad_checkpointing:
            m.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        del torch

    def train_mode(self, on: bool) -> None:
        # Only the encoder and the classifier take part in classification; the span and count heads stay in eval.
        self.root.eval()
        self.root.encoder.train(on)
        self.root.classifier.train(on)

    # -- encoding ---------------------------------------------------------------------------------------------------
    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        """One unit per example: the studio sends all of a request's questions as tasks of one prompt."""
        from ...contract import render
        perms = [shuffled(len(q.keys), rng) if (train and rng is not None and q.type == "choice") else None
                 for q in ex.qs]
        prompt = 0
        for q in ex.qs:
            opts = q.labels + [d for d in q.descriptions if d]
            prompt += approx_tokens(q.instructions) + sum(approx_tokens(o) + 2 for o in opts) + 6
        return [Unit(ex, list(range(len(ex.qs))), perms, cost=approx_tokens(render(ex.request.state)) + prompt + 2)]

    def score(self, units: list[Unit]):
        import torch
        from ...adapters.gliner_adapter import gliner_tasks
        from ...contract import render
        m = self.root
        records, orders = [], []
        for u in units:
            ex = u.example
            tasks, order = gliner_tasks([ex.qs[i] for i in u.qi], u.perm or None)
            schema_dicts, _ = m._build_schema_dicts_and_metadata([m._classification_schema(tasks)])
            records.append((render(ex.request.state), schema_dicts[0]))
            orders.append(order)
        # gliner2's inference collator (no training-time label noise), the path classify_text takes
        batch = m.processor.collate_fn_inference(records, error_policy="raise").to(torch.device(self.device))
        h = m.encoder(input_ids=batch.input_ids, attention_mask=batch.attention_mask).last_hidden_state
        _, schema_embs = m.processor.extract_embeddings_from_batch(h, batch.input_ids, batch)
        out = []
        for b, (u, order) in enumerate(zip(units, orders)):
            if len(schema_embs[b]) != len(order):
                raise RuntimeError(f"GLiNER built {len(schema_embs[b])} tasks for {len(order)} questions")
            row = []
            for j, (q, name, keys) in enumerate(order):
                cls = torch.stack(schema_embs[b][j])[1:]          # [P] first, then one [L] per label
                if cls.shape[0] != len(keys):
                    raise RuntimeError(f"question '{q.display_id}': {cls.shape[0]} label markers for {len(keys)} options")
                lp = torch.log_softmax(m.classifier(cls).squeeze(-1).float(), -1)
                row.append(unpermute(lp, u.perm[j] if u.perm else None))
            out.append(row)
        return out

    # -- serving ----------------------------------------------------------------------------------------------------
    @staticmethod
    def attach(adapter, delta) -> None:
        from .base import attach_delta
        m = adapter.model
        attach_delta(m, delta, next(m.parameters()).device)


TRAINER = GlinerTrainer
