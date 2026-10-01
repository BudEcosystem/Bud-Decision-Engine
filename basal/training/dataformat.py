"""The standard training data format, importers, validation and splits. Pure Python: no PyTorch.

The standard format is the studio's own request plus the right answers, one example per line (JSON Lines):

    {"state": "text, or an object, or a list",
     "questions": {"team":   {"type": "choice", "instructions": "Which team should handle this?",
                              "criteria": {"billing": "charges and refunds", "shipping": "deliveries"}},
                   "urgent": {"type": "noul", "instructions": "Does this need attention today?"}},
     "answers":   {"team": "billing", "urgent": true},
     "id": "optional", "group": "optional: examples that share one situation", "media": [optional images/audio]}

Answers: a choice's option name, true/false for a yes/no question, a scale's level (its number from 0, or its text),
a list of names for pick-all-that-apply, the top option for rank, a number for estimate-a-number, or a soft label
{"probabilities": {...}}. Questions may be written once for the whole file (`import_examples(..., questions=...)`)
and left out of each line.

People who know nothing about any of this can instead upload a plain table (CSV, TSV, JSON Lines, or "text<TAB>label"
lines, the formats the Evaluate page reads): one column holds the text, each other column is an answer. The importer
works out the questions, their type and their options, and says what it did in plain words.

Everything downstream (every model family) reads `Example`: the validated request, its normalised primitive questions
(`contract.normalise`) and one probability target per primitive question, in that question's key order.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..contract import Q, SystemOneRequest, normalise, render

TEXT_NAMES = ("text", "state", "input", "message", "content", "body", "ticket", "email", "review", "comment",
              "description", "prompt", "document", "sentence", "utterance", "query", "question")
ANSWER_NAMES = ("label", "answer", "labels", "class", "category", "target", "gold", "correct", "y", "output", "outcome")
YES = {"yes", "y", "true", "t", "1", "1.0"}
NO = {"no", "n", "false", "f", "0", "0.0"}
MAX_CHOICE_OPTIONS = 64       # a table column with more distinct answers than this is not a decision question
MIN_LABELLED = 30             # below this the trainer refuses: there is nothing reliable to learn from or test on


# ----------------------------------------------------------------------------------------------------------------
# Data classes


@dataclass
class Example:
    """One situation with its questions and, per primitive question, a target distribution over q.keys (None when that
    question has no answer in this example)."""
    index: int
    request: SystemOneRequest
    qs: list[Q]
    targets: list[list[float] | None]
    group: str
    record: dict                      # the canonical record (for storage and export)

    @property
    def labelled(self) -> int:
        return sum(t is not None for t in self.targets)

    def gold(self, i: int) -> int | None:
        t = self.targets[i]
        return None if t is None else max(range(len(t)), key=t.__getitem__)


@dataclass
class Problem:
    level: str                        # "error" (blocks training) | "warning" | "info"
    message: str                      # one plain-language sentence
    rows: list[int] = field(default_factory=list)   # 1-based line numbers, when it applies to specific examples

    def public(self) -> dict:
        return {"level": self.level, "message": self.message, "rows": self.rows[:20], "count": len(self.rows)}


@dataclass
class ImportResult:
    examples: list[Example]
    questions: dict[str, dict]        # the question definitions used (after inference)
    problems: list[Problem]
    detected: str                     # what the importer understood, in plain words
    fmt: str                          # "standard" | "table" | "typed-decisions" | "lines"

    @property
    def usable(self) -> bool:
        return not any(p.level == "error" for p in self.problems)

    def report(self) -> dict:
        per_q: dict[str, dict] = {}
        for qid, q in self.questions.items():
            per_q[qid] = {"type": q["type"], "instructions": render(q.get("instructions")) or None,
                          "options": _option_names(q), "counts": {}, "labelled": 0}
        for ex in self.examples:
            for q, t in zip(ex.qs, ex.targets):
                if t is None:
                    continue
                key = q.parent or q.id
                if key not in per_q:
                    continue
                name = q.meta.get("option") if q.role == "multi" else q.labels[max(range(len(t)), key=t.__getitem__)]
                if q.role == "multi":
                    if t[1] >= 0.5:
                        per_q[key]["counts"][name] = per_q[key]["counts"].get(name, 0) + 1
                else:
                    per_q[key]["counts"][str(name)] = per_q[key]["counts"].get(str(name), 0) + 1
                per_q[key]["labelled"] += 1
        labelled = sum(ex.labelled for ex in self.examples)
        return {"format": self.fmt, "detected": self.detected, "examples": len(self.examples),
                "labelled_answers": labelled, "questions": per_q, "usable": self.usable,
                "problems": [p.public() for p in self.problems]}


def _option_names(q: dict) -> list[str]:
    c = q.get("criteria")
    if q["type"] == "noul":
        return ["no", "yes"]
    if isinstance(c, dict):
        return [str(k) for k in c]
    if isinstance(c, list):
        return [render(x) for x in c]
    return []


# ----------------------------------------------------------------------------------------------------------------
# Answer -> target distribution over a primitive question's keys


def _canon(x: Any) -> str:
    return re.sub(r"\s+", " ", str(x)).strip().casefold()


def _soft(q: Q, probs: dict) -> list[float] | None:
    out = []
    for k, lab, desc in zip(q.keys, q.labels, q.descriptions):
        v = None
        for cand in (k, lab, desc):
            if cand is None:
                continue
            for pk, pv in probs.items():
                if _canon(pk) == _canon(cand):
                    v = pv
                    break
            if v is not None:
                break
        if v is None and q.type == "noul":
            aliases = {"true": YES, "false": NO}[k]
            v = next((pv for pk, pv in probs.items() if _canon(pk) in aliases), None)
        out.append(float(v) if isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 else 0.0)
    s = sum(out)
    return [x / s for x in out] if s > 0 else None


def _onehot(n: int, i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(n)]


def target_for(q: Q, answer: Any) -> list[float] | None:
    """The target distribution for primitive question `q` given the example's answer to its client question.
    Returns None when the answer is missing; raises ValueError (plain words) when it can't be understood."""
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        return None
    if isinstance(answer, dict) and "probabilities" in answer:
        if q.role == "multi":
            p = answer["probabilities"].get(q.meta["option"])
            return None if p is None else [1 - float(p), float(p)]
        t = _soft(q, answer["probabilities"])
        if t is None:
            raise ValueError("its probabilities don't name any of the options")
        return t
    if isinstance(answer, dict) and "label" in answer:          # typed-decisions style {"label": ...}
        return target_for(q, answer["label"])
    n = len(q.keys)
    if q.role == "multi":
        opt = q.meta["option"]
        if isinstance(answer, dict):
            v = answer.get(opt)
            return None if v is None else _onehot(2, 1 if _truthy(v) else 0)
        chosen = answer if isinstance(answer, list) else [s for s in re.split(r"[;,|]", str(answer)) if s.strip()]
        return _onehot(2, 1 if _canon(opt) in {_canon(c) for c in chosen} else 0)
    if q.role == "rank":
        answer = answer[0] if isinstance(answer, list) and answer else answer
    if q.type == "noul":
        return _onehot(2, 1 if _truthy(answer) else 0)
    if q.role == "number":
        try:
            v = float(answer)
        except (TypeError, ValueError):
            raise ValueError(f"'{answer}' is not a number")
        vals = q.meta["values"]
        return _onehot(n, min(range(n), key=lambda i: abs(vals[i] - v)))
    # choice or score: match a label, a description or "label: description" first, then a key (a scale's keys are
    # 0..n-1 while its labels may be "1".."5": "3" means the level called 3)
    a = _canon(answer)
    for i, (lab, desc) in enumerate(zip(q.labels, q.descriptions)):
        if a == _canon(lab) or (desc and a in (_canon(desc), _canon(f"{lab}: {desc}"))):
            return _onehot(n, i)
    for i, k in enumerate(q.keys):
        if a == _canon(k):
            return _onehot(n, i)
    if q.type == "score":
        try:
            f = float(answer)
            if f.is_integer() and 0 <= int(f) < n:
                return _onehot(n, int(f))
        except (TypeError, ValueError):
            pass
    names = ", ".join(q.labels[:8]) + ("…" if n > 8 else "")
    raise ValueError(f"'{answer}' is not one of its options ({names})")


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = _canon(v)
    if s in YES:
        return True
    if s in NO:
        return False
    raise ValueError(f"'{v}' is not a yes or no")


