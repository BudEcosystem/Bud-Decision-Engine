"""What every model adapter implements.

An adapter lives inside a worker process (basal.worker) and owns exactly one
loaded model. It receives normalised questions (basal.contract.Q) and returns,
per question, probabilities in key order. It never formats answers itself.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from ..catalog import ModelSpec
from ..contract import Q, SystemOneRequest


@dataclass
class DecideInput:
    request: SystemOneRequest
    questions: list[Q]
    state_text: str
    media: list[dict] = field(default_factory=list)   # [{"type": "image"|"audio"|"video", "path": "/abs/file"}]


@dataclass
class DecideOutput:
    probs: list[list[float]]
    input_tokens: int | None = None
    passes: int = 1                     # forward passes used (1 for single-pass models)
    notes: list[str] = field(default_factory=list)   # things the UI should tell the user (e.g. "image ignored")
    extras: dict[str, dict] = field(default_factory=dict)  # per-question model-specific signals, keyed by question id


class Adapter:
    """Base class. `stage` reports human-readable loading progress to the UI."""

    def __init__(self, spec: ModelSpec, options: dict, stage: Callable[[str, float | None], None]):
        self.spec = spec
        self.options = options
        self.stage = stage
        from ..config import default_device
        self.device = options.get("device") or default_device()

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def load(self) -> None:
        raise NotImplementedError

    def effective_device(self) -> str | None:
        """Where the model actually ended up, when its library can move it (e.g. to the CPU after a GPU memory error)."""
        return None

    def warmup(self) -> None:
        """Run one tiny request so the first real request isn't slow (kernel compilation, CUDA graph capture)."""
        from ..contract import SystemOneRequest, normalise
        req = SystemOneRequest(state="The customer asked for a refund.",
                               questions={"q": {"type": "noul", "instructions": "Is this about money?"}})
        self.decide(DecideInput(req, normalise(req), "The customer asked for a refund."))

    # -- inference ----------------------------------------------------------------------------------------------------
    def decide(self, x: DecideInput) -> DecideOutput:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------------------------------------------------
    @staticmethod
    def snapshot(repo_id: str) -> str:
        """Local path of an already-downloaded Hub repo (never touches the network).

        The studio downloads repos with include/exclude patterns (e.g. it skips README images). huggingface_hub >= 1.3
        checks a cached snapshot against the full repo listing unless it is given the same patterns, so pass them."""
        from huggingface_hub import snapshot_download
        from ..catalog import CATALOG
        repo = next((r for s in CATALOG for r in s.repos() if r.id == repo_id), None)
        allow = list(repo.include) if repo and repo.include else None
        ignore = list(repo.exclude) if repo and repo.exclude else None
        return snapshot_download(repo_id, local_files_only=True, allow_patterns=allow, ignore_patterns=ignore)

    def torch_dtype(self):
        import torch
        if self.options.get("dtype") == "fp32" or self.device == "cpu":
            return torch.float32
        if self.device == "mps":   # bfloat16 on Apple GPUs needs macOS 14 or newer
            try:
                torch.ones(1, dtype=torch.bfloat16, device="mps")
                return torch.bfloat16
            except Exception:  # noqa: BLE001
                return torch.float16
        return torch.bfloat16

    @staticmethod
    def probs_from_map(q: Q, mapping: dict, aliases: dict[str, str] | None = None) -> list[float]:
        """Pick probabilities out of a {key: p} dict in q.keys order, tolerating alias keys (e.g. 'yes' for 'true')."""
        aliases = aliases or {}
        inv = {}
        for k, v in mapping.items():
            inv[str(k)] = float(v)
            if str(k) in aliases: inv[aliases[str(k)]] = float(v)
        missing = [k for k in q.keys if k not in inv]
        if missing:
            raise ValueError(f"question '{q.id}': model output lacks probabilities for {missing}; got {list(mapping)}")
        return [inv[k] for k in q.keys]


NOUL_ALIASES = {"yes": "true", "no": "false", "True": "true", "False": "false", "1": "true", "0": "false"}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "0") == "1"
