"""Templates: reusable decisions (typed variables, a state template, questions, a default model and settings) that run
on any model. This module is pure logic, with no database: checking a definition, filling in variables, merging a
caller's extra questions, choosing settings per question, and comparing two versions.

A template's *definition* is versioned (every change is a new, immutable version); its *head* (name, description,
metadata, storage, retention) is not. docs/studio-api.md sections 2.1, 4.1 to 4.4.
"""
from __future__ import annotations

import copy
import datetime as _dt
import difflib
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .catalog import BY_ID, CATALOG
from .contract import AnswerQuestion, render as render_state
from .errors import ApiError, Problems, param_path

VAR_TYPES = ("string", "integer", "number", "boolean", "json", "options", "image", "audio", "video")
MEDIA_TYPES = ("image", "audio", "video")
QUESTION_TYPES = ("choice", "score", "noul", "multi", "rank", "number")
OPTION_TYPES = ("choice", "multi", "rank")          # types whose options have names
TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
BUILTIN_ID_RE = re.compile(r"^builtin/[a-z0-9][a-z0-9_-]{0,63}$")
QKEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
VAR_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
PLACEHOLDER = re.compile(r"(\\?)\{\{\s*([^{}]*?)\s*\}\}")
WHOLE = re.compile(r"^\{\{\s*([a-z_][a-z0-9_]{0,63})\s*\}\}$")
DEF_FIELDS = ("modalities", "variables", "state", "questions", "model", "settings", "extensions")
HEAD_FIELDS = ("name", "description", "metadata", "storage", "retention_days")
DEFAULT_EXTENSIONS = {"questions": True, "max_questions": 16, "options": [], "skip": []}
MAX_VARIABLES = 64
MAX_QUESTIONS = 128
STRING_DEFAULT_MAX = 20_000
STRING_PLATFORM_MAX = 200_000
JSON_DEFAULT_MAX = 256 * 1024
TYPE_KEYS = {
    "string": {"min_length", "max_length", "pattern", "enum", "format"},
    "integer": {"minimum", "maximum", "enum"},
    "number": {"minimum", "maximum"},
    "boolean": set(),
    "json": {"schema", "max_bytes"},
    "options": {"min_items", "max_items"},
    "image": {"max_bytes"}, "audio": {"max_bytes"}, "video": {"max_bytes"},
}
COMMON_KEYS = {"type", "description", "required", "default", "example", "sensitive", "trusted"}
_QUESTION = TypeAdapter(AnswerQuestion)
MISSING = object()


# ----------------------------------------------------------------------------------------------------------------------
# Small helpers


def canonical(x: Any) -> str:
    """JSON with no insignificant whitespace and key order preserved (order changes what a model reads)."""
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(x: Any) -> str:
    data = x if isinstance(x, (bytes, str)) else canonical(x)
    return "sha256:" + hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def content_hash(defn: dict) -> str:
    return sha({k: defn.get(k) for k in DEF_FIELDS})


def questions_hash(questions: dict) -> str:
    return sha(questions)


def question_set_key(questions: dict) -> str:
    """Question keys sorted, option order kept: the same questions sent in another key order group together."""
    return sha({k: questions[k] for k in sorted(questions)})


def find_model(name: str | None):
    """A catalog model by studio id, Hugging Face repo id or display name."""
    if not name:
        return None
    for spec in CATALOG:
        if name in (spec.id, spec.repo.id, spec.name):
            return spec
    return None


def split_ref(ref: str) -> tuple[str, str | None]:
    """'support-triage@3' -> ('support-triage', '3'); 'support-triage' -> ('support-triage', None)."""
    if not isinstance(ref, str) or not ref:
        raise ApiError(400, "invalid_reference", "A template reference is a string such as 'support-triage@2'.", "template")
    tid, _, sel = ref.partition("@")
    return tid, (sel or None)


def valid_template_id(tid: str) -> bool:
    return bool(TEMPLATE_ID_RE.match(tid) or BUILTIN_ID_RE.match(tid))


def placeholders(text: str) -> list[str]:
    return [m.group(2) for m in PLACEHOLDER.finditer(text) if not m.group(1)]


def _walk_strings(v, path: tuple = ()):
    """Every string inside a JSON value, with its path (keys are not visited)."""
    if isinstance(v, str):
        yield path, v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _walk_strings(x, path + (k,))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _walk_strings(x, path + (i,))


def option_names(q: dict) -> list[str] | None:
    """Names of a choice/multi/rank question's options; None when they come from a variable."""
    c = q.get("criteria")
    if isinstance(c, str):
        return None
    if isinstance(c, dict):
        return [str(k) for k in c]
    if isinstance(c, list):
        return [str(x) for x in c]
    return []


def number_values(q: dict) -> list[float]:
    c = q.get("criteria")
    keys = list(c) if isinstance(c, dict) else (c or [])
    out = []
    for k in keys:
        try:
            out.append(float(k))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def is_dynamic(q: dict) -> bool:
    return isinstance(q.get("criteria"), str)


def did_you_mean(name: str, choices) -> str:
    m = difflib.get_close_matches(name, list(choices), n=1, cutoff=0.6)
    return f" Did you mean '{m[0]}'?" if m else ""


# ----------------------------------------------------------------------------------------------------------------------
# Checking a definition (when a version is saved)


@dataclass
class Checked:
    definition: dict
    warnings: list[dict] = field(default_factory=list)


def _warn(ws: list, code: str, message: str, param: str | None = None):
    ws.append({"code": code, "message": message, "param": param})


def _closed_vocabulary(spec: dict) -> bool:
    t = spec["type"]
    return t in ("integer", "number", "boolean") or (t == "string" and ("enum" in spec or "format" in spec))


