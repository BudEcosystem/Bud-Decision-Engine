---
title: Call the studio from your code
description: Call Bud Decision Studio from Python and JavaScript, handle errors, retry safely with idempotency keys, cope with slow model starts, and keep calls out of History.
lead: The studio is an HTTP server on your computer, so any language can call it. This guide builds a small, dependable client in Python and in JavaScript, and covers what goes wrong in practice and how to handle it.
---

## The address and keys

While the app is open, the studio listens at `http://127.0.0.1:8420`. If that port is taken, the desktop app picks the first free port from 8421 to 8440 and remembers it; the **API** page in the app always shows the exact address.

Calls from the same computer need no key. To call the studio from other machines, start it with `--host 0.0.0.0` and set the environment variable `BASAL_API_KEY`; clients then send `Authorization: Bearer <key>`. Without a key, remote callers can make decisions but cannot read History or change anything.

Requests are JSON. Clients such as curl, SDKs and servers do not need a `Content-Type` header, because the studio reads the body as JSON whatever it says; requests from a web browser must send `application/json`.

This guide uses the studio's own API, `/v1/studio`. If your code already speaks TypeSafe's Jev API, it works as it is; see [Move from Jev or a gateway](/docs/guides/migrate).

## A small client

The client on the right does four things:

- sends one decision and returns the stored decision object;
- raises an error that carries the studio's `code`, `message` and `param`, so your code can branch on the code;
- retries only what is worth retrying, with a pause that grows each time;
- sends the same **idempotency key** on every attempt, so a retry can never make a second decision.

It needs no SDK: `httpx` in Python, the built-in `fetch` in JavaScript. Both versions were run against a studio with the support-triage template from [the triage guide](/docs/guides/support-triage); the output shows the decision, then the second call's error.

:::console POST /v1/studio/decisions
@@ Python
```python
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8420/v1/studio"


def retryable(status: int, code: str) -> bool:
    """A busy or restarting model is worth another try; a 400 is a mistake in the request."""
    return status in (429, 500, 503, 504) or code == "idempotency_in_progress"


class StudioError(Exception):
    def __init__(self, status: int, error: dict):
        super().__init__(f"{status} {error['code']}: {error['message']}")
        self.status, self.code, self.error = status, error["code"], error


def decide(body: dict, *, key: str | None = None, attempts: int = 4) -> dict:
    """POST /decisions with one idempotency key for every attempt, so a retry never makes a second decision."""
    key = key or str(uuid.uuid4())
    with httpx.Client(base_url=BASE, timeout=120) as client:
        for attempt in range(attempts):
            r = client.post("/decisions", json=body, headers={"Idempotency-Key": key})
            if r.status_code == 200:
                return r.json()
            error = r.json()["error"]
            if not retryable(r.status_code, error["code"]) or attempt == attempts - 1:
                raise StudioError(r.status_code, error)
            time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))


d = decide({"template": "support-triage",
            "variables": {"customer_message": "We were billed twice for March. Refund the duplicate today.",
                          "account_tier": "pro"},
            "metadata": {"ticket_id": "T-5132"}},
           key="ticket-T-5132-triage")
print(d["id"], d["act"], d["needs_review"])

try:
    decide({"template": "support-triage", "variables": {"customer_message": "Hello", "account_tier": "gold"}})
except StudioError as e:
    print(e.status, e.code, e.error["param"])
```
@@ JavaScript
```js
const BASE = "http://127.0.0.1:8420/v1/studio";

class StudioError extends Error {
  constructor(status, error) {
    super(`${status} ${error.code}: ${error.message}`);
    Object.assign(this, { status, code: error.code, error });
  }
}

const retryable = (status, code) => [429, 500, 503, 504].includes(status) || code === "idempotency_in_progress";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// POST /decisions with one idempotency key for every attempt, so a retry never makes a second decision.
export async function decide(body, { key = crypto.randomUUID(), attempts = 4 } = {}) {
  for (let attempt = 0; ; attempt++) {
    const res = await fetch(`${BASE}/decisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120_000),
    });
    const data = await res.json();
    if (res.ok) return data;
    if (!retryable(res.status, data.error.code) || attempt === attempts - 1) throw new StudioError(res.status, data.error);
    await sleep(1000 * Number(res.headers.get("retry-after") ?? 2 ** attempt));
  }
}

