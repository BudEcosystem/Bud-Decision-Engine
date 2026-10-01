"""Jev-Omni trainer contract, on the CPU with a tiny random Gemma 4 unified model (the real processor, chat template,
loader code and head class from the cached checkpoint): load -> units (choice options shuffled, yes/no and scale not)
-> score equals JevOmni.predict through the serving adapter -> two optimizer steps -> export -> attach to a freshly
loaded adapter -> it answers exactly like the trained model."""
import glob
import json
import os
import sys

import pytest

torch = pytest.importorskip("torch")

SNAP = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--akhilaaa3--Jev-Omni/snapshots/*/")))
pytestmark = pytest.mark.skipif(not SNAP, reason="Jev-Omni is not in the Hugging Face cache")

RECORDS = [
    {"state": {"ticket": "Charged twice for order 1182", "tier": "gold"},
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "Charges and refunds.", "shipping": "Deliveries.", "tech": None}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?",
                              "criteria": {"true": "Yes, today.", "false": "It can wait."}},
                   "mood": {"type": "score", "instructions": "How upset is the customer?",
                            "criteria": ["Calm.", "Annoyed.", "Furious."]}},
     "answers": {"team": "billing", "urgent": True, "mood": 1}},
    {"state": "My parcel never arrived and the tracking page is blank.",
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "Charges and refunds.", "shipping": "Deliveries.", "tech": None}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?"}},
     "answers": {"team": "shipping", "urgent": False}},
]


def _tiny_clf():
    import transformers
    from transformers import AutoConfig, AutoProcessor
    if SNAP[0] not in sys.path:
        sys.path.insert(0, SNAP[0])
    import jev_omni as jev
    cfg = AutoConfig.from_pretrained(SNAP[0])
    tc = cfg.text_config
    tc.hidden_size, tc.intermediate_size, tc.num_hidden_layers = 64, 128, 3
    tc.layer_types = tc.layer_types[:2] + ["full_attention"]
    tc.num_attention_heads, tc.num_key_value_heads, tc.head_dim, tc.global_head_dim = 2, 1, 32, 64
    cfg.vision_config.mm_embed_dim = cfg.vision_config.output_proj_dims = 32
    tc.per_layer_config = {2: {"head_dim": 64, "num_key_value_heads": 1}}
    torch.manual_seed(0)
    model = getattr(transformers, cfg.architectures[0])(cfg).to(torch.float32).eval()
    head = jev._Head256(64)
    with torch.no_grad():
        head.mu.normal_(0, 0.1)
        head.sd.uniform_(0.5, 1.5)
        head.linear.weight.normal_(0, 0.3)
    _, decoder = jev._find_backbone(model)
    return jev.JevOmni(model, head.eval(), AutoProcessor.from_pretrained(SNAP[0]), decoder, device="cpu")


@pytest.fixture
def tiny(monkeypatch):
    from basal.adapters import jev_omni_adapter

    def load(self):
        self.clf = _tiny_clf()

    monkeypatch.setattr(jev_omni_adapter.JevOmniAdapter, "load", load)


def _setup():
    from basal.catalog import BY_ID
    from basal.training import dataformat
    from basal.training.device import DeviceProfile
    from basal.training.families.jev_omni import JevOmniTrainer
    spec = BY_ID["jev-omni"]
    dev = DeviceProfile("cpu", "cpu", "CPU", False, "on", "tests", None, "float32", False, {"foreach": False})
    imp = dataformat.import_examples("\n".join(json.dumps(r) for r in RECORDS), "t.jsonl")
    assert len(imp.examples) == len(RECORDS)          # (too few to train on; enough for the contract)
    fam = JevOmniTrainer()
    recipe = fam.recipe(spec, dev, 100)
    recipe.lora_r, recipe.lora_alpha = 4, 8
    fam.load(spec, dev, recipe, lambda *a: None)
    return spec, dev, imp.examples, fam, recipe


def _served(adapter, ex):
    from basal.adapters.base import DecideInput
    from basal.contract import render
    return adapter.decide(DecideInput(ex.request, ex.qs, render(ex.request.state), [])).probs


def _engine(fam, ex):
    fam.train_mode(False)
    with torch.no_grad():
        rows = fam.score(fam.units(ex, train=False))
    return [r[0].exp().tolist() for r in rows]


def _diff(a, b):
    return max(abs(x - y) for pa, pb in zip(a, b) for x, y in zip(pa, pb))


def test_contract_and_parity(tiny, tmp_path):
    import random

    from basal.adapters.jev_omni_adapter import JevOmniAdapter
    from basal.training.families.jev_omni import JevOmniTrainer
    spec, dev, exs, fam, recipe = _setup()
    assert all(fam.supports(spec, ex) is None for ex in exs)
    n_lora = sum(1 for n, p in fam.root.named_parameters() if p.requires_grad and "lora_" in n)
    assert n_lora == 2 * 7 * 3 - 2                   # 7 projections x 3 layers, no v_proj on the global layer
    trainable = {n for n, p in fam.root.named_parameters() if p.requires_grad and "lora_" not in n}
    assert trainable == {"head.linear.weight", "head.linear.bias"}   # mu / sd stay frozen

    # the engine's scores are what the adapter serves (JevOmni.predict), in key order
    for ex in exs:
        assert _diff(_engine(fam, ex), _served(fam.adapter, ex)) < 1e-5

    # choice options are shuffled in training, yes/no and scale are not; scores come back in key order
    units = [u for ex in exs for u in fam.units(ex, train=True, rng=random.Random(3))]
    for u in units:
        q = u.example.qs[u.qi[0]]
        assert (u.perm[0] is not None) == (q.type == "choice")
    shuffled = next(u for u in units if u.perm[0] is not None and u.perm[0] != sorted(u.perm[0]))
    fam.train_mode(False)
    with torch.no_grad():
        plain = fam.score([u for u in fam.units(shuffled.example, train=False) if u.qi == shuffled.qi])[0][0]
        permuted = fam.score([shuffled])[0][0]
    assert plain.shape == permuted.shape

    # two optimizer steps: LoRA and the head linear
    groups = fam.param_groups(recipe)
    assert sorted(g["name"] for g in groups) == ["head", "lora"]
    fam.train_mode(True)
    opt = torch.optim.AdamW([{**{k: v for k, v in g.items() if k != "name"}, "lr": 1e-2} for g in groups])
    for _ in range(2):
        rows = fam.score(units)
        assert all(r[0].requires_grad for r in rows)
        loss = torch.stack([-(torch.tensor(u.example.targets[u.qi[0]]) * r[0]).sum() for u, r in zip(units, rows)]).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    trained = [_engine(fam, ex) for ex in exs]

    # export (unmerged LoRA + head) -> a freshly loaded adapter + attach answers exactly like the trained model
    delta = tmp_path / "delta"
    fam.export(delta, recipe)
    cfg = json.loads((delta / "lora.json").read_text())
    assert cfg["root"] == "model.model.language_model" and cfg["r"] == 4
    fresh = JevOmniAdapter(spec, {"device": "cpu"}, lambda *a: None)
    fresh.load()
    untouched = [_served(fresh, ex) for ex in exs]
    JevOmniTrainer.attach(fresh, delta)
    for ex, want, old in zip(exs, trained, untouched):
        got = _served(fresh, ex)
        assert _diff(got, want) < 1e-5
        assert _diff(got, old) > 1e-4                # the delta really changed the answers
