---
title: Files, models and settings
description: Upload images, audio and video for decisions, list the models the studio knows, and read or change history and decision settings.
lead: The smaller resources around decisions. Files hold the images, audio and video a model reads, models tell you what each one can do and whether it is loaded, and settings control what history keeps and for how long.
---

All three live under `/v1/studio`: files at `/files`, models at `/models` and settings at `/settings`. Files and settings can be changed. Models are read-only here; you download, load and eject them on the app's Models page.

## Upload a file

::endpoint POST /v1/studio/files

Uploads an image, audio or video file once, so decisions can refer to it by id instead of sending the bytes each time. Send it as a multipart form field named `file`, or as the raw request body with its `Content-Type` and an `X-Filename` header (or `?name=`). Files can be up to 200 MB.

| Type | File types |
|---|---|
| `image` | PNG, JPEG, WebP, GIF, BMP |
| `audio` | WAV, MP3, M4A, Ogg, FLAC, WebM |
| `video` | MP4, MOV, WebM, MKV, AVI |

The same bytes are stored once however often they are uploaded, but each upload gets its own id. An upload that no decision uses is removed after a day (`expires_at`); one that decisions use is kept as long as they are.

:::console POST /v1/studio/files
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/files -F "file=@receipt.png"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
with open("receipt.png", "rb") as f:
    up = studio.post("/files", files={"file": ("receipt.png", f, "image/png")}).raise_for_status().json()
print(up["id"])
```
@@ JavaScript
```js
import { readFile } from "node:fs/promises";

const form = new FormData();
form.append("file", new Blob([await readFile("receipt.png")], { type: "image/png" }), "receipt.png");
const up = await (await fetch("http://127.0.0.1:8420/v1/studio/files", { method: "POST", body: form })).json();
console.log(up.id);
```
@@ Response 200
```json
{
  "id": "file_01M3TEEA4ZRXGX82WX1N2D8PSM",
  "object": "file",
  "purpose": "media",
  "type": "image",
  "content_type": "image/png",
  "name": "receipt.png",
  "bytes": 41859,
  "sha256": "sha256:a8eb2c54134a523ff99076153fa44a371a6ebc58f4344e44c528854b2c7a7d27",
  "created_at": 1790815185,
  "expires_at": 1790901585,
  "available": true
}
```
:::

## Use files in a decision

A decision's `media` is a list of items, each naming one file:

| Field | Type | Description |
|---|---|---|
| `file_id` | string | An id from [Upload a file](#upload-a-file). |
| `data` | string | Or the bytes inline, as a data URL: `data:image/png;base64,...`. |
| `type` | string | `image`, `audio` or `video`. Optional: it is read from the file. |
| `name` | string | Optional, shown in history. |

The model must be able to read the media: a text-only model answers `400 model_incompatible` before anything runs, and [List models](#list-models) shows each model's `modalities`. A template accepts only the media types in its `modalities`. Media sent inline is stored like an upload when the decision is stored in full and the `store_media` setting is on; otherwise the model reads it and nothing keeps it.

:::console POST /v1/studio/decisions/preview
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/preview -d '{
  "model": "jev-omni",
  "state": "Expense claim from Dana, team offsite, claimed amount 84.20 EUR.",
  "questions": {"matches_claim": {"type": "noul",
                                  "instructions": "Does the receipt show the claimed amount?"}},
  "media": [{"type": "image", "file_id": "file_01M3TEEA4ZRXGX82WX1N2D8PSM"}]
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
p = studio.post("/decisions/preview", json={
    "model": "jev-omni",
    "state": "Expense claim from Dana, team offsite, claimed amount 84.20 EUR.",
    "questions": {"matches_claim": {"type": "noul",
                                    "instructions": "Does the receipt show the claimed amount?"}},
    "media": [{"type": "image", "file_id": "file_01M3TEEA4ZRXGX82WX1N2D8PSM"}],
}).raise_for_status().json()
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions/preview", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "jev-omni",
    state: "Expense claim from Dana, team offsite, claimed amount 84.20 EUR.",
    questions: { matches_claim: { type: "noul", instructions: "Does the receipt show the claimed amount?" } },
    media: [{ type: "image", file_id: "file_01M3TEEA4ZRXGX82WX1N2D8PSM" }],
  }),
});
const p = await res.json();
```
@@ Response 200
```json
{
  "object": "decision.preview",
  "model": "jev-omni",
  "state": "Expense claim from Dana, team offsite, claimed amount 84.20 EUR.",
  "questions": {
    "matches_claim": {
      "type": "noul",
      "instructions": "Does the receipt show the claimed amount?"
    }
  },
  "media": [
    {
      "type": "image",
      "file_id": "file_01M3TEEA4ZRXGX82WX1N2D8PSM"
    }
  ],
  "settings": {
    "act_threshold": 0.9,
    "temperature": 1.0,
    "questions": {},
    "sources": {
      "act_threshold": "studio",
      "temperature": "studio"
    }
  },
  "store": "full",
  "warnings": []
}
```
:::

