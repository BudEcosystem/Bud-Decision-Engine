"""Intern-Decision trainer mechanics on a tiny random Qwen3.5 with the real tokenizer and inference.py (CPU only):
the trainer's evaluation scores are exactly the served answers, the training path is differentiable and agrees with
evaluation, and a stored delta attached to a fresh serving adapter reproduces the trained model (docs/trainer
ARCHITECTURE.md, Appendix A)."""
import json
import math
import shutil
import sys

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
if "fla" not in sys.modules:
    sys.modules["fla"] = None      # flash-linear-attention is GPU-only; transformers would use it for CPU tensors

REPO = "internlm/Intern-Decision-4B"
COPY = ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "added_tokens.json", "special_tokens_map.json",
        "vocab.json", "merges.txt", "preprocessor_config.json", "video_preprocessor_config.json", "inference.py")

RECORDS = [
    {"state": {"ticket": "My card was charged twice for one order.", "channel": "email"},
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "charges and refunds", "shipping": None, "tech": "bugs"}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?"},
                   "severity": {"type": "score", "instructions": "How severe is it?",
                                "criteria": ["minor", "moderate", "severe"]}},
     "answers": {"team": "billing", "urgent": True, "severity": 1}},
    {"state": "The parcel never arrived and tracking stopped a week ago.",
     "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                            "criteria": {"billing": "charges and refunds", "shipping": None, "tech": "bugs"}}},
     "answers": {"team": "shipping"}},
    {"state": "The app crashes when I open settings.",
     "questions": {"labels": {"type": "multi", "instructions": "Which apply?",
                              "criteria": {"bug": "a defect", "billing": None}},
                   "urgent": {"type": "noul", "instructions": "Is this urgent?",
                              "criteria": {"true": "blocks the customer", "false": ""}}},
     "answers": {"labels": ["bug"], "urgent": False}},
]


def _snapshot():
    try:
        from basal.adapters.base import Adapter
        return Adapter.snapshot(REPO)
    except Exception:  # noqa: BLE001
        pytest.skip("Intern-Decision-4B is not in the Hugging Face cache")


@pytest.fixture(scope="module")
def tiny_intern(tmp_path_factory):
    from pathlib import Path

    from transformers import Qwen3_5Config, Qwen3_5ForConditionalGeneration
    src = Path(_snapshot())
    out = tmp_path_factory.mktemp("tiny-intern")
    c = json.loads((src / "config.json").read_text())
    t, v = c["text_config"], c["vision_config"]
    t.update(num_hidden_layers=4, hidden_size=64, intermediate_size=128, num_attention_heads=2, num_key_value_heads=1,
             head_dim=32, linear_num_value_heads=2, linear_num_key_heads=2, linear_key_head_dim=16,
             linear_value_head_dim=16, layer_types=t["layer_types"][:4], mtp_num_hidden_layers=0)
    t["rope_parameters"] = dict(t["rope_parameters"], mrope_section=[2, 1, 1])
    v.update(depth=1, hidden_size=32, intermediate_size=64, num_heads=2, out_hidden_size=64)
    cfg = Qwen3_5Config(**{k: c[k] for k in c if k not in ("architectures", "transformers_version")})
    torch.manual_seed(0)
    model = Qwen3_5ForConditionalGeneration(cfg)
    with torch.no_grad():                     # sharper logits than the default init, so differences are visible
        model.lm_head.weight.normal_(0, 0.5)
    model.to(torch.bfloat16).save_pretrained(out)
    for name in COPY:
        if (src / name).exists():
            shutil.copy(src / name, out / name)
    return out


@pytest.fixture()
def setup(tiny_intern, monkeypatch):
    from basal.adapters import intern_adapter
    from basal.catalog import BY_ID
    from basal.training import dataformat
    from basal.training.device import DeviceProfile
    monkeypatch.setattr(intern_adapter.InternAdapter, "snapshot", staticmethod(lambda repo_id: str(tiny_intern)))
    spec = BY_ID["intern-decision-4b"]
    dev = DeviceProfile("cpu", "cpu", "CPU", False, "on", "tests", None, "float32", False, {"foreach": False})
    imp = dataformat.import_examples("\n".join(json.dumps(r) for r in RECORDS), "x.jsonl")
    assert len(imp.examples) == len(RECORDS)     # (too few to train on, which is fine for mechanics)
    return spec, dev, imp.examples


