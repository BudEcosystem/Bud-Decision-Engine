"""The standard training format, the importers and the splits (pure Python; no GPU, no model)."""
import json

from basal.training.dataformat import MIN_LABELLED, import_examples, split, target_for
from basal.contract import SystemOneRequest, normalise


def rows(n, fn):
    return "\n".join(fn(i) for i in range(n))


def test_plain_csv_becomes_a_choice_question():
    csv_text = "text,team\n" + rows(60, lambda i: f"ticket {i},{['billing', 'shipping', 'accounts'][i % 3]}")
    r = import_examples(csv_text, "t.csv")
    assert r.usable and r.fmt == "table"
    assert r.questions["team"]["type"] == "choice"
    assert set(r.questions["team"]["criteria"]) == {"billing", "shipping", "accounts"}
    ex = r.examples[0]
    assert ex.targets[0] == [1.0 if k == "billing" else 0.0 for k in ex.qs[0].keys]


def test_headerless_tab_lines_like_the_evaluate_page():
    tsv = rows(40, lambda i: f"message number {i}\t{'yes' if i % 2 else 'no'}")
    r = import_examples(tsv, "e.txt")
    assert len(r.examples) == 40                        # the first line is data, not a header
    assert r.questions["label"]["type"] == "noul"


def test_several_answer_columns_become_several_questions_and_structured_state():
    csv_text = "subject,body,priority,urgent\n" + rows(
        40, lambda i: f"Order {i},Parcel {i} is late,{'high' if i % 2 else 'low'},{'yes' if i % 3 == 0 else 'no'}")
    r = import_examples(csv_text, "x.csv")
    assert set(r.questions) == {"priority", "urgent"}
    assert r.questions["urgent"]["instructions"] == "Is this urgent?"
    assert r.examples[0].request.state == {"subject": "Order 0", "body": "Parcel 0 is late"}


def test_standard_format_every_question_type():
    rec = {"state": "case", "questions": {
        "team": {"type": "choice", "instructions": "Which team?", "criteria": {"a": "alpha", "b": "beta"}},
        "urgent": {"type": "noul", "instructions": "Urgent?"},
        "impact": {"type": "score", "instructions": "Impact?", "criteria": ["low", "mid", "high"]},
        "tags": {"type": "multi", "instructions": "Which apply?", "criteria": {"x": None, "y": None}},
        "eta": {"type": "number", "instructions": "Days?", "criteria": [1, 2, 5, 10]}},
        "answers": {"team": "Beta", "urgent": "yes", "impact": "high", "tags": ["x"], "eta": 4.2}}
    r = import_examples("\n".join(json.dumps(rec) for _ in range(40)), "s.jsonl")
    assert r.usable
    t = dict(zip([q.id for q in r.examples[0].qs], r.examples[0].targets))
    assert t["team"] == [0.0, 1.0]                     # matched by description, case-insensitively
    assert t["urgent"] == [0.0, 1.0]
    assert t["impact"] == [0.0, 0.0, 1.0]              # matched by level text
    assert t["__m0"] == [0.0, 1.0] and t["__m1"] == [1.0, 0.0]   # pick-all-that-apply children
    assert t["eta"] == [0.0, 0.0, 1.0, 0.0]            # nearest value


def test_soft_labels_and_typed_decisions_gold():
    req = SystemOneRequest(state="s", questions={"q": {"type": "choice", "criteria": {"a": None, "b": None}}})
    q = normalise(req)[0]
    assert target_for(q, {"probabilities": {"a": 3, "b": 1}}) == [0.75, 0.25]
    assert target_for(q, {"label": "b"}) == [0.0, 1.0]
    assert target_for(q, None) is None


def test_bad_answers_and_tiny_files_are_reported_in_plain_words():
    r = import_examples("text,label\n" + rows(10, lambda i: f"t{i},a"), "tiny.csv")
    assert not r.usable
    assert any("same answer" in p.message or str(MIN_LABELLED) in p.message for p in r.problems)
    rec = {"state": "s", "questions": {"q": {"type": "choice", "criteria": {"a": None, "b": None}}}}
    lines = [json.dumps({**rec, "answers": {"q": "a" if i % 2 else "b"}}) for i in range(40)]
    lines.append(json.dumps({**rec, "answers": {"q": "zebra"}}))
    r = import_examples("\n".join(lines), "s.jsonl")
    assert r.usable and any("don't match its options" in p.message for p in r.problems)


def test_split_keeps_groups_together_and_honours_explicit_splits():
    lines = []
    for i in range(300):
        lines.append(json.dumps({"state": f"state {i // 3}", "group": f"g{i // 3}",
                                 "questions": {"q": {"type": "noul"}}, "answers": {"q": i % 2 == 0}}))
    lines.append(json.dumps({"state": "fixed", "questions": {"q": {"type": "noul"}}, "answers": {"q": True}, "split": "test"}))
    r = import_examples("\n".join(lines), "g.jsonl")
    s = split(r.examples, seed=0)
    where = {}
    for name, exs in s.items():
        for ex in exs:
            assert where.setdefault(ex.group, name) == name     # a group never straddles two splits
    assert any(ex.request.state == "fixed" for ex in s["test"])
    assert sum(len(v) for v in s.values()) == len(r.examples)
    assert len(s["train"]) > len(s["test"]) > 0


def test_ratings_become_a_scale_and_codes_stay_options():
    csv_text = "review,stars,code\n" + rows(60, lambda i: f"a longer review text number {i},{1 + i % 5},{i % 4}")
    r = import_examples(csv_text, "r.csv")
    assert r.questions["stars"] == {"type": "score", "instructions": "Rate the stars from 1 to 5.",
                                    "criteria": ["1", "2", "3", "4", "5"]}
    assert r.questions["code"]["type"] == "choice"             # 0-3 in a column not named like a rating: codes
    ex = r.examples[3]                                           # stars 4 -> the level called "4", the fourth one
    stars = next(t for q, t in zip(ex.qs, ex.targets) if q.id == "stars")
    assert stars == [0.0, 0.0, 0.0, 1.0, 0.0]


def test_yes_no_wording_and_a_single_valued_file_gets_one_clear_message():
    from basal.training.dataformat import _yes_no_wording
    assert _yes_no_wording("urgent") == "Is this urgent?"
    assert _yes_no_wording("needs_refund") == "Does this need refund?"
    assert _yes_no_wording("refund") == "Should this be marked refund?"
    r = import_examples("text,label\n" + rows(12, lambda i: f"message {i},spam"), "tiny.csv")
    msgs = [p.message for p in r.problems]
    assert any("same answer" in m for m in msgs) and any("only 12 examples" in m for m in msgs)
    assert not any("no questions" in m for m in msgs)