# ----------------------------------------------------------------------------------------------------------------
# Parsing files


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_jsonish(text: str) -> list[dict] | None:
    s = text.strip()
    if not s:
        return []
    if s[0] == "[":
        try:
            v = json.loads(s)
            return v if isinstance(v, list) and all(isinstance(x, dict) for x in v) else None
        except ValueError:
            return None
    lines = [l for l in s.splitlines() if l.strip()]
    if lines and all(l.lstrip().startswith("{") for l in lines[: min(20, len(lines))]):
        out = []
        for l in lines:
            try:
                out.append(json.loads(l))
            except ValueError:
                out.append({"__bad_line__": l})
        return out
    return None


def _read_table(text: str, filename: str) -> list[dict] | None:
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:50])
    delim = "\t" if filename.lower().endswith(".tsv") or "\t" in lines[0] else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    known = {n for n in TEXT_NAMES + ANSWER_NAMES}
    if any(_canon(h) in known for h in header):
        has_header = True
    else:
        # A header's cells don't reappear as values below it; a data row's answer (its last cell) usually does.
        later = [{_canon(r[i]) for r in rows[1:] if i < len(r)} for i in range(len(header))]
        recurs = any(_canon(h) in later[i] for i, h in enumerate(header) if h)
        has_header = (not recurs and len(rows) > 1 and len(header) > 1 and len(set(header)) == len(header)
                      and all(h and not _looks_like_data(h) for h in header) and max(len(h) for h in header) < 40)
    if not has_header:
        # "text<TAB>label" lines, the Evaluate page's simplest format: last cell is the answer
        if all(len(r) >= 2 for r in rows):
            return [{"text": delim.join(r[:-1]), "label": r[-1]} for r in rows]
        return [{"text": delim.join(r)} for r in rows]
    out = []
    for r in rows[1:]:
        r = r + [""] * (len(header) - len(r))
        out.append({h or f"column {i + 1}": v for i, (h, v) in enumerate(zip(header, r))})
    return out


