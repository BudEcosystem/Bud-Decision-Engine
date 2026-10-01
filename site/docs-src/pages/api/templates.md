---
title: Templates
description: Create, version, alias, compare, archive and delete templates with the Bud Decision Studio API.
lead: A template is a saved decision you can run again with new details. It holds typed variables, the situation built from them, the questions, a default model and settings. Every change saves a new numbered version, and old versions keep working.
---

A template has two parts. The **definition** (variables, state, questions, model, settings, extensions, modalities) is versioned: any change to it saves a new, immutable version. The **head** (name, description, metadata, storage and retention) is not versioned, and changing it saves no version.

Callers name a template by reference: `support-triage` runs the latest version, `support-triage@2` pins version 2, and `support-triage@production` follows the alias `production`, which you move when a new version is ready. Starter templates are read-only and named `builtin/<name>`; [clone one](#clone-a-template) to change it. [Templates and versions](/docs/concepts/templates) explains the ideas; this page is the reference.

## Create a template

::endpoint POST /v1/studio/templates

Creates a template at version 1 and answers `201`, with `location` and an `etag` of `"1"`. Sending the same definition again is safe: it answers `200` with `change: "unchanged"`. A different definition under an existing id answers `409 template_exists`; use [PUT](#create-or-replace-a-template) or [PATCH](#update-a-template) to change a template.

| Field | Type | Description |
|---|---|---|
| `id` | string | Required. 1 to 64 lower-case letters, digits, `-` and `_`, starting with a letter or digit. |
| `name`, `description` | string | Shown in the app. The name defaults to the id. |
| `variables` | object | Name to [variable](#variables). Up to 64. |
| `state` | string, object or array | The situation, with `{{variable}}` placeholders. See [The state](#the-state). |
| `questions` | object | Required. Question key to question, as in a decision. Up to 128. |
| `model` | string | The default model. Callers can still choose another. |
| `settings` | object | `act_threshold`, `temperature`, per-question values under `questions`, and per-model values under `models`. See [How settings are chosen](#how-settings-are-chosen). |
| `extensions` | object | What callers may change: `questions`, `max_questions`, `options`, `skip`. See [Extensions](#extensions). |
| `modalities` | array | `text` plus any of `image`, `audio`, `video` the template accepts. |
| `metadata` | object | Up to 16 string values, such as an owning team. |
| `storage` | string | The most this template's decisions keep: `full`, `answers_only` or `none`. |
| `retention_days` | integer | Days to keep this template's decisions; overrides the studio setting. `0` keeps them forever. |
| `note` | string | Why this version was saved. Defaults to "First version". |
| `from` | object | Start from another definition: `{"template": "builtin/support@1"}` or `{"decision": "dec_..."}`. See [Clone a template](#clone-a-template). |

The response is the [template object](#the-template-object), plus `change` (what the call did) and `warnings`, such as a variable no question uses (`variable_unreferenced`) or a default model that cannot run the template (`default_model_incompatible`). A definition with problems answers `400` and lists every problem in `details`.

:::console POST /v1/studio/templates
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates -d '{
  "id": "support-triage",
  "name": "Support triage",
  "description": "Route inbound tickets, rate urgency, flag churn risk.",
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.",
                         "max_length": 8000},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"],
                     "default": "free"}
  },
  "state": {"tier": "{{account_tier}}", "message": "{{customer_message}}"},
  "questions": {
    "department": {"type": "choice",
                   "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds",
                                "technical": "bugs, outages, errors",
                                "sales": "pricing, upgrades, new contracts"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request?",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul",
                   "instructions": "Does the customer threaten to cancel or leave?"}
  },
  "model": "laya",
  "settings": {"act_threshold": 0.85,
               "questions": {"churn_risk": {"act_threshold": 0.7}}},
  "extensions": {"questions": true, "max_questions": 4,
                 "options": ["department"], "skip": ["churn_risk"]},
  "metadata": {"owner_team": "support-eng"}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.post("/templates", json={
    "id": "support-triage",
    "name": "Support triage",
    "description": "Route inbound tickets, rate urgency, flag churn risk.",
    "variables": {
        "customer_message": {"type": "string", "description": "The message, verbatim.",
                             "max_length": 8000},
        "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"],
                         "default": "free"},
    },
    "state": {"tier": "{{account_tier}}", "message": "{{customer_message}}"},
    "questions": {
        "department": {"type": "choice",
                       "instructions": "Which department should handle this request?",
                       "criteria": {"billing": "invoices, payments, refunds",
                                    "technical": "bugs, outages, errors",
                                    "sales": "pricing, upgrades, new contracts"}},
        "urgency": {"type": "score", "instructions": "How urgent is this request?",
                    "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
        "churn_risk": {"type": "noul",
                       "instructions": "Does the customer threaten to cancel or leave?"},
    },
    "model": "laya",
    "settings": {"act_threshold": 0.85, "questions": {"churn_risk": {"act_threshold": 0.7}}},
    "extensions": {"questions": True, "max_questions": 4,
                   "options": ["department"], "skip": ["churn_risk"]},
    "metadata": {"owner_team": "support-eng"},
}).raise_for_status().json()
print(t["version"], t["change"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    id: "support-triage",
    name: "Support triage",
    description: "Route inbound tickets, rate urgency, flag churn risk.",
    variables: {
      customer_message: { type: "string", description: "The message, verbatim.", max_length: 8000 },
      account_tier: { type: "string", enum: ["free", "pro", "enterprise"], default: "free" },
    },
    state: { tier: "{{account_tier}}", message: "{{customer_message}}" },
    questions: {
      department: {
        type: "choice",
        instructions: "Which department should handle this request?",
        criteria: {
          billing: "invoices, payments, refunds",
          technical: "bugs, outages, errors",
          sales: "pricing, upgrades, new contracts",
        },
      },
      urgency: {
        type: "score",
        instructions: "How urgent is this request?",
        criteria: ["can wait", "soon", "today", "blocking or at risk of churn"],
      },
      churn_risk: { type: "noul", instructions: "Does the customer threaten to cancel or leave?" },
    },
    model: "laya",
    settings: { act_threshold: 0.85, questions: { churn_risk: { act_threshold: 0.7 } } },
    extensions: { questions: true, max_questions: 4, options: ["department"], skip: ["churn_risk"] },
    metadata: { owner_team: "support-eng" },
  }),
});
const t = await res.json();
```
@@ Response 201
```json
{
  "id": "support-triage",
  "object": "template",
  "name": "Support triage",
  "description": "Route inbound tickets, rate urgency, flag churn risk.",
  "origin": "user",
  "archived": false,
  "metadata": {"owner_team": "support-eng"},
  "storage": "full",
  "retention_days": null,
  "aliases": {"latest": 1},
  "examples_revision": 0,
  "examples_count": 0,
  "version": 1,
  "note": "First version",
  "content_hash": "sha256:f8ff188b8088fe4dc3a80b75e1a21c97337813d40955b027fa5af43d04c4a092",
  "modalities": ["text"],
  "variables": {
    "customer_message": {
      "type": "string",
      "description": "The message, verbatim.",
      "max_length": 8000,
      "required": true,
      "sensitive": false,
      "trusted": false
    },
    "account_tier": {
      "type": "string",
      "enum": ["free", "pro", "enterprise"],
      "default": "free",
      "required": false,
      "sensitive": false,
      "trusted": false
    }
  },
  "state": {
    "tier": "{{account_tier}}",
    "message": "{{customer_message}}"
  },
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
  "model": "laya",
  "settings": {
    "act_threshold": 0.85,
    "questions": {"churn_risk": {"act_threshold": 0.7}}
  },
  "extensions": {
    "questions": true,
    "max_questions": 4,
    "options": ["department"],
    "skip": ["churn_risk"]
  },
  "created_at": 1790814958,
  "updated_at": 1790814958,
  "last_used_at": null,
  "change": "created",
  "warnings": []
}
```
:::

### Clone a template

`from` copies a definition to start from. `{"template": "<reference>"}` copies another template's version, which is how you adapt a read-only starter template. `{"decision": "dec_..."}` builds a template from a stored decision: its questions, model and request settings become the template's, and its situation becomes variables (one per field of an object situation, or a single `message` variable for text). Any definition fields you send alongside `from` replace the copied ones.

:::console POST /v1/studio/templates
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates -d '{
  "id": "returns-desk",
  "name": "Returns desk",
  "from": {"template": "builtin/support@1"}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.post("/templates", json={
    "id": "returns-desk", "name": "Returns desk", "from": {"template": "builtin/support@1"},
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ id: "returns-desk", name: "Returns desk", from: { template: "builtin/support@1" } }),
});
const t = await res.json();
```
@@ Response 201
```json
{
  "id": "returns-desk",
  "object": "template",
  "name": "Returns desk",
  "origin": "user",
  "version": 1,
  "note": "",
  "questions": {
    "department": {
      "type": "choice",
      "instructions": "Which department should handle this request?",
      "criteria": {
        "billing": "invoices, payments, refunds",
        "technical": "bugs, outages, system errors",
        "sales": "pricing, upgrades, new contracts",
        "other": "anything else"
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
  "change": "created"
}
```
:::

The response is shortened to the fields that show the copy.

## The template object

| Field | Description |
|---|---|
| `id`, `name`, `description` | As saved. |
| `origin` | `user`, or `builtin` for starter templates. |
| `archived` | Hidden from the library; still callable. |
| `metadata`, `storage`, `retention_days` | The head settings. |
| `aliases` | Alias to version number. `latest` is always present. |
| `examples_revision`, `examples_count` | The [test examples](/docs/api/examples): how many, and a counter that rises with every change to them. |
| `version`, `note`, `content_hash` | The version shown, why it was saved, and a hash of its definition. |
| `modalities`, `variables`, `state`, `questions`, `model`, `settings`, `extensions` | The definition of that version. Variables come back with `required`, `sensitive` and `trusted` filled in. |
| `created_at`, `updated_at`, `last_used_at` | Unix seconds. `last_used_at` is the last decision made with the template. |
| `change`, `warnings` | On writes only. `change` is `created`, `new_version`, `metadata_only` or `unchanged`. |

### Variables

Each variable has a `type` and optional settings. A variable is required unless it has a `default` or `"required": false`. A default is used whenever the value is left out, so a variable with one always reads `"required": false`.

| Type | Settings | Value |
|---|---|---|
| `string` | `min_length`, `max_length` (default 20,000; at most 200,000), `pattern`, `enum`, `format` (`date` or `date-time`) | Text. |
| `integer`, `number` | `minimum`, `maximum`; `enum` for integers | A number. |
| `boolean` | | `true` or `false`. |
| `json` | `schema` (a JSON Schema), `max_bytes` (default 256 KB) | Any JSON value. |
| `options` | `min_items`, `max_items` | A list of option names, or an object of name to description. Supplies a `choice`, `multi` or `rank` question's options: `"criteria": "{{name}}"`. |
| `image`, `audio`, `video` | `max_bytes` | A file id from [Upload a file](/docs/api/resources#upload-a-file), or a `data:` URL. Attached to the decision as media, never placed in text. |

Every variable also takes `description`, `default`, `example`, and two flags:

- **`sensitive`**: the value is used for the decision but never stored. History keeps a keyed hash instead, so you can still [find and erase](/docs/api/history#delete-or-redact-many-decisions) every decision made with a given value. A sensitive variable cannot appear in question text, which history keeps.
- **`trusted`**: allows a free-text variable inside a question's text. Without it, a question may only use variables with a closed set of values (a number, a boolean, or a string with an `enum` or a date `format`), so a customer cannot rewrite what a question asks.

### The state

`state` is the situation the model reads, written with `{{variable}}` placeholders. It can be text or JSON.

- A placeholder that is a whole JSON value, such as `"{{open_invoices}}"`, keeps the variable's type: a number stays a number.
- A placeholder inside longer text is written as text. `\{{` writes a literal `{{`.
- An optional variable that was not sent removes its key from a JSON state, and becomes empty text inside a string.
- Without a `state`, the state is an object of every text variable. A template with no variables takes the whole situation from each request's `state`.

### Extensions

`extensions` decides how much one call may change the template.

| Field | Default | Description |
|---|---|---|
| `questions` | `true` | Whether callers may add their own questions. |
| `max_questions` | `16` | How many extra questions one call may add. |
| `options` | `[]` | Questions that accept new options in `add_options`: a list of keys, or `true` for all. Only `choice`, `multi`, `rank` and `number` questions qualify. |
| `skip` | `[]` | Questions callers may leave out with `skip`: a list of keys, or `true` for all. |

### How settings are chosen

Each question's act threshold and temperature come from the first of these places that sets them, from most specific to least:

| Order | Source | Where it is set |
|---|---|---|
| 1 | `request.questions` | The decision request's `settings.questions.<key>`. |
| 2 | `request` | The decision request's `settings`. |
| 3 | `template.models.<model>.questions` | The template's `settings.models.<model>.questions.<key>`. |
| 4 | `template.models.<model>` | The template's `settings.models.<model>`. |
| 5 | `template.questions` | The template's `settings.questions.<key>`. |
| 6 | `template` | The template's `settings`. |
| 7 | `studio` | The studio default: act threshold 0.9 (a [setting](/docs/api/resources#read-the-settings)), temperature 1. |

A decision's `settings.sources` names the source of every value it used, so you can always tell why an answer acted or not. Per-model settings let one template carry, for example, a higher temperature for a model known to be overconfident.

## Retrieve a template

::endpoint GET /v1/studio/templates/{id}

Returns a template with its latest version's definition, and an `etag` header holding the latest version number.

| Parameter | Description |
|---|---|
| `version` | A version number or alias, to read that version's definition instead. |
| `include` | `compatibility` adds the [model check](#check-which-models-can-run-a-template). |
| `include_deleted` | `true` to read a deleted template whose decisions are still in history. |

:::console GET /v1/studio/templates/{id}
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/templates/support-triage?version=production"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.get("/templates/support-triage", params={"version": "production"}).raise_for_status().json()
```
@@ JavaScript
```js
const t = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage?version=production",
)).json();
```
@@ Response 200
```json
{
  "id": "support-triage",
  "object": "template",
  "name": "Support triage",
  "aliases": {"latest": 2, "production": 2},
  "version": 2,
  "note": "Ask about refunds; act a little sooner",
  "content_hash": "sha256:639231b4efd705f3b8f46bc668f41f86794d0fb614a8f3a7ead4a90158dbabf1",
  "model": "laya",
  "settings": {
    "act_threshold": 0.8,
    "questions": {"churn_risk": {"act_threshold": 0.7}}
  },
  "created_at": 1790814958,
  "updated_at": 1790815038,
  "last_used_at": 1790815056
}
```
:::

The response is shortened; the full object has every field of [the template object](#the-template-object).

## List templates

::endpoint GET /v1/studio/templates

Returns the library, most recently changed first. Each item carries a `summary` (its question keys, variable names, model and modalities) instead of the full definition.

| Parameter | Description |
|---|---|
| `origin` | `user` or `builtin`. |
| `q` | Words in the id, name or description. |
| `include_archived` | `true` to list archived templates too. |
| `compatible_with` | A model id: only templates that model can run. |
| `metadata.<key>` | Exact match on a metadata value. |
| `include` | `definition` returns full definitions instead of summaries. |
| `limit`, `after` | [Pages of results](/docs/api/index#pages-of-results); `after` takes a template id. |

:::console GET /v1/studio/templates
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/templates?origin=user"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
for t in studio.get("/templates", params={"origin": "user"}).json()["data"]:
    print(t["id"], t["version"], t["summary"]["questions"])
```
@@ JavaScript
```js
const list = await (await fetch("http://127.0.0.1:8420/v1/studio/templates?origin=user")).json();
for (const t of list.data) console.log(t.id, t.version, t.summary.questions);
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {
      "id": "support-triage",
      "object": "template",
      "name": "Support triage",
      "description": "Route inbound tickets, rate urgency, flag churn risk.",
      "origin": "user",
      "archived": false,
      "metadata": {"owner_team": "support-eng"},
      "storage": "full",
      "retention_days": null,
      "aliases": {"latest": 2, "production": 2},
      "examples_revision": 0,
      "examples_count": 0,
      "version": 2,
      "note": "Ask about refunds; act a little sooner",
      "content_hash": "sha256:639231b4efd705f3b8f46bc668f41f86794d0fb614a8f3a7ead4a90158dbabf1",
      "created_at": 1790814958,
      "updated_at": 1790815038,
      "last_used_at": 1790815056,
      "summary": {
        "questions": [
          "department",
          "urgency",
          "churn_risk",
          "wants_refund"
        ],
        "variables": ["customer_message", "account_tier"],
        "model": "laya",
        "modalities": ["text"]
      }
    }
  ],
  "first_id": "support-triage",
  "last_id": "support-triage",
  "has_more": false
}
```
:::

## Create or replace a template

::endpoint PUT /v1/studio/templates/{id}

Makes the template match the body. It creates the template when it does not exist (`201`). Otherwise it saves a new version when the definition differs, and does nothing when it is the same, so a setup script or CI job can run it on every deploy. Definition fields you leave out reset to their defaults; head fields you leave out keep their values.

| Field | Description |
|---|---|
| Body | The same fields as [Create a template](#create-a-template), without `id` (it comes from the path). |
| `base_version` | Optional. The version your change is based on; a newer version answers `412 version_conflict`. |
| `If-Match` header | The same check, as a header: `If-Match: "2"`. |

`change` in the response says what happened: `created`, `new_version`, `metadata_only` (only head fields changed) or `unchanged`.

:::console PUT /v1/studio/templates/{id}
@@ curl
```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/returns-desk -d '{
  "name": "Returns desk",
  "description": "Sort return requests before a person sees them.",
  "variables": {"message": {"type": "string", "description": "The customer'\''s message."},
                "order_age_days": {"type": "integer", "minimum": 0}},
  "state": "Order placed {{order_age_days}} days ago.\n\n{{message}}",
  "questions": {
    "reason": {"type": "choice", "instructions": "Why does the customer want to return the item?",
               "criteria": {"damaged": "arrived broken or faulty",
                            "wrong_item": "not what was ordered",
                            "changed_mind": "no longer wanted"}},
    "within_policy": {"type": "noul",
                      "instructions": "Is the order within the 30-day return window?"}
  },
  "model": "laya",
  "note": "Returns, not general support"
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.put("/templates/returns-desk", json={
    "name": "Returns desk",
    "description": "Sort return requests before a person sees them.",
    "variables": {"message": {"type": "string", "description": "The customer's message."},
                  "order_age_days": {"type": "integer", "minimum": 0}},
    "state": "Order placed {{order_age_days}} days ago.\n\n{{message}}",
    "questions": {
        "reason": {"type": "choice", "instructions": "Why does the customer want to return the item?",
                   "criteria": {"damaged": "arrived broken or faulty",
                                "wrong_item": "not what was ordered",
                                "changed_mind": "no longer wanted"}},
        "within_policy": {"type": "noul",
                          "instructions": "Is the order within the 30-day return window?"},
    },
    "model": "laya",
    "note": "Returns, not general support",
}).raise_for_status().json()
print(t["version"], t["change"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/returns-desk", {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    name: "Returns desk",
    description: "Sort return requests before a person sees them.",
    variables: {
      message: { type: "string", description: "The customer's message." },
      order_age_days: { type: "integer", minimum: 0 },
    },
    state: "Order placed {{order_age_days}} days ago.\n\n{{message}}",
    questions: {
      reason: {
        type: "choice",
        instructions: "Why does the customer want to return the item?",
        criteria: { damaged: "arrived broken or faulty", wrong_item: "not what was ordered", changed_mind: "no longer wanted" },
      },
      within_policy: { type: "noul", instructions: "Is the order within the 30-day return window?" },
    },
    model: "laya",
    note: "Returns, not general support",
  }),
});
const t = await res.json();
console.log(t.version, t.change);
```
@@ Response 200
```json
{
  "id": "returns-desk",
  "object": "template",
  "version": 2,
  "note": "Returns, not general support",
  "aliases": {"latest": 2},
  "change": "new_version",
  "warnings": []
}
```
:::

The response is shortened. Sending the same body again returns the same template with `change: "unchanged"` and saves nothing.

## Update a template

::endpoint PATCH /v1/studio/templates/{id}

Changes part of a template with a merge patch: objects merge key by key, `null` removes a key, and anything else replaces. Here the patch adds a question to the existing three and lowers the act threshold, keeping the per-question threshold that was already there. A changed definition saves a new version; a change to head fields only saves none.

`id`, `version`, `aliases` and the other read-only fields cannot be patched (`400 read_only_field`), and neither can `archived`: use [archive](#archive-a-template). Send `If-Match` or `base_version` to make sure nobody saved a version since you read the template; if someone did, you get `412 version_conflict` with `current_version`.

:::console PATCH /v1/studio/templates/{id}
@@ curl
```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/templates/support-triage \
  -H 'If-Match: "1"' \
  -d '{
    "note": "Ask about refunds; act a little sooner",
    "questions": {"wants_refund": {"type": "noul",
                                   "instructions": "Does the customer ask for money back?"}},
    "settings": {"act_threshold": 0.8}
  }'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.patch("/templates/support-triage", headers={"If-Match": '"1"'}, json={
    "note": "Ask about refunds; act a little sooner",
    "questions": {"wants_refund": {"type": "noul",
                                   "instructions": "Does the customer ask for money back?"}},
    "settings": {"act_threshold": 0.8},
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage", {
  method: "PATCH",
  headers: { "Content-Type": "application/json", "If-Match": '"1"' },
  body: JSON.stringify({
    note: "Ask about refunds; act a little sooner",
    questions: { wants_refund: { type: "noul", instructions: "Does the customer ask for money back?" } },
    settings: { act_threshold: 0.8 },
  }),
});
const t = await res.json();
```
@@ Response 200
```json
{
  "id": "support-triage",
  "object": "template",
  "version": 2,
  "note": "Ask about refunds; act a little sooner",
  "aliases": {"latest": 2},
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
    },
    "wants_refund": {
      "type": "noul",
      "instructions": "Does the customer ask for money back?"
    }
  },
  "settings": {
    "act_threshold": 0.8,
    "questions": {"churn_risk": {"act_threshold": 0.7}}
  },
  "change": "new_version",
  "warnings": []
}
```
:::

The response is shortened. Decisions that ask for `support-triage` now run version 2; those that pin `support-triage@1` keep running version 1.

## List versions

::endpoint GET /v1/studio/templates/{id}/versions

Returns a template's versions, newest first. Each carries its note, the aliases pointing at it, and `changes`: how it differs from the version before. Add `include=definition` to get each definition too; `limit` and `after` (a version number) page through long histories.

| Field | Description |
|---|---|
| `base_version` | The version the edit started from. |
| `source` | How it was saved: `api`, `clone`, `restore` or `builtin`. |
| `changes.class` | The kind of change; see [change classes](#change-classes). |
| `changes.breaking_for_callers` | `true` when existing callers may now be refused, for example because a required variable was added or an extension removed. |
| `changes.questions` | Each question's comparability with the previous version. |
| `changes.summary` | The change in plain lines. |
| `changes.latest_callers_7d` | Decisions in the last 7 days that asked for the latest version, and so moved to this one when it was saved. |

:::console GET /v1/studio/templates/{id}/versions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/versions
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
for v in studio.get("/templates/support-triage/versions").json()["data"]:
    print(v["version"], v["changes"]["class"], v["changes"]["summary"])
```
@@ JavaScript
```js
const list = await (await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/versions")).json();
for (const v of list.data) console.log(v.version, v.changes.class, v.changes.summary);
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {
      "object": "template.version",
      "template": "support-triage",
      "version": 2,
      "note": "Ask about refunds; act a little sooner",
      "content_hash": "sha256:639231b4efd705f3b8f46bc668f41f86794d0fb614a8f3a7ead4a90158dbabf1",
      "questions_hash": "sha256:895aac4a4c3e7aa4e6e0a1739f25014987442e349f7a07b5ac77ee7e9b05b19c",
      "question_set_key": "sha256:e3905302aa4f6ab71fe17b462594784d4aaf7ecf7d20edf16fe2fc126c1caaf4",
      "source": "api",
      "base_version": 1,
      "aliases": ["latest"],
      "changes": {
        "from": 1,
        "class": "extended",
        "breaking_for_callers": false,
        "questions": {
          "department": "identical",
          "urgency": "identical",
          "churn_risk": "identical",
          "wants_refund": "added"
        },
        "summary": [
          "wants_refund: question added",
          "settings.act_threshold: 0.85 -> 0.8"
        ],
        "latest_callers_7d": 5
      },
      "created_at": 1790815038,
      "created_by": "local"
    }
  ],
  "first_id": 2,
  "last_id": 1,
  "has_more": false
}
```
:::

The response is shortened to the newest version; version 1 follows it with `class: "created"`.

### Change classes

| Class | Meaning |
|---|---|
| `created` | The first version. |
| `breaking` | A question was removed or changed type, a scale's number of levels changed, options were removed, or a variable was removed, retyped, made required or narrowed (it now refuses values it accepted: fewer allowed values, a lower maximum, a higher minimum, a new pattern). Results before and after are not directly comparable. |
| `extended` | Questions, options, optional variables or modalities were added. Existing answers still mean the same. |
| `wording` | Question text or the state template changed, or a variable became more permissive. |
| `settings_only` | Only the model, settings or extensions changed. |

Per question, comparability is `identical`, `text_changed`, `options_changed` (with `added_options` and `removed_options`), `added`, `removed` or `incomparable`.

## Retrieve a version

::endpoint GET /v1/studio/templates/{id}/versions/{n}

Returns one version with its full definition. `n` is a version number, an alias such as `production`, or `latest`. Versions of a deleted template stay readable while its decisions are in history.

## Save a version

::endpoint POST /v1/studio/templates/{id}/versions

Saves a complete definition as the next version. Unlike [PATCH](#update-a-template), nothing is merged: fields you leave out reset to their defaults. Send `base_version` (or `If-Match`) to refuse the save when someone else saved first. The response is the new version object, with `x-basal-version-created: true`; an identical definition saves nothing and returns the current version with `false`.

:::console POST /v1/studio/templates/{id}/versions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/returns-desk/versions -d '{
  "variables": {"message": {"type": "string", "description": "The customer'\''s message."},
                "order_age_days": {"type": "integer", "minimum": 0}},
  "state": "Order placed {{order_age_days}} days ago.\n\n{{message}}",
  "questions": {
    "reason": {"type": "choice", "instructions": "Why does the customer want to return the item?",
               "criteria": {"damaged": "arrived broken or faulty",
                            "wrong_item": "not what was ordered",
                            "changed_mind": "no longer wanted"}},
    "within_policy": {"type": "noul",
                      "instructions": "Is the order within the 30-day return window?"}
  },
  "model": "laya",
  "settings": {"act_threshold": 0.8},
  "note": "Act at 0.8",
  "base_version": 2
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
current = studio.get("/templates/returns-desk").json()
definition = {k: current[k] for k in ("modalities", "variables", "state", "questions",
                                      "model", "settings", "extensions")}
definition["settings"] = {"act_threshold": 0.8}
r = studio.post("/templates/returns-desk/versions",
                json={**definition, "note": "Act at 0.8", "base_version": current["version"]})
print(r.headers["x-basal-version-created"], r.json()["version"])
```
@@ JavaScript
```js
const base = "http://127.0.0.1:8420/v1/studio/templates/returns-desk";
const current = await (await fetch(base)).json();
const { modalities, variables, state, questions, model, extensions } = current;
const res = await fetch(`${base}/versions`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    modalities, variables, state, questions, model, extensions,
    settings: { act_threshold: 0.8 },
    note: "Act at 0.8",
    base_version: current.version,
  }),
});
console.log(res.headers.get("x-basal-version-created"), (await res.json()).version);
```
@@ Response 200
```json
{
  "object": "template.version",
  "template": "returns-desk",
  "version": 3,
  "note": "Act at 0.8",
  "source": "api",
  "base_version": 2,
  "aliases": ["latest"],
  "changes": {
    "from": 2,
    "class": "settings_only",
    "breaking_for_callers": false,
    "questions": {
      "reason": "identical",
      "within_policy": "identical"
    },
    "summary": ["settings.act_threshold: null -> 0.8"],
    "latest_callers_7d": 0
  },
  "settings": {"act_threshold": 0.8},
  "created_at": 1790815090,
  "created_by": "local"
}
```
:::

The response is shortened; it also carries the hashes and the full definition.

## See what changed between versions

::endpoint GET /v1/studio/templates/{id}/versions/{n}/diff

Compares version `n` with the version before it, or with `?against=<n>`. The result has the change class, each question's comparability, changed variables and settings (by path), the model and modalities before and after, a plain summary, and `changes`: JSON Patch operations that turn one definition into the other.

:::console GET /v1/studio/templates/{id}/versions/{n}/diff
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/versions/2/diff
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
d = studio.get("/templates/support-triage/versions/2/diff").raise_for_status().json()
print(d["class"], d["summary"])
```
@@ JavaScript
```js
const d = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/versions/2/diff",
)).json();
console.log(d.class, d.summary);
```
@@ Response 200
```json
{
  "object": "template.diff",
  "template": "support-triage",
  "from": 1,
  "to": 2,
  "class": "extended",
  "breaking_for_callers": false,
  "questions": {
    "department": {"comparability": "identical"},
    "urgency": {"comparability": "identical"},
    "churn_risk": {"comparability": "identical"},
    "wants_refund": {"comparability": "added"}
  },
  "variables": {},
  "settings": {"/act_threshold": {"from": 0.85, "to": 0.8}},
  "model": {"from": "laya", "to": "laya"},
  "modalities": {"from": ["text"], "to": ["text"]},
  "summary": [
    "wants_refund: question added",
    "settings.act_threshold: 0.85 -> 0.8"
  ],
  "changes": [
    {
      "op": "add",
      "path": "/questions/wants_refund",
      "value": {
        "type": "noul",
        "instructions": "Does the customer ask for money back?"
      }
    },
    {"op": "replace", "path": "/settings/act_threshold", "value": 0.8}
  ]
}
```
:::

## Restore a version

::endpoint POST /v1/studio/templates/{id}/versions/{n}/restore

Saves an old version's definition as a new version, so history stays a straight line and nothing is rewritten. The body may carry a `note`; it defaults to "Restore version n".

:::console POST /v1/studio/templates/{id}/versions/{n}/restore
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/returns-desk/versions/2/restore \
  -d '{"note": "Back to version 2"}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.post("/templates/returns-desk/versions/2/restore",
                json={"note": "Back to version 2"}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/returns-desk/versions/2/restore", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ note: "Back to version 2" }),
});
const t = await res.json();
```
@@ Response 200
```json
{
  "id": "returns-desk",
  "object": "template",
  "version": 4,
  "note": "Back to version 2",
  "aliases": {"latest": 4},
  "content_hash": "sha256:a277261f27b677398c3f9e9b88891d9f348af66762203f953672f075246e4610",
  "change": "new_version"
}
```
:::

The response is shortened. Version 4 has the same `content_hash` as version 2, because its definition is the same.

## Point an alias at a version

::endpoint PUT /v1/studio/templates/{id}/aliases/{alias}

::endpoint DELETE /v1/studio/templates/{id}/aliases/{alias}

An alias is a name for a version, such as `production` or `staging`. Callers that send `support-triage@production` follow it, so you can save and test a new version, then move every caller to it in one call, and move them back just as fast. Alias names are 1 to 32 lower-case letters, digits, `-` and `_`, starting with a letter; `latest` is reserved and always means the newest version.

| Field | Type | Description |
|---|---|---|
| `version` | integer | Required for `PUT`. The version the alias should name. |

`PUT` answers with the alias and the version it pointed to before (`previous_version`). `DELETE` removes the alias; callers that still use it then get `404 alias_not_found`.

:::console PUT /v1/studio/templates/{id}/aliases/{alias}
@@ curl
```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/support-triage/aliases/production \
  -d '{"version": 2}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
a = studio.put("/templates/support-triage/aliases/production",
               json={"version": 2}).raise_for_status().json()
print("moved from", a["previous_version"], "to", a["version"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/aliases/production", {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ version: 2 }),
});
const a = await res.json();
```
@@ Response 200
```json
{
  "object": "template.alias",
  "template": "support-triage",
  "alias": "production",
  "version": 2,
  "previous_version": 1,
  "updated_at": 1790815048
}
```
:::

## Archive a template

::endpoint POST /v1/studio/templates/{id}/archive

::endpoint POST /v1/studio/templates/{id}/unarchive

Archiving hides a template from the library without breaking anyone. Decisions that use it still run, with a `template_archived` warning so callers notice. Saving a new version of an archived template answers `409 template_archived`; unarchive it first. Both calls return the template object.

:::console POST /v1/studio/templates/{id}/archive
@@ curl
```bash
curl -s -X POST http://127.0.0.1:8420/v1/studio/templates/returns-desk/archive
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
t = studio.post("/templates/returns-desk/archive").raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/returns-desk/archive", { method: "POST" });
const t = await res.json();
```
@@ Response 200
```json
{
  "id": "returns-desk",
  "object": "template",
  "archived": true,
  "version": 4
}
```
:::

The response is shortened to the fields that matter here.

## Delete a template

::endpoint DELETE /v1/studio/templates/{id}

Deleting breaks every caller that uses the template, so it asks you to repeat the id.

| Parameter | Description |
|---|---|
| `confirm` | Required. The template id again. Without it the call answers `400 confirmation_required`. |
| `history` | `keep` (the default) or `delete`. |

With `history=keep`, the template's decisions stay in history, along with the versions they used (`versions_kept`), so you can still read and filter them. Aliases and test examples are deleted. The id stays reserved while those decisions exist: creating a new template with it answers `409 template_id_reserved`. With `history=delete`, the template and all its decisions are deleted (`decisions_deleted`).

:::console DELETE /v1/studio/templates/{id}
@@ curl
```bash
curl -s -X DELETE "http://127.0.0.1:8420/v1/studio/templates/returns-desk?confirm=returns-desk&history=keep"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.delete("/templates/returns-desk",
                  params={"confirm": "returns-desk", "history": "keep"}).raise_for_status().json()
```
@@ JavaScript
```js
const q = new URLSearchParams({ confirm: "returns-desk", history: "keep" });
const res = await fetch(`http://127.0.0.1:8420/v1/studio/templates/returns-desk?${q}`, { method: "DELETE" });
const r = await res.json();
```
@@ Response 200
```json
{
  "id": "returns-desk",
  "object": "template.deleted",
  "deleted": true,
  "versions_kept": 1,
  "decisions_deleted": 0
}
```
:::

## Variables as JSON Schema

::endpoint GET /v1/studio/templates/{id}/schema

Returns the variables of a version (`?version=`, latest by default) as a JSON Schema, ready for form builders, validators and code generators. Studio-specific details travel in `x-basal-` keys: the template reference, its questions, its extensions, and which variables are sensitive or are media.

:::console GET /v1/studio/templates/{id}/schema
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/schema
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
schema = studio.get("/templates/support-triage/schema").raise_for_status().json()
```
@@ JavaScript
```js
const schema = await (await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/schema")).json();
```
@@ Response 200
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "support-triage@2 variables",
  "type": "object",
  "additionalProperties": false,
  "required": ["customer_message"],
  "properties": {
    "customer_message": {
      "type": "string",
      "maxLength": 8000,
      "description": "The message, verbatim."
    },
    "account_tier": {
      "type": "string",
      "enum": ["free", "pro", "enterprise"],
      "default": "free"
    }
  },
  "x-basal-template": "support-triage@2",
  "x-basal-questions": [
    "department",
    "urgency",
    "churn_risk",
    "wants_refund"
  ],
  "x-basal-extensions": {
    "questions": true,
    "max_questions": 4,
    "options": ["department"],
    "skip": ["churn_risk"]
  }
}
```
:::

## Check which models can run a template

::endpoint GET /v1/studio/templates/{id}/compatibility

Checks every model the studio knows against a version (`?version=`, latest by default), before anything loads. Each model has `ok`, its `status` (`loaded`, `loading`, `downloaded`, `not_downloaded`), the `problems` that stop it, and `notes`.

Problems are what stops a model: question types or option counts it cannot handle, too many questions, or a required file it cannot read. Notes are what to watch for on a model that can run the template: a variable that may hold more text than the model reads (longer values are cut off at the end, and each such decision carries a `state_may_be_truncated` warning), or an optional file the model cannot read.

Versions before 0.3.0 reported the first of those notes as a problem, `context_too_small`, which marked the model `ok: false` although its decisions ran.

:::console GET /v1/studio/templates/{id}/compatibility
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/compatibility
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
c = studio.get("/templates/support-triage/compatibility").raise_for_status().json()
print([m["model"] for m in c["models"] if m["ok"]])
```
@@ JavaScript
```js
const c = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/compatibility",
)).json();
console.log(c.models.filter((m) => m.ok).map((m) => m.model));
```
@@ Response 200
```json
{
  "object": "template.compatibility",
  "template": "support-triage",
  "version": 2,
  "models": [
    {
      "model": "laya",
      "name": "Laya",
      "status": "downloaded",
      "ok": true,
      "problems": [],
      "notes": [
        "customer_message allows 8,000 characters (about 2,000 tokens); Laya reads 512 tokens, so a longer value is cut off at the end.",
        "Cannot read the optional image variable 'screenshot'; decisions that send it will be refused."
      ]
    },
    {
      "model": "kev-4b",
      "name": "Kev 4B",
      "status": "downloaded",
      "ok": true,
      "problems": [],
      "notes": [
        "Cannot read the optional image variable 'screenshot'; decisions that send it will be refused."
      ]
    },
    {
      "model": "jev-omni",
      "name": "Jev-Omni",
      "status": "downloaded",
      "ok": true,
      "problems": [],
      "notes": []
    }
  ]
}
```
:::

The response is shortened to 3 of the 11 models.

## A template's history and statistics

::endpoint GET /v1/studio/templates/{id}/decisions

::endpoint GET /v1/studio/templates/{id}/stats

These are [List decisions](/docs/api/history#list-decisions) and [Decision statistics](/docs/api/history#decision-statistics) fixed to one template, and take the same parameters (plus `version` to pick versions). Statistics group by version by default, and each question says how comparable it is with the first version listed (`reference`), so you can tell a real change in answers from a change in the question.

:::console GET /v1/studio/templates/{id}/stats
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/templates/support-triage/stats?questions=department"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
s = studio.get("/templates/support-triage/stats", params={"questions": "department"}).json()
for g in s["groups"]:
    print(g["key"]["version"], g["questions"]["department"]["distribution"])
```
@@ JavaScript
```js
const s = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/stats?questions=department",
)).json();
for (const g of s.groups) console.log(g.key.version, g.questions.department.distribution);
```
@@ Response 200
```json
{
  "object": "decision.stats",
  "group_by": ["version"],
  "filters": {
    "template": "support-triage",
    "attribution": ["explicit", "header"]
  },
  "what_if": null,
  "groups": [
    {
      "key": {"version": 1},
      "count": 13,
      "failed": 0,
      "act_rate": 0.0,
      "latency_ms": {"p50": 180.0, "p95": 384.6},
      "questions": {
        "department": {
          "runs": 12,
          "excluded_runs": 1,
          "mean_certainty": 0.8838,
          "act_rate": 0.75,
          "distribution": {
            "billing": 0.5833,
            "technical": 0.3333,
            "sales": 0.0833
          },
          "labelled": 0,
          "accuracy": null,
          "comparability": "reference"
        }
      }
    },
    {
      "key": {"version": 2},
      "count": 8,
      "failed": 0,
      "act_rate": 0.0,
      "latency_ms": {"p50": 143.1, "p95": 158.9},
      "questions": {
        "department": {
          "runs": 8,
          "excluded_runs": 0,
          "mean_certainty": 0.8609,
          "act_rate": 0.75,
          "distribution": {
            "billing": 0.5,
            "technical": 0.375,
            "sales": 0.125
          },
          "labelled": 0,
          "accuracy": null,
          "comparability": "identical"
        }
      }
    }
  ]
}
```
:::

## Compare two versions over history

::endpoint GET /v1/studio/templates/{id}/compare

Compares how two versions actually answered. For each question it gives each version's answer spread, certainty, act rate and accuracy, and, where both versions saw the same input, how often they agreed and which answers flipped. Inputs are paired by their variables (`paired_by: "variables_hash"`), or by the whole input for templates without variables.

| Parameter | Description |
|---|---|
| `versions` | Two versions, such as `1,2`. Defaults to the latest two. |
| `model` | Only decisions from this model. |
| `created_after`, `created_before` | A time window, such as `-7d`. |

`paired.flips` counts each change of answer, such as `{"from": "billing", "to": "account", "n": 3}`, and `paired.sample` gives pairs of decision ids to open and read. Questions that changed incomparably have no pairing. `operational` compares counts, failures and speed.

:::console GET /v1/studio/templates/{id}/compare
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/templates/support-triage/compare?versions=1,2"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
c = studio.get("/templates/support-triage/compare", params={"versions": "1,2"}).json()
for key, q in c["questions"].items():
    print(key, q["comparability"], q.get("paired", {}).get("agreement"))
```
@@ JavaScript
```js
const c = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/compare?versions=1,2",
)).json();
for (const [key, q] of Object.entries(c.questions)) console.log(key, q.comparability, q.paired?.agreement);
```
@@ Response 200
```json
{
  "object": "template.comparison",
  "template": "support-triage",
  "versions": [1, 2],
  "filters": {
    "template": "support-triage",
    "attribution": ["explicit", "header"]
  },
  "paired_by": "variables_hash",
  "questions": {
    "department": {
      "comparability": "identical",
      "by_version": {
        "1": {
          "n": 12,
          "distribution": {
            "billing": 0.5833,
            "technical": 0.3333,
            "sales": 0.0833
          },
          "mean_certainty": 0.8838,
          "act_rate": 0.75,
          "feedback": {"labelled": 0, "accuracy": null}
        },
        "2": {
          "n": 8,
          "distribution": {
            "billing": 0.5,
            "technical": 0.375,
            "sales": 0.125
          },
          "mean_certainty": 0.8609,
          "act_rate": 0.75,
          "feedback": {"labelled": 0, "accuracy": null}
        }
      },
      "paired": {
        "n": 8,
        "agreement": 1.0,
        "flips": [],
        "sample": [
          [
            "dec_01M3TE9M200ERSBXY5ZKQ0SSZJ",
            "dec_01M3TEABWQ26PMM3KMJ1Z2N6YV"
          ],
          [
            "dec_01M3TE9M6X0RFC0ZWRABJ3S9D1",
            "dec_01M3TEAC0GKK6SGVACW8VDGN1Q"
          ]
        ]
      }
    }
  },
  "evals": {},
  "operational": {
    "1": {
      "n": 13,
      "failed": 0,
      "latency_ms": {"p50": 180.0, "p95": 384.6}
    },
    "2": {
      "n": 8,
      "failed": 0,
      "latency_ms": {"p50": 143.1, "p95": 158.9}
    }
  }
}
```
:::

The response is shortened to one question and two of its eight sample pairs. Here the same eight tickets ran on both versions and the department never changed, so version 2's new question added nothing that moved the routing.
