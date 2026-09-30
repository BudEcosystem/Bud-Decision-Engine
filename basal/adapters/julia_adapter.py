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
        rows = []
        for q in x.questions:
            if q.type == "noul":
                opts = [q.descriptions[0] or "false", q.descriptions[1] or "true"]
            elif q.type == "choice":
                opts = [d or l for l, d in zip(q.labels, q.descriptions)]
            else:
                opts = list(q.labels)
            rows.append({"state": x.state_text, "question": q.instructions or "Which option fits best?",
                         "type": q.type, "options": opts})
        logits = self.engine.logits(rows)
        probs = []
        for z in logits:
            m = max(z)
            e = [math.exp(v - m) for v in z]
            s = sum(e)
            probs.append([v / s for v in e])
        return DecideOutput(probs)


ADAPTER = JuliaAdapter