def check_definition(body: dict) -> Checked:
    """Validate and normalise a template definition. Raises ApiError(400) listing every problem found."""
    p = Problems()
    warnings: list[dict] = []

    # modalities ---------------------------------------------------------------------------------------------------
    mods_in = body.get("modalities")
    mods: list[str] = []
    if mods_in is None:
        mods = ["text"]
    elif not isinstance(mods_in, list) or not all(isinstance(m, str) for m in mods_in):
        p.add("invalid_field", "modalities", "modalities is a list such as [\"text\", \"image\"].")
    else:
        for i, m in enumerate(mods_in):
            if m not in ("text",) + MEDIA_TYPES:
                p.add("invalid_field", f"modalities[{i}]", f"'{m}' is not a modality. Use text, image, audio or video.")
            elif m not in mods:
                mods.append(m)
        if "text" not in mods:
            mods.insert(0, "text")

    # variables ----------------------------------------------------------------------------------------------------
    vars_in = body.get("variables") or {}
    variables: dict[str, dict] = {}
    if not isinstance(vars_in, dict):
        p.add("invalid_field", "variables", "variables is an object of name to variable spec.")
        vars_in = {}
    if len(vars_in) > MAX_VARIABLES:
        p.add("invalid_field", "variables", f"A template has at most {MAX_VARIABLES} variables; this one has {len(vars_in)}.")
    for name, spec in vars_in.items():
        where = param_path("variables", name)
        if not VAR_RE.match(str(name)):
            p.add("invalid_field", where, f"'{name}' is not a valid variable name: use lower-case letters, digits and "
                                          "underscores, starting with a letter or underscore (up to 64 characters).")
            continue
        if not isinstance(spec, dict) or spec.get("type") not in VAR_TYPES:
            p.add("invalid_field", param_path("variables", name, "type"),
                  f"Variable '{name}' needs a type: one of {', '.join(VAR_TYPES)}.")
            continue
        t = spec["type"]
        extra = set(spec) - COMMON_KEYS - TYPE_KEYS[t]
        for k in sorted(extra):
            p.add("invalid_field", param_path("variables", name, k), f"'{k}' is not a setting of {t} variables.")
        norm = {"type": t}
        for k, v in spec.items():
            if k not in ("type", "required", "sensitive", "trusted") and k not in extra:
                norm[k] = v
        has_default = spec.get("default") is not None
        norm["required"] = bool(spec.get("required", not has_default))
        norm["sensitive"] = bool(spec.get("sensitive", False))
        norm["trusted"] = bool(spec.get("trusted", False))
        _check_var_spec(name, norm, p)
        if t in MEDIA_TYPES and t not in mods and mods_in is not None:
            p.add("modality_not_declared", where, f"Variable '{name}' is an {t}, so '{t}' must be listed in modalities.")
        elif t in MEDIA_TYPES and t not in mods:
            mods.append(t)
        if has_default:
            sub = Problems()
            _check_value(name, norm, spec["default"], sub, param_path("variables", name, "default"))
            for it in sub.items:
                p.add("invalid_field", it["param"], "The default is not valid: " + it["message"])
        variables[name] = norm

    # state --------------------------------------------------------------------------------------------------------
    state = body.get("state")
    if state is not None and not isinstance(state, (str, dict, list)):
        p.add("invalid_field", "state", "state is a text template, a JSON template (object or array), or null.")
    if not variables and state is not None:
        p.add("invalid_definition", "state", "A template without variables takes its state from each request. Remove "
                                             "`state`, or declare the variables it uses.")

    # questions ----------------------------------------------------------------------------------------------------
    qs_in = body.get("questions")
    questions: dict[str, dict] = {}
    if not isinstance(qs_in, dict) or not qs_in:
        p.add("missing_field" if qs_in is None else "invalid_field", "questions",
              "A template needs at least one question: an object of key to question, as in /v1/systemone.")
        qs_in = {}
    if len(qs_in) > MAX_QUESTIONS:
        p.add("invalid_field", "questions", f"A template has at most {MAX_QUESTIONS} questions.")
    uses: dict[str, set[str]] = {}          # variable -> {"state", "question", "criteria"}
    for key, q in qs_in.items():
        where = param_path("questions", key)
        if not QKEY_RE.match(str(key)):
            p.add("invalid_field", where, f"'{key}' is not a valid question key: letters, digits and underscores, "
                                          "starting with a letter or underscore (up to 64 characters).")
            continue
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES:
            p.add("invalid_field", param_path("questions", key, "type"),
                  f"Question '{key}' needs a type: one of {', '.join(QUESTION_TYPES)}.")
            continue
        q = copy.deepcopy(q)
        _collect_question_placeholders(key, q, variables, uses, p)
        # validate the shape with placeholders filled by samples, exactly as the model endpoint would
        sample = _sample_question(q, variables)
        try:
            _QUESTION.validate_python(sample)
        except ValidationError as e:
            for x in e.errors(include_url=False):
                loc = [str(l) for l in x["loc"][1:]]
                p.add("invalid_field", param_path("questions", key, *loc), f"Question '{key}': {x['msg']}")
        questions[key] = q

    for path, s in _walk_strings(state if state is not None else ""):
        for name in placeholders(s):
            uses.setdefault(name, set()).add("state")
            if name not in variables:
                p.add("undeclared_variable", param_path("state", *path),
                      f"The state uses {{{{{name}}}}}, but no variable '{name}' is declared.{did_you_mean(name, variables)}")
            elif variables[name]["type"] in MEDIA_TYPES:
                p.add("media_variable_in_text", param_path("state", *path),
                      f"'{name}' is an {variables[name]['type']} variable: media is attached to the decision, never placed in text.")
            elif variables[name]["type"] == "options":
                p.add("invalid_definition", param_path("state", *path),
                      f"'{name}' is an options variable; it can only supply a question's criteria (\"criteria\": \"{{{{{name}}}}}\").")

    for name, spec in variables.items():
        used = uses.get(name)
        if spec["type"] in MEDIA_TYPES:
            continue
        if not used and state is not None:
            _warn(warnings, "variable_unreferenced", f"Variable '{name}' is declared but not used in the state or the questions.",
                  param_path("variables", name))
        if spec["type"] == "options" and not used:
            _warn(warnings, "variable_unreferenced", f"Options variable '{name}' does not supply any question's criteria.",
                  param_path("variables", name))

    # model --------------------------------------------------------------------------------------------------------
    model = body.get("model")
    if model is not None:
        spec = find_model(model) if isinstance(model, str) else None
        if spec is None:
            p.add("invalid_field", "model", f"Unknown model '{model}'. Use one of: {', '.join(BY_ID)}.")
        else:
            model = spec.id

    # settings -----------------------------------------------------------------------------------------------------
    settings = check_settings(body.get("settings"), p, "settings", question_types={k: q["type"] for k, q in questions.items()},
                              allow_models=True)

    # extensions ---------------------------------------------------------------------------------------------------
    ext_in = body.get("extensions")
    ext = dict(DEFAULT_EXTENSIONS)
    if ext_in is not None:
        if not isinstance(ext_in, dict):
            p.add("invalid_field", "extensions", "extensions is an object: {questions, max_questions, options, skip}.")
            ext_in = {}
        for k in set(ext_in) - set(DEFAULT_EXTENSIONS):
            p.add("invalid_field", f"extensions.{k}", f"'{k}' is not an extensions setting.")
        if "questions" in ext_in:
            if not isinstance(ext_in["questions"], bool):
                p.add("invalid_field", "extensions.questions", "extensions.questions is true or false.")
            else:
                ext["questions"] = ext_in["questions"]
        if "max_questions" in ext_in:
            v = ext_in["max_questions"]
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_QUESTIONS:
                p.add("invalid_field", "extensions.max_questions", f"extensions.max_questions is a whole number from 0 to {MAX_QUESTIONS}.")
            else:
                ext["max_questions"] = v
        for k, allowed in (("options", ("choice", "multi", "rank", "number")), ("skip", QUESTION_TYPES)):
            if k not in ext_in:
                continue
            v = ext_in[k]
            if v is True:
                ext[k] = True
            elif isinstance(v, list) and all(isinstance(x, str) for x in v):
                for i, qk in enumerate(v):
                    if qk not in questions:
                        p.add("unknown_question", f"extensions.{k}[{i}]", f"extensions.{k} names '{qk}', which is not a question "
                                                                         f"of this template.{did_you_mean(qk, questions)}")
                    elif questions[qk]["type"] not in allowed:
                        p.add("options_not_extensible", f"extensions.{k}[{i}]",
                              f"'{qk}' is a {questions[qk]['type']} question; options can be added only to choice, multi, rank "
                              "and number questions (a new level would change what a score or yes/no means).")
                ext[k] = list(dict.fromkeys(v))
            else:
                p.add("invalid_field", f"extensions.{k}", f"extensions.{k} is true or a list of question keys.")

    p.raise_if_any()
    defn = {"modalities": mods, "variables": variables, "state": state, "questions": questions, "model": model,
            "settings": settings, "extensions": ext}
    if model:
        probs = model_problems(defn["questions"], [], BY_ID[model], required_media=_required_media(variables))
        for pr in probs:
            _warn(warnings, "default_model_incompatible", f"The default model cannot run this template: {pr['message']}", pr.get("param"))
    return Checked(defn, warnings)


