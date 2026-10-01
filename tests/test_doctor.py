"""The environment check (python -m basal.doctor) names each PyTorch and GPU failure for what it is.

    .venv/bin/python -m pytest tests/test_doctor.py -q
"""
import sys
import types

from basal import doctor


def fake_torch(*, available=True, fail=None):
    """A stand-in for torch whose GPU test calculation raises `fail`."""
    t = types.ModuleType("torch")
    t.__version__ = "2.11.0+cu130"
    t.bfloat16 = "bfloat16"
    t.version = types.SimpleNamespace(cuda="13.0")

    class Tensor:
        def __matmul__(self, other):
            return self

        def sum(self):
            return types.SimpleNamespace(item=lambda: 1.0)

    def randn(*a, **k):
        if fail:
            raise fail
        return Tensor()

    t.randn = randn
    t.cuda = types.SimpleNamespace(is_available=lambda: available, get_device_name=lambda i: "NVIDIA GB10",
                                   get_device_capability=lambda i: (12, 1))
    return t


def run(monkeypatch, torch):
    monkeypatch.setitem(sys.modules, "torch", torch)
    return doctor.check_torch()


def test_a_working_gpu(monkeypatch):
    problems, lines = run(monkeypatch, fake_torch())
    assert problems == 0 and "GPU: NVIDIA GB10, compute capability 12.1, bfloat16 matmul works" in lines[-1]


def test_out_of_memory_is_not_an_import_failure(monkeypatch):
    class OutOfMemoryError(RuntimeError):
        pass
    for err in (OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 MiB."), RuntimeError("CUDA error: out of memory")):
        problems, lines = run(monkeypatch, fake_torch(fail=err))
        text = "\n".join(lines)
        assert problems == 1 and "out of memory right now" in text and "PyTorch itself is fine" in text
        assert "failed to import" not in text and "PyTorch 2.11.0+cu130" in lines[0]


def test_another_gpu_failure_is_reported_as_itself(monkeypatch):
    problems, lines = run(monkeypatch, fake_torch(fail=RuntimeError("no kernel image is available")))
    assert problems == 1 and "test calculation on it failed: no kernel image is available" in lines[-1]
    assert "failed to import" not in "\n".join(lines)


def test_no_gpu_and_no_pytorch(monkeypatch):
    problems, lines = run(monkeypatch, fake_torch(available=False))
    assert problems == 1 and "No CUDA GPU visible" in lines[-1]
    problems, lines = run(monkeypatch, None)          # `import torch` raises ImportError
    assert problems == 1 and lines[0].strip().startswith("!! PyTorch failed to import")
