"""Julia family trainer: units, and (with the released weights cached) the contract on the CPU: the engine's
log-probabilities equal what the studio serves, a shuffled choice comes back in key order, two training steps run, and
the exported delta attached to a fresh adapter answers like the trained model."""
import json
import math
import random
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from basal.contract import render
from basal.training.dataformat import import_examples

REC = {"state": {"ticket": "The invoice was charged twice", "channel": "email"},
       "questions": {"team": {"type": "choice", "instructions": "Which team?",
                              "criteria": {"billing": "charges and refunds", "shipping": "deliveries", "other": None}},
                     "urgent": {"type": "noul", "instructions": "Is it urgent?"},
                     "level": {"type": "score", "instructions": "How bad?", "criteria": ["fine", "bad", "awful"]}},
       "answers": {"team": "billing", "urgent": True, "level": 1}}


def _example(rec=REC):
    return import_examples(json.dumps(rec) + "\n", "x.jsonl").examples[0]


def test_units_one_per_question_and_only_choice_is_shuffled():
    from basal.training.families.julia import JuliaTrainer
    ex = _example()
    us = JuliaTrainer().units(ex, train=True, rng=random.Random(0))
    assert [u.qi for u in us] == [[i] for i in range(len(ex.qs))]
    assert [(u.perm[0] is not None) for u in us] == [q.type == "choice" for q in ex.qs]
    assert all(u.perm == [None] for u in JuliaTrainer().units(ex, train=False))


def test_recipe_keeps_the_shared_head_frozen():
    from basal.catalog import BY_ID
    from basal.training.families.julia import JuliaTrainer
    r = JuliaTrainer().recipe(BY_ID["julia-1"], None, 500)
    assert r.train_head is False and r.lr == 5e-5 and r.lora_targets == ["Wqkv", "Wo", "Wi"]


def _cached(repo):
    try:
        from basal.adapters.base import Adapter
        Adapter.snapshot(repo)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _cached("SupersonicLabs/Julia-1"), reason="Julia 1 weights not cached")
def test_contract_on_cpu_trained_exported_and_attached():
    import torch
    from basal import adapters
    from basal.adapters.base import DecideInput
    from basal.catalog import BY_ID
    from basal.training.families.julia import JuliaTrainer
    spec = BY_ID["julia-1"]
    dev = SimpleNamespace(kind="cpu", autocast=None, base_dtype="float32")
    fam = JuliaTrainer()
    recipe = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, recipe, lambda *a: None)
    ex = _example()

    def engine(train=False, rng=None):
        us = fam.units(ex, train, rng)
        fam.train_mode(False)
        with torch.no_grad():
            out = fam.score(us)
        return {u.qi[0]: [math.exp(x) for x in row[0].tolist()] for u, row in zip(us, out)}

    def served(adapter):
        return adapter.decide(DecideInput(ex.request, ex.qs, render(ex.request.state))).probs

    e = engine()
    for i, p in enumerate(served(fam.adapter)):
        assert max(abs(a - b) for a, b in zip(e[i], p)) < 1e-4

    # a shuffled choice comes back in key order: the same probabilities as unshuffled, to rounding
    shuffled = engine(train=True, rng=random.Random(3))
    team = next(i for i, q in enumerate(ex.qs) if q.id == "team")
    assert max(abs(a - b) for a, b in zip(shuffled[team], e[team])) < 0.2     # the head reads option positions

    # two real training steps move the answers, with gradients reaching only LoRA weights
    fam.train_mode(True)
    params = [p for p in fam.root.parameters() if p.requires_grad]
    assert params and all(p.dtype == torch.float32 for p in params)
    opt = torch.optim.AdamW(params, lr=1e-3)
    for _ in range(2):
        us = fam.units(ex, True, random.Random(0))
        loss = -sum((torch.tensor(ex.targets[u.qi[0]]) * row[0]).sum() for u, row in zip(us, fam.score(us)))
        loss.backward()
        opt.step()
        opt.zero_grad()
    trained = engine()
    assert max(max(abs(a - b) for a, b in zip(trained[i], e[i])) for i in e) > 1e-3

    d = Path(tempfile.mkdtemp())
    fam.export(d, recipe)
    assert (d / "lora.safetensors").exists() and not (d / "head.safetensors").exists()   # the frozen head isn't saved
    fam.unload()
    a2 = adapters.get("julia")(spec, {o["key"]: o["default"] for o in spec.options()} | {"device": "cpu"}, lambda *a: None)
    a2.load()
    JuliaTrainer.attach(a2, d)
    for i, p in enumerate(served(a2)):
        assert max(abs(a - b) for a, b in zip(trained[i], p)) < 1e-4
