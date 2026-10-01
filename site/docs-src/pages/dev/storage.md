---
title: Data and storage
description: Where Bud Decision Studio keeps its files, what studio.db holds, how migrations, backups and retention work, and how to back up or move your data.
lead: Everything the studio writes lives in one data folder on your computer, and most of it in one SQLite file. Model weights are kept apart, in the Hugging Face cache. This page lists what is where, how it changes between versions, and how to back it up.
---

## Where the data lives

The desktop app keeps everything in its application-data folder: the engine it installed (`engine-env/`), the studio's data (`data/`), and the server's log (`logs/studio.log`). A source checkout uses `./data` inside the checkout. Either way, `BASAL_DATA` points the server at another folder.

| Install | Data folder |
|---|---|
| Desktop app on Linux | `~/.local/share/ai.bud.decisionstudio/data` |
| Desktop app on macOS | `~/Library/Application Support/ai.bud.decisionstudio/data` |
| Desktop app on Windows | `%APPDATA%\ai.bud.decisionstudio\data` |
| Source checkout | `./data` |

Model weights stay in the standard Hugging Face cache, `~/.cache/huggingface/hub` unless `HF_HOME` says otherwise, and are shared with every other tool that uses it. Deleting the data folder never deletes a model; deleting a model on the Models page never touches your history.

:::console Terminal
@@ Linux
```bash
ls ~/.local/share/ai.bud.decisionstudio
ls ~/.local/share/ai.bud.decisionstudio/data
```
@@ macOS
```bash
ls ~/Library/Application\ Support/ai.bud.decisionstudio
ls ~/Library/Application\ Support/ai.bud.decisionstudio/data
```
@@ Windows
```powershell
dir $env:APPDATA\ai.bud.decisionstudio
dir $env:APPDATA\ai.bud.decisionstudio\data
```
@@ Output
```text
data  engine-env  logs  studio-port  ...

activity.jsonl.imported  blobs  config.json  hub_meta.json
logs  studio.db  studio.db-shm  studio.db-wal  uploads
```
:::

## What is in the data folder

| Path | What it holds |
|---|---|
| `studio.db` | History, templates, versions, test examples, feedback and settings. `studio.db-wal` and `studio.db-shm` belong to it while the studio runs |
| `backups/` | A copy of `studio.db` taken before each upgrade changes it; the newest three are kept |
| `blobs/sha256/` | Images, audio and video attached to decisions and examples, one file per distinct content |
| `secret` | A random key, created on first use, for the fingerprints of sensitive variables. Keep it with `studio.db` |
| `config.json` | Where models run: the device chosen during setup and the devices found |
| `settings.json` | Auto-load and idle eject (the System page) |
| `hub_meta.json` | Cached file lists, sizes and likes from Hugging Face, so the studio works offline |
| `download_queue.json` | Models waiting to download; a restart picks the queue up again |
| `logs/` | `worker-<model>.log` per loaded model, `download-<model>.log` per download, `conformance.log` |
| `conformance.json` | The result of the last conformance run started with `POST /api/conformance/run` |
| `uploads/` | Files from the older upload endpoint; anything older than a day is removed at startup |
| `activity.jsonl.imported` | The Activity log of versions before 0.2.0, already imported into history |

The desktop app's folder also has `engine-env/` (Python, PyTorch and the model libraries), `logs/studio.log` (the server's output) and `studio-port` (the port used last time, so the window keeps its saved state between launches).

## The database

`studio.db` is SQLite in WAL mode (write-ahead logging, which lets readers keep reading while a write goes on). The server holds one writer connection behind a lock and opens a read-only connection per thread. A decision is written in one transaction of a few milliseconds. Model workers never open the file. On Linux and macOS the file and its companions are readable by your user only.

Times are stored as Unix milliseconds; the API shows seconds. Every top-level row carries a `workspace_id`, always `ws_local` for now.

