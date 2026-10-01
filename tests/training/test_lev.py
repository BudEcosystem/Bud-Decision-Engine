"""Lev trainer mechanics on a tiny random Qwen3.5 with a tiny "released" Lev LoRA, the real Lev tokenizer and the real
`lev` package (CPU only): the trainer continues the released adapter, unmerged, so its evaluation scores are the served
release's answers at temperature 1; yes/no comes from the 0-8 rating; the training path is differentiable and agrees
with evaluation; and a stored delta attached to a fresh serving adapter reproduces the trained model."""
import json
import math
import shutil
import sys

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
if "fla" not in sys.modules:
    sys.modules["fla"] = None      # flash-linear-attention is GPU-only; transformers would use it for CPU tensors
pytest.importorskip("lev")

REPO = "interfaze-ai/lev"
COPY = ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "calibration.json")
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

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
                            "criteria": {"billing": "charges and refunds", "shipping": None, "tech": "bugs",
                                         "sales": None}}},
     "answers": {"team": "shipping"}},
    {"state": "The app crashes when I open settings.",
     "questions": {"labels": {"type": "multi", "instructions": "Which apply?",
                              "criteria": {"bug": "a defect", "billing": None}}},
     "answers": {"labels": ["bug"]}},
]


def _snapshot():
    try:
        from basal.adapters.base import Adapter
        return Adapter.snapshot(REPO)
    except Exception:  # noqa: BLE001
        pytest.skip("Lev is not in the Hugging Face cache")


@pytest.fixture(scope="module")
def tiny_lev(tmp_path_factory):
    """A tiny random Qwen3.5 backbone and a tiny released LoRA laid out like the Lev release."""
    from pathlib import Path

    from peft import LoraConfig, get_peft_model
    from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig
    src = Path(_snapshot())
    root = tmp_path_factory.mktemp("tiny-lev")
    base_dir, rel = root / "base", root / "release"
    cfg = Qwen3_5TextConfig(vocab_size=248320, hidden_size=64, intermediate_size=128, num_hidden_layers=4,
                            num_attention_heads=2, num_key_value_heads=1, head_dim=32, linear_num_value_heads=2,
                            linear_num_key_heads=2, linear_key_head_dim=16, linear_value_head_dim=16,
                            layer_types=["linear_attention"] * 3 + ["full_attention"], tie_word_embeddings=True,
                            rope_parameters={"rope_type": "default", "rope_theta": 10000000, "partial_rotary_factor": 0.25,
                                             "mrope_section": [2, 1, 1], "mrope_interleaved": True})
    torch.manual_seed(0)
    model = Qwen3_5ForCausalLM(cfg)
    with torch.no_grad():
        model.lm_head.weight.normal_(0, 0.5)
    model = model.to(torch.bfloat16)
    model.save_pretrained(base_dir)
    peft_model = get_peft_model(model, LoraConfig(r=4, lora_alpha=8, target_modules=TARGETS, task_type="CAUSAL_LM"))
    with torch.no_grad():
        for n, p in peft_model.named_parameters():
            if "lora_B" in n:
                p.normal_(0, 0.2)
    peft_model.save_pretrained(rel)
    for name in COPY:
        shutil.copy(src / name, rel / name)
    (rel / "lev_release.json").write_text(json.dumps({"name": "tiny-lev", "base_model": str(base_dir), "lora_rank": 4,
                                                      "mode_b_head": False, "calibrated": True, "noul_readout": "rating",
                                                      "prompt_style": "chat"}))
    return rel


@pytest.fixture()
def setup(tiny_lev, monkeypatch):
    from basal.adapters import lev_adapter
    from basal.catalog import BY_ID
    from basal.training import dataformat
    from basal.training.device import DeviceProfile
    monkeypatch.setattr(lev_adapter.LevAdapter, "snapshot", staticmethod(lambda repo_id: str(tiny_lev)))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)      # lev.load would move the model to CUDA
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    spec = BY_ID["lev"]
    dev = DeviceProfile("cpu", "cpu", "CPU", False, "on", "tests", None, "float32", False, {"foreach": False})
    imp = dataformat.import_examples("\n".join(json.dumps(r) for r in RECORDS), "x.jsonl")
    assert len(imp.examples) == len(RECORDS)
    return spec, dev, imp.examples


