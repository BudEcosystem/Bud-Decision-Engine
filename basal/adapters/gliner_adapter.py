"""GLiNER2.5-Decide (Fastino): a GLiNER2 encoder with a classification head.

GLiNER2 scores every label internally but normally returns only the winner. We
ask for multi-label output with softmax activation and a zero threshold, which
returns the full distribution over labels. Each question becomes one
classification task; all tasks are scored in one pass. The question's
instructions are used as the task prompt, and option descriptions are passed
as label descriptions.
"""
from __future__ import annotations

from .base import Adapter, DecideInput, DecideOutput


class GlinerAdapter(Adapter):
    def load(self):
        from gliner2 import AutoExtractor  # type: ignore
        self.stage("Loading the GLiNER2 encoder", 0.3)
        path = self.snapshot(self.spec.repo.id)
        self.model = AutoExtractor.from_pretrained(path)
        try:
            self.model = self.model.to(self.device)
        except Exception:
            pass
        if hasattr(self.model, "eval"):
            self.model.eval()

    def decide(self, x: DecideInput) -> DecideOutput:
        tasks, order = {}, []
        for q in x.questions:
            name = (q.instructions or q.id).strip()
            while name in tasks:
                name += " "
            if q.type == "noul":
                labels = {"no": q.descriptions[0] or "no, it is not true", "yes": q.descriptions[1] or "yes, it is true"}
                keys = ["no", "yes"]
            elif q.type == "choice":
                keys = list(q.labels)
                labels = {k: d for k, d in zip(q.labels, q.descriptions) if d}
                labels = {k: labels.get(k, k) for k in keys} if labels else keys
            else:
                keys = [f"{i}: {l}" for i, l in enumerate(q.labels)]   # unique even if two levels share text
                labels = keys
            tasks[name] = {"labels": labels, "multi_label": True, "cls_threshold": 0.0, "class_act": "softmax"}
            order.append((q, name, keys))
        import torch
        with torch.inference_mode():
            res = self.model.classify_text(x.state_text, tasks, include_confidence=True)
        probs = []
        for q, name, keys in order:
            got = res.get(name, [])
            m = {}
            for item in got if isinstance(got, list) else [got]:
                if isinstance(item, dict):
                    m[str(item.get("label"))] = float(item.get("confidence", 0.0))
                elif isinstance(item, (tuple, list)) and len(item) == 2:
                    m[str(item[0])] = float(item[1])
            probs.append([m.get(k, 0.0) for k in keys])
        return DecideOutput(probs)


ADAPTER = GlinerAdapter
