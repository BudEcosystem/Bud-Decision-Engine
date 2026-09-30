"""Kev family (Jared Palmer): LoRA adapter + pointer head on a Qwen base.

Uses Kev's own loader and serving loop (installed from its GitHub source), so we
get its exact prompt packing, branch isolation, calibration temperature and
state-prefix cache. The state is read once; each question is its own branch.
"""
from __future__ import annotations

from dataclasses import replace

from ..contract import typesafe_questions
from .base import Adapter, DecideInput, DecideOutput


class KevAdapter(Adapter):
    def load(self):
        import torch
        from kev.checkpoint import Checkpoint, LoadOptions, fused_available  # type: ignore
        from kev.serve import Server  # type: ignore

        self.stage("Reading the adapter and pointer head", 0.15)
        opts = LoadOptions.from_env()
        dtype = torch.float32 if self.options.get("dtype") == "fp32" else torch.bfloat16
        opts = replace(opts, dtype=dtype, backend="torch")
        if self.device == "cuda":
            opts = replace(opts, fused=fused_available() if opts.fused is None else opts.fused,
                           cuda_graphs=self.options.get("cuda_graphs", False))
        ck = Checkpoint(self.spec.repo.id)
        self.stage(f"Loading {self.spec.base.id} and merging the LoRA adapter", 0.35)
        tok, model = ck.load(self.device, opts)
        self.server = Server(ck, tok, model, self.device)
        self.date_facts = bool(self.options.get("date_facts"))
        self.temperature = float(getattr(model.head, "temperature", 1.0))

    def decide(self, x: DecideInput) -> DecideOutput:
        from kev.api import SystemOneRequest as KevRequest, to_record, with_date_facts  # type: ignore
        state = x.request.state
        if self.date_facts:
            state = with_date_facts(state)
        req = KevRequest(state=state, questions=typesafe_questions(x.questions))
        rec, _meta = to_record(req)
        ps, stats = self.server.probs(rec)
        notes = []
        if stats.get("prefix_cache_hit"):
            notes.append("Reused the cached reading of this state, so only the questions were computed.")
        return DecideOutput([list(map(float, p)) for p in ps], input_tokens=stats.get("tokens"), notes=notes)


ADAPTER = KevAdapter
