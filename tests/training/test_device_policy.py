"""The device policy (docs/trainer/ARCHITECTURE.md section 8) for hardware this machine doesn't have, by simulating what
PyTorch reports on each: tier, precision and optimizer settings. The real GB10 path is exercised by every job."""
import sys
import types

import pytest

from basal.training import device


class Props:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def fake_torch(monkeypatch, cuda=None, hip=None, xpu=None, mps=False):
    t = types.SimpleNamespace()
    t.version = types.SimpleNamespace(hip=hip, cuda="13.0")
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda is not None, get_device_properties=lambda i: cuda)
    t.xpu = types.SimpleNamespace(is_available=lambda: xpu is not None, get_device_properties=lambda i: xpu)
    t.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    monkeypatch.setitem(sys.modules, "torch", t)
    monkeypatch.setattr(device, "_config_unified", lambda: False)
    monkeypatch.setattr(device, "_installed_backend", lambda: None)


def test_cpu_only_is_refused(monkeypatch):
    fake_torch(monkeypatch)
    monkeypatch.delenv("BASAL_TRAIN_ALLOW_CPU", raising=False)
    with pytest.raises(RuntimeError, match="needs a GPU"):
        device.profile()


def test_nvidia_generations(monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Linux")
    fake_torch(monkeypatch, cuda=Props(name="NVIDIA RTX 4090", major=8, minor=9))
    p = device.profile()
    assert (p.tier, p.autocast, p.grad_scaler) == ("on", "bfloat16", False)
    fake_torch(monkeypatch, cuda=Props(name="Tesla T4", major=7, minor=5))
    p = device.profile()
    assert (p.tier, p.autocast, p.grad_scaler) == ("experimental", "float16", True)   # no real bf16 on Turing
    fake_torch(monkeypatch, cuda=Props(name="GTX 1080", major=6, minor=1))
    assert device.profile().tier == "off"
    fake_torch(monkeypatch, cuda=Props(name="NVIDIA GB10", major=12, minor=1))
    p = device.profile()
    assert p.tier == "on" and p.unified


def test_amd(monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Linux")
    fake_torch(monkeypatch, cuda=Props(name="AMD Radeon RX 7900 XTX", major=11, minor=0), hip="6.4")
    p = device.profile()
    assert (p.backend, p.tier, p.autocast, p.fast_kernels) == ("rocm", "experimental", "bfloat16", False)
    monkeypatch.setattr(device.platform, "system", lambda: "Windows")
    assert device.profile().tier == "off"


def test_intel(monkeypatch):
    fake_torch(monkeypatch, xpu=Props(name="Intel(R) Arc(TM) B580 Graphics", has_subgroup_matrix_multiply_accumulate=True))
    p = device.profile()
    assert (p.kind, p.tier, p.autocast, p.grad_scaler) == ("xpu", "experimental", "bfloat16", False)
    assert p.optimizer == {"foreach": False} and p.max_single_alloc_gb == 4.0
    fake_torch(monkeypatch, xpu=Props(name="Intel(R) Arc(TM) Graphics", has_subgroup_matrix_multiply_accumulate=False))
    assert device.profile().tier == "off"          # Meteor Lake class: no matrix engines


def test_apple(monkeypatch):
    fake_torch(monkeypatch, mps=True)
    monkeypatch.setattr(device, "_macos_version", lambda: (15, 1))
    monkeypatch.setattr(device, "_apple_chip", lambda: "Apple M3 Pro")
    monkeypatch.setattr(device.psutil, "virtual_memory", lambda: Props(total=36e9, available=20e9))
    p = device.profile()
    assert (p.kind, p.tier, p.autocast, p.optimizer["fused"]) == ("mps", "on", "bfloat16", False)
    monkeypatch.setattr(device, "_apple_chip", lambda: "Apple M1")
    p = device.profile()
    assert p.tier == "experimental" and p.autocast is None            # M1: fp32
    monkeypatch.setattr(device, "_macos_version", lambda: (13, 6))
    assert device.profile().tier == "off"


def test_manager_gate_on_cpu_backend(monkeypatch):
    from basal.training import manager
    from basal import config
    monkeypatch.setattr(config, "load", lambda: {"backend": "cpu"})
    manager._probe.clear()
    ok, why, _ = manager.enabled({})
    assert not ok and "needs a GPU" in why
    manager._probe.clear()
