"""Wire formats: the same decision served in each published API's exact shape.

    typesafe    POST /v1/systemone, GET /v1/models              TypeSafe's Jev API (docs.typesafe.ai, api.typesafe.ai/openapi.json)
    openrouter  POST /api/v1/systemone, POST /api/alpha/decisions  OpenRouter's System One / "Decisions API" (a superset of TypeSafe's)
    vercel      POST /typesafe/v1/systemone                       Vercel AI Gateway's TypeSafe-compatible route
    evaluate    POST /v1/evaluate                                  Vercel AI Gateway's evaluation API ("boolean" questions)

Rules (from docs/external/SPEC_SUMMARY.md):
* TypeSafe answers carry exactly: noul {type, noul}; choice {type, choice, probabilities, confidence};
  score {type, score, legend, probabilities, confidence}; legend echoes the request's criteria values keyed "0".."n-1".
* The envelope is {model, answers, usage: {input_tokens, output_tokens}}; the request id travels only in the
  x-typesafe-request-id header ("req_" + 32 hex), on every response.
* Validation errors: TypeSafe 422 {"detail": [{type, loc, msg, input, ctx?}]}; OpenRouter {"error": {code, message}};
  Vercel {"message", "error_type"}.
* Studio extensions (the multi/rank/number types, media, settings, and extra answer fields) are accepted everywhere,
  but extra answer fields are only sent when the client opts in with the header `X-Basal-Extensions: 1`.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError

from .contract import JSON, Choice, Media, Multi, Noul, Number, Rank, Score

EXT_HEADER = "x-basal-extensions"
PROVIDER = "Bud Decision Studio"


# ------------------------------------------------------------------------------------------------------------------
# Request schema with TypeSafe's validation behaviour: state may not be null, model is required, the question union is
# discriminated by `type` (so error locations read ["body", "questions", "<key>", "<type>", "<field>"]).

class Boolean(BaseModel):
    """Vercel /v1/evaluate spelling of a yes/no question."""
    type: Literal["boolean"]
    instructions: JSON = None
    criteria: dict[str, JSON] | None = None


AnyQuestion = Annotated[Union[Noul, Choice, Score, Multi, Rank, Number], Field(discriminator="type")]
EvalQuestion = Annotated[Union[Boolean, Choice, Score, Multi, Rank, Number], Field(discriminator="type")]


class WireSettings(BaseModel):
    """Only the temperature: the wire routes validate exactly as they always have. Studio-only settings such as
    act_threshold are read leniently elsewhere (decisions.wire_extensions) and never cause a 422."""
    temperature: float | None = Field(default=None, gt=0, le=20)
    model_config = {"extra": "ignore"}


class WireRequest(BaseModel):
    state: Union[str, dict, list]
    model: str
    questions: dict[str, AnyQuestion] = Field(min_length=1)
    media: list[Media] = Field(default_factory=list)
    settings: WireSettings = Field(default_factory=WireSettings)
    model_config = {"extra": "ignore"}


class EvaluateRequest(WireRequest):
    questions: dict[str, EvalQuestion] = Field(min_length=1)


def parse(body: Any, fmt: str) -> tuple[dict | None, list[dict] | None]:
    """-> (normalised TypeSafe-shaped body, None) or (None, pydantic error list with TypeSafe-style body locations)."""
    cls = EvaluateRequest if fmt == "evaluate" else WireRequest
    try:
        req = cls.model_validate(body)
    except ValidationError as e:
        errs = []
        for x in e.errors(include_url=False):
            item = {"type": x["type"], "loc": ["body", *x["loc"]], "msg": x["msg"], "input": x.get("input")}
            if x.get("ctx"):
                item["ctx"] = {k: (str(v) if isinstance(v, Exception) else v) for k, v in x["ctx"].items()}
            errs.append(item)
        return None, errs
    out = req.model_dump(exclude_none=False)
    if fmt == "evaluate":   # boolean -> noul for the runtime
        for q in out["questions"].values():
            if q["type"] == "boolean":
                q["type"] = "noul"
    # model_dump turns the rank/multi list forms into dicts already; drop empty studio extras
    if not out.get("media"):
        out.pop("media", None)
    return out, None


def error_body(fmt: str, status: int, message: str | list) -> dict:
    """Each route's own error shape. Also used for the studio's added failure modes on these routes (403
    cross_site_request, 409 idempotency conflicts, 503 store_failed), so clients see nothing unfamiliar."""
    if fmt in ("openrouter",):
        return {"error": {"code": status, "message": _as_text(message)}}
    if fmt in ("vercel", "evaluate"):
        kind = "invalid_request" if status in (400, 422) else "forbidden" if status == 403 else \
            "conflict" if status == 409 else "provider_error"
        return {"message": _as_text(message), "error_type": kind}
    return {"detail": message}


def validation_status(fmt: str) -> int:
    return 422 if fmt == "typesafe" else 400


def _as_text(message) -> str:
    if isinstance(message, list):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in message)
    if isinstance(message, dict):
        return message.get("message") or str(message)
    return str(message)


# ------------------------------------------------------------------------------------------------------------------
# Responses


def request_id() -> str:
    return "req_" + uuid.uuid4().hex


def strict_answer(a: dict, question: dict) -> dict:
    t = a.get("type")
    if t == "noul":
        return {"type": "noul", "noul": a["noul"]}
    if t == "choice":
        return {"type": "choice", "choice": a["choice"], "probabilities": a["probabilities"], "confidence": a["confidence"]}
    if t == "score":
        crit = question.get("criteria") or []
        legend = {str(i): c for i, c in enumerate(crit)} if isinstance(crit, list) else a.get("legend", {})
        return {"type": "score", "score": a["score"], "legend": legend, "probabilities": a["probabilities"],
                "confidence": a["confidence"]}
    # studio extension types: the official SDK skips unknown answer types with a warning, so they are safe to send
    return a


def shape(fmt: str, res: dict, body: dict, extended: bool, model_name: str) -> dict:
    """The worker's full response -> the requested wire format."""
    questions = body.get("questions") or {}
    usage = res.get("usage") or {}
    in_tok, out_tok = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    if extended:
        answers = res["answers"]
        for k, a in answers.items():
            if a.get("type") == "score" and isinstance((questions.get(k) or {}).get("criteria"), list):
                a["legend"] = {str(i): c for i, c in enumerate(questions[k]["criteria"])}
    else:
        answers = {k: strict_answer(a, questions.get(k) or {}) for k, a in res["answers"].items()}
    if fmt == "evaluate":
        ev = {}
        for k, a in answers.items():
            if a.get("type") == "noul":
                ev[k] = {"type": "boolean", "probability": a["noul"], **({kk: vv for kk, vv in a.items() if kk not in ("type", "noul")} if extended else {})}
            else:
                ev[k] = a
        out = {"model": model_name, "answers": ev, "usage": {"inputTokens": in_tok, "outputTokens": out_tok},
               "providerMetadata": {"basal": {"latencyMs": res.get("latency_ms")}}}
    else:
        out = {"model": model_name, "answers": answers, "usage": {"input_tokens": in_tok, "output_tokens": out_tok}}
        if fmt == "openrouter":
            out["id"] = "gen-dec-" + uuid.uuid4().hex[:24]
            out["provider"] = PROVIDER
            out["usage"]["cost"] = 0.0
        elif fmt == "vercel":
            out["provider_metadata"] = {"gateway": {"routing": {"originalModelId": body.get("model"), "resolvedProvider": PROVIDER,
                                                                "canonicalSlug": model_name, "finalProvider": PROVIDER},
                                                    "cost": "0", "generationId": "gen_" + uuid.uuid4().hex[:24]}}
    if extended:
        for k in ("latency_ms", "wall_ms", "passes", "notes"):
            if k in res:
                out[k] = res[k]
    return out
