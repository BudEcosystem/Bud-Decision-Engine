---
title: Move from Jev or a gateway
description: Point code written for TypeSafe's Jev API, OpenRouter's Decisions API or Vercel AI Gateway at Bud Decision Studio by changing the base URL, then adopt templates and History step by step.
lead: The studio speaks TypeSafe's Jev API and the gateway formats built on it, so existing code works by changing one base URL. Responses keep their exact shape. When you are ready, the same requests can move to the studio's own API, one optional step at a time.
---

## Change the base URL

Point your client at the studio and keep everything else. With the official SDKs, set `base_url` (Python) or `baseURL` (JavaScript) to the studio's address, and use any API key: the studio does not check keys from this computer.

The model name can be any studio model id, such as `laya`, or TypeSafe's own names: `jev-latest` and the other Jev names mean the most recently loaded model.

Both SDK examples on the right were run against the studio with the official packages, `typesafe-sdk` for Python and `@typesafe-ai/sdk` for JavaScript, and printed the output shown.

:::console POST /v1/systemone
@@ Python
```python
# pip install typesafe-sdk
from typesafe_sdk import TypeSafeClient, Choice, Noul

client = TypeSafeClient(api_key="local", base_url="http://127.0.0.1:8420", model="laya")

res = client.system_one(
    state="The package arrived crushed and the screen is cracked.",
    questions={
        "damaged": Noul(instructions="Was the item damaged?"),
        "team": Choice(instructions="Who should handle this?",
                       criteria={"returns": "refunds and replacements",
                                 "shipping": "carriers and delivery", "sales": None}),
    },
)
print(res.answers["damaged"].noul, res.answers["team"].choice)
```
@@ JavaScript
```js
// npm install @typesafe-ai/sdk
import { TypeSafeClient, choice, noul } from "@typesafe-ai/sdk";

const client = new TypeSafeClient({
  apiKey: "local",
  baseURL: "http://127.0.0.1:8420",   // the only change from TypeSafe's hosted API
  defaultModel: "laya",
});

const { data, requestId } = await client.systemOne({
  state: "The package arrived crushed and the screen is cracked.",
  questions: {
    damaged: noul("Was the item damaged?"),
    team: choice("Who should handle this?", { returns: "refunds and replacements", shipping: "carriers and delivery", sales: null }),
  },
}).withResponse();

console.log(requestId, data.answers.damaged.noul, data.answers.team.choice);
```
@@ Output
```text
Python:      0.8961 shipping
JavaScript:  req_2bdfc3ec1a924b5d9643250857dd6d07 0.8961 shipping
```
:::

## What stays the same

On `/v1/systemone` the response body is exactly TypeSafe's: the same envelope (`model`, `answers`, `usage`), the same fields on each answer, the same request id header and the same validation errors (`422` with a `detail` list). The studio's test suite checks this against TypeSafe's published schema and both official SDKs.

| | TypeSafe's hosted API | The studio |
|---|---|---|
| Request body | `model`, `state`, `questions` | The same; also `media`, `settings.temperature` and the `multi`, `rank` and `number` types |
| `noul` answer | `type`, `noul` | The same |
| `choice` answer | `type`, `choice`, `probabilities`, `confidence` | The same |
| `score` answer | `type`, `score`, `legend`, `probabilities`, `confidence` | The same |
| Request id | `x-typesafe-request-id` header | The same |
| Models | `GET /v1/models` | Every studio model, plus `jev-latest` |
| Keys | Required | Not needed on this computer |

The studio only adds response headers, which the SDKs ignore: `x-basal-decision-id` names the stored decision, and `x-basal-stored` says how much of it was kept.

