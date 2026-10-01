---
title: History, feedback and statistics
description: List, search, filter, export and summarise stored decisions, label them with the right answers, and delete or redact many at once.
lead: Every stored decision can be found again, filtered by anything it carries, labelled with the right answer and summarised. These are the same queries the History page runs.
---

History holds decisions from every entry point: the studio API, `/v1/systemone` and the gateway formats, the Playground and Compare. What each decision keeps depends on its [storage level](/docs/api/decisions#choose-what-history-keeps), and old decisions are removed by retention (30 days unless you change it in [settings](/docs/api/resources#read-the-settings)). Pinned decisions, and by default labelled ones, are never removed by retention.

## List decisions

::endpoint GET /v1/studio/decisions

Returns a page of decision summaries, newest first. A summary carries each answer as one label (`decision`) with its certainty, which is enough to scan a list; fetch a [single decision](/docs/api/decisions#retrieve-a-decision) for the input and the probabilities, or pass `view=full` to get full objects in the list.

Filters combine with AND. Most accept several values separated by commas, which combine with OR.

| Parameter | Description |
|---|---|
| `template` | A template id (every version), a reference such as `support-triage@2` or `support-triage@production` (that version), or `none` for decisions without a template. |
| `version` | Version numbers, such as `2,3`. |
| `model` | Model ids. |
| `status` | `completed`, `failed`, `queued`, `in_progress`, `cancelled`. |
| `act` | `false` for decisions that needed a person, `true` for those that acted. |
| `needs_review` | Question keys: decisions where that question did not act. |
| `answer.<question>` | Decisions whose answer to a question was this label, such as `answer.department=billing` or `answer.churn_risk=yes`. |
| `certainty_below.<question>` | Decisions where that answer's certainty was below a number, such as `certainty_below.urgency=0.6`. `certainty_above.<question>` is the opposite. |
| `metadata.<key>` | Exact match on a metadata value, such as `metadata.ticket_id=T-4411`. |
| `q` | Words in the situation or in text variables, matched as whole words. Only decisions stored in full are searchable. |
| `created_after`, `created_before` | Unix seconds, an RFC 3339 time, or a relative time such as `-24h` or `-7d`. |
| `labelled` | `true` for decisions with feedback. |
| `correct` | `false` for decisions with at least one wrong label; `true` for labelled decisions with none. |
| `pinned` | `true` or `false`. |
| `surface` | `api`, `playground`, `compare`, `rerun`, or `all`. |
| `client`, `format`, `endpoint`, `request_id` | Where calls came from, such as `client=curl` or `format=typesafe`. |
| `rerun_of` | Reruns of one decision. |
| `extended` | `true` for template decisions that added questions or options, or skipped questions. |
| `attribution` | For a template filter: `explicit` and `header` by default; add `draft` for the Playground's unsaved edits, or pass `all`. |
| `fold_retries` | `true` by default: hides SDK retry attempts that a later attempt replaced. |
| `view` | `summary` (the default) or `full`. |
| `include` | With `view=full`: `input`, `input.rendered_state`, `answers.raw_probabilities`. |
| `limit`, `after`, `before`, `order` | [Pages of results](/docs/api/index#pages-of-results): 1 to 100 per page, cursors, `desc` or `asc`. |

:::console GET /v1/studio/decisions
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/decisions?template=support-triage&act=false&limit=2"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
page = studio.get("/decisions", params={
    "template": "support-triage", "act": "false", "limit": 2,
}).raise_for_status().json()
for d in page["data"]:
    print(d["id"], d["needs_review"])
```
@@ JavaScript
```js
const q = new URLSearchParams({ template: "support-triage", act: "false", limit: "2" });
const page = await (await fetch(`http://127.0.0.1:8420/v1/studio/decisions?${q}`)).json();
for (const d of page.data) console.log(d.id, d.needs_review);
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {
      "id": "dec_01M3TE8RKPP70FYT393DKVTR93",
      "object": "decision.summary",
      "status": "completed",
      "created_at": 1790815003,
      "template": {"id": "support-triage", "version": 1},
      "model": "laya",
      "source": {
        "surface": "api",
        "client": "curl",
        "attempt": 0
      },
      "extended": false,
      "act": false,
      "needs_review": ["department", "urgency"],
      "answers": {
        "department": {
          "type": "choice",
          "decision": "technical",
          "certainty": 0.767,
          "act": false
        },
        "urgency": {
          "type": "score",
          "decision": "3",
          "certainty": 0.5683,
          "act": false
        },
        "churn_risk": {
          "type": "noul",
          "decision": "no",
          "certainty": 0.8332,
          "act": true
        }
      },
      "timing": {"total_ms": 76.5},
      "metadata": {},
      "pinned": false,
      "labelled": false,
      "error": null
    },
    {
      "id": "dec_01M3TE8JRZMJH9TQ6FTG1AJ4J6",
      "object": "decision.summary",
      "status": "completed",
      "created_at": 1790814997,
      "template": {"id": "support-triage", "version": 1},
      "model": "laya",
      "source": {
        "surface": "api",
        "client": "curl",
        "attempt": 0
      },
      "extended": true,
      "act": false,
      "needs_review": ["urgency"],
      "answers": {
        "department": {
          "type": "choice",
          "decision": "security",
          "certainty": 0.9301,
          "act": true
        },
        "urgency": {
          "type": "score",
          "decision": "2",
          "certainty": 0.5974,
          "act": false
        },
        "outage": {
          "type": "noul",
          "decision": "yes",
          "certainty": 0.9288,
          "act": true
        }
      },
      "timing": {"total_ms": 148.7},
      "metadata": {},
      "pinned": false,
      "labelled": false,
      "error": null
    }
  ],
  "first_id": "dec_01M3TE8RKPP70FYT393DKVTR93",
  "last_id": "dec_01M3TE8JRZMJH9TQ6FTG1AJ4J6",
  "has_more": true
}
```
:::

:::tip Find what to review first
Combine `act=false`, `certainty_below.department=0.6` and `created_after=-24h` to list the last day's decisions where the department was least clear. Add `labelled=false` to keep only those nobody has checked yet.
:::

## Decision statistics

::endpoint GET /v1/studio/decisions/stats

Summarises every decision that matches the [list filters](#list-decisions): how many there were, how often they acted, how fast they were, and for each question how answers were spread, how sure the model was and, where you labelled decisions, how often it was right.

| Parameter | Description |
|---|---|
| `group_by` | Split the summary: `version`, `template`, `model`, `model_revision`, `surface`, `client`, `format`, `status`, `day` or `hour`. Several, comma-separated. |
| `questions` | Only these questions. |
| `include_extended` | `true` to count answers to extra questions and extended options too. By default only template questions (or questions of decisions without a template) count. |
| `what_if.act_threshold` | Recompute how often decisions would have acted at another threshold, and how often they would have been right when acting. |
| `what_if.temperature` | The same at another calibration temperature, from the stored raw probabilities. |

Each group has `count`, `failed`, `act_rate` and `latency_ms` (median `p50` and `p95`, in milliseconds). Each question has:

| Field | Description |
|---|---|
| `runs` | Answers counted. `excluded_runs` were left out: extended, or replaced by a retry. |
| `mean_certainty` | The average certainty. |
| `act_rate` | The share of answers that reached their act threshold. |
| `distribution` | The share of each label. `null` when the options came from a variable and differ between decisions. |
| `labelled`, `accuracy` | How many answers have feedback, and the share of those that were right. |

:::console GET /v1/studio/decisions/stats
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/decisions/stats?template=support-triage&questions=department,urgency&what_if.act_threshold=0.6"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
stats = studio.get("/decisions/stats", params={
    "template": "support-triage",
    "questions": "department,urgency",
    "what_if.act_threshold": 0.6,
}).raise_for_status().json()
g = stats["groups"][0]
print(g["act_rate"], "now;", g["what_if"]["act_rate"], "at 0.6")
```
@@ JavaScript
```js
const q = new URLSearchParams({
  template: "support-triage",
  questions: "department,urgency",
  "what_if.act_threshold": "0.6",
});
const stats = await (await fetch(`http://127.0.0.1:8420/v1/studio/decisions/stats?${q}`)).json();
console.log(stats.groups[0].act_rate, stats.groups[0].what_if.act_rate);
```
@@ Response 200
```json
{
  "object": "decision.stats",
  "group_by": [],
  "filters": {
    "template": "support-triage",
    "attribution": ["explicit", "header"]
  },
  "what_if": {"temperature": null, "act_threshold": 0.6},
  "groups": [
    {
      "key": {},
      "count": 24,
      "failed": 0,
      "act_rate": 0.0417,
      "latency_ms": {"p50": 145.6, "p95": 331.0},
      "questions": {
        "department": {
          "runs": 23,
          "excluded_runs": 1,
          "mean_certainty": 0.8868,
          "act_rate": 0.7826,
          "distribution": {
            "billing": 0.6087,
            "technical": 0.3043,
            "sales": 0.087
          },
          "labelled": 1,
          "accuracy": 1.0
        },
        "urgency": {
          "runs": 24,
          "excluded_runs": 0,
          "mean_certainty": 0.5658,
          "act_rate": 0.0417,
          "distribution": {
            "2": 0.7917,
            "3": 0.125,
            "1": 0.0833
          },
          "labelled": 1,
          "accuracy": 1.0
        }
      },
      "what_if": {
        "act_threshold": 0.6,
        "temperature": null,
        "act_rate": 0.1667,
        "accuracy_when_acting": 1.0,
        "labelled_acting": 3
      }
    }
  ]
}
```
:::

In this history, decisions acted 4% of the time at their template's threshold, and would have acted 17% of the time at 0.6. `accuracy_when_acting` rests on only 3 labelled answers here, so label more decisions before trusting it. For one template, [its statistics](/docs/api/templates#a-templates-history-and-statistics) group by version by default and say which questions are comparable between versions.

## Export decisions

::endpoint GET /v1/studio/decisions/export

Downloads every decision matching the [list filters](#list-decisions), as a file.

| Parameter | Description |
|---|---|
| `format` | `jsonl` (the default): one decision object per line, without its input unless you ask for it with `include`. `csv`: one row per decision with each question's label, certainty and act flag, and one column per metadata key. |
| `wire_format` | Filters by the API format of the call (`typesafe`, `studio`, ...). Here `format` names the file format, so the filter takes this name instead. |
| `include` | For `jsonl`: `input`, `input.rendered_state`, `answers.raw_probabilities`. |

The response is a download (`content-disposition: attachment`), `application/x-ndjson` or `text/csv`.

:::console GET /v1/studio/decisions/export
@@ curl
```bash
curl -s "http://127.0.0.1:8420/v1/studio/decisions/export?template=support-triage&format=csv" \
  -o support-triage.csv
```
@@ Python
```python
import httpx

with httpx.stream("GET", "http://127.0.0.1:8420/v1/studio/decisions/export",
                  params={"template": "support-triage", "format": "csv"}, timeout=None) as r:
    with open("support-triage.csv", "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)
```
@@ JavaScript
```js
import { writeFile } from "node:fs/promises";

const q = new URLSearchParams({ template: "support-triage", format: "csv" });
const res = await fetch(`http://127.0.0.1:8420/v1/studio/decisions/export?${q}`);
await writeFile("support-triage.csv", await res.text());
```
@@ Output
```text
id,created_at,template,version,model,status,act,department,department.certainty,department.act,urgency,urgency.certainty,urgency.act,churn_risk,churn_risk.certainty,churn_risk.act,outage,outage.certainty,outage.act,wants_refund,wants_refund.certainty,wants_refund.act,metadata.ticket_id,metadata.reviewed_by,metadata.channel
dec_01M3TE7XWB2SGE27X70J365D9H,1790814975,support-triage,1,laya,completed,false,billing,0.9838,true,2,0.8461,false,yes,0.6393,false,,,,,,,T-4411,,
dec_01M3TE87TZX20V76ZZB6D55WWR,1790814986,support-triage,1,laya,completed,false,billing,0.9838,true,2,0.8461,false,yes,0.6393,false,,,,,,,T-4411,ops,
dec_01M3TE8JRZMJH9TQ6FTG1AJ4J6,1790814997,support-triage,1,laya,completed,false,security,0.9301,true,2,0.5974,false,,,,yes,0.9288,true,,,,,,
```
:::

The output shows the header and the first three rows. Columns cover every question and metadata key that appears in any exported decision, oldest decision first; cells are empty where a decision did not have them.

## Label a decision

::endpoint POST /v1/studio/decisions/{id}/feedback

Records the right answer to some or all of a decision's questions, a thumbs-up or thumbs-down rating, or both. Labels feed `accuracy` in statistics, can become [test examples](/docs/api/examples#add-a-test-example), and keep the decision safe from retention while `keep_labelled` is on. Labelling the same question again replaces the earlier label.

| Field | Type | Description |
|---|---|---|
| `expected` | object | Question key to the right answer. |
| `rating` | integer | `1` or `-1`, for the decision as a whole. |
| `note` | string | Up to 2,000 characters. |

At least one of `expected` and `rating` is required. Write each answer the way the question's type reads it:

| Type | Write the right answer as |
|---|---|
| `choice`, `rank` | An option name: `"billing"`. For `rank`, the option that should come first. |
| `score` | A level number, `2` or `"2"`, or the level's text, `"today"`. |
| `noul` | `true`, `false`, `"yes"` or `"no"`. |
| `multi` | A list of option names: `["billing", "account"]`. |
| `number` | A number. It counts as right when it falls within the answer's `range`. |

The response lists one feedback entry per question, with the label in its stored form, what the model said (`predicted`) and whether it was right (`correct`). A rating without labels is stored under the question `*`.

:::console POST /v1/studio/decisions/{id}/feedback
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/feedback -d '{
  "expected": {"department": "billing", "urgency": "today", "churn_risk": true},
  "note": "Checked on the call."
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
fb = studio.post("/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/feedback", json={
    "expected": {"department": "billing", "urgency": "today", "churn_risk": True},
    "note": "Checked on the call.",
}).raise_for_status().json()
print([(f["question"], f["correct"]) for f in fb["data"]])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TE87TZX20V76ZZB6D55WWR/feedback", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    expected: { department: "billing", urgency: "today", churn_risk: true },
    note: "Checked on the call.",
  }),
});
const fb = await res.json();
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
    {
      "id": "fb_01M3TEC5AY13M3045X3GKEZF0N",
      "object": "feedback",
      "decision_id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
      "question": "department",
      "expected": "billing",
      "predicted": "billing",
      "correct": true,
      "rating": null,
      "note": "Checked on the call.",
      "actor": "local",
      "created_at": 1790815114,
      "updated_at": 1790815114
    },
    {
      "id": "fb_01M3TEC5AYV5ZN035WK81B8ESS",
      "object": "feedback",
      "decision_id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
      "question": "urgency",
      "expected": "2",
      "predicted": "2",
      "correct": true,
      "rating": null,
      "note": "Checked on the call.",
      "actor": "local",
      "created_at": 1790815114,
      "updated_at": 1790815114
    },
    {
      "id": "fb_01M3TEC5AYV463Y7DVC1EWSA9X",
      "object": "feedback",
      "decision_id": "dec_01M3TE87TZX20V76ZZB6D55WWR",
      "question": "churn_risk",
      "expected": "yes",
      "predicted": "yes",
      "correct": true,
      "rating": null,
      "note": "Checked on the call.",
      "actor": "local",
      "created_at": 1790815114,
      "updated_at": 1790815114
    }
  ],
  "first_id": "fb_01M3TEC5AY13M3045X3GKEZF0N",
  "last_id": "fb_01M3TEC5AYV463Y7DVC1EWSA9X",
  "has_more": false
}
```
:::

A label that does not fit the question answers `400 invalid_expected`, naming the options it accepts.

## List and delete feedback

::endpoint GET /v1/studio/decisions/{id}/feedback

::endpoint DELETE /v1/studio/feedback/{id}

`GET` returns every feedback entry of a decision, oldest first, in the same list shape as above. `DELETE` removes one entry by its `fb_` id. The decision's `feedback` field always shows the latest label per question.

:::console DELETE /v1/studio/feedback/{id}
@@ curl
```bash
curl -s -X DELETE http://127.0.0.1:8420/v1/studio/feedback/fb_01M3TECSHWXMDF5DZKXDR8617E
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
studio.delete("/feedback/fb_01M3TECSHWXMDF5DZKXDR8617E").raise_for_status()
```
@@ JavaScript
```js
await fetch("http://127.0.0.1:8420/v1/studio/feedback/fb_01M3TECSHWXMDF5DZKXDR8617E", { method: "DELETE" });
```
@@ Response 200
```json
{
  "id": "fb_01M3TECSHWXMDF5DZKXDR8617E",
  "object": "feedback.deleted",
  "deleted": true
}
```
:::

## Delete or redact many decisions

::endpoint POST /v1/studio/decisions/delete

::endpoint POST /v1/studio/decisions/redact

Deletes, or removes input from, every decision that matches a filter. The filter takes the same keys as [List decisions](#list-decisions), written as a JSON object. Run with `dry_run: true` first: it counts what would change and changes nothing.

| Field | Type | Description |
|---|---|---|
| `filter` | object | List filters, such as `{"template": "support-triage", "created_before": "-30d"}`. Every surface is included unless you filter by `surface`. |
| `all` | boolean | `true` to act on every decision. Required when `filter` is empty, so an empty body cannot erase history by accident. |
| `dry_run` | boolean | Count only. |
| `include_pinned` | boolean | Also act on pinned decisions, which are skipped by default. |
| `fields` | array | For `/redact`, required: the same fields as [Redact a decision](/docs/api/decisions#redact-a-decision). |

The filter also takes `sensitive_hash`, an object of variable name to value, such as `{"customer_email": "ana@example.com"}`. Values of variables marked `sensitive` are never stored, only a keyed hash of them, and this finds every decision made with that value so you can erase one person's data.

The response counts the decisions that matched, how many were deleted or redacted, and how many pinned ones were skipped.

:::console POST /v1/studio/decisions/delete
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/delete -d '{
  "filter": {"template": "support-triage", "version": "1"},
  "dry_run": true
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.post("/decisions/delete", json={
    "filter": {"template": "support-triage", "version": "1"},
    "dry_run": True,
}).raise_for_status().json()
print(r["matched"], "would go;", r["skipped_pinned"], "pinned kept")
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/delete", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ filter: { template: "support-triage", version: "1" }, dry_run: true }),
});
console.log(await res.json());
```
@@ Response 200
```json
{
  "object": "decision.bulk_delete",
  "matched": 13,
  "deleted": 0,
  "skipped_pinned": 1,
  "dry_run": true
}
```
:::
