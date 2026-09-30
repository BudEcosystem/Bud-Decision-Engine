"""Errors of the studio API (/v1/studio): one envelope, every problem listed at once.

    {"error": {"type", "code", "message", "param", "details": [...], "request_id", "decision_id"}}
"""
from __future__ import annotations

TYPES = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error", 404: "not_found_error",
         409: "conflict_error", 412: "conflict_error", 413: "invalid_request_error", 415: "invalid_request_error",
         429: "rate_limit_error", 500: "api_error", 503: "api_error", 504: "model_error"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, param: str | None = None,
                 details: list[dict] | None = None, type_: str | None = None, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.param = param
        self.details = details or []
        self.type = type_ or TYPES.get(status, "api_error")
        self.extra = extra

    def body(self, request_id: str | None = None) -> dict:
        err = {"type": self.type, "code": self.code, "message": self.message, "param": self.param,
               "details": self.details or [{"code": self.code, "param": self.param, "message": self.message}],
               "request_id": request_id, "decision_id": self.extra.get("decision_id")}
        err.update({k: v for k, v in self.extra.items() if k != "decision_id"})
        return {"error": err}


class Problems:
    """Collects every validation problem of one pass, then raises them together."""

    def __init__(self):
        self.items: list[dict] = []

    def add(self, code: str, param: str | None, message: str):
        self.items.append({"code": code, "param": param, "message": message})

    def __bool__(self):
        return bool(self.items)

    def raise_if_any(self, status: int = 400):
        if self.items:
            first = self.items[0]
            raise ApiError(status, first["code"], first["message"], first["param"], list(self.items))


def param_path(*parts) -> str:
    """questions.urgency.criteria, media[0].file_id, questions["odd key"]."""
    out = ""
    for p in parts:
        if isinstance(p, int):
            out += f"[{p}]"
        elif p and all(ch.isalnum() or ch in "_-" for ch in str(p)):
            out += ("." if out else "") + str(p)
        else:
            out += f'["{p}"]'
    return out
