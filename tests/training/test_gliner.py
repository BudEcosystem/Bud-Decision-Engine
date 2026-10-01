"""GLiNER family trainer: the adapter's prompt rules shared with training, units, and (with the released weights
cached) the contract on the CPU: the engine's log-probabilities equal what the studio serves, before and after a delta
is attached, including for task names that are prefixes of each other."""
import json
import math
import random
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from basal.contract import SystemOneRequest, normalise, render
from basal.training.dataformat import import_examples

REC = {"state": {"ticket": "The invoice was charged twice", "channel": "email"},
       "questions": {"team": {"type": "choice", "instructions": "Which team?",
                              "criteria": {"billing": "charges and refunds", "shipping": "deliveries", "other": None}},
                     "urgent": {"type": "noul", "instructions": "Is it urgent?"},
                     "urgent2": {"type": "noul", "instructions": "Is it urgent?", "criteria": {"true": "needs action today"}},
                     "urgent3": {"type": "noul", "instructions": "Is it urgent? Answer for the customer"},
                     "level": {"type": "score", "instructions": "How bad?", "criteria": ["fine", "bad", "bad"]}},
       "answers": {"team": "billing", "urgent": True, "urgent2": True, "urgent3": False, "level": 1}}


def _example(rec=REC):
    return import_examples(json.dumps(rec) + "\n", "x.jsonl").examples[0]


def _reference_tasks(questions):
    """The adapter's task construction before it became a shared function (verbatim), for the no-change check."""
    tasks, order = {}, []
    for q in questions:
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
            keys = [f"{i}: {l}" for i, l in enumerate(q.labels)]
            labels = keys
        tasks[name] = {"labels": labels, "multi_label": True, "cls_threshold": 0.0, "class_act": "softmax"}
        order.append((q, name, keys))
    return tasks, order


def test_shared_tasks_are_the_adapters_prompt_rules():
    from basal.adapters.gliner_adapter import gliner_tasks
    ex = _example()
    assert gliner_tasks(ex.qs) == _reference_tasks(ex.qs)
    tasks, order = gliner_tasks(ex.qs)
    assert list(tasks) == ["Which team?", "Is it urgent?", "Is it urgent? ", "Is it urgent? Answer for the customer",
                           "How bad?"]
    assert tasks["Which team?"]["labels"] == {"billing": "charges and refunds", "shipping": "deliveries", "other": "other"}
    assert tasks["Is it urgent?"]["labels"] == {"no": "no, it is not true", "yes": "yes, it is true"}
    assert tasks["How bad?"]["labels"] == ["0: fine", "1: bad", "2: bad"]


def test_shuffled_choice_shows_names_and_descriptions_in_that_order():
    from basal.adapters.gliner_adapter import gliner_tasks
    ex = _example()
    perms = [[2, 0, 1]] + [None] * (len(ex.qs) - 1)
    tasks, order = gliner_tasks(ex.qs, perms)
    assert order[0][2] == ["other", "billing", "shipping"]
    assert list(tasks["Which team?"]["labels"].items()) == [("other", "other"), ("billing", "charges and refunds"),
                                                           ("shipping", "deliveries")]


def test_one_unit_per_example_and_only_choice_is_shuffled():
    from basal.training.families.gliner import GlinerTrainer
    ex = _example()
    (u,) = GlinerTrainer().units(ex, train=True, rng=random.Random(0))
    assert u.qi == list(range(len(ex.qs)))
    assert [p is not None for p in u.perm] == [q.type == "choice" for q in ex.qs]
    (u,) = GlinerTrainer().units(ex, train=False)
    assert u.perm == [None] * len(ex.qs) and u.cost > 0


def _cached(repo):
    try:
        from basal.adapters.base import Adapter
        Adapter.snapshot(repo)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _cached("fastino/GLiNER2.5-Decide"), reason="GLiNER2.5 Decide weights not cached")
def test_contract_on_cpu_released_permuted_and_attached():
    import torch
    from basal import adapters
    from basal.adapters.base import DecideInput
    from basal.catalog import BY_ID
    from basal.training.families.gliner import GlinerTrainer
    spec = BY_ID["gliner2.5-decide"]
    dev = SimpleNamespace(kind="cpu", autocast=None, base_dtype="float32")
    fam = GlinerTrainer()
    recipe = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, recipe, lambda *a: None)
    ex = _example()

    def engine(train=False, rng=None):
        (u,) = fam.units(ex, train, rng)
        fam.train_mode(False)
        with torch.no_grad():
            (row,) = fam.score([u])
        return u, [[math.exp(x) for x in lp.tolist()] for lp in row]

    def served(adapter, req=ex.request, qs=ex.qs):
        return adapter.decide(DecideInput(req, qs, render(req.state))).probs

    _, e = engine()
    for a, b in zip(e, served(fam.adapter)):          # includes the duplicate and prefix task names
        assert max(abs(x - y) for x, y in zip(a, b)) < 1e-4

    u, e = engine(train=True, rng=random.Random(4))
    perm = u.perm[0]
    crit = REC["questions"]["team"]["criteria"]
    keys = list(crit)
    qdefs = dict(REC["questions"])
    qdefs["team"] = {**qdefs["team"], "criteria": {keys[k]: crit[keys[k]] for k in perm}}
    req = SystemOneRequest.model_validate({"state": REC["state"], "questions": qdefs})
    qs2 = normalise(req)
    p2 = dict(zip(qs2[0].keys, served(fam.adapter, req, qs2)[0]))
    assert max(abs(e[0][j] - p2[k]) for j, k in enumerate(ex.qs[0].keys)) < 1e-4

    torch.manual_seed(0)
    with torch.no_grad():
        for n, p in fam.root.named_parameters():
            if p.requires_grad:
                p.add_(torch.randn_like(p) * (0.02 if "lora_B" in n else 0.0 if "lora_" in n else 0.01))
    d = Path(tempfile.mkdtemp())
    fam.export(d, recipe)
    _, e = engine()
    fam.unload()
    a2 = adapters.get("gliner")(spec, {"device": "cpu"}, lambda *a: None)
    a2.load()
    before = served(a2)
    GlinerTrainer.attach(a2, d)
    after = served(a2)
    assert max(max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(e, after)) < 1e-4
    assert max(max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(before, after)) > 0.01
