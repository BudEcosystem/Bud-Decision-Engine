"""What the interactive reference (/docs, /openapi.json) shows for the studio API: request bodies with ready-to-run
examples, and the main query filters. The handlers read requests themselves (so every error has one envelope), so
FastAPI cannot infer these; they are declared here, per operationId, and attached by studio_api.route().
"""
from __future__ import annotations

PATH_PARAMS = {
    "did": "A decision id, such as dec_01JB7Q2M4X9V3K8T6R1N5P0HZC.",
    "tid": "A template id, such as support-triage (starter templates are builtin/<name>).",
    "n": "A version number, an alias such as production, or latest.",
    "alias": "An alias name, such as production.",
    "eid": "A test example id, such as ex_01JB7S1D0K3M5N7P9Q1R3S5T7V.",
    "fid": "A feedback id (fb_...) or a file id (file_...).",
    "mid": "A model id, such as laya.",
}

SETTINGS = {"type": "object", "description": "act_threshold (0 to 1), temperature (0 to 20) and per-question values under questions.",
            "properties": {"act_threshold": {"type": "number"}, "temperature": {"type": "number"},
                           "questions": {"type": "object", "additionalProperties": {"type": "object"}}}}
QUESTIONS = {"type": "object", "description": "Question key to question: {type: choice | score | noul | multi | rank | number, "
                                            "instructions, criteria}, exactly as /v1/systemone takes them.",
             "additionalProperties": {"type": "object"}}

DECISION = {
    "type": "object",
    "properties": {
        "template": {"type": "string", "description": "A template reference: support-triage, support-triage@3 or support-triage@production. "
                                                      "Leave it out for a decision without a template."},
        "variables": {"type": "object", "description": "The template's variables."},
        "state": {"description": "The situation, for a decision without a template or a template without variables: text, an object or a list."},
        "questions": QUESTIONS,
        "add_options": {"type": "object", "description": "New options for template questions that allow it."},
        "skip": {"type": "array", "items": {"type": "string"}, "description": "Template questions to leave out, where allowed."},
        "media": {"type": "array", "items": {"type": "object"}, "description": "[{type?, file_id | data: 'data:image/png;base64,...', name?}]"},
        "model": {"type": "string", "description": "A model id; defaults to the template's model, then the most recently loaded one."},
        "settings": SETTINGS,
        "metadata": {"type": "object", "description": "Up to 16 string values, such as a ticket id. Filterable in History."},
        "store": {"description": "true (default), false, \"full\", \"answers_only\" or \"none\". Only lowers what History keeps."},
        "background": {"type": "boolean", "description": "Return at once with status queued; then GET /decisions/{id}?wait=60."},
        "include": {"type": "array", "items": {"type": "string"},
                    "description": "input, input.rendered_state, answers.raw_probabilities, answers.model_extras"},
    },
}

TEMPLATE = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Lower-case letters, digits, - and _ (POST only; PUT takes it from the path)."},
        "name": {"type": "string"}, "description": {"type": "string"}, "note": {"type": "string", "description": "Why this version was saved."},
        "modalities": {"type": "array", "items": {"type": "string"}},
        "variables": {"type": "object", "description": "Name to {type: string | integer | number | boolean | json | options | image | audio | "
                                                      "video, required?, default?, enum?, max_length?, sensitive?, description?}."},
        "state": {"description": "The state template: text or JSON with {{variable}} placeholders; null makes the variables the state."},
        "questions": QUESTIONS,
        "model": {"type": "string"},
        "settings": {**SETTINGS, "description": SETTINGS["description"] + " models.<model id> holds values for one model."},
        "extensions": {"type": "object", "description": "{questions: bool, max_questions, options: [keys] | true, skip: [keys] | true}"},
        "metadata": {"type": "object"}, "storage": {"type": "string"}, "retention_days": {"type": "integer"},
        "from": {"type": "object", "description": "{\"template\": \"builtin/support@1\"} to clone, or {\"decision\": \"dec_...\"} to start from a decision."},
    },
}

SUPPORT = {
    "name": "Support triage",
    "description": "Route inbound tickets and rate urgency.",
    "variables": {"customer_message": {"type": "string", "description": "The message, verbatim."},
                  "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"}},
    "state": {"tier": "{{account_tier}}", "message": "{{customer_message}}"},
    "questions": {"department": {"type": "choice", "instructions": "Which department should handle this?",
                                 "criteria": {"billing": "invoices, payments, refunds", "technical": "bugs, outages", "sales": "pricing, upgrades"}},
                  "urgent": {"type": "noul", "instructions": "Does it need an answer today?"}},
    "settings": {"act_threshold": 0.85},
    "extensions": {"questions": True, "options": ["department"], "skip": ["urgent"]},
}


