"""Scores, calibration and the comparisons the release gate uses. Pure Python + math (no PyTorch), so the server and
the tests can use it too.

A prediction is one primitive question: its log-probabilities over the question's keys, the target distribution, and
which question and example it belongs to. The formulas match the Evaluate page (ui/js/pages/evaluate.js): accuracy by
arg-max, log loss of the gold answer, multi-class Brier, and a 10-bin ECE on the top probability.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass
class Pred:
    example: int
    qid: str                   # client question id (a pick-all-that-apply child uses its parent's id)
    qtype: str                 # primitive type: choice | score | noul
    logp: list[float]          # log-probabilities over keys (temperature 1)
    target: list[float]        # target distribution over keys
    n_options: int = 0
    pid: str = ""              # the primitive question's own id (differs from qid for pick-all-that-apply children)
    options: str = ""          # the option keys: one id can name different questions in different examples

    @property
    def gold(self) -> int:
        return max(range(len(self.target)), key=self.target.__getitem__)


def scaled(logp: list[float], t: float) -> list[float]:
    z = [x / t for x in logp]
    m = max(z)
    s = sum(math.exp(x - m) for x in z)
    return [math.exp(x - m) / s for x in z]


def summarise(preds: list[Pred], temps: dict[str, float] | None = None) -> dict:
    """accuracy, log loss, Brier, ECE (10 bins) over `preds`, applying per-type temperatures when given."""
    if not preds:
        return {"n": 0}
    temps = temps or {}
    n = len(preds)
    correct = nll = brier = 0.0
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    per_q: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in preds:
        pr = scaled(p.logp, temps.get(p.qtype, temps.get("all", 1.0)))
        top = max(range(len(pr)), key=pr.__getitem__)
        ok = top == p.gold
        correct += ok
        nll -= sum(t * math.log(max(x, 1e-12)) for t, x in zip(p.target, pr))
        brier += sum((x - (1.0 if k == p.gold else 0.0)) ** 2 for k, x in enumerate(pr))
        b = min(9, int(pr[top] * 10))
        bins[b][0] += 1; bins[b][1] += pr[top]; bins[b][2] += ok
        per_q[p.qid][0] += ok; per_q[p.qid][1] += 1
    ece = sum(abs(c / k - a / k) * k / n for k, c, a in bins if k)
    return {"n": n, "accuracy": correct / n, "log_loss": nll / n, "brier": brier / n, "ece": ece,
            "per_question": {q: round(c / k, 4) for q, (c, k) in per_q.items()}}


def fit_temperatures(preds: list[Pred], min_per_type: int = 200) -> dict[str, float]:
    """One temperature by log loss over everything; a separate one per question type when that type has at least
    `min_per_type` predictions and its own fit beats the shared one. Bounded to 0.25–10 (a fit at a bound means the
    scores are degenerate and is reported by the caller)."""
    if not preds:
        return {"all": 1.0}
    out = {"all": _fit(preds)}
    by_type: dict[str, list[Pred]] = defaultdict(list)
    for p in preds:
        by_type[p.qtype].append(p)
    for t, ps in by_type.items():
        if len(ps) >= min_per_type:
            tt = _fit(ps)
            if _nll(ps, tt) < _nll(ps, out["all"]) - 1e-4:
                out[t] = tt
    return out


def _nll(preds: list[Pred], t: float) -> float:
    s = 0.0
    for p in preds:
        pr = scaled(p.logp, t)
        s -= sum(q * math.log(max(x, 1e-12)) for q, x in zip(p.target, pr))
    return s / len(preds)


def _fit(preds: list[Pred]) -> float:
    lo, hi = math.log(0.25), math.log(10.0)
    for _ in range(40):                     # golden-section search on log T (log loss is unimodal in T)
        a = hi - (hi - lo) / 1.618
        b = lo + (hi - lo) / 1.618
        if _nll(preds, math.exp(a)) < _nll(preds, math.exp(b)):
            hi = b
        else:
            lo = a
    return round(math.exp((lo + hi) / 2), 4)


def paired_accuracy_gain(base: list[Pred], tuned: list[Pred], rounds: int = 1000, seed: int = 0) -> dict:
    """Accuracy gain of `tuned` over `base` on the same predictions, with a bootstrap over examples (questions of one
    example move together). Returns the gain, a 90% interval and the share of resamples where the gain is > 0."""
    assert len(base) == len(tuned)
    by_ex: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for b, t in zip(base, tuned):
        gb = max(range(len(b.logp)), key=b.logp.__getitem__) == b.gold
        gt = max(range(len(t.logp)), key=t.logp.__getitem__) == t.gold
        by_ex[b.example].append((int(gb), int(gt)))
    exs = list(by_ex.values())
    n = sum(len(e) for e in exs)
    gain = sum(t - b for e in exs for b, t in e) / max(1, n)
    rng = random.Random(seed)
    samples = []
    for _ in range(rounds):
        pick = [exs[rng.randrange(len(exs))] for _ in exs]
        k = sum(len(e) for e in pick)
        samples.append(sum(t - b for e in pick for b, t in e) / max(1, k))
    samples.sort()
    return {"gain": gain, "low": samples[int(0.05 * rounds)], "high": samples[int(0.95 * rounds) - 1],
            "p_better": sum(s > 0 for s in samples) / rounds, "p_worse": sum(s < 0 for s in samples) / rounds}


def collapse(preds: list[Pred], min_n: int = 20) -> list[str]:
    """Questions where the model gives (nearly) the same answer to everything while the right answers vary: the sign
    of a fine-tune that learned a shortcut instead of the task."""
    by_q: dict[str, list[Pred]] = defaultdict(list)
    for p in preds:
        by_q[f"{p.qid} ({p.options})" if p.options else p.qid].append(p)
    out = []
    for q, ps in by_q.items():
        if len(ps) < min_n:
            continue
        pred = Counter(max(range(len(p.logp)), key=p.logp.__getitem__) for p in ps)
        gold = Counter(p.gold for p in ps)
        if pred.most_common(1)[0][1] / len(ps) > 0.95 and gold.most_common(1)[0][1] / len(ps) < 0.8:
            out.append(q)
    return out
