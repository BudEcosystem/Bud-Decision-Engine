"""What every model family's trainer implements, and the LoRA / delta helpers they share.

A family trainer owns one loaded model inside the training job. The engine asks it for *scoring units* (one unit
covers one or more primitive questions of one example, depending on whether the model answers questions one at a
time or all at once) and for the *log-probabilities* of every option of those questions, in the studio's key order
(`contract.Q.keys`). Everything else (the loop, the loss, replay, evaluation, calibration, the release gate) is the
engine's.

A fine-tune is stored as a *delta* on the released model, the same layout for every family:

    delta/lora.safetensors   LoRA tensors, keyed by their path inside the family's LoRA root module
    delta/lora.json          {"root": <module path>, "r", "alpha", "dropout", "targets"}
    delta/head.safetensors   the trainable head's tensors, keyed "<head name>.<parameter>"
    delta/manifest.json      written by the engine

The LoRA starts at zero (its B matrix is zero), so before the first step the trainable model is exactly the released
model: baselines and replay targets are computed on the same loaded model, with no second copy in memory.
"""
from __future__ import annotations

import json
import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..dataformat import Example


@dataclass
class Unit:
    """One forward-pass unit: `qi` indexes `example.qs`. `perm[i]`, when set, is the option order shown to the model
    for question qi[i] (training-time shuffling); scores still come back in key order."""
    example: Example
    qi: list[int]
    perm: list[list[int] | None] = field(default_factory=list)
    cost: int = 0                 # tokens (or another size measure) used for batching


@dataclass
class Recipe:
    """Best-practice defaults per family; the engine scales epochs and evaluation cadence to the data."""
    method: str = "lora"                      # lora | heads (CLM) | full (advanced, small encoders only)
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_targets: list[str] = field(default_factory=list)
    lr: float = 1e-4                          # LoRA (or full) learning rate
    head_lr: float = 1e-4
    weight_decay: float = 0.01
    warmup: float = 0.06
    max_epochs: float = 4.0
    effective_batch: int = 16                 # questions per optimizer step
    token_budget: int = 4096                  # tokens per micro-batch before the memory planner adjusts it
    max_tokens: int = 1024                    # longest unit accepted
    replay_ratio: float = 0.5                 # replay questions per task question
    kl_weight: float = 1.0
    shuffle_options: bool = True              # permute choice options during training (not score / noul)
    label_smoothing: float = 0.0
    patience: int = 3                         # evaluations without improvement before stopping
    drift_weight: float = 1.0                 # weight of general-answer drift (KL) in checkpoint selection
    train_head: bool = True                   # train the family's decision head too (it is shared by every question)
    grad_checkpointing: bool = False
    max_replay: int = 0                       # cap on replay units (0: as many as the run needs); for families where
                                              # each replay unit is expensive to prepare (CLM embeds every text once)
    notes: list[str] = field(default_factory=list)


