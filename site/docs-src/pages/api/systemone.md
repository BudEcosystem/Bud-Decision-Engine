---
title: TypeSafe and gateway formats
description: Use Bud Decision Studio as a drop-in local server for code written for TypeSafe's Jev API, OpenRouter's Decisions API or Vercel AI Gateway.
lead: Code written for TypeSafe's Jev API works against the studio by changing the base URL. The same goes for the OpenRouter and Vercel AI Gateway routes. Responses keep each API's exact shape, and every call still lands in history.
---

These routes are *stateless*: a request carries its whole situation and questions, as it would to TypeSafe's hosted API, and the response holds only answers. The studio adds what it can without changing a byte of the response: it records the decision in history and reports that in headers. For templates, stored settings and the act gate, use the [studio API](/docs/api/decisions), which accepts these same request bodies.

The official SDKs work unchanged: `typesafe-sdk` for Python and `@typesafe-ai/sdk` for JavaScript. The studio's tests check both against TypeSafe's published schema.

## Make a decision in TypeSafe's format

::endpoint POST /v1/systemone

Takes TypeSafe's request and returns TypeSafe's response.

| Field | Type | Description |
|---|---|---|
| `model` | string | Required. A studio model id (`laya`), a Hugging Face repository id, or one of TypeSafe's names such as `jev-latest`, which route to the most recently loaded model. |
| `state` | string, object or array | Required. The situation. |
| `questions` | object | Required. Question key to question: `choice`, `score` and `noul`, plus the studio's `multi`, `rank` and `number`. |
| `media` | array | Images, audio or video, as in the [studio API](/docs/api/resources#use-files-in-a-decision). |
| `settings` | object | `temperature` (above 0, at most 20) recalibrates the probabilities. |

The studio also reads `store`, `metadata` and `settings.act_threshold` from the body, leniently: a value it cannot use is ignored with a warning, so a request TypeSafe accepts is never refused here. TypeSafe's own servers ignore these fields, so the same code runs against both.

Answers carry exactly TypeSafe's fields: `noul` (the probability of yes) for a yes-or-no question; `choice`, `probabilities` and `confidence` for a choice; `score`, `legend`, `probabilities` and `confidence` for a scale. The request id comes back in the `x-typesafe-request-id` header, as from TypeSafe.

:::console POST /v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/systemone -d '{
  "model": "laya",
  "state": "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "wants": {"type": "choice", "instructions": "What does the customer want?",
              "criteria": {"replacement": "a new unit", "refund": "money back",
                           "repair": "a repair"}},
    "urgency": {"type": "score", "instructions": "How urgent is it?",
                "criteria": ["low", "medium", "high"]}
  }
}'
```
@@ Python
```python
# pip install typesafe-sdk
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient(api_key="local", base_url="http://127.0.0.1:8420", model="laya")
res = client.system_one(
    state="Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
    questions={
        "damaged": Noul(instructions="Was the item damaged?"),
        "wants": Choice(instructions="What does the customer want?",
                        criteria={"replacement": "a new unit", "refund": "money back",
                                  "repair": "a repair"}),
        "urgency": Score(instructions="How urgent is it?", criteria=["low", "medium", "high"]),
    },
)
print(res.request_id, res.answers["damaged"].noul, res.answers["wants"].choice)
```
@@ JavaScript
```js
// npm install @typesafe-ai/sdk
import { TypeSafeClient, choice, noul, score } from "@typesafe-ai/sdk";