def _serve(spec, delta=None):
    from basal.adapters.lev_adapter import LevAdapter
    from basal.training.families.lev import LevTrainer, lev_served
    a = LevAdapter(spec, {"device": "cpu"}, lambda *x: None)
    a.load()
    assert a.engine.calibration.temperatures            # the release ships its own temperatures
    if delta is None:
        lev_served(a.engine)
    else:
        LevTrainer.attach(a, delta)
    assert not a.engine.calibration.temperatures        # ... which never apply on top of the studio's
    return a


def _decide(a, ex):
    from basal.adapters.base import DecideInput
    from basal.contract import render
    return a.decide(DecideInput(ex.request, ex.qs, render(ex.request.state), [])).probs


def _eval(fam, exs):
    fam.train_mode(False)
    rows = []
    for ex in exs:
        units = fam.units(ex, train=False)
        assert [u.qi for u in units] == [[i] for i in range(len(ex.qs))]
        with torch.inference_mode():
            rows.append([lp for row in fam.score(units) for lp in row])
    return rows


def test_supports(setup):
    from basal.training.dataformat import import_examples
    from basal.training.families.lev import LevTrainer
    spec, _, exs = setup
    fam = LevTrainer()
    assert all(fam.supports(spec, ex) is None for ex in exs)
    big = {"state": "x", "questions": {"q": {"type": "choice", "criteria": {f"o{i}": None for i in range(69)}}},
           "answers": {"q": "o1"}}
    ex = import_examples(json.dumps(big), "x.jsonl").examples[0]
    assert "68" in fam.supports(spec, ex)


def test_scores_are_served_answers_and_delta_round_trips(setup, tmp_path):
    from basal.training.families.lev import LevTrainer
    spec, dev, exs = setup
    fam = LevTrainer()
    rec = fam.recipe(spec, dev, 100)
    fam.load(spec, dev, rec, lambda t: None)
    trainable = [n for n, p in fam.root.named_parameters() if p.requires_grad]
    assert trainable and all("lora_" in n and ".default." in n for n in trainable)     # the released adapter itself
    assert (rec.lora_r, rec.lora_alpha) == (4, 8)                                      # the release's shape
    assert all(p.dtype == torch.float32 for p in fam.root.parameters() if p.requires_grad)

    # 1. the released model (temperature 1): the trainer's scores are what the studio serves
    base_lp = _eval(fam, exs)
    served = _serve(spec)
    for ex, row in zip(exs, base_lp):
        for q, lp, p in zip(ex.qs, row, _decide(served, ex)):
            assert lp.shape[0] == len(q.keys) and abs(float(lp.exp().sum()) - 1) < 1e-4
            assert max(abs(math.exp(a) - b) for a, b in zip(lp.tolist(), p)) < 1e-4
    del served

    # 2. a "trained" LoRA; the training path (one shuffled order per choice question) is differentiable
    torch.manual_seed(1)
    with torch.no_grad():
        for n, p in fam.root.named_parameters():
            if "lora_B" in n:
                p.normal_(0, 0.3)
    fam.train_mode(False)
    import random
    units = [u for ex in exs for u in fam.units(ex, train=True, rng=random.Random(0))]
    assert any(u.perm[0] is not None for u in units)
    assert all(len(u.rows) == 1 for u in units)
    out = fam.score(units)
    loss = -sum(row[0][0] for row in out)
    loss.backward()
    grads = [p.grad for n, p in fam.root.named_parameters() if p.requires_grad]
    assert all(g is not None for g in grads) and sum(float(g.abs().sum()) for g in grads) > 0
    # without shuffling, the batched training path computes the served readout
    plain = [u for ex in exs for u in fam.units(ex, train=False)]
    batched = [row[0] for row in fam.score(plain)]
    tuned_lp = _eval(fam, exs)
    flat = [lp for row in tuned_lp for lp in row]
    moved = 0.0
    for a, b, c in zip(batched, flat, [lp for row in base_lp for lp in row]):
        assert float((a.detach().exp() - b.exp()).abs().max()) < 0.02
        moved = max(moved, float((b.exp() - c.exp()).abs().max()))
    assert moved > 1e-3

    # 3. export -> attach to a fresh serving adapter -> the same answers (parity)
    fam.export(tmp_path / "delta", rec)
    served = _serve(spec, tmp_path / "delta")
    for ex, row in zip(exs, tuned_lp):
        for lp, p in zip(row, _decide(served, ex)):
            assert max(abs(math.exp(a) - b) for a, b in zip(lp.tolist(), p)) < 1e-4
