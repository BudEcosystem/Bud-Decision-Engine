---
title: Probabilities, certainty and acting
description: How Bud Decision Studio turns a model's probabilities into a decision you can automate, with calibration temperatures, certainty, act thresholds and the settings that decide them.
lead: Each answer comes with a number that says how sure the model is. The studio compares that number with a threshold you choose, and tells you per answer whether it is safe to act on automatically or should go to a person.
---

## Probabilities

For every question, the model returns a probability for each answer you allowed. For a `choice`, `score`, `rank` or `number` question they add up to 1: 0.95 on `billing` leaves 0.05 for every other team together. For a `multi` question each option has its own probability that it applies, so they do not add up to anything in particular.

A probability is only useful if it means what it says. A model is **well calibrated** when, of all the answers it gives at 0.9, about nine in ten are right. Some models are naturally overconfident or underconfident on your data; the temperature setting corrects that.

## Calibration temperature

The calibration **temperature** T reshapes every probability as p' ∝ p^(1/T), then rescales them to add up to 1 again:

- T = 1 changes nothing.
- T above 1 softens the answer: probabilities move towards each other, and the model sounds less sure.
- T below 1 sharpens it: the leading answer gets more of the probability.

The temperature never changes which answer wins, only how sure the answer sounds. The three requests on the right ask the same question at T = 0.5, 1 and 2: the winner stays `technical`, and its probability moves from 0.89 to 0.67 to 0.50.

The studio always stores the probabilities from **before** the temperature as well (`raw_probabilities`, returned with `include=answers.raw_probabilities`), so a temperature can be fitted on past decisions later without asking the model again. The Evaluate page fits one for you from labelled examples; see [Evaluate](/docs/manual/evaluate).

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "model": "laya",
  "state": "The export button has returned a 500 error since this morning. Our month-end close is blocked.",
  "questions": {
    "department": {"type": "choice", "instructions": "Which team should handle this ticket?",
                   "criteria": {"billing": "invoices, payments, refunds",
                                "technical": "bugs and outages",
                                "sales": "pricing and upgrades"}}
  },
  "settings": {"temperature": 2, "act_threshold": 0.9},
  "include": ["answers.raw_probabilities"]
}'
```
@@ Output
```text
temperature  billing  technical  sales   certainty  act
0.5          0.0459   0.8939     0.0603  0.8939     false
1            0.1524   0.6729     0.1747  0.6729     false
2            0.2397   0.5036     0.2566  0.5036     false

raw_probabilities, the same in all three:
billing 0.152415   technical 0.672867   sales 0.174717
```
:::

## Certainty

**Certainty** is the single number the studio uses to decide whether an answer can be acted on:

- for `choice`, `score`, `noul`, `rank` and `number`, it is the probability of the winning answer (`top_probability`);
- for `multi`, it is the certainty of the weakest call: for each option, max(p, 1 − p), and the smallest of those. A `multi` answer is only as certain as its least certain option.

Certainty is not the same as `confidence`. `confidence` is TypeSafe's field, kept unchanged for compatibility: for a `choice` it measures how far the winner is above a uniform guess, and for a `score` how concentrated the probability is around one level. Use `certainty` for automation rules; it reads as a plain probability.

## Acting automatically

You choose an **act threshold** between 0 and 1. Each answer gets `act: true` when its certainty is at least the threshold, and `act: false` otherwise. The decision as a whole:

| Field | Meaning |
|---|---|
| `act` | `true` only when every answer acts. |
| `needs_review` | The keys of the answers that did not act, in question order. |

That gives a simple shape for code: act on what is certain, send the rest to a person. The threshold is the line between automation and review. Raising it sends more decisions to people and makes fewer automated mistakes; lowering it does the opposite. The default is 0.9.

The gate is fixed when the decision is made and stored with it, along with the threshold that applied, so History can show why each answer acted or asked for a person.

:::console Acting on a decision
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)

d = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_message": message, "account_tier": tier},
}).raise_for_status().json()

if d["act"]:
    route(d["answers"]["department"]["choice"])
else:
    send_to_review(d["id"], d["needs_review"])     # for example ["urgency", "churn_risk"]
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ template: "support-triage",
                         variables: { customer_message: message, account_tier: tier } }),
});
const d = await res.json();

if (d.act) route(d.answers.department.choice);
else sendToReview(d.id, d.needs_review);           // for example ["urgency", "churn_risk"]
```
:::

