"""Laya family (ConvAI Innovations): ModernBERT / mmBERT encoder + option-marker decision head.

Driven through the official `laya` package. All questions go through one
forward pass. Laya also reports an act-or-escalate probability per question
(its own learned view of whether acting automatically is safe), which we pass
through as a model extra.
"""
from __future__ import annotations

from ..contract import typesafe_questions
from .base import NOUL_ALIASES, Adapter, DecideInput, DecideOutput


class LayaAdapter(Adapter):
    def load(self):
        import laya  # type: ignore
        self.stage("Loading encoder and decision head", 0.3)
        path = self.snapshot(self.spec.repo.id)
        self.agent = laya.load(path, device=self.device)
        self.max_len = int(self.options.get("max_length") or self.spec.context_tokens)

    def effective_device(self):
        # laya.load moves the model to the CPU by itself when the GPU runs out of memory while loading.
        dev = getattr(self.agent, "device", None)
        return getattr(dev, "type", None)

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = typesafe_questions(x.questions)
        for q in questions.values():
            q.setdefault("instructions", "")
        state = x.request.state if isinstance(x.request.state, (str, dict, list)) else x.state_text
        try:
            res = self.agent.predict(state, questions, max_len=self.max_len)
        except TypeError:   # older runtimes without max_len
            res = self.agent.predict(state, questions)
        probs, extras = [], {}
        for q in x.questions:
            a = res["answers"][q.id]
            if q.type == "noul":
                if "probabilities" in a:
                    probs.append(self.probs_from_map(q, a["probabilities"], NOUL_ALIASES))
                else:
                    p = float(a["noul"])
                    probs.append([1 - p, p])
            else:
                probs.append(self.probs_from_map(q, a["probabilities"]))
            ext = a.get("action") or a.get("rl_agent") or a.get("laya") or {}
            if "act_probability" in ext:
                extras[q.id] = {"act_probability": round(float(ext["act_probability"]), 4),
                                "act_probability_help": "Laya's built-in gate: how safe it thinks acting on this answer automatically is."}
        tokens = (res.get("usage") or {}).get("input_tokens")
        return DecideOutput(probs, input_tokens=tokens, extras=extras)


ADAPTER = LayaAdapter