const client = new TypeSafeClient({ apiKey: "local", baseURL: "http://127.0.0.1:8420", defaultModel: "laya" });
const { data, requestId } = await client.systemOne({
  state: "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
  questions: {
    damaged: noul("Was the item damaged?"),
    wants: choice("What does the customer want?", { replacement: "a new unit", refund: "money back", repair: "a repair" }),
    urgency: score("How urgent is it?", ["low", "medium", "high"]),
  },
}).withResponse();
console.log(requestId, data.answers);
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.7838},
    "wants": {
      "type": "choice",
      "choice": "replacement",
      "probabilities": {
        "replacement": 0.9062,
        "refund": 0.0342,
        "repair": 0.0596
      },
      "confidence": 0.8593
    },
    "urgency": {
      "type": "score",
      "score": 1.2857,
      "legend": {"0": "low", "1": "medium", "2": "high"},
      "probabilities": {"0": 0.1191, "1": 0.4761, "2": 0.4048},
      "confidence": 0.2141
    }
  },
  "usage": {"input_tokens": 144, "output_tokens": 0}
}
```
:::

`api_key` can be any text while the studio runs without `BASAL_API_KEY`; with one, pass that key. The official SDKs know only `choice`, `score` and `noul`; send the studio's other question types over plain HTTP.

## Get the studio's extra answer fields

Send `X-Basal-Extensions: 1` to add the studio's fields to each answer: `probabilities` for every type, the label in `decision`, `top_probability`, any `model_extras` the model reports (Laya, for example, reports its own act probability), and the response-level `latency_ms`, `wall_ms`, `passes` and `notes`. Without the header the response is exactly TypeSafe's, and the official SDKs ignore fields they do not know.

:::console POST /v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/systemone -H "X-Basal-Extensions: 1" -d '{
  "model": "laya",
  "state": "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "wants": {"type": "choice", "instructions": "What does the customer want?",
              "criteria": {"replacement": "a new unit", "refund": "money back",
                           "repair": "a repair"}},
    "urgency": {"type": "score", "instructions": "How urgent is it?",
                "criteria": ["low", "medium", "high"]}
  }
}'
```
@@ Python
```python
import httpx

r = httpx.post("http://127.0.0.1:8420/v1/systemone", headers={"X-Basal-Extensions": "1"}, timeout=120, json={
    "model": "laya",
    "state": "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
    "questions": {
        "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
        "wants": {"type": "choice", "instructions": "What does the customer want?",
                  "criteria": {"replacement": "a new unit", "refund": "money back",
                               "repair": "a repair"}},
        "urgency": {"type": "score", "instructions": "How urgent is it?",
                    "criteria": ["low", "medium", "high"]},
    },
}).json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/systemone", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Basal-Extensions": "1" },
  body: JSON.stringify({
    model: "laya",
    state: "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
    questions: {
      damaged: { type: "noul", instructions: "Was the item damaged?" },
      wants: { type: "choice", instructions: "What does the customer want?",
               criteria: { replacement: "a new unit", refund: "money back", repair: "a repair" } },
      urgency: { type: "score", instructions: "How urgent is it?", criteria: ["low", "medium", "high"] },
    },
  }),
});
const r = await res.json();
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {
      "type": "noul",
      "noul": 0.7838,
      "probabilities": {"false": 0.2162, "true": 0.7838},
      "decision": "yes",
      "top_probability": 0.7838,
      "model_extras": {
        "act_probability": 1.0,
        "act_probability_help": "Laya's built-in gate: how safe it thinks acting on this answer automatically is."
      }
    },
    "wants": {
      "type": "choice",
      "choice": "replacement",
      "probabilities": {
        "replacement": 0.9062,
        "refund": 0.0342,
        "repair": 0.0596
      },
      "confidence": 0.8593,
      "decision": "replacement",
      "top_probability": 0.9062
    },
    "urgency": {
      "type": "score",
      "score": 1.2857,
      "legend": {"0": "low", "1": "medium", "2": "high"},
      "probabilities": {"0": 0.1191, "1": 0.4761, "2": 0.4048},
      "confidence": 0.2141,
      "decision": "1",
      "top_probability": 0.4761
    }
  },
  "usage": {"input_tokens": 144, "output_tokens": 0},
  "latency_ms": 221.7,
  "wall_ms": 241.2,
  "passes": 1,
  "notes": []
}
```
:::

The response is shortened: the `wants` and `urgency` answers carry the same `model_extras` as `damaged`.

## File a call under a template

Wire calls are stored in history like any other decision, without a template. If a call sends exactly the questions of a template version, the header `X-Basal-Template: support-triage@2` files it under that version, so it appears in the template's history and statistics. The studio checks the claim: when the questions differ from the version's, the call is still answered and stored, but without the template, and the response says so.

