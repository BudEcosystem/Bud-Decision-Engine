"""Media files: images, audio and video attached to decisions.

Bytes live on disk, content-addressed (DATA/blobs/sha256/3a/3a7b...e1.jpg), so the same photo sent twice is stored
once. Every upload still gets its own random public id (file_<ULID>): ids derived from content would let one caller
discover that another holds a given file. Model workers read the blob paths directly.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi.responses import FileResponse

from . import db
from .errors import ApiError, param_path
from .ids import ULID_RE, new_id
from .paths import DATA, UPLOADS

BLOBS = DATA / "blobs"
TMP = BLOBS / "tmp"
MAX_BYTES = 200 * 1024 * 1024
FILE_TTL_MS = 24 * 3600 * 1000          # an unreferenced upload is kept for a day
MEDIA_EXT = {"image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
             "audio": {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"},
             "video": {".mp4", ".mov", ".webm", ".mkv", ".avi"}}
INLINE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "audio/wav", "audio/x-wav", "audio/mpeg",
                "audio/mp4", "audio/ogg", "audio/flac", "audio/webm", "video/mp4", "video/quicktime", "video/webm",
                "video/x-matroska", "video/x-msvideo"}
FILE_ID = re.compile(rf"^file_{ULID_RE}$")
LEGACY_ID = re.compile(r"^[0-9a-f]{32}(\.[a-z0-9]{1,5})?$")

for d in (BLOBS, TMP):
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass


def media_type(filename: str, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    for kind, exts in MEDIA_EXT.items():
        if ext in exts:
            return "video" if ext == ".webm" and (content_type or "").startswith("video") else kind
    ct = (content_type or "").split("/")[0]
    if ct in MEDIA_EXT:
        return ct
    raise ApiError(415, "unsupported_media_type", f"Unsupported file type '{ext or content_type}'. Use an image, audio or video file.")


def extension(kind: str, filename: str | None, content_type: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in MEDIA_EXT.get(kind, ()):
        return ext
    ext = mimetypes.guess_extension(content_type or "") or ""
    return ext if ext in MEDIA_EXT.get(kind, ()) else ""


def content_type_of(kind: str, ext: str, given: str | None = None) -> str:
    if given and "/" in given and given.split("/")[0] == kind:
        return given
    return mimetypes.types_map.get(ext, f"{kind}/octet-stream")


def blob_path(sha256: str, ext: str) -> Path:
    return BLOBS / "sha256" / sha256[:2] / f"{sha256}{ext}"


def put_bytes(data: bytes, ext: str) -> tuple[str, Path]:
    """Store bytes content-addressed; the same content is written once. -> (sha256 hex, path)."""
    h = hashlib.sha256(data).hexdigest()
    p = blob_path(h, ext)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = TMP / uuid.uuid4().hex
        tmp.write_bytes(data)
        os.replace(tmp, p)
    return h, p


def temp_bytes(data: bytes, ext: str) -> tuple[str, Path]:
    """Bytes the model reads once and nothing keeps (storage 'none' or 'answers_only')."""
    p = TMP / f"{uuid.uuid4().hex}{ext}"
    p.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), p


def decode_data_url(url: str, where: str) -> tuple[str, bytes]:
    m = re.match(r"data:([\w/+.-]+)(;[\w=-]+)*;base64,(.*)$", url, re.S)
    if not m:
        raise ApiError(400, "invalid_field", "A data URL looks like data:image/png;base64,...", where)
    try:
        raw = base64.b64decode(m.group(3), validate=False)
    except binascii.Error:
        raise ApiError(400, "invalid_field", "The data URL is not valid base64.", where)
    if len(raw) > MAX_BYTES:
        raise ApiError(413, "payload_too_large", "Files up to 200 MB are supported.", where)
    return m.group(1), raw


# ----------------------------------------------------------------------------------------------------------------------
# The file registry


def register(c, sha256: str, ext: str, nbytes: int, *, purpose: str, kind: str | None, content_type: str,
             name: str | None, legacy_id: str | None = None) -> dict:
    now = db.now_ms()
    c.execute("INSERT OR IGNORE INTO blobs (sha256, ext, bytes, stored, created_at) VALUES (?,?,?,1,?)", (sha256, ext, nbytes, now))
    c.execute("UPDATE blobs SET stored = 1 WHERE sha256 = ?", (sha256,))
    fid = new_id("file", now)
    c.execute("INSERT INTO files (id, workspace_id, blob_sha256, purpose, type, content_type, name, bytes, created_at, "
              "expires_at, last_ref_at, legacy_upload_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (fid, db.WS, sha256, purpose, kind, content_type, name, nbytes, now, now + FILE_TTL_MS, now, legacy_id))
    return dict(c.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone())


def create(data: bytes, filename: str | None, content_type: str | None, purpose: str = "media") -> dict:
    if len(data) > MAX_BYTES:
        raise ApiError(413, "payload_too_large", "Files up to 200 MB are supported.")
    kind = media_type(filename or "", content_type)
    ext = extension(kind, filename, content_type)
    ctype = content_type_of(kind, ext, content_type)
    h, _ = put_bytes(data, ext)
    return db.write(lambda c: register(c, h, ext, len(data), purpose=purpose, kind=kind, content_type=ctype, name=filename))


def get(file_id: str, conn=None) -> dict | None:
    c = conn or db.read()
    row = c.execute("SELECT f.*, b.ext, b.stored FROM files f JOIN blobs b ON b.sha256 = f.blob_sha256 "
                    "WHERE f.id = ? OR f.legacy_upload_id = ?", (file_id, file_id)).fetchone()
    return dict(row) if row else None


def path_of(row: dict) -> Path:
    return blob_path(row["blob_sha256"], row.get("ext") or "")


def available(row: dict) -> bool:
    return bool(row.get("stored")) and path_of(row).exists()


def file_object(row: dict) -> dict:
    return {"id": row["id"], "object": "file", "purpose": row["purpose"], "type": row["type"],
            "content_type": row["content_type"], "name": row["name"], "bytes": row["bytes"],
            "sha256": "sha256:" + row["blob_sha256"], "created_at": row["created_at"] // 1000,
            "expires_at": (row["expires_at"] // 1000) if row.get("expires_at") else None,
            "available": available(row)}


def content_response(row: dict):
    """The bytes, served so that nothing stored can run as a page on the studio's origin."""
    p = path_of(row)
    if not row.get("stored") or not p.exists():
        raise ApiError(404, "file_not_found", "This file's bytes are no longer kept (retention, or media storage is off).")
    ctype = row["content_type"] if row["content_type"] in INLINE_TYPES else "application/octet-stream"
    inline = ctype in INLINE_TYPES
    name = (row.get("name") or row["id"]).replace('"', "")
    headers = {"x-content-type-options": "nosniff", "content-security-policy": "sandbox; default-src 'none'",
               "content-disposition": f'{"inline" if inline else "attachment"}; filename="{name}"', "cache-control": "private, max-age=3600"}
    return FileResponse(p, media_type=ctype, headers=headers)


