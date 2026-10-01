"""CLM trainer contract, on the CPU with a tiny random Qwen3 embedder and tiny heads (the real Qwen3 tokenizer):
load -> units -> score equals the serving adapter's decide -> two optimizer steps -> export -> attach to a freshly
loaded adapter -> it answers exactly like the trained model."""
import glob
import json
import math
import os

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("clm")

QWEN = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--Qwen--Qwen3-8B/snapshots/*/")))
pytestmark = pytest.mark.skipif(not QWEN, reason="the Qwen3-8B tokenizer is not in the Hugging Face cache")

RECORDS = [
    {"state": {"customer": "I was charged twice for my order", "tier": "gold"},
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "Charges, invoices and refunds.", "shipping": "Deliveries.",
                                         "tech": "The product is broken."}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?"},
                   "mood": {"type": "score", "instructions": "How upset is the customer?",
                            "criteria": ["Calm.", "Annoyed.", "Furious."]}},
     "answers": {"team": "billing", "urgent": True, "mood": 1}},
    {"state": "My parcel never arrived and the tracking page is blank.",
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "Charges, invoices and refunds.", "shipping": "Deliveries.",
                                         "tech": "The product is broken."}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?",
                              "criteria": {"true": "Yes, today.", "false": "It can wait."}}},
     "answers": {"team": "shipping", "urgent": False}},
]


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    from transformers import AutoTokenizer, Qwen3Config, Qwen3Model
    from clm.heads import make_head
    from basal.adapters import clm_adapter
    tok = AutoTokenizer.from_pretrained(QWEN[0])
    cfg = Qwen3Config(vocab_size=len(tok), hidden_size=64, intermediate_size=128, num_hidden_layers=2,
                      num_attention_heads=4, num_key_value_heads=2, head_dim=16, max_position_embeddings=4096)
    torch.manual_seed(0)
    Qwen3Model(cfg).save_pretrained(tmp_path / "enc")
    tok.save_pretrained(tmp_path / "enc")
    hcfg = {"width": 32, "depth": 3, "projection_dim": 16, "activation": "gelu", "layernorm": True, "hidden_size": 64}
    sh = make_head(32, 3, 16, "gelu", True, False, 64)
    ah = make_head(32, 3, 16, "gelu", True, False, 64)
    ckpt = tmp_path / "CLM_v0.1-8B.pt"
    torch.save({"state_head": sh.state_dict(), "action_head": ah.state_dict(),
                "logit_scale": torch.tensor(math.log(20.0)), "cfg": hcfg}, ckpt)

    def load(self):
        from clm.engine import Engine
        emb = clm_adapter.LocalQwenEmbedder(str(tmp_path / "enc"), self.device, torch.float32, batch=3)
        self.engine = Engine(embedder=emb, checkpoint=str(ckpt), device=self.device, action_cache="0")

    monkeypatch.setattr(clm_adapter.ClmAdapter, "load", load)
    return tmp_path


def _setup():
    from basal.catalog import BY_ID
    from basal.training import dataformat
    from basal.training.device import DeviceProfile
    from basal.training.families.clm import ClmTrainer
    spec = BY_ID["clm-v0.1-8b"]
    dev = DeviceProfile("cpu", "cpu", "CPU", False, "on", "tests", None, "float32", False, {"foreach": False})
    imp = dataformat.import_examples("\n".join(json.dumps(r) for r in RECORDS), "t.jsonl")
    assert len(imp.examples) == len(RECORDS)          # (too few to train on; enough for the contract)
    fam = ClmTrainer()
    recipe = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, recipe, lambda *a: None)
    return spec, dev, imp.examples, fam, recipe


def _served(adapter, ex):
    from basal.adapters.base import DecideInput
    from basal.contract import render
    return adapter.decide(DecideInput(ex.request, ex.qs, render(ex.request.state), [])).probs


def _engine(fam, ex):
    with torch.no_grad():
        rows = fam.score(fam.units(ex, train=False))
    return [r[0].exp().tolist() for r in rows]


def _close(a, b, tol=1e-5):
    return max(abs(x - y) for pa, pb in zip(a, b) for x, y in zip(pa, pb)) < tol


def test_contract_and_parity(tiny, tmp_path):
    from basal.adapters.clm_adapter import ClmAdapter
    from basal.training.families.clm import ClmTrainer
    spec, dev, exs, fam, recipe = _setup()
    assert fam.recipe(spec, dev, 100).method == "heads"
    assert all(fam.supports(spec, ex) is None for ex in exs)

    # the engine's scores are the served probabilities, in key order, and differentiable
    for ex in exs:
        assert _close(_engine(fam, ex), _served(fam.adapter, ex))
    units = [u for ex in exs for u in fam.units(ex, train=True)]
    assert all(u.perm == [None] for u in units)                       # a dual encoder is never shuffled
    lp = fam.score(units)
    assert all(r[0].requires_grad for r in lp)
    assert all(abs(float(r[0].exp().sum()) - 1) < 1e-5 for r in lp)

    # two optimizer steps on the heads only
    groups = fam.param_groups(recipe)
    assert [g["name"] for g in groups] == ["head"]
    before = {k: v.clone() for k, v in fam.trainable_state().items()}
    opt = torch.optim.AdamW([{k: v for k, v in g.items() if k != "name"} for g in groups])
    for _ in range(2):
        loss = torch.stack([-(torch.tensor(u.example.targets[u.qi[0]]) * r[0]).sum()
                            for u, r in zip(units, fam.score(units))]).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    after = fam.trainable_state()
    assert any(not torch.equal(before[k], after[k]) for k in before)
    trained = [_engine(fam, ex) for ex in exs]

    # export -> a freshly loaded adapter + attach answers exactly like the trained model
    delta = tmp_path / "delta"
    fam.export(delta, recipe)
    assert (delta / "head.safetensors").exists() and not (delta / "lora.json").exists()
    fresh = ClmAdapter(spec, {"device": "cpu"}, lambda *a: None)
    fresh.load()
    untouched = [_served(fresh, ex) for ex in exs]
    ClmTrainer.attach(fresh, delta)
    for ex, want, old in zip(exs, trained, untouched):
        got = _served(fresh, ex)
        assert _close(got, want)
        assert not _close(got, old, 1e-4)                              # the delta really changed the answers
    assert abs(fresh.engine.heads["clm-latest"].scale - 20.0) < 1e-3   # the stored scale, unchanged


def test_embedding_never_depends_on_the_batch(tiny):
    """A text's embedding is the same whatever else is embedded with it (nothing is padded)."""
    import numpy as np

    from basal.adapters.clm_adapter import LocalQwenEmbedder
    emb = LocalQwenEmbedder(str(tiny / "enc"), "cpu", torch.float32, batch=4)
    texts = ["short", "a much longer text about a refund that was charged twice", "another one", "short too",
             "billing: charges and refunds"]
    alone = np.stack([emb._encode([t])[0][0] for t in texts])
    together, tokens = emb._encode(texts)
    assert np.array_equal(alone, together)
    assert tokens == sum(len(emb.tok(t, add_special_tokens=False)["input_ids"]) for t in texts)
    got, _ = emb.embed(texts[::-1])
    assert np.array_equal(got, alone[::-1])
