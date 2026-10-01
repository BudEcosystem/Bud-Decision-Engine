"""Laya family trainer: shared encoding, units, and (with the released weights cached) the contract on the CPU:
the engine's log-probabilities equal what the studio serves, before and after a delta is attached."""
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
                     "level": {"type": "score", "instructions": "How bad?", "criteria": ["fine", "bad", "awful"]},
                     "tags": {"type": "multi", "instructions": "Which apply?", "criteria": {"refund": None, "angry": None}}},
       "answers": {"team": "billing", "urgent": True, "level": 1, "tags": ["refund"]}}


def _example(rec=REC):
    imp = import_examples(json.dumps(rec) + "\n", "x.jsonl")
    return imp.examples[0]


def test_adapter_question_dicts_and_state_are_shared():
    from basal.adapters.laya_adapter import laya_questions, laya_state
    ex = _example()
    qs = laya_questions(ex.qs)
    assert set(qs) == {q.id for q in ex.qs}
    assert qs["team"]["criteria"]["billing"] == "charges and refunds"
    assert all("instructions" in q for q in qs.values())
    assert laya_state(ex.request, "rendered") == REC["state"]            # objects pass through as JSON for Laya
    req = SystemOneRequest.model_validate({"state": 42, "questions": REC["questions"]})
    assert laya_state(req, "42") == "42"


def test_units_one_per_question_and_only_choice_is_shuffled():
    from basal.training.families.laya import LayaTrainer
    fam = LayaTrainer()
    fam.max_len, fam.head_max_len = 512, 192
    ex = _example()
    us = fam.units(ex, train=True, rng=random.Random(0))
    assert [u.qi for u in us] == [[i] for i in range(len(ex.qs))]
    for u in us:
        q = ex.qs[u.qi[0]]
        assert (u.perm[0] is not None) == (q.type == "choice")
        assert 0 < u.cost <= 512
    assert all(u.perm == [None] for u in fam.units(ex, train=False))


def test_supports_refuses_more_than_twenty_options():
    from basal.catalog import BY_ID
    from basal.training.families.laya import LayaTrainer
    rec = {"state": "x", "questions": {"q": {"type": "choice", "instructions": "Which?",
                                             "criteria": {f"o{i}": None for i in range(21)}}}, "answers": {"q": "o1"}}
    assert "20 options" in LayaTrainer().supports(BY_ID["laya"], _example(rec))
    assert LayaTrainer().supports(BY_ID["laya"], _example()) is None


def _cached(repo):
    try:
        from basal.adapters.base import Adapter
        Adapter.snapshot(repo)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _cached("convaiinnovations/laya-multilingual"), reason="Laya Multilingual weights not cached")
def test_contract_on_cpu_released_permuted_and_attached():
    import torch
    from basal import adapters
    from basal.adapters.base import DecideInput
    from basal.catalog import BY_ID
    from basal.training.families.laya import LayaTrainer
    spec = BY_ID["laya-multilingual"]
    dev = SimpleNamespace(kind="cpu", autocast=None, base_dtype="float32")
    fam = LayaTrainer()
    recipe = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, recipe, lambda *a: None)
    fam.agent.temperature_by_options = {}
    fam.agent.temperature = [1.0, 1.0, 1.0]
    ex = _example()

    def engine(train=False, rng=None):
        us = fam.units(ex, train, rng)
        fam.train_mode(False)
        with torch.no_grad():
            out = fam.score(us)
        return us, {u.qi[0]: [math.exp(x) for x in row[0].tolist()] for u, row in zip(us, out)}

    def served(adapter, req=ex.request, qs=ex.qs):
        return adapter.decide(DecideInput(req, qs, render(req.state))).probs

    _, e = engine()
    for i, p in enumerate(served(fam.adapter)):
        assert max(abs(a - b) for a, b in zip(e[i], p)) < 2e-4

    # a shuffled choice scores like the served request with its options written in that order, back in key order
    us, e = engine(train=True, rng=random.Random(1))
    u = next(u for u in us if u.perm[0] is not None and u.perm[0] != sorted(u.perm[0]))
    q = ex.qs[u.qi[0]]
    crit = REC["questions"][q.id]["criteria"]
    keys = list(crit)
    req = SystemOneRequest.model_validate({"state": REC["state"], "questions": {
        q.id: {**REC["questions"][q.id], "criteria": {keys[k]: crit[keys[k]] for k in u.perm[0]}}}})
    qs2 = normalise(req)
    p2 = dict(zip(qs2[0].keys, served(fam.adapter, req, qs2)[0]))
    assert max(abs(e[u.qi[0]][j] - p2[k]) for j, k in enumerate(q.keys)) < 2e-4

    # a delta (random, standing in for training) exports, attaches unmerged to a fresh adapter, and matches
    torch.manual_seed(0)
    with torch.no_grad():
        for n, p in fam.root.named_parameters():
            if p.requires_grad:
                p.add_(torch.randn_like(p) * (0.02 if "lora_B" in n else 0.0 if "lora_" in n else 0.01))
    d = Path(tempfile.mkdtemp())
    fam.export(d, recipe)
    _, e = engine()
    fam.unload()
    a2 = adapters.get("laya")(spec, {o["key"]: o["default"] for o in spec.options()} | {"device": "cpu"}, lambda *a: None)
    a2.load()
    before = served(a2)
    LayaTrainer.attach(a2, d)
    assert a2.agent.temperature == [1.0, 1.0, 1.0] and a2.agent.temperature_by_options == {}
    after = served(a2)
    assert max(max(abs(a - b) for a, b in zip(e[i], p)) for i, p in enumerate(after)) < 2e-4
    assert max(max(abs(a - b) for a, b in zip(x, y)) for x, y in zip(before, after)) > 0.01