class FamilyTrainer:
    family: str = ""
    head_names: tuple[str, ...] = ()          # attributes of self.root holding trainable heads
    lora_root: str = ""                       # dotted path (from self.root) of the module that gets LoRA

    def __init__(self):
        self.root = None                      # the torch module owning the family's model
        self.device = "cpu"
        self.autocast = None                  # torch dtype or None

    # -- capability -------------------------------------------------------------------------------------------------
    def recipe(self, spec, dev, n_train_questions: int) -> Recipe:
        raise NotImplementedError

    def supports(self, spec, ex: Example) -> str | None:
        """None if the model can take this example, else a plain reason."""
        for q in ex.qs:
            if q.type not in spec.types:
                return f"{spec.name} does not answer {q.type} questions"
            if len(q.keys) > spec.max_options:
                return f"{spec.name} takes at most {spec.max_options} options"
        for m in ex.request.media:
            if m.type not in spec.modalities:
                return f"{spec.name} cannot read {m.type}"
        return None

    def estimate_gb(self, spec, recipe: Recipe) -> float:
        """Peak memory for training, used to refuse jobs that can't fit. Families override with measured numbers."""
        return spec.memory_gb * 1.6 + 2.0

    # -- model ------------------------------------------------------------------------------------------------------
    def load(self, spec, dev, recipe: Recipe, stage: Callable[[str], None]) -> None:
        raise NotImplementedError

    def units(self, ex: Example, train: bool, rng=None) -> list[Unit]:
        raise NotImplementedError

    def score(self, units: list[Unit]) -> list[list[Any]]:
        """Per unit, per question in unit.qi: a 1-D tensor of log-probabilities over q.keys (differentiable when
        gradients are enabled)."""
        raise NotImplementedError

    def enable_checkpointing(self) -> bool:
        """Turns on activation checkpointing in the family's transformer, when one example doesn't fit in memory.
        Finds the first Hugging Face model inside the family's root that supports it; a family can override this."""
        for m in self.root.modules():
            if getattr(m, "supports_gradient_checkpointing", False) and hasattr(m, "gradient_checkpointing_enable"):
                try:
                    m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    def train_mode(self, on: bool) -> None:
        self.root.train(on)

    def unload(self) -> None:
        """Drop every reference to the model so its memory can be freed."""
        for k in list(vars(self)):
            if k not in ("device", "autocast"):
                setattr(self, k, None)

    # -- trainable parameters ---------------------------------------------------------------------------------------
    def param_groups(self, recipe: Recipe) -> list[dict]:
        lora = [p for n, p in self.root.named_parameters() if p.requires_grad and "lora_" in n]
        head = [p for n, p in self.root.named_parameters() if p.requires_grad and "lora_" not in n]
        groups = []
        if lora:
            groups.append({"params": lora, "lr": recipe.lr, "weight_decay": recipe.weight_decay, "name": "lora"})
        if head:
            groups.append({"params": head, "lr": recipe.head_lr, "weight_decay": recipe.weight_decay, "name": "head"})
        return groups

    def trainable_state(self, on_device: bool = True) -> dict:
        """A copy of the trainable weights. Kept on the GPU by default: snapshots are taken at every improvement, and on
        the GB10 a large GPU-to-host copy under memory pressure has been seen to stall indefinitely (the GPU idle, the
        process waiting in the driver); the weights are small, a device copy is instant."""
        if on_device:
            return {n: p.detach().clone() for n, p in self.root.named_parameters() if p.requires_grad}
        return to_host({n: p for n, p in self.root.named_parameters() if p.requires_grad})

    def load_trainable_state(self, sd: dict) -> None:
        params = dict(self.root.named_parameters())
        import torch
        with torch.no_grad():
            for n, t in sd.items():
                params[n].copy_(t.to(params[n].device, params[n].dtype))

    # -- delta ------------------------------------------------------------------------------------------------------
    def export(self, out: Path, recipe: Recipe) -> None:
        from safetensors.torch import save_file
        out.mkdir(parents=True, exist_ok=True)
        lora_mod = _get(self.root, self.lora_root) if self.lora_root is not None else None
        if lora_mod is not None and recipe.method == "lora":
            sd = to_host({n: t for n, t in lora_mod.state_dict().items() if "lora_" in n})
            save_file(sd, str(out / "lora.safetensors"))
            (out / "lora.json").write_text(json.dumps({"root": self.lora_root, "r": recipe.lora_r, "alpha": recipe.lora_alpha,
                                                       "dropout": recipe.lora_dropout, "targets": recipe.lora_targets}))
        heads = {}
        for h in self.head_names:
            mod = _get(self.root, h)
            if not recipe.train_head and hasattr(mod, "parameters") and not any(p.requires_grad for p in mod.parameters()):
                continue                  # the head was kept frozen: the released weights are already in the base model
            for n, t in to_host(mod.state_dict()).items():
                heads[f"{h}.{n}"] = t
        if heads:
            save_file(heads, str(out / "head.safetensors"))


# ----------------------------------------------------------------------------------------------------------------
# Helpers


def _get(root, path: str):
    m = root
    for part in [p for p in path.split(".") if p]:
        m = getattr(m, part)
    return m


