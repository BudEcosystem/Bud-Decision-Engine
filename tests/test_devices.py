"""Models on GPUs other than NVIDIA's (Intel "xpu" on Windows and Linux, Apple "mps") and on the processor.

    .venv/bin/python -m pytest tests/test_devices.py -q

The first Windows bug report was Laya on an Intel Core Ultra: "Laya failed to load: RuntimeError: expected scalar type
BFloat16 but found Float". The cause (adapters/base.plain_attention) and the three things done about it are tested
here: PyTorch's native attention fast path is switched off on those GPUs, Laya tries 16-bit precision on Intel's and falls
back to full precision if that does not pass a check, and a model that still fails on such a GPU runs on the
processor with a warning. Also: a model whose own library cannot use
a device is not offered on it. The parts that need PyTorch are skipped where it is not installed (CI's light jobs).
"""
import sys
from types import SimpleNamespace

import pytest

from basal import catalog, config, worker
from basal.adapters.base import Adapter, plain_attention

CPU = {"id": "cpu", "name": "CPU", "memory_gb": None, "unified_memory": False}
MACHINES = {
    "intel": [{"id": "xpu", "name": "Intel(R) Arc(TM) Graphics", "memory_gb": 16.0, "unified_memory": True}, CPU],
    "apple": [{"id": "mps", "name": "Apple M2", "memory_gb": 24.0, "unified_memory": True}, CPU],
    "nvidia": [{"id": "cuda", "name": "NVIDIA RTX 6000 Ada", "memory_gb": 48.0, "unified_memory": False}, CPU],
    "cpu": [CPU],
}


@pytest.fixture()
def machine(monkeypatch):
    def use(name):
        devs = MACHINES[name]
        monkeypatch.setattr(config, "available", lambda: devs)
        monkeypatch.setattr(config, "default_device", lambda: devs[0]["id"])
    return use


def run_on(model_id):
    d = catalog.BY_ID[model_id].to_dict()
    o = next(x for x in d["load_options"] if x["key"] == "device")
    return d["fit"], o["default"], [c[0] for c in o["choices"]]


# ---------------------------------------------------------------------------------------------- what is offered where


def test_jev_omni_is_offered_only_on_an_nvidia_gpu(machine):
    machine("nvidia")
    fit, default, choices = run_on("jev-omni")
    assert fit == {"ok": True, "short": "", "reason": ""} and default == "cuda" and choices == ["cuda"]
    for name, shown in (("intel", "Intel(R) Arc(TM) Graphics"), ("apple", "Apple M2")):
        machine(name)
        fit, _, _ = run_on("jev-omni")
        assert not fit["ok"] and fit["short"] == "Needs an NVIDIA GPU"
        assert fit["reason"].startswith("Needs an NVIDIA GPU. This computer runs models on " + shown)
    machine("cpu")
    assert run_on("jev-omni")[0]["short"] == "Needs a GPU"


def test_lev_runs_on_the_processor_where_there_is_no_nvidia_gpu(machine):
    # lev.load puts the model on an NVIDIA GPU or leaves it on the processor; say so instead of naming the other GPU
    for name in ("intel", "apple", "cpu"):
        machine(name)
        fit, default, choices = run_on("lev")
        assert fit["ok"] and default == "cpu" and choices == ["cpu"], name
    machine("nvidia")
    assert run_on("lev")[1:] == ("cuda", ["cuda", "cpu"])


def test_other_models_keep_every_device(machine):
    machine("intel")
    for model_id in ("laya", "laya-multilingual", "laya-typed-decisions", "julia-1", "gliner2.5-decide", "kev-0.5b",
                     "kev-4b", "intern-decision-4b", "clm-v0.1-8b"):
        fit, default, choices = run_on(model_id)
        assert default == "xpu" and choices == ["xpu", "cpu"], model_id
        assert fit["ok"] or fit["short"] == "Too large here", model_id
    machine("nvidia")
    assert all(not s.devices or "cuda" in s.devices for s in catalog.CATALOG)      # nothing lost where it worked


def test_too_large_and_needs_a_gpu_keep_their_words(machine):
    machine("nvidia")
    big = config.fit(60.0, False)
    assert not big["ok"] and big["short"] == "Too large here" and "60 GB" in big["reason"]
    machine("cpu")
    assert config.fit(1.0, True) == {"ok": False, "short": "Needs a GPU",
                                     "reason": "Needs a GPU. This computer runs models on the CPU."}
    assert config.fit(1.0, False)["ok"]


# ------------------------------------------------------------------------------------------------ the attention rule


