"""Templates in the database: heads, immutable versions, aliases, test examples and the starter (built-in) templates.

templates.py holds the pure logic (checking, rendering, comparing); this module stores and finds things.
docs/studio-api.md sections 2.1, 2.2, 2.5, 3.2, 3.6 and 4.4.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path

from . import db
from . import templates as T
from .errors import ApiError
from .history import audit, canonical_label, check_metadata, merge_patch
from .ids import new_id

BUILTINS_FILE = Path(__file__).resolve().parent / "builtin_templates.json"
_versions: dict[tuple[int, int], dict] = {}          # immutable, so cached forever
_lock = threading.Lock()


# ----------------------------------------------------------------------------------------------------------------------
# Finding templates and versions


def head(tid: str, *, include_deleted: bool = False, conn=None) -> dict:
    c = conn or db.read()
    row = c.execute("SELECT * FROM templates WHERE workspace_id = ? AND id = ?", (db.WS, tid)).fetchone()
    if not row or (row["deleted_at"] and not include_deleted):
        extra = ""
        if row and row["deleted_at"]:
            import time
            extra = f" It was deleted on {time.strftime('%Y-%m-%d', time.gmtime(row['deleted_at'] / 1000))}."
        raise ApiError(404, "template_not_found", f"No template '{tid}'.{extra}", "template")
    return dict(row)


def version_row(tseq: int, number: int, conn=None) -> dict:
    c = conn or db.read()
    row = c.execute("SELECT * FROM template_versions WHERE template_seq = ? AND number = ?", (tseq, number)).fetchone()
    if not row:
        raise ApiError(404, "template_version_not_found", f"Version {number} does not exist.")
    return dict(row)


def definition(tseq: int, number: int, conn=None) -> dict:
    key = (tseq, number)
    d = _versions.get(key)
    if d is None:
        d = json.loads(version_row(tseq, number, conn)["definition"])
        _versions[key] = d
    return copy.deepcopy(d)


def resolve(ref: str, conn=None) -> tuple[dict, int, str]:
    """'support-triage', '@3', '@production', '@latest' -> (head, version number, resolved_from)."""
    tid, sel = T.split_ref(ref)
    h = head(tid, conn=conn)
    c = conn or db.read()
    if sel is None or sel == "latest":
        return h, h["latest_version"], "latest"
    if sel.isdigit():
        n = int(sel)
        if not 1 <= n <= h["latest_version"] or not c.execute(
                "SELECT 1 FROM template_versions WHERE template_seq = ? AND number = ?", (h["seq"], n)).fetchone():
            raise ApiError(404, "template_version_not_found",
                           f"{tid} has no version {n}; it has versions 1 to {h['latest_version']}.", "template")
        return h, n, "pinned"
    a = c.execute("SELECT version FROM template_aliases WHERE template_seq = ? AND alias = ?", (h["seq"], sel)).fetchone()
    if not a:
        names = [r[0] for r in c.execute("SELECT alias FROM template_aliases WHERE template_seq = ?", (h["seq"],))]
        raise ApiError(404, "alias_not_found", f"{tid} has no alias '{sel}'. Aliases: latest"
                                               f"{', ' + ', '.join(names) if names else ''}.", "template")
    return h, a[0], "alias"


def resolve_for_filter(ref: str) -> tuple[int, int | None]:
    """History filters: a bare id matches every version, including templates deleted with their history kept."""
    tid, sel = T.split_ref(ref)
    h = head(tid, include_deleted=True)
    if sel is None:
        return h["seq"], None
    if h["deleted_at"]:
        if sel.isdigit():
            return h["seq"], int(sel)
        raise ApiError(404, "template_not_found", f"'{tid}' was deleted; filter by a version number.", "template")
    _, n, _ = resolve(ref)
    return h["seq"], n


def aliases(tseq: int, latest: int, conn=None) -> dict:
    c = conn or db.read()
    out = {"latest": latest}
    for r in c.execute("SELECT alias, version FROM template_aliases WHERE template_seq = ? ORDER BY alias", (tseq,)):
        out[r[0]] = r[1]
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Objects


def template_object(h: dict, number: int | None = None, *, with_definition: bool = True, change: str | None = None,
                    warnings: list | None = None, conn=None) -> dict:
    c = conn or db.read()
    number = number or h["latest_version"]
    v = version_row(h["seq"], number, c)
    count = c.execute("SELECT COUNT(*) FROM examples WHERE template_seq = ? AND retired_in IS NULL", (h["seq"],)).fetchone()[0]
    out = {"id": h["id"], "object": "template", "name": h["name"], "description": h["description"], "origin": h["origin"],
           "archived": bool(h["archived_at"]), "metadata": json.loads(h["metadata"] or "{}"), "storage": h["storage"],
           "retention_days": h["retention_days"], "aliases": aliases(h["seq"], h["latest_version"], c),
           "examples_revision": h["examples_revision"], "examples_count": count, "version": number, "note": v["note"],
           "content_hash": v["content_hash"]}
    if with_definition:
        out.update(definition(h["seq"], number, c))
    out.update({"created_at": h["created_at"] // 1000, "updated_at": h["updated_at"] // 1000,
                "last_used_at": h["last_used_at"] // 1000 if h["last_used_at"] else None})
    if h.get("deleted_at"):
        out["deleted_at"] = h["deleted_at"] // 1000
    if change is not None:
        out["change"] = change
        out["warnings"] = warnings or []
    return out


def version_object(h: dict, v: dict, *, with_definition: bool = True, conn=None) -> dict:
    c = conn or db.read()
    al = [a for a, n in aliases(h["seq"], h["latest_version"], c).items() if n == v["number"]]
    changes = json.loads(v["changes"])
    out = {"object": "template.version", "template": h["id"], "version": v["number"], "note": v["note"],
           "content_hash": v["content_hash"], "questions_hash": v["questions_hash"], "question_set_key": v["question_set_key"],
           "source": v["source"], "base_version": v["base_number"], "aliases": al, "changes": changes}
    if with_definition:
        out.update(json.loads(v["definition"]))
    out.update({"created_at": v["created_at"] // 1000, "created_by": v["created_by"]})
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Writing


def _check_head(body: dict, h: dict | None) -> dict:
    out = {}
    if "name" in body:
        if not isinstance(body["name"], str) or not 0 < len(body["name"]) <= 120:
            raise ApiError(400, "invalid_field", "name is text of 1 to 120 characters.", "name")
        out["name"] = body["name"]
    if "description" in body:
        if not isinstance(body["description"], str) or len(body["description"]) > 2000:
            raise ApiError(400, "invalid_field", "description is text of up to 2,000 characters.", "description")
        out["description"] = body["description"]
    if "metadata" in body:
        base = json.loads(h["metadata"] or "{}") if h else {}
        meta = merge_patch(base, body["metadata"]) if body["metadata"] is not None else {}
        out["metadata"] = check_metadata(meta)
    if "storage" in body:
        if body["storage"] not in ("full", "answers_only", "none"):
            raise ApiError(400, "invalid_field", "storage is full, answers_only or none.", "storage")
        out["storage"] = body["storage"]
    if "retention_days" in body:
        v = body["retention_days"]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            raise ApiError(400, "invalid_field", "retention_days is a whole number (0 keeps forever) or null.", "retention_days")
        out["retention_days"] = v
    return out


def _latest_callers_7d(c, tseq: int) -> int:
    return c.execute("SELECT COUNT(*) FROM decisions WHERE template_seq = ? AND resolved_from = 'latest' AND created_at >= ?",
                     (tseq, db.now_ms() - 7 * 86400_000)).fetchone()[0]


def _insert_version(c, h: dict, defn: dict, note: str, source: str, base: int | None, actor: str = "local") -> int:
    number = h["latest_version"] + 1
    prev = definition(h["seq"], h["latest_version"], c) if h["latest_version"] else None
    changes = T.classify_change(prev, defn)
    changes["from"] = h["latest_version"] or None
    changes["latest_callers_7d"] = _latest_callers_7d(c, h["seq"]) if prev else 0
    now = db.now_ms()
    cur = c.execute("INSERT INTO template_versions (template_seq, number, definition, content_hash, questions_hash, "
                    "question_set_key, base_number, note, source, changes, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (h["seq"], number, T.canonical(defn), T.content_hash(defn), T.questions_hash(defn["questions"]),
                     T.question_set_key(defn["questions"]), base if base is not None else (h["latest_version"] or None),
                     note or "", source, json.dumps(changes), now, actor))
    for r in T.question_index(defn["questions"]):
        c.execute("INSERT INTO template_questions (version_seq, key, position, type, question_hash, text_hash, options_key) "
                  "VALUES (?,?,?,?,?,?,?)", (cur.lastrowid, r["key"], r["position"], r["type"], r["question_hash"],
                                             r["text_hash"], r["options_key"]))
    c.execute("UPDATE templates SET latest_version = ?, updated_at = ? WHERE seq = ?", (number, now, h["seq"]))
    h["latest_version"] = number
    h["updated_at"] = now
    audit(c, "template.version_saved", h["id"], {"version": number, "class": changes["class"]}, actor)
    return number


def _definition_body(body: dict, fill_defaults: bool, base: dict | None = None) -> dict:
    """Definition fields from a request body; with fill_defaults, missing fields reset to defaults (PUT)."""
    out = {} if fill_defaults or base is None else copy.deepcopy(base)
    for k in T.DEF_FIELDS:
        if k in body:
            out[k] = body[k]
    return out


def _check_if_match(h: dict, if_match: str | None, base_version) -> None:
    want = None
    if if_match:
        m = re.match(r'^(?:W/)?"?(\d+)"?$', if_match.strip())
        if not m:
            raise ApiError(400, "invalid_parameter", 'If-Match takes the version number the edit is based on, such as "3".', "If-Match")
        want = int(m.group(1))
    if base_version is not None:
        if not isinstance(base_version, int) or isinstance(base_version, bool):
            raise ApiError(400, "invalid_field", "base_version is a version number.", "base_version")
        want = base_version
    if want is not None and want != h["latest_version"]:
        raise ApiError(412, "version_conflict", f"{h['id']} is at version {h['latest_version']}, but this edit is based on "
                                                f"version {want}. Reload it, reapply your change, and save again.",
                       current_version=h["latest_version"])


def _writable(h: dict):
    if h["origin"] == "builtin":
        raise ApiError(403, "template_read_only", f"{h['id']} is a starter template and cannot be changed. Clone it: "
                                                  f"POST /v1/studio/templates with \"from\": {{\"template\": \"{h['id']}\"}}.")


def create(body: dict, *, source: str = "api", actor: str = "local") -> tuple[dict, int]:
    """POST /templates -> (template object, HTTP status)."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "The body is a JSON object.")
    tid = body.get("id")
    if not isinstance(tid, str) or not T.TEMPLATE_ID_RE.match(tid):
        raise ApiError(400, "invalid_field", "id is 1 to 64 lower-case letters, digits, '-' or '_', starting with a letter or "
                                             "digit, for example 'support-triage'. 'builtin/' is reserved.", "id")
    defn_in = _definition_body(body, True)
    frm = body.get("from")
    if frm is not None:
        defn_in = {**seed_from(frm), **{k: body[k] for k in T.DEF_FIELDS if k in body}}
        source = "clone"
    checked = T.check_definition(defn_in)
    head_in = _check_head(body, None)
    with _lock:
        c = db.read()
        existing = c.execute("SELECT * FROM templates WHERE workspace_id = ? AND id = ?", (db.WS, tid)).fetchone()
        if existing:
            if existing["deleted_at"]:
                raise ApiError(409, "template_id_reserved", f"'{tid}' belonged to a deleted template whose decisions are still "
                                                            "in history; choose another id, or delete that history.", "id")
            h = dict(existing)
            if T.content_hash(checked.definition) == version_row(h["seq"], h["latest_version"])["content_hash"]:
                return template_object(h, change="unchanged", warnings=checked.warnings), 200
            raise ApiError(409, "template_exists", f"'{tid}' already exists with a different definition. Use PUT to replace it or "
                                                   "PATCH to change it; both save a new version.", "id")
        return _create_new(tid, checked, head_in, body.get("note") or ("First version" if source == "api" else ""),
                           source, actor), 201


