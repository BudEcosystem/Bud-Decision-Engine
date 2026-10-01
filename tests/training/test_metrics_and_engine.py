"""Scores, calibration, the release gate's statistics, batching and the fine-tune registry (no GPU)."""
import json
import math
import random

from basal.training import metrics
from basal.training.engine import _batches, epochs_for
from basal.training.metrics import Pred


def pred(ex, gold, conf, n=3, qtype="choice", pid="q"):
    lp = [math.log((1 - conf) / (n - 1))] * n
    lp[gold] = math.log(conf)
    t = [0.0] * n
    t[gold] = 1.0
    return Pred(ex, "q", qtype, lp, t, n, pid)


def test_summary_matches_hand_computation():
    ps = [pred(0, 0, 0.8), pred(1, 1, 0.6), pred(2, 2, 0.3)]      # the last one is wrong (0.35 > 0.3 elsewhere)
    s = metrics.summarise(ps)
    assert abs(s["accuracy"] - 2 / 3) < 1e-9
    assert abs(s["log_loss"] - (-(math.log(0.8) + math.log(0.6) + math.log(0.3)) / 3)) < 1e-9


def test_temperature_fit_softens_overconfidence():
    rng = random.Random(0)
    ps = []
    for i in range(400):
        gold = rng.randrange(3)
        right = rng.random() < 0.6
        top = gold if right else (gold + 1) % 3
        ps.append(Pred(i, "q", "choice", [math.log(0.98) if k == top else math.log(0.01) for k in range(3)],
                       [1.0 if k == gold else 0.0 for k in range(3)], 3, "q"))
    t = metrics.fit_temperatures(ps)["all"]
    assert t > 2.0                                             # 98% confident but 60% right: soften a lot
    assert metrics.summarise(ps, {"all": t})["log_loss"] < metrics.summarise(ps)["log_loss"]


def test_paired_gain_detects_a_real_improvement_and_not_noise():
    base = [pred(i, 0, 0.6 if i % 2 else 0.2) for i in range(200)]   # half right
    better = [pred(i, 0, 0.7) for i in range(200)]                    # all right
    g = metrics.paired_accuracy_gain(base, better)
    assert g["gain"] > 0.45 and g["p_better"] > 0.99
    same = metrics.paired_accuracy_gain(base, base)
    assert same["gain"] == 0 and same["p_better"] == 0 and same["p_worse"] == 0


def test_collapse_is_flagged_only_when_answers_really_vary():
    varied_gold_one_answer = [Pred(i, "q", "choice", [0.0, -5.0, -5.0], [1.0 if k == i % 3 else 0.0 for k in range(3)], 3)
                              for i in range(60)]
    assert metrics.collapse(varied_gold_one_answer) == ["q"]
    honest_majority = [Pred(i, "q", "choice", [0.0, -5.0, -5.0], [1.0, 0.0, 0.0], 3) for i in range(60)]
    assert metrics.collapse(honest_majority) == []


def test_epochs_scale_with_data():
    assert epochs_for(100, 4) == 8
    assert epochs_for(1500, 4) == 4
    assert epochs_for(5000, 4) == 2


class U:
    def __init__(self, cost):
        self.cost = cost


def test_batches_respect_budget_and_step_size():
    rng = random.Random(0)
    units = [U(rng.randrange(10, 500)) for _ in range(200)]
    for b in _batches(units, 2000, shuffle=True, rng=rng, max_units=16):
        assert len(b) <= 16
        assert len(b) == 1 or max(u.cost for u in b) * len(b) <= 2000
    assert sum(len(b) for b in _batches(units, 2000, shuffle=False)) == 200


def test_finetune_registry_derives_specs(tmp_path, monkeypatch):
    from basal import finetunes
    monkeypatch.setattr(finetunes, "FT_DIR", tmp_path)
    d = tmp_path / "julia-1-ft-1"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"id": "julia-1-ft-1", "name": "Julia for tickets", "base_model": "julia-1",
                                                 "temperature": {"all": 1.3}, "accuracy_before": 0.5, "accuracy_after": 0.7}))
    from basal.catalog import CATALOG
    specs = finetunes.derived_specs([s for s in CATALOG if not s.finetune_dir])
    s = next(x for x in specs if x.id == "julia-1-ft-1")
    assert s.base_id == "julia-1" and s.adapter == "julia" and s.finetune_dir == str(d) and dict(s.temperature) == {"all": 1.3}
    from basal.contract import SystemOneRequest, normalise
    qs = normalise(SystemOneRequest(state="x", questions={"q": {"type": "noul"}}))
    out = finetunes.calibrate(s, qs, [[0.2, 0.8]])
    assert 0.5 < out[0][1] < 0.8                               # softened by T = 1.3, winner unchanged