def test_plain_attention_leaves_nvidia_and_the_processor_alone():
    assert plain_attention("cuda") is False and plain_attention("cpu") is False and plain_attention(None) is False


def test_every_adapter_applies_the_rule(monkeypatch):
    seen = []
    monkeypatch.setattr("basal.adapters.base.plain_attention", lambda device: seen.append(device))
    Adapter(catalog.BY_ID["laya"], {"device": "xpu"}, lambda *a: None)
    Adapter(catalog.BY_ID["julia-1"], {"device": "cuda"}, lambda *a: None)
    assert seen == ["xpu", "cuda"]


@pytest.fixture()
def torch_fast_path():
    torch = pytest.importorskip("torch")
    before = torch.backends.mha.get_fastpath_enabled()
    yield torch
    torch.backends.mha.set_fastpath_enabled(before)


def head_layer(torch, device="cpu"):
    """A decision-head layer as Laya and Julia build it."""
    torch.manual_seed(0)
    return torch.nn.TransformerEncoderLayer(64, 4, 256, 0.0, batch_first=True, norm_first=True).to(device).eval()


def test_the_fast_path_runs_inside_mixed_precision_off_nvidia_and_the_rule_stops_it(torch_fast_path, monkeypatch):
    torch = torch_fast_path
    layer = head_layer(torch)
    x = torch.randn(2, 5, 64)
    pad = torch.zeros(2, 5, dtype=torch.bool)
    calls = []
    real = torch._transformer_encoder_layer_fwd
    monkeypatch.setattr(torch, "_transformer_encoder_layer_fwd", lambda *a, **k: calls.append(1) or real(*a, **k))
    with torch.inference_mode():
        reference = layer(x, src_key_padding_mask=pad)
        assert calls == [1]                                    # plain 32-bit inference: the fast path, as always
        with torch.autocast("cpu", dtype=torch.bfloat16):
            # PyTorch's own check for "mixed precision is on" only sees NVIDIA's: this is the root cause
            assert torch.is_autocast_enabled() is False and torch.is_autocast_enabled("cpu") is True
            layer(x, src_key_padding_mask=pad)
            assert calls == [1, 1]                             # so the fast path ran inside mixed precision
            assert plain_attention("xpu") is True and torch.backends.mha.get_fastpath_enabled() is False
            mixed = layer(x, src_key_padding_mask=pad)
            assert calls == [1, 1]                             # the rule: ordinary code from now on
        plain = layer(x, src_key_padding_mask=pad)
        assert calls == [1, 1]
    assert torch.allclose(plain, reference, atol=1e-5)         # the ordinary code computes the same numbers
    assert float((mixed.float() - reference).abs().max()) < 0.1    # and mixed precision through it is only rounding


def test_the_windows_error_and_its_fix_on_an_nvidia_gpu(torch_fast_path, monkeypatch):
    """The Intel kernel reads the layer's 32-bit bias as bf16 and stops. NVIDIA's kernel has the same typed access, so
    the report reproduces on any NVIDIA GPU once PyTorch's check answers as it does on an Intel GPU."""
    torch = torch_fast_path
    if not torch.cuda.is_available():
        pytest.skip("needs an NVIDIA GPU")
    try:
        layer = head_layer(torch, "cuda")
        x = torch.randn(2, 5, 64, device="cuda")
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            pytest.skip("the GPU has no free memory right now")
        raise
    pad = torch.zeros(2, 5, dtype=torch.bool, device="cuda")
    real = torch.is_autocast_enabled
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        good = layer(x, src_key_padding_mask=pad)              # NVIDIA today: the check works, the fast path is skipped
        monkeypatch.setattr(torch, "is_autocast_enabled", lambda *a: real(*a) if a else False)
        with pytest.raises(RuntimeError, match="expected scalar type BFloat16 but found Float"):
            layer(x, src_key_padding_mask=pad)                 # the bug report, word for word
        plain_attention("xpu")
        fixed = layer(x, src_key_padding_mask=pad)
    assert torch.equal(fixed, good)                            # the same code path NVIDIA takes: identical numbers


