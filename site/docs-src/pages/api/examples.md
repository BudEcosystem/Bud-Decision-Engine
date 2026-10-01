---
title: Test examples
description: Keep a set of inputs with their right answers for each template, add them from history, import and export them, and read any past revision.
lead: Test examples are inputs with their right answers, kept with a template. They let you check a new version or another model against cases you already know, instead of trusting a feeling.
---

Each template has its own set of test examples. An example holds the same input a decision would (variables, or a situation for a template without variables, plus any media) and the expected answer to some or all of its questions. The template's **Examples** tab in the app shows the same data.

The set is revisioned. Every add, edit or removal raises the template's `examples_revision` by one and never overwrites the old rows, so you can always read the exact set a past evaluation used. Starter templates come with one example each.

## Add a test example

::endpoint POST /v1/studio/templates/{id}/examples

Adds one example. The input is checked against the template's latest version exactly as a decision would be, and each expected answer must fit its question. Adding an input that is already in the set answers `409 example_exists` with the existing example's id in `existing_id`.

| Field | Type | Description |
|---|---|---|
| `variables` | object | The template's variables. |
| `state` | string, object or array | The situation, for a template without variables. |
| `media` | array | Media items, as in a decision. |
| `expected` | object | Question key to the right answer, written as for [feedback](/docs/api/history#label-a-decision): `"today"` and `"2"` both mean level 2 of a scale. |
| `tags` | array | Short labels for grouping, such as `regression`. |
| `split` | string | `test` (the default) or `calibration`. |
| `note` | string | Up to 2,000 characters. |
| `from_decision` | string | A decision id to copy the input from. Labels from its feedback become expected answers, and labels you send are added on top. |

:::console POST /v1/studio/templates/{id}/examples
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/examples -d '{
  "variables": {"customer_message": "I was charged twice for the same order.",
                "account_tier": "pro"},
  "expected": {"department": "billing", "urgency": "today"},
  "tags": ["regression", "billing"]
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
ex = studio.post("/templates/support-triage/examples", json={
    "variables": {"customer_message": "I was charged twice for the same order.",
                  "account_tier": "pro"},
    "expected": {"department": "billing", "urgency": "today"},
    "tags": ["regression", "billing"],
}).raise_for_status().json()
print(ex["id"], ex["revision"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/examples", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    variables: { customer_message: "I was charged twice for the same order.", account_tier: "pro" },
    expected: { department: "billing", urgency: "today" },
    tags: ["regression", "billing"],
  }),
});
const ex = await res.json();
```
@@ Response 200
```json
{
  "id": "ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
  "object": "template.example",
  "template": "support-triage",
  "revision": 1,
  "variables": {
    "customer_message": "I was charged twice for the same order.",
    "account_tier": "pro"
  },
  "state": null,
  "media": [],
  "expected": {"department": "billing", "urgency": "2"},
  "tags": ["regression", "billing"],
  "split": "test",
  "note": "",
  "from_decision": null,
  "created_at": 1790815277,
  "updated_at": 1790815277
}
```
:::

### From a decision in history

The quickest way to grow a useful set is to promote decisions a person has already checked. `from_decision` copies the decision's input, and its [feedback](/docs/api/history#label-a-decision) becomes the expected answers. The decision must have been stored in full, without redactions and without sensitive variables, which history never keeps. For a template with variables, it must also have been made with this template, so its variables are known.

:::console POST /v1/studio/templates/{id}/examples
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/examples -d '{
  "from_decision": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "tags": ["from-review"]
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
ex = studio.post("/templates/support-triage/examples", json={
    "from_decision": "dec_01M3TE87TZX20V76ZZB6D55WWR", "tags": ["from-review"],
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/examples", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ from_decision: "dec_01M3TE87TZX20V76ZZB6D55WWR", tags: ["from-review"] }),
});
const ex = await res.json();
```
@@ Response 200
```json
{
  "id": "ex_01M3TEH4DQQA4KD03T4X5BY9HD",
  "object": "template.example",
  "template": "support-triage",
  "revision": 2,
  "variables": {
    "customer_message": "We were billed twice for March on invoice 4411. Refund the duplicate today or we cancel.",
    "account_tier": "enterprise"
  },
  "state": null,
  "media": [],
  "expected": {
    "churn_risk": "yes",
    "department": "billing",
    "urgency": "2"
  },
  "tags": ["from-review"],
  "split": "test",
  "note": "",
  "from_decision": "dec_01M3TE87TZX20V76ZZB6D55WWR",
  "created_at": 1790815277,
  "updated_at": 1790815277
}
```
:::

