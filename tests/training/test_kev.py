"""Kev trainer contract, on the CPU with a tiny random Qwen2 backbone laid out as a real Kev checkpoint (a released LoRA,
head.pt with a temperature, the real Qwen2.5 tokenizer): load (released LoRA merged, new LoRA on top) -> units ->
score equals the serving adapter at T = 1 -> two optimizer steps -> export -> a freshly loaded serving adapter + attach
answers exactly like the trained model, at T = 1."""
import glob
import json
import os
from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("kev")
pytest.importorskip("peft")

QWEN = sorted(glob.glob(os.path.expanduser("~/.cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B/snapshots/*/")))
needs_tokenizer = pytest.mark.skipif(not QWEN, reason="the Qwen2.5-0.5B tokenizer is not in the Hugging Face cache")

TEAM = {"type": "choice", "instructions": "Which team should handle this?",
        "criteria": {"billing": "Charges, invoices and refunds.", "shipping": "Deliveries.", "tech": None,
                     "accounts": "Logins and passwords."}}
RECORDS = [
    {"state": {"customer": "I was charged twice for my order", "tier": "gold"},
     "questions": {"team": TEAM, "urgent": {"type": "noul", "instructions": "Does this need attention today?"},
                   "mood": {"type": "score", "instructions": "How upset is the customer?",
                            "criteria": ["Calm.", "Annoyed.", "Furious."]}},
     "answers": {"team": "billing", "urgent": True, "mood": 1}},
    {"state": "My parcel never arrived and the tracking page is blank.",
     "questions": {"team": TEAM, "urgent": {"type": "noul", "instructions": "Does this need attention today?",
                                            "criteria": {"true": "Yes, today.", "false": "It can wait."}}},
     "answers": {"team": "shipping", "urgent": False}},
    {"state": "I cannot log in since the update.", "questions": {"team": TEAM}, "answers": {"team": "accounts"}},
    # a state of more than 384 tokens: the studio serves it through kev's state-prefix cache (a different pass)
    {"state": "My invoice shows a charge I do not recognise. " * 60, "questions": {"team": TEAM},
     "answers": {"team": "billing"}},
]
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


@pytest.fixture
def tiny(tmp_path):
    """A Kev checkpoint directory on a tiny random Qwen2 base: adapter_config.json + adapter_model.safetensors (a
    non-zero released LoRA) + head.pt (pointer head, temperature 2)."""
    from peft import LoraConfig, get_peft_model
    from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
    from kev.model import PointerHead
    from basal.catalog import BY_ID, Repo
    tok = AutoTokenizer.from_pretrained(QWEN[0])
    cfg = Qwen2Config(vocab_size=len(tok), hidden_size=64, intermediate_size=128, num_hidden_layers=2,
                      num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=4096)
    torch.manual_seed(0)
    base = tmp_path / "base"
    Qwen2ForCausalLM(cfg).save_pretrained(base)
    tok.save_pretrained(base)
    lm = Qwen2ForCausalLM.from_pretrained(base).model
    released = get_peft_model(lm, LoraConfig(task_type="FEATURE_EXTRACTION", r=4, lora_alpha=8, target_modules=TARGETS))
    for n, p in released.named_parameters():
        if "lora_B" in n:
            p.data.normal_(0, 0.05)
    ck = tmp_path / "ck"
    released.save_pretrained(ck)
    head = PointerHead(64, 16)
    torch.save({"base": str(base), "head": head.state_dict(), "lora": 4, "head_dim": 16, "temperature": 2.0},
               ck / "head.pt")
    return replace(BY_ID["kev-0.5b"], id="kev-tiny", repo=Repo(str(ck)), base=Repo(str(base)))


def _dev():
    from basal.training.device import DeviceProfile
    return DeviceProfile("cpu", "cpu", "CPU", False, "on", "tests", None, "float32", False, {"foreach": False})


def _examples():
    from basal.training import dataformat
    imp = dataformat.import_examples("\n".join(json.dumps(r) for r in RECORDS), "t.jsonl")
    assert len(imp.examples) == len(RECORDS)
    return imp.examples


def _served(adapter, ex):
    from basal.adapters.base import DecideInput
    from basal.contract import render
    return adapter.decide(DecideInput(ex.request, ex.qs, render(ex.request.state), [])).probs


def _engine(fam, ex):
    fam.train_mode(False)
    with torch.inference_mode():
        rows = fam.score(fam.units(ex, train=False))
    return [lp.exp().tolist() for lp in rows[0]]


def _gap(a, b):
    return max(abs(x - y) for pa, pb in zip(a, b) for x, y in zip(pa, pb))


