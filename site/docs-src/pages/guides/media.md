---
title: Decide about images, audio and video
description: Ask Bud Decision Studio's image, audio and video models about a photo, a recording or a clip; upload files, send them inline, use media variables in templates, and control what History keeps.
lead: Two of the eleven models can look at images, and one can also listen to audio and watch video. You attach the file beside the situation and ask your questions as usual; the answers come back in the same shape as for text.
---

## Which models read what

| Model | Images | Audio | Video | Notes |
|---|---|---|---|---|
| Intern-Decision 4B | yes | no | no | 10 GB of memory; runs on the GPU or the processor |
| Jev-Omni | yes | yes | yes | 26 GB of memory; needs a GPU |
| Every other model | no | no | no | Text only |

A request with media for a model that cannot read it is refused before anything runs, with `400 model_incompatible` and a `modality_not_supported` detail. The **Images, audio and video** examples in the Playground (a receipt photo and a chart) are set up for these two models.

Accepted files, up to 200 MB each:

| Kind | Formats |
|---|---|
| Image | PNG, JPEG, WebP, GIF, BMP |
| Audio | WAV, MP3, M4A (MP4 audio), Ogg, FLAC, WebM |
| Video | MP4, QuickTime (MOV), WebM, Matroska (MKV), AVI |

## Upload a file

Upload the file once, then refer to it by its id. `POST /v1/studio/files` takes a multipart form field named `file`, or the raw bytes with the file name in the `X-Filename` header.

The response names the file `file_…`, its kind, size and a SHA-256 hash of its content. An uploaded file that no decision or test example uses is removed after 24 hours (`expires_at`); once a decision uses it, it is kept as long as that decision.

:::console POST /v1/studio/files
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/files -F file=@receipt.png
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)

with open("receipt.png", "rb") as f:
    file = studio.post("/files", files={"file": ("receipt.png", f, "image/png")}).raise_for_status().json()
print(file["id"])
```
@@ Response 200
```json
{
  "id": "file_01M3TF8QS1XJT4SYYEDJRPDZ7A",
  "object": "file",
  "purpose": "media",
  "type": "image",
  "content_type": "image/png",
  "name": "receipt.png",
  "bytes": 41859,
  "sha256": "sha256:a8eb2c54134a523ff99076153fa44a371a6ebc58f4344e44c528854b2c7a7d27",
  "created_at": 1790816050,
  "expires_at": 1790902450,
  "available": true
}
```
:::

## Ask about it

Attach the file in `media` as `{"type": "image", "file_id": "file_…"}`, next to a short situation that says what the image is for. The questions are ordinary questions.

The photo here is the sample receipt from the Playground's **Check a receipt photo** example. Intern-Decision 4B recognises it as a till receipt (98%) and finds the olive oil on it (93%). The legibility rating, 84% "readable", is below the default act threshold of 90%, so that one answer asks a person.

The stored decision keeps the file, so History can show the image beside the answers.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "model": "intern-decision-4b",
  "state": "A customer sent this photo as proof of purchase for a refund on olive oil.",
  "questions": {
    "doc_type": {"type": "choice", "instructions": "What does the image show?",
                 "criteria": {"receipt": "a till receipt from a shop", "invoice": "a business invoice",
                              "product_photo": "a photo of a product", "other": null}},
    "shows_item": {"type": "noul", "instructions": "Does the receipt list olive oil?"},
    "legible": {"type": "score", "instructions": "How legible is the document?",
                "criteria": ["unreadable", "hard to read", "readable", "perfectly clear"]}
  },
  "media": [{"type": "image", "file_id": "file_01M3TF8QS1XJT4SYYEDJRPDZ7A"}]
}'
```
@@ Response 200
```json
{
  "id": "dec_01M3TF8QVWX5SPDTGQ32TX4XDH",
  "object": "decision",
  "status": "completed",
  "model": "intern-decision-4b",
  "answers": {
    "doc_type": {"type": "choice", "choice": "receipt",
                 "probabilities": {"receipt": 0.9782, "invoice": 0.0061, "product_photo": 0.0069, "other": 0.0088},
                 "certainty": 0.9782, "act": true},
    "shows_item": {"type": "noul", "noul": 0.9331, "probabilities": {"false": 0.0669, "true": 0.9331},
                   "decision": "yes", "certainty": 0.9331, "act": true},
    "legible": {"type": "score", "decision": "2",
                "probabilities": {"0": 0.0125, "1": 0.022, "2": 0.8379, "3": 0.1276},
                "certainty": 0.8379, "act": false}
  },
  "act": false,
  "needs_review": ["legible"]
}
```
:::

## Send it inline

For a one-off decision you can skip the upload and put the file in the request as a `data:` URL: `"data": "data:image/png;base64,…"`. Inline files work on every route, including TypeSafe's `/v1/systemone`, which has no file upload of its own.