def _looks_like_data(s: str) -> bool:
    return len(s) > 60 or bool(re.search(r"[.!?]\s", s))


# ----------------------------------------------------------------------------------------------------------------
# Plain tables -> questions


def _humanise(col: str) -> str:
    s = re.sub(r"[_\-]+", " ", col).strip()
    return s[:1].upper() + s[1:] if s else col


ADJECTIVE_ENDINGS = ("ent", "ant", "ive", "ous", "ful", "less", "able", "ible", "al", "ic", "ed", "y")


def _yes_no_wording(col: str) -> str:
    """A yes/no column's question in plain words: "urgent" -> "Is this urgent?", "has_attachment" -> "Does this have
    attachment?", "refund" -> "Should this be marked refund?"."""
    words = re.sub(r"[_\-]+", " ", col).strip().lower().split()
    if not words:
        return "Is this true?"
    head, rest = words[0], " ".join(words[1:])
    if head in ("is", "are", "was", "were") and rest:
        return f"Is this {rest}?"
    if head in ("has", "have") and rest:
        return f"Does this have {rest}?"
    if head in ("needs", "need", "requires") and rest:
        return f"Does this need {rest}?"
    if head in ("should", "can", "will", "does", "did", "must") and rest:
        return f"{head.capitalize()} this {rest}?"
    name = " ".join(words)
    return f"Is this {name}?" if len(words) == 1 and name.endswith(ADJECTIVE_ENDINGS) else f"Should this be marked {name}?"


