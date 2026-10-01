---
title: The six question types
description: Pick one, rate on a scale, yes or no, pick any, put in order and estimate a number. What each question type takes, what it returns, and when to use it.
lead: Every question has a type that fixes the shape of its answer. Three types come from TypeSafe's Jev API; three more are studio extensions built on top of them and work with every model.
---

## The types at a glance

The app names each type in plain words; the API uses the `type` value in the second column.

| In the app | `type` | You define | You get back | `decision` value |
|---|---|---|---|---|
| Pick one | `choice` | Named options, with optional descriptions | A probability per option, and the winner | The winning option's name |
| Rate on a scale | `score` | Ordered levels, lowest first | A probability per level, and an average position | The most likely level's key: `"0"`, `"1"`, … |
| Yes or no | `noul` | A statement to judge | The probability that it is true | `"yes"` or `"no"` |
| Pick any | `multi` | Options and a cut-off | Every option above the cut-off | The list of selected options |
| Put in order | `rank` | Options | The options from most to least likely | The first option |
| Estimate a number | `number` | The values the number could take | A best estimate and an 80% range | The most likely value |

`choice`, `score` and `noul` are TypeSafe's types. `multi`, `rank` and `number` are studio extensions: the studio turns each into the basic types before the model runs (a `multi` question becomes one yes-or-no question per option) and assembles the answer afterwards. They are accepted on every endpoint, including `/v1/systemone`; TypeSafe's hosted API does not have them.

## Ask all six at once

One request can mix every type, and all questions are answered in the same pass. The request on the right asks six questions about one support ticket. The response is shortened to each answer's `decision` and `certainty`; the sections below show each answer in full.

Every answer also carries:

| Field | Meaning |
|---|---|
| `decision` | The answer in one canonical value, the same vocabulary you use to label answers and filter History. |
| `top_probability` | The probability of the most likely answer. |
| `certainty` | How sure the answer is, for the act gate: `top_probability`, except for `multi` (see below). |
| `act` | Whether `certainty` reached the act threshold. See [Probabilities, certainty and acting](/docs/concepts/acting). |
| `origin` | `adhoc` without a template; with one, `template`, `extended` or `extra`. See [Templates and versions](/docs/concepts/templates). |