class FakeLaya:
    """Answers like the laya runtime's agent; `half` says what 16-bit precision does on this pretend GPU."""

    def __init__(self, torch, kind="xpu", half="close", amp=True, dtype=None):
        self.torch = torch
        self.device = SimpleNamespace(type=kind)
        self.amp_enabled, self.dtype = amp, dtype if dtype is not None else torch.bfloat16
        self.half, self.asked = half, []

    def predict(self, state, questions, max_len=None):
        sixteen = self.amp_enabled and self.dtype is self.torch.float16
        self.asked.append("fp16" if sixteen else "fp32" if not self.amp_enabled else "other")
        if sixteen and self.half == "error":
            raise RuntimeError("could not create a primitive descriptor for the matmul primitive")
        shift = {"close": 0.004, "far": 0.3, "nan": float("nan")}.get(self.half, 0.0) if sixteen else 0.0
        out = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                out[qid] = {"noul": 0.8 + shift / 2}
            else:
                names = list(q["criteria"]) if isinstance(q["criteria"], dict) else [str(i) for i in range(len(q["criteria"]))]
                first = 0.7 - shift
                out[qid] = {"probabilities": {n: (first if i == 0 else (1 - first) / (len(names) - 1)) for i, n in enumerate(names)}}
        return {"answers": out, "usage": {"input_tokens": 12}}


def test_laya_on_an_intel_gpu_uses_16_bits_when_they_pass_the_check():
    torch = pytest.importorskip("torch")
    from basal.adapters.laya_adapter import precision_on_intel
    said = []
    agent = FakeLaya(torch)
    assert precision_on_intel(agent, said.append) == "fp16"
    assert agent.amp_enabled is True and agent.dtype is torch.float16
    assert agent.asked == ["fp32", "fp32", "fp16", "fp16"]                 # full precision first, as the reference
    assert said == ["16-bit precision checked against full precision on this GPU; using it"]


@pytest.mark.parametrize("half, reason", [("error", "could not create a primitive descriptor"),
                                          ("far", "up to 0.30 away from full precision"),
                                          ("nan", "answers that are not numbers")])
def test_laya_on_an_intel_gpu_falls_back_to_full_precision(half, reason):
    torch = pytest.importorskip("torch")
    from basal.adapters.laya_adapter import precision_on_intel
    said = []
    agent = FakeLaya(torch, half=half)
    assert precision_on_intel(agent, said.append) == "fp32"
    assert agent.amp_enabled is False and agent.dtype is torch.float32
    assert len(said) == 1 and reason in said[0] and said[0].endswith("using full precision")


def test_laya_elsewhere_keeps_the_runtimes_own_precision():
    torch = pytest.importorskip("torch")
    from basal.adapters.laya_adapter import precision_on_intel
    for kind, amp, dtype in (("cuda", True, torch.bfloat16), ("mps", True, torch.float16), ("cpu", False, torch.float32)):
        agent = FakeLaya(torch, kind=kind, amp=amp, dtype=dtype)
        assert precision_on_intel(agent) is None and (agent.amp_enabled, agent.dtype) == (amp, dtype) and agent.asked == []


def test_a_request_that_fails_in_16_bits_is_answered_in_full_precision(capsys):
    torch = pytest.importorskip("torch")
    from basal.adapters.base import DecideInput
    from basal.adapters.laya_adapter import LayaAdapter
    from basal.contract import SystemOneRequest, normalise
    a = LayaAdapter(catalog.BY_ID["laya"], {"device": "cpu"}, lambda *x: None)
    a.agent, a.max_len = FakeLaya(torch), 512
    a.precision = "fp16"
    a.agent.amp_enabled, a.agent.dtype = True, torch.float16
    req = SystemOneRequest.model_validate({"state": "x", "questions": {"q": {"type": "noul", "instructions": "Is it?"}}})
    qs = normalise(req)
    good = a.decide(DecideInput(req, qs, "x", []))
    assert good.notes == [] and a.precision == "fp16"
    a.agent.half = "error"                                                 # this request's shape fails in 16 bits
    out = a.decide(DecideInput(req, qs, "x", []))
    assert out.probs == [[pytest.approx(0.2), pytest.approx(0.8)]]
    assert out.notes == ["This model switched to full precision: a request failed in 16-bit precision on this GPU."]
    assert a.precision == "fp32" and a.agent.amp_enabled is False and a.agent.dtype is torch.float32
    assert "16-bit precision failed on a request" in capsys.readouterr().out
    again = a.decide(DecideInput(req, qs, "x", []))
    assert again.notes == [] and a.agent.asked[-1] == "fp32"               # and it stays there, quietly
    a.precision = "fp32"
    a.agent.predict = lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("something else"))
    with pytest.raises(RuntimeError, match="something else"):               # full precision failing is a real error
        a.decide(DecideInput(req, qs, "x", []))


# -------------------------------------------------------------------------------------------- the processor fallback


SPEC = SimpleNamespace(id="laya", needs_gpu=False, devices=(), memory_gb=1.2, finetune_dir="")
BF16 = RuntimeError("expected scalar type BFloat16 but found Float")


