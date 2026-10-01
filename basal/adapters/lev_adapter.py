"""Lev (Interfaze): LoRA adapter on Qwen3.5-4B with label-token readout.

Driven through the official `lev` package. Every option gets a one-token code;
answers are read from next-token logits, averaged over two option orders, with
a learned candidate-path head for very large option sets. Lev's yes/no answers
come from an internal 0-8 rating scale, so we use its `noul` value directly.
"""
from __future__ import annotations

from ..contract import typesafe_questions
from .base import Adapter, DecideInput, DecideOutput


def lev_questions(questions) -> dict:
    """The studio's questions -> the question dicts `lev`'s `system_one` reads. Shared by serving (`decide`) and
    training (basal/training/families/lev.py), which renders them with the same `lev` prompt functions."""
    return typesafe_questions(questions)


class LevAdapter(Adapter):
    def load(self):
        import lev  # type: ignore
        import torch
        self.stage("Loading Qwen3.5-4B and applying the Lev adapter", 0.3)
        path = self.snapshot(self.spec.repo.id)
        # lev.load always moves the model to CUDA when there is one. Loading under that device context puts the weights
        # there directly instead of reading them into host memory first (on unified-memory machines both copies would
        # exist at once, about 8 GB extra).
        with torch.device("cuda" if torch.cuda.is_available() else "cpu"):
            self.engine = lev.load(path)

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = lev_questions(x.questions)
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
