---
title: Training and trained models
description: Upload training examples, start, follow, pause and cancel a training, and list, export, import and delete trained models, from your own scripts.
lead: The Train page is built on these endpoints, so a script can do everything it does. Training endpoints live under `/api/training` and trained models under `/api/finetunes`; a trained model is then called like any other model.
---

These are the studio's management endpoints, like loading and ejecting models. Requests that change something need the header `X-Basal-Client: 1`, which keeps other websites from using the studio through your browser. Training needs a supported GPU; on other computers the endpoints answer, and `capabilities` says why training is off.

## Check what this computer can train

::endpoint GET /api/training/capabilities

Whether training is available here, on which device, and which models could be trained. `experimental` is true for a GPU that trains only after **Allow experimental training** is turned on; `reason` says why when `enabled` is false.

:::console GET /api/training/capabilities
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/training/capabilities
```
@@ Python
```python
import httpx

caps = httpx.get("http://127.0.0.1:8420/api/training/capabilities").json()
print(caps["enabled"], caps["device"]["name"])
```
@@ Response 200
```json
{
  "enabled": true,
  "reason": "",
  "device": {"name": "NVIDIA GB10", "kind": "cuda", "tier": "on", "unified": true, "available_gb": 8.1},
  "experimental": false,
  "models": [
    {"id": "laya", "name": "Laya", "params": "421M", "trainable": true, "reason": "", "train_gb": 6, "score": 60,
     "tagline": "The most popular open decision model.", "eta_minutes": null},
    {"id": "laya-multilingual", "name": "Laya Multilingual", "params": "322M", "trainable": true, "reason": "",
     "train_gb": 6, "score": 44, "tagline": "Decisions in 100+ languages.", "eta_minutes": null}
  ]
}
```
:::

`models` lists every model, shortened here to two. `score` orders the recommendation; `train_gb` is the memory training takes; `eta_minutes` is filled in once there are examples to estimate from.

## Upload examples

::endpoint POST /api/training/datasets

Send a file of examples as the multipart field `file`, in any format the Train page reads: CSV, tab-separated text, JSON lines or the [standard training format](/docs/guides/teach#the-standard-format), up to 200 MB. Optionally send `questions`, a JSON object of question definitions keyed like the answers, to set the wording or the options yourself.

The response is the import report: the questions found, how often each answer appears, any problems, a preview, and every model with whether it can learn these examples and how long it would take.

:::console POST /api/training/datasets
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/training/datasets -H 'X-Basal-Client: 1' -F "file=@support-tickets.csv"
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420", headers={"X-Basal-Client": "1"}, timeout=120)
with open("support-tickets.csv", "rb") as f:
    ds = studio.post("/api/training/datasets", files={"file": ("support-tickets.csv", f, "text/csv")}).json()
print(ds["report"]["usable"], ds["recommended"]["id"])
```
@@ Response 200
```json
{
  "id": "d1a37ed2c0e0",
  "filename": "support-tickets.csv",
  "questions": {
    "team": {"type": "choice", "instructions": "Which team fits best?",
             "criteria": {"identity": null, "payments": null, "devices": null, "network": null, "data": null, "people": null}},
    "urgent": {"type": "noul", "instructions": "Is this urgent?"}
  },
  "report": {
    "format": "table",
    "detected": "420 examples. The text is in 'ticket'; 'team' is a choice between 6 answers; 'urgent' is a yes/no question.",
    "examples": 420,
    "labelled_answers": 840,
    "questions": {
      "team": {"type": "choice", "instructions": "Which team fits best?",
               "options": ["identity", "payments", "devices", "network", "data", "people"],
               "counts": {"devices": 72, "payments": 77, "identity": 79, "data": 62, "network": 71, "people": 59}, "labelled": 420},
      "urgent": {"type": "noul", "instructions": "Is this urgent?", "options": ["no", "yes"],
                 "counts": {"no": 277, "yes": 143}, "labelled": 420}
    },
    "usable": true,
    "problems": []
  },
  "media": [],
  "non_english": false,
  "max_options": 6,
  "types": ["choice", "noul"],
  "preview": [
    {"state": "Hi team, the Harbor dock: the screen flickers and goes black. I found a workaround for now. Appreciate it.",
     "answers": {"team": "devices", "urgent": "no"}}
  ],
  "recommended": {"id": "laya", "name": "Laya", "params": "421M", "trainable": true, "reason": "", "train_gb": 6,
                  "score": 60, "tagline": "The most popular open decision model.", "eta_minutes": 14},
  "models": [
    {"id": "laya-multilingual", "name": "Laya Multilingual", "params": "322M", "trainable": true, "reason": "",
     "train_gb": 6, "score": 44, "tagline": "Decisions in 100+ languages.", "eta_minutes": 14}
  ]
}
```
:::

