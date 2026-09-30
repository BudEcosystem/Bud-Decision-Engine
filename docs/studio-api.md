# Bud Decision Studio API: stored decisions, versioned templates and history

**Status:** the design this API was built from (design B, developer ergonomics, with the grafts and fixes the reviews called for; section 1.7 maps each review finding to its resolution). What version 0.2.0 implements is listed just below.

**Grounded in:**
- `basal/contract.py`: the six question types, `build_answers` at line 338, `apply_temperature`.
- `basal/api_compat.py`: `WireRequest` with `extra="ignore"`, `parse`, `shape`, the error bodies.
- `basal/server.py`: `_wire`, `_run`, `_record`, `_resolve_media`, `auth_and_timing`, `PUBLIC_API_PREFIXES = ("/v1/", …)`, `/api/history`, `/api/compare`, `/api/uploads`.
- `basal/worker.py`: `/decide`, which applies one temperature at line 179.
- `basal/catalog.py`: `modalities`, `types`, `max_options`, `max_questions`, `context_tokens`.
- UI: `ui/js/builder.js`, `ui/js/examples.js`, `ui/js/model-guides.js`, `ui/js/pages/playground.js` (drafts in `bud.draft.v2`, the act threshold pref at `store.js` line 120, compare, order test), `ui/js/pages/evaluate.js`, `ui/js/figures.js` (`gateOf`).
- `docs/external/SPEC_SUMMARY.md` and `README.md` ("Use it from code").


## What 0.2.0 implements

This document is the full design. Version 0.2.0 builds phases 0 to 2 of section 6.8, plus the version comparison of
phase 3. Everything the tests cover (`tests/test_studio_api.py`, `tests/test_templates.py`,
`tests/test_history_store.py`, `tests/test_contract.py`, and the unchanged `tests/test_conformance.py`) behaves as
written here.

**Built**

- **History everywhere.** Every decision from every entry point (`/v1/systemone`, the gateway routes, `/v1/studio`, the
  Playground, Compare) is recorded in `DATA/studio.db` at the level `full`, `answers_only` or `none` (sections 4.5 and
  4.7). The opt-outs are the body's `store`, `X-Basal-Store`, a template's `storage` and the studio setting. Wire
  response bodies are byte-identical with and without storage.
- **The resources of section 3:** decisions (create, preview, retrieve with `?wait` and `?format`, input, list with
  every filter of section 3.3 and cursors, patch, delete, redact, bulk delete and redact with `sensitive_hash`, rerun,
  cancel, background), feedback, stats (with `group_by` and `what_if`), export (JSONL and CSV), templates (create,
  list, retrieve, PUT, merge-patch PATCH, archive, delete with `history=keep|delete`, versions, diff, restore, aliases,
  schema, compatibility), per-template decisions, stats and **compare** (distributions, shared distributions, paired
  agreement and flips, operational figures), test examples (create, from a decision, patch as a new revision, delete,
  list at a revision, import a list, export), files, models and settings.
- **The rules of section 4:** typed variables with every substitution rule (whole-value typing, key removal, single
  pass, escaping, `sensitive` values stored only as HMACs, `trusted`, `options` variables), extensions (extra
  questions, `add_options`, `skip`), the seven-layer settings ladder with `sources`, per-question temperatures in the
  workers, act gates, raw probabilities, version change classes and comparability, idempotency keys (stored, and in
  memory for `none`), SDK retry folding, the cross-site guard, the error envelope and the remote-access rule.
- **Storage (section 5):** the schema of migration 0001, the import of the old activity log (0002), full-text search
  (0003), content-addressed media, the retention sweeper with its exemptions and size cap, backups before migrations,
  checksum checks, and read-only mode for a database written by a newer studio.
- **Starter templates:** `builtin/<scenario>` for each Playground scenario (`basal/builtin_templates.json`, generated
  by `scripts/export-builtins.mjs`).
- **The UI (section 6.6):** a History page (replacing Activity), a Templates page (library, overview, per-template
  history, version comparison, test examples, versions, aliases, restore), and template mode in the Playground
  (variables form, locked questions with skip and add-option, extra questions, Save as template, Edit questions and
  Save version with a conflict check, Keep in history).

**Different from the design, on purpose**

- `decision_answers` has a `position` column, so History shows questions in the order the model read them.
- Background decisions that were queued or running when the studio stopped are marked `failed` with
  `error.code: "interrupted"` at the next start, instead of being re-queued, so no request ever runs twice.
