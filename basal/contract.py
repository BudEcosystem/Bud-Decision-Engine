"""The request/response contract every model speaks through Basal.

It is TypeSafe's public `POST /v1/systemone` shape, so clients written for Jev
(including the official `typesafe-sdk`) work unchanged:

    {"model": "...", "state": <text | object | array>,
     "questions": {"name": {"type": "choice" | "score" | "noul",
                            "instructions": "...", "criteria": ...}}}

Basal adds two optional fields: `media` (images, audio or video for the models
that can see or hear) and `settings` (per-request knobs such as a calibration
temperature).

Adapters never build answers themselves. They return, per question, a list of
probabilities in *key order* (choice: the criteria names; noul: ["false",
"true"]; score: "0".."L-1"). `build_answers` turns those into the response, so
every model reports confidence the same way.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

JSON = Union[str, int, float, bool, None, dict, list]
MAX_OPTIONS = 1000


class Noul(BaseModel):
    type: Literal["noul"]
    instructions: JSON = None
    criteria: dict[str, JSON] | None = None   # optional descriptions for "true" / "false"

    @field_validator("criteria")
    @classmethod
    def _tf(cls, v):
        if v is None: return v
        bad = set(v) - {"true", "false"}
        if bad: raise ValueError(f"noul criteria may only describe 'true' and 'false', got {sorted(bad)}")
        return v


class Choice(BaseModel):
    type: Literal["choice"]
    instructions: JSON = None
    criteria: dict[str, JSON]

    @model_validator(mode="before")
    @classmethod
    def _list_to_dict(cls, v):
        # TypeSafe also accepts a plain list of option names.
        if isinstance(v, dict) and isinstance(v.get("criteria"), list):
            v = {**v, "criteria": {str(x): None for x in v["criteria"]}}
        return v

    @model_validator(mode="after")
    def _check(self):
        if not 1 <= len(self.criteria) <= MAX_OPTIONS:
            raise ValueError(f"a choice needs 1 to {MAX_OPTIONS} options, got {len(self.criteria)}")
        return self


class Score(BaseModel):
    type: Literal["score"]
    instructions: JSON = None
    criteria: list[JSON] = Field(min_length=1, max_length=MAX_OPTIONS)


def _list_to_criteria(v):
    if isinstance(v, dict) and isinstance(v.get("criteria"), list):
        v = {**v, "criteria": {str(x): None for x in v["criteria"]}}
    return v


class Multi(BaseModel):
    """Pick all that apply. Studio extension: each option is judged as its own yes/no question."""
    type: Literal["multi"]
    instructions: JSON = None
    criteria: dict[str, JSON]
    threshold: float = Field(default=0.5, gt=0, lt=1)

    _lists = model_validator(mode="before")(classmethod(lambda cls, v: _list_to_criteria(v)))

    @model_validator(mode="after")
    def _check(self):
        if not 1 <= len(self.criteria) <= 64:
            raise ValueError(f"a pick-all-that-apply question needs 1 to 64 options, got {len(self.criteria)}")
        return self


class Rank(BaseModel):
    """Rank the options. Studio extension: a choice whose options come back ordered by probability."""
    type: Literal["rank"]
    instructions: JSON = None
    criteria: dict[str, JSON]

    _lists = model_validator(mode="before")(classmethod(lambda cls, v: _list_to_criteria(v)))

    @model_validator(mode="after")
    def _check(self):
        if not 2 <= len(self.criteria) <= MAX_OPTIONS:
            raise ValueError(f"a rank question needs 2 to {MAX_OPTIONS} options, got {len(self.criteria)}")
        return self


class Number(BaseModel):
    """Estimate a number. Studio extension: a scale over numeric values; returns the expected value and a likely range.
    criteria: a list of numbers, or {"<number>": "optional description"}."""
    type: Literal["number"]
    instructions: JSON = None
    criteria: dict[str, JSON] | list[float]
    unit: str | None = None

    @model_validator(mode="after")
    def _check(self):
        keys = list(self.criteria) if isinstance(self.criteria, dict) else self.criteria
        try:
            vals = [float(k) for k in keys]
        except (TypeError, ValueError):
            raise ValueError("a number question's options must all be numbers")
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("a number question's options must be finite numbers")
        if not 2 <= len(set(vals)) <= 64:
            raise ValueError(f"a number question needs 2 to 64 distinct values, got {len(set(vals))}")
        return self

    def points(self) -> list[tuple[float, str | None]]:
        if isinstance(self.criteria, dict):
            items = [(float(k), render(v) or None) for k, v in self.criteria.items()]
        else:
            items = [(float(k), None) for k in self.criteria]
        seen, out = set(), []
        for v, d in sorted(items):
            if v not in seen:
                seen.add(v); out.append((v, d))
        return out


Question = Union[Noul, Choice, Score, Multi, Rank, Number]
PRIMITIVE_TYPES = ("choice", "score", "noul")
EXTENSION_TYPES = ("multi", "rank", "number")


class Media(BaseModel):
    type: Literal["image", "audio", "video"]
    data: str | None = None    # data: URL (base64)
    path: str | None = None    # local file path (server-side), or an id returned by /api/uploads
    name: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if not (self.data or self.path): raise ValueError("media needs `data` (a data: URL) or `path`")
        return self


class Settings(BaseModel):
    temperature: float | None = Field(default=None, gt=0, le=20,
                                      description="Calibration temperature applied on top of the model's own. >1 softens, <1 sharpens.")


class SystemOneRequest(BaseModel):
    model: str | None = None
    state: JSON
    questions: dict[str, Question] = Field(min_length=1)
    media: list[Media] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)

    model_config = {"extra": "ignore"}


# --------------------------------------------------------------------------------------------------------------------
# Rendering helpers shared by adapters


def render(v: JSON, indent: int = 0) -> str:
    """Flatten text / object / array state into readable text. Field names are kept as labels."""
    pad = "  " * indent
    if v is None: return ""
    if isinstance(v, (str, int, float, bool)): return str(v)
    if isinstance(v, list): return "\n".join(f"{pad}- {render(x, indent + 1).lstrip()}" for x in v)
    return "\n".join(f"{pad}{k}:\n{render(x, indent + 1)}" if isinstance(x, (dict, list)) else f"{pad}{k}: {render(x)}"
                     for k, x in v.items())


@dataclass
class Q:
    """One question, normalised for adapters."""
    id: str
    type: str
    instructions: str
    keys: list[str]                 # probability keys, in order
    labels: list[str]               # short option names (choice names, "no"/"yes", level text)
    descriptions: list[str | None]  # option descriptions where given
    raw: dict = field(default_factory=dict)   # the question as a primitive TypeSafe dict
    parent: str | None = None       # for questions expanded from a studio extension type: the client's question id
    role: str | None = None         # "multi" | "rank" | "number" when expanded
    meta: dict = field(default_factory=dict)

    @property
    def display_id(self) -> str:
        return self.parent or self.id

    def option_texts(self) -> list[str]:
        """'name: description' when a description exists, else the name."""
        return [f"{l}: {d}" if d else l for l, d in zip(self.labels, self.descriptions)]


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


# TypeSafe makes `instructions` optional (the criteria can carry the meaning), but several models' own libraries reject
# an empty instruction. A neutral default keeps every question answerable on every model.
DEFAULT_INSTRUCTIONS = {"choice": "Which option best fits?", "score": "Where does this fall on the scale?",
                        "noul": "Is this true?", "multi": "Which of these apply?", "rank": "Which option fits best?",
                        "number": "Which value is most likely?"}


def normalise(req: SystemOneRequest) -> list[Q]:
    """Primitive questions (choice / score / noul) for the adapters. Studio extension types expand here and are
    recomposed by build_answers."""
    out = []
    n_child = 0
    for qid, q in req.questions.items():
        raw = q.model_dump()
        instr = render(q.instructions).strip() or DEFAULT_INSTRUCTIONS[q.type]
        if not render(q.instructions).strip():
            raw["instructions"] = instr
            q = q.model_copy(update={"instructions": instr})
        if q.type == "multi":
            for name, desc in q.criteria.items():
                d = render(desc) or None
                child_instr = f'{instr}\nDoes "{name}" apply?' + (f" ({d})" if d else "")
                cid = f"__m{n_child}"; n_child += 1
                out.append(Q(cid, "noul", child_instr, ["false", "true"], ["no", "yes"], [None, d],
                             {"type": "noul", "instructions": child_instr}, parent=qid, role="multi",
                             meta={"option": name, "threshold": q.threshold}))
            continue
        if q.type == "rank":
            keys = list(q.criteria)
            descs = [render(v) or None for v in q.criteria.values()]
            out.append(Q(qid, "choice", instr, keys, keys, descs,
                         {"type": "choice", "instructions": q.instructions, "criteria": q.criteria}, parent=qid, role="rank"))
            continue
        if q.type == "number":
            pts = q.points()
            unit = f" {q.unit}" if q.unit else ""
            labels = [f"{_fmt_num(v)}{unit}" + (f": {d}" if d else "") for v, d in pts]
            out.append(Q(qid, "score", instr, [str(i) for i in range(len(pts))], labels, [None] * len(pts),
                         {"type": "score", "instructions": q.instructions, "criteria": labels}, parent=qid, role="number",
                         meta={"values": [v for v, _ in pts], "unit": q.unit}))
            continue
        if q.type == "choice":
            keys = list(q.criteria)
            labels = keys
            descs = [render(v) or None for v in q.criteria.values()]
        elif q.type == "noul":
            keys = ["false", "true"]
            labels = ["no", "yes"]
            c = q.criteria or {}
            descs = [render(c.get("false")) or None, render(c.get("true")) or None]
        else:
            keys = [str(i) for i in range(len(q.criteria))]
            labels = [render(x) for x in q.criteria]
            descs = [None] * len(labels)
        out.append(Q(qid, q.type, instr, keys, labels, descs, raw))
    return out


def typesafe_questions(qs: list[Q]) -> dict:
    """Back to the plain TypeSafe dict (for adapters whose libraries speak it natively)."""
    return {q.id: {k: v for k, v in q.raw.items() if v is not None and k in ("type", "instructions", "criteria")}
            for q in qs}


# --------------------------------------------------------------------------------------------------------------------
# Answers


def _normalise_probs(p: list[float]) -> list[float]:
    p = [max(0.0, float(x)) if math.isfinite(float(x)) else 0.0 for x in p]
    t = sum(p)
    return [1 / len(p)] * len(p) if t <= 0 else [x / t for x in p]


def apply_temperature(p: list[float], t: float | None) -> list[float]:
    """p' ∝ p^(1/T). Never changes which option wins; only how sure the model sounds."""
    if not t or t == 1: return p
    logs = [math.log(max(x, 1e-12)) / t for x in p]
    m = max(logs)
    w = [math.exp(x - m) for x in logs]
    s = sum(w)
    return [x / s for x in w]


