"""CLM 8B (Contrastive LM): projection heads on a frozen Qwen3-8B encoder.

The official engine expects Qwen3-8B embeddings from a separate vLLM server. We
run the encoder in-process instead with the same recipe (last-token pooling,
L2-normalised, left-truncated to 2,048 tokens) and hand it to the engine as its
embedder. Option texts are embedded separately from the state, and cached, so
long candidate lists stay cheap.

Every text is embedded alone, in its own forward pass, so its embedding depends on nothing but the text. Batching
made answers depend on the company a text kept: with left padding (the previous behaviour) padding shifts every real
token's position and changes the attention kernels, and in bf16 that moved the same request's answers by up to
0.04-0.06 depending on which option texts happened to be cached already (typed-decisions, GB10). Even unpadded
batches of equal-length texts still differ from one text alone by up to 0.039 (the GPU's matrix kernels depend on the
batch shape; a CLM fine-tune failed its serving parity check by that much). Each text also sees what it sees in the
publisher's vLLM embedder: no padding, positions from 0. Training (basal/training/families/clm.py) embeds with this
same class, so a fine-tune learns exactly what is served.
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

    def __init__(self, model_dir: str, device: str, dtype, batch: int = 1, cache_size: int = 50_000):
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
        """Embeddings of `texts`, never padded. `batch` > 1 lets texts of exactly equal token length share a forward pass
        (exact on the CPU; on a GPU the result then depends slightly on the batch, so serving and training use 1)."""
        torch = self.torch
        ids = self.tok(texts, truncation=True, max_length=MAX_TOKENS, add_special_tokens=False)["input_ids"]
        ids = [x if x else [self.tok.pad_token_id] for x in ids]
        same: dict[int, list[int]] = {}
        for i, x in enumerate(ids):
            same.setdefault(len(x), []).append(i)
        out = np.empty((len(texts), self.model.config.hidden_size), dtype=np.float32)
        with torch.inference_mode():
            for idx in same.values():
                for s in range(0, len(idx), self.batch):
                    chunk = idx[s:s + self.batch]
                    inp = torch.tensor([ids[i] for i in chunk], device=self.device)
                    h = self.model(input_ids=inp, attention_mask=torch.ones_like(inp)).last_hidden_state[:, -1].float()
                    out[chunk] = torch.nn.functional.normalize(h, dim=-1).cpu().numpy()
        return out, sum(len(x) for x in ids)

    def embed(self, texts: list[str]):
        vecs, todo = {}, []
        with self.lock:
            for t in dict.fromkeys(texts):
                if t in self.cache:
                    self.cache.move_to_end(t); vecs[t] = self.cache[t]
                else:
                    todo.append(t)
        tokens = 0
        if todo:
            got, tokens = self._encode(todo)
            with self.lock:
                for t, v in zip(todo, got):
                    vecs[t] = v; self.cache[t] = v
                while len(self.cache) > self.cache_size:
                    self.cache.popitem(last=False)
        return np.stack([vecs[t] for t in texts]), tokens


HEAD_NAME = "clm-latest"      # clm.engine.DEFAULT_MODEL: the released heads the studio answers with


def clm_questions(qs) -> dict:
    """The TypeSafe questions the CLM engine reads. Shared by serving (decide) and training
    (basal/training/families/clm.py)."""
    questions = typesafe_questions(qs)
    for q in questions.values():
        q.setdefault("instructions", "")
    return questions


def clm_pairs(request, qs) -> dict:
    """{question id: (state text, option keys, option texts)}: exactly the texts the engine embeds for a request
    (clm.schema.build_pairs, which Engine.answer calls on the same arguments)."""
    from clm.schema import build_pairs  # type: ignore
    return build_pairs(request.state, clm_questions(qs))


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
        questions = clm_questions(x.questions)
        res = self.engine.answer(x.request.state, questions, model=HEAD_NAME)
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
