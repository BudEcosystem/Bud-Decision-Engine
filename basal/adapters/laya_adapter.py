"""Laya family (ConvAI Innovations): ModernBERT / mmBERT encoder + option-marker decision head.

Driven through the official `laya` package. All questions go through one
forward pass. Laya also reports an act-or-escalate probability per question
(its own learned view of whether acting automatically is safe), which we pass
through as a model extra.
"""
from __future__ import annotations

from ..contract import typesafe_questions
from .base import NOUL_ALIASES, Adapter, DecideInput, DecideOutput


def laya_state(request, state_text: str):
    """The state as Laya reads it: text, objects and lists pass through (Laya writes objects as JSON), anything else
    as the rendered text. Shared by serving (decide) and training (basal/training/families/laya.py)."""
    return request.state if isinstance(request.state, (str, dict, list)) else state_text


def laya_questions(qs) -> dict:
    """Studio questions -> the TypeSafe question dicts Laya reads. Shared by serving and training."""
    questions = typesafe_questions(qs)
    for q in questions.values():
        q.setdefault("instructions", "")
    return questions


# The check an Intel GPU's 16-bit precision has to pass when Laya loads: a short request with every question type and
# a long one (ModernBERT's sliding-window layers only differ from its global ones past 128 tokens).
INTEL_CHECK = (
    ("Hi, I was charged twice for my March subscription. Please refund one of the payments.",
     {"team": {"type": "choice", "instructions": "Which team should handle this?",
               "criteria": {"billing": "charges and refunds", "technical": "bugs and outages", "sales": "new purchases"}},
      "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
      "urgency": {"type": "score", "instructions": "How urgent is this?", "criteria": ["low", "medium", "high"]}}),
    ("The parcel left the warehouse on Monday and tracking stopped updating two days later. " * 14,
     {"late": {"type": "noul", "instructions": "Is the delivery late?"},
      "next": {"type": "choice", "instructions": "What should support do next?",
               "criteria": {"trace": "ask the carrier to trace the parcel", "refund": "refund the order", "wait": "do nothing yet"}}}),
)
INTEL_TOLERANCE = 0.05      # fp16 moved Laya's probabilities by under 0.01 on 473 questions per model (NVIDIA); wrong maths moves them by far more


def _check_answers(agent) -> list[float]:
    """Every probability Laya gives for INTEL_CHECK, in a fixed order."""
    out = []
    for state, questions in INTEL_CHECK:
        for a in agent.predict(state, questions)["answers"].values():
            out += [float(v) for v in a["probabilities"].values()] if "probabilities" in a else [float(a["noul"])]
    return out


def choose_precision(agent, report=lambda text: None) -> str:
    """Try 16-bit mixed precision (fp16) and keep it only if it works here; otherwise full precision. -> "fp16" | "fp32"

    "Works" means: the check requests run without an error, every probability is a finite number, and none is further
    than INTEL_TOLERANCE from the same request in full precision, which is run first as the reference. Whatever the
    outcome, the agent is left in the precision returned.

    fp16 and not the laya runtime's bf16: fp16 is native on every Intel GPU the installer accepts, while bf16 is only
    accelerated on chips with matrix engines and emulated on the rest (Core Ultra series 1); and on the three Laya
    models fp16 stays within 0.009 of full precision where bf16 drifts by up to 0.077. It is what the runtime itself
    uses on Apple GPUs and on older NVIDIA cards."""
    import math

    import torch
    agent.amp_enabled, agent.dtype = False, torch.float32
    try:
        full = _check_answers(agent)
    except Exception as e:  # noqa: BLE001 — full precision itself fails: nothing to compare with; the warm-up will say why
        report(f"The precision check could not run in full precision ({type(e).__name__}: {e})")
        return "fp32"
    why = ""
    try:
        agent.amp_enabled, agent.dtype = True, torch.float16
        half = _check_answers(agent)
        if not all(math.isfinite(v) for v in half):
            why = "it gave answers that are not numbers"
        else:
            worst = max(abs(x - y) for x, y in zip(half, full))
            if worst > INTEL_TOLERANCE:
                why = f"its answers were up to {worst:.2f} away from full precision"
    except Exception as e:  # noqa: BLE001 — an operation this GPU's software does not handle in 16 bits
        why = f"{type(e).__name__}: {e}"
    if why:
        agent.amp_enabled, agent.dtype = False, torch.float32
        report(f"16-bit precision does not work on this GPU ({why[:200]}); using full precision")
        return "fp32"
    report("16-bit precision checked against full precision on this GPU; using it")
    return "fp16"