def _required_media(variables: dict) -> list[str]:
    return [v["type"] for v in variables.values() if v["type"] in MEDIA_TYPES and v.get("required")]


def _check_var_spec(name: str, spec: dict, p: Problems):
    t = spec["type"]
    at = lambda k: param_path("variables", name, k)   # noqa: E731
    for k in ("min_length", "max_length", "min_items", "max_items", "max_bytes"):
        if k in spec and (not isinstance(spec[k], int) or isinstance(spec[k], bool) or spec[k] < 0):
            p.add("invalid_field", at(k), f"{k} is a whole number of zero or more.")
    if t == "string":
        if "max_length" in spec and isinstance(spec["max_length"], int) and spec["max_length"] > STRING_PLATFORM_MAX:
            p.add("invalid_field", at("max_length"), f"Strings are limited to {STRING_PLATFORM_MAX:,} characters.")
        if "pattern" in spec:
            try:
                re.compile(spec["pattern"])
            except (re.error, TypeError):
                p.add("invalid_field", at("pattern"), "pattern is not a valid regular expression.")
        if "enum" in spec and (not isinstance(spec["enum"], list) or not spec["enum"]
                               or not all(isinstance(x, str) for x in spec["enum"])):
            p.add("invalid_field", at("enum"), "enum is a non-empty list of strings.")
        if "format" in spec and spec["format"] not in ("date", "date-time"):
            p.add("invalid_field", at("format"), "format is 'date' or 'date-time'.")
    if t == "integer" and "enum" in spec and (not isinstance(spec["enum"], list) or not spec["enum"]
                                              or not all(isinstance(x, int) and not isinstance(x, bool) for x in spec["enum"])):
        p.add("invalid_field", at("enum"), "enum is a non-empty list of whole numbers.")
    for k in ("minimum", "maximum"):
        if k in spec and (not isinstance(spec[k], (int, float)) or isinstance(spec[k], bool)):
            p.add("invalid_field", at(k), f"{k} is a number.")
    if t == "json" and "schema" in spec and not isinstance(spec["schema"], dict):
        p.add("invalid_field", at("schema"), "schema is a JSON Schema object.")
    if t == "options" and "max_items" in spec and isinstance(spec["max_items"], int) and spec["max_items"] > 1000:
        p.add("invalid_field", at("max_items"), "An options variable holds at most 1,000 options.")
    if spec.get("trusted") and spec.get("sensitive"):
        p.add("invalid_field", at("trusted"), "A sensitive variable cannot be trusted: question text is kept in history.")


def _collect_question_placeholders(key: str, q: dict, variables: dict, uses: dict, p: Problems):
    t = q["type"]
    c = q.get("criteria")

    def text_use(path: tuple, s: str):
        for name in placeholders(s):
            where = param_path("questions", key, *path)
            uses.setdefault(name, set()).add("question")
            if name not in variables:
                p.add("undeclared_variable", where,
                      f"Question '{key}' uses {{{{{name}}}}}, but no variable '{name}' is declared.{did_you_mean(name, variables)}")
                continue
            v = variables[name]
            if v["type"] in MEDIA_TYPES:
                p.add("media_variable_in_text", where, f"'{name}' is an {v['type']} variable; media is never placed in text.")
            elif v["type"] == "options":
                p.add("invalid_definition", where, f"'{name}' is an options variable; use it as the whole criteria: "
                                                   f"\"criteria\": \"{{{{{name}}}}}\".")
            elif v.get("sensitive"):
                p.add("invalid_definition", where, f"'{name}' is sensitive and question text is kept in history, so it "
                                                   "cannot appear in a question.")
            elif not _closed_vocabulary(v) and not v.get("trusted"):
                p.add("untrusted_variable_in_question", where,
                      f"'{name}' is free text, so an end user could change what question '{key}' asks. Declare it with "
                      "\"trusted\": true if only your code sets it, or give it an enum.")

    for path, s in _walk_strings(q.get("instructions")):
        text_use(("instructions",) + path, s)
    if isinstance(c, str):
        m = WHOLE.match(c)
        where = param_path("questions", key, "criteria")
        if not m:
            p.add("invalid_field", where, f"Question '{key}': criteria is an object or a list, or exactly \"{{{{variable}}}}\".")
            return
        name = m.group(1)
        uses.setdefault(name, set()).add("criteria")
        if t not in OPTION_TYPES:
            p.add("placeholder_not_allowed", where, f"Only choice, multi and rank questions take their options from a "
                                                    f"variable; '{key}' is a {t} question.")
        elif name not in variables:
            p.add("undeclared_variable", where, f"Question '{key}' takes its options from '{name}', which is not declared."
                                                f"{did_you_mean(name, variables)}")
        elif variables[name]["type"] != "options":
            p.add("invalid_definition", where, f"'{name}' must be an options variable to supply '{key}''s options.")
        return
    if t in OPTION_TYPES or t == "noul":
        items = c.items() if isinstance(c, dict) else ((x, None) for x in (c or []))
        for name, desc in items:
            if placeholders(str(name)):
                p.add("placeholder_not_allowed", param_path("questions", key, "criteria"),
                      f"Question '{key}': option names cannot contain placeholders; they are the answer vocabulary "
                      "history is grouped by. Put the variable in the option's description instead.")
            for path, s in _walk_strings(desc):
                text_use(("criteria", str(name)) + path, s)
    elif t == "score":
        if isinstance(c, list):
            for i, lvl in enumerate(c):
                for path, s in _walk_strings(lvl):
                    text_use(("criteria", i) + path, s)
    elif t == "number":
        keys = list(c) if isinstance(c, dict) else (c or [])
        if any(isinstance(k, str) and placeholders(k) for k in keys):
            p.add("placeholder_not_allowed", param_path("questions", key, "criteria"),
                  f"Question '{key}': number values cannot come from placeholders.")
        if isinstance(c, dict):
            for k, desc in c.items():
                for path, s in _walk_strings(desc):
                    text_use(("criteria", str(k)) + path, s)


def _sample_question(q: dict, variables: dict) -> dict:
    """The question with every placeholder replaced by a sample, for shape validation."""
    s = copy.deepcopy(q)
    if isinstance(s.get("criteria"), str):
        s["criteria"] = ["option_a", "option_b"]
    return s