RATING_WORDS = ("star", "rating", "rate", "score", "priority", "severity", "level", "grade", "urgency", "satisfaction",
                "risk", "impact", "importance", "quality", "stars")


def _int_scale(col: str, values: list[str]) -> tuple[int, int] | None:
    """Whole numbers in a short range, in a column named like a rating (stars, priority, severity): an ordered scale.
    Numbers in a column such as "label" are more often category codes, so they stay a list of options."""
    if not any(w in _canon(col) for w in RATING_WORDS):
        return None
    try:
        nums = {float(v) for v in values}
    except ValueError:
        return None
    if not nums or not all(n.is_integer() for n in nums):
        return None
    ints = sorted(int(n) for n in nums)
    span = ints[-1] - ints[0] + 1
    return (ints[0], ints[-1]) if len(ints) >= 2 and span <= 10 and 2 * len(ints) >= span else None


def _infer_table(rows: list[dict], questions: dict | None) -> tuple[list[dict], dict, list[Problem], str]:
    problems: list[Problem] = []
    cols = list(dict.fromkeys(k for r in rows for k in r))
    if not cols:
        return [], {}, [Problem("error", "The file has no columns I could read.")], ""
    lengths = {c: sum(len(str(r.get(c) or "")) for r in rows) / max(1, len(rows)) for c in cols}
    distinct = {c: len({_canon(r.get(c)) for r in rows if str(r.get(c) or "").strip()}) for c in cols}
    # answer columns: named like answers, or given in `questions`, or (two columns) the shorter one
    if questions:
        answer_cols = [c for c in cols if c in questions]
    else:
        answer_cols = [c for c in cols if _canon(c) in ANSWER_NAMES]
        if not answer_cols:
            if len(cols) == 1:
                return [], {}, [Problem("error", "The file has only one column. Add a column with the right answer for "
                                                 "each example (for example: text, answer).")], ""
            # low-variety, short columns are answers; the longest column is the text
            text_guess = max(cols, key=lambda c: lengths[c])
            answer_cols = [c for c in cols if c != text_guess and lengths[c] < 40
                           and 1 < distinct[c] <= max(MAX_CHOICE_OPTIONS, 2) and distinct[c] < 0.5 * len(rows) + 2]
            if not answer_cols:
                answer_cols = [cols[-1]] if cols[-1] != text_guess else [cols[0]]
    input_cols = [c for c in cols if c not in answer_cols]
    if not input_cols:
        return [], {}, [Problem("error", "I couldn't find a column with the text to decide about.")], ""
    text_cols = [c for c in input_cols if _canon(c) in TEXT_NAMES] or input_cols
    single_text = len(input_cols) == 1

    qdefs: dict[str, dict] = {}
    for c in answer_cols:
        if questions and c in questions:
            qdefs[c] = questions[c]
            continue
        values = [str(r.get(c)).strip() for r in rows if r.get(c) is not None and str(r.get(c)).strip()]
        freq = Counter(values)
        canon_vals = {_canon(v) for v in freq}
        name = _humanise(c)
        generic = _canon(c) in ANSWER_NAMES
        scale = _int_scale(c, list(freq)) if not (canon_vals <= (YES | NO)) else None
        if canon_vals and canon_vals <= (YES | NO) and 1 <= len(canon_vals) <= 2:
            # yes/no, true/false, y/n or 1/0: a yes/no question
            if len(canon_vals) < 2:
                problems.append(Problem("error", f"Every example in '{c}' has the same answer ('{values[0]}'). "
                                                 "The model needs examples of both yes and no."))
                continue
            qdefs[c] = {"type": "noul", "instructions": "Is this true?" if generic else _yes_no_wording(c)}
        elif scale:
            lo, hi = scale
            qdefs[c] = {"type": "score", "instructions": f"Rate the {'answer' if generic else name.lower()} from {lo} to {hi}.",
                        "criteria": [str(v) for v in range(lo, hi + 1)]}
        else:
            opts = [v for v, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]
            if len(opts) > MAX_CHOICE_OPTIONS:
                problems.append(Problem("error", f"The column '{c}' has {len(opts)} different answers. A decision model "
                                                 f"picks from a fixed list; use at most {MAX_CHOICE_OPTIONS} answers."))
                continue
            if len(opts) < 2:
                problems.append(Problem("error", f"Every example in '{c}' has the same answer ('{opts[0] if opts else ''}'). "
                                                 "The model needs examples of at least two different answers."))
                continue
            qdefs[c] = {"type": "choice", "instructions": "Which option fits best?" if generic else f"Which {name.lower()} fits best?",
                        "criteria": {o: None for o in opts}}
    records = []
    for i, r in enumerate(rows):
        if single_text:
            state: Any = str(r.get(input_cols[0]) or "")
        else:
            state = {c: r.get(c) for c in input_cols if str(r.get(c) or "").strip()}
        records.append({"state": state, "answers": {c: r.get(c) for c in qdefs}, "__line__": i + 2})
    what = (f"{len(rows)} examples. The text is in '{', '.join(text_cols if single_text else input_cols)}'; "
            + "; ".join(f"'{c}' is {_describe(q)}" for c, q in qdefs.items()) + ".")
    return records, qdefs, problems, what