`preview` and `models` are shortened here. A problem in `report.problems` has a `level` (`error` stops training, `warning` and `info` don't), a plain `message`, and the line numbers it applies to. `GET /api/training/datasets/{id}` returns the same report again, and `POST /api/training/datasets/{id}/questions` with `{"questions": {...}}` rewords questions and returns the new report.

## Start a training

::endpoint POST /api/training/jobs

| Field | | Meaning |
|---|---|---|
| `dataset_id` | required | from the upload |
| `model_id` | optional | the model to train; the recommended one when left out |
| `name` | optional | the trained model's name in the model list |

The training starts in its own process and the call returns at once. One training runs at a time on a computer; a second one is accepted and waits its turn (`state` `waiting`).

:::console POST /api/training/jobs
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/training/jobs -H 'X-Basal-Client: 1' -H 'content-type: application/json' \
  -d '{"dataset_id": "d1a37ed2c0e0", "model_id": "julia-1", "name": "Julia 1 for support tickets"}'
```
@@ Python
```python
job = studio.post("/api/training/jobs", json={"dataset_id": ds["id"], "model_id": "julia-1",
                                               "name": "Julia 1 for support tickets"}).json()
```
@@ Response 200
```json
{"id": "20261001-171252-44f71d", "model_id": "julia-1", "name": "Julia 1 for support tickets",
 "dataset_id": "d1a37ed2c0e0", "created": 1790854972.9147606, "state": "queued", "updated": 1790854972.9149415,
 "text": "Starting"}
```
:::

A model that can't learn these examples here is refused with `409` and the reason, such as *Download this model first (Models page).*

## Follow a training

::endpoint GET /api/training/jobs/{id}

| Field | Meaning |
|---|---|
| `state` | `queued`, `waiting`, `running`, `paused`, `done`, `cancelled` or `failed` |
| `stage`, `text` | what it is doing, in words: `check`, `data`, `load`, `baseline`, `train`, `check_result`, `save`, `verify` |
| `progress` | 0 to 1 |
| `step`, `total_steps`, `eta_seconds` | while training |
| `curve` | the practice score on examples it isn't learning from, at each check |
| `baseline_accuracy` | the original model's score on the test examples |
| `notes` | the latest notes, such as a switch to smaller batches |
| `result` | once done: `outcome` (`improved`, `not_improved`, `forgot`, `collapsed`, `parity_failed`), `accepted`, `message`, accuracy before and after, general accuracy before and after, `finetune_id`, the example it used to get wrong (`showcase`) and the held-out examples in Evaluate's format (`evaluate`) |

:::console GET /api/training/jobs/{id}
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/training/jobs/20261001-171252-44f71d
```
@@ Python
```python
import time

while True:
    j = studio.get(f"/api/training/jobs/{job['id']}").json()
    if j["state"] not in ("queued", "waiting", "running"):
        break
    print(f"{j.get('text')}: {round(100 * (j.get('progress') or 0))}%")
    time.sleep(10)
print(j["result"]["message"] if j.get("result") else j.get("message"))
```
@@ Response 200
```json
{
  "id": "20261001-171252-44f71d",
  "model_id": "julia-1",
  "name": "Julia 1 for support tickets",
  "dataset_id": "d1a37ed2c0e0",
  "created": 1790854972.9147606,
  "state": "running",
  "updated": 1790855017.2827582,
  "pid": 2033992,
  "stage": "train",
  "text": "Learning from your examples (reading them up to 7 times)",
  "progress": 0.20254980079681276,
  "curve": [{"step": 31, "accuracy": 0.6746}],
  "notes": [],
  "step": 33,
  "total_steps": 251,
  "eta_seconds": 163,
  "baseline_accuracy": 0.5158730158730159
}
```
:::

`GET /api/training/jobs` lists every training, newest first, in the same form without `curve` and `notes`.

## Pause, continue, cancel or delete

::endpoint POST /api/training/jobs/{id}/{action}

`action` is `pause` (stops after the current step, keeping what it learned), `resume` (starts a paused, cancelled or failed training again, from where it stopped when it was paused or cancelled mid-training), `cancel` (stops and keeps nothing) or `delete` (stops it if it runs and removes its folder). The response is the training's current state; a pause or cancel takes effect within seconds, after which its `state` is `paused` or `cancelled`.

:::console POST /api/training/jobs/{id}/cancel
@@ curl
```bash
curl -s -X POST -H 'X-Basal-Client: 1' http://127.0.0.1:8420/api/training/jobs/20261001-171252-44f71d/cancel
```
@@ Response 200
```json
{"id": "20261001-171252-44f71d", "model_id": "julia-1", "name": "Julia 1 for support tickets",
 "dataset_id": "d1a37ed2c0e0", "created": 1790854972.9147606, "state": "running", "updated": 1790855017.2827582,
 "pid": 2033992, "stage": "train", "text": "Learning from your examples (reading them up to 7 times)",
 "progress": 0.20254980079681276}
```
:::

## List trained models

::endpoint GET /api/finetunes

Every trained model on this computer, with its record: the model it was trained from, the LoRA settings, the fitted temperature, accuracy before and after on held-out examples and on general questions, the size of each part of the data, and the result of the saved-model check. The same models appear in `GET /v1/studio/models` and `/v1/models` under their ids.

:::console GET /api/finetunes
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/finetunes
```
@@ Response 200
```json
{"finetunes": [{
  "id": "laya-ft-20261001-165732",
  "name": "Laya for support tickets",
  "base_model": "laya",
  "family": "laya",
  "created": "2026-10-01T16:57:33",
  "method": "lora",
  "lora": {"r": 16, "alpha": 32, "targets": ["Wqkv", "Wo", "Wi"]},
  "temperature": {"all": 1.081},
  "accuracy_before": 0.7619047619047619,
  "accuracy_after": 1.0,
  "guard_before": 0.48651452282157676,
  "guard_after": 0.49066390041493774,
  "data": {"train": 588, "calibration": 126, "test": 126, "skipped": 0, "replay": 1099, "guard": 964},
  "questions": ["team", "urgent"],
  "dataset_sha1": "5bd68993a593e597426e9918e2a852c14b4af31c",
  "parity": {"ok": true, "max_difference": 4.8e-05, "questions": 24}
}]}
```
:::

Each entry also carries `example` (the request **Use it now** opens), `versions` and `device`, left out here. `DELETE /api/finetunes/{id}` deletes one and answers `{"deleted": "<id>"}`; the model it was trained from is untouched.

## Export and import

::endpoint GET /api/finetunes/{id}/export

The trained model as one .zip: `manifest.json`, `lora.json`, `lora.safetensors` and, when its head was trained, `head.safetensors`. Local paths are left out. The model it was trained from isn't included; it downloads on the other computer like any other model.

:::console GET /api/finetunes/{id}/export
@@ curl
```bash
curl -s -OJ http://127.0.0.1:8420/api/finetunes/laya-ft-20261001-165732/export
```
@@ Output
```text
content-type: application/zip
content-disposition: attachment; filename="Laya-for-support-tickets.zip"
content-length: 28813273
```
:::

::endpoint POST /api/finetunes/import

Adds a .zip exported from any studio, sent as the multipart field `file`. The studio accepts only those four files, checks that each tensor file is one, and refuses a model trained from one this studio doesn't have (`422`, with the reason). An id already in use gets a new one.

:::console POST /api/finetunes/import
@@ curl
```bash
curl -s http://127.0.0.1:8420/api/finetunes/import -H 'X-Basal-Client: 1' -F "file=@Laya-for-support-tickets.zip"
```
@@ Response 200
```json
{"id": "laya-ft-20261001-171421", "name": "Laya for support tickets", "base_model": "laya",
 "imported": "2026-10-01T17:14:21"}
```
:::

The full response is the imported model's record, as in the list above, shortened here.

## Use a trained model

A trained model is a model: name its id in `"model"` in any decision format, or make it a template's default model. See [Teach a model your own decisions](/docs/guides/teach#use-it-from-code).