:::console POST /v1/systemone
@@ curl
```bash
curl -s -i http://127.0.0.1:8420/v1/systemone -d '{
  "model": "laya",
  "state": "The package arrived crushed and the screen is cracked.",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "team": {"type": "choice", "instructions": "Who should handle this?",
             "criteria": {"returns": "refunds and replacements",
                          "shipping": "carriers and delivery", "sales": null}}
  }
}'
```
@@ Response 200
```text
HTTP/1.1 200 OK
x-basal-stored: full
x-basal-decision-id: dec_01M3TFA1NJ94HPXBB3QEJY6CED
content-type: application/json
x-typesafe-request-id: req_71c4f1cccb6f40a5b63b8e1f86893f22

{"model":"laya",
 "answers":{"damaged":{"type":"noul","noul":0.8961},
            "team":{"type":"choice","choice":"shipping",
                    "probabilities":{"returns":0.3393,"shipping":0.553,"sales":0.1077},
                    "confidence":0.3294}},
 "usage":{"input_tokens":78,"output_tokens":0}}
```
:::

## More detail when you want it

Send the header `X-Basal-Extensions: 1` and each answer also carries the studio's fields: `decision`, `top_probability` and the full `probabilities`, plus `latency_ms`, `passes` and `notes` on the response. Some models add signals of their own under `model_extras`; Laya, for instance, reports its built-in act gate. The official SDKs keep the fields they know and ignore the rest.

The studio's own API returns all of this without a header, plus `certainty` and `act`; see [Move to the studio API](#move-to-the-studio-api-step-by-step) below.

:::console POST /v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/systemone -H 'X-Basal-Extensions: 1' -d @body.json
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.8961,
                "probabilities": {"false": 0.1039, "true": 0.8961},
                "decision": "yes", "top_probability": 0.8961,
                "model_extras": {"act_probability": 1.0,
                                 "act_probability_help": "Laya's built-in gate: how safe it thinks acting on this answer automatically is."}},
    "team": {"type": "choice", "choice": "shipping",
             "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
             "confidence": 0.3294, "decision": "shipping", "top_probability": 0.553,
             "model_extras": {"act_probability": 1.0,
                              "act_probability_help": "Laya's built-in gate: how safe it thinks acting on this answer automatically is."}}
  },
  "usage": {"input_tokens": 78, "output_tokens": 0},
  "latency_ms": 394.7,
  "wall_ms": 401.4,
  "passes": 1,
  "notes": []
}
```
:::

## Your calls are in History

Every call to `/v1/systemone` is recorded in History with its situation, questions and full answers, kept for 30 days by default. That happens without any change to your code; the record is a full studio decision, with `certainty` and an act gate at the studio's default threshold, even though your response was TypeSafe's plain shape.

Find a call by the decision id from the `x-basal-decision-id` header, or by the request id your SDK already logs: `GET /v1/studio/decisions?request_id=req_…`. Its `source` says which route and which client sent it. The response on the right is shortened.

Recording never changes the response, and a failure to record never fails the call: you get the answer with `x-basal-stored: failed` instead.

:::console GET /v1/studio/decisions/{id}
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TFA1NJ94HPXBB3QEJY6CED
```
@@ Response 200
```json
{
  "id": "dec_01M3TFA1NJ94HPXBB3QEJY6CED",
  "object": "decision",
  "status": "completed",
  "template": null,
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.8961, "probabilities": {"false": 0.1039, "true": 0.8961},
                "decision": "yes", "top_probability": 0.8961,
                "certainty": 0.8961, "act": false, "origin": "adhoc"},
    "team": {"type": "choice", "choice": "shipping",
             "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
             "confidence": 0.3294, "decision": "shipping", "top_probability": 0.553,
             "certainty": 0.553, "act": false, "origin": "adhoc"}
  },
  "act": false,
  "needs_review": ["damaged", "team"],
  "settings": {"act_threshold": 0.9, "temperature": 1.0, "questions": {},
               "sources": {"act_threshold": "studio", "temperature": "studio"}},
  "source": {"surface": "api", "endpoint": "/v1/systemone", "format": "typesafe", "client": "curl",
             "request_id": "req_71c4f1cccb6f40a5b63b8e1f86893f22", "attempt": 0, "retry_of": null},
  "store": "full"
}
```
:::