def _describe(q: dict) -> str:
    if q["type"] == "noul":
        return "a yes/no question"
    if q["type"] == "score":
        return f"a scale from {q['criteria'][0]} to {q['criteria'][-1]}"
    return f"a choice between {len(_option_names(q))} answers"


# ----------------------------------------------------------------------------------------------------------------
# Public entry point


def import_examples(data: bytes | str, filename: str = "data.jsonl", questions: dict | None = None) -> ImportResult:
    """Read a file in any accepted format and return validated `Example`s plus a plain-language report.

    `questions`: optional question definitions (TypeSafe shape, keyed like the answers) that override or supply the
    questions, e.g. the one question the Evaluate page defines, or the user's wording from the Train page."""
    text = data if isinstance(data, str) else _decode(data)
    problems: list[Problem] = []
    objs = _read_jsonish(text)
    fmt, detected = "standard", ""
    qdefs: dict[str, dict] = dict(questions or {})
    if objs is not None and objs and any("questions" in o or "answers" in o or "gold" in o for o in objs if isinstance(o, dict)):
        records = []
        for i, o in enumerate(objs):
            if "__bad_line__" in o:
                problems.append(Problem("error", "A line is not valid JSON.", [i + 1]))
                continue
            rec = dict(o)
            rec["__line__"] = i + 1
            if "gold" in rec and "answers" not in rec:           # typed-decisions shape
                fmt = "typed-decisions"
                for k in ("state", "questions", "gold"):
                    if isinstance(rec.get(k), str):
                        try:
                            rec[k] = json.loads(rec[k])
                        except ValueError:
                            pass
                rec["answers"] = {q: g for q, g in (rec.get("gold") or {}).items()}
            records.append(rec)
        detected = f"{len(records)} examples in the standard format."
    elif objs is not None:
        fmt = "lines"
        rows = [{"text": o.get("text", o.get("state", json.dumps(o, ensure_ascii=False))),
                 **({"label": o.get("label", o.get("answer"))} if ("label" in o or "answer" in o) else {})}
                if isinstance(o, dict) and ("text" in o or "state" in o) else o for o in objs]
        records, inferred, p2, detected = _infer_table(rows, questions)
        qdefs.update(inferred)
        problems += p2
    else:
        fmt = "table"
        rows = _read_table(text, filename) or []
        if not rows:
            return ImportResult([], {}, [Problem("error", "The file is empty.")], "", fmt)
        records, inferred, p2, detected = _infer_table(rows, questions)
        qdefs.update(inferred)
        problems += p2
        if not qdefs:                     # every answer column was unusable: say why, and how many rows there were
            if len(rows) < MIN_LABELLED:
                problems.append(Problem("error", f"The file has only {len(rows)} examples. Add at least {MIN_LABELLED} (a "
                                                 "few hundred works much better)."))
            return ImportResult([], {}, problems, detected, fmt)
    examples, p3, qdefs = _build(records, qdefs)
    problems += p3
    problems += _quality_checks(examples, qdefs)
    return ImportResult(examples, qdefs, problems, detected, fmt)