def choice_confidence(p: list[float]) -> float:
    """TypeSafe's reference formula: (p_max - 1/K) / (1 - 1/K). 0 = no better than guessing, 1 = certain."""
    k = len(p)
    return 1.0 if k == 1 else max(0.0, (max(p) - 1 / k) / (1 - 1 / k))


def score_confidence(p: list[float]) -> float:
    """1 - E|level - mode| / D, where D is that spread for a uniform distribution. 1 = all mass on one level."""
    n = len(p)
    if n == 1: return 1.0
    mode = max(range(n), key=p.__getitem__)
    d = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return max(0.0, 1.0 - sum(pi * abs(i - mode) for i, pi in enumerate(p)) / d)


def r4(x: float) -> float:
    return round(float(x), 4)


def _num(v: float):
    return int(v) if float(v).is_integer() else v


def _central_range(values: list[float], p: list[float], mass: float = 0.8) -> list[float]:
    """Smallest and largest value of the central `mass` of the distribution (values in ascending order)."""
    lo_cut, hi_cut = (1 - mass) / 2, 1 - (1 - mass) / 2
    acc, lo, hi = 0.0, values[0], values[-1]
    for v, x in zip(values, p):
        if acc + x > lo_cut:
            lo = v; break
        acc += x
    acc = 0.0
    for v, x in zip(values, p):
        acc += x
        if acc >= hi_cut:
            hi = v; break
    return [lo, hi]