## Keeping calls out of History

To keep nothing for a call, add `"store": false` to the body, or send the header `X-Basal-Store: 0` if you cannot change the body. `"store": "answers_only"` (or `X-Basal-Store: answers_only`) keeps the answers without the situation. With the official Python SDK, pass `extra_body={"store": False}`.

TypeSafe's servers ignore these fields, so the same code runs against both. The studio reads them leniently: a value it does not understand is ignored and named in an `x-basal-warning` header, never turned into a 422.

To keep nothing from any caller, set the studio's level on the History page. [History and privacy](/docs/concepts/history) covers every switch.

:::console POST /v1/systemone
@@ curl
```bash
# nothing stored
curl -s -i http://127.0.0.1:8420/v1/systemone -d '{"model": "laya", "store": false, ...}'

# answers kept, situation not
curl -s -i http://127.0.0.1:8420/v1/systemone -H 'X-Basal-Store: answers_only' -d @body.json
```
@@ Output
```text
nothing stored:
x-basal-stored: none
x-typesafe-request-id: req_510ee38fe4524e469751d67c7dd6a3ec

answers kept, situation not:
x-basal-stored: answers_only
x-basal-decision-id: dec_01M3TFA55CE74BE6T6XMJYC0HT
```
:::

## Gateway formats

The studio also serves the formats of the two gateways that offer TypeSafe's models. Each route returns exactly that gateway's response and error shapes, so their clients work unchanged.

| Route | Format |
|---|---|
| `POST /v1/systemone`, `GET /v1/models` | TypeSafe's Jev API |
| `POST /api/v1/systemone`, `POST /api/alpha/decisions` | OpenRouter's Decisions API: adds `id`, `provider` and `usage.cost` |
| `POST /typesafe/v1/systemone`, `GET /typesafe/v1/models` | Vercel AI Gateway's TypeSafe route: adds `provider_metadata` |
| `POST /v1/evaluate` | Vercel AI Gateway's evaluation API: `boolean` questions answered with a `probability` |

On these routes the cost is always 0 and the provider is Bud Decision Studio. History records which format each call used.

:::console POST /api/v1/systemone
@@ OpenRouter
```json
{"model": "laya",
 "answers": {"damaged": {"type": "noul", "noul": 0.8961},
             "team": {"type": "choice", "choice": "shipping",
                      "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
                      "confidence": 0.3294}},
 "usage": {"input_tokens": 78, "output_tokens": 0, "cost": 0.0},
 "id": "gen-dec-d06f8b6a43c54fd19da148fa",
 "provider": "Bud Decision Studio"}
```
@@ Vercel
```json
{"model": "laya",
 "answers": {"damaged": {"type": "noul", "noul": 0.8961},
             "team": {"type": "choice", "choice": "shipping",
                      "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
                      "confidence": 0.3294}},
 "usage": {"input_tokens": 78, "output_tokens": 0},
 "provider_metadata": {"gateway": {"routing": {"originalModelId": "laya", "resolvedProvider": "Bud Decision Studio",
                                               "canonicalSlug": "laya", "finalProvider": "Bud Decision Studio"},
                                   "cost": "0", "generationId": "gen_2f46634da9764be2b96ef637"}}}
```
@@ Evaluate
```json
{"model": "laya",
 "answers": {"damaged": {"type": "boolean", "probability": 0.8943}},
 "usage": {"inputTokens": 41, "outputTokens": 0},
 "providerMetadata": {"basal": {"latencyMs": 232.6}}}
```
:::

## Label calls with a template

If your code sends the same questions every time, save them as a template and label each call with the header `X-Basal-Template: <id>@<version>`. The response does not change, but History attributes the call to that template version, so it appears in the template's history, statistics and version comparisons.

