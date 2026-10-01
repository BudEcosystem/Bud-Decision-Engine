---
title: History and privacy
description: What Bud Decision Studio records for every decision, the three storage levels, how to opt out, sensitive variables, retention, and how to find, export, redact and erase decisions.
lead: Every decision is recorded on your computer, whichever way it arrived, so you can review it, label it and compare versions later. You decide how much of each decision is kept and for how long, and personal data can be kept out entirely.
---

## What is recorded

History is one database on your computer (`studio.db` in the studio's data folder). It records decisions from every entry point: the Playground, the studio API (`/v1/studio`), TypeSafe's API (`/v1/systemone`), the OpenRouter and Vercel formats, Compare and Evaluate. A decision made by your code looks the same in History as one made in the app.

At the default level, `full`, a decision keeps:

- the situation: the variables or state, the text the model read, and any attached files;
- the questions as asked, including caller extensions;
- every answer with its probabilities, the probabilities before calibration, certainty and act gate;
- the model, the template version, and every setting with the layer it came from;
- timing, token counts, where the request came from (surface, endpoint, format and client) and its request id;
- your `metadata`: up to 16 keys of your own, such as a ticket id, for joining History to your systems.

Nothing stored ever changes an answer. On TypeSafe's routes the response body is byte-for-byte the same whether or not the call is stored; the studio only adds headers such as `x-basal-decision-id`.

## Storage levels

Each decision is stored at one of three levels:

| Level | What is kept | What still works |
|---|---|---|
| `full` | Everything above | Review, labels, statistics, rerun, test examples |
| `answers_only` | Everything except the situation: no variables, state, rendered text, file contents or file names (a file's type, size and hash are kept) | Review of answers, labels, statistics. Rerun and replay do not: `409 input_unavailable`. |
| `none` | Nothing | The response still has an `id` for your logs, with `store: "none"`; reading it back returns 404 |

`metadata` is kept at `answers_only` too, because it is your key for joining decisions to your own records. Put ids in it, not personal data.

The responses on the right show the three levels for the same template.

:::console Storage levels
@@ full
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "refund-request",
  "variables": {"message": "I was charged twice, please refund one payment.",
                "customer_email": "jane@example.com"}
}'
# x-basal-stored: full
```
@@ answers_only
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "refund-request",
  "variables": {"message": "Refund my last invoice.", "customer_email": "sam@example.com"},
  "store": "answers_only"
}'
# Reading it back: "store": "answers_only", "input": null
# Rerunning it: 409 input_unavailable
```
@@ none
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "refund-request",
  "variables": {"message": "Refund please.", "customer_email": "kim@example.com"},
  "store": false
}'
# x-basal-stored: none
```
@@ Response 404
```json
{
  "error": {
    "type": "not_found_error",
    "code": "decision_not_found",
    "message": "No stored decision 'dec_01M3TFDV8J4GBWMM1VVR7TTHTM'. It may have been deleted, removed by retention, or made with store: none (never saved).",
    "param": null,
    "request_id": "req_…",
    "decision_id": null
  }
}
```
:::

## Opting out

Four switches set the level. The most private one always wins: a caller can store less than a template allows, never more.

| Switch | Where | Values |
|---|---|---|
| `store` in the request body | Any decision route | `true` / `"full"`, `"answers_only"`, `false` / `"none"` |
| `X-Basal-Store` header | Any decision route; for clients that cannot change the body | `1`, `answers_only`, `0` |
| The template's `storage` | Every decision made with that template | `full`, `answers_only`, `none` |
| The studio's `history.store` | Every decision | `full`, `answers_only`, `none` |

On TypeSafe's routes, `store` and `metadata` are read leniently: a value the studio does not understand is ignored and reported in the `x-basal-warning` header, never turned into an error. TypeSafe's own servers ignore these fields, so the same code runs against both. With the official Python SDK, pass `extra_body={"store": False}`.

In the app, the Playground's **Keep in history** switch sends `store: false`, and the History page's settings set the studio level. A request asking for more than its template allows gets the warning `store_downgraded`.

A decision that runs in the background must be stored, so that you can fetch it: `background` with `store: false` returns `400 background_requires_store`. Use `answers_only` if you do not want its input kept.

## Sensitive variables

Mark a template variable `"sensitive": true` and its value is used for the decision but never written to disk. History keeps a keyed hash in its place (an HMAC, keyed with a secret created for your installation), shows `[redacted:<name>]` wherever the value appeared, and leaves it out of search.

Because the hash is keyed, nobody can read the value back from it, but you can still find every decision that used a given value: send the value itself as a `sensitive_hash` filter, and the studio computes the hash for you. That is how you erase one person's decisions on request.

A decision with a sensitive value can be rerun only if you send the value again.

The protection covers every kind of variable, files included. The model reads a file sent in a sensitive `image`, `audio` or `video` variable, and History keeps no contents, no file name and only a keyed hash of it. (Versions before 0.3.0 still stored such a file; on those, use `answers_only` or turn off `store_media` for private files.)

:::console GET /v1/studio/decisions/{id}
@@ Template
```json
"variables": {
  "message": {"type": "string"},
  "customer_email": {"type": "string", "sensitive": true}
},
"state": {"from": "{{customer_email}}", "message": "{{message}}"}
```
@@ Response 200
```json
"input": {
  "variables": {
    "message": "I was charged twice, please refund one payment.",
    "customer_email": {
      "$redacted": "hmac-sha256:b10e61505dedb232f13958502826ada8625e77a64d1dbc6e513befcfb9685e44"
    }
  },
  "state": {
    "from": "[redacted:customer_email]",
    "message": "I was charged twice, please refund one payment."
  },
  "rendered_state": "from: [redacted:customer_email]\nmessage: I was charged twice, please refund one payment."
}
```
:::

## Retention

Decisions are deleted automatically once they are older than the retention period: 30 days unless you change it. A template's own `retention_days` overrides the studio's for its decisions, and `0` means keep forever. Changes apply to decisions already stored, from the next clean-up.

Some decisions are kept regardless of age:

- **pinned** decisions;
- decisions you have **labelled**, while the studio's `keep_labelled` setting is on (the default), because labels are your evaluation data.

Test examples are never removed by retention. There is also a size cap, 20 GB by default: past it, the oldest decisions that are not exempt are removed until usage drops to 90% of the cap. With `store_media` off, file contents are discarded right after each decision, and History shows "file not kept".

Change the studio settings in the app (History, then the gear button) or with `PATCH /v1/studio/settings`.

:::console PATCH /v1/studio/settings
@@ curl
```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/settings \
  -d '{"history": {"retention_days": 90}}'
