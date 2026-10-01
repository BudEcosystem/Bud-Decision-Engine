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


def gliner_tasks(questions, perms=None) -> tuple[dict, list]:
    """Studio questions -> gliner2 classification tasks. Shared by serving (decide) and training
    (basal/training/families/gliner.py), so a fine-tune learns exactly the prompts the studio sends:

    - the task name is the question's instructions, made unique with trailing spaces. gliner2's inference resolves a
      prompt to the *longest* task name it starts with, so names that are prefixes of each other are served
      correctly; the trainer maps each task to its question by position and never uses gliner2's training-time
      target builder, whose first-prefix match is the known silent-loss bug (training_research/models/
      gliner2.5-decide/ANALYSIS.md section 4);
    - yes/no: labels "no" / "yes", always with descriptions (the criteria text, or a default);
    - pick one: the option names, with descriptions when any option has one (the name stands in for a missing one);
    - scale: labels "i: level text", unique even when two levels share text.

    `perms[i]`, when given (training only), is the order question i's choice options are shown in: position j shows
    option perm[j]. Returns (tasks, order); order[i] = (question, task name, label names in the order shown)."""
    tasks, order = {}, []
    for i, q in enumerate(questions):
        name = (q.instructions or q.id).strip()
        while name in tasks:
            name += " "
        perm = perms[i] if perms else None
        if q.type == "noul":
            labels = {"no": q.descriptions[0] or "no, it is not true", "yes": q.descriptions[1] or "yes, it is true"}
            keys = ["no", "yes"]
        elif q.type == "choice":
            names, descs = list(q.labels), list(q.descriptions)
            if perm is not None:
                names, descs = [names[k] for k in perm], [descs[k] for k in perm]
            keys = names
            labels = {k: d for k, d in zip(names, descs) if d}
            labels = {k: labels.get(k, k) for k in keys} if labels else keys
        else:
            keys = [f"{j}: {l}" for j, l in enumerate(q.labels)]   # unique even if two levels share text
            labels = keys
        tasks[name] = {"labels": labels, "multi_label": True, "cls_threshold": 0.0, "class_act": "softmax"}
        order.append((q, name, keys))
    return tasks, order


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
        tasks, order = gliner_tasks(x.questions)
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
