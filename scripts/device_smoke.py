"""Loads a real model the way the studio's worker does, on this computer's default device, and asks it one request.

    python scripts/device_smoke.py [model id ...]      # default: laya; downloads the model if it is missing

Made for CI on Windows, macOS and Linux runners (the processor) and for trying a GPU by hand: it prints where the model
ran, any warning the studio would show, and the answers, and exits 1 if the load failed or the answers are not
probabilities. For the three Laya models it also shows the cause of the first Windows bug report as far as a computer
without an Intel GPU can: under mixed precision off NVIDIA, PyTorch sends the decision head through its native fast
path, and after `plain_attention` it does not (basal/adapters/base.py).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BODY = {"state": "Hi, I was charged twice for my March subscription. Please refund one of the payments.",
        "questions": {"team": {"type": "choice", "instructions": "Which team should handle this?",
                               "criteria": {"billing": "charges and refunds", "technical": "bugs", "sales": "purchases"}},
                      "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"}}}


def download(spec) -> None:
    from huggingface_hub import snapshot_download
    os.environ["HF_HUB_OFFLINE"] = "0"
    for repo in spec.repos():
        snapshot_download(repo.id, allow_patterns=list(repo.include) or None, ignore_patterns=list(repo.exclude) or None)
    os.environ["HF_HUB_OFFLINE"] = "1"


def ask(adapter) -> dict:
    from basal.adapters.base import DecideInput
    from basal.contract import SystemOneRequest, build_answers, normalise, render
    req = SystemOneRequest.model_validate(BODY)
    qs = normalise(req)
    out = adapter.decide(DecideInput(req, qs, render(req.state), []))
    for q, p in zip(qs, out.probs):
        assert len(p) == len(q.keys) and abs(sum(p) - 1) < 1e-3 and all(0 <= v <= 1 for v in p), (q.id, p)
    return build_answers(qs, out.probs, None)


def laya_mixed_precision(agent) -> bool:
    """With the real model: does the head take the fast path inside mixed precision, and does the rule stop it?"""
    import torch
    from basal.adapters.base import plain_attention
    if agent.device.type != "cpu":
        return True
    calls = []
    real = torch._transformer_encoder_layer_fwd
    torch._transformer_encoder_layer_fwd = lambda *a, **k: calls.append(1) or real(*a, **k)
    try:
        state, qs = BODY["state"], BODY["questions"]
        full = agent.predict(state, qs)["answers"]["refund"]["noul"]
        agent.amp_enabled, agent.dtype = True, torch.bfloat16        # the laya runtime's setting on an Intel GPU
        calls.clear()
        agent.predict(state, qs)
        inside = len(calls)
        plain_attention("xpu")
        calls.clear()
        mixed = agent.predict(state, qs)["answers"]["refund"]["noul"]
        after = len(calls)
    finally:
        torch._transformer_encoder_layer_fwd = real
        torch.backends.mha.set_fastpath_enabled(True)
        agent.amp_enabled, agent.dtype = False, torch.float32
    print(f"  mixed precision off NVIDIA: the head took the fast path {inside} time(s) before the rule and {after} after; "
          f"answer {mixed:.4f} against {full:.4f} in full precision")
    return inside > 0 and after == 0 and abs(mixed - full) < 0.02


def main() -> int:
    import torch
    from basal import worker
    from basal.catalog import BY_ID
    print(f"PyTorch {torch.__version__} on {sys.platform}; devices: cuda={torch.cuda.is_available()}, "
          f"xpu={hasattr(torch, 'xpu') and torch.xpu.is_available()}, mps={torch.backends.mps.is_available()}")
    ok = True
    for model_id in sys.argv[1:] or ["laya"]:
        spec = BY_ID[model_id]
        download(spec)
        options = {o["key"]: o["default"] for o in spec.options()}
        worker.S = worker.State()
        worker.load_model(model_id, options)
        s = worker.S
        if s.status != "ready":
            print(f"{spec.name}: FAILED to load on {options.get('device')}: {s.error}\n{s.detail}")
            ok = False
            continue
        answers = ask(s.adapter)
        short = {k: v.get("choice", v.get("noul")) for k, v in answers.items()}
        print(f"{spec.name}: loaded on {s.adapter.effective_device() or s.adapter.device}; answers {short}"
              + (f"; warning: {s.warning}" if s.warning else ""))
        if spec.adapter == "laya":
            ok = laya_mixed_precision(s.adapter.agent) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