```
@@ Response 200
```json
"history": {
  "store": "full",
  "retention_days": 90,
  "max_storage_gb": 20,
  "store_media": true,
  "keep_labelled": true,
  "on_store_error": "serve",
  "notice_acknowledged_at": null
}
```
:::

## Finding and exporting

History is the decisions list, `GET /v1/studio/decisions`, with filters. You can combine any of them:

| Filter | Example | Finds |
|---|---|---|
| `template` | `support-triage`, `support-triage@3` | One template, or one version of it |
| `model` | `laya` | One model's decisions |
| `act` | `false` | Decisions that needed a person |
| `needs_review` | `urgency` | Decisions where that answer did not act |
| `answer.<key>` | `answer.department=billing` | Decisions with that answer |
| `metadata.<key>` | `metadata.ticket_id=T-5120` | Your own ids |
| `labelled`, `correct` | `true` | Labelled decisions, and whether the model was right |
| `created_after`, `created_before` | `-7d`, `2026-10-01` | A time range, absolute or relative |
| `q` | `invoice` | Full-text search over the situation |
| `request_id` | `req_…` | The decision of one HTTP call |

`GET /v1/studio/decisions/export` returns every matching decision as JSON Lines (one full decision per line) or CSV (one column per answer, its certainty and its act gate, plus your metadata keys).

:::console GET /v1/studio/decisions/export
@@ curl
```bash
curl -s 'http://127.0.0.1:8420/v1/studio/decisions/export?template=refund-request&format=csv'
```
@@ Output
```text
id,created_at,template,version,model,status,act,wants_refund,wants_refund.certainty,wants_refund.act
dec_01M3TFDSP83JRHV7ENTKKQEP79,1790816216,refund-request,1,laya,completed,false,yes,0.8596,false
dec_01M3TFDTNJ8SCW38XVNWE2AM82,1790816217,refund-request,1,laya,completed,false,no,0.5128,false
```
:::

## Redacting and erasing

You can remove parts of a stored decision and keep its answers, or delete decisions outright:

- `POST /v1/studio/decisions/{id}/redact` with `fields` such as `input.variables.message`, `input.state`, `input.media` or `metadata.<key>` removes those parts of one decision. Its answers, labels and statistics stay.
- `POST /v1/studio/decisions/redact` and `POST /v1/studio/decisions/delete` do the same for every decision matching a `filter`. They skip pinned decisions unless you say otherwise, and `"dry_run": true` reports the count without changing anything.
- `DELETE /v1/studio/decisions/{id}` deletes one decision and everything stored with it.

The request on the right finds every decision made with one customer's email address, a sensitive variable, without the address ever having been stored.

:::console POST /v1/studio/decisions/delete
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/delete -d '{
  "filter": {"sensitive_hash": {"customer_email": "jane@example.com"}},
  "dry_run": true
}'
```
@@ Response 200
```json
{
  "object": "decision.bulk_delete",
  "matched": 1,
  "deleted": 0,
  "skipped_pinned": 0,
  "dry_run": true
}
```
:::

## Where it lives, and who can read it

- **On this computer only.** The studio listens on `127.0.0.1` by default. Other websites open in your browser cannot send it requests or read its history.
- **From other machines,** only with a key. When you start the studio with `--host 0.0.0.0`, set `BASAL_API_KEY`; clients send `Authorization: Bearer <key>`. Without a key, remote callers can still make decisions but cannot read History or change anything.
- **Files on disk.** The database and the installation's secret are readable only by your user account, and attached files are stored by their content in the `blobs` folder beside the database. Nothing is encrypted at rest, so protect the data folder as you would any other personal data.
- **When a write fails** (a full disk, for example), the answer is still returned, with the header `x-basal-stored: failed` and the warning `history_write_failed`. Set `on_store_error` to `fail` if an unrecorded decision is unacceptable; the studio then answers 503 instead.

The first time you open History, the app shows a notice that decisions from your code are now kept, with buttons to change the level and the retention.
