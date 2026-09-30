"""Jev-Omni: Gemma 4 12B (text, image, audio, video) with a 256-way decision head.

Uses the loader shipped in the model repository. Jev-Omni answers one question
per forward pass, so a request with N questions costs N passes (the UI says so).
One media file per request; audio is capped at 30 s and video sampled to 16 frames
by the model's own preprocessing.
"""
from __future__ import annotations

import sys

from .base import Adapter, DecideInput, DecideOutput


class JevOmniAdapter(Adapter):
    def load(self):
        if self.device == "cpu":
            raise RuntimeError("Jev-Omni needs a GPU with about 26 GB of memory; it is too large to run on the CPU.")
        path = self.snapshot(self.spec.repo.id)
        if path not in sys.path:
            sys.path.insert(0, path)
        self.stage("Loading Gemma 4 12B (text, vision and audio towers), ~24 GB", 0.2)
        from jev_omni import load_jev_omni  # type: ignore
        self.clf = load_jev_omni(self.spec.repo.id, device=self.device)

    def decide(self, x: DecideInput) -> DecideOutput:
        media = x.media[0] if x.media else None
        notes = []
        if len(x.media) > 1:
            notes.append(f"Jev-Omni reads one media file per request; used '{media.get('name') or 'the first file'}'.")
        probs = []
        for q in x.questions:
            if q.type == "noul":
                options = [f"No: {q.descriptions[0]}" if q.descriptions[0] else "No",
                           f"Yes: {q.descriptions[1]}" if q.descriptions[1] else "Yes"]
            else:
                options = q.option_texts()
            kw = {}
            if media:
                kw = {"media": media["path"], "modality": media["type"]}
            r = self.clf.predict(state=x.state_text, question=q.instructions or "Which option is correct?",
                                 options=options, **kw)
            probs.append([float(r["probabilities"][o]) for o in options])
        if len(x.questions) > 1:
            notes.append(f"Jev-Omni answers one question per pass: {len(x.questions)} passes for this request.")
        return DecideOutput(probs, passes=len(x.questions), notes=notes)


ADAPTER = JevOmniAdapter
