---
title: Decisions
description: Create, preview, retrieve, rerun, cancel, pin, redact and delete decisions with the Bud Decision Studio API.
lead: A decision is one run of a model over a situation and a set of questions. You can make one from a saved template or from questions you send yourself, and every decision is kept in history unless you say otherwise.
---

All decision endpoints live under `/v1/studio/decisions`. The examples use a template named `support-triage`, which routes a customer message to a department, rates its urgency on a four-level scale and asks whether the customer threatens to leave. [Templates](/docs/api/templates#create-a-template) shows how it was created.

## Create a decision

::endpoint POST /v1/studio/decisions

Runs a model and returns its answers. Send a `template` and its `variables` to reuse saved questions, or send `state` and `questions` for a decision without a template. The response comes back when the model has answered, usually in a fraction of a second; a model that still has to load takes longer (see [slow first calls](/docs/api/index#slow-first-calls-and-background-decisions)).

Each answer carries a `certainty` and an `act` flag. An answer *acts* when its certainty reaches its act threshold. The decision's own `act` is `true` only when every answer acts, and `needs_review` lists the questions that did not, so your code can act automatically on sure answers and send the rest to a person.

| Field | Type | Description |
|---|---|---|
| `template` | string | A template reference: `support-triage` (its latest version), `support-triage@2` (a version) or `support-triage@production` (an alias). Leave it out for a decision without a template. |
| `variables` | object | The template's variables by name. Each value is checked against the variable's type; required variables must be present. |
| `state` | string, object or array | The situation to decide about. Required without a template, and for a template that has no variables. |
| `questions` | object | Question key to question. Without a template, the questions to ask (at least one). With a template, extra questions, where the template allows them. |
| `add_options` | object | New options for template questions that allow them. |
| `skip` | array | Template questions to leave out, where allowed. |
| `media` | array | Images, audio or video for the model to read. See [Use files in a decision](/docs/api/resources#use-files-in-a-decision). |
| `model` | string | A model id such as `laya`. Defaults to the template's model, then to the most recently loaded model. A downloaded model that is not loaded is loaded first. |
| `settings` | object | `act_threshold` (above 0, at most 1), `temperature` (above 0, at most 20; 1 leaves probabilities as the model gave them) and per-question values under `questions`. |
| `metadata` | object | Up to 16 string values, such as a ticket id. Keys up to 64 characters, values up to 512. You can filter history by them. |
| `store` | boolean or string | `true` (the default), `false`, `"full"`, `"answers_only"` or `"none"`. See [Choose what history keeps](#choose-what-history-keeps). |
| `background` | boolean | Answer at once with `status: "queued"`; see [Run a decision in the background](#run-a-decision-in-the-background). |
| `include` | array | Extra fields in the response: `input`, `input.rendered_state`, `answers.raw_probabilities`, `answers.model_extras`. |

Headers: `Idempotency-Key` makes retries safe, `X-Basal-Store` works like `store`, and `X-Basal-Metadata` adds metadata (see [Headers](/docs/api/index#headers)). Fields the endpoint does not know are ignored and named in `warnings` with the code `unknown_field_ignored`.

The response is `200` with the [decision object](#the-decision-object). When the decision was stored, the headers `x-basal-decision-id` and `location` name it.

The example names the model, lowers the act threshold to 0.8 for this one decision and asks for full storage. The response's `settings.sources` shows where each value came from.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {
    "customer_message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel.",
    "account_tier": "enterprise"
  },
  "model": "laya",
  "settings": {"act_threshold": 0.8},
  "metadata": {"ticket_id": "T-4411"},
  "store": "full"
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {
        "customer_message": "We were billed twice for March on invoice 4411. "
                            "Refund the duplicate today or we cancel.",
        "account_tier": "enterprise",
    },
    "model": "laya",
    "settings": {"act_threshold": 0.8},
    "metadata": {"ticket_id": "T-4411"},
    "store": "full",
}).raise_for_status().json()

if d["act"]:
    print("route to", d["answers"]["department"]["choice"])
else:
    print("ask a person about", d["needs_review"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: {
      customer_message: "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel.",
      account_tier: "enterprise",
    },
    model: "laya",
    settings: { act_threshold: 0.8 },
    metadata: { ticket_id: "T-4411" },
    store: "full",
  }),
});
const d = await res.json();
if (!res.ok) throw new Error(d.error.message);
console.log(d.act ? `route to ${d.answers.department.choice}` : `ask a person about ${d.needs_review}`);
```
@@ Response 200
```json
{
  "id": "dec_01M3TJF7S0K5RYAPVBFF1ZAW1W",
  "object": "decision",
  "status": "completed",
  "created_at": 1790819409,
  "completed_at": 1790819410,
  "template": {
    "id": "support-triage",
    "version": 2,
    "ref": "support-triage",
    "resolved_from": "latest",
    "attribution": "explicit"
  },
  "model": "laya",
  "model_requested": "laya",
  "model_revision": null,
  "extensions": {"questions": [], "options": {}, "skipped": []},
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {
        "billing": 0.9838,
        "technical": 0.0084,
        "sales": 0.0078
      },
      "confidence": 0.9757,
      "decision": "billing",
      "top_probability": 0.9838,
      "certainty": 0.9838,
      "act": true,
      "origin": "template"
    },
    "urgency": {
      "type": "score",
      "score": 1.9591,
      "legend": {
        "0": "can wait",
        "1": "soon",
        "2": "today",
        "3": "blocking or at risk of churn"
      },
      "probabilities": {"0": 0.0046, "1": 0.0905, "2": 0.8461, "3": 0.0588},
      "confidence": 0.8415,
      "decision": "2",
      "top_probability": 0.8461,
      "certainty": 0.8461,
      "act": true,
      "origin": "template"
    },
    "churn_risk": {
      "type": "noul",
      "noul": 0.6393,
      "probabilities": {"false": 0.3607, "true": 0.6393},
      "decision": "yes",
      "top_probability": 0.6393,
      "certainty": 0.6393,
      "act": false,
      "origin": "template"
    },
    "wants_refund": {
      "type": "noul",
      "noul": 0.8538,
      "probabilities": {"false": 0.1462, "true": 0.8538},
      "decision": "yes",
      "top_probability": 0.8538,
      "certainty": 0.8538,
      "act": true,
      "origin": "template"
    }
  },
  "act": false,
  "needs_review": ["churn_risk"],
  "settings": {
    "act_threshold": 0.8,
    "temperature": 1.0,
    "questions": {},
    "sources": {"act_threshold": "request", "temperature": "studio"}
  },
  "usage": {"input_tokens": 278, "output_tokens": 0},
  "timing": {
    "queue_ms": 0.0,
    "load_ms": 0.0,
    "model_ms": 561.3,
    "total_ms": 596.0
  },
  "passes": 1,
  "notes": [],
  "warnings": [],
  "source": {
    "surface": "api",
    "endpoint": "/v1/studio/decisions",
    "format": "studio",
    "client": "curl",
    "request_id": "req_b46e2b7dab084a1587d0d60450933614",
    "attempt": 0,
    "retry_of": null
  },
  "group": null,
  "batch": null,
  "eval": null,
  "rerun_of": null,
  "metadata": {"ticket_id": "T-4411"},
  "pinned": false,
  "feedback": {},
  "store": "full",
  "expires_at": 1793411409,
  "error": null
}
```
:::

### Without a template

Send `state` and `questions` directly. The body of a TypeSafe `/v1/systemone` request works here as it is, and gains history, metadata and the act gate. Without a template, the act threshold comes from `settings`, or else from the studio's default (0.9 unless you change it in [settings](/docs/api/resources#read-the-settings)).

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "model": "laya",
  "state": {"order": "A-1182",
            "message": "The package arrived crushed and the screen is cracked."},
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged in transit?"},
    "next_step": {"type": "choice", "instructions": "What should support offer?",
                  "criteria": {"replace": "send a new unit",
                               "refund": "refund the order",
                               "ask_photos": "ask for photos first"}}
  },
  "settings": {"act_threshold": 0.8}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.post("/decisions", json={
    "model": "laya",
    "state": {"order": "A-1182",
              "message": "The package arrived crushed and the screen is cracked."},
    "questions": {
        "damaged": {"type": "noul", "instructions": "Was the item damaged in transit?"},
        "next_step": {"type": "choice", "instructions": "What should support offer?",
                      "criteria": {"replace": "send a new unit",
                                   "refund": "refund the order",
                                   "ask_photos": "ask for photos first"}},
    },
    "settings": {"act_threshold": 0.8},
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "laya",
    state: { order: "A-1182", message: "The package arrived crushed and the screen is cracked." },
    questions: {
      damaged: { type: "noul", instructions: "Was the item damaged in transit?" },
      next_step: {
        type: "choice",
        instructions: "What should support offer?",
        criteria: { replace: "send a new unit", refund: "refund the order", ask_photos: "ask for photos first" },
      },
    },
    settings: { act_threshold: 0.8 },
  }),
});
const d = await res.json();
```
@@ Response 200
```json
{
  "id": "dec_01M3TE8JJAYEVWX0726926GMJY",
  "object": "decision",
  "status": "completed",
  "template": null,
  "model": "laya",
  "answers": {
    "damaged": {
      "type": "noul",
      "noul": 0.8935,
      "probabilities": {"false": 0.1065, "true": 0.8935},
      "decision": "yes",
      "top_probability": 0.8935,
      "certainty": 0.8935,
      "act": true,
      "origin": "adhoc"
    },
    "next_step": {
      "type": "choice",
      "choice": "replace",
      "probabilities": {
        "replace": 0.5337,
        "refund": 0.0904,
        "ask_photos": 0.3759
      },
      "confidence": 0.3005,
      "decision": "replace",
      "top_probability": 0.5337,
      "certainty": 0.5337,
      "act": false,
      "origin": "adhoc"
    }
  },
  "act": false,
  "needs_review": ["next_step"],
  "settings": {
    "act_threshold": 0.8,
    "temperature": 1.0,
    "questions": {},
    "sources": {"act_threshold": "request", "temperature": "studio"}
  },
  "store": "full"
}
```
:::

The response above is shortened to its main fields; the full object has every field shown in [Create a decision](#create-a-decision).

### Extend a template for one call

A template decides which of its questions callers may change. Its `extensions` setting can allow extra questions (up to `max_questions`), new options for named `choice`, `multi`, `rank` and `number` questions, and skipping named questions. Options can never be added to `score` or `noul` questions, because a new level would change what the answer means. Unless the template says otherwise, it accepts up to 16 extra questions and nothing else.

| Field | Type | Description |
|---|---|---|
| `add_options` | object | Question key to new options: an object of name to description, or a list of names (numbers for a `number` question). |
| `skip` | array | Template question keys to leave out of this decision. |
| `questions` | object | Extra questions, asked after the template's own. A key may not repeat a template question. |

The response records what changed in `extensions`, and each answer's `origin` says where its question came from: `template`, `extended` (the template's question with added options), `extra`, or `adhoc` for a decision without a template. Template statistics count only `template` answers by default, so one-off extensions do not skew them.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_message": "Our SSO login fails for every user since this morning.",
                "account_tier": "pro"},
  "add_options": {"department": {"security": "logins, SSO, permissions"}},
  "skip": ["churn_risk"],
  "questions": {"outage": {"type": "noul",
                           "instructions": "Does this describe an outage affecting many users?"}}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_message": "Our SSO login fails for every user since this morning.",
                  "account_tier": "pro"},
    "add_options": {"department": {"security": "logins, SSO, permissions"}},
    "skip": ["churn_risk"],
    "questions": {"outage": {"type": "noul",
                             "instructions": "Does this describe an outage affecting many users?"}},
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_message: "Our SSO login fails for every user since this morning.", account_tier: "pro" },
    add_options: { department: { security: "logins, SSO, permissions" } },
    skip: ["churn_risk"],
    questions: { outage: { type: "noul", instructions: "Does this describe an outage affecting many users?" } },
  }),
});
const d = await res.json();
```
@@ Response 200
```json
{
  "id": "dec_01M3TE8JRZMJH9TQ6FTG1AJ4J6",
  "object": "decision",
  "status": "completed",
  "template": {
    "id": "support-triage",
    "version": 1,
    "ref": "support-triage",
    "resolved_from": "latest",
    "attribution": "explicit"
  },
  "model": "laya",
  "extensions": {
    "questions": ["outage"],
    "options": {"department": ["security"]},
    "skipped": ["churn_risk"]
  },
  "answers": {
    "department": {
      "type": "choice",
      "choice": "security",
      "probabilities": {
        "billing": 0.0038,
        "technical": 0.0616,
        "sales": 0.0045,
        "security": 0.9301
      },
      "confidence": 0.9068,
      "decision": "security",
      "top_probability": 0.9301,
      "certainty": 0.9301,
      "act": true,
      "origin": "extended"
    },
    "urgency": {
      "type": "score",
      "score": 2.2945,
      "probabilities": {
        "0": 0.0101,
        "1": 0.0389,
        "2": 0.5974,
        "3": 0.3536
      },
      "decision": "2",
      "certainty": 0.5974,
      "act": false,
      "origin": "template"
    },
    "outage": {
      "type": "noul",
      "noul": 0.9288,
      "probabilities": {"false": 0.0712, "true": 0.9288},
      "decision": "yes",
      "top_probability": 0.9288,
      "certainty": 0.9288,
      "act": true,
      "origin": "extra"
    }
  },
  "act": false,
  "needs_review": ["urgency"]
}
```
:::

The response is shortened: the urgency answer also carries its `legend`, `confidence` and `top_probability`, and the object has the remaining fields of [the decision object](#the-decision-object).

## The decision object

| Field | Type | Description |
|---|---|---|
| `id` | string | `dec_` followed by a time-ordered id. |
| `status` | string | `completed`, `failed`, or for background decisions `queued`, `in_progress` and `cancelled`. |
| `created_at`, `completed_at` | integer | Unix seconds. |
| `template` | object | `id`, `version`, the `ref` you sent, `resolved_from` (`latest`, `pinned` or `alias`) and `attribution` (`explicit`; `header` for a [wire-format call](/docs/api/systemone#file-a-call-under-a-template) filed under a template; `draft` for the Playground's unsaved edits). `null` without a template. |
| `model`, `model_requested` | string | The model that answered, and the one you named (`null` when the default was used). |
| `model_revision` | string | Always `null` for now. |
| `input` | object | What the model was given: `variables`, `state`, `media` and `questions`. Returned by [Retrieve a decision](#retrieve-a-decision), or on create with `include`. |
| `extensions` | object | The extra questions, added options and skipped questions of this call. |
| `answers` | object | One answer per question, described below. `null` when the decision failed or has not finished. |
| `act` | boolean | `true` when every answer reached its act threshold. |
| `needs_review` | array | The questions whose answers did not. |
| `settings` | object | The act threshold and temperature used, per question where they differ, and `sources` naming where each value came from. |
| `usage` | object | `input_tokens`, and `output_tokens`, which is always 0: decision models read text, they do not write it. |
| `timing` | object | `queue_ms`, `load_ms` (time spent loading the model for this call), `model_ms` and `total_ms`. |
| `passes`, `notes` | integer, array | How many passes the model needed, and its notes, such as running on the processor. |
| `warnings` | array | Things the studio changed or ignored, each `{code, message, param}`. |
| `source` | object | Where the call came from: `surface` (`api`, `playground`, `compare`, `rerun`), `endpoint`, `format`, `client` (read from the user agent), `request_id`, `attempt` and `retry_of`. |
| `group`, `rerun_of` | object, string | The comparison a decision belongs to, and the decision it reran. |
| `batch`, `eval` | null | Reserved; always `null` for now. |
| `metadata`, `pinned` | object, boolean | Your labels, and whether retention keeps it forever. |
| `feedback` | object | The latest label per question: `{expected, correct, feedback_id}`. |
| `store` | string | `full`, `answers_only` or `none`. |
| `expires_at` | integer | When retention will delete it, or `null` when it is kept (pinned, labelled, or retention off). |
| `error` | object | For a failed decision: `{type, code, message}`. |

Every answer has these fields:

| Field | Description |
|---|---|
| `type` | `choice`, `score`, `noul`, `multi`, `rank` or `number`. |
| `probabilities` | The model's probability for each option, after calibration by `temperature`. |
| `decision` | The answer as one label: an option name, a level number as text (`"2"`), `yes` or `no`, a list for `multi`, a number for `number`. Feedback, filters and statistics use this form. |
| `top_probability` | The highest probability. |
| `certainty` | How sure the answer is: the top probability. For `multi`, the lowest of max(p, 1 − p) across the options, so one unsure option holds the whole answer back. |
| `act` | `true` when `certainty` reached this question's act threshold. |
| `origin` | `template`, `extended`, `extra` or `adhoc`. |

Each type adds its own fields, the same ones TypeSafe returns: `choice` and `confidence` for a choice; `score`, `legend` and `confidence` for a scale; `noul` (the probability of yes) for a yes-or-no question; `selected` and `threshold` for pick-all-that-apply (`multi`); `ranking` for put-in-order (`rank`); and `estimate`, `most_likely`, `range` and `unit` for `number`. [The six question types](/docs/concepts/question-types) explains each one.

With `include: ["answers.raw_probabilities"]`, each answer also carries `raw_probabilities`: the model's probabilities before the temperature was applied, which the studio keeps so statistics can ask "what if" at another temperature.

## Preview a decision

::endpoint POST /v1/studio/decisions/preview

Checks a request and shows exactly what the model would receive, without running it or storing anything. It takes the same body as [Create a decision](#create-a-decision). Use it to check variables, see the rendered situation, or turn a template call into the equivalent `/v1/systemone` request.

| Field | Description |
|---|---|
| `state`, `rendered_state` | The situation with the variables filled in, and the text the model reads. |
| `questions` | The final questions, after extensions and skips. |
| `settings` | The resolved settings and their sources. |
| `systemone_request` | The same decision as a `/v1/systemone` body. |
| `worker_settings` | The per-question temperatures the model will use. |
| `store` | What history would keep. |
| `warnings` | Problems that would not stop the decision, such as `state_may_be_truncated`. |

A preview needs no loaded model when the request or the template names one. Errors are the same as for a real decision, so a preview is a cheap way to validate input.

:::console POST /v1/studio/decisions/preview
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/preview -d '{
  "template": "support-triage",
  "variables": {"customer_message": "Can I move to annual billing?", "account_tier": "pro"}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
p = studio.post("/decisions/preview", json={
    "template": "support-triage",
    "variables": {"customer_message": "Can I move to annual billing?", "account_tier": "pro"},
}).raise_for_status().json()
print(p["rendered_state"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/preview", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_message: "Can I move to annual billing?", account_tier: "pro" },
  }),
});
const p = await res.json();
console.log(p.rendered_state);
```
@@ Response 200
```json
{
  "object": "decision.preview",
  "template": {
    "id": "support-triage",
    "version": 1,
    "ref": "support-triage",
    "resolved_from": "latest",
    "attribution": "explicit"
  },
  "model": "laya",
  "state": {
    "tier": "pro",
    "message": "Can I move to annual billing?"
  },
  "rendered_state": "tier: pro\nmessage: Can I move to annual billing?",
  "extensions": {"questions": [], "options": {}, "skipped": []},
  "media": [],
  "settings": {
    "act_threshold": 0.85,
    "temperature": 1.0,
    "questions": {"churn_risk": {"act_threshold": 0.7}},
    "sources": {
      "act_threshold": "template",
      "temperature": "studio",
      "questions.churn_risk.act_threshold": "template.questions"
    }
  },
  "usage": {"input_tokens": 127},
  "worker_settings": {"temperature": null, "questions": {}},
  "store": "full",
  "warnings": []
}
```
:::