| Header | Meaning |
|---|---|
| `X-Basal-Template` | A template reference whose questions this call sends unchanged. |
| `X-Basal-Metadata` | `key=value;key2=value2`: labels the stored decision, as the studio API's `metadata` does. |
| `X-Basal-Store` | `full`, `answers_only` or `none` (or `1` and `0`). |

The response headers report the result: `x-basal-template-status` is `attributed` or `mismatch`, `x-basal-warning` lists warning codes such as `template_mismatch`, and `x-basal-stored` and `x-basal-decision-id` name the stored decision.

:::console POST /v1/systemone
@@ curl
```bash
curl -s -i http://127.0.0.1:8420/v1/systemone \
  -H "X-Basal-Template: support-triage@2" \
  -H "X-Basal-Metadata: ticket_id=T-4520;channel=email" \
  -d @request.json
```
@@ Python
```python
import httpx

studio = "http://127.0.0.1:8420"
t = httpx.get(f"{studio}/v1/studio/templates/support-triage", params={"version": 2}).json()
r = httpx.post(f"{studio}/v1/systemone", timeout=120,
               headers={"X-Basal-Template": "support-triage@2",
                        "X-Basal-Metadata": "ticket_id=T-4520;channel=email"},
               json={"model": "laya",
                     "state": {"tier": "pro", "message": "I was charged twice this month."},
                     "questions": t["questions"]})
print(r.headers["x-basal-template-status"], r.headers["x-basal-decision-id"])
```
@@ JavaScript
```js
const studio = "http://127.0.0.1:8420";
const t = await (await fetch(`${studio}/v1/studio/templates/support-triage?version=2`)).json();
const res = await fetch(`${studio}/v1/systemone`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Basal-Template": "support-triage@2",
    "X-Basal-Metadata": "ticket_id=T-4520;channel=email",
  },
  body: JSON.stringify({
    model: "laya",
    state: { tier: "pro", message: "I was charged twice this month." },
    questions: t.questions,
  }),
});
console.log(res.headers.get("x-basal-template-status"), res.headers.get("x-basal-decision-id"));
```
@@ Output
```http
HTTP/1.1 200 OK
x-basal-stored: full
x-basal-decision-id: dec_01M3TEFZQ24A2SH4N4T458PS8J
x-basal-template-status: attributed
content-type: application/json
x-typesafe-request-id: req_c47314a052b941ebb922ddd9d62cafdc
```
:::

In the curl tab, `request.json` holds the same body the other tabs build: the state and the version's four questions.

## List models in TypeSafe's format

::endpoint GET /v1/models

