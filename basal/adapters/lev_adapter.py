"""Lev (Interfaze): LoRA adapter on Qwen3.5-4B with label-token readout.

Driven through the official `lev` package. Every option gets a one-token code;
answers are read from next-token logits, averaged over two option orders, with
a learned candidate-path head for very large option sets. Lev's yes/no answers
come from an internal 0-8 rating scale, so we use its `noul` value directly.
"""
from __future__ import annotations

from ..contract import typesafe_questions
from .base import Adapter, DecideInput, DecideOutput


class LevAdapter(Adapter):
    def load(self):
        import lev  # type: ignore
        self.stage("Loading Qwen3.5-4B and applying the Lev adapter", 0.3)
        path = self.snapshot(self.spec.repo.id)
        self.engine = lev.load(path)

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = typesafe_questions(x.questions)
        res = self.engine.system_one(x.request.state, questions)
        probs = []
        for q in x.questions:
            a = res.answers[q.id]
            if q.type == "noul":
                p = float(a.noul)
                probs.append([1 - p, p])
            else:
                m = {str(k): float(v) for k, v in a.probabilities.items()}
                probs.append(self.probs_from_map(q, m))
        return DecideOutput(probs, input_tokens=getattr(res.usage, "input_tokens", None))


ADAPTER = LevAdapter