def build_answers(qs: list[Q], probs: list[list[float]], temperature: float | None = None) -> dict[str, Any]:
    if len(probs) != len(qs):
        raise ValueError(f"model returned {len(probs)} answers for {len(qs)} questions")
    prim = _primitive_answers(qs, probs, temperature)
    out: dict[str, Any] = {}
    for q in qs:
        a = prim[q.id]
        if q.role is None:
            out[q.id] = a
        elif q.role == "multi":
            m = out.setdefault(q.parent, {"type": "multi", "selected": [], "probabilities": {},
                                          "threshold": q.meta["threshold"], "decision": [], "top_probability": 0.0})
            py = a["noul"]
            m["probabilities"][q.meta["option"]] = py
            if py >= q.meta["threshold"]:
                m["selected"].append(q.meta["option"])
            m["decision"] = list(m["selected"])
            m["top_probability"] = r4(max(m["top_probability"], max(py, 1 - py)))
        elif q.role == "rank":
            dist = a["probabilities"]
            order = sorted(dist, key=lambda k: -dist[k])
            out[q.parent] = {"type": "rank", "ranking": order, "probabilities": dist, "choice": order[0],
                             "decision": order[0], "confidence": a["confidence"], "top_probability": a["top_probability"]}
        elif q.role == "number":
            vals = q.meta["values"]
            p = [a["probabilities"][k] for k in q.keys]
            est = sum(v * x for v, x in zip(vals, p))
            mode = _num(vals[max(range(len(p)), key=p.__getitem__)])
            out[q.parent] = {"type": "number", "estimate": r4(est), "most_likely": mode,
                             "range": [_num(x) for x in _central_range(vals, p)], "unit": q.meta["unit"],
                             "probabilities": {_fmt_num(v): x for v, x in zip(vals, p)},
                             "decision": mode, "top_probability": a["top_probability"],
                             "confidence": a.get("confidence")}
    return out