def _build(records: list[dict], shared_q: dict) -> tuple[list[Example], list[Problem], dict]:
    problems: list[Problem] = []
    bad_answers: dict[str, list[int]] = defaultdict(list)
    bad_answer_msg: dict[str, str] = {}
    invalid: list[int] = []
    invalid_msg = ""
    examples: list[Example] = []
    used_q: dict[str, dict] = {}
    for rec in records:
        line = rec.get("__line__", len(examples) + 1)
        qs_def = rec.get("questions") or shared_q
        answers = rec.get("answers") or {}
        if not qs_def:
            invalid.append(line)
            invalid_msg = "An example has no questions. Either give each line its questions, or describe them once for the file."
            continue
        try:
            req = SystemOneRequest.model_validate({"state": rec.get("state"), "questions": qs_def,
                                                  "media": rec.get("media") or []})
        except Exception as e:  # noqa: BLE001
            invalid.append(line)
            invalid_msg = f"An example is not a valid decision request: {str(e).splitlines()[0]}"
            continue
        if rec.get("state") in (None, "", {}, []):
            invalid.append(line)
            invalid_msg = "An example has no text to decide about."
            continue
        qs = normalise(req)
        targets: list[list[float] | None] = []
        for q in qs:
            cid = q.parent or q.id
            try:
                targets.append(target_for(q, answers.get(cid)))
            except ValueError as e:
                targets.append(None)
                bad_answers[cid].append(line)
                bad_answer_msg[cid] = str(e)
        for cid, qd in req.questions.items():
            used_q.setdefault(cid, qd.model_dump(exclude_none=True))
        group = str(rec.get("group") or _state_hash(rec.get("state")))
        canon = {"state": rec.get("state"), "questions": {k: v.model_dump(exclude_none=True) for k, v in req.questions.items()},
                 "answers": {k: v for k, v in answers.items() if k in req.questions}}
        for k in ("id", "group", "media", "split", "source"):
            if rec.get(k):
                canon[k] = rec[k]
        examples.append(Example(len(examples), req, qs, targets, group, canon))
    if invalid:
        problems.append(Problem("error" if len(invalid) > max(3, 0.2 * max(1, len(records))) else "warning",
                                invalid_msg + (" These lines were skipped." if len(invalid) <= max(3, 0.2 * len(records)) else ""),
                                invalid))
    for cid, lines in bad_answers.items():
        share = len(lines) / max(1, len(examples))
        problems.append(Problem("error" if share > 0.2 else "warning",
                                f"Some answers to '{cid}' don't match its options, e.g. {bad_answer_msg[cid]}. "
                                + ("Those answers were left out." if share <= 0.2 else "Check the answers or the options."),
                                lines))
    return examples, problems, used_q or shared_q


