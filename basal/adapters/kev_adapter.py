"""Kev family (Jared Palmer): LoRA adapter + pointer head on a Qwen base.

Uses Kev's own loader and serving loop (installed from its GitHub source), so we
get its exact prompt packing, branch isolation, calibration temperature and
state-prefix cache. The state is read once; each question is its own branch.

`kev_load_options` and `kev_record` are shared with the trainer (basal/training/families/kev.py), so a fine-tune is
trained on exactly the model and the text the studio serves.
"""
from __future__ import annotations

from dataclasses import replace

from ..contract import typesafe_questions
from .base import Adapter, DecideInput, DecideOutput


def kev_load_options(device: str, options: dict, finetune: bool = False):
    """kev.checkpoint.LoadOptions for the studio's load of a Kev checkpoint (bf16 unless fp32 is asked for; on CUDA
    kev's fused Qwen3.5 kernels when flash-linear-attention is installed).

    finetune: the model will carry a fine-tune's LoRA, attached unmerged on top of the released weights
    (docs/trainer/ARCHITECTURE.md Appendix A). The fused kernels replace the projection layers the LoRA sits on
    (kev/fused_qwen35.py: "only for serving, the fused projections replace the originals"), so a fine-tune keeps the
    reference layers, as it had in training."""
    import torch
    from kev.checkpoint import LoadOptions, fused_available  # type: ignore
    opts = LoadOptions.from_env()
    dtype = torch.float32 if options.get("dtype") == "fp32" else torch.bfloat16
    opts = replace(opts, dtype=dtype, backend="torch")
    if device == "cuda":
        fused = fused_available() if opts.fused is None else opts.fused
        opts = replace(opts, fused=fused and not finetune,
                       cuda_graphs=options.get("cuda_graphs", False) and not finetune)
    return opts


def kev_record(request, questions, date_facts: bool = False, orders: dict | None = None) -> dict:
    """A studio request -> Kev's internal record (kev.api.to_record): the state and question text the model reads.
    orders: {question id: option keys in the order shown} for choice questions (training-time option shuffling)."""
    from kev.api import SystemOneRequest as KevRequest, to_record, with_date_facts  # type: ignore
    state = request.state
    if date_facts:
        state = with_date_facts(state)
    qs = typesafe_questions(questions)
    for qid, order in (orders or {}).items():
        crit = qs[qid]["criteria"]
        qs[qid] = {**qs[qid], "criteria": {k: crit[k] for k in order}}
    rec, _meta = to_record(KevRequest(state=state, questions=qs))
    return rec


class KevAdapter(Adapter):
    def load(self):
        from kev.checkpoint import Checkpoint  # type: ignore
        from kev.serve import Server  # type: ignore

        self.stage("Reading the adapter and pointer head", 0.15)
        opts = kev_load_options(self.device, self.options, finetune=bool(self.spec.finetune_dir))
        ck = Checkpoint(self.spec.repo.id)
        self.stage(f"Loading {self.spec.base.id} and merging the LoRA adapter", 0.35)
        tok, model = ck.load(self.device, opts)
        self.server = Server(ck, tok, model, self.device)
        self.date_facts = bool(self.options.get("date_facts"))
        self.temperature = float(getattr(model.head, "temperature", 1.0))

    def decide(self, x: DecideInput) -> DecideOutput:
        rec = kev_record(x.request, x.questions, self.date_facts)
        ps, stats = self.server.probs(rec)
        notes = []
        if stats.get("prefix_cache_hit"):
            notes.append("Reused the cached reading of this state, so only the questions were computed.")
        return DecideOutput([list(map(float, p)) for p in ps], input_tokens=stats.get("tokens"), notes=notes)


ADAPTER = KevAdapter