- With a template open, the Playground sends an act threshold or temperature only when you change it ("this decision
  only"); otherwise the template's own per-question values apply.
- Ad hoc decisions whose `metadata["basal.draft_of"]` names a template version are recorded as that version's `draft`
  (the Playground's "Edit questions"), hidden from the template's default history and stats.

**Planned (not in 0.2.0)**

- Phase 3: `POST /v1/studio/comparisons` (the Playground's Compare still uses `/api/compare`, now recorded as one
  comparison group), eval runs (`/v1/studio/evals`; the Evaluate page still runs its own loop, recorded with
  `surface: "eval"`), batches and replays, server-sent events (`"stream": true` answers 400 until then), the live
  `/decisions/events` feed, and per-model priority queues.
- Importing examples from an uploaded file (`file_id` with a column mapping); a list in the body works.
- `model_revision` is `null`: the Hub commit of each model's weights is not tracked yet.
- Multi-user workspaces and scoped API keys (section 5.8): the tables exist; one workspace is used.

---

## 1. Overview and mental model

### 1.1 Two ways in, one engine

| | `POST /v1/systemone` (and the gateway routes) | `/v1/studio/*` (new) |
|---|---|---|
| Analogy | OpenAI Chat Completions | OpenAI Responses |
| Shape | Exactly TypeSafe (or OpenRouter or Vercel). Not one byte of any response body changes. | Resource objects with ids, status, metadata and links. |
| State | Stateless. No request field refers to a stored object, and no answer depends on history. | Stateful. Decisions can be retrieved, filtered, labelled, rerun and compared. Templates are versioned resources. |
| Templates | No. `X-Basal-Template` only labels the history record; it never changes the answer. | Yes: `{"template": "support-triage@production", "variables": {…}}` |
| History | Every call is stored as a side effect by default. `x-basal-decision-id` is the handle. | Every call is stored by default and the response is the stored object. |
| Opt out | Body `"store": false` (read leniently, so it never causes a 422) or the header `X-Basal-Store: 0` | `"store": false` |

Both paths run through one pipeline in `basal/decisions.py`: **resolve → render → run → record**. The wire routes enter at "run", with no template. Every stored decision, whatever its entry point, has the same shape and lives in one SQLite store (`DATA/studio.db`). That store *is* the history.

**The guarantee:** any valid `/v1/systemone` body is also a valid `POST /v1/studio/decisions` body. It returns answers under the same keys, with every TypeSafe field unchanged and a few fields added. A conformance test enforces this (section 6.7).

### 1.2 The mental model in eight lines

1. A **decision** (`dec_…`) is one run: a state and questions go in, and a probability for every allowed answer comes out. It is stored unless the request lowers storage.
2. A **template** (`support-triage`) is a reusable decision definition. It holds:
   - questions;
   - allowed media types;
   - typed **variables** that fill a state template;
   - a default model;
   - settings (act threshold, calibration temperature, with per-model and per-question values);
   - an extension policy.

   It works with any model; limits are checked against the model that answers.
3. Every change to a template's definition creates an immutable **version** (`support-triage@3`). An **alias** such as `production` points at the version you pick. A bare id means the latest version.
4. **History is not a separate API.** It is `GET /v1/studio/decisions` with filters. Per-template history is `GET /v1/studio/templates/{id}/decisions`.
5. Callers may **extend** a template per decision:
   - add questions with new keys;
   - add options to questions the template allows;
   - skip questions the template allows.

   They can never rewrite a template question, so each question id keeps one meaning across history.
6. **Feedback** records what the right answer was. **Test examples** are labelled inputs that belong to a template. **Eval runs** score models × versions on those examples.
7. Every answer carries a `decision` value, a `certainty` and an `act` gate. The same `decision` values are the vocabulary for labels, filters and examples.
8. Version comparison works four ways:
   - stats grouped by version, with a graded comparability class per question;
   - the paired `/compare` endpoint;
   - eval runs across versions;
   - replay batches that rerun real traffic on a new version.

### 1.3 Where the new API lives: `/v1/studio`

Design B proposed `/bud/v1`. This spec uses **`/v1/studio`** instead, for these reasons:

- **It keeps clear of other vendors' paths.** `docs/external/SPEC_SUMMARY.md` section 2.2 says "Do not invent a `/v1/decisions` shape", and PRODUCT.md requires compatibility with OpenAI's Decisions API, whose paths are unpublished. `/v1/decisions`, `/v1/files` and `/v1/batches` stay free.
- **Auth needs no change.** `/v1/studio` already sits under `PUBLIC_API_PREFIXES` (`"/v1/"`, `server.py:167`).
- **It matches the grafted material.** Every graft and fix in the reviews is written against `/v1/studio`.
- **B's other conventions stay.** One template-reference syntax, `X-Basal-*` headers only, and a generated SDK under a `studio` namespace (section 3.0).

### 1.4 Quickstart

```bash
# 1. Save a template once (idempotent; re-running it changes nothing)
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/support-triage -d @support-triage.json

# 2. The everyday call: two variables, answers back, stored in history
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_message": "We were billed twice for March. Refund it today or we cancel.", "account_tier": "enterprise"}}'

# 3. See what needs a human
curl -s 'http://127.0.0.1:8420/v1/studio/templates/support-triage/decisions?act=false&created_after=-7d'
```

`curl -d` works without a `Content-Type` header, because non-browser calls are parsed as JSON whatever the header says (section 4.12). Section 3.1 walks through the five most common calls with full requests and responses.

### 1.5 Migrating from `/v1/systemone`

Each step is optional, and each keeps working on its own.

1. **Change nothing.** Your calls are already in History.
   - Every response carries `x-basal-decision-id`; `GET /v1/studio/decisions?request_id=req_…` finds the record.
   - Add `"store": false` (Python SDK: `extra_body={"store": False}`) or the header `X-Basal-Store: 0` to opt out.
2. **Change the path, keep the body.** POST the same body to `/v1/studio/decisions`.
   - `model`, `answers` and `usage` keep their meaning, and every answer keeps its TypeSafe fields.
   - You also get the fields `id`, `act` and `needs_review`, plus `decision`, `certainty` and `act` on each answer. You no longer need `X-Basal-Extensions`.
3. **Save your questions once.** Send `PUT /v1/studio/templates/support-triage {"questions": <your questions, unchanged>}`. Then call `{"template": "support-triage", "state": "…"}`. A template without variables takes a whole state.
4. **Add variables when you want smaller payloads.** Save a version that declares variables and a `state` template.
   - Callers pinned to `@1` keep sending `state`; callers on `@2` send `variables`.
   - History can compare the two versions (section 3.5).
5. **Pin an alias.** Point `production` at a version, and code calls `support-triage@production`. Promoting a version is then one `PUT` with no deploy, and edits made in the Playground never reach production by accident.

The Playground's Code tab shows the `/v1/studio` call and the equivalent `/v1/systemone` call side by side. `POST /v1/studio/decisions/preview` returns the equivalent wire body as `systemone_request`.

### 1.6 Decisions that need product-owner sign-off

| # | Decision in this spec | Why it departs from, or interprets, the brief | Alternative if rejected |
|---|---|---|---|
| PO-1 | **Test examples live beside versions, not inside them.** Each template has an `examples_revision` counter. Example rows are immutable, so an edit writes a new row. Every eval records `(versions, examples_revision, dataset_hash)`. | The brief says a template "defines labelled test examples" and "every save creates an immutable version". Putting examples inside versions would make adding one test case create a new version that production has not pinned, and would split per-version history for no behavioural reason. | Snapshot examples into each version. This costs version churn and splits stats. |
| PO-2 | **A save identical to the latest version creates no version.** The response says `"change": "unchanged"`. Head-only edits (name, description, metadata, retention, storage) never create versions. | "Every save" is read as "every save that changes behaviour". Retries, autosave and setup scripts stay idempotent. | Always create a version. This means version noise and non-idempotent setup scripts. |
| PO-3 | **`/v1/systemone` traffic is stored by default** with its full state, with 30-day default retention. | Requirement 1 asks for this. Existing integrations may send personal data. Mitigations: a first-run notice and switch on History, a notice on the API page and in the README, the `answers_only` level, and the studio-wide `history.store` ceiling. | Default wire traffic to `answers_only`. |
| PO-4 | **Default retention is 30 days** (the designs proposed 30, 30, 90 and 90). | 30 days is long enough to compare a month of traffic across versions, and short enough to limit personal data held on the machine. | 90 days. |
| PO-5 | **Per-question calibration temperature is supported** in requests, templates and eval "apply". | One review suggested deferring it. The runtime change is small (sections 6.2 and 6.4), and without it an eval cannot apply the per-question temperatures it fits. | Model-wide temperature only. |

### 1.7 What changed from design B (review fixes)

| Review finding | Resolution | Where |
|---|---|---|
| `/bud/v1` prefix; `X-Bud-*` headers | `/v1/studio`; `X-Basal-*` for every new header | 1.3, 2.0 |
| A template could not hold a plain temperature (`temperature_needs_model`) | `settings.temperature` allowed, plus `settings.models.<model>` and per-question values | 2.1, 4.3 |
| No per-question temperature in the runtime | `build_answers` takes a per-question mapping | 4.3, 6.2 |
| Raw (pre-temperature) probabilities were not stored | Stored for every answer; `include=answers.raw_probabilities`; used for `what_if` and temperature fitting | 2.3, 3.5 |
| Evals were not stored as decisions | Eval items are decisions (surface `eval`), hidden by default, unless the eval says `store: false` | 3.6, 4.5 |
| `DELETE` destroyed versions; archived templates rejected calls (C, D) | Archive keeps a template callable, with a `template_archived` warning. Delete keeps versions that history references, unless the caller also deletes that history. | 4.4 |
| Diff was an op list; comparability was binary (D) | Change class per version plus a graded comparability per question (`identical`, `text_changed`, `options_changed`, `incomparable`, `added`, `removed`) | 2.2, 4.4 |
| RFC 3339 timestamps, `next_cursor`, 201/202, two update verbs | Unix seconds, `first_id`/`last_id`/`has_more`, 200 everywhere except template creation (201), PATCH as the only update verb | 2.0 |
| No streaming | `?wait=` long-poll (phase 1) plus SSE with `sequence_number` (phase 3) | 4.10 |
| One `questions` map overloaded with patches (D) | `questions` (new keys only), `add_options`, `skip`, each with its own error code | 4.2 |
| `base_version` mandatory (D) | Optional. `If-Match`/`ETag` carry the version number. `PUT` upsert; POST with an identical body returns `unchanged`. | 4.4 |
| Inconsistent label vocabulary | One value everywhere: the answer's `decision`. Aliases are accepted on input only. | 2.3 |
| Cross-site history pollution through `/v1/systemone` | `Origin`/`Sec-Fetch-Site` guard on every public write, plus JSON-only browser writes on `/v1/studio` | 4.12 |
| `store` validation could 422 on the wire (A) | Wire extension fields are read leniently from the raw body; bad values are ignored and reported in `x-basal-warning` | 4.7 |
| Silent history-write failures | `x-basal-stored: failed`, a `history_write_failed` warning, and the `on_store_error` setting | 4.5 |
| SDK retries created duplicates | `Idempotency-Key` on the wire routes; `attempt` taken from `X-TypeSafe-Retry-Count`; `retry_of` folding in History | 4.9 |
| An eval covered one version | `targets` = models × versions over one `dataset_hash`, with flips between versions | 3.6 |
| `store:false` batches were refused | Allowed: results go only to an expiring output file | 3.7 |
| Validation errors used mixed statuses | 400 `invalid_request_error` for all validation, including `model_incompatible`; `model_error` only for runtime 503/504 | 4.11 |
| File ids derived from content hashes | Random `file_<ulid>` ids mapped to deduplicated blobs | 5.5 |
| `questions_hash` depended on key order | Exact `questions_hash` plus an order-insensitive `question_set_key` | 5.2 |
| Untemplated traffic stored in the clear | Storage levels `full` / `answers_only` / `none`, sensitive variables, notice | 4.5, 4.1 |
| Attribution of Playground edits and header-linked calls | `template.attribution` = `explicit`, `header`, `inferred` or `draft`; default stats count only `explicit` and `header` | 4.7, 4.8 |
| Storage over-built for phase 1 (D) | One writer connection with a transaction per decision; immutable template ids (no slug table); inferred linking deferred to phase 4; FTS feature-gated | 5 |
| End-user text in question instructions | A free-text variable used in question text must be declared `trusted` | 4.1 |
| Remote history readable without a key | 403 `remote_access_requires_key` | 4.12 |

---

## 2. Resources and object shapes

### 2.0 Conventions

| Topic | Rule |
|---|---|
| Base URL | `http://127.0.0.1:8420/v1/studio`. The desktop app may use ports 8421–8440; the API page shows the exact address. Remote callers send `Authorization: Bearer $BASAL_API_KEY`. |
| Bodies | JSON. Requests without `Origin` and `Sec-Fetch-Site` (curl, SDKs, servers) are parsed as JSON whatever `Content-Type` says. Browser writes must send `application/json` (section 4.12). `POST /files` also takes multipart or raw bytes. |
| Names | snake_case. TypeSafe's names wherever the concept exists: `model`, `state`, `questions`, `instructions`, `criteria`, `answers`, `usage`, `media`, `settings.temperature`. |
| Ids | A prefix plus a 26-character ULID (time-sortable, 80 random bits): `dec_`, `ex_`, `fb_`, `evr_`, `bat_`, `cmp_`, `file_`. |
| Identifier patterns | Template ids are chosen by the user and are immutable: `^[a-z0-9][a-z0-9_-]{0,63}$`. `builtin/<name>` is reserved for the starter templates. Question keys: `^[A-Za-z_][A-Za-z0-9_]{0,63}$` in templates (ad hoc keys are free-form, as in TypeSafe). Variable names: `^[a-z_][a-z0-9_]{0,63}$`. Aliases: `^[a-z][a-z0-9_-]{0,31}$`, with `latest` reserved. |
| Template references | One syntax in bodies, query parameters and history: `support-triage` (the latest version when the request arrives), `support-triage@3`, `support-triage@production`, `support-triage@latest`. |
| Time | `*_at` fields are integer Unix seconds. Filters accept Unix seconds, RFC 3339, or relative times (`-30m`, `-24h`, `-7d`). Durations are `*_ms`, as float milliseconds. |
| Objects | Every object has `"object"`: `template`, `template.version`, `template.alias`, `template.diff`, `template.compatibility`, `template.example`, `decision`, `decision.summary`, `decision.input`, `decision.preview`, `decision.stats`, `feedback`, `comparison`, `eval`, `eval.item`, `batch`, `batch.comparison`, `file`, `model`, `settings`, `list`, `<type>.deleted`. |
| Lists | `{"object": "list", "data": [...], "first_id": "...", "last_id": "...", "has_more": true}`. |
| List parameters | `limit` (1–100, default 20), `order` (`desc` by default, by creation), `after` / `before`. |
| Cursors | A cursor is an id from a page you received: a decision id, a template id, or a version number for version lists. It is a *position*, so it stays valid under any filter. |
| Live tail | `order=asc&after=<newest id seen>`. |
| Updates | `PATCH` only, as an RFC 7396 JSON merge patch: maps merge, `null` removes, arrays are replaced. `metadata` follows the same rule on every resource. |
| Deletes | Return `{"id": "...", "object": "<type>.deleted", "deleted": true}` (template deletes add counts, section 3.2). |
| `metadata` | At most 16 caller keys. Keys up to 64 characters matching `^[A-Za-z0-9_.:-]+$`; values are strings up to 512 characters. The keys `basal.*` and `openrouter.*` are written by the studio and do not count toward the 16. Metadata is never sent to a model. |
| `include` | `include=a,b` or repeated `include=`. The allowed values are listed per endpoint. |
| Success codes | `200` for every success, including creating decisions, background decisions (returned with `status: "queued"`), batches and evals. `201` only when a template is created. |
| OpenAPI | `/openapi.json` (FastAPI), tag "Studio API". operationIds are `studio.<namespace>.<verb>`, listed in section 3.0. |

**Request headers**

| Header | Where | Meaning |
|---|---|---|
| `Authorization: Bearer …` | all | Required for remote callers, as today. |
| `Idempotency-Key` | every POST under `/v1/studio`; the wire routes | Section 4.9. |
| `If-Match: "3"` | `PUT`/`PATCH /templates/{id}`, `POST …/versions` | Optimistic concurrency on the latest version number (section 4.4). |
| `X-Basal-Store` | all decision routes | `0`/`false`/`none`, `answers_only`, or `1`/`true`/`full`. When body and header disagree, the more private value wins. |
| `X-Basal-Metadata` | all decision routes | `ticket_id=T-4411; channel=email` (values percent-encoded). Merged under body `metadata`. |
| `X-Basal-Template` | wire routes only | Attributes a stateless call to a template version (section 4.7). |
| `X-Basal-Client`, `X-Basal-Surface` | the studio UI | Record `source.surface` (section 4.8). |
| `X-Basal-Extensions: 1` | wire routes | Unchanged: adds the studio answer fields to wire responses. |
| `X-TypeSafe-Retry-Count` | sent by the official SDKs | Recorded as `source.attempt`. |

**Response headers**

| Header | Meaning |
|---|---|
| `x-typesafe-request-id` | As today, on every response. On `/v1/studio` it is repeated as `x-request-id` (same `req_<32 hex>` value). |
| `x-basal-decision-id` | Present whenever a decision was stored. |
| `x-basal-stored` | `full`, `answers_only`, `none` or `failed`, on every decision response. |
| `x-basal-warning` | Comma-separated warning codes on the wire routes, whose bodies have no place for warnings. |
| `x-basal-idempotent-replayed: true` | This response is a replay (section 4.9). |
| `x-basal-template-status` | `attributed` or `mismatch`, when `X-Basal-Template` was sent. |
| `ETag`, `Location` | Templates (version number) and created objects. |

**Hashes** are written `sha256:<64 hex>`. Examples in this document shorten them to 16 hex characters.

### 2.1 Template

This is `GET /v1/studio/templates/support-triage` right after version 1 was created by `PUT` (section 3.1, call 1). It is also exactly that `PUT`'s response body.

```json
{
  "id": "support-triage",
  "object": "template",
  "name": "Support triage",
  "description": "Route inbound tickets, rate urgency, flag churn risk.",
  "origin": "user",
  "archived": false,
  "metadata": {"owner_team": "support-eng"},
  "storage": "full",
  "retention_days": null,
  "aliases": {"latest": 1},
  "examples_revision": 0,
  "examples_count": 0,
  "version": 1,
  "note": "First version",
  "content_hash": "sha256:5c1f0e9a44b2d7c1",
  "modalities": ["text", "image"],
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000,
                         "required": true, "sensitive": false, "trusted": false},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free",
                     "required": false, "sensitive": false, "trusted": false},
    "open_invoices": {"type": "integer", "minimum": 0, "required": false, "sensitive": false, "trusted": false},
    "customer_email": {"type": "string", "max_length": 320, "required": false, "sensitive": true, "trusted": false},
    "screenshot": {"type": "image", "required": false, "sensitive": false, "trusted": false}
  },
  "state": {
    "customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
    "message": "{{customer_message}}"
  },
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"}
  },
  "model": "laya",
  "settings": {
    "act_threshold": 0.9,
    "temperature": 1.1,
    "questions": {"churn_risk": {"act_threshold": 0.7}},
    "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}
  },
  "extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
  "created_at": 1790845800,
  "updated_at": 1790845800,
  "last_used_at": null,
  "change": "created",
  "warnings": []
}
```

**Head fields.** These are not versioned, can be changed with `PATCH`, and never change an answer.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The immutable template id. |
| `name` | string (up to 120 characters) | Display name. Defaults to the id. |
| `description` | string (up to 2,000 characters) | |
| `origin` | `user` or `builtin` | Built-ins (`builtin/<name>`) are read-only: writes get 403 `template_read_only`. |
| `archived` | bool | Hidden from `GET /templates` unless `include_archived=true`. Still callable (section 4.4). |
| `metadata` | map | Section 2.0. |
| `storage` | `full`, `answers_only` or `none` | The storage ceiling for this template's decisions. Callers can only store less (section 4.5). |
| `retention_days` | integer or null | Overrides the studio retention for this template's decisions. `0` keeps them forever; `null` uses the studio setting. |
| `aliases` | map of alias to version | Always includes `latest`. |
| `examples_revision` | integer | Rises by 1 on every example add, edit or delete (section 4.4). |
| `examples_count` | integer | Current examples. |
| `created_at`, `updated_at`, `last_used_at` | Unix seconds | `last_used_at` is the time of the latest decision made with this template. |

**Version fields.** The template object embeds the latest version, or the one asked for with `?version=`.

| Field | Type | Meaning |
|---|---|---|
| `version` | integer | The version number of the definition shown. |
| `note` | string | Why this version was saved. |
| `content_hash` | string | Hash of the canonical definition (section 4.4). |
| `modalities` | array of `text`, `image`, `audio`, `video` | What callers may attach. `text` is always present. Every media variable's type must be listed. |
| `variables` | map of name to variable spec | Section 4.1. Returned normalised, with `required`, `sensitive` and `trusted` filled in. |
| `state` | string, object, array or null | The state template (section 4.1). A string gives a text state; an object or array gives a JSON state; `null` means "the object of the non-media variables". |
| `questions` | map of key to question | 1 to 128 questions, in the six `contract.py` shapes (`choice`, `score`, `noul`, `multi`, `rank`, `number`). `{{var}}` placeholders are allowed where section 4.1 says. |
| `model` | string or null | The default model. `null` means the most recently loaded model. It need not be downloaded when the template is saved. |
| `settings` | Settings object | See below. |
| `extensions` | object | What callers may change per decision (section 4.2). Default: `{"questions": true, "max_questions": 16, "options": [], "skip": []}`. |

**Write-only fields**, present on write responses:
- `change`: `created`, `new_version`, `unchanged` or `metadata_only`.
- `warnings`: `[{code, message, param}]`.

**`include=compatibility`** adds `compatibility`, the same content as section 3.2's compatibility endpoint.

**Settings object.** The same shape is used in templates, requests and decisions.

| Field | Where | Meaning |
|---|---|---|
| `act_threshold` | template, request | 0 < t ≤ 1. An answer "acts" when its `certainty` is at least this value. |
| `temperature` | template, request | Calibration temperature, 0 < T ≤ 20, applied as p' ∝ p^(1/T). It never changes which option wins. 1 means no calibration. |
| `questions.<q>.act_threshold` | template, request | Per-question threshold. |
| `questions.<q>.temperature` | template, request | Per-question temperature. |
| `questions.<q>.multi_threshold` | template, request | For `multi` questions only: the selection cut-off, overriding the question's `threshold`. |
| `models.<model id>` | template only | `{act_threshold, temperature, questions}` for that resolved model. Calibration belongs to a model, and this is how one template works well on every model. |
| `sources` | decision only (output) | Which layer produced each effective value (section 4.3). |

**Extensions object**

| Field | Type | Default | Meaning |
|---|---|---|---|
| `questions` | bool | `true` | Callers may add questions with new keys. |
| `max_questions` | integer | 16 | Upper bound on added questions. |
| `options` | list of question keys, or `true` | `[]` | The `choice`, `multi`, `rank` or `number` questions that accept `add_options`. `true` means all of them. |
| `skip` | list of question keys, or `true` | `[]` | The template questions callers may leave out. |

### 2.2 Template version

`GET /v1/studio/templates/support-triage/versions/3`. Version 3 added an `account` option to `department`:

```json
{
  "object": "template.version",
  "template": "support-triage",
  "version": 3,
  "note": "Add the account team",
  "content_hash": "sha256:9f2c47d1e0ab6c35",
  "questions_hash": "sha256:a93d77c0b1e24f58",
  "question_set_key": "sha256:0d6e5b9a7c3f1e22",
  "source": "api",
  "base_version": 2,
  "aliases": ["latest"],
  "changes": {
    "from": 2,
    "class": "extended",
    "breaking_for_callers": false,
    "latest_callers_7d": 1204,
    "questions": {"department": "options_changed", "urgency": "identical", "churn_risk": "identical", "wants_refund": "identical"},
    "summary": ["department: option 'account' added"]
  },
  "modalities": ["text", "image"],
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000, "required": true, "sensitive": false, "trusted": false},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free", "required": false, "sensitive": false, "trusted": false},
    "open_invoices": {"type": "integer", "minimum": 0, "required": false, "sensitive": false, "trusted": false},
    "customer_email": {"type": "string", "max_length": 320, "required": false, "sensitive": true, "trusted": false},
    "screenshot": {"type": "image", "required": false, "sensitive": false, "trusted": false}
  },
  "state": {
    "customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
    "message": "{{customer_message}}"
  },
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "account": "login, seats, access",
                                "other": "anything else"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}
  },
  "model": "laya",
  "settings": {
    "act_threshold": 0.85,
    "temperature": 1.1,
    "questions": {"churn_risk": {"act_threshold": 0.7}},
    "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}
  },
  "extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
  "created_at": 1790851200,
  "created_by": "local"
}
```

| Field | Meaning |
|---|---|
| `template`, `version` | Identity. Versions are 1, 2, 3 and so on, with no gaps. They are never edited or renumbered. |
| `note` | Given on save. |
| `content_hash` | Hash of the canonical definition. |
| `questions_hash` | Exact hash of the template's questions: key and option order are preserved, and placeholders are left unrendered. |
| `question_set_key` | The same questions hashed with question keys sorted and option order preserved (section 5.2). |
| `source` | `api`, `studio`, `restore`, `eval_apply`, `clone`, `builtin` or `import`. |
| `base_version` | The version this one was edited from. `null` for version 1. |
| `aliases` | The aliases that currently point at this version. |
| `changes` | Computed against the previous version when it is saved, and stored. Fields below. |
| `created_by` | The actor: `local`, `key_env` (the `BASAL_API_KEY` holder) or `key_<id>` in the future. |
| Definition fields | As in section 2.1. |

The `changes` object:

| Field | Meaning |
|---|---|
| `from` | The version compared against. |
| `class` | `settings_only` (model, settings or extensions changed), `wording` (only text changed), `extended` (questions, options or variables with defaults were added; nothing existing changed), or `breaking` (something was removed, renamed or re-typed; a score scale changed; a required variable was added without a default; a modality was removed). |
| `breaking_for_callers` | True when requests that worked on `from` would now get a 400. |
| `latest_callers_7d` | Decisions in the last 7 days that resolved through `latest` and so move to this version automatically. |
| `questions` | Comparability of each question against `from` (section 4.4). |
| `summary` | Plain-language lines. |

### 2.3 Decision

This is `GET /v1/studio/decisions/dec_01JB7Q2M4X9V3K8T6R1N5P0HZC?include=input.rendered_state,answers.raw_probabilities`. The decision was made in section 3.1, call 2. `GET` includes `input` by default.

```json
{
  "id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "object": "decision",
  "status": "completed",
  "created_at": 1790846043,
  "completed_at": 1790846043,
  "template": {"id": "support-triage", "version": 1, "ref": "support-triage", "resolved_from": "latest", "attribution": "explicit"},
  "model": "laya",
  "model_requested": null,
  "model_revision": "3c1f0a9d",
  "input": {
    "variables": {"customer_message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.",
                  "account_tier": "enterprise"},
    "state": {"customer": {"tier": "enterprise"},
              "message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel."},
    "rendered_state": "customer:\n  tier: enterprise\nmessage: We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.",
    "media": [],
    "questions": {
      "department": {"type": "choice", "instructions": "Which department should handle this request?",
                     "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                  "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
      "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the enterprise plan.",
                  "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
      "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"}
    }
  },
  "extensions": {"questions": [], "options": {}, "skipped": []},
  "answers": {
    "department": {"type": "choice", "choice": "billing",
                   "probabilities": {"billing": 0.9612, "technical": 0.0141, "sales": 0.0107, "other": 0.014},
                   "confidence": 0.9483, "decision": "billing", "top_probability": 0.9612,
                   "certainty": 0.9612, "act": true, "origin": "template",
                   "raw_probabilities": {"billing": 0.989, "technical": 0.0041, "sales": 0.0029, "other": 0.0041}},
    "urgency": {"type": "score", "score": 2.61,
                "legend": {"0": "can wait", "1": "soon", "2": "today", "3": "blocking or at risk of churn"},
                "probabilities": {"0": 0.02, "1": 0.08, "2": 0.17, "3": 0.73},
                "confidence": 0.61, "decision": "3", "top_probability": 0.73,
                "certainty": 0.73, "act": false, "origin": "template",
                "raw_probabilities": {"0": 0.0014, "1": 0.0171, "2": 0.0664, "3": 0.9151}},
    "churn_risk": {"type": "noul", "noul": 0.9431, "probabilities": {"false": 0.0569, "true": 0.9431},
                   "decision": "yes", "top_probability": 0.9431,
                   "certainty": 0.9431, "act": true, "origin": "template",
                   "raw_probabilities": {"false": 0.0253, "true": 0.9747}}
  },
  "act": false,
  "needs_review": ["urgency"],
  "settings": {
    "act_threshold": 0.9,
    "temperature": 1.3,
    "questions": {"churn_risk": {"act_threshold": 0.7}, "urgency": {"temperature": 1.8}},
    "sources": {"act_threshold": "template", "temperature": "template.models.laya",
                "questions.churn_risk.act_threshold": "template.questions",
                "questions.urgency.temperature": "template.models.laya.questions"}
  },
  "usage": {"input_tokens": 142, "output_tokens": 0},
  "timing": {"queue_ms": 0.4, "load_ms": 0.0, "model_ms": 96.2, "total_ms": 126.0},
  "passes": 1,
  "notes": [],
  "warnings": [],
  "source": {"surface": "api", "endpoint": "/v1/studio/decisions", "format": "studio", "client": "curl",
             "request_id": "req_5d0c2b7e9a414f6c8e1f3a2b4c6d8e0f", "attempt": 0, "retry_of": null},
  "group": null,
  "batch": null,
  "eval": null,
  "rerun_of": null,
  "metadata": {},
  "pinned": false,
  "feedback": {},
  "store": "full",
  "expires_at": 1793438043,
  "error": null
}
```

| Field | Meaning |
|---|---|
| `status` | `queued`, `in_progress`, `completed`, `failed` or `cancelled`. A synchronous create returns `completed`, or an error status (section 4.11) with the failed decision's id. |
| `template` | `null` for ad hoc decisions. Otherwise it holds `id`; `version` (the resolved integer, never an alias); `ref` (exactly as the caller wrote it, or the `X-Basal-Template` value); `resolved_from` (`latest`, `pinned` or `alias`); and `attribution` (`explicit`, `header`, `inferred` or `draft`; sections 4.7 and 4.8). |
| `model`, `model_requested`, `model_revision` | `model` is the resolved studio id that answered and is never an alias (as in TypeSafe). `model_requested` is what the caller sent (an alias, a Hugging Face repo id or `null`). `model_revision` is the Hub commit of the weights, when known. |
| `input` | Included by `GET` by default, and on create, list and export only with `include=input`. It is always the **stored** form: sensitive values appear as `{"$redacted": "hmac-sha256:…"}` and `[redacted:<name>]`. It is `null` under `answers_only` or after redaction. |
| `input.variables` | As received, after defaults were applied. `null` for ad hoc decisions. |
| `input.state` | The state the model read, after substitution. |
| `input.rendered_state` | Only with `include=input.rendered_state`: the exact text the adapters received (`contract.render(state)`). |
| `input.media` | `[{type, file_id, name, content_type, bytes, variable, available}]` (section 3.7). |
| `input.questions` | The effective questions: after skip, `add_options` and extras, with placeholders filled, in the order the model saw them. |
| `extensions` | `questions` lists the added question keys; `options` maps each question key to the option names added; `skipped` lists the template questions left out. |
| `answers` | Per question, below. `null` until the decision completes. |
| `act`, `needs_review` | `act` is true when every answer acts. `needs_review` lists the keys of the answers that do not. |
| `settings` | The effective settings. `act_threshold` and `temperature` apply to every question not listed under `questions`. `questions.<q>` lists only the values that differ. `sources` maps each value's path to the layer that set it (section 4.3). |
| `usage` | `{input_tokens, output_tokens}`, as on the wire. |
| `timing` | `queue_ms`: waiting for the model's worker. `load_ms`: loading the model, if this call triggered a load. `model_ms`: the worker's own time (today's `latency_ms`). `total_ms`: time inside the server (today's `wall_ms`). |
| `passes`, `notes` | From the worker: forward passes used, and notes about the model such as "image ignored". |
| `warnings` | Non-fatal issues: `[{code, message, param}]`. Codes are listed in section 4.11. |
| `source` | Fields below. |
| `group` | `{type: "comparison" or "order_test", id: "cmp_…"}` when the decision was made as part of a comparison, otherwise `null`. |
| `batch` | `{id, custom_id}` for batch items. |
| `eval` | `{id, example_id, target}` for eval items. |
| `rerun_of` | The original decision's id, for reruns and replays. |
| `metadata` | Caller metadata plus `basal.*` and `openrouter.*` keys. |
| `pinned` | Pinned decisions are never removed by retention. |
| `feedback` | The latest feedback per question: `{"<q>": {"expected", "correct", "feedback_id"}}`. `{}` when there is none. |
| `store` | The effective storage level: `full` or `answers_only`. A `none` decision is never retrievable, but the create response says `"none"`. |
| `expires_at` | Computed on every read from the effective retention. `null` when kept forever or exempt. |
| `error` | `null`, or `{type, code, message}` when failed. |

The `source` object:

| Field | Meaning |
|---|---|
| `surface` | `api`, `playground`, `compare`, `order_test`, `eval`, `batch`, `replay`, `rerun` or `imported`. |
| `endpoint` | The path called. |
| `format` | `studio`, `typesafe`, `openrouter`, `vercel` or `evaluate`. |
| `client` | From `server.client_of()`. |
| `request_id` | The `x-typesafe-request-id` of the HTTP call. |
| `attempt` | From `X-TypeSafe-Retry-Count`; 0 is the first try. |
| `retry_of` | The earlier attempt this one retried (section 4.9). |

**Answers.** Each answer is the full studio answer that `contract.build_answers` produces today, the same for every model, plus these fields:

| Type | Fields |
|---|---|
| `noul` | `noul` (P(yes)), `probabilities` (`false`, `true`), `decision` (`yes` or `no`), `top_probability` |
| `choice` | `choice`, `probabilities`, `confidence`, `decision`, `top_probability` |
| `score` | `score` (the expected level), `legend` (the request's criteria, keyed `"0"`…), `probabilities`, `confidence`, `decision` (the level key `"0"`…`"n-1"`), `top_probability` |
| `multi` | `selected`, `probabilities` (option to P(applies)), `threshold`, `decision` (the selected list), `top_probability` |
| `rank` | `ranking`, `probabilities`, `choice`, `decision` (the first option), `confidence`, `top_probability` |
| `number` | `estimate`, `most_likely`, `range` (the central 80%), `unit`, `probabilities`, `decision` (the `most_likely` number), `top_probability`, `confidence` |

Added to every answer:
- `certainty`: equal to `top_probability`, except for `multi`, where it is the minimum over options of max(p, 1−p). That is the rule `figures.js` `gateOf` applies today.
- `act`: `certainty >= act_threshold`, using that question's effective threshold.
- `origin`:
  - `template`: an untouched template question;
  - `extended`: a template question with options added by the caller;
  - `extra`: a question added by the caller;
  - `adhoc`: no template.
- `dynamic_options`: present, and `true`, only when the options came from an `options` variable (section 4.1).

Opt-in fields:
- `raw_probabilities`, with `include=answers.raw_probabilities`: the same keys as `probabilities`, normalised but **before** the calibration temperature. They are always stored, under `full` and under `answers_only`.
- `model_extras`, with `include=answers.model_extras`: the worker's model-specific signals (for example Laya's `act_probability`).

**The label vocabulary.** An answer's `decision` value is the one vocabulary used for `expected` in feedback and examples, for the history filter `answer.<q>=`, for CSV export and for eval metrics. Aliases are accepted **on input only** and are stored in the canonical form.

| Type | Canonical value | Also accepted on input | Correct when |
|---|---|---|---|
| `choice` | an option name | none | equals `choice` |
| `noul` | `"yes"` or `"no"` | `true`/`false`, `"true"`/`"false"` | equals `decision` |
| `score` | the level key `"0"`…`"n-1"` | an integer index, or the exact level text | equals `decision` |
| `multi` | a list of option names | none | set equality (per-option accuracy is reported too) |
| `rank` | an option name (the first) | a full or partial ordering (its first element is used; a full ordering also yields Kendall tau in evals) | equals `decision` |
| `number` | a number | a numeric string | inside `range`; absolute error is reported too |

**`DecisionSummary`.** List items default to `view=summary`, a separate typed object for SDKs:

```json
{
  "id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "object": "decision.summary",
  "status": "completed",
  "created_at": 1790846043,
  "template": {"id": "support-triage", "version": 1},
  "model": "laya",
  "source": {"surface": "api", "client": "curl", "attempt": 0},
  "extended": false,
  "act": false,
  "needs_review": ["urgency"],
  "answers": {"department": {"type": "choice", "decision": "billing", "certainty": 0.9612, "act": true},
              "urgency": {"type": "score", "decision": "3", "certainty": 0.73, "act": false},
              "churn_risk": {"type": "noul", "decision": "yes", "certainty": 0.9431, "act": true}},
  "timing": {"total_ms": 126.0},
  "metadata": {},
  "pinned": false,
  "labelled": false,
  "error": null
}
```

In a summary, `extended` is true when `extensions` is not empty, and `labelled` is true when any feedback exists. Every other field is as defined above.

### 2.4 Feedback

A human's verdict on a stored decision: `POST /v1/studio/decisions/{id}/feedback` (section 3.4).

```json
{
  "id": "fb_01JB7W2E4G6J8K0M2N4P6Q8R0S",
  "object": "feedback",
  "decision_id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "question": "urgency",
  "expected": "3",
  "predicted": "3",
  "correct": true,
  "rating": null,
  "note": "Confirmed on the call: they will cancel if not refunded today.",
  "actor": "local",
  "created_at": 1790852580,
  "updated_at": 1790852580
}
```

| Field | Meaning |
|---|---|
| `question` | A key from the decision's effective questions, or `"*"` for the decision as a whole (rating only). |
| `expected` | The right answer, in the canonical vocabulary (section 2.3). It is validated against that decision's effective question, including options the caller added or options that came from a variable. `null` when only a rating is given. |
| `predicted` | The decision's `decision` value for that question, copied when the feedback is written. |
| `correct` | Computed with the rules in section 2.3; `null` when `expected` is `null`. |
| `rating` | `1`, `-1` or `null`. |
| `note` | Free text, up to 2,000 characters. |
| `actor` | Who wrote it. There is one feedback per (decision, question, actor); writing again updates it. |

### 2.5 Test example

A labelled input belonging to a template: `POST /v1/studio/templates/{id}/examples` (section 3.6).

```json
{
  "id": "ex_01JB7S1D0K3M5N7P9Q1R3S5T7V",
  "object": "template.example",
  "template": "support-triage",
  "revision": 1,
  "variables": {"customer_message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.",
                "account_tier": "enterprise"},
  "state": null,
  "media": [],
  "expected": {"urgency": "3", "department": "billing", "churn_risk": "yes"},
  "tags": ["from-review", "regression"],
  "split": "test",
  "note": "",
  "from_decision": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "created_at": 1790852640,
  "updated_at": 1790852640
}
```

| Field | Meaning |
|---|---|
| `revision` | The template's `examples_revision` at which this content was written. Rows are immutable: an edit writes a new row with the same `id` and a new `revision`, so any past revision can be listed with `?revision=N`. |
| `variables` or `state` | Whichever kind of input the template takes. The other is `null`. |
| `media` | File references (section 3.7), or the values of media variables inside `variables`. |
| `expected` | Canonical labels (section 2.3) for any subset of the template's questions. Unlabelled questions still count toward agreement between models. |
| `tags` | Free labels for selecting subsets. |
| `split` | `test` (default) or `calibration`. Evals fit temperatures on `calibration` examples when any exist. |
| `from_decision` | Set when the example was promoted from history. |

Examples are validated against the **latest** version when written. When an eval runs an older version, examples that don't fit it are reported as `skipped`, with a reason.

### 2.6 Supporting objects

These objects are defined where they are used: `decision.preview` and `decision.input` (section 3.3), `decision.stats` and `template.comparison` (section 3.5), `eval` and `eval.item` (section 3.6), `comparison`, `batch`, `batch.comparison`, `file`, `model` and `settings` (section 3.7).

---

## 3. Endpoints

### 3.0 Endpoint index

All paths are relative to `/v1/studio`. OperationIds are `studio.<namespace>.<verb>` in the OpenAPI document. A generated Python SDK exposes them as, for example, `bud.studio.decisions.create(...)`.

| Method | Path | operationId | Purpose |
|---|---|---|---|
| POST | `/templates` | `templates.create` | Create at version 1, from a definition or a `from` source |
| GET | `/templates` | `templates.list` | Library |
| GET | `/templates/{id}` | `templates.retrieve` | Head plus a definition (`?version=`, `include=compatibility`) |
| PUT | `/templates/{id}` | `templates.upsert` | Idempotent create or replace |
| PATCH | `/templates/{id}` | `templates.update` | Merge patch; a new version only if the definition changes |
| POST | `/templates/{id}/archive`, `/unarchive` | `templates.archive`, `templates.unarchive` | Hide or unhide; stays callable |
| DELETE | `/templates/{id}` | `templates.delete` | Delete, with `confirm` and `history=keep\|delete` |
| GET | `/templates/{id}/versions` | `templates.versions.list` | Versions, newest first |
| POST | `/templates/{id}/versions` | `templates.versions.create` | Save a new version from a full definition |
| GET | `/templates/{id}/versions/{n}` | `templates.versions.retrieve` | One version (a number, an alias or `latest`) |
| GET | `/templates/{id}/versions/{n}/diff` | `templates.versions.diff` | Structural diff with comparability |
| POST | `/templates/{id}/versions/{n}/restore` | `templates.versions.restore` | A new version with version n's definition |
| PUT, DELETE | `/templates/{id}/aliases/{alias}` | `templates.aliases.set`, `.delete` | Move or remove an alias |
| GET | `/templates/{id}/schema` | `templates.schema` | JSON Schema of the variables |
| GET | `/templates/{id}/compatibility` | `templates.compatibility` | Every catalog model checked |
| GET | `/templates/{id}/decisions` | `templates.decisions.list` | Per-template history |
| GET | `/templates/{id}/stats` | `templates.stats` | Per-template aggregates |
| GET | `/templates/{id}/compare` | `templates.compare` | Version comparison over history |
| GET, POST | `/templates/{id}/examples` | `templates.examples.list`, `.create` | Test examples |
| POST | `/templates/{id}/examples/import` | `templates.examples.import_` | Bulk add |
| GET | `/templates/{id}/examples/export` | `templates.examples.export` | JSONL |
| GET, PATCH, DELETE | `/templates/{id}/examples/{ex}` | `templates.examples.retrieve`, `.update`, `.delete` | One example |
| GET | `/templates/{id}/evals` | `templates.evals.list` | Same as `GET /evals?template={id}` |
| POST | `/decisions` | `decisions.create` | Make a decision |
| POST | `/decisions/preview` | `decisions.preview` | Resolve and validate without running |
| GET | `/decisions` | `decisions.list` | History: filter and paginate |
| GET | `/decisions/{id}` | `decisions.retrieve` | One decision (`?wait=`, `?stream=`, `?format=`) |
| GET | `/decisions/{id}/input` | `decisions.input` | The stored input only |
| PATCH | `/decisions/{id}` | `decisions.update` | `metadata`, `pinned` |
| DELETE | `/decisions/{id}` | `decisions.delete` | Hard delete |
| POST | `/decisions/{id}/redact` | `decisions.redact` | Remove input fields, keep answers |
| POST | `/decisions/delete`, `/decisions/redact` | `decisions.bulk_delete`, `.bulk_redact` | By filter, with `dry_run` |
| POST | `/decisions/{id}/cancel` | `decisions.cancel` | Background decisions |
| POST | `/decisions/{id}/rerun` | `decisions.rerun` | The same input on another model, version or settings |
| POST, GET | `/decisions/{id}/feedback` | `decisions.feedback.create`, `.list` | Labels and ratings |
| DELETE | `/feedback/{id}` | `feedback.delete` | |
| GET | `/decisions/stats` | `decisions.stats` | Aggregates over any filter |
| GET | `/decisions/export` | `decisions.export` | JSONL or CSV stream |
| GET | `/decisions/events` | `decisions.events` | SSE live feed (phase 3) |
| POST, GET | `/comparisons`, `/comparisons/{id}` | `comparisons.create`, `.retrieve` | Vary models, versions or option order |
| POST, GET | `/evals`, `/evals/{id}` | `evals.create`, `.list`, `.retrieve` | Score targets on examples |
| GET | `/evals/{id}/items` | `evals.items` | Per example and target |
| POST | `/evals/{id}/apply`, `/evals/{id}/cancel` | `evals.apply`, `evals.cancel` | |
| DELETE | `/evals/{id}` | `evals.delete` | Deletes its item decisions too |
| POST, GET | `/batches`, `/batches/{id}` | `batches.create`, `.list`, `.retrieve` | Bulk and replay |
| GET | `/batches/{id}/results`, `/batches/{id}/comparison` | `batches.results`, `batches.comparison` | |
| POST | `/batches/{id}/cancel` | `batches.cancel` | |
| POST, GET, DELETE | `/files`, `/files/{id}`, `/files/{id}/content` | `files.create`, `.retrieve`, `.content`, `.delete` | Media and job files |
| GET | `/models`, `/models/{id}` | `models.list`, `.retrieve` | Capabilities |
| GET, PATCH | `/settings` | `settings.retrieve`, `.update` | History settings |

The wire routes are unchanged: `/v1/systemone`, `/v1/models`, `/api/v1/systemone`, `/api/alpha/decisions`, `/typesafe/v1/systemone`, `/typesafe/v1/models` and `/v1/evaluate`.

### 3.1 The five most common calls, end to end

**Call 2 is the one you will make millions of times.**

#### Call 1: set up a template (once, from a setup script or CI)

```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/support-triage -d '{
  "name": "Support triage",
  "description": "Route inbound tickets, rate urgency, flag churn risk.",
  "metadata": {"owner_team": "support-eng"},
  "note": "First version",
  "modalities": ["text", "image"],
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"},
    "open_invoices": {"type": "integer", "minimum": 0, "required": false},
    "customer_email": {"type": "string", "max_length": 320, "required": false, "sensitive": true},
    "screenshot": {"type": "image", "required": false}
  },
  "state": {"customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
            "message": "{{customer_message}}"},
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"}
  },
  "model": "laya",
  "settings": {"act_threshold": 0.9, "temperature": 1.1,
               "questions": {"churn_risk": {"act_threshold": 0.7}},
               "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}},
  "extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]}
}'
```

The response is `201 Created`, with the headers `ETag: "1"` and `Location: /v1/studio/templates/support-triage`. The body is exactly the object in section 2.1 (`"change": "created"`).

Running the same command again returns `200` with the same body, `"change": "unchanged"` and `ETag: "1"`, and creates no version.

- The `{{account_tier}}` placeholder in `urgency.instructions` is allowed because `account_tier` is a closed `enum` (section 4.1).
- An existing `/v1/systemone` `questions` value pastes in unchanged.

#### Call 2: run the template with two variables (the everyday call)

```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.",
                "account_tier": "enterprise"}
}'
```

The response is `200`, with the headers `x-basal-decision-id: dec_01JB7Q2M4X9V3K8T6R1N5P0HZC`, `x-basal-stored: full`, `x-request-id: req_5d0c2b7e9a414f6c8e1f3a2b4c6d8e0f` and `Location: /v1/studio/decisions/dec_01JB7Q2M4X9V3K8T6R1N5P0HZC`.

```json
{
  "id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "object": "decision",
  "status": "completed",
  "created_at": 1790846043,
  "completed_at": 1790846043,
  "template": {"id": "support-triage", "version": 1, "ref": "support-triage", "resolved_from": "latest", "attribution": "explicit"},
  "model": "laya",
  "model_requested": null,
  "model_revision": "3c1f0a9d",
  "extensions": {"questions": [], "options": {}, "skipped": []},
  "answers": {
    "department": {"type": "choice", "choice": "billing",
                   "probabilities": {"billing": 0.9612, "technical": 0.0141, "sales": 0.0107, "other": 0.014},
                   "confidence": 0.9483, "decision": "billing", "top_probability": 0.9612,
                   "certainty": 0.9612, "act": true, "origin": "template"},
    "urgency": {"type": "score", "score": 2.61,
                "legend": {"0": "can wait", "1": "soon", "2": "today", "3": "blocking or at risk of churn"},
                "probabilities": {"0": 0.02, "1": 0.08, "2": 0.17, "3": 0.73},
                "confidence": 0.61, "decision": "3", "top_probability": 0.73,
                "certainty": 0.73, "act": false, "origin": "template"},
    "churn_risk": {"type": "noul", "noul": 0.9431, "probabilities": {"false": 0.0569, "true": 0.9431},
                   "decision": "yes", "top_probability": 0.9431, "certainty": 0.9431, "act": true, "origin": "template"}
  },
  "act": false,
  "needs_review": ["urgency"],
  "settings": {"act_threshold": 0.9, "temperature": 1.3,
               "questions": {"churn_risk": {"act_threshold": 0.7}, "urgency": {"temperature": 1.8}},
               "sources": {"act_threshold": "template", "temperature": "template.models.laya",
                           "questions.churn_risk.act_threshold": "template.questions",
                           "questions.urgency.temperature": "template.models.laya.questions"}},
  "usage": {"input_tokens": 142, "output_tokens": 0},
  "timing": {"queue_ms": 0.4, "load_ms": 0.0, "model_ms": 96.2, "total_ms": 126.0},
  "passes": 1,
  "notes": [],
  "warnings": [],
  "source": {"surface": "api", "endpoint": "/v1/studio/decisions", "format": "studio", "client": "curl",
             "request_id": "req_5d0c2b7e9a414f6c8e1f3a2b4c6d8e0f", "attempt": 0, "retry_of": null},
  "group": null,
  "batch": null,
  "eval": null,
  "rerun_of": null,
  "metadata": {},
  "pinned": false,
  "feedback": {},
  "store": "full",
  "expires_at": 1793438043,
  "error": null
}
```

The same call in Python:

```python
import httpx
studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio")   # headers={"Authorization": f"Bearer {key}"} when remote
d = studio.post("/decisions", json={"template": "support-triage",
                                    "variables": {"customer_message": msg, "account_tier": tier}}).raise_for_status().json()
if d["act"]:
    route_to(d["answers"]["department"]["choice"])     # the same field TypeSafe returns
else:
    send_to_review(d["id"], d["needs_review"])          # ["urgency"]
```

A mistake gets a precise answer. For example, `"acount_tier"` returns `400` with `code: "unknown_variable"`, `param: "variables.acount_tier"` and "Did you mean 'account_tier'?" (section 4.11).

#### Call 3: change the template, then promote the new version

```bash
curl -s -X PATCH http://127.0.0.1:8420/v1/studio/templates/support-triage -H 'If-Match: "1"' -d '{
  "note": "Ask about refunds; act a little sooner",
  "questions": {"wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}},
  "settings": {"act_threshold": 0.85}
}'
```

The response is `200`, with `ETag: "2"`. The merge patch keeps every other field, including `settings.questions` and `settings.models`:

```json
{
  "id": "support-triage", "object": "template", "name": "Support triage",
  "description": "Route inbound tickets, rate urgency, flag churn risk.", "origin": "user", "archived": false,
  "metadata": {"owner_team": "support-eng"}, "storage": "full", "retention_days": null,
  "aliases": {"latest": 2}, "examples_revision": 0, "examples_count": 0,
  "version": 2, "note": "Ask about refunds; act a little sooner", "content_hash": "sha256:77b3e1c9d0a5f412",
  "modalities": ["text", "image"],
  "variables": {
    "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000, "required": true, "sensitive": false, "trusted": false},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free", "required": false, "sensitive": false, "trusted": false},
    "open_invoices": {"type": "integer", "minimum": 0, "required": false, "sensitive": false, "trusted": false},
    "customer_email": {"type": "string", "max_length": 320, "required": false, "sensitive": true, "trusted": false},
    "screenshot": {"type": "image", "required": false, "sensitive": false, "trusted": false}
  },
  "state": {"customer": {"tier": "{{account_tier}}", "open_invoices": "{{open_invoices}}", "email": "{{customer_email}}"},
            "message": "{{customer_message}}"},
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the {{account_tier}} plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}
  },
  "model": "laya",
  "settings": {"act_threshold": 0.85, "temperature": 1.1,
               "questions": {"churn_risk": {"act_threshold": 0.7}},
               "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}},
  "extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
  "created_at": 1790845800, "updated_at": 1790847000, "last_used_at": 1790846043,
  "change": "new_version",
  "warnings": []
}
```

If another tab had already saved version 2, the response would instead be `412` with `code: "version_conflict"` and `current_version: 2`.

Next, promote version 2:

```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/support-triage/aliases/production -d '{"version": 2}'
```

```json
{"object": "template.alias", "template": "support-triage", "alias": "production", "version": 2,
 "previous_version": null, "updated_at": 1790847072}
```

Production code sends `"template": "support-triage@production"`. From now on, promoting a version is one `PUT`. `previous_version` lets a script notice that it raced another promotion.

#### Call 4: one decision using every option

This call uses:
- a pinned version and another model;
- an image through a media variable;
- a sensitive variable;
- an extra question, an added option and a skipped question;
- a request threshold, metadata and idempotency.

```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -H 'Idempotency-Key: 6c1b8f2e-2f0a-4b8e-9d51-1c6a4c3f7a10' -d '{
  "template": "support-triage@2",
  "model": "intern-decision-4b",
  "variables": {"customer_message": "The screen arrived cracked, photo attached. I want a replacement.",
                "account_tier": "pro", "customer_email": "ana@example.com",
                "screenshot": "file_01JB7PZ3YQ0M6T2C9W4K8D1R5V"},
  "questions": {"damage_visible": {"type": "noul", "instructions": "Does the photo show visible damage to the product?"}},
  "add_options": {"department": {"returns": "replacements and returns of damaged goods"}},
  "skip": ["churn_risk"],
  "settings": {"act_threshold": 0.8},
  "metadata": {"ticket_id": "T-4412", "channel": "email"},
  "include": ["input"]
}'
```

The `file_…` id came from `POST /v1/studio/files` (section 3.7). The response is `200`, with the headers `x-basal-decision-id: dec_01JB7RQ4H6K8M0P2R4T6V8X0Z2` and `x-basal-stored: full`.

```json
{
  "id": "dec_01JB7RQ4H6K8M0P2R4T6V8X0Z2",
  "object": "decision",
  "status": "completed",
  "created_at": 1790848330,
  "completed_at": 1790848330,
  "template": {"id": "support-triage", "version": 2, "ref": "support-triage@2", "resolved_from": "pinned", "attribution": "explicit"},
  "model": "intern-decision-4b",
  "model_requested": "intern-decision-4b",
  "model_revision": "a81d2c7e",
  "input": {
    "variables": {"customer_message": "The screen arrived cracked, photo attached. I want a replacement.", "account_tier": "pro",
                  "customer_email": {"$redacted": "hmac-sha256:7c1e0b4f9a2d6e83"}, "screenshot": "file_01JB7PZ3YQ0M6T2C9W4K8D1R5V"},
    "state": {"customer": {"tier": "pro", "email": "[redacted:customer_email]"},
              "message": "The screen arrived cracked, photo attached. I want a replacement."},
    "media": [{"type": "image", "file_id": "file_01JB7PZ3YQ0M6T2C9W4K8D1R5V", "name": "cracked.jpg", "content_type": "image/jpeg",
               "bytes": 183422, "variable": "screenshot", "available": true}],
    "questions": {
      "department": {"type": "choice", "instructions": "Which department should handle this request?",
                     "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                  "sales": "pricing, upgrades, new contracts", "other": "anything else",
                                  "returns": "replacements and returns of damaged goods"}},
      "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the pro plan.",
                  "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
      "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"},
      "damage_visible": {"type": "noul", "instructions": "Does the photo show visible damage to the product?"}
    }
  },
  "extensions": {"questions": ["damage_visible"], "options": {"department": ["returns"]}, "skipped": ["churn_risk"]},
  "answers": {
    "department": {"type": "choice", "choice": "returns",
                   "probabilities": {"billing": 0.06, "technical": 0.03, "sales": 0.01, "other": 0.02, "returns": 0.88},
                   "confidence": 0.85, "decision": "returns", "top_probability": 0.88, "certainty": 0.88, "act": true, "origin": "extended"},
    "urgency": {"type": "score", "score": 1.3,
                "legend": {"0": "can wait", "1": "soon", "2": "today", "3": "blocking or at risk of churn"},
                "probabilities": {"0": 0.1, "1": 0.55, "2": 0.3, "3": 0.05},
                "confidence": 0.5, "decision": "1", "top_probability": 0.55, "certainty": 0.55, "act": false, "origin": "template"},
    "wants_refund": {"type": "noul", "noul": 0.12, "probabilities": {"false": 0.88, "true": 0.12},
                     "decision": "no", "top_probability": 0.88, "certainty": 0.88, "act": true, "origin": "template"},
    "damage_visible": {"type": "noul", "noul": 0.97, "probabilities": {"false": 0.03, "true": 0.97},
                       "decision": "yes", "top_probability": 0.97, "certainty": 0.97, "act": true, "origin": "extra"}
  },
  "act": false,
  "needs_review": ["urgency"],
  "settings": {"act_threshold": 0.8, "temperature": 1.1, "questions": {},
               "sources": {"act_threshold": "request", "temperature": "template"}},
  "usage": {"input_tokens": 318, "output_tokens": 0},
  "timing": {"queue_ms": 0.6, "load_ms": 0.0, "model_ms": 212.4, "total_ms": 251.9},
  "passes": 1,
  "notes": [],
  "warnings": [],
  "source": {"surface": "api", "endpoint": "/v1/studio/decisions", "format": "studio", "client": "curl",
             "request_id": "req_2b9e4f6a8c0d4e1fa3b5c7d9e1f3a5b7", "attempt": 0, "retry_of": null},
  "group": null,
  "batch": null,
  "eval": null,
  "rerun_of": null,
  "metadata": {"ticket_id": "T-4412", "channel": "email"},
  "pinned": false,
  "feedback": {},
  "store": "full",
  "expires_at": 1793440330,
  "error": null
}
```

Things to notice:
- **The request's threshold wins.** The request's `act_threshold: 0.8` beats the template's 0.85 and every per-question template threshold, because a request value beats any template value (section 4.3).
- **Temperature comes from the template.** `intern-decision-4b` has no `settings.models` entry, so its temperature is the template's own 1.1.
- **The sensitive value is never stored.** The model saw `ana@example.com`; history keeps only an HMAC, which still lets you erase by value (section 3.3).
- **The same key replays.** Sending the same `Idempotency-Key` again returns this object with `x-basal-idempotent-replayed: true`.
- **Model compatibility is checked up front.** Sending the image to `laya` instead would fail before any model loads, with `400 model_incompatible` and the detail `modality_not_supported`.

#### Call 5: browse this template's history, compare versions, close the loop

First, the review queue:

```bash
curl -s 'http://127.0.0.1:8420/v1/studio/templates/support-triage/decisions?act=false&created_after=-7d&limit=2'
```

```json
{
  "object": "list",
  "data": [
    {"id": "dec_01JB7RQ4H6K8M0P2R4T6V8X0Z2", "object": "decision.summary", "status": "completed", "created_at": 1790848330,
     "template": {"id": "support-triage", "version": 2}, "model": "intern-decision-4b",
     "source": {"surface": "api", "client": "curl", "attempt": 0}, "extended": true,
     "act": false, "needs_review": ["urgency"],
     "answers": {"department": {"type": "choice", "decision": "returns", "certainty": 0.88, "act": true},
                 "urgency": {"type": "score", "decision": "1", "certainty": 0.55, "act": false},
                 "wants_refund": {"type": "noul", "decision": "no", "certainty": 0.88, "act": true},
                 "damage_visible": {"type": "noul", "decision": "yes", "certainty": 0.97, "act": true}},
     "timing": {"total_ms": 251.9}, "metadata": {"ticket_id": "T-4412", "channel": "email"},
     "pinned": false, "labelled": false, "error": null},
    {"id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC", "object": "decision.summary", "status": "completed", "created_at": 1790846043,
     "template": {"id": "support-triage", "version": 1}, "model": "laya",
     "source": {"surface": "api", "client": "curl", "attempt": 0}, "extended": false,
     "act": false, "needs_review": ["urgency"],
     "answers": {"department": {"type": "choice", "decision": "billing", "certainty": 0.9612, "act": true},
                 "urgency": {"type": "score", "decision": "3", "certainty": 0.73, "act": false},
                 "churn_risk": {"type": "noul", "decision": "yes", "certainty": 0.9431, "act": true}},
     "timing": {"total_ms": 126.0}, "metadata": {}, "pinned": false, "labelled": false, "error": null}
  ],
  "first_id": "dec_01JB7RQ4H6K8M0P2R4T6V8X0Z2",
  "last_id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
  "has_more": true
}
```

The next page is `…&after=dec_01JB7Q2M4X9V3K8T6R1N5P0HZC`.

Second, compare versions:

```bash
curl -s 'http://127.0.0.1:8420/v1/studio/templates/support-triage/stats?group_by=version&created_after=-7d'
```

```json
{
  "object": "decision.stats",
  "group_by": ["version"],
  "filters": {"template": "support-triage", "created_after": 1790241243, "attribution": ["explicit", "header"]},
  "what_if": null,
  "groups": [
    {"key": {"version": 1}, "count": 3120, "failed": 4, "act_rate": 0.71, "latency_ms": {"p50": 44.1, "p95": 71.8},
     "questions": {
       "department": {"comparability": "reference", "runs": 3120, "excluded_runs": 0, "mean_certainty": 0.91, "act_rate": 0.83,
                      "distribution": {"billing": 0.41, "technical": 0.33, "sales": 0.12, "other": 0.14}, "labelled": 40, "accuracy": 0.95},
       "churn_risk": {"comparability": "reference", "runs": 3120, "excluded_runs": 0, "mean_certainty": 0.9, "act_rate": 0.9,
                      "distribution": {"yes": 0.08, "no": 0.92}, "labelled": 40, "accuracy": 0.975}}},
    {"key": {"version": 2}, "count": 1877, "failed": 1, "act_rate": 0.79, "latency_ms": {"p50": 45.0, "p95": 73.2},
     "questions": {
       "department": {"comparability": "identical", "runs": 1841, "excluded_runs": 36, "mean_certainty": 0.92, "act_rate": 0.86,
                      "distribution": {"billing": 0.4, "technical": 0.35, "sales": 0.11, "other": 0.14}, "labelled": 12, "accuracy": 0.917},
       "churn_risk": {"comparability": "identical", "runs": 1702, "excluded_runs": 175, "mean_certainty": 0.91, "act_rate": 0.91,
                      "distribution": {"yes": 0.07, "no": 0.93}, "labelled": 9, "accuracy": 1.0},
       "wants_refund": {"comparability": "added", "runs": 1877, "excluded_runs": 0, "mean_certainty": 0.89, "act_rate": 0.88,
                        "distribution": {"yes": 0.22, "no": 0.78}, "labelled": 0, "accuracy": null}}}
  ]
}
```

`excluded_runs` counts answers that stats leave out by default: answers whose options were extended by the caller, and questions that were skipped. The stats group is trimmed to two questions here for brevity; a real response lists every question.

Third, label the decision that needed review, and turn it into a test case:

```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions/dec_01JB7Q2M4X9V3K8T6R1N5P0HZC/feedback \
  -d '{"expected": {"urgency": "3"}, "note": "Confirmed on the call: they will cancel if not refunded today."}'
```

```json
{"object": "list", "data": [
  {"id": "fb_01JB7W2E4G6J8K0M2N4P6Q8R0S", "object": "feedback", "decision_id": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC",
   "question": "urgency", "expected": "3", "predicted": "3", "correct": true, "rating": null,
   "note": "Confirmed on the call: they will cancel if not refunded today.", "actor": "local",
   "created_at": 1790852580, "updated_at": 1790852580}],
 "first_id": "fb_01JB7W2E4G6J8K0M2N4P6Q8R0S", "last_id": "fb_01JB7W2E4G6J8K0M2N4P6Q8R0S", "has_more": false}
```

```bash
curl -s http://127.0.0.1:8420/v1/studio/templates/support-triage/examples \
  -d '{"from_decision": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC", "expected": {"department": "billing", "churn_risk": "yes"}, "tags": ["from-review", "regression"]}'
```

The response is the example object in section 2.5. Its `expected` merges the decision's feedback (`urgency: "3"`) with the labels sent in the body; body values win.

### 3.2 Templates

**`POST /templates`: create.** The body is the template fields (section 2.1) plus `id` and optional `note`.

| Situation | Result |
|---|---|
| New id | `201`, `change: "created"` |
| The id exists and the definition is identical | `200`, `change: "unchanged"` |
| The id exists and the definition differs | `409 template_exists`; the message points to `PUT`/`PATCH` |
| The id is held by a deleted template whose history was kept | `409 template_id_reserved` |

Instead of definition fields, the body may carry `from`, and any definition fields in the same body override the copied ones:
- `{"from": {"template": "builtin/support@1"}}` clones a template's definition. Its head fields (name, metadata) are not copied.
- `{"from": {"decision": "dec_…"}}` seeds a template from a stored decision:
  - Version 1 copies the decision's effective questions, model and settings.
  - `modalities` is inferred from its media.
  - A text state becomes one variable `message` (a `string`) with the state template `"{{message}}"`.
  - A JSON state becomes one variable per top-level key, with the inferred type, and a JSON state template.
  - The History page's "Save as template" uses this.

**`GET /templates`: list.**
- Filters:
  - `q` searches id, name and description.
  - `origin=user|builtin`.
  - `include_archived=true`.
  - `metadata.<k>=v`.
  - `compatible_with=<model id>` keeps only templates that model can run.
- Items are template objects **without** the definition fields, unless `include=definition` is sent.
- Pagination: `after` takes a template id; items are ordered by `updated_at` descending.

**`GET /templates/{id}`: retrieve.**
- `?version=3` or `?version=production` returns another version's definition.
- `include=compatibility` adds the compatibility report.
- The response carries `ETag: "<latest version>"`.
- A deleted template returns `404 template_not_found`, whose message gives the deletion date; `?include_deleted=true` returns it with `"deleted_at"`.

**`PUT /templates/{id}`: idempotent upsert.**
- It returns `201 created`, `200 new_version` or `200 unchanged`.
- Definition fields left out of the body go back to their defaults.
- Head fields left out keep their values.
- `If-Match` is optional (section 4.4).

**`PATCH /templates/{id}`: update.**
- An RFC 7396 merge patch over the template object. `application/json` and `application/merge-patch+json` are both accepted.
- Maps merge, so `{"questions": {"department": {"criteria": {"legal": "contracts"}}}}` adds one option.
- `null` deletes, so `{"questions": {"churn_risk": null}}` removes a question.
- Arrays are replaced whole.
- A definition change creates a version (`new_version`). A head-only change returns `metadata_only`.
- `archived` is read-only here (`400 read_only_field`); use `/archive`.

**`POST /templates/{id}/versions`: save a new version.** This is the explicit "new version" call.
- The body is a full definition (every definition field, section 2.1) plus optional `note` and `base_version`.
- `base_version` is an alternative to `If-Match`. A stale value returns `412 version_conflict` with `current_version`.
- It returns the `template.version` object (section 2.2), with `200` and `x-basal-version-created: true|false`.
- The template must exist (`404` otherwise) and must not be archived (`409 template_archived`; unarchive first).

**`GET /templates/{id}/versions`**
- Returns `template.version` objects without definition fields, unless `include=definition` is sent.
- Newest first; `after` takes a version number.

**`GET /templates/{id}/versions/{n}`** takes a number, an alias or `latest`.

**`GET /templates/{id}/versions/{n}/diff?against={m}`** (`against` defaults to n−1):

```json
{
  "object": "template.diff", "template": "support-triage", "from": 2, "to": 3, "class": "extended", "breaking_for_callers": false,
  "questions": {"department": {"comparability": "options_changed", "added_options": ["account"], "removed_options": []},
                "urgency": {"comparability": "identical"}, "churn_risk": {"comparability": "identical"},
                "wants_refund": {"comparability": "identical"}},
  "variables": {},
  "settings": {},
  "model": {"from": "laya", "to": "laya"},
  "modalities": {"from": ["text", "image"], "to": ["text", "image"]},
  "changes": [{"op": "add", "path": "/questions/department/criteria/account", "value": "login, seats, access"}]
}
```

`variables` and `settings` list changed paths as `{"<path>": {"from": …, "to": …}}`. `changes` is the full RFC 6902-style operation list.

**`POST /templates/{id}/versions/{n}/restore`**
- Body: `{"note": "Back to v2"}`.
- Creates version latest+1 with version n's definition, `source: "restore"`. History stays linear.

**`PUT /templates/{id}/aliases/{alias}`**
- Body: `{"version": 2}`. Returns the `template.alias` object shown in call 3.
- `DELETE` removes the alias and returns `{"id": "production", "object": "template.alias.deleted", "deleted": true}`.
- Aliases are last-write-wins. Every change writes an audit event.

**`POST /templates/{id}/archive`** and **`/unarchive`**
- Return the template object with `archived` set.
- Archived templates stay callable: every decision made with one gets a `template_archived` warning.
- Built-ins return `403 template_read_only`.

**`DELETE /templates/{id}?confirm=support-triage&history=keep|delete`**
- Without a `confirm` value equal to the id, the call returns `400 confirmation_required`.
- `history=keep` (the default):
  - Deletes the head, aliases, examples and eval runs.
  - Keeps every version that a stored decision references, read-only, so history still shows "the exact version used".
  - Retained decisions keep `template: {"id", "version", …}`.
  - The id stays reserved while any retained decision references it.
- `history=delete` also deletes every decision of the template, then all its versions, and frees the id.
- Calls to a deleted template return `404 template_not_found`. This is the only operation that breaks pinned callers.
- Response: `{"id": "support-triage", "object": "template.deleted", "deleted": true, "versions_kept": 3, "decisions_deleted": 0}`.

**`GET /templates/{id}/schema?version=production`**: JSON Schema 2020-12 of the variables, for forms and SDK code generation.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "support-triage@2 variables",
  "type": "object",
  "additionalProperties": false,
  "required": ["customer_message"],
  "properties": {
    "customer_message": {"type": "string", "maxLength": 8000, "description": "The message, verbatim."},
    "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"},
    "open_invoices": {"type": "integer", "minimum": 0},
    "customer_email": {"type": "string", "maxLength": 320, "x-basal-sensitive": true},
    "screenshot": {"type": "string", "pattern": "^(file_[0-9A-HJKMNP-TV-Z]{26}|data:image/)", "x-basal-type": "image"}
  },
  "x-basal-template": "support-triage@2",
  "x-basal-questions": ["department", "urgency", "churn_risk", "wants_refund"],
  "x-basal-extensions": {"questions": true, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]}
}
```

**`GET /templates/{id}/compatibility?version=production`** checks every catalog model.

```json
{
  "object": "template.compatibility", "template": "support-triage", "version": 2,
  "models": [
    {"model": "laya", "status": "loaded", "ok": true, "problems": [],
     "notes": ["Cannot read the optional image variable 'screenshot'; decisions that send it will be refused."]},
    {"model": "intern-decision-4b", "status": "downloaded", "ok": true, "problems": [], "notes": []},
    {"model": "kev-0.5b", "status": "not_downloaded", "ok": false,
     "problems": [{"code": "context_too_small", "param": "variables.customer_message",
                   "message": "customer_message allows 8,000 characters (about 2,000 tokens); Kev 0.5B reads 512 tokens."}], "notes": []}
  ]
}
```

- `status` values are those of the model object (section 3.7).
- Problem codes:
  - `type_not_supported`
  - `too_many_options`
  - `too_many_questions`
  - `modality_not_supported` (for **required** media variables only)
  - `context_too_small` (estimated from the variables' `max_length`)
- `ok` is false when there is any problem.

### 3.3 Decisions

**`POST /decisions`: create.**

| Field | Type | Default | Rules |
|---|---|---|---|
| `template` | string reference | none | Omit it for an ad hoc decision. |
| `variables` | object | none | Needs a template that declares variables (`400 variables_need_template` otherwise). Validated per section 4.1. |
| `state` | string, object or array | none | Ad hoc: required and not null (TypeSafe's rule). With a template: required when it declares no variables; `400 state_not_allowed` when it does. |
| `questions` | map | none | Ad hoc: all the questions (at least one; the six types). With a template: **extra** questions, new keys only (section 4.2). |
| `add_options` | map of question key to criteria (map or list; a list of numbers for `number`) | none | Template only (section 4.2). |
| `skip` | list of question keys | `[]` | Template only (section 4.2). |
| `media` | array | `[]` | `{type?, file_id}`, `{type?, data: "data:<mime>;base64,…"}`, or the legacy `{type?, path: "<upload id>"}`, plus an optional `name`. `type` is inferred when missing. Must be in the template's `modalities`. |
| `model` | string | section 4.3 | A studio id, a Hugging Face repo id, a model name, or an alias (`jev-latest`, `default`, `auto`, …), exactly as `_route` accepts them today. |
| `settings` | Settings object | none | Request layer (section 4.3). |
| `metadata` | map | `{}` | Section 2.0. |
| `store` | bool or `"full"`, `"answers_only"`, `"none"` | `true` (= `full`) | Can only lower storage (section 4.5). `false` means `none`. |
| `background` | bool | `false` | Returns at once with `status: "queued"`. Needs storage other than `none`. |
| `stream` | bool | `false` | Server-sent events (section 4.10, phase 3). |
| `include` | list | `[]` | `input`, `input.rendered_state`, `answers.raw_probabilities`, `answers.model_extras`. |

Unknown top-level fields are ignored, with the warning `unknown_field_ignored`. This keeps the systemone-body guarantee, since TypeSafe SDK users send extra keys through `extra_body`. OpenRouter's `user` and `session_id` become `metadata.openrouter.user` and `metadata.openrouter.session_id`, as on the wire.

**`POST /decisions/preview`**
- Takes the full create body and runs steps 1 to 8 of section 4.3: everything except the model call and storage.
- Returns the same 400 errors as create, with every problem listed in `details`, so editors can mark all fields at once.

```json
{
  "object": "decision.preview",
  "template": {"id": "support-triage", "version": 2, "ref": "support-triage@production", "resolved_from": "alias", "attribution": "explicit"},
  "model": "laya",
  "state": {"customer": {"tier": "enterprise"}, "message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel."},
  "rendered_state": "customer:\n  tier: enterprise\nmessage: We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.",
  "questions": {
    "department": {"type": "choice", "instructions": "Which department should handle this request?",
                   "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
    "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the enterprise plan.",
                "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
    "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
    "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}
  },
  "extensions": {"questions": [], "options": {}, "skipped": []},
  "media": [],
  "settings": {"act_threshold": 0.85, "temperature": 1.3,
               "questions": {"churn_risk": {"act_threshold": 0.7}, "urgency": {"temperature": 1.8}},
               "sources": {"act_threshold": "template", "temperature": "template.models.laya",
                           "questions.churn_risk.act_threshold": "template.questions",
                           "questions.urgency.temperature": "template.models.laya.questions"}},
  "usage": {"input_tokens": 139},
  "systemone_request": {"model": "laya",
                        "state": {"customer": {"tier": "enterprise"}, "message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel."},
                        "questions": {"department": {"type": "choice", "instructions": "Which department should handle this request?",
                                                     "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages, system errors",
                                                                  "sales": "pricing, upgrades, new contracts", "other": "anything else"}},
                                      "urgency": {"type": "score", "instructions": "How urgent is this request? The customer is on the enterprise plan.",
                                                  "criteria": ["can wait", "soon", "today", "blocking or at risk of churn"]},
                                      "churn_risk": {"type": "noul", "instructions": "Does the customer threaten to cancel or leave?"},
                                      "wants_refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}},
                        "settings": {"temperature": 1.3}},
  "warnings": [{"code": "per_question_settings_not_portable", "param": "settings.questions.urgency.temperature",
                "message": "/v1/systemone takes one temperature per request; systemone_request uses 1.3 for every question."}]
}
```

- `usage.input_tokens` is an estimate (the model's tokenizer is not loaded).
- `systemone_request` is the equivalent `/v1/systemone` body. It is portable to any TypeSafe-compatible service when the questions use only the three TypeSafe types.

**`GET /decisions/{id}`: retrieve.**
- Returns the object in section 2.3, with `input` included by default.
- Other `include` values work as on create.
- `?wait=N` (N ≤ 60 seconds) holds the request until the decision reaches a terminal status or N seconds pass, then returns it. It works from curl.
- `?stream=true&starting_after=<sequence_number>` resumes an SSE stream (phase 3).
- `?format=typesafe|openrouter|vercel|evaluate` re-renders the stored answers as that wire route would have answered, through `api_compat.shape`. It is strict unless `&extended=true`. Use it to migrate stored traffic into TypeSafe-shaped tooling.
- `404 decision_not_found` covers unknown, deleted and `store:"none"` decisions. The message says when a decision was not stored.

**`GET /decisions/{id}/input`** returns a `decision.input` object: `{"object": "decision.input", "decision_id": …}` plus the fields of the decision's `input`. It returns `409 input_unavailable` when the input was not stored or was redacted.

**`GET /decisions`: history.** The same parameters work on `/templates/{id}/decisions` (with the template fixed), `/decisions/stats`, `/decisions/export`, and the `filter` of bulk operations, batches and evals.

| Parameter | Example | Meaning |
|---|---|---|
| `template` | `support-triage`, `support-triage@2`, `support-triage@production`, `none` | A bare id matches all versions. `@N` or `@alias` matches one version; aliases resolve at query time. `none` matches ad hoc decisions. |
| `version` | `2,3` | With `template`. |
| `attribution` | `explicit,header` | Default when `template` is set: `explicit,header`. `all` includes `inferred` and `draft`. |
| `model`, `model_revision` | `laya,kev-4b` | The resolved model. A comma means OR in every filter. |
| `status` | `failed` | |
| `surface` | `api,playground` | Default: everything except `eval`, `order_test` and `replay`. `all` includes them. |
| `endpoint`, `format`, `client` | `/v1/systemone`, `typesafe`, `TypeSafe Python SDK` | Exact match. |
| `created_after`, `created_before` | `-7d`, `2026-09-24T00:00:00Z`, `1790208000` | |
| `act` | `false` | The review queue. |
| `needs_review` | `urgency` | Decisions where that question did not act. |
| `answer.<q>` | `answer.department=billing,sales`, `answer.churn_risk=yes`, `answer.topics=bug` | Matches the canonical `decision` value (input aliases accepted). For `multi` it means "selected contains". Question keys outside `[A-Za-z0-9_-]` are percent-encoded. |
| `certainty_below.<q>`, `certainty_above.<q>` | `certainty_below.department=0.7` | Low-confidence triage. |
| `extended` | `true` | The caller added or skipped anything. |
| `labelled`, `correct` | `labelled=true`, `correct=false` | Feedback. `correct=false` means any question labelled wrong. |
| `pinned` | `true` | |
| `metadata.<k>` | `metadata.ticket_id=T-4411` | Exact match. |
| `q` | `invoice 4411` | Full-text search over the stored state and non-sensitive string variables (FTS5). Returns `400 search_unavailable` on SQLite builds without FTS5. |
| `group`, `batch`, `eval`, `rerun_of`, `request_id`, `questions_hash`, `question_set_key`, `input_hash` | `request_id=req_…` | Structural links. |
| `fold_retries` | `true` (default) | Hides attempts superseded by an SDK retry (section 4.9). |
| `view` | `summary` (default) or `full` | `full` returns decision objects; `input` only with `include=input`. |
| `include` | `input` | With `view=full`. |
| `limit`, `after`, `before`, `order` | | Section 2.0. |

Items are always ordered by creation time; any other sort would break stable cursors. For "least certain first", filter with `certainty_below`.

**`PATCH /decisions/{id}`**
- Body: `{"metadata": {"reviewed_by": "ops"}, "pinned": true}`. `metadata` merges.
- Any other field returns `400 read_only_field`: answers, inputs and settings never change.

**`DELETE /decisions/{id}`**
- Hard delete of the decision, its body, answers, metadata, feedback, full-text entry and media links. Blobs that nothing else references are collected by the sweeper.
- Returns `{"id": "dec_…", "object": "decision.deleted", "deleted": true}`.
- Writes an audit event with the id and actor, never the content.

**`POST /decisions/{id}/redact`**
- Body: `{"fields": ["input.state", "input.variables.customer_message", "input.media", "metadata.ticket_id"]}`.
- Replaces each field with `"[redacted]"`, deletes media links and the full-text entry, and records the paths in the body's `redacted` list.
- Answers, raw probabilities and settings stay, so stats still work.
- Returns the decision.

**`POST /decisions/delete`** and **`POST /decisions/redact`** (bulk)

```json
{"filter": {"template": "support-triage", "created_before": "2026-09-01T00:00:00Z",
            "sensitive_hash": {"customer_email": "ana@example.com"}},
 "dry_run": true, "include_pinned": false}
```

- Response: `{"object": "decision.bulk_delete", "matched": 37, "deleted": 0, "skipped_pinned": 2, "dry_run": true}`. The bulk redact response has `"object": "decision.bulk_redact"` and `redacted` in place of `deleted`, and its body adds `fields`.
- `sensitive_hash` is hashed with the install's HMAC key and matched against stored sensitive-variable hashes. This is how you erase "everything containing this email" without the studio ever storing the email.
- A body with an empty filter must say `"all": true`, otherwise `400 filter_required`.
- The legacy `DELETE /api/history` maps to `{"all": true, "include_pinned": false}`.

**`POST /decisions/{id}/cancel`**
- Works while a background decision is `queued` or waiting for a model load. It returns the decision with `status: "cancelled"`.
- A worker call already in flight (about 100 ms) completes.
- A finished decision returns `409 decision_finished`.

**`POST /decisions/{id}/rerun`**
- Every field is optional: `{"model": "kev-4b", "template": "support-triage@3", "settings": {"temperature": 1.0}, "variables": {"customer_email": "ana@example.com"}, "store": "full"}`.
- It creates a new decision with `rerun_of` set and `source.surface: "rerun"`, reusing the original's variables (or state), media and extensions. `variables` sent in the body are merged over the stored ones, which is how sensitive values are supplied again.
- `409 input_unavailable` when the stored input is incomplete (answers only, redacted, media bytes gone, or a sensitive value not supplied again).

**`GET /decisions/stats`** takes the list filters plus:
- `group_by`: any of `version`, `model`, `model_revision`, `surface`, `client`, `format`, `status`, `day`, `hour`.
- `questions`: which questions to summarise.
- `include_extended=true`: count answers with added options and extra questions too.
- `what_if.temperature` and `what_if.act_threshold`: recompute from stored raw probabilities.

The response shape is in section 3.1, call 5. Group fields:
- `count`, `failed`, `act_rate`, `latency_ms` (`p50`, `p95` of `total_ms`).
- `questions.<q>`:
  - `comparability`: relative to the first group; only when grouping by `version`.
  - `runs`, `excluded_runs`, `mean_certainty`, `act_rate`.
  - `distribution`: the share of each `decision` value. Excluded for `dynamic_options` answers.
  - `shared_distribution`: renormalised over options present in both versions; only for `options_changed`.
  - `labelled`, `accuracy`.
- `what_if`.

**`GET /decisions/export?format=jsonl|csv&<filters>`**
- Streams every match without pagination.
- JSONL writes full decision objects (`include` applies).
- CSV has the columns `id, created_at, template, version, model, status, act`, then `<q>`, `<q>.certainty` and `<q>.act` per question, then `metadata.<k>`. CSV is best with a `template` filter.
- Every export writes an audit event with the filter and row count.

### 3.4 Feedback

**`POST /decisions/{id}/feedback`**
- Body: `{"expected": {"<q>": <label>, …}, "rating": 1 | -1 | null, "note": "…"}`. At least one of `expected` and `rating` is required. A `rating` without `expected` is stored with `question: "*"`.
- Returns a `list` of `feedback` objects, one per question written (section 3.1, call 5).
- `400 invalid_expected` (with `param: "expected.<q>"`) when a label is not a valid answer for that decision's effective question.
- Feedback on a `none` decision is impossible, because there is no record. Callers who need feedback without keeping content use `answers_only`.

**`GET /decisions/{id}/feedback`** lists all feedback, from every actor. The decision's `feedback` field shows the latest per question.

**`DELETE /feedback/{id}`** returns `{"id": "fb_…", "object": "feedback.deleted", "deleted": true}`.

### 3.5 Per-template history and version comparison

- **`GET /templates/{id}/decisions`** is `GET /decisions` with `template={id}` fixed. `version=` narrows it, and `attribution` defaults to `explicit,header`.
- **`GET /templates/{id}/stats`** is `GET /decisions/stats` with the template fixed. Its `group_by` defaults to `version`.

What-if example:

```bash
curl -s 'http://127.0.0.1:8420/v1/studio/templates/support-triage/stats?group_by=version&version=2&what_if.act_threshold=0.8'
```

Each group then carries `"what_if": {"act_threshold": 0.8, "temperature": null, "act_rate": 0.84, "accuracy_when_acting": 0.97, "labelled_acting": 41}`, computed from raw probabilities, the stored temperatures and feedback. Temperature what-ifs recompute probabilities from `raw_probabilities`.

**`GET /templates/{id}/compare?versions=2,3&model=laya&created_after=-30d`**

```json
{
  "object": "template.comparison",
  "template": "support-triage",
  "versions": [2, 3],
  "filters": {"model": ["laya"], "created_after": 1788254043, "attribution": ["explicit", "header"]},
  "questions": {
    "urgency": {
      "comparability": "identical",
      "by_version": {
        "2": {"n": 4210, "distribution": {"0": 0.12, "1": 0.31, "2": 0.33, "3": 0.24}, "mean_certainty": 0.71, "act_rate": 0.38,
              "feedback": {"labelled": 120, "accuracy": 0.83}},
        "3": {"n": 3902, "distribution": {"0": 0.11, "1": 0.30, "2": 0.35, "3": 0.24}, "mean_certainty": 0.74, "act_rate": 0.43,
              "feedback": {"labelled": 96, "accuracy": 0.86}}},
      "paired": {"n": 312, "agreement": 0.94, "flips": [{"from": "2", "to": "3", "n": 11}],
                 "sample": [["dec_01JB6ZK2M4P6R8T0V2X4Z6B8D0", "dec_01JB7V4A6C8E0G2J4K6M8N0P2Q"]]}},
    "department": {
      "comparability": "options_changed",
      "added_options": ["account"], "removed_options": [],
      "by_version": {
        "2": {"n": 4210, "distribution": {"billing": 0.44, "technical": 0.28, "sales": 0.17, "other": 0.11}},
        "3": {"n": 3902, "distribution": {"billing": 0.41, "technical": 0.25, "sales": 0.16, "account": 0.09, "other": 0.09},
              "shared_distribution": {"billing": 0.4505, "technical": 0.2747, "sales": 0.1758, "other": 0.0989}}},
      "paired": {"n": 312, "agreement": 0.9, "flips": [{"from": "technical", "to": "account", "n": 19}, {"from": "other", "to": "account", "n": 9}]}}
  },
  "evals": {"2": {"eval": "evr_01JB7V3X5Z7B9D1F3H5K7M9P1R", "dataset_hash": "sha256:77a0c4e1b2d93f60", "accuracy": {"department": 0.917}},
            "3": {"eval": "evr_01JB7V3X5Z7B9D1F3H5K7M9P1R", "dataset_hash": "sha256:77a0c4e1b2d93f60", "accuracy": {"department": 0.958}}},
  "operational": {"2": {"n": 4210, "failed": 3, "latency_ms": {"p50": 112, "p95": 240}},
                  "3": {"n": 3902, "failed": 1, "latency_ms": {"p50": 118, "p95": 251}}}
}
```

- **`paired`**: for each version (and model), take the latest completed decision per `variables_hash` (or per `input_hash` for raw-state templates), join the versions on that hash, and compare the `decision` values.
  - `n` is the number of pairs; `agreement` is the share that agree.
  - `flips` counts value changes from the first version to the second.
  - `sample` lists up to 20 decision-id pairs.
- **`shared_distribution`**: the distribution renormalised over options that exist in both versions.
- **`evals`**: the latest eval results for each version, compared only when both come from the same `dataset_hash`, so the comparison is like for like.
- **`operational`**: counts, failures and latency per version.

If history has no pairs, compare versions on identical inputs with a replay batch (section 3.7) or an eval with a `history` dataset (section 3.6).

### 3.6 Test examples and eval runs

**Examples**
- **`GET /templates/{id}/examples`**
  - Filters: `tag`, `split`, `labelled`, `q`.
  - `revision=N` lists the set as it was at examples revision N.
- **`POST /templates/{id}/examples`**
  - Body: `{variables | state, media?, expected, tags?, split?, note?}`, or `{"from_decision": "dec_…", "expected": {…}?, "tags": […]}`.
  - Returns the `template.example` object.
  - A duplicate input (same `input_hash`) returns `409 example_exists`, with the existing id.
- **`PATCH /templates/{id}/examples/{ex}`** writes a new row: same `id`, new `revision`.
- **`DELETE /templates/{id}/examples/{ex}`** retires it. Both bump `examples_revision`.
- **`POST /templates/{id}/examples/import`**
  - Body: `{"examples": [ … up to 5,000 … ]}`, or `{"file_id": "file_…", "format": "jsonl" | "csv" | "tsv", "columns": {"text": "variables.customer_message", "label": "expected.department"}}` (the Evaluate page's formats).
  - Returns `{"object": "import_result", "created": 4998, "revision": 12, "failed": [{"index": 17, "error": {"code": "invalid_variable", "param": "variables.account_tier", "message": "account_tier must be one of free, pro, enterprise; got 'gold'."}}]}`.

**`POST /evals`: run a template's test examples on any models and versions.**

```json
{
  "template": "support-triage",
  "models": ["laya", "kev-4b"],
  "versions": [2, 3],
  "dataset": {"type": "examples", "tags": ["regression"]},
  "questions": ["department", "urgency", "churn_risk"],
  "error_budget": 0.05,
  "store": "full",
  "metadata": {"reason": "v3 candidate"}
}
```

**Targets.** `models` × `versions` gives the targets in model-major order: `(laya,2) (laya,3) (kev-4b,2) (kev-4b,3)`. Alternatives:
- `targets: [{"model", "version"}]` gives them explicitly (up to 8).
- Omitting both uses the template's default model on the latest version.
- `version` accepts aliases.

**Dataset types.**
- `examples`: `{tags?, split?, ids?, revision?}`. The default is the current `examples_revision`.
- `history`: `{filter: {…list filters…}, limit ≤ 5000, labels: "feedback"}`. Back-tests on real past inputs, using feedback as labels. Answers-only and redacted decisions are skipped and counted.
- `inline`: `{items: [{variables | state, media?, expected}], save_as_examples: false}`. This is the Evaluate page's paste flow, limited to 2,000 items.

Without a template, send `"definition": {"questions": {…}}` in place of `template`. Such evals cannot be applied.

**Other fields.**
- `questions`: the subset to score (default: all labelled questions).
- `error_budget`: the error rate used to suggest an act threshold.
- `store`:
  - `full` (default) or `answers_only`: every example × target is a decision with `source.surface: "eval"` and `eval: {id, example_id, target}`, hidden from default history and stats.
  - `none`: no decisions are written. Items keep only labels, predictions and raw probabilities (no inputs), and inline items keep nothing beyond the eval's life.

The response is `200` with the eval object, `status: "queued"`. `GET /evals/{id}?wait=60` returns it when done:

```json
{
  "id": "evr_01JB7V3X5Z7B9D1F3H5K7M9P1R",
  "object": "eval",
  "status": "completed",
  "template": "support-triage",
  "targets": [{"index": 0, "model": "laya", "version": 2}, {"index": 1, "model": "laya", "version": 3},
              {"index": 2, "model": "kev-4b", "version": 2}, {"index": 3, "model": "kev-4b", "version": 3}],
  "dataset": {"type": "examples", "examples_revision": 7, "count": 24, "skipped": 0, "dataset_hash": "sha256:77a0c4e1b2d93f60",
              "selector": {"tags": ["regression"]}},
  "questions": ["department", "urgency", "churn_risk"],
  "error_budget": 0.05,
  "store": "full",
  "progress": {"total": 96, "completed": 96, "failed": 0, "skipped": 0},
  "results": [
    {"target": 1, "latency_ms": {"p50": 41.0, "p95": 63.5}, "failed": 0,
     "questions": {
       "department": {"n": 24, "accuracy": 0.958, "log_loss": 0.21, "brier": 0.06, "ece": 0.04, "confident_mistakes": 0,
                      "fitted_temperature": 1.35, "ece_at_fitted": 0.021,
                      "suggested_act_threshold": 0.86, "coverage_at_threshold": 0.83, "error_rate_at_threshold": 0.05,
                      "confusion": {"billing": {"billing": 8, "technical": 0, "sales": 0, "account": 0, "other": 0}}},
       "urgency": {"n": 18, "accuracy": 0.667, "within_one": 0.944, "mae_levels": 0.39, "ece": 0.09, "fitted_temperature": 1.6}}}
  ],
  "comparisons": [
    {"question": "department", "model": "laya", "from_version": 2, "to_version": 3, "n": 24,
     "accuracy_delta": 0.041, "flips": {"fixed": 2, "broken": 1}, "mcnemar_p": 0.56}
  ],
  "output_file_id": null,
  "metadata": {"reason": "v3 candidate"},
  "created_at": 1790849100,
  "completed_at": 1790849109
}
```

`results` and `comparisons` above show only the laya rows, for brevity; a real response has one `results` entry per target and one comparison per model × question.

| Metric | Types | Definition |
|---|---|---|
| `accuracy` | all | Correct by the rules in section 2.3. |
| `brier`, `log_loss`, `ece` | choice, noul, score, rank | `ece` uses 10 bins on `top_probability`, as `evaluate.js` does. |
| `confident_mistakes` | same | Wrong with certainty ≥ 0.9. |
| `fitted_temperature`, `ece_at_fitted` | same | Minimises log loss over the stored `raw_probabilities` on a log grid 0.25–8 (61 steps, as `evaluate.js` does). Fitted on `calibration`-split examples when there are any. |
| `suggested_act_threshold`, `coverage_at_threshold`, `error_rate_at_threshold` | same | The lowest threshold that keeps errors under `error_budget`. |
| `within_one`, `mae_levels` | score | Within-one-level accuracy; mean absolute error in levels. |
| `f1`, `exact_match` | multi | Per option and exact-set. |
| `kendall_tau` | rank | Against full expected orderings. |
| `mae`, `within_range_rate` | number | Absolute error; share of expected values inside `range`. |
| `confusion` | choice, noul, score, rank | Counts by expected (row) and predicted (column) value. |
| `comparisons` | all | For each model, version-to-version on the same items. `fixed` counts items wrong before and right after; `broken` counts the opposite; `mcnemar_p` is the exact McNemar test. |

**`GET /evals/{id}/items?target=1&correct=false&question=department`** returns rows like `{"object": "eval.item", "example_id", "example_revision", "target", "decision_id", "status", "questions": {"department": {"expected": "technical", "predicted": "billing", "correct": false, "certainty": 0.71}}}`.

**`POST /evals/{id}/apply`**
- Body: `{"target": 1, "temperature": true, "act_threshold": true, "min_n": 20, "note": "Calibrated on 24 regression examples"}`.
- Under the target's model, it writes `settings.models.<model>.questions.<q>.temperature` and `.act_threshold` for each question with at least `min_n` labelled items. Values are model-specific, because they were fitted on that model.
- It saves a new version (`source: "eval_apply"`) and returns the `template.version`.
- Inline-definition evals return `400 eval_has_no_template`.

Evals can be cancelled (`POST /evals/{id}/cancel`) and deleted (`DELETE /evals/{id}`, which deletes their item decisions). Statuses are `queued`, `in_progress`, `completed`, `failed` and `cancelled`. Runs resume after a restart.

### 3.7 Comparisons, batches, files, models, settings

**`POST /comparisons`** takes a create body with `vary` in place of `model`:
- `{"models": [2–8 ids]}`
- `{"versions": [2–8]}`: needs a template; the same variables are rendered by each version.
- `{"option_order": {"runs": 2–10, "seed": 11}}`: shuffles `choice` and `rank` options (questions with 3 or more options) on every run after the first.

```json
{
  "id": "cmp_01JB7QX3F0R8T1V5N2K7M9D4HC", "object": "comparison", "created_at": 1790846200,
  "vary": {"models": ["laya", "kev-4b"]},
  "decisions": [
    {"id": "dec_01JB6ZK2M4P6R8T0V2X4Z6B8D0", "object": "decision.summary", "status": "completed", "created_at": 1790846200,
     "template": {"id": "support-triage", "version": 2}, "model": "laya", "source": {"surface": "compare", "client": "Studio", "attempt": 0},
     "extended": false, "act": true, "needs_review": [],
     "answers": {"department": {"type": "choice", "decision": "technical", "certainty": 0.93, "act": true}},
     "timing": {"total_ms": 51.2}, "metadata": {}, "pinned": false, "labelled": false, "error": null},
    {"id": "dec_01JB7V4A6C8E0G2J4K6M8N0P2Q", "object": "decision.summary", "status": "completed", "created_at": 1790846200,
     "template": {"id": "support-triage", "version": 2}, "model": "kev-4b", "source": {"surface": "compare", "client": "Studio", "attempt": 0},
     "extended": false, "act": false, "needs_review": ["department"],
     "answers": {"department": {"type": "choice", "decision": "technical", "certainty": 0.81, "act": false}},
     "timing": {"total_ms": 133.9}, "metadata": {}, "pinned": false, "labelled": false, "error": null}
  ],
  "agreement": {"department": {"agree": true, "values": {"laya": "technical", "kev-4b": "technical"}}},
  "stability": null
}
```

- Each run is a stored decision with `group: {"type": "comparison", "id": "cmp_…"}`, or `order_test` for `option_order`.
- Models run in parallel, one process each, as `/api/compare` does today.
- A model that fails appears with `status: "failed"` and its `error`; the others still return.
- `stability` is set for `option_order`: `{"department": {"same_winner": 5, "of": 5, "max_probability_shift": 0.031, "stable": true}}`.
- `GET /comparisons/{id}` rebuilds the comparison from the grouped decisions.

**`POST /batches`**

```json
{
  "template": "support-triage@production",
  "settings": {"act_threshold": 0.85},
  "metadata": {"job": "nightly-backfill"},
  "store": "answers_only",
  "items": [
    {"custom_id": "T-5001", "variables": {"customer_message": "Where is my refund?", "account_tier": "pro"}},
    {"custom_id": "T-5002", "variables": {"customer_message": "Login loops forever on Safari."}, "metadata": {"priority": "high"}}
  ]
}
```

- **Input.** Exactly one of:
  - `items` (up to 1,000 inline);
  - `input_file_id` (JSONL, one `{custom_id, …create body…}` per line, up to 50,000);
  - `replay: {"filter": {…list filters…}, "limit": ≤ 5000}`.
- **Items.** Top-level fields are defaults for every item. Items may set any create field except `store`, `background`, `stream` and `include`; their `settings` and `metadata` merge over the defaults. `custom_id` is up to 128 characters and unique within the batch.
- **Replay.** Each matched decision reruns with its stored variables (or state), media and extensions on the batch's `template` or `model`. It gets `rerun_of` and `source.surface: "replay"`.
- **Storage.** `store` may be `none`: then no decision rows are written, items are kept in memory only (a restart fails the batch as `interrupted`), and results exist only in the output file. `delete_output_after_download: true` removes that file on first download.

The response is `200`:

```json
{"id": "bat_01JB7T9V2C4E6G8J0K2N4Q6S8V", "object": "batch", "status": "queued", "template": "support-triage@production",
 "model": null, "input": {"type": "items", "count": 2}, "store": "answers_only",
 "counts": {"total": 2, "completed": 0, "failed": 0, "cancelled": 0},
 "output_file_id": null, "error_file_id": null, "created_at": 1790849400, "started_at": null, "completed_at": null,
 "expires_at": null, "metadata": {"job": "nightly-backfill"}}
```

- `input.type` is `items`, `file` or `replay`.
- `expires_at` is when the output file expires: 24 hours after completion when `store` is `none`, otherwise 30 days.
- `GET /batches/{id}?wait=60` long-polls.
- **`GET /batches/{id}/results`** pages rows (`{"custom_id", "decision_id", "status", "act", "answers": {q: {decision, certainty, act, probabilities}}, "error"}`); `?format=jsonl` streams them. The output file (`purpose: "batch_output"`) holds the same rows.
- Batch items run at `bulk` priority and yield to interactive calls between items (section 4.10). `POST /batches/{id}/cancel` stops the items that have not run.

**`GET /batches/{id}/comparison`** (replay batches only):

```json
{"object": "batch.comparison", "batch_id": "bat_01JB7T9V2C4E6G8J0K2N4Q6S8V", "pairs": 500,
 "baseline": "as recorded on each original decision", "candidate": {"template": "support-triage", "version": 3, "model": "laya"},
 "questions": {"department": {"comparability": "options_changed", "agreement": 0.954,
                              "flips": [{"from": "technical", "to": "account", "n": 17}, {"from": "other", "to": "account", "n": 6}],
                              "act_rate": {"baseline": 0.83, "candidate": 0.86}, "mean_certainty": {"baseline": 0.91, "candidate": 0.92},
                              "label_accuracy": {"labelled": 58, "baseline": 0.91, "candidate": 0.95}},
               "urgency": {"comparability": "identical", "agreement": 0.986, "mean_abs_score_change": 0.04}}}
```

**Files.** `POST /files` takes either multipart (`file`, `purpose`) or raw bytes with the file's own content type and `?name=cracked.jpg&purpose=media`:

```bash
curl -s 'http://127.0.0.1:8420/v1/studio/files?name=cracked.jpg&purpose=media' -H 'content-type: image/jpeg' --data-binary @cracked.jpg
```

```json
{"id": "file_01JB7PZ3YQ0M6T2C9W4K8D1R5V", "object": "file", "purpose": "media", "type": "image", "content_type": "image/jpeg",
 "name": "cracked.jpg", "bytes": 183422, "sha256": "3a7bd3e2360a3d29", "created_at": 1790848202, "expires_at": 1790934602}
```

- `purpose` is `media`, `batch_input`, `examples`, `batch_output` or `eval_output`.
- `type` is `image`, `audio` or `video` for media.
- `expires_at` applies only while nothing references the file: 24 hours, as with today's uploads.
- **`GET /files/{id}`** returns the metadata. **`GET /files/{id}/content`** returns the bytes, with the safe headers in section 4.12.
- **`DELETE /files/{id}`** removes the bytes even when decisions refer to them; those decisions then show `"available": false`. Privacy wins over completeness. It returns `409 file_in_use` only while a queued batch or eval needs the file.
- `/api/uploads` stays as an alias for `purpose=media`, and legacy 32-hex upload ids still resolve until they are swept.

**Models.** `GET /models` and `GET /models/{id}`:

```json
{"id": "laya", "object": "model", "name": "Laya", "maker": "ConvAI Innovations", "modalities": ["text"],
 "types": ["choice", "score", "noul"], "max_options": 20, "max_questions": 64, "context_tokens": 512,
 "status": "loaded", "revision": "3c1f0a9d"}
```

- `types` lists the primitive types the model supports; the studio types `multi`, `rank` and `number` expand to them.
- `status` is `loaded`, `loading`, `downloaded` or `not_downloaded`.
- `/v1/models` keeps TypeSafe's `{"models": [{name, description, release_date}]}` shape unchanged.

**Settings.** `GET /settings` and `PATCH /settings`:

```json
{
  "object": "settings",
  "history": {"store": "full", "retention_days": 30, "max_storage_gb": 20, "store_media": true, "keep_labelled": true,
              "on_store_error": "serve", "notice_acknowledged_at": null},
  "decisions": {"default_act_threshold": 0.9},
  "storage": {"db_bytes": 231456789, "blob_bytes": 1203456789, "decisions": 104233, "oldest_at": 1788254043,
              "last_sweep_at": 1790848800, "last_sweep_deleted": 1840, "pending_deletion": 0, "search_available": true}
}
```

The `history` fields:

| Field | Meaning |
|---|---|
| `store` | The studio-wide default and ceiling (section 4.5). |
| `retention_days` | ≥ 1, or `0` to keep forever. |
| `max_storage_gb` | Size cap for the database plus blobs. |
| `store_media` | `false` keeps media metadata but not bytes. |
| `keep_labelled` | Decisions with feedback are exempt from age-based retention. |
| `on_store_error` | `serve` or `fail` (section 4.5). |
| `notice_acknowledged_at` | Set by the History page's first-run notice. |

Other fields:
- `decisions.default_act_threshold` is the studio default layer (section 4.3).
- `storage` is read-only.
- A `PATCH` that shortens retention returns `storage.pending_deletion`, the number of decisions the next sweep will remove.
- These settings are management: remote callers need a key (section 4.12).

---

## 4. Rules

### 4.1 State variables

**Types**

| `type` | JSON value | Constraints | Rendered as text |
|---|---|---|---|
| `string` | string | `min_length`, `max_length` (default 20,000; platform maximum 200,000), `pattern`, `enum`, `format` (`date` or `date-time`) | as-is |
| `integer` | integer | `minimum`, `maximum`, `enum` | `42` |
| `number` | number (integers accepted) | `minimum`, `maximum` | shortest round-trip: `0.5` |
| `boolean` | `true` or `false` | none | `true` / `false` |
| `json` | any JSON value | `schema` (JSON Schema 2020-12), `max_bytes` (default 256 KB) | `contract.render` (field names as labels, one per line) |
| `options` | `{"name": "description or null"}` or `["name", …]` | `min_items`, `max_items` (≤ 1000); names unique, non-empty, ≤ 200 characters | never rendered; only binds `criteria` |
| `image`, `audio`, `video` | a string: a file id `"file_…"` or a `data:` URL | `max_bytes` (default 200 MB) | never rendered; attached as media |

**Common keys**

| Key | Meaning |
|---|---|
| `description` | Shown in forms and SDK docs. Never sent to the model. |
| `required` | Defaults to `true`, unless a `default` is given. |
| `default` | Applied when the variable is missing or `null`. It is validated when the version is saved. |
| `example` | A sample value used by the Playground form and by `preview`. |
| `sensitive` | Default `false`. The model sees the value; history does not (see below). |
| `trusted` | Default `false`. Allows a free-text variable in question text (see below). |

**Validation**
- Types are strict, with no coercion: `"42"` is not an integer.
- All problems are reported in one response (`details[]`):
  - `missing_variable`
  - `invalid_variable`, whose message says what was expected and what was received
  - `unknown_variable`, with a did-you-mean hint. A typo would otherwise silently change the state.
- `null` counts as missing.
- A template has at most 64 variables.

**Substitution rules** (logic-free)

1. **Syntax.** `{{name}}`, with optional inner spaces (`{{ name }}`). `\{{` renders a literal `{{`. There are no expressions, filters, loops, conditionals, dotted paths or includes.
2. **Where placeholders are allowed.**
   - In the `state` template: in any string of a JSON template (never in keys), and in a text template.
   - In question `instructions` (including strings inside structured instructions), option descriptions (criteria values), score level texts, and `noul` `true`/`false` descriptions.
   - A whole-value `"criteria": "{{var}}"` binds an `options` variable. This works for `choice`, `multi` and `rank` only.
3. **Where placeholders are never allowed** (`400 placeholder_not_allowed`, checked when the version is saved): question keys, option names, number values and the level count. These are the answer vocabulary that history aggregates by.
4. **Whole-value substitution in JSON states.** A string that is exactly one placeholder is replaced by the **typed** value: numbers stay numbers, and objects stay objects.
5. **Embedded substitution.** A placeholder inside longer text, and every placeholder in a text state or in question text, is rendered with the "Rendered as text" column above.
6. **Missing optional values** (no value and no default):
   - A whole-value placeholder removes its key from the object (or its element from the array), so the model never reads `tier:` with nothing after it.
   - Inside text, it renders as `""`.
   - A state that renders to nothing returns `400 empty_state`.
7. **Single pass.** Substituted values are never scanned again. A customer who types `{{account_tier}}` is read literally, so template structure cannot be injected.
8. **Media variables** never appear in text (`400 media_variable_in_text` at save). Each becomes a media item `{type, file_id, variable: "<name>", name}`, attached in declaration order before any request `media`. The type must be in `modalities` (`400 modality_not_declared` at save).
9. **Default state.** With `state: null`, the state is the object of non-media variables in declaration order, with unset optional variables left out. In this mode variable names are visible to the model, so name them readably.
10. **Checks when a version is saved.**
    - A placeholder naming an undeclared variable is an error: `400 undeclared_variable`, with `param` such as `questions.urgency.instructions`.
    - An unused declared variable is a warning: `variable_unreferenced`.
    - Questions the default model cannot run produce the warning `default_model_incompatible`.

**Worked examples.** With the template in section 2.1, call 2's variables render to this state:

```json
{"customer": {"tier": "enterprise"}, "message": "We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel."}
```

`open_invoices` and `customer_email` were not sent, so their keys are gone. The model reads `contract.render(state)`:

```
customer:
  tier: enterprise
message: We were billed twice for March on invoice #4411. Refund the duplicate today or we cancel.
```

The same variables with a text state template `"Plan: {{account_tier}}\nOpen invoices: {{open_invoices}}\n\n{{customer_message}}"` render to `"Plan: enterprise\nOpen invoices: \n\nWe were billed twice…"`.

**`sensitive: true`**
- The value is used for the decision but never written to disk.
- `input.variables` stores `{"$redacted": "hmac-sha256:<hex>"}`, keyed with a per-install secret in `DATA/secret` (0600).
- The stored state and `rendered_state` show `[redacted:<name>]`.
- Full-text search never indexes the value.
- A sensitive media variable's bytes are not kept.
- The decision can be rerun only when the value is supplied again.
- The HMAC makes erasure by value possible (`sensitive_hash`, section 3.3).

**`trusted: true`**
- A variable used in question text changes what the question asks, so end-user text there is an injection risk.
- Closed-vocabulary variables are always allowed in question text: `string` with `enum`, `integer`, `number`, `boolean`, and `string` with `format`.
- A free-text `string` or `json` variable in question text is a save error (`400 untrusted_variable_in_question`) unless it is declared `trusted: true`. That declaration is the author's statement that callers control the value.

**`options` variables**
- The variable's option set becomes the criteria.
- Options added with `add_options` are appended after it.
- Answers carry `dynamic_options: true`. Stats exclude them from option distributions, but count them in accuracy, act rate and latency.
- Candidates beyond the model's `max_options` fail before any model runs (`400 model_incompatible`).
- Typical use: an agent choosing its next action from candidates that change every call.

### 4.2 Questions: extension, skipping, no overrides

The effective question set is built in this order:
1. The template's questions in definition order, minus `skip`.
2. `add_options`, applied to those questions.
3. Extra `questions`, appended in request order.

The order is recorded, because option order can bias some models; this is why the order test exists.

| Request field | Rule | Error codes |
|---|---|---|
| `questions` (with a template) | New keys only. Allowed when `extensions.questions` is true; at most `extensions.max_questions`. Extras may use the template's variables in their text. | `question_conflict` (the key exists in the template, even if skipped; `param: "questions.<key>"`; the message points to `add_options` or to saving a new version), `extension_not_allowed`, `too_many_extra_questions` |
| `add_options` | The key must be a template question listed in `extensions.options` (or `options: true`). `choice`, `multi` and `rank` take a map or list of names; `number` takes a list of numbers (sorted in). Refused for `score` and `noul`, because a new level changes what the scale means. New names only, appended after the template's options (or the options variable's). | `unknown_question`, `extension_not_allowed`, `options_not_extensible`, `option_conflict` |
| `skip` | The key must be in `extensions.skip` (or `skip: true`). At least one question must remain. | `unknown_question`, `skip_not_allowed`, `no_questions` |
| Overrides | **Never.** A template question's type, instructions, criteria or levels cannot change per call. Send the change as a new version. Per-question thresholds, temperatures and `multi_threshold` are settings, not overrides (section 4.3). | (the same key under `questions` is `question_conflict`) |
| Ad hoc decisions | `questions` is the whole set; `add_options` and `skip` are refused. | `requires_template` |

**After the merge**, model limits are checked against the resolved model, **before** any model loads:
- `types`;
- `max_options` per question;
- `max_questions`, counted after `multi` expands into one yes/no per option, as `worker.py` counts;
- the modality of every media item.

Violations return one `400 model_incompatible`, with a `details[]` entry per problem (`type_not_supported`, `too_many_options`, `too_many_questions`, `modality_not_supported`). A state estimated to exceed `context_tokens` is a warning, `state_may_be_truncated`, because adapters truncate.

**Recording.** The decision stores `extensions`, and each answer's `origin`. By default, stats and version comparisons count only `origin: "template"` answers without `dynamic_options`, and report the rest as `excluded_runs`. `include_extended=true` counts everything.

### 4.3 Resolution: model, settings, act gates

**Pipeline.** Identical for every entry point; steps 1 to 8 are what `preview` runs.

1. **Authenticate and guard.** Section 4.12. Mint or reuse the `request_id`.
2. **Idempotency.** Section 4.9.
3. **Resolve the template** reference to exactly one version (`404 template_not_found`, `template_version_not_found`, `alias_not_found`). An archived template adds the warning `template_archived`.
4. **Validate variables and render** the state and question text (section 4.1).
5. **Build the effective questions** (section 4.2).
6. **Resolve the model:**
   1. the request's `model`;
   2. the template version's `model`;
   3. the most recently loaded model (today's `_route` rule, including the aliases `jev-latest`, `default`, `auto` and `""`).

   A model named explicitly that is unknown returns `404 model_not_found`; there is never a silent substitution, because history must say truthfully which model answered.
7. **Resolve settings** per question (below).
8. **Check limits and modalities** (section 4.2).
9. **Run.** Load the model if needed, exactly as `_ensure_ready` does today (wait up to 900 s when auto-load is on; `409 model_not_loaded` when it is off; `409 model_not_downloaded`). Resolve media into blob paths. Call `workers.decide` with the effective questions and per-question temperatures. `build_answers` applies the temperatures; the raw probabilities come back beside the answers.
10. **Gate and record.** Compute `certainty` and `act` per answer, then the decision-level `act` and `needs_review`. Record at the effective storage level (section 4.5) and respond.

Requests rejected at steps 1 to 8 are not decisions: they are counted in `usage_counters` only. Failures in step 9 (load failed, ejected, timeout, a worker error, "no model loaded") are stored as `failed` decisions, with `error` and `decision_id` in the error response. `model` is `null` when none resolved.

**Settings precedence.** For each question `q` and each key (`act_threshold`, `temperature`, `multi_threshold`), the first value found wins:

| # | Layer | Name in `settings.sources` |
|---|---|---|
| 1 | `request.settings.questions[q]` | `request.questions` |
| 2 | `request.settings` | `request` |
| 3 | `template.settings.models[model].questions[q]` | `template.models.<model>.questions` |
| 4 | `template.settings.models[model]` | `template.models.<model>` |
| 5 | `template.settings.questions[q]` | `template.questions` |
| 6 | `template.settings` | `template` |
| 7 | Studio defaults: `act_threshold` = `settings.decisions.default_act_threshold` (0.9, today's Playground default); `temperature` = 1.0; `multi_threshold` = the question's own `threshold` (default 0.5) | `studio` (`question` for `multi_threshold`) |

Consequences:
- A request's decision-level value applies to **every** question, overriding the template's per-question values. Sending `"temperature": 1` turns calibration off for that decision.
- `models.<model>` is read **after** the model is resolved, so a temperature fitted on Laya never applies to Kev 4B.
- Every effective value is recorded with its source, so History can explain every "act" and every "ask a human".

**Act gate**
- `certainty` = `top_probability`, except for `multi`, where it is min over options of max(p, 1−p).
- `act` = `certainty >= act_threshold(q)`.
- Decision-level `act` = all answers act.
- The gate is fixed when the decision is made; stats can re-gate from stored probabilities with `what_if`.

### 4.4 Versions: creation, pinning, aliases, comparability, archive and delete

**When a version is created**
- `POST /templates` (version 1);
- a `PUT`, `PATCH` or `POST …/versions` whose **definition** differs from the latest (`modalities`, `variables`, `state`, `questions`, `model`, `settings`, `extensions`);
- `restore`;
- `evals/{id}/apply`.

The definition is canonicalised: JSON without insignificant whitespace, with **key order preserved** (order changes what a model reads) and defaults filled in. It is then hashed into `content_hash`. If the hash equals the latest version's, no version is created and the response says `change: "unchanged"` (PO-2).

**Head fields are not versioned:** `name`, `description`, `metadata`, `storage`, `retention_days`, `archived` and aliases. Examples are not versioned either; they have `examples_revision` (PO-1).

**Concurrency**
- `GET /templates/{id}` returns `ETag: "<latest version>"`.
- `If-Match: "<n>"` on `PUT`, `PATCH` or `POST …/versions`, or the body field `base_version`, returns `412 version_conflict` with `current_version` when the template moved on.
- Both are optional, so setup scripts stay idempotent without them.
- Head-only edits are last-write-wins and leave the ETag unchanged.

**References and pinning**
- `id` resolves to the latest version **when the request arrives**.
- `id@N` is exact.
- `id@alias` resolves to the alias's current version.
- Every decision records `template.version` (the integer), `ref` and `resolved_from`, so history always shows the exact version used.
- Versions are cached in memory forever, since they are immutable; the latest pointer and aliases are invalidated on write.

**Comparability of a question between two versions** (stored in `changes.questions` when saved, and used by the diff, stats and compare):

| Class | When | How stats treat it |
|---|---|---|
| `identical` | Same type, text and options | One series |
| `text_changed` | Instructions, option descriptions, score level text or noul descriptions changed; same type and option set | One series, flagged |
| `options_changed` | Same type; the option set differs (choice, multi, rank, or number values) | Compared on the shared options (`shared_distribution`); full distributions shown separately |
| `incomparable` | The type changed, the number of score levels changed, or the options became dynamic | Shown side by side, never charted as one series |
| `added` / `removed` | The question exists in only one version | Shown in one version only |
| `reference` | The first group in a stats response | none |

**Archive, delete and built-ins**
- **Archive** hides a template and keeps it callable. Its decisions carry the warning `template_archived`. New versions are refused until it is unarchived (`409 template_archived`).
- **Delete** (section 3.2) is the only way to break callers. With `history=keep`, every version that a stored decision references stays readable.
- **Built-ins** come from `ui/js/examples.js`. There is one `builtin/<example id>` per scenario, for example `builtin/support` and `builtin/agent-browser`.
  - They are raw-state templates: no variables, with the scenario's state as their first unlabelled example.
  - `metadata["basal.recommended_models"]` comes from `ui/js/model-guides.js`.
  - They are read-only, and cloned with `from: {"template": "builtin/support@1"}`.
  - When a studio release changes a built-in, the next startup adds a new built-in version. User templates never churn.

### 4.5 Storage levels and `store:false`

| Level | Persisted |
|---|---|
| `full` | Everything in section 2.3. Sensitive values appear only as HMACs and markers. |
| `answers_only` | Everything **except the situation**: no `input.variables`, `state` or `rendered_state`, no media bytes or names (type, byte count and sha256 are kept), no full-text entry, no unknown request keys. Questions, answers, raw probabilities, settings, model, template, timing, metadata and `input_hash` are kept. Stats, feedback and evals-from-history labels work; rerun and replay do not (`409 input_unavailable`). |
| `none` | Nothing on disk. No decision row, body, projection, full-text entry or media. An inline `data:` payload goes to a temporary blob that is deleted when the worker returns. A content-free `usage_counters` row and an in-memory ring of 500 `{time, model, status, total_ms, stored: false}` entries feed the live Activity charts. The response still carries an `id`, with `store: "none"`, for log correlation; `GET` returns 404. |

**Effective level** = the most private of these three:
- the studio `history.store`;
- the template's `storage` (for templated decisions);
- the request's `store`, which defaults to `full` and can be set by the body, by `X-Basal-Store`, or by both (the more private wins).

A caller can always store less and never more. `store: true` means "`full`, subject to the ceilings". A lowered request gets the warning `store_downgraded`.

Constraints:
- `background: true` needs `full` or `answers_only` (`400 background_requires_store`), because there is nothing to poll otherwise.
- Streaming works with `none`, but cannot be resumed.
- Batches and evals may use `none` (sections 3.6 and 3.7).
- `metadata` is kept under `answers_only`, because it is the caller's join key. Docs and the API page tell callers to put ids in it, not personal data.

**When a history write fails** (disk full, a locked database), `history.on_store_error` decides:
- **`serve`** (default): the answer is returned anyway.
  - The studio API adds the warning `history_write_failed` and `x-basal-stored: failed`.
  - The wire routes add `x-basal-stored: failed` and omit `x-basal-decision-id`.
  - The failure is logged, and the System page shows it.
- **`fail`**: `503` with code `store_failed` (type `api_error`; the TypeSafe `detail` shape on wire routes). This is for setups where an unrecorded decision is unacceptable.

**Privacy notice (PO-3).**
- The History page shows, on first run, "API calls are now saved to History on this computer for 30 days", with switches for the level and the retention. Acknowledging it sets `notice_acknowledged_at`.
- The API page and README "Use it from code" say the same and document every opt-out: body `store`, `X-Basal-Store`, the template `storage`, and the studio `history.store`.

### 4.6 Retention

- **Effective retention** is the template's `retention_days` when set, else the studio's `history.retention_days` (default 30). `0` means forever.
- **`expires_at`** is computed on each read, so retention changes are retroactive from the next sweep.
- **Exempt from age-based deletion:**
  - pinned decisions;
  - decisions with feedback, when `keep_labelled` is on;
  - decisions of an existing eval run (they go when the eval is deleted);
  - decisions of a batch still in progress.
- Examples are never swept: they are curated data.
- **Size cap.** If the database plus blobs exceed `max_storage_gb`, the oldest non-exempt decisions are deleted until usage is at 90% of the cap.
- **`store_media: false`** unlinks media bytes right after the decision. Metadata and hashes stay, and the History page shows "file not kept".
- The sweeper is described in section 5.6. Every run writes a `history.swept` audit event with the counts.

### 4.7 How `/v1/systemone` and the gateway routes write history

These rules apply to `/v1/systemone`, `/api/v1/systemone`, `/api/alpha/decisions`, `/typesafe/v1/systemone` and `/v1/evaluate`.

1. **Parsing is unchanged.**
   - `_wire` reads the JSON and calls `api_compat.parse`, whose `WireRequest` keeps `extra="ignore"` and a temperature-only settings model.
   - Validation errors, statuses and envelopes are byte-identical to today.
   - Rejected requests are not stored; they are counted in `usage_counters`.
2. **Studio extensions are read leniently from the raw body before parsing** (`api_compat.studio_extensions`).
   - `store`: `true`, `false`, `"full"`, `"answers_only"` or `"none"`. Anything else is **ignored** and reported as `x-basal-warning: store_ignored`. It is never a 422.
   - `metadata`: an object within the limits in section 2.0. Anything else is ignored whole (`metadata_ignored`).
   - `settings.act_threshold`: a number in (0, 1], used only for the stored gate. Anything else is ignored.
   - OpenRouter's `user` and `session_id` (up to 256 characters) become `metadata.openrouter.user` and `metadata.openrouter.session_id`.
   - `X-Basal-Store` and `X-Basal-Metadata` do the same for clients that cannot change bodies. The more private store value wins, and body metadata wins per key.
   - TypeSafe's own server ignores these keys (FastAPI's default), so the same client code runs against both.
3. **Idempotency and retries**, section 4.9.
4. **Run** through the shared pipeline from step 6 (section 4.3), with no template: the model is routed exactly as today.
5. **Record** the decision with:
   - `source.surface: "api"` (or the UI surface, section 4.8), `endpoint`, `format` (`typesafe`, `openrouter`, `vercel` or `evaluate`), `client` (from `client_of()`), `request_id` and `attempt`;
   - `template: null`;
   - the full studio answers, with `certainty`, `act` and raw probabilities, even when the response was strict TypeSafe;
   - gates at the studio default threshold, or the lenient `settings.act_threshold`.
6. **Optional attribution.** `X-Basal-Template: support-triage@4` labels the record, without changing the response.
   - The call is attributed when every question of that version is present in the request **with an identical definition**. Question hashes are compared after rendering, which requires the template's questions to have no placeholders, or to be rendered from `X-Basal-Template`-declared variables. In that case the record gets `template: {"id", "version", "ref", "resolved_from", "attribution": "header"}`, other keys become `origin: "extra"`, and the response carries `x-basal-template-status: attributed`.
   - Otherwise the record is stored untemplated, with `x-basal-template-status: mismatch` and a warning.
7. **Response.**
   - The body is exactly `api_compat.shape(...)`, as today. The studio-only fields are added to a **deep copy** used for recording, never to the object `shape` receives.
   - Added headers: `x-basal-decision-id` (only when stored), `x-basal-stored`, and `x-basal-warning` or `x-basal-template-status` when relevant.
   - The official SDKs ignore unknown headers.
8. **Failure isolation.** The history write runs in its own `try`. It can never change the status, body or latency class of a wire response (except under `on_store_error: fail`).
9. **Statelessness holds.** Nothing stored ever changes an answer on these routes: no template, alias or stored default is ever applied.

A stored wire call looks like this:

```
POST /v1/systemone        (body exactly as README "Use it from code")
HTTP/1.1 200 OK
x-typesafe-request-id: req_9d2e41c07b3f4a5e8c61d0f2a7b9e3c4
x-basal-decision-id: dec_01JB7S0A2C4E6G8J0K2M4N6P8R
x-basal-stored: full
```

`GET /v1/studio/decisions/dec_01JB7S0A2C4E6G8J0K2M4N6P8R` then returns a section 2.3 object with:
- `template: null`
- `source: {"surface": "api", "endpoint": "/v1/systemone", "format": "typesafe", "client": "TypeSafe Python SDK", "request_id": "req_9d2e41c07b3f4a5e8c61d0f2a7b9e3c4", "attempt": 0, "retry_of": null}`
- `settings.sources: {"act_threshold": "studio", "temperature": "studio"}`

### 4.8 How the Playground and other studio pages write history

The UI sends `X-Basal-Client: ui` (as `store.js` does today) and `X-Basal-Surface: <surface>`. The surface header is honoured only alongside `X-Basal-Client`.

| UI action | Call | Recorded as |
|---|---|---|
| Decide, free draft | `POST /v1/studio/decisions` with `{model, state, questions, media (file ids), settings: {act_threshold, temperature}}` | `surface: "playground"`, ad hoc |
| Decide with a template open | `{template: "id@N", variables, questions (extras), add_options, skip, settings}`. The left pane is a form generated from `/templates/{id}/schema`, with media variables as drop zones. | `attribution: "explicit"` |
| An edit to a template question that is not a valid extension (rewording, removing an option) | Sent ad hoc, with `metadata["basal.draft_of"] = "id@N"`. The UI offers "Save as version N+1". | `template: {"id", "version": N, "attribution": "draft"}`. Hidden from default per-template history and stats (`attribution=all` shows it). |
| Act threshold slider | Sent as `settings.act_threshold` on every Decide. The browser preference `store.prefs.threshold` becomes only the slider's initial value. The gate in the UI reads `answers.<q>.act`. Moving the slider afterwards previews locally with a `what_if` label. | Recorded in `settings`, with sources |
| Temperature slider | `settings.temperature`. With a template open, the inspector shows the template's value for the selected model and a "this run only" marker when overridden. | Recorded |
| "Keep in history" switch (default on) | Off sends `store: false`. | Not stored |
| Compare | `POST /v1/studio/comparisons` with `vary.models`. `/api/compare` stays for one release as a wrapper returning the old shape. | `surface: "compare"`, grouped |
| Test option order | `POST /v1/studio/comparisons` with `vary.option_order`, replacing the client-side loop of 5 runs. | `surface: "order_test"`, hidden by default |
| Save as template / Save version | `POST /templates` with `from: {"decision": last.id}` or the draft's definition; then `PATCH` with `If-Match` and `note`. The editor offers a `string` variable for each `{{…}}` it finds in the state text. | New version |
| Evaluate page | `POST /v1/studio/evals`: an inline dataset (pasted rows), or a template's examples. "Use this temperature" and "Use this threshold" become `POST /evals/{id}/apply` when a template is open. | `surface: "eval"`, hidden by default |
| History page (replaces Activity) | `GET /v1/studio/decisions?view=summary`, tailing with `order=asc&after=<newest>` every 2 s (SSE in phase 3). Charts come from `/decisions/stats?group_by=hour` and `usage_counters`. "Open in Playground" is `GET /decisions/{id}` then `loadRequest`; "Rerun on…", "Label", "Add to examples" and "Save as template" are also available. | none |
| Drafts | Stay in `localStorage` (`bud.draft.v2`) and gain `template: {id, version, content_hash}`. | Never stored: a draft is work in progress |

### 4.9 Idempotency and retries

**The `Idempotency-Key` header** (up to 255 printable ASCII characters) is accepted on every POST under `/v1/studio` and on every wire route. Keys are scoped to the workspace and kept for 24 hours. `request_hash` = sha256 of the method, the path and the canonical body.

| Case | Result |
|---|---|
| Same key and hash, first request finished with `2xx` | The stored response is replayed with its status, body and headers, plus `x-basal-idempotent-replayed: true`. On wire routes the replay is the stored wire bytes. |
| Same key while the first request is still running | `409 idempotency_in_progress`, with `retry-after: 1` |
| Same key, different hash | `409 idempotency_key_reused`; nothing runs |
| First request failed (`4xx` or `5xx`) | Not saved; a retry runs again |
| `store: "none"` | The key is honoured from memory only, for 10 minutes, and never written to disk |

On wire routes the conflict errors use that route's own error shape (`api_compat.error_body`).

**SDK retries without a key.** The TypeSafe SDKs retry 408, 429 and 5xx up to twice, without a key, and send `X-TypeSafe-Retry-Count`.
- `source.attempt` records the count.
- For `attempt ≥ 1`, the studio links `retry_of` to the most recent decision in the last 120 s with the same workspace, client, endpoint, `input_hash`, `questions_hash` and `attempt − 1`, and marks that earlier decision `superseded`.
- `fold_retries=true` (the default) hides superseded attempts from lists and stats, so each logical call counts once.
- SDK authors should still send a UUIDv4 `Idempotency-Key` per logical call.

### 4.10 Background, waiting, streaming and queues

- **Background.**
  - `"background": true` returns `200` with `status: "queued"` and a `Location` header.
  - Wait with `GET /decisions/{id}?wait=60`; cancel with `/cancel`.
  - This is the answer for clients with short timeouts (the TypeSafe SDK's default is 10 s), since a cold model can take minutes to load.
- **Long-polling** (phase 1). `?wait=N`, with N ≤ 60, on decisions, batches and evals. It works from curl, with no client library.
- **Streaming** (phase 3). `"stream": true`, or `Accept: text/event-stream`, returns SSE. Every event carries `type` and a rising `sequence_number`:
  - `decision.created` (always first);
  - `decision.queued`;
  - `decision.model_loading`, repeated as `{stage, progress, elapsed_s}` from the worker handle;
  - `decision.in_progress`;
  - `decision.answer.done`, one per question (single-pass adapters emit them together);
  - exactly one terminal event: `decision.completed` (the full object), `decision.failed` or `decision.cancelled`;
  - `error`, for stream faults.

  Resume a stream with `GET /decisions/{id}?stream=true&starting_after=N` (stored decisions only).
- **Live feed** (phase 3). `GET /decisions/events` is SSE of `decision.created`, `decision.completed`, `decision.failed` and `decision.deleted`, carrying summary objects (plus `stored: false` stubs for `none` calls). It replaces the Activity page's polling.
- **Queues** (phase 3).
  - Each model gets a priority queue in the studio process: `interactive` (sync API, Playground, comparisons), then `background`, then `bulk` (batches, evals, replays). Bulk work yields between items. The worker still answers one request at a time under `S.lock`.
  - Limits per model: 256 interactive, 1,000 background and 10,000 bulk waiting. Beyond that: `429 queue_full` with `retry-after` and `retry-after-ms`, which the TypeSafe SDK honours.
- **Restart recovery.** `queued` and `in_progress` rows are re-queued at startup, with `run_attempts + 1`. After 3 attempts a row becomes `failed`, with `error.code: "interrupted"`. Decisions are pure computations, so retrying them is safe.

### 4.11 Errors

**Envelope** (`/v1/studio` only):

```json
{
  "error": {
    "type": "invalid_request_error",
    "code": "unknown_variable",
    "message": "Template support-triage@2 has no variable 'acount_tier'. Did you mean 'account_tier'?",
    "param": "variables.acount_tier",
    "details": [
      {"code": "unknown_variable", "param": "variables.acount_tier", "message": "Template support-triage@2 has no variable 'acount_tier'. Did you mean 'account_tier'?"},
      {"code": "missing_variable", "param": "variables.customer_message", "message": "customer_message is required (a string of up to 8000 characters)."}
    ],
    "request_id": "req_2b9e4f6a8c0d4e1fa3b5c7d9e1f3a5b7",
    "decision_id": null
  }
}
```

- **`param`** is the path to the offending field: dots for keys (`questions.urgency.criteria`), `[n]` for array items (`media[0].file_id`), and `["…"]` for ad hoc keys containing dots or spaces.
- **`details`** lists every problem found in the same pass.
- **`decision_id`** is set when a failed decision was stored.
- **Extra typed fields:** `current_version` (on `version_conflict`) and `existing_id` (on `example_exists`).
- The official TypeSafe SDKs extract `error.message` first, so even they display these errors well.

**Wire routes** keep their own shapes exactly: TypeSafe `{"detail": …}`, OpenRouter `{"error": {code, message}}`, and Vercel `{"message", "error_type"}`. The new wire failure modes use the same shapes: `cross_site_request` 403, the idempotency 409s, and `store_failed` 503.

**Auth by path.**
- `/v1/studio` returns the envelope above, with `401` for both a missing and a wrong key.
- Wire routes keep TypeSafe's 403 (missing) and 401 (wrong), checked before the body.

| HTTP | `type` | Codes |
|---|---|---|
| 400 | `invalid_request_error` | **Body and query:** `invalid_json`, `invalid_field`, `missing_field`, `read_only_field`, `invalid_parameter`, `invalid_reference`, `invalid_metadata`, `filter_required`, `confirmation_required`, `search_unavailable`. **Variables and state:** `missing_variable`, `unknown_variable`, `invalid_variable`, `variables_need_template`, `state_required`, `state_not_allowed`, `empty_state`. **Questions:** `question_conflict`, `unknown_question`, `extension_not_allowed`, `options_not_extensible`, `option_conflict`, `skip_not_allowed`, `too_many_extra_questions`, `no_questions`, `requires_template`. **Media and model:** `modality_not_allowed`, `model_incompatible` (details: `type_not_supported`, `too_many_options`, `too_many_questions`, `modality_not_supported`), `model_rejected_input` (the worker's 422; stored as a failed decision, with `decision_id`). **Template saves:** `undeclared_variable`, `placeholder_not_allowed`, `media_variable_in_text`, `modality_not_declared`, `untrusted_variable_in_question`, `invalid_definition`. **Other:** `invalid_expected`, `background_requires_store`, `eval_has_no_template`, `too_many_items` |
| 401 | `authentication_error` | `missing_api_key`, `invalid_api_key` |
| 403 | `permission_error` | `cross_site_request`, `remote_access_requires_key`, `template_read_only`, `insufficient_scope` (reserved) |
| 404 | `not_found_error` | `decision_not_found`, `template_not_found`, `template_version_not_found`, `alias_not_found`, `example_not_found`, `feedback_not_found`, `eval_not_found`, `batch_not_found`, `comparison_not_found`, `file_not_found`, `model_not_found`. Messages say what does exist ("support-triage has versions 1 to 3"). |
| 409 | `conflict_error` | `template_exists`, `template_id_reserved`, `template_archived` (version saves only), `example_exists`, `model_not_loaded` (auto-load off), `model_not_downloaded`, `decision_finished`, `idempotency_in_progress`, `idempotency_key_reused`, `input_unavailable`, `file_in_use` |
| 412 | `conflict_error` | `version_conflict` (with `current_version`) |
| 413 | `invalid_request_error` | `payload_too_large` |
| 415 | `invalid_request_error` | `unsupported_media_type` (a browser write without JSON; an upload that is not image, audio or video) |
| 429 | `rate_limit_error` | `queue_full` |
| 500 | `api_error` | `internal_error` |
| 503 | `model_error` | `model_load_failed`, `model_ejected`, `model_crashed` (all with `decision_id`) |
| 503 | `api_error` | `store_failed` (only with `on_store_error: fail`) |
| 504 | `model_error` | `model_load_timeout`, `model_timeout` (with `decision_id`) |

**Warning codes** (in `warnings[]`, or `x-basal-warning` on the wire):
- `template_archived`
- `unknown_field_ignored`
- `store_downgraded`
- `state_may_be_truncated`
- `history_write_failed`
- `per_question_settings_not_portable`
- `variable_unreferenced`
- `default_model_incompatible`
- `store_ignored` and `metadata_ignored` (wire only)
- `template_mismatch` (wire, `X-Basal-Template`)

### 4.12 Security

**Kept from today:**
- The server binds to 127.0.0.1 by default.
- The DNS-rebinding `Host` check stays.
- No CORS unless `BASAL_CORS_ORIGINS` is set.
- `Authorization: Bearer $BASAL_API_KEY` is required for remote callers, and locally with `BASAL_AUTH_LOCAL=1`.
- `X-Basal-Client` is required on mutating `/api/*` calls.
- Media is never a server path or a remote URL, so there is no traversal and no SSRF.

**New: the cross-site guard.** It applies to every non-GET, non-HEAD, non-OPTIONS request under the public prefixes (`/v1/`, including `/v1/systemone` and `/v1/studio`; `/api/v1/`; `/api/alpha/`; `/typesafe/v1/`) and under `/api/`. This is `basal/guard.py`, run in `auth_and_timing` before auth.

1. **If `Origin` is present**, it must be one of these, otherwise `403 cross_site_request`:
   - the studio's own origin, computed from the `Host` header: `http://127.0.0.1:<port>`, `http://localhost:<port>` or `http://[::1]:<port>`, where `<port>` is the port in `Host`. This covers the desktop app's 8420–8440 range, whose webview loads the studio from `http://127.0.0.1:<port>/`. Off-loopback, it is `http(s)://<Host>`.
   - an entry in `BASAL_CORS_ORIGINS`.

   `Origin: null` (sandboxed frames, `file://`) is refused.
2. **If `Origin` is absent but `Sec-Fetch-Site` is `cross-site` or `same-site`**, the request gets `403 cross_site_request`. `same-site` is refused because `http://localhost:3000` is "same-site" with the studio.
3. **Browser writes to `/v1/studio` must be `application/json`** (or `application/merge-patch+json`), or multipart carrying `X-Basal-Client`; otherwise `415`. A browser write is one that carries `Origin` or any `Sec-Fetch-*` header. JSON is not a simple content type, so a hostile page's `fetch` would also need a preflight, which fails with CORS off. This is defence in depth.
4. **Requests with neither `Origin` nor `Sec-Fetch-*`** (curl, the SDKs, servers) are unaffected. Their bodies parse as JSON whatever `Content-Type` says, so `curl -d '{…}'` keeps working.

This closes the gap where any web page could `POST` a `text/plain` body to `localhost:8420/v1/systemone` (`request.json()` ignores the content type, and public prefixes skip the `X-Basal-Client` check) and pollute or flood history.

**New: no remote history without a key.** When the studio is bound off-loopback and `BASAL_API_KEY` is unset:
- Remote callers may still decide: the wire routes, `POST /v1/studio/decisions` and `preview`, and `GET /v1/studio/models`.
- Every other `/v1/studio` route (history reads, feedback, deletes, templates, examples, evals, batches, files and settings) returns `403 remote_access_requires_key`.

**Serving stored files.** `/v1/studio/files/{id}/content` (and `/api/uploads/{id}`) send `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox; default-src 'none'`. Only allowlisted `image/*`, `audio/*` and `video/*` types are served inline; everything else is `Content-Disposition: attachment`. HTML and SVG are never served inline. An active file on the studio's origin would have full access to the API and history.

**Data at rest.**
- `studio.db` and `DATA/secret` are 0600; `DATA/blobs/` is 0700.
- Sensitive variables are never written (section 4.1).
- `Authorization` headers and cookies are never stored; decisions record the `actor` only.
- There is no encryption at rest (section 6.9).

**Injection.**
- Substitution is single-pass and evaluates nothing.
- Free-text variables in question text require `trusted`.
- The history query builder uses allowlisted columns only; metadata keys and values are bound parameters.

### 4.13 Limits

| Item | Limit |
|---|---|
| JSON body | 32 MB (a template definition: 256 KB) |
| File | 200 MB for media; 512 MB for batch input |
| Variables per template | 64. String values up to 200,000 characters, or the variable's `max_length` if lower. |
| Questions per decision | 128 after extensions, and the model's `max_questions` |
| Options per question | 1,000, and the model's `max_options` |
| Added questions | `extensions.max_questions` (default 16) |
| `metadata` | 16 keys; keys up to 64 characters; values up to 512 characters |
| `Idempotency-Key` | 255 characters, kept 24 hours |
| List `limit` | 1–100 (default 20) |
| `wait` | 60 s |
| Comparisons | 2–8 models or versions; 2–10 order runs |
| Batches | 1,000 inline items, 50,000 from a file, 5,000 for replays |
| Evals | 8 targets; 2,000 inline items; 5,000 history items |
| Examples | 10,000 per template; imports of up to 5,000 at a time |
| Queue per model | 256 interactive, 1,000 background, 10,000 bulk |

---

## 5. Storage model

This section is the implementation appendix for developers of the studio itself. API users can skip it.

### 5.1 Files and connections

```
DATA/                                   paths.DATA: ./data in a checkout, BASAL_DATA for the desktop app
  studio.db  studio.db-wal  studio.db-shm      0600
  secret                                       per-install HMAC key for sensitive variables, 0600
  blobs/sha256/3a/3a7bd3e2…4f1b.jpg            content-addressed media, 0700; the extension is kept for adapters and ffmpeg
  blobs/tmp/                                   in-flight uploads, renamed atomically into place
  backups/studio-m0002-20261001T101500.db      VACUUM INTO before each migration; the last 3 are kept
  activity.jsonl.imported                      the old log after migration 0002
  uploads/                                     retired; still swept after 24 h for one release
```

- **SQLite version.** At least 3.37 (for STRICT tables); the venv has 3.45.1. If the check fails, `db.open()` disables history and says so on the System page, and the wire routes keep working.
- **PRAGMAs:** `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`, `cache_size=-65536`, `temp_store=MEMORY`. `auto_vacuum=INCREMENTAL` is set once, on the empty file.
- **Writes.** One writer connection guarded by a lock and used through `asyncio.to_thread`, with **one transaction per decision** (no group-commit thread).
- **Reads.** Per-thread read-only connections (`mode=ro`); WAL gives them consistent snapshots.
- Worker processes never open the database.

### 5.2 Schema (migration `0001_init.sql`)

```sql
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at INTEGER NOT NULL) STRICT;

CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL) STRICT;
INSERT INTO workspaces VALUES ('ws_local', 'This computer', CAST(strftime('%s','now') AS INTEGER) * 1000);

CREATE TABLE api_keys (                      -- empty until multi-user; BASAL_API_KEY acts as the virtual key 'key_env' with scope '*'
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
  secret_hash TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '["*"]',
  created_at INTEGER NOT NULL, last_used_at INTEGER, revoked_at INTEGER) STRICT;

CREATE TABLE settings (workspace_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, key)) WITHOUT ROWID, STRICT;

-- Effective question sets, content-addressed. questions_hash keeps key and option order (exact reproducibility);
-- set_key sorts question keys but keeps option order (grouping, "save these questions as a template", header attribution).
CREATE TABLE question_sets (hash TEXT PRIMARY KEY, set_key TEXT NOT NULL, definition TEXT NOT NULL, created_at INTEGER NOT NULL) WITHOUT ROWID, STRICT;
CREATE INDEX qs_set_key ON question_sets(set_key);

-- ------------------------------------------------------------------ templates
CREATE TABLE templates (
  seq INTEGER PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  id TEXT NOT NULL,                          -- 'support-triage'; immutable
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
  origin TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('user','builtin')),
  latest_version INTEGER NOT NULL DEFAULT 0,
  examples_revision INTEGER NOT NULL DEFAULT 0,
  storage TEXT NOT NULL DEFAULT 'full' CHECK (storage IN ('full','answers_only','none')),
  retention_days INTEGER CHECK (retention_days IS NULL OR retention_days >= 0),
  archived_at INTEGER, deleted_at INTEGER,   -- deleted_at: tombstone kept while retained history references it
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, last_used_at INTEGER, created_by TEXT NOT NULL) STRICT;
CREATE UNIQUE INDEX tpl_id ON templates(workspace_id, id);

CREATE TABLE template_versions (
  seq INTEGER PRIMARY KEY,
  template_seq INTEGER NOT NULL REFERENCES templates(seq) ON DELETE CASCADE,
  number INTEGER NOT NULL,
  definition TEXT NOT NULL, format INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT NOT NULL, questions_hash TEXT NOT NULL, question_set_key TEXT NOT NULL,
  base_number INTEGER, note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK (source IN ('api','studio','restore','eval_apply','clone','builtin','import')),
  changes TEXT NOT NULL,                      -- the change record of section 2.2
  created_at INTEGER NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (template_seq, number)) STRICT;
CREATE TRIGGER template_versions_immutable BEFORE UPDATE ON template_versions
BEGIN SELECT RAISE(ABORT, 'template versions are immutable'); END;

CREATE TABLE template_questions (             -- per-version index for diffs and comparability classes
  version_seq INTEGER NOT NULL REFERENCES template_versions(seq) ON DELETE CASCADE,
  key TEXT NOT NULL, position INTEGER NOT NULL, type TEXT NOT NULL,
  question_hash TEXT NOT NULL,                -- the whole question definition
  text_hash TEXT NOT NULL,                    -- instructions and descriptions only
  options_key TEXT NOT NULL,                  -- choice/multi/rank: sorted option names; score: level count; number: sorted values; '{{var}}' when dynamic
  PRIMARY KEY (version_seq, key)) WITHOUT ROWID, STRICT;

CREATE TABLE template_aliases (
  template_seq INTEGER NOT NULL REFERENCES templates(seq) ON DELETE CASCADE,
  alias TEXT NOT NULL CHECK (alias <> 'latest'), version INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL,
  PRIMARY KEY (template_seq, alias)) WITHOUT ROWID, STRICT;

-- ------------------------------------------------------------------ examples (revisioned, immutable rows)
CREATE TABLE examples (
  row_seq INTEGER PRIMARY KEY,
  id TEXT NOT NULL,                           -- ex_<ulid>, stable across edits
  template_seq INTEGER NOT NULL REFERENCES templates(seq) ON DELETE CASCADE,
  revision INTEGER NOT NULL,                  -- examples_revision that wrote this row
  retired_in INTEGER,                         -- examples_revision that replaced or deleted it; NULL = current
  input TEXT NOT NULL, expected TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
  split TEXT NOT NULL DEFAULT 'test' CHECK (split IN ('test','calibration')),
  note TEXT NOT NULL DEFAULT '', input_hash TEXT NOT NULL, from_decision TEXT, created_at INTEGER NOT NULL) STRICT;
CREATE INDEX ex_current ON examples(template_seq, id) WHERE retired_in IS NULL;
CREATE UNIQUE INDEX ex_dedupe ON examples(template_seq, input_hash) WHERE retired_in IS NULL;
CREATE INDEX ex_revision ON examples(template_seq, revision, retired_in);
CREATE TRIGGER examples_immutable BEFORE UPDATE ON examples
WHEN OLD.retired_in IS NOT NULL OR NEW.retired_in IS NULL
  OR (NEW.input, NEW.expected, NEW.tags, NEW.split, NEW.note, NEW.revision) IS NOT (OLD.input, OLD.expected, OLD.tags, OLD.split, OLD.note, OLD.revision)
BEGIN SELECT RAISE(ABORT, 'example rows are immutable; write a new row'); END;

-- ------------------------------------------------------------------ files and blobs (random public ids, deduplicated bytes)
CREATE TABLE blobs (sha256 TEXT PRIMARY KEY, ext TEXT NOT NULL, bytes INTEGER NOT NULL,
  stored INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL) WITHOUT ROWID, STRICT;
CREATE TABLE files (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,           -- file_<ulid>, never derived from content
  workspace_id TEXT NOT NULL, blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  purpose TEXT NOT NULL CHECK (purpose IN ('media','batch_input','batch_output','examples','eval_output')),
  type TEXT, content_type TEXT NOT NULL, name TEXT, bytes INTEGER NOT NULL,
  created_at INTEGER NOT NULL, expires_at INTEGER, last_ref_at INTEGER NOT NULL,
  delete_after_download INTEGER NOT NULL DEFAULT 0, legacy_upload_id TEXT) STRICT;
CREATE INDEX files_blob ON files(blob_sha256);
CREATE INDEX files_expiry ON files(expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE example_media (
  example_row_seq INTEGER NOT NULL REFERENCES examples(row_seq) ON DELETE CASCADE,
  position INTEGER NOT NULL, file_seq INTEGER NOT NULL REFERENCES files(seq), variable TEXT, name TEXT,
  PRIMARY KEY (example_row_seq, position)) WITHOUT ROWID, STRICT;

-- ------------------------------------------------------------------ jobs (declared before decisions; FK targets)
CREATE TABLE batches (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL, actor TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('items','file','replay')),
  template_seq INTEGER REFERENCES templates(seq), version_number INTEGER, model TEXT,
  defaults TEXT NOT NULL, store TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','in_progress','completed','failed','cancelled')),
  total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0,
  output_file_seq INTEGER REFERENCES files(seq), error_file_seq INTEGER REFERENCES files(seq), metadata TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER) STRICT;
CREATE TABLE batch_items (                    -- not written at all when store = 'none' (items live in memory)
  batch_seq INTEGER NOT NULL REFERENCES batches(seq) ON DELETE CASCADE,
  item INTEGER NOT NULL, custom_id TEXT NOT NULL, body TEXT, source_decision_seq INTEGER,
  status TEXT NOT NULL, decision_seq INTEGER, error TEXT,
  PRIMARY KEY (batch_seq, item), UNIQUE (batch_seq, custom_id)) WITHOUT ROWID, STRICT;
CREATE TABLE eval_runs (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL, actor TEXT NOT NULL,
  template_seq INTEGER REFERENCES templates(seq) ON DELETE CASCADE,
  targets TEXT NOT NULL, dataset TEXT NOT NULL, examples_revision INTEGER, dataset_hash TEXT NOT NULL,
  questions TEXT, error_budget REAL, store TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','in_progress','completed','failed','cancelled')),
  progress TEXT NOT NULL, results TEXT, comparisons TEXT, output_file_seq INTEGER, metadata TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL, completed_at INTEGER) STRICT;
CREATE INDEX evr_template ON eval_runs(template_seq, seq);

-- ------------------------------------------------------------------ decisions: hot row
CREATE TABLE decisions (
  seq INTEGER PRIMARY KEY,                    -- monotonic = creation order; pagination and join key
  id TEXT NOT NULL UNIQUE,                    -- dec_<ulid>
  workspace_id TEXT NOT NULL, actor TEXT NOT NULL,
  created_at INTEGER NOT NULL, completed_at INTEGER,          -- Unix ms (the API shows seconds)
  status TEXT NOT NULL CHECK (status IN ('queued','in_progress','completed','failed','cancelled')),
  storage TEXT NOT NULL CHECK (storage IN ('full','answers_only')),
  surface TEXT NOT NULL, endpoint TEXT NOT NULL, format TEXT NOT NULL, client TEXT,
  request_id TEXT, attempt INTEGER NOT NULL DEFAULT 0,
  retry_of_seq INTEGER REFERENCES decisions(seq) ON DELETE SET NULL, superseded INTEGER NOT NULL DEFAULT 0,
  template_seq INTEGER REFERENCES templates(seq), version_number INTEGER,
  template_ref TEXT, resolved_from TEXT, attribution TEXT CHECK (attribution IN ('explicit','header','inferred','draft')),
  model TEXT, model_requested TEXT, model_revision TEXT, studio_version TEXT NOT NULL,
  questions_hash TEXT NOT NULL REFERENCES question_sets(hash), question_set_key TEXT NOT NULL,
  input_hash TEXT NOT NULL, variables_hash TEXT,
  extended INTEGER NOT NULL DEFAULT 0, dynamic_options INTEGER NOT NULL DEFAULT 0,
  n_questions INTEGER NOT NULL, n_media INTEGER NOT NULL DEFAULT 0,
  act INTEGER, min_certainty REAL,
  queue_ms REAL, load_ms REAL, model_ms REAL, total_ms REAL, input_tokens INTEGER,
  http_status INTEGER, error_code TEXT,
  group_type TEXT, group_id TEXT,
  batch_seq INTEGER REFERENCES batches(seq) ON DELETE CASCADE, custom_id TEXT,
  eval_seq INTEGER REFERENCES eval_runs(seq) ON DELETE CASCADE, eval_example_id TEXT, eval_target INTEGER,
  rerun_of_seq INTEGER REFERENCES decisions(seq) ON DELETE SET NULL,
  run_attempts INTEGER NOT NULL DEFAULT 0,    -- restart re-queues (not SDK retries)
  pinned INTEGER NOT NULL DEFAULT 0, has_feedback INTEGER NOT NULL DEFAULT 0,
  metadata TEXT NOT NULL DEFAULT '{}', body_bytes INTEGER NOT NULL DEFAULT 0, format_version INTEGER NOT NULL DEFAULT 1) STRICT;

-- ------------------------------------------------------------------ decisions: cold body (read only when one decision is opened)
CREATE TABLE decision_bodies (
  decision_seq INTEGER PRIMARY KEY REFERENCES decisions(seq) ON DELETE CASCADE,
  variables TEXT, state TEXT, rendered_state TEXT,     -- NULL under answers_only; replaced on redaction
  extensions TEXT NOT NULL, settings TEXT NOT NULL,    -- effective settings with sources
  answers TEXT, raw_probabilities TEXT,                -- raw: {"<q>": {"<key>": p}} before temperature
  extras TEXT NOT NULL DEFAULT '{}',                   -- {"usage":{}, "passes":1, "notes":[], "warnings":[]}
  error TEXT, request_extras TEXT, redacted TEXT) STRICT;
CREATE TRIGGER decision_outputs_frozen BEFORE UPDATE ON decision_bodies
WHEN (SELECT status FROM decisions WHERE seq = OLD.decision_seq) IN ('completed','failed','cancelled')
 AND (NEW.answers IS NOT OLD.answers OR NEW.raw_probabilities IS NOT OLD.raw_probabilities
      OR NEW.settings IS NOT OLD.settings OR NEW.extensions IS NOT OLD.extensions)
BEGIN SELECT RAISE(ABORT, 'a finished decision''s outputs are immutable'); END;

-- ------------------------------------------------------------------ answer projection (filters, stats, comparisons)
CREATE TABLE decision_answers (
  decision_seq INTEGER NOT NULL REFERENCES decisions(seq) ON DELETE CASCADE,
  question_key TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,   -- multi: one row per selected option (one NULL row if none)
  template_seq INTEGER, version_number INTEGER, model TEXT, created_at INTEGER NOT NULL,   -- copied in for index-only scans
  type TEXT NOT NULL,
  answer TEXT,               -- the canonical decision value ("billing", "3", "yes", a selected option, "7")
  value REAL,                -- noul: P(yes); score: expected level; number: estimate; multi: P(option)
  certainty REAL NOT NULL, act INTEGER NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('template','extended','extra','adhoc')),
  dynamic_options INTEGER NOT NULL DEFAULT 0,
  counted INTEGER NOT NULL DEFAULT 1,   -- 0 for superseded retries, hidden surfaces (eval, order_test, replay) and non-default attribution
  PRIMARY KEY (decision_seq, question_key, ordinal)) WITHOUT ROWID, STRICT;

CREATE TABLE decision_metadata (decision_seq INTEGER NOT NULL REFERENCES decisions(seq) ON DELETE CASCADE,
  key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (decision_seq, key)) WITHOUT ROWID, STRICT;
CREATE TABLE decision_media (
  decision_seq INTEGER NOT NULL REFERENCES decisions(seq) ON DELETE CASCADE, position INTEGER NOT NULL,
  file_seq INTEGER REFERENCES files(seq) ON DELETE SET NULL,   -- NULL under answers_only or once deleted
  type TEXT NOT NULL, name TEXT, variable TEXT, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
  PRIMARY KEY (decision_seq, position)) WITHOUT ROWID, STRICT;
CREATE TABLE decision_secrets (             -- HMACs of sensitive variables, for erasure by value
  decision_seq INTEGER NOT NULL REFERENCES decisions(seq) ON DELETE CASCADE,
  variable TEXT NOT NULL, hmac TEXT NOT NULL, PRIMARY KEY (decision_seq, variable)) WITHOUT ROWID, STRICT;

CREATE TABLE feedback (
  seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
  decision_seq INTEGER NOT NULL REFERENCES decisions(seq) ON DELETE CASCADE,
  template_seq INTEGER, version_number INTEGER, model TEXT,        -- copied in for accuracy by version and model
  question_key TEXT NOT NULL, expected TEXT, predicted TEXT, correct INTEGER, rating INTEGER CHECK (rating IN (-1, 1)),
  note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE (decision_seq, question_key, actor)) STRICT;
CREATE TRIGGER feedback_flag AFTER INSERT ON feedback
BEGIN UPDATE decisions SET has_feedback = 1 WHERE seq = NEW.decision_seq; END;
CREATE TRIGGER feedback_unflag AFTER DELETE ON feedback
BEGIN UPDATE decisions SET has_feedback = EXISTS (SELECT 1 FROM feedback WHERE decision_seq = OLD.decision_seq) WHERE seq = OLD.decision_seq; END;

CREATE TABLE eval_items (
  eval_seq INTEGER NOT NULL REFERENCES eval_runs(seq) ON DELETE CASCADE,
  item INTEGER NOT NULL, target INTEGER NOT NULL,
  example_id TEXT, example_revision INTEGER, expected TEXT NOT NULL,   -- a snapshot of the labels scored against
  predicted TEXT, raw_probabilities TEXT, correct TEXT, latency_ms REAL, status TEXT NOT NULL,
  decision_seq INTEGER REFERENCES decisions(seq) ON DELETE SET NULL,
  PRIMARY KEY (eval_seq, item, target)) WITHOUT ROWID, STRICT;

-- ------------------------------------------------------------------ idempotency, counters, audit
CREATE TABLE idempotency_keys (
  workspace_id TEXT NOT NULL, key TEXT NOT NULL, method_path TEXT NOT NULL, request_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('in_progress','done')),
  status_code INTEGER, response_headers TEXT, response_body BLOB, object_id TEXT, created_at INTEGER NOT NULL,
  PRIMARY KEY (workspace_id, key)) WITHOUT ROWID, STRICT;
CREATE TABLE usage_counters (                -- content-free; written for every request, stored or not, accepted or rejected
  workspace_id TEXT NOT NULL, minute INTEGER NOT NULL, model TEXT NOT NULL DEFAULT '', template_seq INTEGER NOT NULL DEFAULT 0,
  surface TEXT NOT NULL, format TEXT NOT NULL, status INTEGER NOT NULL, stored TEXT NOT NULL,
  count INTEGER NOT NULL, model_ms_sum REAL NOT NULL DEFAULT 0, total_ms_sum REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (workspace_id, minute, model, template_seq, surface, format, status, stored)) WITHOUT ROWID, STRICT;
CREATE TABLE audit_events (
  seq INTEGER PRIMARY KEY, workspace_id TEXT NOT NULL, at INTEGER NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL,     -- template.created | template.version_saved | template.alias_moved | template.archived | template.deleted |
                            -- decisions.deleted | decisions.redacted | decisions.exported | history.swept | settings.changed | file.deleted
  object_id TEXT, data TEXT) STRICT;
```

**Full-text search** is created by migration `0003_search.py` only when `sqlite_compileoption_used('ENABLE_FTS5')` is true:

```sql
CREATE VIRTUAL TABLE decision_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2');   -- rowid = decisions.seq
CREATE TRIGGER decisions_fts_delete AFTER DELETE ON decisions BEGIN DELETE FROM decision_fts WHERE rowid = OLD.seq; END;
```

This is a regular (content-storing) FTS5 table. It works on any FTS5 build, unlike `contentless_delete`, which needs SQLite 3.43. The indexed text is the masked `rendered_state` plus non-sensitive string variables.

**Hashes** are computed over canonical JSON: UTF-8, no insignificant whitespace, key order preserved unless stated.

| Hash | Covers |
|---|---|
| `content_hash` | A version's whole definition |
| `questions_hash` | The effective questions exactly |
| `question_set_key` | The effective questions with question keys sorted and option order preserved. Identical question sets sent in a different key order group together. |
| `input_hash` | The rendered state plus the ordered media sha256 list |
| `variables_hash` | The variables (with HMACs for sensitive ones) plus the media hashes. It pairs one input across versions whose state templates differ. |

### 5.3 Indexes and query plans

```sql
CREATE INDEX dec_ws        ON decisions(workspace_id, seq);                        -- the default History page
CREATE INDEX dec_created   ON decisions(workspace_id, created_at);                 -- time bounds -> seq bounds; retention
CREATE INDEX dec_tpl       ON decisions(template_seq, seq);                        -- per-template history
CREATE INDEX dec_tpl_ver   ON decisions(template_seq, version_number, model, seq); -- per-version history and operational stats
CREATE INDEX dec_pair      ON decisions(template_seq, version_number, model, variables_hash) WHERE variables_hash IS NOT NULL;
CREATE INDEX dec_model     ON decisions(workspace_id, model, seq);
CREATE INDEX dec_status    ON decisions(workspace_id, status, seq) WHERE status <> 'completed';   -- failures; queue recovery
CREATE INDEX dec_act       ON decisions(template_seq, act, seq);                   -- the review queue
CREATE INDEX dec_input     ON decisions(input_hash, seq);                          -- retry linking, pairing
CREATE INDEX dec_qset      ON decisions(question_set_key, seq);
CREATE INDEX dec_request   ON decisions(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX dec_group     ON decisions(group_id) WHERE group_id IS NOT NULL;
CREATE UNIQUE INDEX dec_batch_item ON decisions(batch_seq, custom_id) WHERE batch_seq IS NOT NULL;
CREATE INDEX dec_eval      ON decisions(eval_seq) WHERE eval_seq IS NOT NULL;
CREATE INDEX dec_rerun     ON decisions(rerun_of_seq) WHERE rerun_of_seq IS NOT NULL;
CREATE INDEX ans_filter    ON decision_answers(template_seq, question_key, answer, decision_seq);
CREATE INDEX ans_stats     ON decision_answers(template_seq, question_key, version_number, model, counted, origin, answer);
CREATE INDEX ans_certainty ON decision_answers(template_seq, question_key, certainty);
CREATE INDEX ans_any       ON decision_answers(question_key, answer, decision_seq);
CREATE INDEX meta_lookup   ON decision_metadata(key, value, decision_seq);
CREATE INDEX secrets_lookup ON decision_secrets(variable, hmac);
CREATE INDEX fb_stats      ON feedback(template_seq, question_key, version_number, model);
```

Estimated times at 100k decisions, to be confirmed by `scripts/bench_history.py` at 1M rows on Linux, macOS and Windows before release:

| Query | Plan | Estimate |
|---|---|---|
| History page | Backward scan of `dec_ws` with `LIMIT 21`, then primary-key probes into `decision_answers` for the summaries. `decision_bodies` is never read. | 1–3 ms |
| Time window | Two probes of `dec_created` turn the bounds into a `seq` range that every other index then filters | +1 ms |
| Answer filter | Driven by `ans_filter`, already ordered by `decision_seq`, so ORDER BY and LIMIT stay index-ordered | 1–5 ms |
| Stats by version | `GROUP BY` over the covering index `ans_stats` | 20–60 ms per question |
| Paired comparison | Index-only `GROUP BY variables_hash` on `dec_pair` per version, then a hash join in Python | < 100 ms |
| Latency percentiles | `total_ms` via `dec_tpl_ver`, computed in Python | < 20 ms |

A daily rollup table is deferred until histories exceed 1M rows. Exact stats from raw rows stay correct under deletion without any rollup bookkeeping.

### 5.4 Write path

**Synchronous decisions** write once, after the run, in **one transaction**:
1. `INSERT OR IGNORE question_sets`
2. `decisions`
3. `decision_bodies`
4. `decision_answers`
5. `decision_metadata`
6. `decision_media`
7. `decision_secrets`
8. `decision_fts`
9. the `idempotency_keys` completion
10. the `usage_counters` upsert
11. retry linking (`superseded`, `counted`)

This takes about 0.3–3 ms against 30–250 ms of model time. The transaction commits before the response is sent, so a `GET` right afterwards always finds the decision.

**Background and batch decisions** insert `decisions` (status `queued`) and `decision_bodies` (inputs only) first, then update them once when the run finishes. The trigger freezes the outputs after that.

**Rejected requests and `store: none` calls** write only `usage_counters`.

About 3 KB per decision (hot row and indexes about 0.5 KB, answers about 0.3 KB each, body 1–2 KB). Question text is deduplicated through `question_sets`. 100k decisions take about 300 MB, excluding media.

### 5.5 Media and files

**Upload, and inline `data:` URLs on any route.**
1. Stream to `blobs/tmp/`, hashing as the bytes arrive.
2. Sniff the kind with today's `MEDIA_EXT` and `_media_type` rules.
3. Rename atomically to `blobs/sha256/<2 hex>/<sha256>.<ext>`. If the blob exists, discard the copy.
4. `INSERT OR IGNORE blobs`.
5. Insert a `files` row with a **random** `file_<ulid>`.

Two uploads of the same bytes get two different file ids that share one blob. Content-derived ids would let one workspace discover that another holds a given file.

**Rules.**
- `_resolve_media` no longer writes random files into `uploads/`; the worker receives blob paths.
- Legacy upload ids resolve through `files.legacy_upload_id`.
- Blobs never live inside SQLite, so the database stays small, `VACUUM INTO` backups stay fast, and the worker reads plain files.
- A stored decision references its media through `decision_media.file_seq`.
- Under `answers_only`, or with `store_media: false`, `file_seq` is `NULL` and the type, byte count and sha256 remain. The decision then shows `"available": false`.

### 5.6 Retention sweeper

The sweeper (`history.sweep()`) runs 60 s after startup, then hourly, and right after a settings change. Each chunk is its own transaction, and the writer lock is released between chunks so live decisions keep committing.

1. **Per-template overrides, then the global rule**, in chunks of 2,000:

   ```sql
   DELETE FROM decisions WHERE seq IN (
     SELECT d.seq FROM decisions d LEFT JOIN batches b ON b.seq = d.batch_seq
     WHERE d.workspace_id = :ws AND d.created_at < :cutoff_ms AND d.pinned = 0 AND d.eval_seq IS NULL
       AND (:keep_labelled = 0 OR d.has_feedback = 0)
       AND (b.seq IS NULL OR b.status NOT IN ('queued','in_progress'))
       AND (d.template_seq IS NULL OR d.template_seq NOT IN (SELECT seq FROM templates WHERE retention_days IS NOT NULL))
     ORDER BY d.seq LIMIT 2000);
   ```

   Templates with `retention_days` get the same query with `d.template_seq = :t` and their own cutoff; `retention_days = 0` skips the template. Cascades remove bodies, answers, metadata, media links, secrets and feedback; the trigger removes full-text rows.
2. **The size cap.** While the database plus blobs exceed `max_storage_gb`, delete the oldest non-exempt decisions in chunks until usage is at 90% of the cap.
3. **Files.**
   - Delete expired unreferenced files: `expires_at < now` and no `decision_media`, `example_media`, batch or eval reference.
   - Then delete blobs no file references (row first, then unlink).
   - A weekly scan removes blob files that have no row.
4. **Housekeeping.**
   - Delete unreferenced `question_sets`.
   - Delete idempotency keys older than 24 hours, and `usage_counters` older than 400 days.
   - Delete tombstoned templates and orphan versions once no decision references them.
   - Run `PRAGMA incremental_vacuum(4000)`, `PRAGMA wal_checkpoint(TRUNCATE)` and `PRAGMA optimize`.
   - Write the `history.swept` audit event.

A large first sweep, for example after lowering retention on 1M rows, reports its progress in `/settings` (`storage.pending_deletion`) and throttles to 10 chunks per second.

### 5.7 Migrations

- **Files.** `basal/migrations/NNNN_name.sql` for schema, or `NNNN_name.py` for data (a `run(conn)` function).
- **When they run.** At startup, before the server accepts requests. When they take more than 2 s, the desktop app shows "Updating your history".
- **Transactions and checksums.** Each migration runs in its own transaction and records `(version, name, checksum)`. `PRAGMA user_version` mirrors the highest version. A changed checksum on an applied migration stops startup with a clear message.
- **Backups.** `VACUUM INTO DATA/backups/studio-m<NNNN>-<timestamp>.db` runs before the first pending migration. The last 3 backups are kept.
- **Forward-only.** Rolling back means restoring a backup.
- **A database newer than the code** (after a downgrade) opens read-only: History stays browsable, and writes are disabled with a notice.
- **Expand and contract.** Add nullable columns and tables first, backfill in background chunks, then tighten constraints in a later migration.
- **Stored JSON** carries `template_versions.format` and `decisions.format_version`, and is upgraded on read by pure functions, never rewritten.
- **Seed migrations:**
  - `0001_init.sql`: section 5.2.
  - `0002_import_activity.py`: each line of `data/activity.jsonl` becomes a decision.
    - `client == "Studio"` becomes `surface: "playground"`; path `/api/compare` becomes `compare`; everything else is `api` and `imported`.
    - The original `time` becomes `created_at`.
    - HTTP 200 becomes `completed`; anything else becomes `failed`.
    - `request` becomes the state and questions; `response.answers` becomes the answers (`raw_probabilities: null`).
    - `latency_ms` becomes `model_ms` and `wall_ms` becomes `total_ms`.
    - `path`, `format`, `client` and `request_id` go into `source`, and the old id goes into `metadata["basal.legacy_id"]`.

    Normal retention then applies. The file is renamed to `activity.jsonl.imported`.
  - `0003_search.py`: creates FTS when available, backfills it, and records `search_available`.

Built-in templates are seeded at startup by `templates.seed_builtins()`, which compares content hashes, not by a migration, because they change with releases.

### 5.8 Ready for more users

- **Ownership columns.** Every top-level row carries `workspace_id` (`ws_local` today) and `actor` or `created_by`. The hot indexes lead with `workspace_id` now; a constant value costs a few bytes and avoids an index rebuild later.
- **Keys and scopes.** The `api_keys` table exists, with scopes `decisions:write`, `history:read`, `history:delete`, `templates:read`, `templates:write` and `admin`. Today the local user and `BASAL_API_KEY` (`key_env`) hold `*`, and `insufficient_scope` is reserved.
- **Ids and URLs.** Decision, file and job ids are globally unique ULIDs. Template ids are unique per workspace. The workspace will come from the key, so no URL changes.
- **Workspace-scoped data.** File ids are already workspace-scoped over a shared blob store. Settings are per workspace.
- **Audit.** `audit_events` records every mutating change, never content.
- **Moving to Postgres.** All SQL goes through `basal/history.py` and `basal/db.py`, and nothing depends on SQLite-only features beyond FTS5 (which has a Postgres `tsvector` equivalent), STRICT and partial indexes.

---

## 6. Implementation plan

### 6.1 New modules and files

| File | Purpose | Key functions |
|---|---|---|
| `basal/ids.py` | ULIDs and prefixed ids (about 20 lines, no dependency) | `new_id(prefix)`, `ulid_time(id)` |
| `basal/db.py` | Opening the database, PRAGMAs, version and feature checks, the writer lock, read connections, the migration runner, backups, read-only mode | `open(path)`, `write(fn)`, `read()`, `migrate()` |
| `basal/migrations/0001_init.sql`, `0002_import_activity.py`, `0003_search.py` | Section 5.7 | |
| `basal/guard.py` | The cross-site guard, remote-access rules, and the browser JSON rule | `check(request) -> Response | None` |
| `basal/blobs.py` | The blob store and file registry; `data:` URL intake; the safe content response | `put_stream`, `put_data_url`, `resolve_media(body, ws)`, `content_response(file)` |
| `basal/templates.py` | Definition models (pydantic), variable validation, rendering, extension merge, canonical hashing, diffs and comparability, JSON Schema export, built-in seeding | `validate_definition`, `render(version, variables)`, `merge_questions(version, extras, add_options, skip)`, `resolve_settings(...)`, `diff(a, b)`, `schema(version)` |
| `basal/decisions.py` | The shared pipeline (section 4.3) for every entry point; gates; idempotency and retry linking | `resolve(body, source) -> Resolved`, `execute(resolved) -> Result`, `gate(answers, settings)` |
| `basal/history.py` | Recording at storage levels, projections, the query builder (filters, cursors), stats and what-if, comparisons, export, bulk delete and redact, the sweeper, usage counters | `record(result, level)`, `list(filters, page)`, `stats(...)`, `compare(...)`, `sweep()` |
| `basal/studio_api.py` | The FastAPI `APIRouter` mounted at `/v1/studio`: error envelope, operationIds, `?wait` | one handler per section 3.0 row |
| `basal/queue.py` (phase 3) | Per-model priority queues, background execution, restart recovery, SSE events | `submit(resolved, priority)`, `recover()`, `events(decision_id, after)` |
| `basal/evals.py`, `basal/batches.py` (phase 3) | Eval targets and metrics (ported from `evaluate.js`), apply; batches, replay, output files, comparison | |
| `basal/builtin_templates.json` + `scripts/export-builtins.mjs` | Built-ins generated from `ui/js/examples.js` and `ui/js/model-guides.js`. A test checks they are in sync. | |
| `basal/adapters/fake_adapter.py` | A deterministic test model (probabilities from a hash of state, question and option; all modalities; instant load). Registered in the catalog only when `BASAL_FAKE_MODEL=1`. | |
| `scripts/bench_history.py` | A 1M-row synthetic benchmark of the queries in section 5.3 | |

### 6.2 `basal/contract.py`

- **`Settings`** gains `act_threshold: float | None` (0 < t ≤ 1) and `questions: dict[str, QuestionSettings]`. `QuestionSettings` has `temperature`, `act_threshold` and `multi_threshold`. The worker's `SystemOneRequest` uses them.

  **Wire validation must not change**, so `api_compat.WireRequest` switches to its own temperature-only `WireSettings` (section 6.3).
- **`build_answers(qs, probs, temperature: float | Mapping[str, float] | None = None)`** (line 338) and **`_primitive_answers`** (line 374) take either one temperature or a mapping keyed by the client question id (`q.display_id`, so `multi` children use their parent's value). The existing single-float behaviour is unchanged.
- **New `raw_probabilities(qs, probs) -> dict[str, dict[str, float]]`**: normalised probabilities before temperature, keyed like each answer's `probabilities`. For `multi`, it gives P(applies) per option. It rounds to 6 decimals so temperature fitting is not lossy.
- **New `certainty(answer) -> float`** (the multi rule from `figures.js` `gateOf`) and **`gate(answer, threshold) -> bool`**.
- **No change to** `normalise`, `render`, the question models or the answer shapes.

### 6.3 `basal/api_compat.py`

- **`WireSettings(BaseModel)`** has `temperature` only and `extra="ignore"`. `WireRequest.settings` uses it, so the published wire validation (and `test_conformance.py`) is byte-for-byte unchanged.
- **New `studio_extensions(raw, headers) -> WireExtensions(store, metadata, act_threshold, warnings)`** implements the lenient rules of section 4.7.
- **New `render_stored(fmt, decision, extended) -> dict`** for `GET /v1/studio/decisions/{id}?format=`. It reuses `shape` on the stored answers.
- **New `error_body` cases** for 403 `cross_site_request`, the 409 idempotency errors and 503 `store_failed`, in each wire format.
- **`shape`** is unchanged. The recording code must pass it the worker response untouched and record from a deep copy.

### 6.4 `basal/worker.py` and `basal/workers.py`

- In `/decide`, build `temps = {q: s.temperature for q, s in req.settings.questions.items() if s.temperature}`. Pass `temps or req.settings.temperature` to `build_answers` (line 179), using the per-question value when present and `req.settings.temperature` otherwise.
- Add `raw_probabilities: contract.raw_probabilities(qs, out.probs)` at the **top level** of the response, never inside `answers`, so `shape(extended=True)` cannot leak it onto the wire.
- `workers.decide` is unchanged. Phase 3 adds per-model queues in front of it (`basal/queue.py`) and forwards `health.stage` and `progress` to SSE `decision.model_loading` events.

### 6.5 `basal/server.py`

- **Remove** `history` (the deque), `ACTIVITY_FILE`, `_load_activity` and `_record`.
- **`lifespan`:**
  - `db.open(DATA / "studio.db")` then `db.migrate()`;
  - `templates.seed_builtins()`;
  - start the `history.sweep` loop;
  - phase 3: `queue.recover()`;
  - keep the `uploads/` 24-hour sweep for one release.
- **`auth_and_timing`:**
  - run `guard.check` first;
  - choose the error envelope by prefix: `/v1/studio` gets the OpenAI-style envelope, with 401 for a missing key; the wire routes keep TypeSafe's 403 and 401;
  - add `remote_access_requires_key`;
  - add `x-request-id` on `/v1/studio`.
  - `PUBLIC_API_PREFIXES` already covers `/v1/studio`.
- **`_resolve_media`** becomes `blobs.resolve_media`.
- **`_run`** becomes a thin wrapper over `decisions.execute` (kept for `/api/compare` until phase 3).
- **`_wire`:**
  1. read the raw body;
  2. `api_compat.studio_extensions`;
  3. idempotency check;
  4. `api_compat.parse` (unchanged);
  5. `decisions.execute(… source …)`;
  6. `api_compat.shape`;
  7. add the `x-basal-*` headers;
  8. make the history write in an isolated `try` (section 4.7).
- **Shims:**
  - `GET /api/history` maps decisions to the old entry shape `{id, time, model, status, questions, latency_ms, wall_ms, media, path, format, client, request_id, request, response}`, newest 500, for one release.
  - `DELETE /api/history` becomes a bulk delete (`all: true`, pinned kept).
  - `/api/compare` becomes a wrapper over comparisons (phase 3).
  - `/api/uploads` becomes an alias of `POST /v1/studio/files`.
- **`app.include_router(studio_api.router, prefix="/v1/studio", tags=["Studio API"])`**, with `generate_unique_id_function` producing the `studio.<namespace>.<verb>` operationIds.

### 6.6 UI

| File | Change |
|---|---|
| `ui/js/app.js` | Routes: `history` becomes the page (`activity` is now an alias of it, reversing today's `ALIASES`); add `templates` (`#/templates`, `#/templates/<id>`). The nav shows History under Developer and Templates beside Playground. |
| `ui/js/store.js` | `decide()` posts to `/v1/studio/decisions` with `X-Basal-Surface` and `settings.act_threshold` from the slider. It returns the decision object plus `meta.decisionId`. `errorText` already reads `error.message`. Add a `studio(path, opts)` helper. |
| `ui/js/pages/history.js` (new, replaces `activity.js`) | Two panes: a filter bar (model, template, version, time range, status, surface, act, answer), the list (`view=summary`, retries folded, live tail), and an inspector (answers through `figures()`, input, settings with a "why" popover from `sources`, feedback, metadata). Actions: Label, Add to examples, Rerun on…, Open in Playground, Save as template, Pin, Delete. Charts from stats and `usage_counters`. A first-run notice (PO-3). A review-queue tab (`act=false`). |
| `ui/js/pages/templates.js` (new) | Library: the user's templates, a "Starter templates" section of built-ins, a Show archived toggle, New template. Template page tabs: **Definition** (read-only view, versions with notes, diff viewer, aliases, compatibility, archive/delete), **History** (per-template list), **Compare versions** (stats by version with comparability badges, the `/compare` view, "Replay recent traffic on vN"), **Examples** (table, import, add from history), **Evaluate** (eval runs, targets picker, apply). One job per screen, depth one click away. |
| `ui/js/pages/playground.js` | A template picker in the header, and a variable form built from `/schema` (new `ui/js/template-form.js`). Template questions render as locked rows; extra rows are marked "added for this run"; option chips on extensible questions are marked "added". **Save as template** / **Save version** (a dialog with a note, `If-Match`, and a conflict message offering "Reload"). "Keep in history" switch. The threshold and temperature go into every request. Rewording a locked row switches to draft attribution, with a banner offering "Save as version N+1". Compare and order test go through comparisons. The result stamp shows "Saved · dec_…", linking to History. |
| `ui/js/builder.js` | `rowHTML` supports `locked` and `added` states; `buildQuestions` returns `{questions, add_options, skip}` in template mode. |
| `ui/js/figures.js` | `gateOf` prefers `a.act` and `a.certainty` when present; the local threshold is used only for what-if previews. |
| `ui/js/snippets.js`, Code tab | Show the `/v1/studio` call and the equivalent `/v1/systemone` call (from `preview.systemone_request`). |
| `ui/js/pages/evaluate.js` | Phase 1: tag calls with `X-Basal-Surface: eval`. Phase 3: move to `POST /v1/studio/evals` (inline dataset; "Save as template examples"; apply when a template is open). |
| `ui/js/pages/api.js` | Document `/v1/studio`, the storage notice and every opt-out; link `/openapi.json`. |
| `README.md` | "Use it from code" gains the store-by-default notice, the opt-outs and the migration ladder (section 1.5). |

### 6.7 Tests to add

| Test | Covers |
|---|---|
| `tests/test_conformance.py` (extend) | The 14 existing tests unchanged, plus: wire bodies are byte-identical with and without storage; a malformed `store` or `metadata` never causes a 422 and yields `x-basal-warning`; `x-basal-decision-id` and `x-basal-stored` values; `Idempotency-Key` replay on `/v1/systemone`; `X-TypeSafe-Retry-Count` linking; the official Python and JS SDKs still pass. |
| `tests/test_guard.py` | Cross-site `Origin` gets 403 on `/v1/systemone` and `/v1/studio`; `Sec-Fetch-Site: same-site` gets 403; no `Origin` (curl with a form content type) passes; same-origin on ports 8420 and 8437 passes; `BASAL_CORS_ORIGINS` passes; a browser `text/plain` write to `/v1/studio` gets 415; `remote_access_requires_key`. |
| `tests/test_contract.py` | Per-question temperature (including `multi` children); `raw_probabilities` round trip; `certainty` and `gate` for all six types; single-float behaviour unchanged. |
| `tests/test_templates.py` | Every variable type and error code; substitution rules S1–S10 (whole-value typing, key removal, single pass, escaping, text rendering); `trusted`; `options` variables; extension rules and error codes; the settings ladder and `sources` (including request-beats-template-per-question); canonical hashing and the no-op save; diff classes and comparability for every case; JSON Schema export. |
| `tests/test_history_store.py` | Temporary-database unit tests: `full`, `answers_only` and `none` persistence (including "nothing on disk" for `none`); the frozen-output and immutable-version triggers; projections for `multi` and `number`; every list filter and cursor stability under inserts; stats and `what_if`; retention with exemptions and the size cap; media GC; HMAC erasure; migrations (backup, checksum refusal, read-only when newer, activity import mapping). |
| `tests/test_studio_api.py` | Against a live server with `BASAL_FAKE_MODEL=1`: the five calls of section 3.1 verbatim; PUT upsert 201/200/unchanged; `If-Match` 412; archived templates callable with a warning; delete with keep and delete; preview parity with create; `?wait`; feedback and examples round trip; eval targets and flips; `store:none` batch output-file expiry; file security headers; the error envelope and a 400 for every validation code. |
| `tests/test_systemone_superset.py` | For every scenario in `ui/js/examples.js` (via the built-ins JSON): the same body to `/v1/systemone` (with `X-Basal-Extensions: 1`) and `/v1/studio/decisions` on the fake model gives identical `answers` keys and identical TypeSafe fields. |
| `scripts/e2e.py` (extend) | History page filters and inspector; Save as template from the Playground; the template form run; a version save conflict; per-template history; the Evaluate flow. |

### 6.8 Phased order of work

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Groundwork** (about 2 days) | `ids.py`, `db.py`, the migration runner and `0001`; the fake adapter; **`guard.py` shipped on its own**, which closes the cross-site gap on today's routes before history exists | Guard tests pass; conformance 14/14 |
| **1. History everywhere** (about 1 week) | Contract and worker raw probabilities and gates; `history.record` with storage levels; wire recording with lenient extensions, headers, idempotency, retry linking and failure isolation; `usage_counters`; blobs and files (random ids); `POST /v1/studio/decisions` **ad hoc**, preview (ad hoc), retrieve, `/input`, list with filters and cursors, PATCH, delete, redact, bulk delete, export, stats (model, day, surface, `what_if`); settings; retention sweeper; activity import; the `/api/history` shim; UI: History page with notice, Playground via `/v1/studio` with threshold and temperature in settings, Keep-in-history switch | Requirements 1 and 4 met for ad hoc traffic; every wire test is byte-identical; `test_history_store` green; e2e History passes |
| **2. Templates** (about 2 weeks) | `templates.py`; every template endpoint (create, PUT, PATCH, versions, diff, restore, aliases, archive, delete, schema, compatibility); built-ins; templated decisions with extensions, the settings ladder and per-question temperature; full preview; background plus `?wait`; `X-Basal-Template` attribution; feedback; examples with revisions; per-template history and stats by version with comparability; models endpoints; UI: Templates library and page (Definition, History, Examples), Playground template mode, Save as template or version, draft attribution, Code tab | Requirements 2 and 3 met; the migration ladder in section 1.5 works end to end; `test_templates` and `test_studio_api` (phase-2 parts) green |
| **3. Evaluate and compare** (about 1.5 weeks) | `queue.py` (priorities, restart recovery); SSE streaming and the events feed; comparisons (models, versions, option order) replacing `/api/compare`; evals (targets, datasets, apply) replacing the Evaluate page's loop; `/templates/{id}/compare`; batches (items, file, replay, `store:none` output files, comparison); UI: Compare versions tab, Evaluate tab, live History | Version comparison works four ways; `test_studio_api` complete; bench numbers recorded |
| **4. Later** | Inferred attribution (linking past untemplated decisions by `question_set_key`); template export and import bundles; scoped API keys and workspaces; optional encryption at rest; daily rollups beyond 1M rows | none |

### 6.9 Risks to watch

- **Privacy and disk.**
  - Full states and media are kept for 30 days by default, unencrypted, on the user's machine.
  - Mitigations: the notice, `answers_only`, sensitive variables, per-template storage ceilings, `store_media`, retention and the size cap.
  - A low-disk guard that stops keeping media, and warns, is part of phase 1.
- **Retries.** Heuristic retry linking can mislink two genuinely identical calls from the same client within 120 s. It only folds them in views; nothing is deleted. SDK users should send `Idempotency-Key`.
- **Extra questions cost context.** "Adding questions does not change the template's answers" holds for single-pass independent scoring. On small-context models such as Laya (512 tokens), extras consume context and can truncate the state. `state_may_be_truncated` warns, and stats exclude extended runs by default.
- **Weight updates.** Updated weights change behaviour under the same model id. `model_revision` is recorded and `group_by=model_revision` exists, but it is not the default grouping.
- **Wording changes.** `text_changed` can hide real behavioural change. Stats flag it, and eval comparisons on the same `dataset_hash` are the reliable check.
- **Namespace.** If OpenAI's Decisions API lands under `/v1/studio`, which is unlikely, the namespace would need an alias. `/v1/decisions` stays free by design.
- **Unverified assumption.** The claim that TypeSafe's live service ignores `store` and `metadata` in the body is unverified (SPEC_SUMMARY: "[UNVERIFIED]"). `X-Basal-Store` is the portable fallback.