The response is shortened: it also carries `questions` (the three template questions) and `systemone_request` (the same decision as a `/v1/systemone` body).

## Run a decision in the background

Add `"background": true` to a create request. The studio stores the decision with `status: "queued"`, answers at once, and runs it. Collect the result with [Retrieve a decision](#retrieve-a-decision) and `?wait=60`, which holds the request open for up to 60 seconds and returns as soon as the decision has finished.

A background decision must be stored so it can be fetched, so `store: false` is refused with `background_requires_store`; use `"answers_only"` to keep the answers without the input. A queued decision can be [cancelled](#cancel-a-background-decision) until the model starts on it. If the studio stops while a decision is queued or running, it is marked `failed` with `error.code: "interrupted"` at the next start, and is never run twice.

:::console POST /v1/studio/decisions
@@ curl
```bash
id=$(curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_message": "The export to CSV has been failing since Tuesday.",
                "account_tier": "free"},
  "background": true
}' | jq -r .id)

curl -s "http://127.0.0.1:8420/v1/studio/decisions/$id?wait=60"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
queued = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_message": "The export to CSV has been failing since Tuesday.",
                  "account_tier": "free"},
    "background": True,
}).raise_for_status().json()

d = studio.get(f"/decisions/{queued['id']}", params={"wait": 60}).json()
print(d["status"], d["answers"]["department"]["choice"])
```
@@ JavaScript
```js
const base = "http://127.0.0.1:8420/v1/studio";
const queued = await (await fetch(`${base}/decisions`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_message: "The export to CSV has been failing since Tuesday.", account_tier: "free" },
    background: true,
  }),
})).json();

const d = await (await fetch(`${base}/decisions/${queued.id}?wait=60`)).json();
console.log(d.status, d.answers.department.choice);
```
@@ Response 200
```json
{
  "id": "dec_01M3TE8RKPP70FYT393DKVTR93",
  "object": "decision",
  "status": "queued",
  "created_at": 1790815003,
  "completed_at": null,
  "template": {
    "id": "support-triage",
    "version": 1,
    "ref": "support-triage",
    "resolved_from": "latest",
    "attribution": "explicit"
  },
  "model": "laya",
  "answers": null,
  "act": null,
  "store": "full"
}
```
:::

The response shown is the first call's, shortened. The second call returns the same decision with `status: "completed"` and its answers.

## Retrieve a decision

::endpoint GET /v1/studio/decisions/{id}

Returns a stored decision, including its `input`. A decision made with `store: "none"` was never saved and answers `404 decision_not_found`, as does one removed by retention.

| Parameter | Description |
|---|---|
| `wait` | Up to 60 seconds to wait for a queued or running decision to finish. |
| `include` | `input.rendered_state` adds the text the model read; `answers.raw_probabilities` adds the probabilities before calibration. Comma-separated. |
| `format` | `typesafe`, `openrouter`, `vercel` or `evaluate`: return the answers as that API would have. |

:::console GET /v1/studio/decisions/{id}
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.get("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR").raise_for_status().json()
print(d["input"]["variables"], d["needs_review"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR");
const d = await res.json();
console.log(d.input.variables, d.needs_review);
```
@@ Response 200
```json
{
  "id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "object": "decision",
  "status": "completed",
  "created_at": 1790814986,
  "template": {
    "id": "support-triage",
    "version": 1,
    "ref": "support-triage",
    "resolved_from": "latest",
    "attribution": "explicit"
  },
  "model": "laya",
  "input": {
    "variables": {
      "customer_message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel.",
      "account_tier": "enterprise"
    },
    "state": {
      "tier": "enterprise",
      "message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel."
    },
    "media": []
  },
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "decision": "billing",
      "certainty": 0.9838,
      "act": true,
      "origin": "template"
    }
  },
  "act": false,
  "needs_review": ["urgency", "churn_risk"],
  "feedback": {},
  "store": "full",
  "expires_at": 1793406986
}
```
:::

The response is shortened. In full, `input` also lists the questions, and `answers` holds all three answers in full, as in [Create a decision](#create-a-decision).

### In another API's format

`?format=typesafe` returns a stored decision's answers in the exact shape `/v1/systemone` returns, which helps when code written for TypeSafe reads decisions back from history. `openrouter`, `vercel` and `evaluate` work the same way for the [gateway formats](/docs/api/systemone#gateway-formats); add `extended=true` for the studio's extra answer fields.

:::console GET /v1/studio/decisions/{id}?format=typesafe
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR?format=typesafe"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.get("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR", params={"format": "typesafe"}).json()
```
@@ JavaScript
```js
const r = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR?format=typesafe",
)).json();
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {
        "billing": 0.9838,
        "technical": 0.0084,
        "sales": 0.0078
      },
      "confidence": 0.9757
    },
    "urgency": {
      "type": "score",
      "score": 1.9591,
      "legend": {
        "0": "can wait",
        "1": "soon",
        "2": "today",
        "3": "blocking or at risk of churn"
      },
      "probabilities": {
        "0": 0.0046,
        "1": 0.0905,
        "2": 0.8461,
        "3": 0.0588
      },
      "confidence": 0.8415
    },
    "churn_risk": {"type": "noul", "noul": 0.6393}
  },
  "usage": {"input_tokens": 212, "output_tokens": 0}
}
```
:::

## Retrieve a decision's input

::endpoint GET /v1/studio/decisions/{id}/input

Returns only what the model was given: the variables, the situation, media, the questions, and the rendered text the model read. It answers `409 input_unavailable` when the decision kept only its answers or its input was redacted.

:::console GET /v1/studio/decisions/{id}/input
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/input
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
inp = studio.get("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/input").raise_for_status().json()
print(inp["rendered_state"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/input");
const input = await res.json();
console.log(input.rendered_state);
```
@@ Response 200
```json
{
  "object": "decision.input",
  "decision_id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "variables": {
    "customer_message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel.",
    "account_tier": "enterprise"
  },
  "state": {
    "tier": "enterprise",
    "message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel."
  },
  "media": [],
  "questions": {
    "department": {
      "type": "choice",
      "instructions": "Which department should handle this request?",
      "criteria": {
        "billing": "invoices, payments, refunds",
        "technical": "bugs, outages, errors",
        "sales": "pricing, upgrades, new contracts"
      }
    },
    "urgency": {
      "type": "score",
      "instructions": "How urgent is this request?",
      "criteria": [
        "can wait",
        "soon",
        "today",
        "blocking or at risk of churn"
      ]
    },
    "churn_risk": {
      "type": "noul",
      "instructions": "Does the customer threaten to cancel or leave?"
    }
  },
  "rendered_state": "tier: enterprise\nmessage: We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel."
}
```
:::

## Rerun a decision

::endpoint POST /v1/studio/decisions/{id}/rerun

Runs a stored decision's input again, by default on the same model, the same template version and the settings you sent the first time. Change any of them in the body to see how the answer moves. The new decision links back with `rerun_of`, its `source.surface` is `rerun`, and it copies the original's metadata.

| Field | Type | Description |
|---|---|---|
| `model` | string | Another model. |
| `template` | string | Another template reference, such as `support-triage@production`. |
| `settings` | object | Settings that replace the ones the original request sent. |
| `variables` | object | Variables to change. Sensitive variables are never stored, so a decision that used them needs them sent again here. |
| `store` | boolean or string | What history keeps for the rerun. |
| `include` | array | As in [Create a decision](#create-a-decision). |

A rerun needs the full input, so it answers `409 input_unavailable` for a decision that kept only its answers, had its input redacted, or used media whose files are no longer kept. The body may be empty.

:::console POST /v1/studio/decisions/{id}/rerun
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/rerun \
  -d '{"settings": {"act_threshold": 0.6}}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.post("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/rerun",
                json={"settings": {"act_threshold": 0.6}}).raise_for_status().json()
print(r["act"], r["rerun_of"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/rerun", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ settings: { act_threshold: 0.6 } }),
});
const r = await res.json();
console.log(r.act, r.rerun_of);
```
@@ Response 200
```json
{
  "id": "dec_01M3TEC5FDPQZ46C56QXXQJ9SN",
  "object": "decision",
  "status": "completed",
  "template": {
    "id": "support-triage",
    "version": 1,
    "ref": "support-triage@1",
    "resolved_from": "pinned",
    "attribution": "explicit"
  },
  "model": "laya",
  "act": true,
  "needs_review": [],
  "settings": {
    "act_threshold": 0.6,
    "temperature": 1.0,
    "questions": {},
    "sources": {"act_threshold": "request", "temperature": "studio"}
  },
  "source": {
    "surface": "rerun",
    "endpoint": "/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/rerun",
    "format": "studio",
    "client": "curl",
    "request_id": "req_b20fc2c9cad84aed96920e56ce408e60",
    "attempt": 0,
    "retry_of": null
  },
  "rerun_of": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "metadata": {"ticket_id": "T-4411", "reviewed_by": "ops"}
}
```
:::

The response is shortened; its `answers` are omitted here. Note `settings.questions` is empty: an act threshold sent in the request applies to every question, ahead of the template's per-question values. The [settings ladder](/docs/api/templates#how-settings-are-chosen) explains the order.

## Cancel a background decision

::endpoint POST /v1/studio/decisions/{id}/cancel

Cancels a background decision that has not reached the model yet, for example one waiting for its model to load. It returns the decision with `status: "cancelled"`. A decision the model is already working on, or one that has finished, answers `409 decision_finished`.

If the decision itself started that model loading and no other request is waiting for the model, the load stops too and its memory is freed. A model you loaded yourself is left to finish. (Before 0.3.0 the load always carried on.)

:::console POST /v1/studio/decisions/{id}/cancel
@@ curl
```bash
curl -s -X POST http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TED3BVAZSH3DFGYZRXFJ8Y/cancel
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.post("/decisions/dec_01M3TED3BVAZSH3DFGYZRXFJ8Y/cancel").json()
print(d.get("status") or d["error"]["code"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TED3BVAZSH3DFGYZRXFJ8Y/cancel", {
  method: "POST",
});
const d = await res.json();
console.log(res.ok ? d.status : d.error.code);
```
@@ Response 200
```json
{
  "id": "dec_01M3TED3BVAZSH3DFGYZRXFJ8Y",
  "object": "decision",
  "status": "cancelled",
  "created_at": 1790815145,
  "completed_at": 1790815145,
  "model": "kev-0.5b",
  "answers": null,
  "act": null,
  "store": "full",
  "error": null
}
```
:::

## Update a decision

::endpoint PATCH /v1/studio/decisions/{id}

A decision's answers, input and settings never change once made. You can change two things:

| Field | Type | Description |
|---|---|---|
| `pinned` | boolean | `true` keeps the decision whatever the retention setting says; `expires_at` becomes `null`. |
| `metadata` | object | A merge patch: keys you send are set, keys set to `null` are removed, other keys stay. |

Sending any other field answers `400 read_only_field`. The response is the updated decision.

:::console PATCH /v1/studio/decisions/{id}
@@ curl
```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR \
  -d '{"pinned": true, "metadata": {"reviewed_by": "ops"}}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.patch("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR",
                 json={"pinned": True, "metadata": {"reviewed_by": "ops"}}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR", {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ pinned: true, metadata: { reviewed_by: "ops" } }),
});
const d = await res.json();
```
@@ Response 200
```json
{
  "id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "object": "decision",
  "status": "completed",
  "metadata": {"ticket_id": "T-4411", "reviewed_by": "ops"},
  "pinned": true,
  "expires_at": null
}
```
:::

The response is shortened to the fields that changed; the full decision object comes back.

## Redact a decision

::endpoint POST /v1/studio/decisions/{id}/redact

Removes parts of a decision's input and keeps its answers, so statistics stay correct after you delete personal data. Redacted values read `[redacted]`, and the input lists what was removed under `redacted`.

| Field | Type | Description |
|---|---|---|
| `fields` | array | Required. Any of `input.state`, `input.rendered_state`, `input.variables`, `input.variables.<name>`, `input.media` and `metadata.<key>`. |

Removing variables also removes the situation built from them. A redacted decision can no longer be rerun or turned into a test example. To redact many decisions at once, see [Delete or redact many decisions](/docs/api/history#delete-or-redact-many-decisions).

:::console POST /v1/studio/decisions/{id}/redact
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE8JJAYEVWX0726926GMJY/redact \
  -d '{"fields": ["input.state"]}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.post("/decisions/dec_01M3TE8JJAYEVWX0726926GMJY/redact",
                json={"fields": ["input.state"]}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE8JJAYEVWX0726926GMJY/redact", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ fields: ["input.state"] }),
});
const d = await res.json();
```
@@ Response 200
```json
{
  "id": "dec_01M3TE8JJAYEVWX0726926GMJY",
  "object": "decision",
  "status": "completed",
  "input": {
    "variables": null,
    "state": "[redacted]",
    "media": [],
    "questions": {
      "damaged": {
        "type": "noul",
        "instructions": "Was the item damaged in transit?"
      },
      "next_step": {
        "type": "choice",
        "instructions": "What should support offer?",
        "criteria": {
          "replace": "send a new unit",
          "refund": "refund the order",
          "ask_photos": "ask for photos first"
        }
      }
    },
    "redacted": ["input.state"]
  }
}
```
:::

The response is shortened; the full decision object comes back, with its answers unchanged.

## Delete a decision

::endpoint DELETE /v1/studio/decisions/{id}

Deletes a decision with everything stored for it: input, answers, feedback and the link to any media. This cannot be undone. Media files it used are cleaned up later, once nothing else refers to them.

:::console DELETE /v1/studio/decisions/{id}
@@ curl
```bash
curl -s -X DELETE http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE7HB1PWCAG6T51N36WT7B
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
studio.delete("/decisions/dec_01M3TE7HB1PWCAG6T51N36WT7B").raise_for_status()
```
@@ JavaScript
```js
await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE7HB1PWCAG6T51N36WT7B", { method: "DELETE" });
```
@@ Response 200
```json
{
  "id": "dec_01M3TE7HB1PWCAG6T51N36WT7B",
  "object": "decision.deleted",
  "deleted": true
}
```
:::

## Choose what history keeps

Every decision is stored at one of three levels. The level actually used is the most private of what the request asks for, what the template allows (its `storage` field) and the studio's setting (`history.store`), so a request can keep less than the studio does but never more. When a level was lowered for you, `warnings` says so with `store_downgraded`.

| Level | Kept | Not kept |
|---|---|---|
| Everything (`full`) | Input, answers, settings, metadata, timing. | Values of variables marked `sensitive`, which are only ever stored as keyed hashes. |
| Answers only (`answers_only`) | Answers, settings, metadata, timing. | The input. The decision cannot be rerun or made a test example. |
| Nothing (`none`) | Nothing; only anonymous counts for the History chart. | Everything. The response still has an `id`, but the decision cannot be fetched. |

The `x-basal-stored` header always reports the level used. Wire-format calls such as `/v1/systemone` are stored the same way; TypeSafe's own servers ignore `store`, so the same code runs against both.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s -i http://127.0.0.1:8420/v1/studio/decisions \
  -H "X-Basal-Store: answers_only" \
  -d '{"template": "support-triage",
       "variables": {"customer_message": "Where can I download my invoices?", "account_tier": "pro"}}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_message": "Where can I download my invoices?", "account_tier": "pro"},
    "store": "answers_only",
})
print(r.headers["x-basal-stored"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_message: "Where can I download my invoices?", account_tier: "pro" },
    store: "answers_only",
  }),
});
console.log(res.headers.get("x-basal-stored"));
```
@@ Output
```http
HTTP/1.1 200 OK
x-basal-stored: answers_only
x-basal-decision-id: dec_01M3TEFBTDS0Q225K1AW4D6PC7
location: /v1/studio/decisions/dec_01M3TEFBTDS0Q225K1AW4D6PC7
content-type: application/json
x-request-id: req_aba108ef003a44f7a39315dc17c66187
```
:::

[History and privacy](/docs/concepts/history) covers retention, sensitive variables and what the studio writes to disk.