def check_settings(s, p: Problems, where: str, question_types: dict[str, str] | None = None,
                   allow_models: bool = False, warnings: list | None = None) -> dict:
    """A Settings object (section 2.1). Template settings may carry `models`; request settings may not."""
    if s is None:
        return {}
    if not isinstance(s, dict):
        p.add("invalid_field", where, f"{where} is an object: {{act_threshold, temperature, questions{', models' if allow_models else ''}}}.")
        return {}
    out: dict = {}
    allowed = {"act_threshold", "temperature", "multi_threshold", "questions"} | ({"models"} if allow_models else set())
    for k in s:
        if k not in allowed:
            if warnings is not None:
                _warn(warnings, "unknown_field_ignored", f"'{where}.{k}' is not a setting and was ignored.", f"{where}.{k}")
            else:
                p.add("invalid_field", f"{where}.{k}", f"'{k}' is not a setting. Use act_threshold, temperature, questions"
                                                       f"{' or models' if allow_models else ''}.")
    _check_values(s, out, p, where)
    if "questions" in s:
        qs = s["questions"]
        if not isinstance(qs, dict):
            p.add("invalid_field", f"{where}.questions", "questions is an object of question key to settings.")
        else:
            out["questions"] = {}
            for qk, qv in qs.items():
                qw = f"{where}.questions.{qk}"
                if question_types is not None and qk not in question_types:
                    p.add("unknown_question", qw, f"'{qk}' is not one of the questions.{did_you_mean(qk, question_types)}")
                    continue
                if not isinstance(qv, dict):
                    p.add("invalid_field", qw, "Per-question settings are an object: {act_threshold, temperature, multi_threshold}.")
                    continue
                for k in set(qv) - {"act_threshold", "temperature", "multi_threshold"}:
                    p.add("invalid_field", f"{qw}.{k}", f"'{k}' is not a per-question setting.")
                qo: dict = {}
                _check_values(qv, qo, p, qw)
                if "multi_threshold" in qo and question_types is not None and question_types.get(qk) != "multi":
                    p.add("invalid_field", f"{qw}.multi_threshold", f"multi_threshold applies to multi questions; '{qk}' is not one.")
                out["questions"][qk] = qo
    if allow_models and "models" in s:
        ms = s["models"]
        if not isinstance(ms, dict):
            p.add("invalid_field", f"{where}.models", "models is an object of model id to settings.")
        else:
            out["models"] = {}
            for mk, mv in ms.items():
                spec = find_model(mk)
                mw = f"{where}.models.{mk}"
                if spec is None:
                    p.add("invalid_field", mw, f"Unknown model '{mk}'. Use one of: {', '.join(BY_ID)}.")
                    continue
                sub = check_settings(mv, p, mw, question_types=question_types, allow_models=False)
                out["models"][spec.id] = sub
    return out


def _check_values(src: dict, out: dict, p: Problems, where: str):
    rules = {"act_threshold": (0, 1, True, "a number above 0 and at most 1"),
             "temperature": (0, 20, True, "a number above 0 and at most 20 (1 means no calibration)"),
             "multi_threshold": (0, 1, False, "a number between 0 and 1")}
    for k, (lo, hi, hi_incl, say) in rules.items():
        if k not in src or src[k] is None:
            continue
        v = src[k]
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > lo and (v <= hi if hi_incl else v < hi)
        if not ok:
            p.add("invalid_field", f"{where}.{k}", f"{k} is {say}; got {json.dumps(v)}.")
        else:
            out[k] = v


# ----------------------------------------------------------------------------------------------------------------------
# Variables: validation and substitution (section 4.1)


