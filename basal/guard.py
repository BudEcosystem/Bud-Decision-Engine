"""The cross-site guard: other websites open in the person's browser must not be able to write to the studio.

A page on any site can send a "simple" POST (text/plain, no preflight) to http://localhost:8420/v1/systemone, and
before history existed the worst it could do was spend compute. Now every decision is saved, so writes from a browser
must come from the studio's own pages (or an origin listed in BASAL_CORS_ORIGINS). Requests without browser headers
(curl, the SDKs, servers) are unaffected. docs/studio-api.md section 4.12.
"""
from __future__ import annotations

import os

from fastapi import Request

GUARDED_PREFIXES = ("/v1/", "/api/")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


def allowed_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in os.environ.get("BASAL_CORS_ORIGINS", "").split(",") if o.strip()}


def own_origins(host_header: str, scheme: str = "http") -> set[str]:
    host = (host_header or "").strip()
    if not host:
        return set()
    if host.startswith("["):
        name, _, port = host.partition("]")
        name += "]"
        port = port.lstrip(":")
    else:
        name, _, port = host.partition(":")
    suffix = f":{port}" if port else ""
    if name in LOOPBACK or name == "::1":
        return {f"http://{h}{suffix}" for h in LOOPBACK} | {f"https://{h}{suffix}" for h in LOOPBACK}
    return {f"http://{host}", f"https://{host}"}


def check(request: Request) -> tuple[int, str, str] | None:
    """-> None when the request may proceed, else (status, code, message)."""
    if request.method not in WRITE_METHODS or not request.url.path.startswith(GUARDED_PREFIXES):
        return None
    h = request.headers
    origin = h.get("origin")
    site = (h.get("sec-fetch-site") or "").lower()
    if origin is not None:
        o = origin.strip().rstrip("/")
        if o == "null" or (o not in own_origins(h.get("host", ""), request.url.scheme) and o not in allowed_origins()):
            return 403, "cross_site_request", (f"Requests from {origin} may not change this studio. Other websites can't write "
                                               "to it; to allow one, list it in BASAL_CORS_ORIGINS.")
    elif site in ("cross-site", "same-site"):
        return 403, "cross_site_request", ("This request came from another website, which may not change the studio. To allow "
                                           "one, list its origin in BASAL_CORS_ORIGINS.")
    browser = origin is not None or any(k.startswith("sec-fetch-") for k in h.keys())
    if browser and request.url.path.startswith("/v1/studio/"):
        ctype = (h.get("content-type") or "").split(";")[0].strip().lower()
        has_body = request.method in ("POST", "PUT", "PATCH") and (h.get("content-length") not in (None, "0") or
                                                                    h.get("transfer-encoding"))
        if has_body and ctype not in ("application/json", "application/merge-patch+json") and \
                not (ctype == "multipart/form-data" and h.get("x-basal-client")) and \
                not (ctype and ctype.split("/")[0] in ("image", "audio", "video") and h.get("x-basal-client")):
            return 415, "unsupported_media_type", "Send JSON (Content-Type: application/json) from a browser."
    return None
