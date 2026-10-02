"""Making room before a model loads (basal/worker.make_room): on a unified-memory Linux machine such as the NVIDIA
GB10, the GPU cannot use memory the system holds as file cache, so the worker frees that cache first.

    .venv/bin/python -m pytest tests/test_worker_memory.py -q
"""
import sys
from types import SimpleNamespace

import pytest

from basal import worker
from basal.training import device as devmod

BIG = SimpleNamespace(memory_gb=20.0)
SMALL = SimpleNamespace(memory_gb=1.2)


@pytest.fixture()
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(devmod, "profile", lambda: SimpleNamespace(unified=True))
    monkeypatch.setattr(devmod, "reclaim", lambda dev, need: seen.append(need) or 12.5)
    return seen


def test_frees_cache_for_a_large_model_on_a_unified_gpu(calls):
    assert worker.make_room(BIG, "cuda") == 12.5 and calls == [22.0]          # the model plus a margin
    assert worker.make_room(BIG, "cuda", extra_gb=16.0) == 12.5 and calls[-1] == 36.0


def test_does_nothing_where_it_is_not_needed(calls, monkeypatch):
    assert worker.make_room(SMALL, "cuda") == 0.0                             # small models fit in what is free
    assert worker.make_room(BIG, "cpu") == 0.0 and worker.make_room(BIG, "mps") == 0.0
    monkeypatch.setattr(devmod, "profile", lambda: SimpleNamespace(unified=False))   # a discrete card has its own memory
    assert worker.make_room(BIG, "cuda") == 0.0
    monkeypatch.setattr(sys, "platform", "win32")
    assert worker.make_room(BIG, "cuda") == 0.0
    assert calls == []


def test_a_failure_here_never_stops_the_load(calls, monkeypatch):
    def broken():
        raise RuntimeError("no GPU driver")
    monkeypatch.setattr(devmod, "profile", broken)
    assert worker.make_room(BIG, "cuda") == 0.0


def test_out_of_memory_is_recognised():
    assert worker.out_of_memory(RuntimeError("CUDA error: out of memory"))
    assert worker.out_of_memory(MemoryError("CUDA OOM while allocating"))
    assert not worker.out_of_memory(FileNotFoundError("model.safetensors"))