def _primitive_answers(qs: list[Q], probs: list[list[float]], temperature: float | None) -> dict[str, Any]:
    out = {}
    for q, p in zip(qs, probs):
        if len(p) != len(q.keys):
            raise ValueError(f"question '{q.display_id}': model returned {len(p)} probabilities for {len(q.keys)} options")
        p = apply_temperature(_normalise_probs(p), temperature)
        top = max(range(len(p)), key=p.__getitem__)
        dist = {k: r4(v) for k, v in zip(q.keys, p)}
        if q.type == "noul":
            a = {"type": "noul", "noul": r4(p[1]), "probabilities": dist,
                 "decision": "yes" if p[1] >= 0.5 else "no", "top_probability": r4(max(p))}
        elif q.type == "choice":
            a = {"type": "choice", "choice": q.keys[top], "probabilities": dist, "confidence": r4(choice_confidence(p)),
                 "decision": q.keys[top], "top_probability": r4(p[top])}
        else:
            a = {"type": "score", "score": r4(sum(i * x for i, x in enumerate(p))),
                 "legend": dict(zip(q.keys, q.labels)), "probabilities": dist, "confidence": r4(score_confidence(p)),
                 "decision": q.keys[top], "top_probability": r4(p[top])}
        out[q.id] = a
    return out


def approx_tokens(req: SystemOneRequest) -> int:
    """Rough input size for usage reporting when a model doesn't count tokens itself (≈ 4 characters per token)."""
    text = render(req.state) + json.dumps({k: q.model_dump() for k, q in req.questions.items()})
    return max(1, len(text) // 4)