## List test examples

::endpoint GET /v1/studio/templates/{id}/examples

Returns the current examples in the order they were added, with the set's `revision`.

| Parameter | Description |
|---|---|
| `revision` | Read the set as it was at an earlier revision. |
| `tag` | Only examples with this tag. |
| `split` | `test` or `calibration`. |
| `labelled` | `true` for examples with at least one expected answer, `false` for those without. |
| `q` | Text in the input. |
| `limit`, `after` | Up to 1,000 per page (default 100); `after` takes an example id. |

:::console GET /v1/studio/templates/{id}/examples
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/templates/support-triage/examples?tag=regression"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
page = studio.get("/templates/support-triage/examples", params={"tag": "regression"}).json()
print(page["revision"], [e["id"] for e in page["data"]])
```
@@ JavaScript
```js
const page = await (await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/examples?tag=regression",
)).json();
console.log(page.revision, page.data.map((e) => e.id));
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {
      "id": "ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
      "object": "template.example",
      "template": "support-triage",
      "revision": 1,
      "variables": {
        "customer_message": "I was charged twice for the same order.",
        "account_tier": "pro"
      },
      "state": null,
      "media": [],
      "expected": {"department": "billing", "urgency": "2"},
      "tags": ["regression", "billing"],
      "split": "test",
      "note": "",
      "from_decision": null,
      "created_at": 1790815277,
      "updated_at": 1790815277
    }
  ],
  "first_id": "ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
  "last_id": "ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
  "has_more": false,
  "revision": 4
}
```
:::

## Retrieve a test example

::endpoint GET /v1/studio/templates/{id}/examples/{eid}

Returns one example as it is now, or as it was at an earlier set revision with `?revision=`. An example keeps its id across edits; each edit is a new row with a new `revision`.

## Update a test example

::endpoint PATCH /v1/studio/templates/{id}/examples/{eid}

Changes an example by writing a new revision of it; the old revision stays readable. `variables` and `expected` merge with what is there (send `null` to remove a key); `state`, `media`, `tags`, `split` and `note` replace.

:::console PATCH /v1/studio/templates/{id}/examples/{eid}
@@ curl
```bash
curl -s -X PATCH \
  http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/ex_01M3TEH4CS8CWPKV7CF4RW5WDH \
  -d '{"expected": {"churn_risk": "no"}, "tags": ["regression", "billing", "checked"]}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