def test_showcase_picks_an_example_the_original_got_wrong_and_the_fine_tune_gets_right():
    from basal.training.dataformat import import_examples
    from basal.training.engine import Engine
    csv_text = "text,team\n" + "\n".join(f"ticket {i},{['billing', 'shipping'][i % 2]}" for i in range(40))
    exs = import_examples(csv_text, "t.csv").examples[:3]

    def p(ex, right):
        gold = ex.gold(0)
        lp = [math.log(0.9) if (k == gold) == right else math.log(0.1) for k in range(2)]
        return Pred(ex.index, "team", "choice", lp, ex.targets[0], 2, ex.qs[0].id, "|".join(ex.qs[0].keys))
    base = [p(exs[0], True), p(exs[1], False), p(exs[2], False)]
    tuned = [p(exs[0], True), p(exs[1], False), p(exs[2], True)]
    sc = Engine._showcase(None, exs, base, tuned)
    assert sc["fixed"] == 1 and sc["text"] == "ticket 2"
    assert sc["questions"][0]["before"] != sc["questions"][0]["right"] == sc["questions"][0]["after"]
    assert sc["request"]["state"] == "ticket 2" and "team" in sc["request"]["questions"]


def test_collapse_keeps_questions_that_share_an_id_apart():
    # Two different questions both called "disposition"; each answer is mostly the first option for one and the second
    # for the other. Neither has collapsed, but pooled by id it looks like "always option 0 while the truth varies".
    a = [Pred(i, "disposition", "choice", [0.0, -5.0], [1.0, 0.0] if i % 6 else [0.0, 1.0], 2, "disposition",
              "close|contain") for i in range(30)]
    b = [Pred(100 + i, "disposition", "choice", [0.0, -5.0], [0.0, 1.0] if i % 6 else [1.0, 0.0], 2, "disposition",
              "approve|hold") for i in range(30)]
    assert metrics.collapse(a + b) == []
    pooled = [Pred(p.example, p.qid, p.qtype, p.logp, p.target, 2, p.pid) for p in a + b]
    assert metrics.collapse(pooled) == ["disposition"]


def test_evaluate_set_is_the_held_out_examples_in_the_evaluate_format():
    from basal.training.dataformat import import_examples
    from basal.training.engine import Engine
    csv_text = "text,team,stars\n" + "\n".join(f"ticket {i},{['billing', 'shipping'][i % 2]},{1 + i % 5}" for i in range(40))
    exs = import_examples(csv_text, "t.csv").examples[:4]
    ev = Engine._evaluate_set(exs)
    assert ev["question_id"] == "team" and ev["question"]["type"] == "choice"
    assert [json.loads(l) for l in ev["text"].splitlines()][:2] == [{"text": "ticket 0", "label": "billing"},
                                                                     {"text": "ticket 1", "label": "shipping"}]


def test_replay_share_carries_across_small_micro_batches():
    import types
    from basal.training.engine import Engine

    class Fam:
        def score(self, units):
            import torch
            return [[torch.zeros(2, requires_grad=True)] for _ in units]
    torch = __import__("pytest").importorskip("torch")
    eng = Engine.__new__(Engine)
    eng.fam, eng.recipe = Fam(), types.SimpleNamespace(replay_ratio=0.5, label_smoothing=0.0, kl_weight=1.0)
    ex = types.SimpleNamespace(targets=[[1.0, 0.0]])
    unit = types.SimpleNamespace(example=ex, qi=[0])
    drawn = []

    def replay():
        while True:
            drawn.append(1)
            yield unit
    it = replay()
    teacher = {(id(ex), 0): [0.5, 0.5]}
    for _ in range(10):                       # ten one-unit micro-batches at ratio 0.5: five replay units in all
        eng._batch_loss([unit], it, teacher)
    assert len(drawn) == 5


def test_release_gate_weighs_general_losses_against_the_task_gain():
    import types
    from basal.training.engine import Engine
    eng = Engine.__new__(Engine)
    eng.job = types.SimpleNamespace(seed=0)

    def preds(n, right, offset=0):          # n two-option questions, the first `right` of them answered correctly
        return [pred(offset + i, 0, 0.8 if i < right else 0.2, n=2, pid=f"q{i}") for i in range(n)]

    def verdict(test_before, test_after, guard_before, guard_after):
        base = {"test": preds(200, test_before), "guard": preds(1000, guard_before, 10**4), "calibration": []}
        tuned = {"test": preds(200, test_after), "guard": preds(1000, guard_after, 10**4), "calibration": []}
        return eng.gate(base, tuned, {}, {})["outcome"]
    assert verdict(100, 130, 600, 585) == "improved"       # +15 points on the task, -1.5 on general questions
    assert verdict(100, 104, 600, 585) == "forgot"         # +2 points does not outweigh -1.5
    assert verdict(100, 140, 600, 575) == "forgot"         # -2.5: too much, whatever the gain
    assert verdict(100, 100, 600, 600) == "not_improved"


