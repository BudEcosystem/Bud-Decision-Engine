"""The answer contract (basal/contract.py): per-question temperature, raw probabilities, certainty and the act gate.

    .venv/bin/python -m pytest tests/test_contract.py -q
"""
import pytest

from basal.contract import (SystemOneRequest, build_answers, certainty, gate, normalise, raw_probabilities,
                            recalibrate)

REQ = SystemOneRequest.model_validate({
    "state": "x",
    "questions": {
        "team": {"type": "choice", "criteria": ["billing", "technical", "other"]},
        "urgency": {"type": "score", "criteria": ["low", "mid", "high"]},
        "refund": {"type": "noul"},
        "topics": {"type": "multi", "criteria": ["bug", "billing"]},
        "first": {"type": "rank", "criteria": ["a", "b"]},
        "days": {"type": "number", "criteria": [1, 7, 30]},
    },
    "settings": {"questions": {"topics": {"multi_threshold": 0.8}}},
})
QS = normalise(REQ)
# in the order normalise() expands them: team, urgency, refund, topics/bug, topics/billing, first, days
PROBS = [[0.7, 0.2, 0.1], [0.1, 0.3, 0.6], [0.3, 0.7], [0.4, 0.6], [0.1, 0.9], [0.35, 0.65], [0.8, 0.15, 0.05]]


def probs_for(qs):
    assert [len(q.keys) for q in qs] == [len(x) for x in PROBS]
    return [list(x) for x in PROBS]


def test_single_temperature_unchanged():
    p = probs_for(QS)
    assert build_answers(QS, p, 2.0) == build_answers(QS, p, 2.0, {})


def test_per_question_temperature_including_multi_children():
    p = probs_for(QS)
    base = build_answers(QS, p, None)
    hot = build_answers(QS, p, None, {"team": 5.0, "topics": 5.0})
    assert hot["team"]["probabilities"]["billing"] < base["team"]["probabilities"]["billing"]
    assert hot["team"]["choice"] == base["team"]["choice"]                    # temperature never changes the winner
    assert hot["urgency"] == base["urgency"]                                  # untouched question
    assert hot["topics"]["probabilities"] != base["topics"]["probabilities"]  # children use their parent's value


def test_multi_threshold_from_settings():
    assert [q.meta["threshold"] for q in QS if q.role == "multi"] == [0.8, 0.8]


def test_raw_probabilities_keys_match_answers():
    p = probs_for(QS)
    ans = build_answers(QS, p, 3.0)
    raw = raw_probabilities(QS, p)
    for k, a in ans.items():
        assert set(raw[k]) == set(a["probabilities"]), k
    assert raw["team"] == {"billing": 0.7, "technical": 0.2, "other": 0.1}   # before temperature


def test_recalibrate_round_trip():
    p = probs_for(QS)
    raw = raw_probabilities(QS, p)
    ans = build_answers(QS, p, 2.0)
    again = recalibrate("choice", raw["team"], 2.0)
    assert again["billing"] == pytest.approx(ans["team"]["probabilities"]["billing"], abs=1e-4)


@pytest.mark.parametrize("answer,expected", [
    ({"type": "choice", "top_probability": 0.9, "probabilities": {"a": 0.9, "b": 0.1}}, 0.9),
    ({"type": "noul", "top_probability": 0.8, "probabilities": {"false": 0.2, "true": 0.8}}, 0.8),
    ({"type": "multi", "probabilities": {"a": 0.95, "b": 0.3}}, 0.7),
    ({"type": "multi", "probabilities": {}}, 1.0),
])
def test_certainty(answer, expected):
    assert certainty(answer) == pytest.approx(expected)
    assert gate(answer, expected) and not gate(answer, expected + 0.01)