def _body(schema: dict, examples: dict) -> dict:
    return {"requestBody": {"required": True, "content": {"application/json": {
        "schema": schema,
        "examples": {k: {"summary": s, "value": v} for k, (s, v) in examples.items()}}}}}


def _query(*params) -> dict:
    return {"parameters": [{"name": n, "in": "query", "required": False, "description": d, "schema": {"type": "string"}}
                           for n, d in params]}


FILTERS = [("template", "support-triage, support-triage@2, support-triage@production, or none"),
           ("model", "Model ids, comma-separated"), ("status", "completed, failed, queued"),
           ("act", "false for the decisions that asked a human"), ("created_after", "-24h, -7d, Unix seconds or RFC 3339"),
           ("created_before", "Same forms as created_after"), ("answer.<question>", "For example answer.department=billing"),
           ("metadata.<key>", "Exact match, such as metadata.ticket_id=T-4411"), ("q", "Words in the situation"),
           ("labelled", "true for decisions with feedback"), ("pinned", "true"),
           ("surface", "api, playground, compare, rerun, eval, all"), ("limit", "1 to 100 (default 20)"),
           ("after", "The last id of the previous page"), ("order", "desc (default) or asc")]

DOCS = {
    "decisions.create": _body(DECISION, {
        "starter": ("Run a starter template on a situation",
                    {"template": "builtin/support", "state": "We were billed twice for March. Refund the duplicate today or we cancel."}),
        "template": ("Run your template with variables",
                     {"template": "support-triage", "variables": {"customer_message": "The app crashes when I open invoices.", "account_tier": "pro"}}),
        "adhoc": ("A decision without a template (the /v1/systemone body works as it is)",
                  {"model": "laya", "state": "The package arrived crushed and the screen is cracked.",
                   "questions": {"damaged": {"type": "noul", "instructions": "Was the item damaged?"}}}),
        "nostore": ("Keep nothing in History",
                    {"template": "builtin/support", "state": "Please cancel my plan.", "store": False}),
    }),
    "decisions.preview": _body(DECISION, {"template": ("Check a request without running a model",
                                                       {"template": "support-triage", "variables": {"customer_message": "Hi", "account_tier": "gold"}})}),
    "decisions.list": _query(*FILTERS, ("view", "summary (default) or full"), ("fold_retries", "true (default) hides superseded SDK retries")),
    "templates.decisions.list": _query(*[f for f in FILTERS if f[0] != "template"], ("version", "Version numbers, comma-separated")),
    "decisions.stats": _query(("group_by", "version, template, model, surface, client, format, status, day, hour"),
                              ("what_if.act_threshold", "Recompute the act rate at another threshold"), *FILTERS[:6]),
    "templates.stats": _query(("group_by", "version (default), model, day, hour"), ("what_if.act_threshold", "Another threshold"),
                              ("created_after", "-7d, -30d")),
    "templates.compare": _query(("versions", "Two versions, such as 1,2 (default: the latest two)"), ("model", "One model id"),
                                ("created_after", "-7d, -30d")),
    "decisions.export": _query(("format", "jsonl (default) or csv"), *FILTERS[:6]),
    "decisions.update": _body({"type": "object", "properties": {"metadata": {"type": "object"}, "pinned": {"type": "boolean"}}},
                              {"pin": ("Pin, so retention keeps it", {"pinned": True, "metadata": {"reviewed_by": "ops"}})}),
    "decisions.redact": _body({"type": "object", "properties": {"fields": {"type": "array", "items": {"type": "string"}}}},
                              {"redact": ("Remove the situation, keep the answers", {"fields": ["input.state", "input.variables"]})}),
    "decisions.bulk_delete": _body({"type": "object", "properties": {"filter": {"type": "object"}, "all": {"type": "boolean"},
                                                                     "dry_run": {"type": "boolean"}, "include_pinned": {"type": "boolean"}}},
                                   {"dry": ("How many decisions of a template would go", {"filter": {"template": "support-triage", "created_before": "-30d"}, "dry_run": True}),
                                    "erase": ("Erase everything containing one sensitive value", {"filter": {"sensitive_hash": {"customer_email": "ana@example.com"}}})}),
    "decisions.bulk_redact": _body({"type": "object", "properties": {"filter": {"type": "object"}, "fields": {"type": "array", "items": {"type": "string"}},
                                                                     "dry_run": {"type": "boolean"}}},
                                   {"redact": ("Remove situations older than a week", {"filter": {"created_before": "-7d"}, "fields": ["input.state"]})}),
    "decisions.rerun": _body({"type": "object", "properties": {"model": {"type": "string"}, "template": {"type": "string"},
                                                               "settings": SETTINGS, "variables": {"type": "object"}, "store": {}}},
                             {"model": ("The same input on another model", {"model": "kev-4b"})}),
    "decisions.feedback.create": _body({"type": "object", "properties": {"expected": {"type": "object"}, "rating": {"type": "integer"}, "note": {"type": "string"}}},
                                       {"label": ("The right answers (scales take the level number)", {"expected": {"department": "billing", "urgent": "yes"}, "note": "Checked on the call."})}),
    "templates.create": _body(TEMPLATE, {"new": ("A template with two variables", {"id": "support-triage", **SUPPORT}),
                                         "clone": ("Clone a starter template", {"id": "my-support", "from": {"template": "builtin/support@1"}})}),
    "templates.upsert": _body(TEMPLATE, {"put": ("Create, or save a new version when it changed", SUPPORT)}),
    "templates.update": _body(TEMPLATE, {"patch": ("Add a question and act a little sooner (a new version)",
                                                   {"note": "Ask about refunds", "questions": {"refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}},
                                                    "settings": {"act_threshold": 0.8}})}),
    "templates.versions.create": _body(TEMPLATE, {"version": ("A full definition as the next version", {**{k: v for k, v in SUPPORT.items() if k not in ("name", "description")}, "note": "Tighter wording", "base_version": 1})}),
    "templates.versions.restore": _body({"type": "object", "properties": {"note": {"type": "string"}}}, {"restore": ("Back to an old version", {"note": "Back to version 1"})}),
    "templates.aliases.set": _body({"type": "object", "properties": {"version": {"type": "integer"}}}, {"promote": ("Promote version 2", {"version": 2})}),
    "templates.examples.create": _body({"type": "object"}, {
        "example": ("An input with its right answers", {"variables": {"customer_message": "I was charged twice.", "account_tier": "pro"}, "expected": {"department": "billing"}, "tags": ["regression"]}),
        "promote": ("From a decision in History", {"from_decision": "dec_01JB7Q2M4X9V3K8T6R1N5P0HZC", "tags": ["from-review"]})}),
    "templates.examples.import_": _body({"type": "object", "properties": {"examples": {"type": "array", "items": {"type": "object"}}}},
                                        {"import": ("Several at once", {"examples": [{"variables": {"customer_message": "Refund please"}, "expected": {"department": "billing"}}]})}),
    "templates.examples.update": _body({"type": "object"}, {"tags": ("Change the tags (a new revision)", {"tags": ["regression", "billing"]})}),
    "templates.list": _query(("origin", "user or builtin"), ("q", "Words in the id, name or description"), ("include_archived", "true"),
                             ("compatible_with", "A model id"), ("limit", "1 to 100"), ("after", "A template id from the previous page")),
    "templates.delete": _query(("confirm", "The template id again (required)"), ("history", "keep (default) or delete")),
    "templates.retrieve": _query(("version", "A version number or alias"), ("include", "compatibility")),
    "decisions.retrieve": _query(("wait", "Up to 60 seconds, for a background decision"), ("include", "input.rendered_state, answers.raw_probabilities"),
                                 ("format", "typesafe, openrouter, vercel or evaluate: the answers as that API would return them")),
    "files.create": {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "properties": {"file": {"type": "string", "format": "binary", "description": "An image, audio or video file."}}}}}}},
    "settings.update": _body({"type": "object", "properties": {"history": {"type": "object"}, "decisions": {"type": "object"}}},
                             {"retention": ("Keep decisions for 90 days", {"history": {"retention_days": 90}}),
                              "off": ("Keep nothing", {"history": {"store": "none"}})}),
}


def openapi_extra(op: str, path: str) -> dict | None:
    extra = dict(DOCS.get(op) or {})
    params = [{"name": n, "in": "path", "required": True, "description": d, "schema": {"type": "string"}}
              for n, d in PATH_PARAMS.items() if "{" + n + "}" in path]
    if params:
        extra["parameters"] = params + list(extra.get("parameters", []))
    return extra or None