def _type_name(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "a boolean"
    if isinstance(v, int):
        return "an integer"
    if isinstance(v, float):
        return "a number"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "a list"
    return "an object"


def describe(spec: dict) -> str:
    t = spec["type"]
    if t == "string":
        if "enum" in spec:
            return "one of " + ", ".join(spec["enum"])
        if spec.get("format"):
            return f"a {spec['format']} string"
        return f"a string of up to {spec.get('max_length', STRING_DEFAULT_MAX):,} characters"
    if t in MEDIA_TYPES:
        return f"an {t}: a file id (file_...) or a data: URL"
    if t == "options":
        return "options: {\"name\": \"description\"} or [\"name\", ...]"
    if t == "json":
        return "any JSON value"
    return {"integer": "a whole number", "number": "a number", "boolean": "true or false"}[t]


def _check_value(name: str, spec: dict, v, p: Problems, where: str | None = None):
    where = where or param_path("variables", name)
    t = spec["type"]

    def bad(msg):
        p.add("invalid_variable", where, f"{name} must be {msg}; got {_type_name(v) if not isinstance(v, str) else repr(v[:60])}.")

    if t == "string":
        if not isinstance(v, str):
            return bad(describe(spec))
        mx = min(spec.get("max_length", STRING_DEFAULT_MAX), STRING_PLATFORM_MAX)
        if len(v) > mx:
            return p.add("invalid_variable", where, f"{name} allows at most {mx:,} characters; got {len(v):,}.")
        if len(v) < spec.get("min_length", 0):
            return p.add("invalid_variable", where, f"{name} needs at least {spec['min_length']} characters.")
        if "enum" in spec and v not in spec["enum"]:
            return p.add("invalid_variable", where, f"{name} must be one of {', '.join(spec['enum'])}; got {v!r}.")
        if "pattern" in spec and not re.search(spec["pattern"], v):
            return p.add("invalid_variable", where, f"{name} does not match the pattern {spec['pattern']}.")
        if spec.get("format"):
            try:
                (_dt.date if spec["format"] == "date" else _dt.datetime).fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return p.add("invalid_variable", where, f"{name} must be a {spec['format']} in ISO 8601 form; got {v!r}.")
    elif t == "integer":
        if not isinstance(v, int) or isinstance(v, bool):
            return bad("a whole number")
        _range(name, spec, v, p, where)
        if "enum" in spec and v not in spec["enum"]:
            p.add("invalid_variable", where, f"{name} must be one of {', '.join(map(str, spec['enum']))}; got {v}.")
    elif t == "number":
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            return bad("a number")
        _range(name, spec, v, p, where)
    elif t == "boolean":
        if not isinstance(v, bool):
            return bad("true or false")
    elif t == "json":
        size = len(canonical(v).encode())
        if size > spec.get("max_bytes", JSON_DEFAULT_MAX):
            return p.add("invalid_variable", where, f"{name} is {size:,} bytes; the limit is {spec.get('max_bytes', JSON_DEFAULT_MAX):,}.")
        if spec.get("schema"):
            try:
                import jsonschema
                jsonschema.validate(v, spec["schema"])
            except ImportError:
                pass
            except Exception as e:   # noqa: BLE001 - jsonschema's ValidationError or SchemaError
                p.add("invalid_variable", where, f"{name} does not match its schema: {getattr(e, 'message', e)}")
    elif t == "options":
        names = list(v) if isinstance(v, dict) else v if isinstance(v, list) else None
        if names is None or not all(isinstance(x, str) and x.strip() and len(x) <= 200 for x in names):
            return bad("options: an object of name to description, or a list of names (each 1 to 200 characters)")
        if isinstance(v, list) and len(set(v)) != len(v):
            return p.add("invalid_variable", where, f"{name} has repeated option names.")
        if not spec.get("min_items", 1) <= len(names) <= min(spec.get("max_items", 1000), 1000):
            return p.add("invalid_variable", where, f"{name} needs {spec.get('min_items', 1)} to "
                                                    f"{min(spec.get('max_items', 1000), 1000)} options; got {len(names)}.")
    elif t in MEDIA_TYPES:
        if not isinstance(v, str) or not (v.startswith("file_") or v.startswith("data:")):
            return bad(describe(spec))


def _range(name, spec, v, p, where):
    if "minimum" in spec and v < spec["minimum"]:
        p.add("invalid_variable", where, f"{name} must be at least {spec['minimum']}; got {v}.")
    if "maximum" in spec and v > spec["maximum"]:
        p.add("invalid_variable", where, f"{name} must be at most {spec['maximum']}; got {v}.")


def as_text(spec: dict, v) -> str:
    t = spec["type"]
    if v is None:
        return ""
    if t == "string":
        return v
    if t in ("integer", "number", "boolean"):
        return json.dumps(v)
    if t == "json":
        return v if isinstance(v, str) else render_state(v)
    return ""


def _substitute(template, values: dict, variables: dict, mask: set[str]):
    """Single pass: whole-value placeholders keep their type; embedded ones render as text. Unset optional values
    drop their key (whole value) or render as "" (embedded)."""
    if isinstance(template, str):
        m = WHOLE.match(template)
        if m and m.group(1) in variables:
            name = m.group(1)
            if name not in values:
                return MISSING
            if name in mask:
                return f"[redacted:{name}]"
            return copy.deepcopy(values[name])

        def one(mt):
            if mt.group(1):                       # \{{ is a literal {{
                return mt.group(0)[1:]
            name = mt.group(2)
            if name not in variables:
                return mt.group(0)
            if name in mask and name in values:
                return f"[redacted:{name}]"
            return as_text(variables[name], values.get(name))
        return PLACEHOLDER.sub(one, template)
    if isinstance(template, dict):
        out = {}
        for k, x in template.items():
            r = _substitute(x, values, variables, mask)
            if r is not MISSING:
                out[k] = r
        return out
    if isinstance(template, list):
        return [r for r in (_substitute(x, values, variables, mask) for x in template) if r is not MISSING]
    return template


def secret_hash(secret: bytes, value) -> str:
    return "hmac-sha256:" + hmac.new(secret, canonical(value).encode(), hashlib.sha256).hexdigest()


@dataclass
class Rendered:
    values: dict                 # validated values after defaults (real, including sensitive ones)
    stored_variables: dict       # what history keeps: sensitive values as {"$redacted": hmac}
    secrets: dict                # variable -> hmac, for erasure by value
    state: Any                   # what the model reads
    stored_state: Any            # the same with sensitive values as [redacted:<name>]
    media: list[dict]            # [{"variable", "type", "value"}] in declaration order
    questions: dict              # the template's questions with placeholders filled
    dynamic: set                 # questions whose options came from an options variable


def render(defn: dict, variables_in, state_in, secret: bytes, p: Problems) -> Rendered | None:
    """Validate a request's variables (or raw state) against a version and fill in the templates."""
    variables = defn["variables"]
    values: dict = {}
    if not variables:
        if variables_in:
            p.add("variables_need_template", "variables",
                  "This template declares no variables; send the situation as `state`.")
        if state_in is None:
            p.add("state_required", "state", "This template takes its state from each request: send `state` "
                                             "(text, an object or a list).")
            return None
        if not isinstance(state_in, (str, dict, list)):
            p.add("invalid_field", "state", "state is text, an object or a list.")
            return None
        if state_in in ("", {}, []):
            p.add("empty_state", "state", "The state is empty; describe the situation to decide about.")
        return Rendered({}, {}, {}, state_in, state_in, [], render_questions(defn["questions"], {}, variables),
                        {k for k, q in defn["questions"].items() if is_dynamic(q)})
    if state_in is not None:
        p.add("state_not_allowed", "state", "This template builds the state from its variables; send `variables` "
                                            "instead of `state`.")
    if variables_in is None:
        variables_in = {}
    if not isinstance(variables_in, dict):
        p.add("invalid_field", "variables", "variables is an object of name to value.")
        return None
    for name in variables_in:
        if name not in variables:
            p.add("unknown_variable", param_path("variables", name),
                  f"This template has no variable '{name}'.{did_you_mean(name, variables)}")
    for name, spec in variables.items():
        v = variables_in.get(name)
        if v is None and spec.get("default") is not None:
            v = copy.deepcopy(spec["default"])
        if v is None:
            if spec.get("required"):
                p.add("missing_variable", param_path("variables", name), f"{name} is required ({describe(spec)}).")
            continue
        before = len(p.items)
        _check_value(name, spec, v, p)
        if len(p.items) == before:
            values[name] = v
    sensitive = {n for n, s in variables.items() if s.get("sensitive")}
    stored, secrets = {}, {}
    for name, v in values.items():
        if name in sensitive and variables[name]["type"] not in MEDIA_TYPES:
            h = secret_hash(secret, v)
            stored[name] = {"$redacted": h}
            secrets[name] = h
        else:
            stored[name] = v
    text_vars = {n: s for n, s in variables.items() if s["type"] not in MEDIA_TYPES + ("options",)}
    if defn["state"] is None:
        state = {n: values[n] for n in text_vars if n in values}
        stored_state = {n: (f"[redacted:{n}]" if n in sensitive else values[n]) for n in text_vars if n in values}
    else:
        state = _substitute(defn["state"], values, variables, set())
        stored_state = _substitute(defn["state"], values, variables, sensitive)
        if state is MISSING:
            state, stored_state = "", ""
    if state in ("", {}, []) or (isinstance(state, str) and not state.strip()):
        p.add("empty_state", "variables", "With these variables the state is empty; send at least one of: "
                                          + ", ".join(text_vars) + ".")
    media = [{"variable": n, "type": s["type"], "value": values[n]} for n, s in variables.items()
             if s["type"] in MEDIA_TYPES and n in values]
    questions = render_questions(defn["questions"], values, variables, p)
    dynamic = {k for k, q in defn["questions"].items() if is_dynamic(q)}
    return Rendered(values, stored, secrets, state, stored_state, media, questions, dynamic)


def render_questions(questions: dict, values: dict, variables: dict, p: Problems | None = None) -> dict:
    out = {}
    for key, q in questions.items():
        r = copy.deepcopy(q)
        if "instructions" in r:
            v = _substitute(r["instructions"], values, variables, set())
            r["instructions"] = "" if v is MISSING else v
        c = r.get("criteria")
        if isinstance(c, str):
            m = WHOLE.match(c)
            name = m.group(1) if m else None
            if name in values:
                r["criteria"] = copy.deepcopy(values[name])
            elif p is not None:
                p.add("missing_variable", param_path("variables", name or "?"),
                      f"Question '{key}' takes its options from '{name}'; send it (a list of names or an object of name to description).")
        elif isinstance(c, dict):
            r["criteria"] = {k: ("" if (x := _substitute(d, values, variables, set())) is MISSING else x) for k, d in c.items()}
        elif isinstance(c, list) and q["type"] == "score":
            r["criteria"] = [("" if (x := _substitute(d, values, variables, set())) is MISSING else x) for d in c]
        out[key] = r
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Questions: extension, skipping (section 4.2)


@dataclass
class Merged:
    questions: dict
    origins: dict
    extensions: dict
    dynamic: set


def merge_questions(template_questions: dict, dynamic: set, ext_rules: dict, extras, add_options, skip,
                    p: Problems) -> Merged:
    skip = skip or []
    add_options = add_options or {}
    extras = extras or {}
    if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
        p.add("invalid_field", "skip", "skip is a list of question keys.")
        skip = []
    if not isinstance(add_options, dict):
        p.add("invalid_field", "add_options", "add_options is an object of question key to new options.")
        add_options = {}
    if not isinstance(extras, dict):
        p.add("invalid_field", "questions", "questions is an object of key to question.")
        extras = {}
    allowed_skip = ext_rules.get("skip") or []
    for i, k in enumerate(skip):
        if k not in template_questions:
            p.add("unknown_question", f"skip[{i}]", f"skip names '{k}', which is not a question of this template."
                                                    f"{did_you_mean(k, template_questions)}")
        elif allowed_skip is not True and k not in allowed_skip:
            p.add("skip_not_allowed", f"skip[{i}]", f"This template does not let callers skip '{k}'. Its extensions.skip "
                                                    "lists the questions that may be skipped.")
    out: dict = {}
    origins: dict = {}
    added: dict = {}
    for k, q in template_questions.items():
        if k in skip:
            continue
        out[k] = copy.deepcopy(q)
        origins[k] = "template"
    allowed_opts = ext_rules.get("options") or []
    for k, new in add_options.items():
        where = param_path("add_options", k)
        if k not in template_questions:
            p.add("unknown_question", where, f"add_options names '{k}', which is not a question of this template."
                                             f"{did_you_mean(k, template_questions)}")
            continue
        if k in skip:
            p.add("invalid_field", where, f"'{k}' is skipped in this request, so options cannot be added to it.")
            continue
        q = out[k]
        if q["type"] in ("score", "noul"):
            p.add("options_not_extensible", where, f"'{k}' is a {q['type']} question; a new level would change what it means.")
            continue
        if allowed_opts is not True and k not in allowed_opts:
            p.add("extension_not_allowed", where, f"This template does not let callers add options to '{k}'. List it in "
                                                  "extensions.options to allow it.")
            continue
        if q["type"] == "number":
            if not isinstance(new, list) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in new):
                p.add("invalid_field", where, f"'{k}' is a number question: add_options takes a list of numbers.")
                continue
            have = set(number_values(q))
            clash = [x for x in new if float(x) in have]
            if clash:
                p.add("option_conflict", where, f"'{k}' already has the value(s) {', '.join(map(str, clash))}.")
                continue
            c = q["criteria"]
            q["criteria"] = ({**c, **{str(x): None for x in new}} if isinstance(c, dict) else list(c) + list(new))
            added[k] = [str(x) for x in new]
            origins[k] = "extended"
            continue
        names = list(new) if isinstance(new, dict) else new if isinstance(new, list) else None
        if names is None or not all(isinstance(x, str) and x for x in names):
            p.add("invalid_field", where, f"add_options.{k} is an object of new option name to description, or a list of names.")
            continue
        have = option_names(q) or []
        clash = [x for x in names if x in have]
        if clash:
            p.add("option_conflict", where, f"'{k}' already has the option(s) {', '.join(clash)}.")
            continue
        c = q["criteria"]
        if isinstance(c, list):
            c = {str(x): None for x in c}
        q["criteria"] = {**c, **(new if isinstance(new, dict) else {x: None for x in new})}
        added[k] = names
        origins[k] = "extended"
    if extras:
        if not ext_rules.get("questions", True):
            p.add("extension_not_allowed", "questions", "This template does not accept extra questions "
                                                        "(extensions.questions is false).")
        elif len(extras) > ext_rules.get("max_questions", 16):
            p.add("too_many_extra_questions", "questions", f"This template accepts at most {ext_rules.get('max_questions', 16)} "
                                                           f"extra questions; got {len(extras)}.")
        for k, q in extras.items():
            where = param_path("questions", k)
            if k in template_questions:
                p.add("question_conflict", where, f"'{k}' is already a question of this template. Use add_options to add "
                                                  "options to it, or save a new version to change it.")
                continue
            try:
                _QUESTION.validate_python(q)
            except ValidationError as e:
                for x in e.errors(include_url=False):
                    p.add("invalid_field", param_path("questions", k, *[str(l) for l in x["loc"][1:]]), f"Question '{k}': {x['msg']}")
                continue
            out[k] = copy.deepcopy(q)
            origins[k] = "extra"
    if not out:
        p.add("no_questions", "skip", "At least one question must remain after skipping.")
    if len(out) > MAX_QUESTIONS:
        p.add("invalid_field", "questions", f"A decision has at most {MAX_QUESTIONS} questions; this one has {len(out)}.")
    ext = {"questions": [k for k, o in origins.items() if o == "extra"], "options": added,
           "skipped": [k for k in template_questions if k in skip]}
    return Merged(out, origins, ext, {k for k in dynamic if k in out})