# ----------------------------------------------------------------------------------------------------------------------
# Media on a decision


@dataclass
class MediaRef:
    type: str
    path: str
    name: str | None
    content_type: str
    bytes: int
    sha256: str
    variable: str | None = None
    file_seq: int | None = None
    file_id: str | None = None
    temp: bool = False           # delete right after the model has read it
    sensitive: bool = False      # a sensitive variable's file: read by the model, never linked to the decision
    stored_hash: str | None = None   # what history records instead of the content hash (a keyed hash)

    @property
    def fingerprint(self) -> str:
        """The hash history and the API show: the content hash, or the keyed hash of a sensitive file."""
        return self.stored_hash or "sha256:" + self.sha256

    def worker(self) -> dict:
        return {"type": self.type, "path": self.path, "name": self.name}


def resolve(items: list[dict], *, keep: bool, allowed: list[str] | None = None) -> list[MediaRef]:
    """Request media -> files the worker can read. keep=False: inline bytes are never stored (storage 'none' or
    'answers_only', or media storage switched off). The file of a sensitive variable is never kept, whatever `keep`
    says. Server paths and remote URLs are never accepted."""
    out: list[MediaRef] = []
    try:
        _resolve_into(out, items, keep, allowed)
    except BaseException:
        cleanup(out)         # a later item was refused: the temporary copies of the earlier ones go too
        raise
    return out