ex = studio.patch("/templates/support-triage/examples/ex_01M3TEH4CS8CWPKV7CF4RW5WDH", json={
    "expected": {"churn_risk": "no"}, "tags": ["regression", "billing", "checked"],
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
  {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected: { churn_risk: "no" }, tags: ["regression", "billing", "checked"] }),
  },
);
const ex = await res.json();
```
@@ Response 200
```json
{
  "id": "ex_01M3TEH4CS8CWPKV7CF4RW5WDH",
  "object": "template.example",
  "template": "support-triage",
  "revision": 5,
  "variables": {
    "customer_message": "I was charged twice for the same order.",
    "account_tier": "pro"
  },
  "state": null,
  "media": [],
  "expected": {
    "department": "billing",
    "urgency": "2",
    "churn_risk": "no"
  },
  "tags": ["regression", "billing", "checked"],
  "split": "test",
  "note": "",
  "from_decision": null,
  "created_at": 1790815277,
  "updated_at": 1790815277
}
```
:::

Reading the same example with `?revision=1` still returns it as first added, with two expected answers and two tags.

## Retire a test example

::endpoint DELETE /v1/studio/templates/{id}/examples/{eid}

Removes an example from the current set. Past revisions of the set still include it, so earlier evaluations stay reproducible.

:::console DELETE /v1/studio/templates/{id}/examples/{eid}
@@ curl
```bash
curl -s -X DELETE \
  http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/ex_01M3TEH4GD6F1NZTDCVXPYBJXB
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
studio.delete("/templates/support-triage/examples/ex_01M3TEH4GD6F1NZTDCVXPYBJXB").raise_for_status()
```
@@ JavaScript
```js
await fetch(
  "http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/ex_01M3TEH4GD6F1NZTDCVXPYBJXB",
  { method: "DELETE" },
);
```
@@ Response 200
```json
{
  "id": "ex_01M3TEH4GD6F1NZTDCVXPYBJXB",
  "object": "template.example.deleted",
  "deleted": true
}
```
:::

## Import test examples

::endpoint POST /v1/studio/templates/{id}/examples/import

Adds up to 5,000 examples in one call. Each item takes the same fields as [Add a test example](#add-a-test-example). Items that fail do not stop the others: the response counts what was created and lists each failure with its position in the list and the error.

| Field | Type | Description |
|---|---|---|
| `examples` | array | Required. 1 to 5,000 example objects. |

:::console POST /v1/studio/templates/{id}/examples/import
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/import -d '{
  "examples": [
    {"variables": {"customer_message": "Please add two more seats to our plan.",
                   "account_tier": "enterprise"},
     "expected": {"department": "sales"}},
    {"variables": {"customer_message": "The API returns 502 for every request.",
                   "account_tier": "enterprise"},
     "expected": {"department": "technical", "urgency": "3"}},
    {"variables": {"customer_message": "Hello?"},
     "expected": {"department": "support"}}
  ]
}'
```
@@ Python
```python
import json
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
with open("cases.jsonl") as f:                     # one example object per line
    examples = [json.loads(line) for line in f if line.strip()]
r = studio.post("/templates/support-triage/examples/import",
                json={"examples": examples}).raise_for_status().json()
print(r["created"], "added;", len(r["failed"]), "failed")
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/import", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    examples: [
      { variables: { customer_message: "Please add two more seats to our plan.", account_tier: "enterprise" },
        expected: { department: "sales" } },
      { variables: { customer_message: "The API returns 502 for every request.", account_tier: "enterprise" },
        expected: { department: "technical", urgency: "3" } },
      { variables: { customer_message: "Hello?" }, expected: { department: "support" } },
    ],
  }),
});
const r = await res.json();
```
@@ Response 200
```json
{
  "object": "import_result",
  "created": 2,
  "revision": 4,
  "failed": [
    {
      "index": 2,
      "error": {
        "code": "invalid_expected",
        "param": "expected.department",
        "message": "Expected one of billing, technical, sales; got \"support\"."
      }
    }
  ]
}
```
:::

## Export test examples

::endpoint GET /v1/studio/templates/{id}/examples/export

Downloads the current examples as JSON Lines, one example object per line, in a file named after the template. To copy examples to another studio, send their input and expected fields to [import](#import-test-examples); leave out `from_decision`, which names a decision only this studio has.

:::console GET /v1/studio/templates/{id}/examples/export
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/export \
  -o support-triage-examples.jsonl
```
@@ Python
```python
import httpx

r = httpx.get("http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/export")
open("support-triage-examples.jsonl", "wb").write(r.content)
```
@@ JavaScript
```js
import { writeFile } from "node:fs/promises";

const res = await fetch("http://127.0.0.1:8420/v1/studio/templates/support-triage/examples/export");
await writeFile("support-triage-examples.jsonl", await res.text());
```
@@ Output
```http
HTTP/1.1 200 OK
content-disposition: attachment; filename="support-triage-examples.jsonl"
content-type: application/x-ndjson

{"id": "ex_01M3TEH4DQQA4KD03T4X5BY9HD", "object": "template.example", "template": "support-triage", "revision": 2, ...}
```
:::

Each line is a complete example object; the one above is cut short.
