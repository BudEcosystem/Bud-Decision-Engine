"""A deterministic test model: instant load, every modality, probabilities from a hash of the state, the question and
each option. Registered only when BASAL_FAKE_MODEL=1 (tests and CI), so the studio's API can be tested end to end
without downloading or running a real model."""
from __future__ import annotations

import hashlib
import os
import time

from .base import Adapter, DecideInput, DecideOutput


def _weight(*parts: str) -> float:
    h = hashlib.sha256("\x1f".join(parts).encode()).digest()
    return 0.05 + int.from_bytes(h[:4], "big") / 2**32


class FakeAdapter(Adapter):
    def load(self) -> None:
        # tests of what happens while a model loads (BASAL_FAKE_LOAD_SECONDS) need a load that takes a moment
        time.sleep(float(os.environ.get("BASAL_FAKE_LOAD_SECONDS") or 0))
        self.stage("Ready", 1.0)

    def warmup(self) -> None:
        pass

    def decide(self, x: DecideInput) -> DecideOutput:
        media = "|".join(m.get("type", "") for m in x.media)
        probs = []
        for q in x.questions:
            w = [_weight(x.state_text, media, q.instructions, k, lbl) ** 3 for k, lbl in zip(q.keys, q.labels)]
            probs.append(w)
        return DecideOutput(probs, input_tokens=max(1, len(x.state_text) // 4))


ADAPTER = FakeAdapter