# ----------------------------------------------------------------------------------------------------------------------
# Settings per question (section 4.3)


def resolve_settings(questions: dict, model: str | None, tmpl: dict | None, req: dict | None,
                     default_act: float) -> tuple[dict, dict]:
    """-> (per question {act_threshold, temperature, multi_threshold}, the decision's settings object with sources)."""
    tmpl = tmpl or {}
    req = req or {}
    tm = (tmpl.get("models") or {}).get(model or "", {}) if model else {}

    def ladder(q: str | None, key: str):
        layers = []
        if q is not None:
            layers.append(((req.get("questions") or {}).get(q, {}), "request.questions"))
        layers.append((req, "request"))
        if q is not None:
            layers.append(((tm.get("questions") or {}).get(q, {}), f"template.models.{model}.questions"))
        layers.append((tm, f"template.models.{model}"))
        if q is not None:
            layers.append(((tmpl.get("questions") or {}).get(q, {}), "template.questions"))
        layers.append((tmpl, "template"))
        for src, name in layers:
            if isinstance(src, dict) and src.get(key) is not None:
                return src[key], name
        if key == "act_threshold":
            return default_act, "studio"
        if key == "temperature":
            return 1.0, "studio"
        return None, "question"

    base_at, src_at = ladder(None, "act_threshold")
    base_t, src_t = ladder(None, "temperature")
    per: dict = {}
    listed: dict = {}
    sources = {"act_threshold": src_at, "temperature": src_t}
    for k, q in questions.items():
        at, s_at = ladder(k, "act_threshold")
        t, s_t = ladder(k, "temperature")
        mt, s_mt = ladder(k, "multi_threshold") if q.get("type") == "multi" else (None, "question")
        if mt is None and q.get("type") == "multi":
            mt = q.get("threshold", 0.5)
        per[k] = {"act_threshold": at, "temperature": t, "multi_threshold": mt}
        for key, val, s in (("act_threshold", at, s_at), ("temperature", t, s_t), ("multi_threshold", mt, s_mt)):
            if s.endswith("questions") and (key != "multi_threshold" or q.get("type") == "multi"):
                listed.setdefault(k, {})[key] = val
                sources[f"questions.{k}.{key}"] = s
    return per, {"act_threshold": base_at, "temperature": base_t, "questions": listed, "sources": sources}