def _create_new(tid: str, checked: T.Checked, head_in: dict, note: str, source: str, actor: str, origin: str = "user") -> dict:
    now = db.now_ms()

    def tx(c):
        cur = c.execute("INSERT INTO templates (workspace_id, id, name, description, metadata, origin, storage, retention_days, "
                        "created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (db.WS, tid, head_in.get("name") or tid, head_in.get("description", ""),
                         json.dumps(head_in.get("metadata") or {}), origin, head_in.get("storage", "full"),
                         head_in.get("retention_days"), now, now, actor))
        h = dict(c.execute("SELECT * FROM templates WHERE seq = ?", (cur.lastrowid,)).fetchone())
        _insert_version(c, h, checked.definition, note, source, None, actor)
        audit(c, "template.created", tid, None, actor)
        return h
    db.write(tx)
    return template_object(head(tid), change="created", warnings=checked.warnings)


def upsert(tid: str, body: dict, *, if_match: str | None = None, source: str = "api") -> tuple[dict, int]:
    """PUT /templates/{id}: missing definition fields reset to defaults; missing head fields keep their values."""
    if not T.TEMPLATE_ID_RE.match(tid):
        if T.BUILTIN_ID_RE.match(tid):
            raise ApiError(403, "template_read_only", f"{tid} is a starter template and cannot be changed.")
        raise ApiError(400, "invalid_field", "Template ids are lower-case letters, digits, '-' and '_'.", "id")
    if "id" in body and body["id"] != tid:
        raise ApiError(400, "invalid_field", "The body's id differs from the one in the path.", "id")
    c = db.read()
    existing = c.execute("SELECT * FROM templates WHERE workspace_id = ? AND id = ?", (db.WS, tid)).fetchone()
    if not existing or existing["deleted_at"]:
        return create({**body, "id": tid}, source=source)
    return _save(dict(existing), _definition_body(body, True), body, if_match, body.get("base_version"), source)