The example uses a [preview](/docs/api/decisions#preview-a-decision), which checks the request against Jev-Omni without loading it; the response is shortened. The same body sent to `POST /v1/studio/decisions` runs it. With `"model": "laya"` instead, the request is refused with `model_incompatible`: "Laya cannot read image input."

## Retrieve a file

::endpoint GET /v1/studio/files/{id}

::endpoint GET /v1/studio/files/{id}/content

The first returns the file's details, as in the upload response; `available` is `false` once the bytes are no longer kept. The second returns the bytes. Images, audio and video are served inline; everything is sent with headers that stop a stored file from running as a page on the studio's address (`content-security-policy: sandbox` and `x-content-type-options: nosniff`).

:::console GET /v1/studio/files/{id}/content
@@ curl
```bash
curl -s -o receipt.png http://127.0.0.1:8420/v1/studio/files/file_01M3TEEA4ZRXGX82WX1N2D8PSM/content
```
@@ Python
```python
import httpx

r = httpx.get("http://127.0.0.1:8420/v1/studio/files/file_01M3TEEA4ZRXGX82WX1N2D8PSM/content")
open("receipt.png", "wb").write(r.content)
```
@@ JavaScript
```js
import { writeFile } from "node:fs/promises";

const res = await fetch("http://127.0.0.1:8420/v1/studio/files/file_01M3TEEA4ZRXGX82WX1N2D8PSM/content");
await writeFile("receipt.png", Buffer.from(await res.arrayBuffer()));
```
@@ Output
```http
HTTP/1.1 200 OK
x-content-type-options: nosniff
content-security-policy: sandbox; default-src 'none'
content-disposition: inline; filename="receipt.png"
cache-control: private, max-age=3600
content-type: image/png
content-length: 41859
```
:::

## Delete a file

::endpoint DELETE /v1/studio/files/{id}

Deletes a file and its bytes at once, even when decisions used it. Privacy comes first: those decisions keep the file's type, size and hash, and show the media as no longer available. They can no longer be rerun.

:::console DELETE /v1/studio/files/{id}
@@ curl
```bash
curl -s -X DELETE http://127.0.0.1:8420/v1/studio/files/file_01M3TEEKDBM934CWKYFDB71FVY
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
studio.delete("/files/file_01M3TEEKDBM934CWKYFDB71FVY").raise_for_status()
```
@@ JavaScript
```js
await fetch("http://127.0.0.1:8420/v1/studio/files/file_01M3TEEKDBM934CWKYFDB71FVY", { method: "DELETE" });
```
@@ Response 200
```json
{
  "id": "file_01M3TEEKDBM934CWKYFDB71FVY",
  "object": "file.deleted",
  "deleted": true
}
```
:::

## List models

::endpoint GET /v1/studio/models

::endpoint GET /v1/studio/models/{id}

Lists every model the studio knows, downloaded or not, with what it can do. `GET /models/{id}` returns one; it accepts the studio id, the Hugging Face repository id or the display name.

| Field | Description |
|---|---|
| `id`, `name`, `maker` | The id to send as `model`, and who made it. |
| `modalities` | What it reads: `text`, and for some models `image`, `audio`, `video`. |
| `types` | The question types the model answers natively. The studio builds `multi`, `rank` and `number` questions from these, so all six types work on every model. |
| `max_options`, `max_questions` | The most options per question and questions per decision it takes. |
| `context_tokens` | How much input it reads; longer input is cut off at the end. |
| `status` | `loaded`, `loading`, `downloaded` or `not_downloaded`. A decision naming a downloaded model loads it first. |
| `revision` | Always `null` in 0.2.1. |

Loading, ejecting and downloading models are done in the app's Models page. `GET /v1/models` lists the same models in [TypeSafe's format](/docs/api/systemone#list-models-in-typesafes-format).

:::console GET /v1/studio/models
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/models
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
for m in studio.get("/models").json()["data"]:
    print(m["id"], m["status"], m["modalities"], m["context_tokens"])
```
@@ JavaScript
```js
const list = await (await fetch("http://127.0.0.1:8420/v1/studio/models")).json();
for (const m of list.data) console.log(m.id, m.status, m.modalities, m.context_tokens);
```
@@ Response 200
```json
{
  "object": "list",
  "data": [
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
  ],
  "first_id": "julia-1",
  "last_id": "jev-omni",
  "has_more": false
}
```
:::

The response is shortened to one of the eleven models.

## Read the settings

::endpoint GET /v1/studio/settings

::endpoint PATCH /v1/studio/settings

`GET` returns the history and decision settings, and how much space history uses. `PATCH` changes any of the settings below; send only the ones you want to change, grouped as in the response. `storage` is read-only.

| Setting | Default | Description |
|---|---|---|
| `history.store` | `full` | The most any decision keeps: `full`, `answers_only` or `none`. Requests and templates can only keep less. |
| `history.retention_days` | `30` | Days to keep decisions. `0` keeps them forever. Templates can set their own. |
| `history.max_storage_gb` | `20` | When history grows past this, the oldest decisions that retention may remove go first. |
| `history.store_media` | `true` | Keep the images, audio and video of decisions stored in full. |
| `history.keep_labelled` | `true` | Never remove decisions that have feedback. |
| `history.on_store_error` | `serve` | When a decision cannot be saved: `serve` returns the answer anyway, with a warning; `fail` refuses it with `503 store_failed`. |
| `history.notice_acknowledged_at` | `null` | When the app's first notice about history was dismissed. `true` sets it to now. |
| `decisions.default_act_threshold` | `0.9` | The act threshold when neither the request nor the template sets one. |

Changing retention, the size cap or `keep_labelled` starts a cleanup at once; otherwise it runs a minute after the studio starts and then every hour. `storage` reports `db_bytes` and `blob_bytes`, the number of `decisions` and the oldest one, the last cleanup, how many decisions are past retention (`pending_deletion`), whether full-text search is available, whether history is `read_only` (a database written by a newer studio), and the last `store_errors`.

:::console PATCH /v1/studio/settings
@@ curl
```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/settings -d '{"history": {"retention_days": 90}}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
s = studio.patch("/settings", json={"history": {"retention_days": 90}}).raise_for_status().json()
print(s["history"]["retention_days"], s["storage"]["pending_deletion"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/settings", {
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ history: { retention_days: 90 } }),
});
const s = await res.json();
```
@@ Response 200
```json
{
  "object": "settings",
  "history": {
    "store": "full",
    "retention_days": 90,
    "max_storage_gb": 20,
    "store_media": true,
    "keep_labelled": true,
    "on_store_error": "serve",
    "notice_acknowledged_at": null
  },
  "decisions": {"default_act_threshold": 0.9},
  "storage": {
    "db_bytes": 3993960,
    "blob_bytes": 41859,
    "decisions": 24,
    "oldest_at": 1790814975,
    "last_sweep_at": 1790814768,
    "last_sweep_deleted": 0,
    "pending_deletion": 0,
    "search_available": true,
    "read_only": false
  }
}
```
:::

The response is shortened: `storage` also lists `store_errors`. A setting the studio does not know answers `400 invalid_field` naming it, such as `history.retention`.
