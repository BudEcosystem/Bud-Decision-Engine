---
title: API reference
description: The Bud Decision Studio HTTP API. Base URL, authentication, headers, pagination, retries and every endpoint of the studio API, the TypeSafe-compatible API and the gateway formats.
lead: Everything the app does is available over HTTP on your own computer. This page covers what every endpoint shares, from the base URL and authentication to headers, pages of results and safe retries, and ends with a list of every endpoint.
---

The studio runs a small web server while the app is open (or while `python -m basal.server` runs from source). Its API takes JSON and returns JSON. The examples on these pages were captured from a running studio with the Laya model loaded; only ids and timings differ from what you will see.

Code examples use three forms. **curl** runs in any terminal. **Python** uses [httpx](https://www.python-httpx.org/) (`pip install httpx`). **JavaScript** uses the `fetch` built into Node.js 18 and later, written as an ES module so `await` works at the top level.

## Base URL

The studio listens on `http://127.0.0.1:8420`. The desktop app prefers port 8420; when another program holds it, the app takes the first free port from 8421 to 8440 and keeps using it. The **API** page in the app always shows the address in use.

A quick way to check the studio is up is to ask for a model:

| Field | Type | Description |
|---|---|---|
| `id` | string | The model id you send in `model`, such as `laya`. |
| `status` | string | `loaded`, `loading`, `downloaded` or `not_downloaded`. |
| `context_tokens` | integer | How much text the model reads; longer input is cut off at the end. |

:::console GET /v1/studio/models/laya
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/models/laya
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
model = studio.get("/models/laya").raise_for_status().json()
print(model["status"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/models/laya");
const model = await res.json();
console.log(model.status);
```
@@ Response 200
```json
{
  "id": "laya",
  "object": "model",
  "name": "Laya",
  "maker": "ConvAI Innovations",
  "modalities": ["text"],
  "types": ["choice", "score", "noul"],
  "max_options": 20,
  "max_questions": 64,
  "context_tokens": 512,
  "status": "loaded",
  "revision": null
}
```
:::

## Three families of endpoints

The studio serves three groups of endpoints. They share one engine, so a decision made through any of them uses the same models and lands in the same history.

| Family | Paths | Use it for |
|---|---|---|
| The studio API | `/v1/studio/...` | New code. Templates, decisions with history, feedback, test examples, files and settings. Errors come in one [envelope](/docs/api/errors). |
| TypeSafe and gateway formats | `/v1/systemone`, `/v1/models`, `/api/v1/systemone`, `/api/alpha/decisions`, `/typesafe/v1/systemone`, `/v1/evaluate` | Code already written for TypeSafe's Jev API, OpenRouter or Vercel AI Gateway. Responses keep each API's exact shape. See [TypeSafe and gateway formats](/docs/api/systemone). |
| The app's own routes | `/api/state`, `/api/models/{id}/load`, `/api/compare` and others | The app's pages. They are internal and may change in any release; build on `/v1/studio` instead. |

:::note The app's own routes
Requests that change something under `/api/` (loading, downloading or ejecting a model, for example) must carry the header `X-Basal-Client: 1`. A plain web form cannot send that header, which is what protects the studio from other websites.
:::

## Authentication and remote access

By default the studio listens on this computer only and needs no key. Requests from other programs on the same computer, such as your scripts, are accepted as they are.

To serve other machines, start the studio with `--host 0.0.0.0` (or `BASAL_HOST=0.0.0.0`) and set `BASAL_API_KEY`. Remote callers then send `Authorization: Bearer <key>`. Callers on the studio's own computer still need no key unless `BASAL_AUTH_LOCAL=1` is also set.

| Situation | What happens |
|---|---|
| Remote call to `/v1/studio` without the key | `401 missing_api_key` |
| Remote call to `/v1/studio` with a wrong key | `401 invalid_api_key` |
| Remote call to `/v1/systemone` without the key | `403`, exactly as TypeSafe's own servers answer |
| Studio on the network with **no** `BASAL_API_KEY` | Remote callers may make decisions (`POST /v1/studio/decisions`, its `preview`, `GET /v1/studio/models` and the wire formats). Every other `/v1/studio` route answers `403 remote_access_requires_key`, so history, templates and settings stay private. |

:::console Terminal
@@ Shell
```bash
# On the machine that runs the studio (from a source checkout)
BASAL_API_KEY="$(openssl rand -hex 24)" python -m basal.server --host 0.0.0.0

# From another machine
curl -s http://studio-host:8420/v1/studio/decisions \
  -H "Authorization: Bearer $BASAL_API_KEY" \
  -d '{"template": "support-triage", "variables": {"customer_message": "Refund please"}}'
```
:::

:::warning
The key travels in plain text over `http`. Outside a trusted network, put the studio behind a proxy that adds HTTPS.
:::

## Calling from a web page

Every decision is recorded, so the studio refuses writes that come from other websites open in your browser. A request that carries an `Origin` header must come from the studio's own address or from an origin listed in `BASAL_CORS_ORIGINS` (comma-separated). Listing an origin also turns on CORS for it.

Browser writes to `/v1/studio` must also send `Content-Type: application/json`. Requests without browser headers, from curl, the SDKs or a server, are not affected, and their bodies are read as JSON whatever the content type says.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions \
  -H "Origin: https://example.com" \
  -H "Content-Type: application/json" \
  -d '{"state": "hi", "questions": {"q": {"type": "noul"}}, "store": false}'
```
@@ Response 403
```json
{
  "error": {
    "type": "permission_error",
    "code": "cross_site_request",
    "message": "Requests from https://example.com may not change this studio. Other websites can't write to it; to allow one, list it in BASAL_CORS_ORIGINS.",
    "param": null,
    "details": [
      {
        "code": "cross_site_request",
        "param": null,
        "message": "Requests from https://example.com may not change this studio. Other websites can't write to it; to allow one, list it in BASAL_CORS_ORIGINS."
      }
    ],
    "request_id": "req_7427898b07574830bbf24486fd78fe90",
    "decision_id": null
  }
}
```
:::

## Headers

Headers the studio reads:

| Header | Where | Meaning |
|---|---|---|
| `Authorization` | All | `Bearer <BASAL_API_KEY>`, when the studio requires a key. |
| `Idempotency-Key` | Any `POST` that makes a decision | Makes a retry safe: the same key and body return the first response instead of deciding again. See [Retries and idempotency](#retries-and-idempotency). |
| `X-Basal-Store` | Decisions | `full`, `answers_only` or `none` (also `1` and `0`). Lowers what history keeps for this call. |
| `X-Basal-Metadata` | Decisions | `key=value;key2=value2`, values URL-encoded. Labels the decision in history, like the body's `metadata`. |
| `X-Basal-Template` | Wire formats | Names the template version a `/v1/systemone` call came from, so history files it under that template. |
| `X-Basal-Extensions` | Wire formats | `1` returns the studio's extra answer fields. |
| `X-TypeSafe-Retry-Count` | All | Sent by the official SDKs on a retry; the studio records it and folds retries together in history. |
| `X-TypeSafe-Request-Id` | All | Sets the request id instead of letting the studio make one. |
| `If-Match` | Template saves | The version number your edit is based on, such as `"3"`. A newer version answers `412 version_conflict`. |

Headers the studio returns:

| Header | Meaning |
|---|---|
| `x-request-id`, `x-typesafe-request-id` | The request id (`req_` and 32 hex digits). The same id is in error bodies and in the decision's `source.request_id`. Studio API responses carry both headers; the wire formats carry only `x-typesafe-request-id`, as TypeSafe does. |
| `x-basal-stored` | What history kept for this decision: `full`, `answers_only`, `none` or `failed`. |
| `x-basal-decision-id` | The stored decision's id. Absent when nothing was stored. |
| `location` | `/v1/studio/decisions/{id}` for a stored decision, or `/v1/studio/templates/{id}` for a new template. |
| `x-basal-idempotent-replayed` | `true` when this is the saved response of an earlier request with the same `Idempotency-Key`. |
| `etag` | A template's latest version number, such as `"2"`. Send it back in `If-Match`. |
| `x-basal-version-created` | `true` or `false`, on `POST /templates/{id}/versions`. |
| `x-basal-warning`, `x-basal-template-status` | On the wire formats only: warning codes, and whether `X-Basal-Template` was `attributed` or a `mismatch`. |
| `retry-after` | `1`, on `409 idempotency_in_progress`. |
| `server-timing` | How long the studio spent on the request, in milliseconds. |

## Objects, ids and times

Objects carry an `object` field naming their kind: `decision`, `decision.summary`, `template`, `template.version`, `feedback`, `file`, `list` and so on. Ids start with a prefix that names the kind: `dec_` for decisions, `fb_` for feedback, `ex_` for test examples and `file_` for files. The rest of an id is a [ULID](https://github.com/ulid/spec), so ids sort in the order they were made. Templates use the id you choose, such as `support-triage`; starter templates are `builtin/<name>`.

Times are Unix seconds (`created_at`, `completed_at`, `expires_at`). Filters also accept RFC 3339 times and relative times such as `-24h` or `-7d`.

## Pages of results

Lists return at most `limit` items (1 to 100, default 20), newest first. To read the next page, pass the previous page's `last_id` as `after`. `has_more` says whether another page exists. Pass `order=asc` to read oldest first.

| Field | Type | Description |
|---|---|---|
| `data` | array | The items on this page. |
| `first_id` | string | The id of the first item, or `null` for an empty page. |
| `last_id` | string | The id of the last item. Send it as `after` for the next page. |
| `has_more` | boolean | `true` while more items match. |

:::console GET /v1/studio/decisions
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/decisions?template=support-triage&limit=2&after=dec_01M3TEFBTDS0Q225K1AW4D6PC7"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
params = {"template": "support-triage", "limit": 100}
while True:
    page = studio.get("/decisions", params=params).raise_for_status().json()
    for d in page["data"]:
        print(d["id"], d["act"])
    if not page["has_more"]:
        break
    params["after"] = page["last_id"]
```
@@ JavaScript
```js
let after = null;
do {
  const q = new URLSearchParams({ template: "support-triage", limit: "100", ...(after && { after }) });
  const page = await (await fetch(`http://127.0.0.1:8420/v1/studio/decisions?${q}`)).json();
  for (const d of page.data) console.log(d.id, d.act);
  after = page.has_more ? page.last_id : null;
} while (after);
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {"id": "dec_01M3TEFBCTEDKGKGFDQN11SAJJ", "object": "decision.summary", "created_at": 1790815219},
    {"id": "dec_01M3TEC5FDPQZ46C56QXXQJ9SN", "object": "decision.summary", "created_at": 1790815114}
  ],
  "first_id": "dec_01M3TEFBCTEDKGKGFDQN11SAJJ",
  "last_id": "dec_01M3TEC5FDPQZ46C56QXXQJ9SN",
  "has_more": true
}
```
:::

The response above is shortened: each summary also carries its template, model, answers and metadata, as described in [List decisions](/docs/api/history#list-decisions).

## Slow first calls and background decisions

A decision normally takes a fraction of a second. The first decision for a model that is downloaded but not loaded takes longer: the studio loads the model first, which can take from a few seconds to a few minutes for the largest models. Set your client's timeout accordingly (the examples use 120 seconds), or load the model in the app beforehand.

For clients with short timeouts, send `"background": true`. The studio answers at once with `status: "queued"` and a `location` header, and you collect the result with `GET /v1/studio/decisions/{id}?wait=60`, which holds the request open for up to 60 seconds until the decision finishes. See [Run a decision in the background](/docs/api/decisions#run-a-decision-in-the-background).

:::note
Streaming (`"stream": true`) is not available yet. A request that asks for it answers `400` and suggests background decisions instead.
:::

## Retries and idempotency

Send an `Idempotency-Key` header, any string of up to 255 printable characters, with each logical call. If the network fails and you retry with the same key and the same body, the studio returns the saved response with `x-basal-idempotent-replayed: true` instead of running the decision twice. Keys are kept for 24 hours.

| Case | Result |
|---|---|
| Same key and body, first request succeeded | The first response is replayed, with its status, body and headers. |
| Same key while the first request is still running | `409 idempotency_in_progress` with `retry-after: 1`. |
| Same key, different body | `409 idempotency_key_reused`. Nothing runs. |
| First request failed | Nothing is saved; a retry runs again. |
| With `store: "none"` | The key is honoured from memory for 10 minutes and never written to disk. |

The official TypeSafe SDKs retry some failures without a key and send `X-TypeSafe-Retry-Count`. The studio links such a retry to the attempt it replaces (`source.retry_of`) and hides the replaced attempt from lists and statistics, so each logical call counts once. Pass `fold_retries=false` to see every attempt.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s -i http://127.0.0.1:8420/v1/studio/decisions \
  -H "Idempotency-Key: ticket-T-4502" \
  -d '{"template": "support-triage",
       "variables": {"customer_message": "Where can I download my invoices?", "account_tier": "pro"}}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
body = {"template": "support-triage",
        "variables": {"customer_message": "Where can I download my invoices?", "account_tier": "pro"}}
r = studio.post("/decisions", json=body, headers={"Idempotency-Key": "ticket-T-4502"})
print(r.headers.get("x-basal-idempotent-replayed"), r.json()["id"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Idempotency-Key": "ticket-T-4502" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_message: "Where can I download my invoices?", account_tier: "pro" },
  }),
});
console.log(res.headers.get("x-basal-idempotent-replayed"), (await res.json()).id);
```
@@ Output
```http
HTTP/1.1 200 OK
x-basal-stored: full
x-basal-decision-id: dec_01M3TEFBCTEDKGKGFDQN11SAJJ
location: /v1/studio/decisions/dec_01M3TEFBCTEDKGKGFDQN11SAJJ
x-basal-idempotent-replayed: true
content-type: application/json
x-typesafe-request-id: req_83eb5a3f0c4f4db48cd5c4ae6964f128
x-request-id: req_83eb5a3f0c4f4db48cd5c4ae6964f128

{"id":"dec_01M3TEFBCTEDKGKGFDQN11SAJJ","object":"decision","status":"completed", ...}
```
:::

The output above is the second call with the same key. The body is cut short here; it is the complete decision object returned by the first call.

## Errors

Every error from `/v1/studio` comes in one envelope: an `error` object with the fields `type`, `code`, `message`, `param`, `details`, `request_id` and `decision_id`. `code` is a stable name to branch on, `param` points at the field that caused it, and `details` lists every problem found in the same pass, so one round trip shows all of them. The wire formats keep their own error shapes. See [Errors](/docs/api/errors) for every code.

## Interactive reference

The studio also serves an interactive reference generated from the code, at `http://127.0.0.1:8420/docs`, with the raw OpenAPI document at `/openapi.json`. Every studio API operation there has ready-to-run examples. Requests sent from that page are real: they run models and are stored in history like any other.

## Every endpoint

Decisions and history:

| Method | Path | What it does |
|---|---|---|
| `POST` | `/v1/studio/decisions` | [Create a decision](/docs/api/decisions#create-a-decision) |
| `POST` | `/v1/studio/decisions/preview` | [Preview a decision](/docs/api/decisions#preview-a-decision) without running a model |
| `GET` | `/v1/studio/decisions/{id}` | [Retrieve a decision](/docs/api/decisions#retrieve-a-decision) |
| `GET` | `/v1/studio/decisions/{id}/input` | [Retrieve a decision's input](/docs/api/decisions#retrieve-a-decisions-input) |
| `POST` | `/v1/studio/decisions/{id}/rerun` | [Rerun a decision](/docs/api/decisions#rerun-a-decision) |
| `POST` | `/v1/studio/decisions/{id}/cancel` | [Cancel a background decision](/docs/api/decisions#cancel-a-background-decision) |
| `PATCH` | `/v1/studio/decisions/{id}` | [Update a decision](/docs/api/decisions#update-a-decision) (pin it, change metadata) |
| `POST` | `/v1/studio/decisions/{id}/redact` | [Redact a decision](/docs/api/decisions#redact-a-decision) |
| `DELETE` | `/v1/studio/decisions/{id}` | [Delete a decision](/docs/api/decisions#delete-a-decision) |
| `GET` | `/v1/studio/decisions` | [List decisions](/docs/api/history#list-decisions) |
| `GET` | `/v1/studio/decisions/stats` | [Decision statistics](/docs/api/history#decision-statistics) |
| `GET` | `/v1/studio/decisions/export` | [Export decisions](/docs/api/history#export-decisions) as JSONL or CSV |
| `POST` | `/v1/studio/decisions/delete` | [Delete many decisions](/docs/api/history#delete-or-redact-many-decisions) |
| `POST` | `/v1/studio/decisions/redact` | [Redact many decisions](/docs/api/history#delete-or-redact-many-decisions) |
| `POST` | `/v1/studio/decisions/{id}/feedback` | [Label a decision](/docs/api/history#label-a-decision) |
| `GET` | `/v1/studio/decisions/{id}/feedback` | [List a decision's feedback](/docs/api/history#list-and-delete-feedback) |
| `DELETE` | `/v1/studio/feedback/{id}` | [Delete feedback](/docs/api/history#list-and-delete-feedback) |

Templates:

| Method | Path | What it does |
|---|---|---|
| `POST` | `/v1/studio/templates` | [Create a template](/docs/api/templates#create-a-template) |
| `GET` | `/v1/studio/templates` | [List templates](/docs/api/templates#list-templates) |
| `GET` | `/v1/studio/templates/{id}` | [Retrieve a template](/docs/api/templates#retrieve-a-template) |
| `PUT` | `/v1/studio/templates/{id}` | [Create or replace a template](/docs/api/templates#create-or-replace-a-template) |
| `PATCH` | `/v1/studio/templates/{id}` | [Update a template](/docs/api/templates#update-a-template) |
| `DELETE` | `/v1/studio/templates/{id}` | [Delete a template](/docs/api/templates#delete-a-template) |
| `POST` | `/v1/studio/templates/{id}/archive`, `/unarchive` | [Archive a template](/docs/api/templates#archive-a-template) |
| `GET` | `/v1/studio/templates/{id}/versions` | [List versions](/docs/api/templates#list-versions) |
| `POST` | `/v1/studio/templates/{id}/versions` | [Save a version](/docs/api/templates#save-a-version) |
| `GET` | `/v1/studio/templates/{id}/versions/{n}` | [Retrieve a version](/docs/api/templates#retrieve-a-version) |
| `GET` | `/v1/studio/templates/{id}/versions/{n}/diff` | [See what changed](/docs/api/templates#see-what-changed-between-versions) |
| `POST` | `/v1/studio/templates/{id}/versions/{n}/restore` | [Restore a version](/docs/api/templates#restore-a-version) |
| `PUT`, `DELETE` | `/v1/studio/templates/{id}/aliases/{alias}` | [Point an alias at a version](/docs/api/templates#point-an-alias-at-a-version) |
| `GET` | `/v1/studio/templates/{id}/schema` | [Variables as JSON Schema](/docs/api/templates#variables-as-json-schema) |
| `GET` | `/v1/studio/templates/{id}/compatibility` | [Check which models can run it](/docs/api/templates#check-which-models-can-run-a-template) |
| `GET` | `/v1/studio/templates/{id}/decisions` | [A template's history](/docs/api/templates#a-templates-history-and-statistics) |
| `GET` | `/v1/studio/templates/{id}/stats` | [A template's statistics](/docs/api/templates#a-templates-history-and-statistics) |
| `GET` | `/v1/studio/templates/{id}/compare` | [Compare two versions over history](/docs/api/templates#compare-two-versions-over-history) |

Test examples, files, models and settings:

| Method | Path | What it does |
|---|---|---|
| `POST` | `/v1/studio/templates/{id}/examples` | [Add a test example](/docs/api/examples#add-a-test-example) |
| `GET` | `/v1/studio/templates/{id}/examples` | [List test examples](/docs/api/examples#list-test-examples) |
| `GET` | `/v1/studio/templates/{id}/examples/{eid}` | [Retrieve a test example](/docs/api/examples#retrieve-a-test-example) |
| `PATCH` | `/v1/studio/templates/{id}/examples/{eid}` | [Update a test example](/docs/api/examples#update-a-test-example) |
| `DELETE` | `/v1/studio/templates/{id}/examples/{eid}` | [Retire a test example](/docs/api/examples#retire-a-test-example) |
| `POST` | `/v1/studio/templates/{id}/examples/import` | [Import test examples](/docs/api/examples#import-test-examples) |
| `GET` | `/v1/studio/templates/{id}/examples/export` | [Export test examples](/docs/api/examples#export-test-examples) |
| `POST` | `/v1/studio/files` | [Upload a file](/docs/api/resources#upload-a-file) |
| `GET` | `/v1/studio/files/{id}`, `/content` | [Retrieve a file](/docs/api/resources#retrieve-a-file) |
| `DELETE` | `/v1/studio/files/{id}` | [Delete a file](/docs/api/resources#delete-a-file) |
| `GET` | `/v1/studio/models`, `/models/{id}` | [List models](/docs/api/resources#list-models) |
| `GET`, `PATCH` | `/v1/studio/settings` | [Read and change settings](/docs/api/resources#read-the-settings) |

TypeSafe and gateway formats:

| Method | Path | Format |
|---|---|---|
| `POST` | `/v1/systemone` | [TypeSafe Jev API](/docs/api/systemone#make-a-decision-in-typesafes-format) |
| `GET` | `/v1/models` | [TypeSafe's model list](/docs/api/systemone#list-models-in-typesafes-format) |
| `POST` | `/api/v1/systemone`, `/api/alpha/decisions` | [OpenRouter](/docs/api/systemone#gateway-formats) |
| `POST` | `/typesafe/v1/systemone` | [Vercel AI Gateway](/docs/api/systemone#gateway-formats) |
| `GET` | `/typesafe/v1/models` | [Vercel AI Gateway's model list](/docs/api/systemone#gateway-formats) |
| `POST` | `/v1/evaluate` | [Vercel AI Gateway evaluation](/docs/api/systemone#gateway-formats) |