def to_host(tensors: dict) -> dict:
    """Copies tensors from the GPU to host memory through page-locked buffers. On the GB10, a plain `.cpu()` of the
    trained weights (into ordinary memory, which the system may be swapping) has twice hung indefinitely with the GPU
    idle; page-locked memory takes the DMA path and can't be paged out mid-copy."""
    import torch
    out, cuda = {}, False
    for n, t in tensors.items():
        t = t.detach()
        if t.device.type == "cuda":
            buf = torch.empty(t.shape, dtype=t.dtype, pin_memory=True)
            buf.copy_(t.contiguous(), non_blocking=True)
            out[n], cuda = buf, True
        else:
            out[n] = t.contiguous().cpu()
    if cuda:
        torch.cuda.synchronize()
    return out


def inject_lora(module, targets: list[str], r: int, alpha: int, dropout: float):
    """Adds LoRA layers to `module` in place (no wrapper class, so the module's own forward stays unchanged)."""
    from peft import LoraConfig, inject_adapter_in_model
    cfg = LoraConfig(r=r, lora_alpha=alpha, lora_dropout=dropout, target_modules=targets, bias="none")
    inject_adapter_in_model(cfg, module)
    n = 0
    for name, p in module.named_parameters():
        if "lora_" in name:
            # PEFT creates LoRA weights in the base layer's dtype (bf16 on bf16 bases). Keep them in fp32 for training
            # and serving alike: PEFT casts the input to the LoRA's dtype and the result back, so a bf16 base is fine,
            # and a fine-tune is never rounded to bf16 when it is loaded (parity with training).
            p.data = p.data.float()
            n += 1
    if n == 0:
        raise RuntimeError(f"LoRA found no layers named {targets} in {type(module).__name__}")
    return module


def freeze(module) -> None:
    for p in module.parameters():
        p.requires_grad_(False)


def unfreeze(module) -> None:
    for p in module.parameters():
        p.requires_grad_(True)


def attach_delta(root, delta: Path, device) -> dict:
    """Serving side: put a stored delta onto an already-loaded released model, unmerged (exact parity with training,
    docs/trainer/ARCHITECTURE.md Appendix A). Returns {"lora": n tensors, "head": n tensors}."""
    import torch
    from safetensors.torch import load_file
    delta = Path(delta)
    counts = {"lora": 0, "head": 0}
    if (delta / "lora.json").exists():
        cfg = json.loads((delta / "lora.json").read_text())
        mod = _get(root, cfg["root"])
        inject_lora(mod, cfg["targets"], cfg["r"], cfg["alpha"], 0.0)
        sd = load_file(str(delta / "lora.safetensors"), device=str(device))
        missing, unexpected = mod.load_state_dict(sd, strict=False)
        lora_missing = [k for k in missing if "lora_" in k]
        if lora_missing or unexpected:
            raise RuntimeError(f"The fine-tune does not match this model ({len(lora_missing)} missing, {len(unexpected)} unexpected LoRA tensors)")
        for name, p in mod.named_parameters():
            if "lora_" in name:
                p.data = p.data.to(device)
        counts["lora"] = len(sd)
    if (delta / "head.safetensors").exists():
        heads = load_file(str(delta / "head.safetensors"), device=str(device))
        by_head: dict[str, dict] = {}
        for k, t in heads.items():
            h, n = k.split(".", 1)
            by_head.setdefault(h, {})[n] = t
        for h, sd in by_head.items():
            m = _get(root, h)
            ref = next(iter(m.state_dict().values()), None)
            m.load_state_dict({k: v.to(ref.dtype if ref is not None and ref.is_floating_point() and v.is_floating_point() else v.dtype)
                               for k, v in sd.items()})
        counts["head"] = len(heads)
    root.eval()
    del torch
    return counts


@contextmanager
def autocast(device: str, dtype):
    import torch
    if dtype is None or device == "cpu":
        yield
    else:
        with torch.autocast(device_type=device, dtype=dtype):
            yield


def log_softmax_slice(logits, n: int):
    import torch
    return torch.log_softmax(logits[:n].float(), dim=-1)


def shuffled(n: int, rng) -> list[int]:
    p = list(range(n))
    rng.shuffle(p)
    return p


def unpermute(logp_shown, perm: list[int] | None):
    """logp_shown[j] is the log-prob of the option shown at position j = key perm[j]; return key order."""
    if perm is None:
        return logp_shown
    import torch
    inv = [0] * len(perm)
    for j, k in enumerate(perm):
        inv[k] = j
    return logp_shown[torch.tensor(inv, device=logp_shown.device)]


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 3.5))