def patch(tid: str, body: dict, *, if_match: str | None = None) -> dict:
    """PATCH /templates/{id}: an RFC 7396 merge patch over the template object."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "The body is a JSON merge patch (an object).")
    if "archived" in body:
        raise ApiError(400, "read_only_field", "Use POST /templates/{id}/archive or /unarchive.", "archived")
    for k in ("id", "object", "origin", "version", "content_hash", "aliases", "examples_revision", "examples_count",
              "created_at", "updated_at", "last_used_at"):
        if k in body:
            raise ApiError(400, "read_only_field", f"'{k}' cannot be changed.", k)
    h = head(tid)
    base = definition(h["seq"], h["latest_version"])
    merged = {k: (merge_patch(base.get(k), body[k]) if isinstance(body.get(k), dict) and isinstance(base.get(k), dict)
                  else body[k]) if k in body else base.get(k) for k in T.DEF_FIELDS}
    return _save(h, merged, body, if_match, body.get("base_version"), "api")[0]


def _save(h: dict, defn_in: dict, body: dict, if_match, base_version, source: str) -> tuple[dict, int]:
    _writable(h)
    checked = T.check_definition(defn_in)
    head_in = _check_head(body, h)
    note = body.get("note") or ""
    with _lock:
        h = head(h["id"])
        _check_if_match(h, if_match, base_version)
        same = T.content_hash(checked.definition) == version_row(h["seq"], h["latest_version"])["content_hash"]
        head_changed = any(h.get(k) != (json.dumps(v) if k == "metadata" else v) for k, v in head_in.items())
        if not same and h["archived_at"]:
            raise ApiError(409, "template_archived", f"{h['id']} is archived; unarchive it before saving a new version.")

        def tx(c):
            if head_in:
                sets = ", ".join(f"{k} = ?" for k in head_in)
                vals = [json.dumps(v) if k == "metadata" else v for k, v in head_in.items()]
                c.execute(f"UPDATE templates SET {sets}, updated_at = ? WHERE seq = ?", vals + [db.now_ms(), h["seq"]])
            if not same:
                _insert_version(c, h, checked.definition, note, source, base_version)
        db.write(tx)
    change = "new_version" if not same else ("metadata_only" if head_changed else "unchanged")
    return template_object(head(h["id"]), change=change, warnings=checked.warnings), 200


def new_version(tid: str, body: dict, *, if_match: str | None = None) -> tuple[dict, bool]:
    """POST /templates/{id}/versions: an explicit full definition. -> (version object, created?)."""
    h = head(tid)
    _writable(h)
    if h["archived_at"]:
        raise ApiError(409, "template_archived", f"{tid} is archived; unarchive it before saving a new version.")
    before = h["latest_version"]
    obj, _ = _save(h, _definition_body(body, True), {"note": body.get("note")}, if_match, body.get("base_version"), "api")
    h = head(tid)
    return version_object(h, version_row(h["seq"], h["latest_version"])), h["latest_version"] != before


def restore(tid: str, number: int, note: str | None) -> dict:
    h = head(tid)
    _writable(h)
    old = definition(h["seq"], number)
    obj, _ = _save(h, old, {"note": note or f"Restore version {number}"}, None, None, "restore")
    return obj


def set_archived(tid: str, archived: bool) -> dict:
    h = head(tid)
    _writable(h)
    db.write(lambda c: (c.execute("UPDATE templates SET archived_at = ?, updated_at = ? WHERE seq = ?",
                                  (db.now_ms() if archived else None, db.now_ms(), h["seq"])),
                        audit(c, "template.archived" if archived else "template.unarchived", tid)))
    return template_object(head(tid))


def set_alias(tid: str, alias: str, version) -> dict:
    h = head(tid)
    _writable(h)
    if alias == "latest" or not T.ALIAS_RE.match(alias):
        raise ApiError(400, "invalid_field", "An alias is 1 to 32 lower-case letters, digits, '-' or '_', starting with a letter; "
                                             "'latest' is reserved.", "alias")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ApiError(400, "invalid_field", "version is a version number.", "version")
    version_row(h["seq"], version)
    prev = db.read().execute("SELECT version FROM template_aliases WHERE template_seq = ? AND alias = ?", (h["seq"], alias)).fetchone()
    now = db.now_ms()
    db.write(lambda c: (c.execute("INSERT INTO template_aliases (template_seq, alias, version, updated_at, updated_by) VALUES "
                                  "(?,?,?,?,?) ON CONFLICT(template_seq, alias) DO UPDATE SET version = excluded.version, "
                                  "updated_at = excluded.updated_at", (h["seq"], alias, version, now, "local")),
                        audit(c, "template.alias_moved", tid, {"alias": alias, "version": version})))
    return {"object": "template.alias", "template": tid, "alias": alias, "version": version,
            "previous_version": prev[0] if prev else None, "updated_at": now // 1000}


def delete_alias(tid: str, alias: str) -> dict:
    h = head(tid)
    _writable(h)
    if not db.read().execute("SELECT 1 FROM template_aliases WHERE template_seq = ? AND alias = ?", (h["seq"], alias)).fetchone():
        raise ApiError(404, "alias_not_found", f"{tid} has no alias '{alias}'.")
    db.write(lambda c: (c.execute("DELETE FROM template_aliases WHERE template_seq = ? AND alias = ?", (h["seq"], alias)),
                        audit(c, "template.alias_moved", tid, {"alias": alias, "version": None})))
    return {"id": alias, "object": "template.alias.deleted", "deleted": True}


def delete(tid: str, confirm: str | None, history_mode: str) -> dict:
    h = head(tid)
    _writable(h)
    if confirm != tid:
        raise ApiError(400, "confirmation_required", f"Deleting a template breaks every caller that uses it. Repeat its id: "
                                                     f"?confirm={tid}", "confirm")
    if history_mode not in ("keep", "delete"):
        raise ApiError(400, "invalid_parameter", "history is keep or delete.", "history")

    def tx(c):
        n_dec = 0
        if history_mode == "delete":
            n_dec = c.execute("SELECT COUNT(*) FROM decisions WHERE template_seq = ?", (h["seq"],)).fetchone()[0]
            c.execute("DELETE FROM decisions WHERE template_seq = ?", (h["seq"],))
            c.execute("DELETE FROM templates WHERE seq = ?", (h["seq"],))
            kept = 0
        else:
            c.execute("DELETE FROM template_aliases WHERE template_seq = ?", (h["seq"],))
            c.execute("DELETE FROM examples WHERE template_seq = ?", (h["seq"],))
            used = {r[0] for r in c.execute("SELECT DISTINCT version_number FROM decisions WHERE template_seq = ?", (h["seq"],))}
            if used:
                c.execute(f"DELETE FROM template_versions WHERE template_seq = ? AND number NOT IN ({','.join('?' * len(used))})",
                          [h["seq"], *used])
                c.execute("UPDATE templates SET deleted_at = ?, archived_at = COALESCE(archived_at, ?) WHERE seq = ?",
                          (db.now_ms(), db.now_ms(), h["seq"]))
            else:
                c.execute("DELETE FROM templates WHERE seq = ?", (h["seq"],))
            kept = len(used)
        audit(c, "template.deleted", tid, {"history": history_mode, "decisions_deleted": n_dec})
        return kept, n_dec
    kept, n_dec = db.write(tx)
    for k in [k for k in _versions if k[0] == h["seq"]]:
        _versions.pop(k, None)
    return {"id": tid, "object": "template.deleted", "deleted": True, "versions_kept": kept, "decisions_deleted": n_dec}


def list_templates(f: dict) -> dict:
    from .history import _limit, truthy
    limit = _limit(f.get("limit"))
    where, args = ["workspace_id = ?", "deleted_at IS NULL"], [db.WS]
    if not truthy(f.get("include_archived")):
        where.append("archived_at IS NULL")
    if f.get("origin") in ("user", "builtin"):
        where.append("origin = ?")
        args.append(f["origin"])
    if f.get("q"):
        where.append("(id LIKE ? OR name LIKE ? OR description LIKE ?)")
        args += [f"%{f['q']}%"] * 3
    for k, v in f.items():
        if k.startswith("metadata."):
            where.append("json_extract(metadata, ?) = ?")
            args += [f'$."{k[9:]}"', v]
    c = db.read()
    if f.get("after"):
        a = c.execute("SELECT updated_at, seq FROM templates WHERE workspace_id = ? AND id = ?", (db.WS, f["after"])).fetchone()
        if not a:
            raise ApiError(400, "invalid_parameter", "after takes a template id from an earlier page.", "after")
        where.append("(updated_at < ? OR (updated_at = ? AND seq < ?))")
        args += [a[0], a[0], a[1]]
    rows = [dict(r) for r in c.execute(f"SELECT * FROM templates WHERE {' AND '.join(where)} ORDER BY updated_at DESC, seq DESC",
                                       args)]
    want = f.get("compatible_with")
    if want:
        spec = T.find_model(want)
        if spec is None:
            raise ApiError(404, "model_not_found", f"Unknown model '{want}'.", "compatible_with")
        rows = [r for r in rows if not T.model_problems(definition(r["seq"], r["latest_version"])["questions"], [], spec,
                                                        T._required_media(definition(r["seq"], r["latest_version"])["variables"]))]
    more = len(rows) > limit
    rows = rows[:limit]
    full = "definition" in (f.get("include") or "")
    data = [template_object(r, with_definition=full) for r in rows]
    for d, r in zip(data, rows):
        if not full:
            dd = definition(r["seq"], r["latest_version"])
            d["summary"] = {"questions": list(dd["questions"]), "variables": list(dd["variables"]), "model": dd["model"],
                            "modalities": dd["modalities"]}
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": more}


def list_versions(tid: str, f: dict) -> dict:
    from .history import _limit
    h = head(tid, include_deleted=True)
    limit = _limit(f.get("limit"))
    where, args = ["template_seq = ?"], [h["seq"]]
    if f.get("after"):
        where.append("number < ?")
        args.append(int(f["after"]))
    rows = [dict(r) for r in db.read().execute(f"SELECT * FROM template_versions WHERE {' AND '.join(where)} "
                                                f"ORDER BY number DESC LIMIT ?", args + [limit + 1])]
    more = len(rows) > limit
    full = "definition" in (f.get("include") or "")
    data = [version_object(h, r, with_definition=full) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["version"] if data else None,
            "last_id": data[-1]["version"] if data else None, "has_more": more}


def version_number(h: dict, sel: str) -> int:
    if sel == "latest":
        return h["latest_version"]
    if str(sel).isdigit():
        n = int(sel)
        version_row(h["seq"], n)
        return n
    _, n, _ = resolve(f"{h['id']}@{sel}")
    return n


# ----------------------------------------------------------------------------------------------------------------------
# Seeding a definition from another template or a stored decision


def seed_from(frm) -> dict:
    if not isinstance(frm, dict) or len(frm) != 1 or not ({"template", "decision"} & set(frm)):
        raise ApiError(400, "invalid_field", "from is {\"template\": \"<ref>\"} or {\"decision\": \"dec_...\"}.", "from")
    if "template" in frm:
        h, n, _ = resolve(frm["template"])
        return definition(h["seq"], n)
    from .history import _row
    row = _row(frm["decision"])
    c = db.read()
    if row["storage"] != "full":
        raise ApiError(409, "input_unavailable", "This decision kept only its answers, so its state cannot seed a template.", "from")
    b = c.execute("SELECT * FROM decision_bodies WHERE decision_seq = ?", (row["seq"],)).fetchone()
    qs = json.loads(c.execute("SELECT definition FROM question_sets WHERE hash = ?", (row["questions_hash"],)).fetchone()[0])
    st = json.loads(b["settings"] or "{}")
    settings = {k: st[k] for k in ("act_threshold", "temperature") if st.get(k) is not None and st.get("sources", {}).get(k) != "studio"}
    if st.get("questions"):
        settings["questions"] = {k: v for k, v in st["questions"].items() if k in qs}
    state = json.loads(b["state"]) if b["state"] is not None else ""
    variables: dict = {}
    if isinstance(state, dict):
        tmpl_state = {}
        for k, v in state.items():
            name = re.sub(r"[^a-z0-9_]", "_", str(k).lower()).strip("_") or "field"
            if not re.match(r"^[a-z_]", name):
                name = "f_" + name
            name = name[:64]
            while name in variables:
                name = (name + "_2")[:64]
            variables[name] = _infer(v)
            tmpl_state[k] = "{{" + name + "}}"
        state_t = tmpl_state
    elif isinstance(state, list):
        variables["items"] = {"type": "json", "description": "The list the decision is about."}
        state_t = "{{items}}"
    else:
        variables["message"] = {"type": "string", "description": "The situation to decide about."}
        state_t = "{{message}}"
    mods = ["text"]
    for i, m in enumerate(c.execute("SELECT type FROM decision_media WHERE decision_seq = ? ORDER BY position", (row["seq"],))):
        if m[0] not in mods:
            mods.append(m[0])
        variables[f"{m[0]}_{i + 1}" if i else m[0]] = {"type": m[0], "required": False}
    return {"modalities": mods, "variables": variables, "state": state_t, "questions": qs, "model": row["model"],
            "settings": settings}


def _infer(v) -> dict:
    if isinstance(v, bool):
        return {"type": "boolean"}
    if isinstance(v, int):
        return {"type": "integer"}
    if isinstance(v, float):
        return {"type": "number"}
    if isinstance(v, str):
        return {"type": "string", "trusted": False}
    if v is None:
        return {"type": "string", "required": False}
    return {"type": "json"}


# ----------------------------------------------------------------------------------------------------------------------
# Test examples (revisioned, immutable rows)


def _example_input(h: dict, defn: dict, body: dict, where: str = "") -> dict:
    from .errors import Problems
    from .decisions import install_secret
    p = Problems()
    variables, state = body.get("variables"), body.get("state")
    media = body.get("media") or []
    T.render(defn, variables, state, install_secret(), p)
    if isinstance(media, list):
        for i, m in enumerate(media):
            if not isinstance(m, dict) or not (m.get("file_id") or m.get("data")):
                p.add("invalid_field", f"{where}media[{i}]", "Each media item is {type?, file_id | data, name?}.")
    else:
        p.add("invalid_field", f"{where}media", "media is a list.")
    if p:
        for it in p.items:
            if where and it["param"]:
                it["param"] = where + it["param"]
        p.raise_if_any()
    return {"variables": variables if defn["variables"] else None, "state": None if defn["variables"] else state,
            "media": media}


def _labels(defn: dict, expected, where: str = "expected") -> dict:
    if expected is None:
        return {}
    if not isinstance(expected, dict):
        raise ApiError(400, "invalid_field", "expected is an object of question key to label.", where)
    out = {}
    for k, v in expected.items():
        if k not in defn["questions"]:
            raise ApiError(400, "invalid_expected", f"'{k}' is not a question of this template.{T.did_you_mean(k, defn['questions'])}",
                           f"{where}.{k}")
        q = defn["questions"][k]
        if T.is_dynamic(q):
            out[k] = v
            continue
        out[k] = canonical_label(q, v, f"{where}.{k}")
    return out


def _example_fields(body: dict, where: str = "") -> dict:
    tags = body.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and 0 < len(t) <= 64 for t in tags):
        raise ApiError(400, "invalid_field", "tags is a list of short labels.", f"{where}tags")
    split = body.get("split", "test")
    if split not in ("test", "calibration"):
        raise ApiError(400, "invalid_field", "split is test or calibration.", f"{where}split")
    note = body.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        raise ApiError(400, "invalid_field", "note is text of up to 2,000 characters.", f"{where}note")
    return {"tags": tags, "split": split, "note": note}


def example_object(h: dict, row: dict, created_at: int | None = None) -> dict:
    inp = json.loads(row["input"])
    return {"id": row["id"], "object": "template.example", "template": h["id"], "revision": row["revision"],
            "variables": inp.get("variables"), "state": inp.get("state"), "media": inp.get("media") or [],
            "expected": json.loads(row["expected"]), "tags": json.loads(row["tags"]), "split": row["split"], "note": row["note"],
            "from_decision": row["from_decision"], "created_at": (created_at or row["created_at"]) // 1000,
            "updated_at": row["created_at"] // 1000}


def _from_decision(h: dict, decision_id: str) -> tuple[dict, dict]:
    from .history import _row
    row = _row(decision_id)
    c = db.read()
    if row["storage"] != "full":
        raise ApiError(409, "input_unavailable", "This decision kept only its answers; its input cannot become an example.")
    b = c.execute("SELECT variables, state, redacted FROM decision_bodies WHERE decision_seq = ?", (row["seq"],)).fetchone()
    if b["redacted"]:
        raise ApiError(409, "input_unavailable", "Parts of this decision's input were redacted.")
    variables = json.loads(b["variables"]) if b["variables"] else None
    if variables and any(isinstance(v, dict) and "$redacted" in v for v in variables.values()):
        raise ApiError(409, "input_unavailable", "This decision used sensitive values, which history never keeps.")
    media = [{"type": m["type"], "file_id": m["id"]} for m in c.execute(
        "SELECT m.type, f.id FROM decision_media m JOIN files f ON f.seq = m.file_seq WHERE m.decision_seq = ? AND m.variable IS NULL "
        "ORDER BY m.position", (row["seq"],))]
    fb = {}
    for f in c.execute("SELECT question_key, expected FROM feedback WHERE decision_seq = ? AND expected IS NOT NULL", (row["seq"],)):
        fb[f[0]] = json.loads(f[1])
    if variables is not None and row["template_seq"] == h["seq"]:
        return {"variables": variables, "media": media}, fb
    return {"state": json.loads(b["state"]) if b["state"] else None, "media": media}, fb


def add_example(tid: str, body: dict, *, _bulk: bool = False, conn=None) -> dict:
    h = head(tid)
    defn = definition(h["seq"], h["latest_version"])
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "An example is an object.")
    fields = _example_fields(body)
    fb = {}
    src = body
    if body.get("from_decision"):
        src, fb = _from_decision(h, body["from_decision"])
        if not defn["variables"] and src.get("state") is None:
            raise ApiError(409, "input_unavailable", "This decision has no stored state.")
        if defn["variables"] and src.get("variables") is None:
            raise ApiError(400, "invalid_field", "This decision was not made with this template, so its variables are unknown. "
                                                 "Send the example's variables yourself.", "from_decision")
    inp = _example_input(h, defn, src)
    expected = {**{k: v for k, v in fb.items() if k in defn["questions"]}, **_labels(defn, body.get("expected"))}
    ih = T.sha({k: inp.get(k) for k in ("variables", "state", "media")})

    def tx(c):
        dup = c.execute("SELECT id FROM examples WHERE template_seq = ? AND input_hash = ? AND retired_in IS NULL",
                        (h["seq"], ih)).fetchone()
        if dup:
            raise ApiError(409, "example_exists", f"This input is already example {dup[0]}.", existing_id=dup[0])
        rev = c.execute("UPDATE templates SET examples_revision = examples_revision + 1 WHERE seq = ? RETURNING examples_revision",
                        (h["seq"],)).fetchone()[0]
        eid = new_id("ex")
        c.execute("INSERT INTO examples (id, template_seq, revision, input, expected, tags, split, note, input_hash, from_decision, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, h["seq"], rev, json.dumps(inp), json.dumps(expected), json.dumps(fields["tags"]), fields["split"],
                   fields["note"], ih, body.get("from_decision"), db.now_ms()))
        return eid
    eid = tx(conn) if conn is not None else db.write(tx)
    if _bulk:
        return {"id": eid}
    return get_example(tid, eid)


def get_example(tid: str, eid: str, revision: int | None = None) -> dict:
    h = head(tid)
    c = db.read()
    if revision is None:
        row = c.execute("SELECT * FROM examples WHERE template_seq = ? AND id = ? AND retired_in IS NULL", (h["seq"], eid)).fetchone()
    else:
        row = c.execute("SELECT * FROM examples WHERE template_seq = ? AND id = ? AND revision <= ? AND (retired_in IS NULL OR "
                        "retired_in > ?) ORDER BY revision DESC LIMIT 1", (h["seq"], eid, revision, revision)).fetchone()
    if not row:
        raise ApiError(404, "example_not_found", f"{tid} has no example '{eid}'.")
    first = c.execute("SELECT MIN(created_at) FROM examples WHERE template_seq = ? AND id = ?", (h["seq"], eid)).fetchone()[0]
    return example_object(h, dict(row), first)


def patch_example(tid: str, eid: str, body: dict) -> dict:
    h = head(tid)
    cur = get_example(tid, eid)
    defn = definition(h["seq"], h["latest_version"])
    merged = {"variables": body.get("variables", cur["variables"]), "state": body.get("state", cur["state"]),
              "media": body.get("media", cur["media"])}
    if isinstance(body.get("variables"), dict) and isinstance(cur["variables"], dict):
        merged["variables"] = merge_patch(cur["variables"], body["variables"])
    inp = _example_input(h, defn, merged)
    expected = cur["expected"]
    if "expected" in body:
        expected = {k: v for k, v in merge_patch(expected, body["expected"]).items()}
        expected = _labels(defn, expected)
    fields = _example_fields({"tags": body.get("tags", cur["tags"]), "split": body.get("split", cur["split"]),
                              "note": body.get("note", cur["note"])})
    ih = T.sha({k: inp.get(k) for k in ("variables", "state", "media")})

    def tx(c):
        dup = c.execute("SELECT id FROM examples WHERE template_seq = ? AND input_hash = ? AND retired_in IS NULL AND id <> ?",
                        (h["seq"], ih, eid)).fetchone()
        if dup:
            raise ApiError(409, "example_exists", f"This input is already example {dup[0]}.", existing_id=dup[0])
        rev = c.execute("UPDATE templates SET examples_revision = examples_revision + 1 WHERE seq = ? RETURNING examples_revision",
                        (h["seq"],)).fetchone()[0]
        c.execute("UPDATE examples SET retired_in = ? WHERE template_seq = ? AND id = ? AND retired_in IS NULL", (rev, h["seq"], eid))
        c.execute("INSERT INTO examples (id, template_seq, revision, input, expected, tags, split, note, input_hash, from_decision, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, h["seq"], rev, json.dumps(inp), json.dumps(expected), json.dumps(fields["tags"]), fields["split"],
                   fields["note"], ih, cur["from_decision"], db.now_ms()))
    db.write(tx)
    return get_example(tid, eid)


def delete_example(tid: str, eid: str) -> dict:
    h = head(tid)
    get_example(tid, eid)

    def tx(c):
        rev = c.execute("UPDATE templates SET examples_revision = examples_revision + 1 WHERE seq = ? RETURNING examples_revision",
                        (h["seq"],)).fetchone()[0]
        c.execute("UPDATE examples SET retired_in = ? WHERE template_seq = ? AND id = ? AND retired_in IS NULL", (rev, h["seq"], eid))
    db.write(tx)
    return {"id": eid, "object": "template.example.deleted", "deleted": True}


def list_examples(tid: str, f: dict) -> dict:
    from .history import truthy
    h = head(tid)
    c = db.read()
    rev = f.get("revision")
    where, args = ["template_seq = ?"], [h["seq"]]
    if rev not in (None, ""):
        where.append("revision <= ? AND (retired_in IS NULL OR retired_in > ?)")
        args += [int(rev), int(rev)]
    else:
        where.append("retired_in IS NULL")
    if f.get("split"):
        where.append("split = ?")
        args.append(f["split"])
    if f.get("tag"):
        where.append("EXISTS (SELECT 1 FROM json_each(examples.tags) WHERE value = ?)")
        args.append(f["tag"])
    lab = truthy(f.get("labelled"))
    if lab is not None:
        where.append("expected <> '{}'" if lab else "expected = '{}'")
    if f.get("q"):
        where.append("input LIKE ?")
        args.append(f"%{f['q']}%")
    limit = int(f.get("limit") or 100)
    if f.get("after"):
        a = c.execute("SELECT MIN(row_seq) FROM examples WHERE template_seq = ? AND id = ?", (h["seq"], f["after"])).fetchone()[0]
        if a:
            where.append("row_seq > ?")
            args.append(a)
    rows = [dict(r) for r in c.execute(f"SELECT * FROM examples WHERE {' AND '.join(where)} ORDER BY row_seq LIMIT ?",
                                       args + [min(max(limit, 1), 1000) + 1])]
    more = len(rows) > limit
    data = [example_object(h, r) for r in rows[:limit]]
    return {"object": "list", "data": data, "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None, "has_more": more, "revision": h["examples_revision"]}


def import_examples(tid: str, body: dict) -> dict:
    items = body.get("examples")
    if not isinstance(items, list) or not items:
        raise ApiError(400, "invalid_field", "examples is a list of example objects (up to 5,000).", "examples")
    if len(items) > 5000:
        raise ApiError(400, "too_many_items", "Import up to 5,000 examples at a time.", "examples")
    created, failed = 0, []
    for i, ex in enumerate(items):
        try:
            add_example(tid, ex, _bulk=True)
            created += 1
        except ApiError as e:
            failed.append({"index": i, "error": {"code": e.code, "param": e.param, "message": e.message}})
    return {"object": "import_result", "created": created, "revision": head(tid)["examples_revision"], "failed": failed}


def export_examples(tid: str):
    h = head(tid)
    for r in db.read().execute("SELECT * FROM examples WHERE template_seq = ? AND retired_in IS NULL ORDER BY row_seq", (h["seq"],)):
        yield json.dumps(example_object(h, dict(r)), ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------------------------------------------------
# Built-in (starter) templates, generated from the Playground's examples (scripts/export-builtins.mjs)


def seed_builtins() -> int:
    """Create or update the read-only builtin/<scenario> templates. A changed scenario adds a version. -> changes made."""
    if not BUILTINS_FILE.exists() or not db.available() or db.get().read_only:
        return 0
    items = json.loads(BUILTINS_FILE.read_text())
    changed = 0
    for it in items:
        tid = f"builtin/{it['id']}"
        try:
            checked = T.check_definition({"questions": it["questions"], "modalities": it.get("modalities") or ["text"]})
        except ApiError as e:
            print(f"[templates] starter template {tid} is invalid: {e.message}", flush=True)
            continue
        meta = {"basal.group": it.get("group", ""), "basal.recommended_models": ",".join(it.get("recommended_models") or [])}
        if it.get("sample"):
            meta["basal.sample"] = it["sample"]
        row = db.read().execute("SELECT * FROM templates WHERE workspace_id = ? AND id = ?", (db.WS, tid)).fetchone()
        if row is None:
            now = db.now_ms()

            def tx(c, it=it, tid=tid, checked=checked, meta=meta):
                cur = c.execute("INSERT INTO templates (workspace_id, id, name, description, metadata, origin, created_at, updated_at, "
                                "created_by) VALUES (?,?,?,?,?,'builtin',?,?,'studio')",
                                (db.WS, tid, it["title"], it.get("blurb", ""), json.dumps(meta), now, now))
                h = dict(c.execute("SELECT * FROM templates WHERE seq = ?", (cur.lastrowid,)).fetchone())
                _insert_version(c, h, checked.definition, "Starter template", "builtin", None, "studio")
                if it.get("state") not in (None, "", {}, []):
                    inp = {"variables": None, "state": it["state"], "media": []}
                    c.execute("UPDATE templates SET examples_revision = 1 WHERE seq = ?", (h["seq"],))
                    c.execute("INSERT INTO examples (id, template_seq, revision, input, expected, tags, split, note, input_hash, "
                              "created_at) VALUES (?,?,1,?,?,?,'test',?,?,?)",
                              (new_id("ex"), h["seq"], json.dumps(inp), json.dumps(it.get("expected") or {}),
                               json.dumps(["starter"]), it.get("sample") and f"Uses the sample image {it['sample']}." or "",
                               T.sha({"variables": None, "state": it["state"], "media": []}), now))
            db.write(tx)
            changed += 1
            continue
        h = dict(row)
        if T.content_hash(checked.definition) != version_row(h["seq"], h["latest_version"])["content_hash"]:
            db.write(lambda c, h=h, checked=checked: _insert_version(c, h, checked.definition, "Updated starter template", "builtin",
                                                                     None, "studio"))
            changed += 1
        if (h["name"], h["description"], h["metadata"]) != (it["title"], it.get("blurb", ""), json.dumps(meta)):
            db.write(lambda c, h=h, it=it, meta=meta: c.execute(
                "UPDATE templates SET name = ?, description = ?, metadata = ? WHERE seq = ?",
                (it["title"], it.get("blurb", ""), json.dumps(meta), h["seq"])))
    return changed