`decision`, `certainty`, `act` and `origin` come from the studio API. On `/v1/systemone` you get TypeSafe's fields only, unless you send the header `X-Basal-Extensions: 1`.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "model": "intern-decision-4b",
  "state": "Hi, we were billed twice for March on invoice #4411. Please refund the duplicate today or we will cancel our plan. The new dashboard is also very slow since last week.",
  "questions": {
    "department": {"type": "choice", "instructions": "Which team should handle this ticket?",
                   "criteria": {"billing": "invoices, payments, refunds",
                                "technical": "bugs and outages",
                                "sales": "pricing and upgrades"}},
    "urgency": {"type": "score", "instructions": "How urgent is this ticket?",
                "criteria": ["can wait", "soon", "today", "blocking"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel?"},
    "topics": {"type": "multi", "instructions": "Which topics does the ticket raise?",
               "criteria": ["billing error", "refund", "performance", "login", "pricing"]},
    "first_reply": {"type": "rank", "instructions": "Which reply should come first?",
                    "criteria": ["confirm the refund", "apologise for the slow dashboard", "offer a discount"]},
    "minutes": {"type": "number", "instructions": "How many minutes will an agent need to resolve this?",
                "criteria": [5, 15, 30, 60, 120], "unit": "minutes"}
  }
}'
```
@@ Response 200
```json
{
  "model": "intern-decision-4b",
  "answers": {
    "department":  {"type": "choice", "decision": "billing", "certainty": 0.9616},
    "urgency":     {"type": "score",  "decision": "2", "certainty": 0.8158},
    "churn_risk":  {"type": "noul",   "decision": "yes", "certainty": 0.9892},
    "topics":      {"type": "multi",  "decision": ["billing error", "refund", "performance"], "certainty": 0.799},
    "first_reply": {"type": "rank",   "decision": "confirm the refund", "certainty": 0.807},
    "minutes":     {"type": "number", "decision": 15, "certainty": 0.3561}
  },
  "act": false,
  "needs_review": ["urgency", "topics", "first_reply", "minutes"]
}
```
:::

### Pick one: `choice`

The model picks exactly one option. Give each option a short name, which is what your code compares against, and a description when the name alone is ambiguous; a description often matters more to the model than the name. `criteria` can also be a plain list of names.

| Field | Meaning |
|---|---|
| `choice` | The winning option. |
| `probabilities` | One probability per option, adding up to 1. |
| `confidence` | TypeSafe's measure: (top − 1/K) / (1 − 1/K) for K options. 0 means no better than guessing, 1 means certain. |

A question may have up to 1,000 options, within the model's own limit (20 for Laya, 500 for Lev; see [the model table](/docs/concepts/decision-models#the-eleven-models)).

:::console choice
@@ Question
```json
"department": {
  "type": "choice",
  "instructions": "Which team should handle this ticket?",
  "criteria": {"billing": "invoices, payments, refunds",
               "technical": "bugs and outages",
               "sales": "pricing and upgrades"}
}
```
@@ Response
```json
"department": {
  "type": "choice",
  "choice": "billing",
  "probabilities": {"billing": 0.9616, "technical": 0.0346, "sales": 0.0038},
  "confidence": 0.9423,
  "decision": "billing",
  "top_probability": 0.9616,
  "certainty": 0.9616,
  "act": true,
  "origin": "adhoc"
}
```
:::

### Rate on a scale: `score`

Levels are ordered from lowest to highest. The answer gives a probability for each level, keyed `"0"`, `"1"` and so on, and an average position: `score` 2.11 here means "today, leaning towards blocking". Use `decision` (the single most likely level) when you need one level, and `score` when you sort or threshold.

| Field | Meaning |
|---|---|
| `score` | The expected level: the average of the level numbers, weighted by probability. |
| `legend` | Your levels, keyed by their number. |
| `confidence` | 1 when all the probability sits on one level; lower as it spreads. |
| `decision` | The most likely level's key, as a string. |

Levels may carry descriptions in the text itself, such as `"today: a person cannot work"`; [Improve a template safely](/docs/guides/improve) shows how much that can help.

:::console score
@@ Question
```json
"urgency": {
  "type": "score",
  "instructions": "How urgent is this ticket?",
  "criteria": ["can wait", "soon", "today", "blocking"]
}
```
@@ Response
```json
"urgency": {
  "type": "score",
  "score": 2.1073,
  "legend": {"0": "can wait", "1": "soon", "2": "today", "3": "blocking"},
  "probabilities": {"0": 0.0084, "1": 0.0259, "2": 0.8158, "3": 0.1499},
  "confidence": 0.8074,
  "decision": "2",
  "top_probability": 0.8158,
  "certainty": 0.8158,
  "act": false,
  "origin": "adhoc"
}
```
:::

### Yes or no: `noul`

The name comes from TypeSafe's API. Write the instruction as a statement or a question that is clearly true or false. `noul` is the probability of yes; `decision` is `"yes"` from 0.5 upwards. You can describe what yes and no mean with `"criteria": {"true": "…", "false": "…"}`.

| Field | Meaning |
|---|---|
| `noul` | The probability of yes. |
| `probabilities` | `false` and `true`. |
| `decision` | `"yes"` or `"no"`. |

:::console noul
@@ Question
```json
"churn_risk": {
  "type": "noul",
  "instructions": "Does the customer threaten to cancel?"
}
```
@@ Response
```json
"churn_risk": {
  "type": "noul",
  "noul": 0.9892,
  "probabilities": {"false": 0.0108, "true": 0.9892},
  "decision": "yes",
  "top_probability": 0.9892,
  "certainty": 0.9892,
  "act": true,
  "origin": "adhoc"
}
```
:::

### Pick any: `multi`

Each option is judged on its own as a yes-or-no question, so any number of options can apply, including none. An option is selected when its probability reaches `threshold` (0.5 unless you set it). A `multi` question takes 1 to 64 options, and each option counts as one question towards the model's question limit.

| Field | Meaning |
|---|---|
| `threshold` | The cut-off for selecting an option, between 0 and 1. A per-decision override is `settings.questions.<key>.multi_threshold`. |
| `selected` | The options at or above the cut-off. |
| `probabilities` | The probability that each option applies. These do not add up to 1. |
| `certainty` | The least certain option's max(p, 1 − p). Here "pricing" at 0.201 is 0.799 sure to be absent, the weakest call, so the answer is 0.799 certain. |

:::console multi
@@ Question
```json
"topics": {
  "type": "multi",
  "instructions": "Which topics does the ticket raise?",
  "criteria": ["billing error", "refund", "performance", "login", "pricing"]
}
```
@@ Response
```json
"topics": {
  "type": "multi",
  "selected": ["billing error", "refund", "performance"],
  "probabilities": {"billing error": 0.8679, "refund": 0.9405, "performance": 0.9203,
                    "login": 0.0631, "pricing": 0.201},
  "threshold": 0.5,
  "decision": ["billing error", "refund", "performance"],
  "top_probability": 0.9405,
  "certainty": 0.799,
  "act": false,
  "origin": "adhoc"
}
```
:::

### Put in order: `rank`

A `rank` question is a `choice` whose options come back ordered from most to least likely. Use it when you will try options in turn: the reply to send first, the fix to try first, the candidate to show first. It needs at least two options.

| Field | Meaning |
|---|---|
| `ranking` | Every option, most likely first. |
| `choice`, `decision` | The first option. |
| `confidence` | As for `choice`. |

:::console rank
@@ Question
```json
"first_reply": {
  "type": "rank",
  "instructions": "Which reply should come first?",
  "criteria": ["confirm the refund", "apologise for the slow dashboard", "offer a discount"]
}
```
@@ Response
```json
"first_reply": {
  "type": "rank",
  "ranking": ["confirm the refund", "apologise for the slow dashboard", "offer a discount"],
  "probabilities": {"confirm the refund": 0.807,
                    "apologise for the slow dashboard": 0.1579,
                    "offer a discount": 0.035},
  "choice": "confirm the refund",
  "decision": "confirm the refund",
  "confidence": 0.7105,
  "top_probability": 0.807,
  "certainty": 0.807,
  "act": false,
  "origin": "adhoc"
}
```
:::

### Estimate a number: `number`

You list the values the number can take, 2 to 64 of them, as a list or as `{"30": "half an hour"}` with descriptions. The model scores each value, and the answer reports the expected value, the single most likely value and the range that holds the central 80% of the probability. Add `unit` to label the values.

| Field | Meaning |
|---|---|
| `estimate` | The expected value. |
| `most_likely` | The value with the highest probability; also `decision`. |
| `range` | The smallest and largest value of the central 80%. |
| `unit` | The unit you gave. |

Spread the values over the range you care about; the estimate can only fall between your smallest and largest value.

:::console number
@@ Question
```json
"minutes": {
  "type": "number",
  "instructions": "How many minutes will an agent need to resolve this?",
  "criteria": [5, 15, 30, 60, 120],
  "unit": "minutes"
}
```
@@ Response
```json
"minutes": {
  "type": "number",
  "estimate": 26.0705,
  "most_likely": 15,
  "range": [5, 60],
  "unit": "minutes",
  "probabilities": {"5": 0.1786, "15": 0.3561, "30": 0.295, "60": 0.1575, "120": 0.0128},
  "decision": 15,
  "top_probability": 0.3561,
  "confidence": 0.3108,
  "certainty": 0.3561,
  "act": false,
  "origin": "adhoc"
}
```
:::

## Labels use the same values

When you record the right answer for a decision (feedback), add a test example or filter History by an answer, you use the same values as `decision`. A few other spellings are accepted on input and stored in the canonical form.

| Type | Canonical value | Also accepted | Counted correct when |
|---|---|---|---|
| `choice` | an option name | none | it equals `choice` |
| `noul` | `"yes"` or `"no"` | `true`, `false`, `"true"`, `"false"` | it equals `decision` |
| `score` | a level key, `"0"` to `"n-1"` | the level number, or the exact level text | it equals `decision` |
| `multi` | a list of option names | none | the sets are equal |
| `rank` | an option name | a full or partial ordering (its first element is used) | it equals `decision` |
| `number` | a number | a numeric string | it falls inside `range` |

## Choosing a type

- One right answer from a fixed list: **Pick one**. If the list has more than about 20 entries, check the model's option limit, or use Lev or CLM 8B.
- Several labels can be true at once: **Pick any**, not several yes-or-no questions; the threshold and certainty then work across the set.
- An ordered judgement (urgency, severity, quality): **Rate on a scale**, so neighbouring levels count as near misses rather than unrelated options.
- A single rule check: **Yes or no**.
- Options you will try in turn: **Put in order**.
- A quantity: **Estimate a number**, with values spread over the range you care about.