The response from `/v1/systemone` is TypeSafe's plain shape, and the decision is stored in History all the same; the `x-basal-decision-id` header names it.

:::console POST /v1/systemone
@@ Python
```python
import base64
import httpx

with open("receipt.png", "rb") as f:
    receipt = "data:image/png;base64," + base64.b64encode(f.read()).decode()

r = httpx.post("http://127.0.0.1:8420/v1/systemone", timeout=120, json={
    "model": "intern-decision-4b",
    "state": "A customer sent this photo as proof of purchase for a refund on olive oil.",
    "questions": {"shows_item": {"type": "noul", "instructions": "Does the receipt list olive oil?"}},
    "media": [{"type": "image", "data": receipt, "name": "receipt.png"}],
})
print(r.headers["x-basal-decision-id"], r.json())
```
@@ Response 200
```json
{
  "model": "intern-decision-4b",
  "answers": {"shows_item": {"type": "noul", "noul": 0.9692}},
  "usage": {"input_tokens": 592, "output_tokens": 0}
}
```
:::

## In a template

A template takes a file through a variable of type `image`, `audio` or `video`, and lists that kind in `modalities`. Callers send the file id (or a `data:` URL) as the variable's value; the file is attached in the order the variables are declared, before any `media` in the request. A media variable can never appear inside text.

In the app, a template with a media variable shows a drop zone for it in the Playground's form.

The template on the right checks proof of purchase for any item. The decision used the same receipt and asked about olive oil.

:::console PUT /v1/studio/templates/{id}
@@ curl
```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/refund-proof -d '{
  "name": "Refund proof check",
  "variables": {
    "item": {"type": "string", "description": "The product the refund is for."},
    "receipt": {"type": "image", "description": "The photo the customer uploaded."}
  },
  "modalities": ["text", "image"],
  "state": "A customer sent this photo as proof of purchase for a refund on {{item}}.",
  "questions": {
    "doc_type": {"type": "choice", "instructions": "What does the image show?",
                 "criteria": {"receipt": "a till receipt from a shop", "invoice": "a business invoice",
                              "product_photo": "a photo of a product", "other": null}},
    "shows_item": {"type": "noul", "instructions": "Does the receipt list the item the refund is for?"}
  },
  "model": "intern-decision-4b",
  "settings": {"act_threshold": 0.9}
}'

curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "refund-proof",
  "variables": {"item": "olive oil", "receipt": "file_01M3TF8QS1XJT4SYYEDJRPDZ7A"}
}'
```
@@ Response 200
```json
{
  "id": "dec_01M3TF9E7GVCF7A6Y7CFK3T9FJ",
  "template": {"id": "refund-proof", "version": 1, "ref": "refund-proof",
               "resolved_from": "latest", "attribution": "explicit"},
  "model": "intern-decision-4b",
  "answers": {
    "doc_type": {"type": "choice", "choice": "receipt", "certainty": 0.984, "act": true},
    "shows_item": {"type": "noul", "noul": 0.8605, "decision": "yes", "certainty": 0.8605, "act": false}
  },
  "act": false,
  "needs_review": ["shows_item"]
}
```
:::

## Audio and video

Audio and video work the same way, with `"type": "audio"` or `"type": "video"` and Jev-Omni as the model. Jev-Omni needs a GPU with about 26 GB of free memory; load it from the Models page first, since loading takes a while.

| | Request |
|---|---|
| A recorded call | `"media": [{"type": "audio", "file_id": "file_…"}]` with questions such as "Does the caller ask to cancel?" |
| A short clip | `"media": [{"type": "video", "file_id": "file_…"}]` with questions such as "Does the video show the product damaged?" |
| In a template | A variable `{"type": "audio"}` or `{"type": "video"}`, and `"modalities": ["text", "audio"]` or `["text", "video"]` |

## What History keeps of a file

Files are stored once by their content, so the same photo sent twenty times takes the space of one.

| Setting | What happens to the file |
|---|---|
| `store: "full"` (the default) | Kept with the decision, shown in History, and reusable for a rerun |
| `store: "answers_only"` | Not kept. History records its kind, size and hash, but not its name or content |
| `store: false` | Nothing is kept; an inline file is deleted as soon as the model has read it |
| `store_media` off in the studio's settings | Contents are discarded right after each decision; History shows "file not kept" |

:::note A sensitive file is never kept
Mark an `image`, `audio` or `video` variable `sensitive` and the model reads the file while History keeps nothing of it: no contents, no file name, and a keyed hash where the content hash would be. Rerunning such a decision needs the file sent again. Send private files inline, as a data URL: a file you uploaded yourself with `POST /v1/studio/files` follows the rule for uploads and stays until it expires, a day after its last use.

Versions before 0.3.0 stored such a file with the decision. On those, call with `"store": "answers_only"` or turn off `store_media` for private files.
:::

`DELETE /v1/studio/files/{id}` removes a file's contents even when decisions used it; those decisions keep their answers and can no longer be rerun.