def test_when_a_failed_load_moves_to_the_processor():
    assert worker.cpu_fallback(SPEC, "xpu", BF16) and worker.cpu_fallback(SPEC, "mps", NotImplementedError("op missing"))
    assert not worker.cpu_fallback(SPEC, "cuda", BF16)                         # NVIDIA: a failure is not the backend's
    assert not worker.cpu_fallback(SPEC, "cpu", BF16) and not worker.cpu_fallback(SPEC, None, BF16)
    assert not worker.cpu_fallback(SimpleNamespace(needs_gpu=True, devices=()), "xpu", BF16)
    assert not worker.cpu_fallback(SimpleNamespace(needs_gpu=False, devices=("cuda",)), "xpu", BF16)
    assert worker.cpu_fallback(SimpleNamespace(needs_gpu=False, devices=("cuda", "cpu")), "xpu", BF16)
    assert not worker.cpu_fallback(SPEC, "xpu", RuntimeError("XPU out of memory"))   # the processor shares that memory
    assert not worker.cpu_fallback(SPEC, "xpu", ModuleNotFoundError("No module named 'laya'"))
    assert not worker.cpu_fallback(SPEC, "xpu", FileNotFoundError("model.safetensors"))
    assert not worker.cpu_fallback(SPEC, "xpu", OSError("offline mode: cannot find the requested files"))


class FakeAdapter:
    """Loads anywhere, but its warm-up request fails on the devices in `broken`."""
    broken: tuple = ()
    built: list = []

    def __init__(self, spec, options, stage):
        self.device = options.get("device")
        FakeAdapter.built.append(self.device)

    def load(self):
        pass

    def effective_device(self):
        return self.device

    def warmup(self):
        if self.device in self.broken:
            raise RuntimeError("expected scalar type BFloat16 but found Float")


@pytest.fixture()
def fake_worker(monkeypatch):
    monkeypatch.setattr(worker, "BY_ID", {"laya": SPEC, "big": SimpleNamespace(**{**vars(SPEC), "id": "big", "needs_gpu": True})})
    monkeypatch.setattr(worker.adapters, "get", lambda name: FakeAdapter)
    monkeypatch.setattr(worker, "make_room", lambda *a, **k: 0.0)
    monkeypatch.setattr(worker, "release_gpu", lambda: None)
    monkeypatch.setattr(worker, "log", lambda msg: None)
    monkeypatch.setattr(worker, "gpu_memory", lambda: {})
    monkeypatch.setattr(worker, "S", worker.State())
    monkeypatch.setattr(SPEC, "adapter", "laya", raising=False)
    monkeypatch.setattr(worker.BY_ID["big"], "adapter", "laya", raising=False)
    FakeAdapter.built, FakeAdapter.broken = [], ()
    return worker


def test_a_model_that_fails_on_an_intel_gpu_runs_on_the_processor_with_a_warning(fake_worker):
    FakeAdapter.broken = ("xpu",)
    fake_worker.load_model("laya", {"device": "xpu"})
    s = fake_worker.S
    assert s.status == "ready" and s.error is None and FakeAdapter.built == ["xpu", "cpu"]
    assert s.adapter.device == "cpu"
    assert s.warning == ("Running on the processor, which is slower: this model could not run on this computer's "
                         "Intel GPU (RuntimeError: expected scalar type BFloat16 but found Float).")


def test_a_model_that_loads_is_left_alone(fake_worker):
    for device in ("xpu", "mps", "cuda", "cpu"):
        FakeAdapter.built = []
        fake_worker.S.warning = None
        fake_worker.load_model("laya", {"device": device})
        assert fake_worker.S.status == "ready" and fake_worker.S.warning is None and FakeAdapter.built == [device]


def test_no_fallback_on_nvidia_or_for_a_model_that_needs_a_gpu(fake_worker):
    FakeAdapter.broken = ("cuda", "xpu")
    fake_worker.load_model("laya", {"device": "cuda"})
    assert fake_worker.S.status == "error" and "BFloat16" in fake_worker.S.error and FakeAdapter.built == ["cuda"]
    FakeAdapter.built = []
    fake_worker.load_model("big", {"device": "xpu"})
    assert fake_worker.S.status == "error" and FakeAdapter.built == ["xpu"]


def test_a_failure_on_the_processor_too_is_reported(fake_worker):
    FakeAdapter.broken = ("xpu", "cpu")
    fake_worker.load_model("laya", {"device": "xpu"})
    assert fake_worker.S.status == "error" and FakeAdapter.built == ["xpu", "cpu"] and "BFloat16" in fake_worker.S.error