| Table | What it holds |
|---|---|
| `decisions` | One small row per decision for lists and filters: when, which template version and model, where it came from, act, timing, status |
| `decision_bodies` | What one decision's page shows: variables, situation, effective settings, answers and raw probabilities. Read only when a decision is opened |
| `decision_answers` | One row per answer, for answer filters and statistics |
| `decision_metadata`, `decision_media` | Your metadata keys; which files a decision used |
| `decision_secrets` | Fingerprints (HMACs) of sensitive variable values, so a value can be erased by value. Never the values |
| `question_sets` | Each distinct set of questions, stored once and shared by the decisions that asked it |
| `templates`, `template_versions` | Template heads (name, storage, retention) and their immutable, numbered versions |
| `template_questions`, `template_aliases` | Per-version question index for comparisons; aliases such as `production` |
| `examples`, `example_media` | Test examples. Editing one writes a new row, so earlier revisions stay reproducible |
| `feedback` | Labels: the right answer, a rating and a note per answer |
| `files`, `blobs` | Uploaded files (with random `file_...` ids) and the stored content they point to |
| `settings` | History and decision settings (`/v1/studio/settings`) |
| `usage_counters` | Content-free request counts per minute, including calls that were not stored |
| `idempotency_keys` | Responses kept for 24 hours so a retried request is answered once |
| `audit_events` | Template saves, alias moves, deletions, settings changes and sweeps |
| `decision_fts` | The full-text search index over stored situations, when SQLite has FTS5 |
| `schema_migrations` | Which migrations ran, with their checksums |

Triggers keep finished decisions and template versions immutable: a version is never edited, and a decision's answers never change after it completes. The tables `batches`, `eval_runs`, `api_keys` and their companions exist for features that are planned but not yet built.

:::console Terminal
@@ Migrations
```bash
sqlite3 -header -column "file:data/studio.db?mode=ro" \
  "SELECT version, name, substr(checksum, 1, 12) AS checksum FROM schema_migrations"
```
@@ Decisions
```bash
sqlite3 -header -column "file:data/studio.db?mode=ro" \
  "SELECT id, model, storage, endpoint FROM decisions ORDER BY seq DESC LIMIT 3"
```
@@ Output
```text
version  name                  checksum
-------  --------------------  ------------
1        0001_init             b2841a6da95a
2        0002_import_activity  0db299a886cf
3        0003_search           8da38b0a6448

id                              model         storage  endpoint
------------------------------  ------------  -------  ----------------------
dec_01M3TEFSYYAD3VKR5C5GW76YJY  fake-decider  full     /v1/systemone
dec_01M3TECGXF3EKF8Y2CN65S9HCD  fake-decider  full     /v1/evaluate
dec_01M3TECGSBDV6E6KH6037V43N2  fake-decider  full     /typesafe/v1/systemone
```
:::

Open the file read-only (`?mode=ro`) when the studio is running, and change it only through the API: the studio relies on its own transactions and triggers.

## Migrations and upgrades

The schema changes only through numbered files in `basal/migrations/`: `0001_init.sql` creates the tables, `0002_import_activity.py` imports the Activity log of earlier versions, and `0003_search.py` adds full-text search when SQLite supports it. At startup the studio applies the ones not yet recorded, in order, each in its own transaction, and records each with a SHA-256 checksum of its file (line endings ignored, so a Windows checkout matches).

Four rules keep upgrades safe:

- **A backup first.** Before applying new migrations to an existing database, the studio writes a copy to `backups/studio-m<version>-<time>.db` and keeps the newest three.
- **Released migrations never change.** If a migration file differs from the one that was applied, history refuses to open, with *Migration ... changed after it was applied ... Restore the original file; migrations are never edited once released.* Decisions keep working, unsaved.
- **A newer database opens read-only.** If the database was written by a newer studio, an older one reads it but saves nothing, with *This history was written by a newer version of the studio, so it opens read-only. Update the studio to keep saving decisions.*
- **Old SQLite turns history off.** History needs SQLite 3.37 or newer (for strict tables). With an older one the studio says so at startup and keeps answering without saving.

A new migration is a new file with the next number. Never edit or renumber one that has been released.

## Storage levels

Each decision is stored at one of three levels. The most private of three wins: what the request asks for (`"store"` or `X-Basal-Store`), the template's `storage`, and the studio's `history.store` setting. When the result is more private than the request asked, the response carries a `store_downgraded` warning.

| Level | What is written |
|---|---|
| `full` | Everything: variables, the situation, answers, raw probabilities, settings, media files (unless media storage is off) |
| `answers_only` | The decision row, answers, settings and timing. The variables, the situation and the media bytes are not written; media keep only their type, size and fingerprint |
| `none` | Nothing but a content-free usage count. An idempotency key, if you send one, is kept in memory instead |