Returns models in TypeSafe's shape: downloaded and loaded models, led by `jev-latest`, an alias for the most recently loaded one. The studio API's [List models](/docs/api/resources#list-models) gives more detail.

:::console GET /v1/models
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/models
```
@@ Python
```python
import httpx

for m in httpx.get("http://127.0.0.1:8420/v1/models").json()["models"]:
    print(m["name"], m["description"])
```
@@ JavaScript
```js
const { models } = await (await fetch("http://127.0.0.1:8420/v1/models")).json();
for (const m of models) console.log(m.name, m.description);
```
@@ Response 200
```json
{
  "models": [
    {
      "name": "jev-latest",
      "description": "Alias for the most recently loaded model (Laya).",
      "release_date": "2026-09-24"
    },
    {
      "name": "julia-1",
      "description": "Julia 1 by Supersonic Labs. The tiny, multilingual router.",
      "release_date": "2026-09-26"
    },
    {
      "name": "laya",
      "description": "Laya by ConvAI Innovations. The most popular open decision model.",
      "release_date": "2026-09-24"
    }
  ]
}
```
:::

The response is shortened to three of its entries.

## Gateway formats

The same decision is also served in the shapes of two gateways that resell TypeSafe's models, so code written for them can point at the studio. Requests take the same body as `/v1/systemone`.

| Route | Format | Differences from TypeSafe |
|---|---|---|
| `POST /api/v1/systemone`, `POST /api/alpha/decisions` | OpenRouter's System One and Decisions API | Adds `id`, `provider` and `usage.cost`. Errors are `{"error": {"code", "message"}}`, and invalid requests answer `400`. |
| `POST /typesafe/v1/systemone`, `GET /typesafe/v1/models` | Vercel AI Gateway's TypeSafe route | Adds `provider_metadata`. Errors are `{"message", "error_type"}`. |
| `POST /v1/evaluate` | Vercel AI Gateway's evaluation API | Yes-or-no questions are `boolean`, answered with `probability`. Usage is `inputTokens` and `outputTokens`. |

### OpenRouter

:::console POST /api/v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/v1/systemone -d '{
  "model": "laya",
  "state": "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
  "questions": {"damaged": {"type": "noul", "instructions": "Was the item damaged?"}}
}'
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.7851}
  },
  "usage": {
    "input_tokens": 50,
    "output_tokens": 0,
    "cost": 0.0
  },
  "id": "gen-dec-a332dafb5d104699b18babb4",
  "provider": "Bud Decision Studio"
}
```
:::

### Vercel AI Gateway

:::console POST /typesafe/v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/typesafe/v1/systemone -d '{
  "model": "laya",
  "state": "Order #8812 arrived with a cracked screen. I want a replacement, not a refund.",
  "questions": {"damaged": {"type": "noul", "instructions": "Was the item damaged?"}}
}'
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.7851}
  },
  "usage": {"input_tokens": 50, "output_tokens": 0},
  "provider_metadata": {
    "gateway": {
      "routing": {
        "originalModelId": "laya",
        "resolvedProvider": "Bud Decision Studio",
        "canonicalSlug": "laya",
        "finalProvider": "Bud Decision Studio"
      },
      "cost": "0",
      "generationId": "gen_0b86c5eb8b8444bd9d554d20"
    }
  }
}
```
:::

### Vercel evaluation API

:::console POST /v1/evaluate
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/evaluate -d '{
  "model": "laya",
  "state": "Order #8812 arrived with a cracked screen.",
  "questions": {"damaged": {"type": "boolean", "instructions": "Was the item damaged?"}}
}'
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "boolean", "probability": 0.8314}
  },
  "usage": {"inputTokens": 41, "outputTokens": 0},
  "providerMetadata": {"basal": {"latencyMs": 108.5}}
}
```
:::

## Errors on these routes

Each route keeps its API's own error shape, including for the studio's added failures (a refused cross-site request, an idempotency conflict, a failed save). The [studio API's error envelope](/docs/api/errors) applies only under `/v1/studio`.

| Situation | TypeSafe routes | OpenRouter | Vercel |
|---|---|---|---|
| Invalid request | `422 {"detail": [...]}`, one entry per problem with its location | `400 {"error": {"code", "message"}}` | `400 {"message", "error_type": "invalid_request"}` |
| Unknown model | `404 {"detail": "Unknown model ..."}` | `404`, same envelope | `404`, same envelope |
| No model loaded, or not downloaded | `409` | `409` | `409`, `error_type: "conflict"` |
| Missing or wrong API key | `403` missing, `401` wrong, checked before the body | as TypeSafe | as TypeSafe |

:::console POST /v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/systemone -d '{
  "model": "laya",
  "state": "hi",
  "questions": {"q": {"type": "choice", "instructions": "Pick one"}}
}'
```
@@ Response 422
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "questions", "q", "choice", "criteria"],
      "msg": "Field required",
      "input": {"type": "choice", "instructions": "Pick one"}
    }
  ]
}
```
:::

## How these calls are recorded

Each call is stored in history with `source.format` set to `typesafe`, `openrouter`, `vercel` or `evaluate`, `source.surface` set to `api`, and the client read from the user agent (the official SDKs are recognised by name). Response bodies are identical whether or not the call was stored. Filter them with `format=typesafe` in [List decisions](/docs/api/history#list-decisions), and fetch any of them back in its original shape with [`?format=`](/docs/api/decisions#in-another-apis-format).

The act gate is applied in history too: each answer is marked as acting or not using `settings.act_threshold` from the request, or the studio's default. The wire response itself never changes, so history is where you see which calls needed a person.