You can also gate per answer. In the [agent guide](/docs/guides/agents) the loop reads only `answers.next_action.act`, and asks a person when that one answer is unsure, whatever the other answers say.

## Where the settings come from

The act threshold and the temperature can be set in several places: per request, per template, per model inside a template, and per question in each of those. For every question and every setting, the studio takes the first value it finds in this order:

| Order | Where | Name in `settings.sources` |
|---|---|---|
| 1 | The request's per-question value: `settings.questions.<key>` | `request.questions` |
| 2 | The request: `settings` | `request` |
| 3 | The template, for this model and this question: `settings.models.<model>.questions.<key>` | `template.models.<model>.questions` |
| 4 | The template, for this model: `settings.models.<model>` | `template.models.<model>` |
| 5 | The template, for this question: `settings.questions.<key>` | `template.questions` |
| 6 | The template: `settings` | `template` |
| 7 | The studio's defaults: act threshold 0.9, temperature 1 | `studio` |

Two consequences:

- A request-level value applies to **every** question, overriding the template's per-question values. Sending `"temperature": 1` turns calibration off for that decision.
- Per-model values are read after the model is known, so a temperature fitted for Laya never applies when the same template runs on Kev 4B. This is how one template stays well calibrated on several models.

Each decision records the values it used and where each came from. In the decision on the right, the template sets a threshold of 0.85, a lower 0.7 for `churn_risk`, and for Laya a temperature of 1.3 with 1.8 for `urgency`; `sources` names the layer of each.

`multi` questions have a third per-question setting, `multi_threshold`, which follows the same order, ending at the question's own `threshold`.

:::console Settings and their sources
@@ Template settings
```json
"settings": {
  "act_threshold": 0.85,
  "temperature": 1.1,
  "questions": {"churn_risk": {"act_threshold": 0.7}},
  "models": {
    "laya": {"temperature": 1.3,
             "questions": {"urgency": {"temperature": 1.8}}}
  }
}
```
@@ Response
```json
"settings": {
  "act_threshold": 0.85,
  "temperature": 1.3,
  "questions": {
    "urgency": {"temperature": 1.8},
    "churn_risk": {"act_threshold": 0.7}
  },
  "sources": {
    "act_threshold": "template",
    "temperature": "template.models.laya",
    "questions.urgency.temperature": "template.models.laya.questions",
    "questions.churn_risk.act_threshold": "template.questions"
  }
}
```
:::

## Choosing a threshold

A good threshold depends on the cost of a wrong automatic action against the cost of a person's time. Two tools help:

- **Evaluate**, in the app, runs a question over labelled examples and recommends the threshold that keeps mistakes under the error rate you choose, showing how much work it would automate. See [Evaluate](/docs/manual/evaluate).
- **What-if statistics** recompute the gate over decisions already in History, from their stored probabilities, at another threshold or temperature, without running any model.

The what-if request on the right (its response shortened) asks how version 2 of a template would have done at a threshold of 0.75. Of the labelled answers that would have acted, 83% were right. `act_rate` stays 0 because it counts decisions where every answer acts, and this template's `urgency` question never reaches 0.75; that points at the question to improve, which is the subject of [Improve a template safely](/docs/guides/improve).

:::console GET /v1/studio/templates/{id}/stats
@@ curl
```bash
curl -s 'http://127.0.0.1:8420/v1/studio/templates/support-triage/stats?version=2&what_if.act_threshold=0.75'
```
@@ Response 200
```json
{
  "object": "decision.stats",
  "group_by": ["version"],
  "what_if": {"temperature": null, "act_threshold": 0.75},
  "groups": [
    {
      "key": {"version": 2},
      "count": 22,
      "act_rate": 0.0,
      "what_if": {
        "act_threshold": 0.75,
        "temperature": null,
        "act_rate": 0.0,
        "accuracy_when_acting": 0.8333,
        "labelled_acting": 6
      }
    }
  ]
}
```
:::