def worker_settings(per: dict, settings_out: dict) -> dict:
    """The settings the model worker applies: one base temperature plus per-question overrides."""
    base = settings_out["temperature"]
    qs = {}
    for k, s in per.items():
        e = {}
        if s["temperature"] != base:
            e["temperature"] = s["temperature"]
        if s.get("multi_threshold") is not None:
            e["multi_threshold"] = s["multi_threshold"]
        if e:
            qs[k] = e
    return {"temperature": None if base == 1.0 else base, "questions": qs}


# ----------------------------------------------------------------------------------------------------------------------
# Model limits (checked before any model loads)


def expanded(questions: dict) -> list[tuple[str, str, int]]:
    """[(question key, primitive type, option count)] as the worker expands them."""
    out = []
    for k, q in questions.items():
        t = q.get("type")
        c = q.get("criteria")
        if t == "multi":
            n = len(c) if isinstance(c, (dict, list)) else 0
            out += [(k, "noul", 2)] * n
        elif t == "rank":
            out.append((k, "choice", len(c) if isinstance(c, (dict, list)) else 0))
        elif t == "number":
            out.append((k, "score", len(number_values(q))))
        elif t == "noul":
            out.append((k, "noul", 2))
        else:
            out.append((k, t, len(c) if isinstance(c, (dict, list)) else 0))
    return out


def model_problems(questions: dict, media_types: list[str], spec, required_media: list[str] | None = None) -> list[dict]:
    probs = []
    seen = set()
    ex = expanded(questions)
    for k, t, n in ex:
        if t not in spec.types and (k, "type") not in seen:
            seen.add((k, "type"))
            probs.append({"code": "type_not_supported", "param": param_path("questions", k),
                          "message": f"{spec.name} does not answer {t} questions ('{k}')."})
        if n > spec.max_options and (k, "opts") not in seen:
            seen.add((k, "opts"))
            probs.append({"code": "too_many_options", "param": param_path("questions", k),
                          "message": f"'{k}' has {n} options; {spec.name} accepts at most {spec.max_options}."})
    if len(ex) > spec.max_questions:
        probs.append({"code": "too_many_questions", "param": "questions",
                      "message": f"{len(ex)} questions after expanding pick-all-that-apply options; {spec.name} answers at "
                                 f"most {spec.max_questions}."})
    for t in list(dict.fromkeys(list(media_types) + list(required_media or []))):
        if t not in spec.modalities:
            probs.append({"code": "modality_not_supported", "param": "media",
                          "message": f"{spec.name} cannot read {t} input."})
    return probs


# ----------------------------------------------------------------------------------------------------------------------
# Versions: comparability, change class, diff (section 4.4)


def _text_signature(q: dict) -> str:
    c = q.get("criteria")
    t = q.get("type")
    if t in OPTION_TYPES and isinstance(c, dict):
        descs = [c[k] for k in c]
    elif t == "noul":
        descs = c or {}
    elif t == "score":
        descs = c
    elif t == "number" and isinstance(c, dict):
        descs = list(c.values())
    else:
        descs = None
    return sha({"instructions": q.get("instructions"), "descriptions": descs, "order": option_names(q) if t in OPTION_TYPES else None,
                "unit": q.get("unit"), "threshold": q.get("threshold")})


def options_key(q: dict) -> str:
    t = q.get("type")
    if t in OPTION_TYPES:
        names = option_names(q)
        return q["criteria"] if names is None else canonical(sorted(names))
    if t == "score":
        return f"levels:{len(q.get('criteria') or [])}"
    if t == "number":
        return canonical(number_values(q))
    return ""


def compare_question(a: dict | None, b: dict | None) -> dict:
    if a is None:
        return {"comparability": "added"}
    if b is None:
        return {"comparability": "removed"}
    if a.get("type") != b.get("type"):
        return {"comparability": "incomparable", "reason": f"type changed from {a.get('type')} to {b.get('type')}"}
    t = a["type"]
    if is_dynamic(a) != is_dynamic(b) or (is_dynamic(a) and a["criteria"] != b["criteria"]):
        return {"comparability": "incomparable", "reason": "the options now come from a variable" if is_dynamic(b) else
                "the options no longer come from a variable"}
    if t == "score" and len(a.get("criteria") or []) != len(b.get("criteria") or []):
        return {"comparability": "incomparable", "reason": "the number of levels changed"}
    if options_key(a) != options_key(b):
        if t == "number":
            va, vb = number_values(a), number_values(b)
            return {"comparability": "options_changed", "added_options": [str(x) for x in vb if x not in va],
                    "removed_options": [str(x) for x in va if x not in vb]}
        na, nb = option_names(a) or [], option_names(b) or []
        return {"comparability": "options_changed", "added_options": [x for x in nb if x not in na],
                "removed_options": [x for x in na if x not in nb]}
    if canonical(a) == canonical(b):
        return {"comparability": "identical"}
    return {"comparability": "text_changed"}


def classify_change(prev: dict | None, new: dict) -> dict:
    """The change record stored with a version: class, comparability per question and plain-language lines."""
    if prev is None:
        return {"from": None, "class": "created", "breaking_for_callers": False,
                "questions": {k: "added" for k in new["questions"]}, "summary": ["First version"]}
    summary: list[str] = []
    breaking = extended = wording = settings_only = False
    callers_break = False
    qa, qb = prev["questions"], new["questions"]
    comp = {}
    for k in list(qa) + [k for k in qb if k not in qa]:
        c = compare_question(qa.get(k), qb.get(k))
        comp[k] = c["comparability"]
        cls = c["comparability"]
        if cls == "added":
            extended = True
            summary.append(f"{k}: question added")
        elif cls == "removed":
            breaking = True
            summary.append(f"{k}: question removed")
            ext = prev.get("extensions") or {}
            if ext.get("skip") is True or k in (ext.get("skip") or []) or ext.get("options") is True or k in (ext.get("options") or []):
                callers_break = True
        elif cls == "incomparable":
            breaking = True
            summary.append(f"{k}: {c.get('reason', 'changed incompatibly')}")
        elif cls == "options_changed":
            if c.get("removed_options"):
                breaking = True
            else:
                extended = True
            for o in c.get("added_options", []):
                summary.append(f"{k}: option '{o}' added")
            for o in c.get("removed_options", []):
                summary.append(f"{k}: option '{o}' removed")
        elif cls == "text_changed":
            wording = True
            summary.append(f"{k}: wording changed")
    va, vb = prev["variables"], new["variables"]
    for n, s in vb.items():
        if n not in va:
            if s.get("required"):
                breaking = callers_break = True
                summary.append(f"variable {n} added (required)")
            else:
                extended = True
                summary.append(f"variable {n} added")
        elif va[n]["type"] != s["type"]:
            breaking = callers_break = True
            summary.append(f"variable {n}: type changed from {va[n]['type']} to {s['type']}")
        elif canonical(va[n]) != canonical(s):
            if s.get("required") and not va[n].get("required"):
                breaking = callers_break = True
                summary.append(f"variable {n}: now required")
            else:
                wording = True
                summary.append(f"variable {n}: constraints changed")
    for n in va:
        if n not in vb:
            breaking = callers_break = True
            summary.append(f"variable {n} removed")
    if canonical(prev["state"]) != canonical(new["state"]):
        wording = True
        summary.append("state template changed")
    removed_mods = [m for m in prev["modalities"] if m not in new["modalities"]]
    if removed_mods:
        breaking = callers_break = True
        summary.append("modalities removed: " + ", ".join(removed_mods))
    elif prev["modalities"] != new["modalities"]:
        extended = True
        summary.append("modalities added: " + ", ".join(m for m in new["modalities"] if m not in prev["modalities"]))
    ea, eb = prev["extensions"], new["extensions"]
    if canonical(ea) != canonical(eb):
        settings_only = True
        narrower = (ea["questions"] and not eb["questions"]) or eb["max_questions"] < ea["max_questions"]
        for k in ("options", "skip"):
            if ea[k] is True and eb[k] is not True:
                narrower = True
            elif isinstance(ea[k], list) and isinstance(eb[k], list) and set(ea[k]) - set(eb[k]):
                narrower = True
        if narrower:
            callers_break = True
        summary.append("extensions changed" + (" (callers may now be refused)" if narrower else ""))
    if prev.get("model") != new.get("model"):
        settings_only = True
        summary.append(f"default model: {prev.get('model') or 'most recently loaded'} -> {new.get('model') or 'most recently loaded'}")
    if canonical(prev.get("settings")) != canonical(new.get("settings")):
        settings_only = True
        for path, (a, b) in _changed_paths(prev.get("settings") or {}, new.get("settings") or {}).items():
            summary.append(f"settings{path.replace('/', '.')}: {json.dumps(a)} -> {json.dumps(b)}")
    cls = ("breaking" if breaking else "extended" if extended else "wording" if wording
           else "settings_only" if settings_only else "unchanged")
    return {"from": None, "class": cls, "breaking_for_callers": callers_break, "questions": comp, "summary": summary}