def precision_on_intel(agent, report=lambda text: None) -> str | None:
    """Laya's precision on an Intel GPU ("xpu"): 16-bit if it passes `choose_precision`, else full. -> None elsewhere.

    The laya runtime turns on bf16 mixed precision there, a path it cannot have run: with PyTorch 2.11 it stops at
    the first request ("expected scalar type BFloat16 but found Float", see adapters.base.plain_attention), and its
    own retry in full precision covers Apple GPUs and the processor but not Intel's. With the fast path off the
    ordinary code handles mixed precision, so 16 bits can be used where it proves itself. NVIDIA, Apple and processor
    runs keep the runtime's own choice."""
    if getattr(getattr(agent, "device", None), "type", None) != "xpu":
        return None
    return choose_precision(agent, report)


class LayaAdapter(Adapter):
    def load(self):
        import laya  # type: ignore
        self.stage("Loading encoder and decision head", 0.3)
        path = self.snapshot(self.spec.repo.id)
        self.agent = laya.load(path, device=self.device)
        self.precision = precision_on_intel(self.agent, lambda text: self.stage(text, 0.8))
        self.max_len = int(self.options.get("max_length") or self.spec.context_tokens)

    def effective_device(self):
        # laya.load moves the model to the CPU by itself when the GPU runs out of memory while loading.
        dev = getattr(self.agent, "device", None)
        return getattr(dev, "type", None)

    def predict(self, state, questions) -> tuple[dict, list[str]]:
        """One request through the laya runtime. On an Intel GPU in 16-bit precision, a request that fails is asked
        again in full precision, and the model stays there: the check at load covers two requests, not every shape."""
        def run():
            try:
                return self.agent.predict(state, questions, max_len=self.max_len)
            except TypeError:   # older runtimes without max_len
                return self.agent.predict(state, questions)
        try:
            return run(), []
        except Exception as e:  # noqa: BLE001
            if getattr(self, "precision", None) != "fp16" or "out of memory" in str(e).lower():
                raise
            import torch
            self.agent.amp_enabled, self.agent.dtype, self.precision = False, torch.float32, "fp32"
            print(f"16-bit precision failed on a request ({type(e).__name__}: {str(e)[:300]}); using full precision "
                  "from now on", flush=True)       # the worker's log
            return run(), ["This model switched to full precision: a request failed in 16-bit precision on this GPU."]

    def decide(self, x: DecideInput) -> DecideOutput:
        questions = laya_questions(x.questions)
        state = laya_state(x.request, x.state_text)
        res, notes = self.predict(state, questions)
        probs, extras = [], {}
        for q in x.questions:
            a = res["answers"][q.id]
            if q.type == "noul":
                if "probabilities" in a:
                    probs.append(self.probs_from_map(q, a["probabilities"], NOUL_ALIASES))
                else:
                    p = float(a["noul"])
                    probs.append([1 - p, p])
            else:
                probs.append(self.probs_from_map(q, a["probabilities"]))
            ext = a.get("action") or a.get("rl_agent") or a.get("laya") or {}
            if "act_probability" in ext:
                extras[q.id] = {"act_probability": round(float(ext["act_probability"]), 4),
                                "act_probability_help": "Laya's built-in gate: how safe it thinks acting on this answer automatically is."}
        tokens = (res.get("usage") or {}).get("input_tokens")
        return DecideOutput(probs, input_tokens=tokens, extras=extras, notes=notes)


ADAPTER = LayaAdapter