The call is attributed only when every question of that version is in the request with an identical definition. Otherwise it is stored without a template, and the response headers say why: `x-basal-template-status: mismatch` and `x-basal-warning: template_mismatch`.

:::console POST /v1/systemone
@@ curl
```bash
# once: the same questions as a template
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/returns-desk -d '{
  "name": "Returns desk",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "team": {"type": "choice", "instructions": "Who should handle this?",
             "criteria": {"returns": "refunds and replacements",
                          "shipping": "carriers and delivery", "sales": null}}
  },
  "model": "laya"
}'

# every call: unchanged, plus one header
curl -s -i http://127.0.0.1:8420/v1/systemone -H 'X-Basal-Template: returns-desk@1' -d @body.json
```
@@ Output
```text
with returns-desk@1:
x-basal-stored: full
x-basal-decision-id: dec_01M3TFAJYXH10F9BRWJTDM4WD0
x-basal-template-status: attributed

with a template whose questions differ:
x-basal-stored: full
x-basal-decision-id: dec_01M3TFAK4X1XN86M8XQW5PWVAR
x-basal-warning: template_mismatch
x-basal-template-status: mismatch
```
:::

## Move to the studio API step by step

Each step is optional, and each keeps working on its own.

:::steps
1. **Change nothing.** Your calls are already in History, and you can review, label and export them.
2. **Change the path, keep the body.** Any valid `/v1/systemone` body is also a valid `POST /v1/studio/decisions` body. Answers keep their keys and their TypeSafe fields, and gain `decision`, `certainty`, `act` and `origin`, plus `id`, `act` and `needs_review` on the decision. You no longer need `X-Basal-Extensions`.
3. **Save your questions once.** Put them in a template, then call `{"template": "returns-desk", "state": "…"}`. A template without variables takes the whole situation, so callers still send the text.
4. **Add variables when you want smaller requests.** Save a version that declares variables and a `state` with placeholders. Callers pinned to the old version keep sending `state`; callers of the new one send `variables`.
5. **Pin an alias.** Point `production` at a version and call `returns-desk@production`. Promoting a new version is then one request, with no deploy; see [Improve a template safely](/docs/guides/improve).
:::

:::console POST /v1/studio/decisions
@@ Same body
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d @body.json
```
@@ Template
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "returns-desk",
  "state": "The package arrived crushed and the screen is cracked."
}'
```
@@ Response 200
```json
{
  "id": "dec_01M3TFAKDHETT23K53CSES61QE",
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.8961,
                "probabilities": {"false": 0.1039, "true": 0.8961},
                "decision": "yes", "top_probability": 0.8961,
                "certainty": 0.8961, "act": false, "origin": "adhoc"},
    "team": {"type": "choice", "choice": "shipping",
             "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
             "confidence": 0.3294, "decision": "shipping", "top_probability": 0.553,
             "certainty": 0.553, "act": false, "origin": "adhoc"}
  },
  "act": false,
  "needs_review": ["damaged", "team"],
  "usage": {"input_tokens": 78, "output_tokens": 0}
}
```
:::

## Differences to know

- **The model must be on this computer.** A model that is downloaded but not loaded is loaded by the first call, which can take minutes; a model that is not downloaded returns an error. TypeSafe's SDK times out after 10 seconds by default, so load models ahead of time or raise the timeout.
- **`jev-latest` is whichever model you loaded last.** Name a model explicitly when the choice matters.
- **Calls are recorded by default.** See [Keeping calls out of History](#keeping-calls-out-of-history).
- **Only this computer can call it,** unless you start the studio with `--host 0.0.0.0` and set `BASAL_API_KEY`. Remote clients then send the key as `Authorization: Bearer <key>`, as they do with TypeSafe.
- **Extra question types and media** are accepted on every route. TypeSafe's hosted API rejects them, so code that uses them runs only against the studio.
