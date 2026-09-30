"""Intern-Decision-4B (InternLM): Qwen3.5-4B fine-tuned for structured decisions.

Uses the `inference.py` shipped in the model repository. Questions are written
into a JSON answer skeleton and every answer is read from one forward pass.
Accepts up to 8 images per request.
"""
from __future__ import annotations

import sys

from ..contract import typesafe_questions
from .base import NOUL_ALIASES, Adapter, DecideInput, DecideOutput


class InternAdapter(Adapter):
    def load(self):
        path = self.snapshot(self.spec.repo.id)
        if path not in sys.path:
            sys.path.insert(0, path)
        self.stage("Loading Qwen3.5-4B decision weights and image processor", 0.3)
        from inference import DecisionEngine  # type: ignore
        self.engine = DecisionEngine(checkpoint=path, device=self.device,
                                     max_length=int(self.options.get("max_length") or 8192), media_root="/")

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = typesafe_questions(x.questions)
        for q in questions.values():
            q.setdefault("instructions", "")
            crit = q.get("criteria")
            # Intern's prompt builder writes str(description); a missing one would reach the model as the word "None".
            if q["type"] == "choice" and isinstance(crit, dict):
                q["criteria"] = {k: (v if v not in (None, "") else k) for k, v in crit.items()}
            elif q["type"] == "noul" and isinstance(crit, dict):
                kept = {k: v for k, v in crit.items() if v not in (None, "")}
                if kept: q["criteria"] = kept
                else: q.pop("criteria", None)   # falls back to Intern's own yes/no wording
        request = {"state": x.request.state, "questions": questions}
        notes = []
        images = [m["path"] for m in x.media if m["type"] == "image"]
        if images:
            request["images"] = images[:8]
            if len(images) > 8:
                notes.append(f"Only the first 8 of {len(images)} images were used.")
        res = self.engine.predict(request)
        probs = []
        for q in x.questions:
            a = res["answers"][q.id]
            probs.append(self.probs_from_map(q, a["probabilities"], NOUL_ALIASES if q.type == "noul" else None))
        tokens = (res.get("usage") or {}).get("input_tokens")
        return DecideOutput(probs, input_tokens=tokens, notes=notes)


ADAPTER = InternAdapter