def state_hash(state: Any) -> str:
    """Identity of a situation, ignoring case, spacing and key order: finds the same case in two files."""
    s = state if isinstance(state, str) else json.dumps(state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(_canon(s).encode()).hexdigest()[:16]


_state_hash = state_hash


def _quality_checks(examples: list[Example], qdefs: dict) -> list[Problem]:
    out: list[Problem] = []
    labelled = sum(ex.labelled for ex in examples)
    if labelled < MIN_LABELLED:
        out.append(Problem("error", f"There are only {labelled} answered questions. Add at least {MIN_LABELLED} examples "
                                    "(a few hundred works much better) so the model has something to learn from and "
                                    "something to be tested on."))
    elif labelled < 200:
        out.append(Problem("warning", f"{labelled} answered questions is enough to try, but results are more reliable "
                                      "with a few hundred."))
    # per question: options with too few examples, and imbalance
    counts: dict[str, Counter] = defaultdict(Counter)
    for ex in examples:
        for i, q in enumerate(ex.qs):
            g = ex.gold(i)
            if g is not None and q.role != "multi":
                counts[q.parent or q.id][q.labels[g]] += 1
    for cid, c in counts.items():
        qd = qdefs.get(cid) or {}
        opts = _option_names(qd) if qd else list(c)
        missing = [o for o in opts if c.get(o, 0) == 0]
        if qd.get("type") in ("choice", "rank") and missing and len(missing) < len(opts):
            names = ", ".join(f"'{m}'" for m in missing[:5]) + ("…" if len(missing) > 5 else "")
            out.append(Problem("warning", f"No example answers '{cid}' with {names}. The model can't learn an answer it "
                                          "never sees."))
        total = sum(c.values())
        if total and max(c.values()) / total > 0.9 and len(c) > 1:
            top = c.most_common(1)[0][0]
            out.append(Problem("warning", f"{round(100 * c[top] / total)}% of the answers to '{cid}' are '{top}'. With so "
                                          "few of the others, the model may learn to always say that."))
    # the same text with different answers
    seen: dict[tuple, set] = defaultdict(set)
    for ex in examples:
        for i, q in enumerate(ex.qs):
            g = ex.gold(i)
            if g is not None:
                seen[(ex.group, q.id)].add(g)
    conflicts = [k for k, v in seen.items() if len(v) > 1]
    if conflicts:
        out.append(Problem("warning", f"{len(conflicts)} texts appear more than once with different answers. The model "
                                      "will learn them as uncertain."))
    return out


# ----------------------------------------------------------------------------------------------------------------
# Splits


def split(examples: list[Example], seed: int = 0, fractions: tuple[float, float, float] = (0.7, 0.15, 0.15)) -> dict[str, list[Example]]:
    """Train / calibration / test, by group (examples that share a situation never straddle splits), stratified by the
    first labelled answer so every split sees every common answer. Explicit splits (record["split"]) win."""
    explicit = {"train": [], "calibration": [], "test": []}
    rest = []
    for ex in examples:
        s = ex.record.get("split")
        if s in ("train", "calibration", "validation", "test"):
            explicit["calibration" if s == "validation" else s].append(ex)
        else:
            rest.append(ex)
    by_group: dict[str, list[Example]] = defaultdict(list)
    for ex in rest:
        by_group[ex.group].append(ex)
    strata: dict[str, list[str]] = defaultdict(list)
    for g, exs in by_group.items():
        first = next(((q.id, ex.gold(i)) for ex in exs for i, q in enumerate(ex.qs) if ex.gold(i) is not None), ("", -1))
        strata[f"{first[0]}={first[1]}"].append(g)
    out = {k: list(v) for k, v in explicit.items()}
    n_groups = len(by_group)
    small = sum(ex.labelled for ex in rest) < 300
    f_train, f_cal, f_test = (0.6, 0.2, 0.2) if small else fractions
    carry = 0.0
    for key in sorted(strata):
        gs = sorted(strata[key], key=lambda g: hashlib.sha1(f"{seed}:{g}".encode()).hexdigest())
        n = len(gs)
        n_test = int(round(n * f_test + carry))
        n_cal = int(round(n * f_cal))
        if n >= 3:
            n_test, n_cal = max(1, n_test), max(1, n_cal)
        carry = (n * f_test + carry) - n_test
        for g in gs[:n_test]:
            out["test"] += by_group[g]
        for g in gs[n_test:n_test + n_cal]:
            out["calibration"] += by_group[g]
        for g in gs[n_test + n_cal:]:
            out["train"] += by_group[g]
    del n_groups
    return out


def to_jsonl(examples: list[Example]) -> str:
    return "\n".join(json.dumps(ex.record, ensure_ascii=False) for ex in examples) + "\n"