Variables marked `sensitive` are never written at any level. The studio stores an HMAC of each value, keyed with `DATA/secret`, so you can later delete every decision that used a given email address without the address ever being on disk. How this looks from the app and the API: [History and privacy](/docs/concepts/history).

## Media files

Images, audio and video are stored by content: a file's bytes are named after their SHA-256 hash, in `blobs/sha256/` under a folder named for the hash's first two characters, so the same photo sent a hundred times is stored once. Each upload still gets its own random id (`file_...`); an id derived from the content would let one caller find out that another holds a given file. Files are limited to 200 MB.

Media sent with a decision stored at `answers_only` or `none`, or with media storage switched off, is written to `blobs/tmp/` for the model to read and deleted straight after, whether the decision answers, fails or is cancelled. The same goes for the file of a `sensitive` variable at any storage level: its row in `decision_media` has no file, no name and a keyed hash (`hmac-sha256:...`) in place of the content hash. An uploaded file that no decision or example uses is kept for a day. Deleting a file removes its bytes unless another upload shares them; decisions that used it keep its type, size and fingerprint and show the media as no longer available.

## Retention

The **retention sweeper** runs 60 seconds after the studio starts, then every hour, and straight after you change `retention_days`, `max_storage_gb` or `keep_labelled`. It deletes decisions older than the retention period, in batches, so new decisions keep saving while it works.

| Setting | Default | What it does |
|---|---|---|
| `history.retention_days` | `30` | Delete decisions older than this. `0` keeps them forever. A template's own `retention_days` overrides it for that template's decisions |
| `history.max_storage_gb` | `20` | When the database and media pass this size, the oldest decisions are deleted until it is back under 90% |
| `history.keep_labelled` | `true` | Decisions you labelled are never deleted by age or size |
| `history.store_media` | `true` | Keep images, audio and video with decisions stored at `full` |

Pinned decisions are never swept, and neither are decisions that belong to an evaluation or a running batch. Each sweep also removes expired upload records and unreferenced media, idempotency keys older than a day, usage counts older than 400 days, question sets no decision uses, and deleted templates whose history is gone, then returns some free space to the disk.

The History page sets the retention period; every setting can be changed with `PATCH /v1/studio/settings`.

:::console PATCH /v1/studio/settings
@@ curl
```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/settings \
  -H 'content-type: application/json' \
  -d '{"history": {"retention_days": 90, "max_storage_gb": 50}}'
```
@@ Response 200
```json
{
  "object": "settings",
  "history": {
    "store": "full",
    "retention_days": 90,
    "max_storage_gb": 50,
    "store_media": true,
    "keep_labelled": true,
    "on_store_error": "serve",
    "notice_acknowledged_at": null
  },
  "decisions": {"default_act_threshold": 0.9},
  "storage": {
    "db_bytes": 2531384,
    "blob_bytes": 0,
    "decisions": 13,
    "oldest_at": 1790815024,
    "last_sweep_at": 1790815083,
    "last_sweep_deleted": 0,
    "pending_deletion": 0,
    "search_available": true,
    "read_only": false,
    "store_errors": []
  }
}
```
:::

## Back up and move your data

The data folder is self-contained. To back it up or move it to another computer, copy the whole folder with the studio stopped, and keep `secret` with `studio.db`: without it, erasing a sensitive value by value no longer finds the decisions that used it. Models do not need to move with it; download them again on the new computer, or copy the Hugging Face cache separately.

While the studio is running, SQLite can write a consistent copy of the database itself. Copy `blobs/` and `secret` alongside it.

For a portable export of decisions rather than a backup, `GET /v1/studio/decisions/export` writes JSON Lines or CSV with the same filters as the History page ([History API](/docs/api/history)).

:::console Terminal
@@ Stopped
```bash
# Quit the app (or stop ./run.sh) first
cp -a ~/.local/share/ai.bud.decisionstudio/data ~/studio-backup
```
@@ Running
```bash
mkdir -p ~/studio-backup
cd ~/.local/share/ai.bud.decisionstudio/data
sqlite3 studio.db "VACUUM INTO '$HOME/studio-backup/studio.db'"
cp -a blobs ~/studio-backup/
cp -a secret ~/studio-backup/ 2>/dev/null   # exists once a sensitive variable was used
```
@@ Restore
```bash
# With the studio stopped; on a new computer, run the app once first
cp -a ~/studio-backup/. ~/.local/share/ai.bud.decisionstudio/data/
```
:::