def test_kev_record_orders_only_reorder_choice_options():
    from basal.adapters.kev_adapter import kev_record
    from basal.contract import SystemOneRequest, normalise
    req = SystemOneRequest(**{k: RECORDS[0][k] for k in ("state", "questions")})
    qs = normalise(req)
    plain = kev_record(req, qs)
    shown = kev_record(req, qs, orders={"team": ["tech", "accounts", "billing", "shipping"]})
    assert plain["questions"][0]["options"][0] == "billing: Charges, invoices and refunds."
    assert shown["questions"][0]["options"] == ["tech", "accounts: Logins and passwords.",
                                                "billing: Charges, invoices and refunds.", "shipping: Deliveries."]
    assert shown["questions"][1:] == plain["questions"][1:] and shown["state"] == plain["state"]


def test_supports_refuses_more_than_255_options():
    from basal.catalog import BY_ID
    from basal.contract import SystemOneRequest, normalise
    from basal.training.dataformat import Example
    from basal.training.families.kev import KevTrainer
    req = SystemOneRequest(state="x", questions={"q": {"type": "choice", "criteria": [f"o{i}" for i in range(300)]}})
    ex = Example(0, req, normalise(req), [None], "", {})
    assert "255" in KevTrainer().supports(BY_ID["kev-4b"], ex)


@needs_tokenizer
def test_contract_and_parity(tiny, tmp_path):
    import random
    from basal.adapters.kev_adapter import KevAdapter
    from basal.contract import apply_temperature
    from basal.training.families.kev import KevTrainer
    spec, dev, exs = tiny, _dev(), _examples()
    fam = KevTrainer()
    recipe = fam.recipe(spec, dev, 100)
    assert sorted(recipe.lora_targets) == sorted(TARGETS) and recipe.grad_checkpointing
    fam.load(spec, dev, recipe, lambda *a: None)
    assert all(fam.supports(spec, ex) is None for ex in exs)
    assert fam.model.head.temperature == 1.0
    assert {p.dtype for p in fam.root.parameters() if p.requires_grad} == {torch.float32}
    assert all(("lora_" in n) or n.startswith("head.") for n, p in fam.root.named_parameters() if p.requires_grad)

    # at the start the trainable model IS the released model: the served answers, at T = 1 (the studio serves T = 2)
    released = KevAdapter(spec, {"device": "cpu"}, lambda *a: None)
    released.load()
    assert released.temperature == 2.0
    for ex in exs:
        assert _gap([apply_temperature(p, 2.0) for p in _engine(fam, ex)], _served(released, ex)) < 2e-3

    # one unit per example; choice options shuffled while training, never yes/no or scales; scores in key order
    rng = random.Random(0)
    units = [u for ex in exs for u in fam.units(ex, train=True, rng=rng)]
    assert [len(u.qi) for u in units] == [3, 2, 1, 1]
    assert units[3].enc["seg"].count(0) >= fam.model.prefix_min_tokens
    assert units[0].perm[0] is not None and units[0].perm[1] is None and units[0].perm[2] is None
    fam.train_mode(True)
    lp = fam.score(units)
    assert all(t.requires_grad and abs(float(t.detach().exp().sum()) - 1) < 1e-4 for row in lp for t in row)
    assert [len(t) for t in lp[0]] == [4, 2, 3]

    # two optimizer steps on the new LoRA and the head
    groups = fam.param_groups(recipe)
    assert [g["name"] for g in groups] == ["lora", "head"]
    before = {k: v.clone() for k, v in fam.trainable_state().items()}
    opt = torch.optim.AdamW([{k: v for k, v in g.items() if k != "name"} for g in groups], lr=1e-2)
    for _ in range(2):
        rows = fam.score(units)
        loss = torch.stack([-(torch.tensor(u.example.targets[i]) * t).sum()
                            for u, row in zip(units, rows) for i, t in zip(u.qi, row)]).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    after = fam.trainable_state()
    assert any(not torch.equal(before[k], after[k]) for k in before if "lora_B" in k)
    assert any(not torch.equal(before[k], after[k]) for k in before if k.startswith("head."))
    trained = [_engine(fam, ex) for ex in exs]

    # export -> a freshly loaded serving adapter + attach answers exactly like the trained model, at T = 1
    delta = tmp_path / "delta"
    fam.export(delta, recipe)
    assert json.loads((delta / "lora.json").read_text())["root"] == "lm"
    assert (delta / "head.safetensors").exists()
    fam.unload()
    fresh = KevAdapter(replace(spec, finetune_dir=str(delta)), {"device": "cpu"}, lambda *a: None)
    fresh.load()
    untouched = [_served(fresh, ex) for ex in exs]
    KevTrainer.attach(fresh, delta)
    assert fresh.temperature == 1.0 and fresh.server.model.head.temperature == 1.0
    assert {p.dtype for n, p in fresh.server.model.named_parameters() if "lora_" in n} == {torch.float32}
    moved = 0.0
    for ex, want, old in zip(exs, trained, untouched):
        got = _served(fresh, ex)
        assert _gap(got, want) < 2e-3
        moved = max(moved, _gap(got, old))
    assert moved > 1e-2                                                  # the delta really changed the answers
