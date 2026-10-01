---
title: Templates and versions
description: What a template holds, how variables become the situation a model reads, how every change becomes a version, and how aliases and extensions let code and templates change at their own pace.
lead: A template is a decision you reuse. It holds the questions, the variables that fill in the situation, a default model and settings. Every change to it is saved as a new version that never changes again, so you always know which questions produced a past answer.
---

## What a template holds

Callers send a template's id and the values of its variables; the template supplies everything else. The response on the right is the support-triage template used throughout these docs, at version 2.

| Field | Meaning |
|---|---|
| `id` | The name used in code, chosen once and never changed: lowercase letters, digits, `-` and `_`. |
| `name`, `description`, `metadata` | For people. Changing them never creates a version. |
| `variables` | The values callers fill in, each with a type. See [Variables](#variables). |
| `state` | How the variables become the situation: text or JSON with `{{name}}` placeholders. |
| `questions` | 1 to 128 questions, in any of the [six types](/docs/concepts/question-types). |
| `modalities` | The kinds of input callers may attach: `text`, plus `image`, `audio` or `video`. |
| `model` | The default model. `null` means the most recently loaded model. |
| `settings` | Act threshold and temperature, with per-model and per-question values. See [Where the settings come from](/docs/concepts/acting#where-the-settings-come-from). |
| `extensions` | What callers may change per decision. See [Extensions](#extensions). |
| `storage`, `retention_days` | How much of this template's history is kept, and for how long. See [History and privacy](/docs/concepts/history). |
| `aliases` | Names for versions, such as `production`. `latest` always points at the newest. |
| `version`, `note` | The version shown, and why it was saved. |

A template works with every model. Limits such as the number of options are checked against the model that answers each decision, and the template page in the app lists which models can run it.

:::console GET /v1/studio/templates/{id}
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage
```
@@ Response 200
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
  "aliases": {"latest": 2, "production": 2},
  "examples_revision": 1,
  "examples_count": 1,
  "version": 2,
  "note": "Add the account team; ask about refunds; act a little sooner",
  "content_hash": "sha256:dc9d7ac12f17ca5058a94453934581e092833b263a655d070949622dcddd35fe",
  "modalities": ["text", "image"],
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000,
                         "required": true, "sensitive": false, "trusted": false},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free",
                     "required": false, "sensitive": false, "trusted": false},
    "open_invoices": {"type": "integer", "minimum": 0, "required": false, "sensitive": false, "trusted": false},
    "customer_email": {"type": "string", "max_length": 320, "required": false, "sensitive": true, "trusted": false},
    "screenshot": {"type": "image", "required": false, "sensitive": false, "trusted": false}
  },
  "state": {
    "customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
    "message": "{{customer_message}}"
  },
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "other": "anything else",
                                "account": "login, seats, access"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}
  },
  "model": "laya",
  "settings": {
    "act_threshold": 0.85,
    "temperature": 1.1,
    "questions": {"churn_risk": {"act_threshold": 0.7}},
    "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}
  },
  "extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
  "created_at": 1790809637,
  "updated_at": 1790809637,
  "last_used_at": 1790809696
}
```
:::

## Variables

A variable is a typed value the caller sends with each decision. Types are strict: `"42"` is not an integer, and a typo in a variable name is reported rather than ignored, with a suggestion for the name you probably meant. Every problem in a request is reported at once.

| `type` | Value | Constraints you can set |
|---|---|---|
| `string` | text | `min_length`, `max_length` (default 20,000, at most 200,000), `pattern`, `enum`, `format` (`date` or `date-time`) |
| `integer` | a whole number | `minimum`, `maximum`, `enum` |
| `number` | any number | `minimum`, `maximum` |
| `boolean` | `true` or `false` | none |
| `json` | any JSON value | `schema` (JSON Schema), `max_bytes` (default 256 KB) |
| `options` | a list of names, or names with descriptions | `min_items`, `max_items` (up to 1,000). Becomes a question's options; see below. |
| `image`, `audio`, `video` | a file id (`file_…`) or a `data:` URL | `max_bytes` (default 200 MB). Attached as media, never written into text. |

Every variable can also have:

| Key | Meaning |
|---|---|
| `description` | Shown in the app's form and in the schema. Never sent to the model. |
| `required` | `true` unless the variable has a `default`: the default is used whenever the value is left out, so a variable with one is never required. A missing or `null` value counts as missing. |
| `default` | Used when the caller leaves the variable out. |
| `example` | A sample value for the app's form. |
| `sensitive` | Used for the decision, never written to disk; History keeps only a keyed hash. Applies to text, number and JSON variables in 0.2.1. See [History and privacy](/docs/concepts/history#sensitive-variables). |
| `trusted` | Allows a free-text variable inside question text (see below). |

A template has at most 64 variables. `GET /v1/studio/templates/{id}/schema` returns them as a JSON Schema, which the app uses for its form.

### Variables in question text

A variable can appear in a question's instructions or in option descriptions, as in the `urgency` question above. Variables with a closed set of values (`string` with `enum`, `integer`, `number`, `boolean`, `string` with `format`) are always allowed there. A free-text `string` or `json` variable is refused when the version is saved, unless you declare it `trusted: true`: text in a question changes what the question asks, so end-user text there could rewrite it.

Placeholders are never allowed in question keys, option names, number values or the number of levels, because those are the vocabulary History aggregates by.

## How variables become the situation

Substitution is deliberately simple: `{{name}}` is replaced by the value, in one pass, with no expressions, conditions or loops. A customer who types `{{account_tier}}` in a message has it read literally.

- In a JSON state, a string that is exactly one placeholder takes the value with its type: numbers stay numbers, objects stay objects.
- A placeholder inside longer text, and every placeholder in a text state, is written as text.
- An optional variable that was not sent removes its key from the JSON (so the model never reads `tier:` with nothing after it), and becomes an empty string inside text.
- With `"state": null`, the state is simply an object of the variables, in the order they are declared.
- Media variables never appear in text; each becomes an attachment.

The preview on the right sends two of the template's five variables to version 2. The keys for `open_invoices` and `customer_email` are gone from the state, the plan appears in the `urgency` question's text, and `rendered_state` is the text the model reads. `POST /v1/studio/decisions/preview` checks and renders a decision like this without running a model, which makes it the quickest way to test a template.

:::console POST /v1/studio/decisions/preview
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/preview -d '{
  "template": "support-triage@2",
  "variables": {
    "customer_message": "We were billed twice for March. Refund the duplicate today or we cancel.",
    "account_tier": "enterprise"
  }
}'
```
@@ Response 200
```json
{
  "object": "decision.preview",
  "template": {"id": "support-triage", "version": 2, "ref": "support-triage@2",
               "resolved_from": "pinned", "attribution": "explicit"},
  "model": "laya",
  "state": {
    "customer": {"tier": "enterprise"},
    "message": "We were billed twice for March. Refund the duplicate today or we cancel."
  },
  "rendered_state": "customer:\n  tier: enterprise\nmessage: We were billed twice for March. Refund the duplicate today or we cancel.",
  "questions": {
    "urgency": {
      "type": "score",
      "instructions": "How urgent is this request? The customer is on the enterprise plan.",
      "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]
    }
  }
}
```
:::

## Options from a variable

A question's whole option list can come from an `options` variable: write `"criteria": "{{actions}}"` on a `choice`, `multi` or `rank` question. Callers then send the options with each decision. This suits lists that change every call, such as the actions an agent can take right now; the [agent guide](/docs/guides/agents) builds one.

Answers whose options came from a variable carry `dynamic_options: true`. Statistics leave them out of option distributions, since the options differ from call to call, but count them for accuracy, act rate and timing.

## Versions

Every change to what a template decides creates a new, numbered version: 1, 2, 3, with no gaps. A version never changes once saved. Each decision records the exact version it used, so History can always show the questions behind an answer and compare versions fairly.

- **What creates a version:** a change to `modalities`, `variables`, `state`, `questions`, `model`, `settings` or `extensions`, whether sent with `PUT`, `PATCH` or `POST …/versions`, or by restoring an old version.
- **What does not:** a save identical to the latest version (the response says `"change": "unchanged"`), and changes to `name`, `description`, `metadata`, `storage`, `retention_days`, aliases or archiving (`"change": "metadata_only"`). Setup scripts can therefore run the same `PUT` again and again safely.
- **Two people editing at once:** send `If-Match: "<version>"` (or `base_version` in the body) and a save based on an older version is refused with `412 version_conflict`, naming the current version.

Each new version records how it differs from the one before:

| Class | What changed | Callers |
|---|---|---|
| `settings_only` | The model, settings or extensions | Keep working |
| `wording` | Instructions, descriptions, level texts, the state template, or a variable made more permissive | Keep working |
| `extended` | Questions, options, optional variables or modalities were added; nothing was removed | Keep working |
| `breaking` | Something was removed, renamed or re-typed; a score's levels changed; a required variable was added; a variable was narrowed (fewer allowed values, a lower maximum, a new pattern) | Some calls may get a 400; `breaking_for_callers` says so |

And for each question, how comparable its answers are across the two versions: `identical`, `text_changed`, `options_changed` (compared on the options both share), `incomparable` (the type or the number of levels changed), `added` or `removed`. History and the version comparison use these to decide what can be charted as one series.

## Calling a version

There is one way to name a template in a request, a query or History:

| Reference | Means |
|---|---|
| `support-triage` | The latest version when the request arrives |
| `support-triage@3` | Exactly version 3 |
| `support-triage@production` | Whichever version the `production` alias points at |

An **alias** is a name you point at a version, and move later. Code that calls `support-triage@production` keeps working, unchanged, while you promote version 4 to production with one request, and moves back just as quickly if version 4 misbehaves. Calls to the bare id follow every new version immediately; each new version reports in `latest_callers_7d` how many recent decisions did that.

Every decision records the version it used, the reference it was called with, and how that resolved (`latest`, a pinned number or an alias).

:::console PUT /v1/studio/templates/{id}/aliases/{alias}
@@ curl
```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/support-triage/aliases/production \
  -d '{"version": 3}'
```
@@ Response 200
```json
{
  "object": "template.alias",
  "template": "support-triage",
  "alias": "production",
  "version": 3,
  "previous_version": 2,
  "updated_at": 1790815431
}
```
:::

## Extensions

A template can let callers adapt a single decision without changing the template. Its `extensions` field says what is allowed:

| Request field | Allowed when | Effect |
|---|---|---|
| `questions` | `extensions.questions` is `true` (the default), up to `max_questions` (default 16) | Extra questions with new keys, asked alongside the template's. |
| `add_options` | The question is listed in `extensions.options` | New options appended to a `choice`, `multi`, `rank` or `number` question. Never to a `score` or yes-or-no question, because a new level would change what the scale means. |
| `skip` | The question is listed in `extensions.skip` | Leaves that question out of this decision. |

A caller can never rewrite one of the template's own questions. Sending a question under an existing key returns `400 question_conflict`; change the template instead. This keeps one meaning per question key across all of History.

Each answer's `origin` records what happened: `template` (unchanged), `extended` (options were added), or `extra` (a caller's own question). Statistics and version comparisons count only unchanged template questions by default.

The decision on the right calls the `production` version, adds a `finance_ops` department, skips `churn_risk` and asks one extra question.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage@production",
  "variables": {"customer_message": "I need an invoice in our company name for the March payment.",
                "account_tier": "pro"},
  "add_options": {"department": {"finance_ops": "invoice copies, tax documents"}},
  "skip": ["churn_risk"],
  "questions": {
    "needs_invoice": {"type": "noul", "instructions": "Does the customer ask for an invoice or a receipt?"}
  }
}'
```
@@ Response 200
```json
{
  "template": {"id": "support-triage", "version": 2, "ref": "support-triage@production",
               "resolved_from": "alias", "attribution": "explicit"},
  "extensions": {"questions": ["needs_invoice"], "options": {"department": ["finance_ops"]},
                 "skipped": ["churn_risk"]},
  "answers": {
    "department": {"type": "choice", "choice": "billing",
                   "probabilities": {"billing": 0.8083, "technical": 0.0128, "sales": 0.0156,
                                     "other": 0.0187, "account": 0.0128, "finance_ops": 0.1317},
                   "certainty": 0.8083, "act": false, "origin": "extended"},
    "urgency": {"type": "score", "decision": "2", "certainty": 0.4013, "act": false, "origin": "template"},
    "wants_refund": {"type": "noul", "noul": 0.172, "decision": "no",
                     "certainty": 0.828, "act": false, "origin": "template"},
    "needs_invoice": {"type": "noul", "noul": 0.8521, "decision": "yes",
                      "certainty": 0.8521, "act": true, "origin": "extra"}
  },
  "act": false,
  "needs_review": ["department", "urgency", "wants_refund"]
}
```
:::

## Starter templates

The studio comes with a read-only starter template for every Playground example, named `builtin/<example>`: `builtin/support`, `builtin/agent-browser`, `builtin/image` and so on. They take a whole situation instead of variables. Call them as they are, or clone one and make it yours; in the app, open it and choose **Clone to edit**. When a studio release changes a starter template, it gains a new version; your own templates never change by themselves.

## Archiving and deleting

- **Archive** hides a template from the library and keeps it callable, so code that uses it keeps working; its decisions carry a `template_archived` warning. New versions are refused until you unarchive it.
- **Delete** is the only change that breaks callers: they get `template_not_found`. By default the versions that History refers to stay readable, so past decisions still show their questions; deleting with `history=delete` removes those decisions too. Over the API, a delete must repeat the id as `?confirm=<id>`, so a mistyped path cannot delete the wrong template.