def _serve(spec, delta=None):
    from basal.adapters.intern_adapter import InternAdapter
    from basal.training.families.intern import InternTrainer
    a = InternAdapter(spec, {"device": "cpu"}, lambda *x: None)
    a.load()
    if delta is None:
        a.engine.temperature = 1.0
    else:
        InternTrainer.attach(a, delta)
        assert a.engine.temperature == 1.0
    return a


def _decide(a, ex):
    from basal.adapters.base import DecideInput
    from basal.contract import render
    return a.decide(DecideInput(ex.request, ex.qs, render(ex.request.state), [])).probs


def _eval(fam, exs):
    fam.train_mode(False)
    units = [u for ex in exs for u in fam.units(ex, train=False)]
    with torch.inference_mode():
        return fam.score(units)


def test_supports(setup):
    from basal.training.families.intern import InternTrainer
    spec, _, exs = setup
    fam = InternTrainer()
    assert all(fam.supports(spec, ex) is None for ex in exs)
    bad = exs[1]
    bad.record = {**bad.record, "state": "text with <decision> inside"}
    assert "<decision>" in fam.supports(spec, bad)


def test_scores_are_served_answers_and_delta_round_trips(setup, tmp_path):
    from basal.training.families.intern import InternTrainer
    spec, dev, exs = setup
    fam = InternTrainer()
    rec = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, rec, lambda t: None)
    assert fam.head_names == () and fam.autocast is None
    trainable = [n for n, p in fam.root.named_parameters() if p.requires_grad]
    assert trainable and all("lora_" in n and "language_model" in n for n in trainable)
    assert all(p.dtype == torch.float32 for p in fam.root.parameters() if p.requires_grad)
    assert any("linear_attn.in_proj_qkv.lora_A" in n for n in trainable)

    # 1. the released model: the trainer's scores are what the studio serves (at temperature 1)
    base_lp = _eval(fam, exs)
    served = _serve(spec)
    for ex, row in zip(exs, base_lp):
        assert len(row) == len(ex.qs)
        for q, lp, p in zip(ex.qs, row, _decide(served, ex)):
            assert lp.shape[0] == len(q.keys)
            assert abs(float(lp.exp().sum()) - 1) < 1e-4
            assert max(abs(math.exp(a) - b) for a, b in zip(lp.tolist(), p)) < 1e-5
    del served

    # 2. a "trained" LoRA: the training path is differentiable and agrees with evaluation
    torch.manual_seed(1)
    with torch.no_grad():
        for n, p in fam.root.named_parameters():
            if "lora_B" in n:
                p.normal_(0, 0.3)
    fam.train_mode(False)                     # no dropout, so both paths compute the same function
    units = [u for ex in exs for u in fam.units(ex, train=True)]
    out = fam.score(units)
    loss = -sum(lp[0] for row in out for lp in row)
    loss.backward()
    grads = [p.grad for n, p in fam.root.named_parameters() if p.requires_grad]
    assert all(g is not None for g in grads) and sum(float(g.abs().sum()) for g in grads) > 0
    tuned_lp = _eval(fam, exs)
    moved = 0.0
    for row_t, row_e, row_b in zip(out, tuned_lp, base_lp):
        for a, b, c in zip(row_t, row_e, row_b):
            assert float((a.detach().exp() - b.exp()).abs().max()) < 0.02
            moved = max(moved, float((b.exp() - c.exp()).abs().max()))
    assert moved > 1e-3

    # 3. export -> attach to a fresh serving adapter -> the same answers (parity)
    fam.export(tmp_path / "delta", rec)
    assert (tmp_path / "delta" / "lora.safetensors").exists() and not (tmp_path / "delta" / "head.safetensors").exists()
    served = _serve(spec, tmp_path / "delta")
    for ex, row in zip(exs, tuned_lp):
        for lp, p in zip(row, _decide(served, ex)):
            assert max(abs(math.exp(a) - b) for a, b in zip(lp.tolist(), p)) < 1e-5
