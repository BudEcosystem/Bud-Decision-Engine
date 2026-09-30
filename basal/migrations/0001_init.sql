-- Bud Decision Studio history: templates, decisions, feedback, examples, files and settings (docs/studio-api.md, section 5).
-- Times are Unix milliseconds; the API shows seconds. Every top-level row carries workspace_id ('ws_local' today).

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
  position INTEGER NOT NULL DEFAULT 0,        -- the question's place in the effective question order
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

-- ------------------------------------------------------------------ indexes
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