const d = await decide({
  template: "support-triage",
  variables: { customer_message: "We were billed twice for March. Refund the duplicate today.", account_tier: "pro" },
  metadata: { ticket_id: "T-5133" },
}, { key: "ticket-T-5133-triage" });
console.log(d.id, d.act, d.needs_review);
```
@@ Output
```text
dec_01M3TG3BWT4NGDGPFE2YMQTQGF True []
400 invalid_variable variables.account_tier
```
:::

## Errors

Every error from `/v1/studio` has the same envelope: a `type`, a machine-readable `code`, a `message` written for people, the `param` that caused it (such as `variables.account_tier`), and `details` listing every problem found in the same pass. When a failed decision was still stored, for example because the model crashed, `decision_id` points at it.

What to do depends on the status:

| Status | Means | What to do |
|---|---|---|
| 400 | The request is wrong: an unknown variable, a missing question, a model that cannot read the input | Fix the request. Retrying returns the same error. |
| 401, 403 | A missing or wrong key, or a request from a web page on another site | Check the key and where the request comes from. |
| 404 | No such template, version, alias, decision or model | Check the name; the message lists what does exist. |
| 409 | A conflict: the same idempotency key with a different body, a model that is not downloaded, a finished decision | Read the `code`. Only `idempotency_in_progress` is worth retrying. |
| 412 | A template save based on an old version | Reload the template and save again. |
| 429, 500, 503, 504 | The model is busy, restarting, crashed or timed out | Retry with the same idempotency key, after `retry-after` seconds when given. |

[Errors](/docs/api/errors) lists every code.

## Retries and idempotency keys

Networks drop responses. If your client times out after the studio has made a decision, a plain retry makes a second decision, and History counts the ticket twice. An **idempotency key** prevents that: send the header `Idempotency-Key` with a value unique to the logical call (a UUID, or a stable id such as `ticket-T-5130-triage`), and send the same value on every retry of that call.

| Situation | Result |
|---|---|
| Same key, same body, first call succeeded | The stored response is returned again, with the same decision id and the header `x-basal-idempotent-replayed: true`. No model runs. |
| Same key while the first call is still running | `409 idempotency_in_progress` with `retry-after: 1`. |
| Same key, different body | `409 idempotency_key_reused`. Nothing runs. Use a new key for each logical call. |
| The first call failed | Nothing is saved for the key; a retry runs again. |

Keys are kept for 24 hours. With `store: false`, a key is remembered in memory only, for 10 minutes. The same header works on TypeSafe's routes, and the official TypeSafe SDKs' own retries are recognised and folded together in History, so each logical call is counted once.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s -i http://127.0.0.1:8420/v1/studio/decisions \
  -H 'Idempotency-Key: ticket-T-5130-triage' -d '{
  "template": "support-triage",
  "variables": {"customer_message": "Our invoice shows 60 seats but we only have 45 users.",
                "account_tier": "pro"},
  "metadata": {"ticket_id": "T-5130"}
}'
```
@@ Output
```text
first call:
HTTP/1.1 200 OK
x-basal-decision-id: dec_01M3TFC6BVRP2147MD0X1P5RMM

the same call again:
HTTP/1.1 200 OK
x-basal-decision-id: dec_01M3TFC6BVRP2147MD0X1P5RMM
x-basal-idempotent-replayed: true

the same key with a different body:
HTTP/1.1 409 Conflict
{"error": {"type": "conflict_error", "code": "idempotency_key_reused",
           "message": "This Idempotency-Key was already used with a different request. Use a new key for each logical call.",
           "param": "Idempotency-Key", ...}}
```
:::

## Slow starts: background and waiting

Most decisions take milliseconds, but the first call to a model that is not loaded yet loads it first. That takes from a few seconds for a small model to several minutes for a large one, and the request waits up to 15 minutes. A client with a short timeout gives up before the answer arrives.

There are two ways to cope:

- **Load models ahead of time,** from the app or from your deployment script, and give your client a generous timeout. The examples above wait 120 seconds.
- **Run the decision in the background.** Send `"background": true` and the studio answers at once with `status: "queued"`, the decision's `id` and a `Location` header. Then fetch the decision with `?wait=60`, which holds the request open until the decision finishes or 60 seconds pass, whichever comes first. Repeat until `status` is `completed` or `failed`.

A background decision must be stored so that you can fetch it; with `store: false` it is refused with `400 background_requires_store`. A queued decision can be cancelled with `POST /v1/studio/decisions/{id}/cancel` until it starts. If the studio stops while a background decision is queued or running, the decision is marked `failed` with the code `interrupted` when the studio starts again; it is never run twice.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_message": "Please add two more seats to our plan.", "account_tier": "pro"},
  "background": true
}'

curl -s 'http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TFCCP5NWDT2WSQ8P7DHXKZ?wait=60'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=90)

d = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_message": "Please add two more seats to our plan.", "account_tier": "pro"},
    "background": True,
}).raise_for_status().json()

while d["status"] in ("queued", "in_progress"):
    d = studio.get(f"/decisions/{d['id']}", params={"wait": 60}).raise_for_status().json()
```
@@ Response 200
```json
{
  "id": "dec_01M3TFCCP5NWDT2WSQ8P7DHXKZ",
  "object": "decision",
  "status": "queued",
  "created_at": 1790816170,
  "completed_at": null,
  "template": {"id": "support-triage", "version": 2, "ref": "support-triage",
               "resolved_from": "latest", "attribution": "explicit"},
  "model": "laya",
  "answers": null,
  "act": null,
  "needs_review": [],
  "store": "full",
  "error": null
}
```
:::

## Choosing what History keeps

Every call is recorded in History by default. Three request options control what is kept:

| Option | Effect |
|---|---|
| `"store": false` | Nothing is written. The response still has an `id` for your logs. |
| `"store": "answers_only"` | The answers, settings and metadata are kept; the situation is not. |
| `metadata` | Up to 16 keys of your own, stored with the decision and never sent to the model. Use ids, such as a ticket number, and filter History by them with `metadata.ticket_id=T-5130`. |

Clients that cannot change the body can send the headers `X-Basal-Store: 0` and `X-Basal-Metadata: ticket_id=T-5130; channel=email` instead. Template variables marked `sensitive` are never written at all. [History and privacy](/docs/concepts/history) covers every switch, retention and erasure.

## Checking a request without running it

`POST /v1/studio/decisions/preview` takes the same body as a decision and runs every check: the template version it resolves to, the variables, the rendered situation, the effective questions, the model and the settings with their sources. It runs no model and stores nothing. Use it in tests and in CI to catch a broken template or a renamed variable before production does.

## Where to go next

- [Decisions](/docs/api/decisions): every field and option of a decision.
- [Templates](/docs/api/templates): create and version templates from a script.
- [Let an agent choose its next action](/docs/guides/agents): a decision inside an agent loop.
