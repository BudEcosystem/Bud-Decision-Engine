"""CLM 8B (Contrastive LM): projection heads on a frozen Qwen3-8B encoder.

The official engine expects Qwen3-8B embeddings from a separate vLLM server. We
run the encoder in-process instead with the same recipe (last-token pooling,
L2-normalised, left-truncated to 2,048 tokens) and hand it to the engine as its
embedder. Option texts are embedded separately from the state, and cached, so
long candidate lists stay cheap.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np

from ..contract import typesafe_questions
from .base import Adapter, DecideInput, DecideOutput

MAX_TOKENS = 2048


class LocalQwenEmbedder:
    """Drop-in for clm.embedder.Embedder: .embed(texts) -> (L2-normalised [n, 4096] array, tokens spent)."""

    def __init__(self, model_dir: str, device: str, dtype, batch: int = 16, cache_size: int = 50_000):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        self.tok.padding_side = "left"
        self.tok.truncation_side = "left"
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        # Load straight onto the GPU: loading 16 GB on the CPU first and then copying doubles the peak on unified memory.
        if str(device).startswith("cuda"):
            self.model = AutoModel.from_pretrained(model_dir, dtype=dtype, device_map=device).eval()
        else:
            self.model = AutoModel.from_pretrained(model_dir, dtype=dtype).to(device).eval()
        self.device, self.batch = device, batch
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.cache_size = cache_size
        self.lock = threading.Lock()

    def _encode(self, texts: list[str]) -> tuple[np.ndarray, int]:
        torch = self.torch
        enc = self.tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=MAX_TOKENS,
                       add_special_tokens=False).to(self.device)
        with torch.inference_mode():
            out = self.model(**enc).last_hidden_state[:, -1].float()   # left padding: last position is the last real token
        v = torch.nn.functional.normalize(out, dim=-1).cpu().numpy()
        return v, int(enc["attention_mask"].sum())

    def embed(self, texts: list[str]):
        vecs, todo = {}, []
        with self.lock:
            for t in dict.fromkeys(texts):
                if t in self.cache:
                    self.cache.move_to_end(t); vecs[t] = self.cache[t]
                else:
                    todo.append(t)
        tokens = 0
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            got, tk = self._encode(chunk)
            tokens += tk
            with self.lock:
                for t, v in zip(chunk, got):
                    vecs[t] = v; self.cache[t] = v
                while len(self.cache) > self.cache_size:
                    self.cache.popitem(last=False)
        return np.stack([vecs[t] for t in texts]), tokens


class ClmAdapter(Adapter):
    def load(self):
        from clm.engine import Engine  # type: ignore
        base = self.snapshot(self.spec.base.id)
        heads = self.snapshot(self.spec.repo.id)
        self.stage("Loading the Qwen3-8B encoder (16 GB)", 0.2)
        embedder = LocalQwenEmbedder(base, self.device, self.torch_dtype())
        self.stage("Loading the contrastive projection heads", 0.85)
        self.engine = Engine(embedder=embedder, checkpoint=f"{heads}/CLM_v0.1-8B.pt", device=self.device, action_cache="0")

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = typesafe_questions(x.questions)
        for q in questions.values():
            q.setdefault("instructions", "")
        res = self.engine.answer(x.request.state, questions)
        probs = []
        for q in x.questions:
            a = res["answers"][q.id]
            if q.type == "noul":
                p = float(a["noul"])
                probs.append([1 - p, p])
            else:
                probs.append(self.probs_from_map(q, {str(k): float(v) for k, v in a["probabilities"].items()}))
        tokens = (res.get("usage") or {}).get("input_tokens")
        return DecideOutput(probs, input_tokens=tokens or None)


ADAPTER = ClmAdapter