def _changed_paths(a, b, path: str = "") -> dict:
    out = {}
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            out.update(_changed_paths(a.get(k, None), b.get(k, None), f"{path}/{k}"))
        return out
    if canonical(a) != canonical(b):
        out[path or "/"] = (a, b)
    return out


def _ptr(k) -> str:
    return str(k).replace("~", "~0").replace("/", "~1")


def json_patch(a, b, path: str = "") -> list[dict]:
    """RFC 6902-style operations turning a into b (objects recurse; arrays and values are replaced whole)."""
    if isinstance(a, dict) and isinstance(b, dict):
        ops = []
        for k in a:
            if k not in b:
                ops.append({"op": "remove", "path": f"{path}/{_ptr(k)}"})
        for k in b:
            if k not in a:
                ops.append({"op": "add", "path": f"{path}/{_ptr(k)}", "value": b[k]})
            else:
                ops += json_patch(a[k], b[k], f"{path}/{_ptr(k)}")
        return ops
    if canonical(a) == canonical(b):
        return []
    return [{"op": "replace", "path": path or "/", "value": b}]


def diff(a: dict, b: dict) -> dict:
    qs = {}
    for k in list(a["questions"]) + [k for k in b["questions"] if k not in a["questions"]]:
        qs[k] = compare_question(a["questions"].get(k), b["questions"].get(k))
    ch = classify_change(a, b)
    return {"class": ch["class"], "breaking_for_callers": ch["breaking_for_callers"], "questions": qs,
            "variables": {p: {"from": x, "to": y} for p, (x, y) in _changed_paths(a["variables"], b["variables"]).items()},
            "settings": {p: {"from": x, "to": y} for p, (x, y) in _changed_paths(a.get("settings") or {}, b.get("settings") or {}).items()},
            "model": {"from": a.get("model"), "to": b.get("model")},
            "modalities": {"from": a["modalities"], "to": b["modalities"]},
            "summary": ch["summary"],
            "changes": json_patch({k: a.get(k) for k in DEF_FIELDS}, {k: b.get(k) for k in DEF_FIELDS})}


def question_index(questions: dict) -> list[dict]:
    """Rows for template_questions: one per question, with the hashes comparability uses."""
    return [{"key": k, "position": i, "type": q["type"], "question_hash": sha(q), "text_hash": _text_signature(q),
             "options_key": options_key(q)} for i, (k, q) in enumerate(questions.items())]


# ----------------------------------------------------------------------------------------------------------------------
# JSON Schema of the variables (forms, SDK generators)


def schema(defn: dict, ref: str) -> dict:
    props, required = {}, []
    for n, s in defn["variables"].items():
        t = s["type"]
        d: dict = {}
        if t == "string":
            d = {"type": "string"}
            for a, b in (("min_length", "minLength"), ("max_length", "maxLength"), ("pattern", "pattern"), ("enum", "enum"), ("format", "format")):
                if a in s:
                    d[b] = s[a]
        elif t in ("integer", "number"):
            d = {"type": t}
            for a in ("minimum", "maximum", "enum"):
                if a in s:
                    d[a] = s[a]
        elif t == "boolean":
            d = {"type": "boolean"}
        elif t == "json":
            d = dict(s.get("schema") or {})
        elif t == "options":
            d = {"oneOf": [{"type": "object", "additionalProperties": {"type": ["string", "null"]}},
                           {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 200}, "uniqueItems": True}],
                 "x-basal-type": "options"}
        else:
            d = {"type": "string", "pattern": f"^(file_[0-9A-HJKMNP-TV-Z]{{26}}|data:{t}/)", "x-basal-type": t}
        if s.get("description"):
            d["description"] = s["description"]
        if s.get("default") is not None:
            d["default"] = s["default"]
        if s.get("example") is not None:
            d["examples"] = [s["example"]]
        if s.get("sensitive"):
            d["x-basal-sensitive"] = True
        props[n] = d
        if s.get("required"):
            required.append(n)
    out = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": f"{ref} variables", "type": "object",
           "additionalProperties": False, "required": required, "properties": props,
           "x-basal-template": ref, "x-basal-questions": list(defn["questions"]), "x-basal-extensions": defn["extensions"]}
    if not defn["variables"]:
        out["description"] = "This template takes its state from each request: send `state` (text, an object or a list)."
    return out


def compatibility(defn: dict, status_of) -> list[dict]:
    """Every catalog model checked against a version. status_of(spec) -> loaded | loading | downloaded | not_downloaded."""
    out = []
    required_media = _required_media(defn["variables"])
    optional_media = [v["type"] for v in defn["variables"].values() if v["type"] in MEDIA_TYPES and not v.get("required")]
    for spec in CATALOG:
        probs = model_problems(defn["questions"], [], spec, required_media)
        notes = []
        for n, v in defn["variables"].items():
            if v["type"] in optional_media and v["type"] not in spec.modalities:
                notes.append(f"Cannot read the optional {v['type']} variable '{n}'; decisions that send it will be refused.")
            if v["type"] == "string" and "max_length" in v and v["max_length"] / 4 > spec.context_tokens:
                probs.append({"code": "context_too_small", "param": param_path("variables", n),
                              "message": f"{n} allows {v['max_length']:,} characters (about {v['max_length'] // 4:,} tokens); "
                                         f"{spec.name} reads {spec.context_tokens:,} tokens."})
        if any(is_dynamic(q) for q in defn["questions"].values()):
            notes.append(f"Options that come from a variable must stay within {spec.max_options} per question.")
        out.append({"model": spec.id, "name": spec.name, "status": status_of(spec), "ok": not probs, "problems": probs, "notes": notes})
    return out