def _resolve_into(out: list[MediaRef], items: list[dict], keep_all: bool, allowed: list[str] | None):
    for i, m in enumerate(items or []):
        where = m.get("_param") or param_path("media", i)
        secret = isinstance(m, dict) and bool(m.get("_sensitive"))
        keep = keep_all and not secret
        if not isinstance(m, dict):
            raise ApiError(400, "invalid_field", "Each media item is {type?, file_id | data | path, name?}.", where)
        want = m.get("type")
        if want is not None and want not in MEDIA_EXT:
            raise ApiError(400, "invalid_field", "media type is image, audio or video.", f"{where}.type")
        ref = None
        if m.get("data"):
            mime, raw = decode_data_url(m["data"], f"{where}.data")
            kind = want or media_type("", mime)
            ext = extension(kind, m.get("name"), mime)
            ctype = content_type_of(kind, ext, mime)
            if keep and db.available():
                h, p = put_bytes(raw, ext)
                row = db.write(lambda c: register(c, h, ext, len(raw), purpose="media", kind=kind, content_type=ctype, name=m.get("name")))
                ref = MediaRef(kind, str(p), m.get("name"), ctype, len(raw), h, m.get("variable"), row["seq"], row["id"])
            else:
                h, p = temp_bytes(raw, ext)
                ref = MediaRef(kind, str(p), m.get("name"), ctype, len(raw), h, m.get("variable"), temp=True)
        else:
            fid = m.get("file_id") or (Path(m["path"]).name if isinstance(m.get("path"), str) else None)
            if not fid:
                raise ApiError(400, "invalid_field", "A media item needs `file_id` (from POST /v1/studio/files) or `data` "
                                                     "(a data: URL).", where)
            row = get(fid) if db.available() and (FILE_ID.match(fid) or LEGACY_ID.match(fid)) else None
            if row:
                if not available(row):
                    raise ApiError(409, "input_unavailable", f"The bytes of {fid} are no longer kept.", where)
                ref = MediaRef(row["type"] or media_type(row.get("name") or "", row["content_type"]), str(path_of(row)),
                               m.get("name") or row["name"], row["content_type"], row["bytes"], row["blob_sha256"],
                               m.get("variable"), row["seq"] if keep else None, row["id"])
            elif LEGACY_ID.match(fid) and (UPLOADS / fid).exists():
                p = UPLOADS / fid
                kind = want or media_type(p.name, None)
                raw = p.read_bytes()
                ref = MediaRef(kind, str(p), m.get("name") or p.name, content_type_of(kind, p.suffix.lower()), len(raw),
                               hashlib.sha256(raw).hexdigest(), m.get("variable"))
            else:
                raise ApiError(404, "file_not_found", f"No file '{fid}'. Upload it with POST /v1/studio/files; uploads that "
                                                      "no decision uses are removed after a day.", where)
        if want and want != ref.type:
            ref.type = want
        ref.sensitive = secret
        if allowed is not None and ref.type not in allowed:
            cleanup([ref] + out)
            raise ApiError(400, "modality_not_allowed", f"This template accepts {', '.join(allowed)} input, not {ref.type}. "
                                                        "Add it to the template's modalities to allow it.", where)
        out.append(ref)


def cleanup(refs: list[MediaRef]):
    for r in refs:
        if r.temp:
            try:
                Path(r.path).unlink()
            except OSError:
                pass


def sweep_files(c, now_ms: int) -> dict:
    """Delete expired files nothing references, then blobs no file references. -> counts."""
    dead = [r["seq"] for r in c.execute(
        "SELECT f.seq FROM files f WHERE f.expires_at IS NOT NULL AND f.expires_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM decision_media m WHERE m.file_seq = f.seq) "
        "AND NOT EXISTS (SELECT 1 FROM example_media e WHERE e.file_seq = f.seq) LIMIT 2000", (now_ms,))]
    for s in dead:
        c.execute("DELETE FROM files WHERE seq = ?", (s,))
    orphans = [dict(r) for r in c.execute("SELECT sha256, ext FROM blobs b WHERE NOT EXISTS "
                                          "(SELECT 1 FROM files f WHERE f.blob_sha256 = b.sha256) LIMIT 2000")]
    for b in orphans:
        c.execute("DELETE FROM blobs WHERE sha256 = ?", (b["sha256"],))
        try:
            blob_path(b["sha256"], b["ext"]).unlink()
        except OSError:
            pass
    return {"files": len(dead), "blobs": len(orphans)}


def delete(c, row: dict):
    """DELETE /files/{id}: the file goes, and its bytes with it unless another upload shares them. Decisions that used
    it keep the type, size and hash and show the media as no longer available: privacy wins over completeness."""
    c.execute("DELETE FROM files WHERE seq = ?", (row["seq"],))
    if not c.execute("SELECT 1 FROM files WHERE blob_sha256 = ?", (row["blob_sha256"],)).fetchone():
        c.execute("DELETE FROM blobs WHERE sha256 = ?", (row["blob_sha256"],))
        try:
            blob_path(row["blob_sha256"], row.get("ext") or "").unlink()
        except OSError:
            pass


def disk_bytes() -> int:
    total = 0
    for root, _, files in os.walk(BLOBS / "sha256"):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
