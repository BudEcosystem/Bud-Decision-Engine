"""Julia 1 (Supersonic Labs): mmBERT-small encoder + decision head.

The repository ships its own `julia` package; we import it from the downloaded
snapshot rather than pip-installing it (its metadata pins an older transformers
than the rest of the studio needs, but the code runs fine on the newer one).
Each question becomes one row (state, question, 2–20 option texts); all rows go
through the encoder as one batch.
"""
from __future__ import annotations

import math
import sys

from .base import Adapter, DecideInput, DecideOutput


def julia_state(request, state_text: str):
    """Julia was trained on structured states written as JSON text; the rendered `key: value` form cost it 17 points
    on typed-decisions (73.2% -> 56.0%, training_research/models/julia-1). Text states pass through unchanged."""
    st = request.state
    if isinstance(st, (dict, list)):
        return st                                   # julia.data.sequence writes objects and lists as JSON text
    return state_text


def julia_option_texts(q) -> list[str]:
    if q.type == "noul":
        return [q.descriptions[0] or "false", q.descriptions[1] or "true"]
    if q.type == "choice":
        return [d or l for l, d in zip(q.labels, q.descriptions)]
    return list(q.labels)


def julia_row(q, state, options: list[str] | None = None) -> dict:
    """One question -> one Julia row. Shared by serving (decide) and training (basal/training/families/julia.py)."""
    return {"state": state, "question": q.instructions or "Which option fits best?", "type": q.type,
            "options": options if options is not None else julia_option_texts(q)}


class JuliaAdapter(Adapter):
    def load(self):
        path = self.snapshot(self.spec.repo.id)
        if path not in sys.path:
            sys.path.insert(0, path)
        self.stage("Loading the encoder and decision head", 0.3)
        # Julia's FastEngine patches a private transformers method that newer versions removed; its plain
        # TransformerEngine runs the same weights through the standard forward pass (identical on the GPU).
        from julia.inference import TransformerEngine  # type: ignore
        self.engine = TransformerEngine(path, device=self.device,
                                        max_length=int(self.options.get("max_length") or 8192), head_length=512)

    def decide(self, x: DecideInput) -> DecideOutput:
        state = julia_state(x.request, x.state_text)
        rows = [julia_row(q, state) for q in x.questions]
        logits = self.engine.logits(rows)
        probs = []
        for z in logits:
            m = max(z)
            e = [math.exp(v - m) for v in z]
            s = sum(e)
            probs.append([v / s for v in e])
        return DecideOutput(probs)


ADAPTER = JuliaAdapter