def test_an_example_too_big_for_memory_turns_on_checkpointing_then_is_skipped(monkeypatch):
    import types
    from basal.training import engine as eng_mod
    from basal.training.engine import Engine
    monkeypatch.setattr(eng_mod, "_empty_cache", lambda kind: None)
    monkeypatch.setattr("basal.training.device.reclaim", lambda dev, gb: 0.0)

    class Fam:
        root = types.SimpleNamespace(zero_grad=lambda set_to_none=True: None)
        calls = 0

        def enable_checkpointing(self):
            return True

        def score(self, units):
            Fam.calls += 1
            raise RuntimeError("CUDA out of memory")
    notes = []
    eng = Engine.__new__(Engine)
    eng.fam, eng.dev, eng.should_stop = Fam(), types.SimpleNamespace(kind="cpu"), lambda: None
    eng.out = __import__("pathlib").Path("/tmp")
    eng.emit = notes.append
    eng.recipe = types.SimpleNamespace(token_budget=4096, grad_checkpointing=False, replay_ratio=0.0,
                                       label_smoothing=0.0, kl_weight=1.0)
    eng._budget0, eng._n_train_examples = 4096, 100
    unit = types.SimpleNamespace(example=types.SimpleNamespace(targets=[[1.0, 0.0]]), qi=[0])
    assert eng._micro_step([unit], iter(()), {}, None) == (0.0, 0)
    assert eng.recipe.grad_checkpointing and eng.recipe.token_budget == 4096   # tried the lighter way first
    assert id(unit.example) in eng._skipped
    assert any("slower way" in n.get("text", "") for n in notes) and any("left out" in n.get("text", "") for n in notes)


def _tensor_file(path, name="w"):
    import struct
    header = json.dumps({name: {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\x00" * 8)


def test_a_fine_tune_exports_to_a_zip_and_imports_into_another_studio(tmp_path, monkeypatch):
    import zipfile
    from basal import finetunes
    src, dst = tmp_path / "a", tmp_path / "b"
    d = src / "julia-1-ft-20261001-120000"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"id": d.name, "name": "Julia 1 for tickets", "base_model": "julia-1",
                                                 "family": "julia", "job": "/home/someone/jobs/1", "temperature": {}}))
    (d / "lora.json").write_text(json.dumps({"root": "encoder", "r": 16, "alpha": 32, "dropout": 0.05, "targets": ["Wo"]}))
    _tensor_file(d / "lora.safetensors")
    monkeypatch.setattr(finetunes, "FT_DIR", src)
    monkeypatch.setattr(finetunes, "DATA", tmp_path)
    monkeypatch.setattr(finetunes, "refresh", lambda: [])
    path, name = finetunes.export_zip(d.name)
    assert name == "Julia-1-for-tickets.zip"
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist()) == ["lora.json", "lora.safetensors", "manifest.json"]
        assert "job" not in json.loads(z.read("manifest.json"))          # local paths stay on this computer
    monkeypatch.setattr(finetunes, "FT_DIR", dst)
    man = finetunes.import_zip(path)
    assert man["id"] == d.name and (dst / d.name / "lora.safetensors").exists() and man["imported"]
    again = finetunes.import_zip(path)                                    # the same file twice: a second copy
    assert again["id"] != d.name and again["id"].startswith("julia-1-ft-")


def test_import_refuses_what_is_not_a_fine_tune(tmp_path, monkeypatch):
    import pytest
    import zipfile
    from basal import finetunes
    monkeypatch.setattr(finetunes, "FT_DIR", tmp_path / "ft")
    monkeypatch.setattr(finetunes, "refresh", lambda: [])

    def make(files):
        p = tmp_path / f"x{len(list(tmp_path.iterdir()))}.zip"
        with zipfile.ZipFile(p, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        return p
    good_lora = (tmp_path / "t.safetensors")
    _tensor_file(good_lora)
    man = json.dumps({"id": "x", "base_model": "julia-1", "family": "julia"})
    with pytest.raises(ValueError, match="unexpected"):
        finetunes.import_zip(make({"manifest.json": man, "../evil.sh": "rm -rf /"}))
    with pytest.raises(ValueError, match="doesn't have"):
        finetunes.import_zip(make({"manifest.json": json.dumps({"base_model": "no-such-model"}),
                                   "head.safetensors": good_lora.read_bytes()}))
    with pytest.raises(ValueError, match="not a tensor file"):
        finetunes.import_zip(make({"manifest.json": man, "head.safetensors": b"\xff" * 64}))
    with pytest.raises(ValueError, match="isn't a .zip"):
        bad = tmp_path / "bad.zip"
        bad.write_text("hello")
        finetunes.import_zip(bad)
    assert not any((tmp_path / "ft").glob("*")) if (tmp_path / "ft").exists() else True
