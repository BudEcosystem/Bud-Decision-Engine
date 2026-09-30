"""Template logic (basal/templates.py): checking definitions, filling in variables, extensions, the settings ladder,
hashing and version comparability. Pure functions; no server, no database.

    .venv/bin/python -m pytest tests/test_templates.py -q
"""
import json
import subprocess
from pathlib import Path

import pytest

from basal import templates as T
from basal.errors import ApiError, Problems

ROOT = Path(__file__).resolve().parent.parent
SECRET = b"test-secret"

SUPPORT = {
    "modalities": ["text", "image"],
    "variables": {
        "customer_message": {"type": "string", "description": "The message, verbatim.", "max_length": 8000},
        "account_tier": {"type": "string", "enum": ["free", "pro", "enterprise"], "default": "free"},
        "open_invoices": {"type": "integer", "minimum": 0, "required": False},
        "customer_email": {"type": "string", "max_length": 320, "required": False, "sensitive": True},
        "screenshot": {"type": "image", "required": False},
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
    },
    "model": "laya",
    "settings": {"act_threshold": 0.9, "temperature": 1.1, "questions": {"churn_risk": {"act_threshold": 0.7}},
                 "models": {"laya": {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}}},
    "extensions": {"questions": True, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]},
}


def codes(body) -> list[tuple[str, str]]:
    with pytest.raises(ApiError) as e:
        T.check_definition(body)
    return [(d["code"], d["param"]) for d in e.value.details]


def render(defn, variables=None, state=None):
    p = Problems()
    r = T.render(defn, variables, state, SECRET, p)
    return r, [(i["code"], i["param"]) for i in p.items]


@pytest.fixture(scope="module")
def support():
    return T.check_definition(SUPPORT).definition


# ---------------------------------------------------------------------------------------------------- definitions


def test_normalises_variables_and_defaults(support):
    v = support["variables"]
    assert v["customer_message"] == {"type": "string", "description": "The message, verbatim.", "max_length": 8000,
                                     "required": True, "sensitive": False, "trusted": False}
    assert v["account_tier"]["required"] is False          # a default makes a variable optional
    assert support["extensions"] == {"questions": True, "max_questions": 8, "options": ["department"], "skip": ["churn_risk"]}
    assert support["modalities"] == ["text", "image"]


def test_closed_vocabulary_is_allowed_in_question_text(support):
    assert "{{account_tier}}" in support["questions"]["urgency"]["instructions"]


def test_free_text_in_question_needs_trusted():
    body = {"variables": {"topic": {"type": "string"}}, "state": "{{topic}}",
            "questions": {"q": {"type": "noul", "instructions": "Is this about {{topic}}?"}}}
    assert ("untrusted_variable_in_question", "questions.q.instructions") in codes(body)
    body["variables"]["topic"]["trusted"] = True
    T.check_definition(body)


@pytest.mark.parametrize("body,expected", [
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}} {{nope}}", "questions": {"q": {"type": "noul"}}},
     ("undeclared_variable", "state")),
    ({"variables": {"m": {"type": "string"}, "pic": {"type": "image"}}, "modalities": ["text"], "state": "{{m}}",
      "questions": {"q": {"type": "noul"}}}, ("modality_not_declared", "variables.pic")),
    ({"variables": {"m": {"type": "string"}, "pic": {"type": "image"}}, "state": "{{m}} {{pic}}",
      "questions": {"q": {"type": "noul"}}}, ("media_variable_in_text", "state")),
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}}",
      "questions": {"q": {"type": "choice", "criteria": {"{{m}}": "x", "b": None}}}}, ("placeholder_not_allowed", "questions.q.criteria")),
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}}",
      "questions": {"q": {"type": "score", "criteria": "{{m}}"}}}, ("placeholder_not_allowed", "questions.q.criteria")),
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}}", "questions": {"bad key": {"type": "noul"}}},
     ("invalid_field", 'questions["bad key"]')),
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}}", "questions": {"q": {"type": "noul"}},
      "extensions": {"options": ["q"]}}, ("options_not_extensible", "extensions.options[0]")),
    ({"variables": {"m": {"type": "string"}}, "state": "{{m}}", "questions": {"q": {"type": "noul"}},
      "settings": {"temperature": 50}}, ("invalid_field", "settings.temperature")),
    ({"variables": {"m": {"type": "string", "default": 5}}, "state": "{{m}}", "questions": {"q": {"type": "noul"}}},
     ("invalid_field", "variables.m.default")),
    ({"state": "fixed", "questions": {"q": {"type": "noul"}}}, ("invalid_definition", "state")),
])
def test_save_errors(body, expected):
    assert expected in codes(body)


def test_all_problems_reported_together():
    got = codes({"variables": {"m": {"type": "string"}}, "state": "{{m}} {{a}} {{b}}", "questions": {"q": {"type": "nope"}}})
    assert len(got) >= 3


def test_unused_variable_warns():
    c = T.check_definition({"variables": {"m": {"type": "string"}, "unused": {"type": "integer"}}, "state": "{{m}}",
                            "questions": {"q": {"type": "noul"}}})
    assert [w["code"] for w in c.warnings] == ["variable_unreferenced"]


def test_default_model_incompatible_warns():
    c = T.check_definition({"variables": {"m": {"type": "string"}}, "state": "{{m}}", "model": "laya",
                            "questions": {"q": {"type": "choice", "criteria": [str(i) for i in range(30)]}}})
    assert any(w["code"] == "default_model_incompatible" for w in c.warnings)


# ---------------------------------------------------------------------------------------------------- substitution


def test_whole_value_typing_and_key_removal(support):
    r, errs = render(support, {"customer_message": "Refund today or we cancel.", "account_tier": "enterprise"})
    assert errs == []
    assert r.state == {"customer": {"tier": "enterprise"}, "message": "Refund today or we cancel."}
    r, _ = render(support, {"customer_message": "x", "open_invoices": 3})
    assert r.state["customer"] == {"tier": "free", "open_invoices": 3}      # stays a number


def test_text_state_rendering():
    d = T.check_definition({"variables": {"tier": {"type": "string"}, "n": {"type": "integer", "required": False},
                                          "msg": {"type": "string"}},
                            "state": "Plan: {{tier}}\nOpen invoices: {{n}}\n\n{{msg}}", "questions": {"q": {"type": "noul"}}}).definition
    r, _ = render(d, {"tier": "enterprise", "msg": "Billed twice"})
    assert r.state == "Plan: enterprise\nOpen invoices: \n\nBilled twice"


def test_single_pass_and_escaping():
    d = T.check_definition({"variables": {"a": {"type": "string"}, "b": {"type": "string", "required": False}},
                            "state": "A={{a}} B={{b}} literal=\\{{a}}", "questions": {"q": {"type": "noul"}}}).definition
    r, _ = render(d, {"a": "{{b}}", "b": "SECRET"})
    assert r.state == "A={{b}} B=SECRET literal={{a}}"      # a value is never scanned again


def test_default_state_from_variables():
    d = T.check_definition({"variables": {"ticket": {"type": "string"}, "tier": {"type": "string", "required": False}},
                            "state": None, "questions": {"q": {"type": "noul"}}}).definition
    r, _ = render(d, {"ticket": "hi"})
    assert r.state == {"ticket": "hi"}


def test_variable_errors_with_did_you_mean(support):
    _, errs = render(support, {"acount_tier": "pro", "open_invoices": "3"})
    assert ("unknown_variable", "variables.acount_tier") in errs
    assert ("missing_variable", "variables.customer_message") in errs
    assert ("invalid_variable", "variables.open_invoices") in errs          # "3" is not an integer: no coercion
    p = Problems()
    T.render(support, {"acount_tier": "pro"}, None, SECRET, p)
    assert "Did you mean 'account_tier'?" in p.items[0]["message"]


def test_sensitive_values_are_masked(support):
    r, _ = render(support, {"customer_message": "x", "customer_email": "ana@example.com"})
    assert r.state["customer"]["email"] == "ana@example.com"                # the model sees it
    assert r.stored_state["customer"]["email"] == "[redacted:customer_email]"
    assert r.stored_variables["customer_email"] == {"$redacted": T.secret_hash(SECRET, "ana@example.com")}
    assert "ana@example.com" not in json.dumps(r.stored_variables)


def test_state_rules_for_raw_state_templates():
    d = T.check_definition({"questions": {"q": {"type": "noul"}}}).definition
    _, errs = render(d, None, None)
    assert ("state_required", "state") in errs
    _, errs = render(d, {"x": 1}, "hi")
    assert ("variables_need_template", "variables") in errs
    r, errs = render(d, None, {"ticket": "hi"})
    assert errs == [] and r.state == {"ticket": "hi"}


def test_empty_state():
    d = T.check_definition({"variables": {"m": {"type": "string", "required": False}}, "state": "{{m}}",
                            "questions": {"q": {"type": "noul"}}}).definition
    _, errs = render(d, {})
    assert ("empty_state", "variables") in errs


def test_options_variable_binds_criteria():
    d = T.check_definition({"variables": {"goal": {"type": "string"}, "actions": {"type": "options"}}, "state": "{{goal}}",
                            "questions": {"next": {"type": "choice", "instructions": "What next?", "criteria": "{{actions}}"}}}).definition
    r, errs = render(d, {"goal": "book", "actions": ["click search", "type date"]})
    assert errs == [] and r.questions["next"]["criteria"] == ["click search", "type date"]
    assert r.dynamic == {"next"}


# ---------------------------------------------------------------------------------------------------- extensions


def merge(support, extras=None, add=None, skip=None):
    r, _ = render(support, {"customer_message": "x"})
    p = Problems()
    m = T.merge_questions(r.questions, r.dynamic, support["extensions"], extras, add, skip, p)
    return m, [(i["code"], i["param"]) for i in p.items]


def test_extension_order_and_origins(support):
    m, errs = merge(support, {"damage": {"type": "noul", "instructions": "Damage?"}},
                    {"department": {"returns": "replacements"}}, ["churn_risk"])
    assert errs == []
    assert list(m.questions) == ["department", "urgency", "damage"]
    assert m.origins == {"department": "extended", "urgency": "template", "damage": "extra"}
    assert list(m.questions["department"]["criteria"])[-1] == "returns"
    assert m.extensions == {"questions": ["damage"], "options": {"department": ["returns"]}, "skipped": ["churn_risk"]}


@pytest.mark.parametrize("extras,add,skip,expected", [
    ({"urgency": {"type": "noul"}}, None, None, ("question_conflict", "questions.urgency")),
    ({"churn_risk": {"type": "noul"}}, None, ["churn_risk"], ("question_conflict", "questions.churn_risk")),
    (None, {"urgency": ["x"]}, None, ("options_not_extensible", "add_options.urgency")),
    (None, {"department": ["billing"]}, None, ("option_conflict", "add_options.department")),
    (None, {"nope": ["x"]}, None, ("unknown_question", "add_options.nope")),
    (None, None, ["urgency"], ("skip_not_allowed", "skip[0]")),
    ({f"q{i}": {"type": "noul"} for i in range(9)}, None, None, ("too_many_extra_questions", "questions")),
])
def test_extension_errors(support, extras, add, skip, expected):
    _, errs = merge(support, extras, add, skip)
    assert expected in errs


# ---------------------------------------------------------------------------------------------------- settings


def test_settings_ladder_and_sources(support):
    qs = {k: support["questions"][k] for k in support["questions"]}
    per, out = T.resolve_settings(qs, "laya", support["settings"], {}, 0.9)
    assert out == {"act_threshold": 0.9, "temperature": 1.3,
                   "questions": {"urgency": {"temperature": 1.8}, "churn_risk": {"act_threshold": 0.7}},
                   "sources": {"act_threshold": "template", "temperature": "template.models.laya",
                               "questions.urgency.temperature": "template.models.laya.questions",
                               "questions.churn_risk.act_threshold": "template.questions"}}
    assert per["urgency"]["temperature"] == 1.8 and per["department"]["temperature"] == 1.3


def test_request_beats_every_template_value(support):
    per, out = T.resolve_settings(support["questions"], "kev-4b", support["settings"], {"act_threshold": 0.8}, 0.9)
    assert per["churn_risk"]["act_threshold"] == 0.8                       # beats the template's per-question 0.7
    assert out["temperature"] == 1.1 and out["sources"]["temperature"] == "template"   # kev-4b has no models entry


def test_worker_settings(support):
    per, out = T.resolve_settings(support["questions"], "laya", support["settings"], {}, 0.9)
    assert T.worker_settings(per, out) == {"temperature": 1.3, "questions": {"urgency": {"temperature": 1.8}}}


# ---------------------------------------------------------------------------------------------------- versions


def test_hash_is_stable_and_order_sensitive(support):
    again = T.check_definition(json.loads(json.dumps(SUPPORT))).definition
    assert T.content_hash(again) == T.content_hash(support)
    swapped = dict(SUPPORT, questions=dict(reversed(list(SUPPORT["questions"].items()))))
    assert T.content_hash(T.check_definition(swapped).definition) != T.content_hash(support)
    assert T.question_set_key(swapped["questions"]) == T.question_set_key(SUPPORT["questions"])


@pytest.mark.parametrize("a,b,cls", [
    ({"type": "noul", "instructions": "x"}, {"type": "noul", "instructions": "x"}, "identical"),
    ({"type": "noul", "instructions": "x"}, {"type": "noul", "instructions": "y"}, "text_changed"),
    ({"type": "choice", "criteria": ["a", "b"]}, {"type": "choice", "criteria": ["a", "b", "c"]}, "options_changed"),
    ({"type": "choice", "criteria": ["a", "b"]}, {"type": "choice", "criteria": ["b", "a"]}, "text_changed"),
    ({"type": "score", "criteria": ["l", "h"]}, {"type": "score", "criteria": ["l", "m", "h"]}, "incomparable"),
    ({"type": "noul"}, {"type": "choice", "criteria": ["a", "b"]}, "incomparable"),
    ({"type": "number", "criteria": [1, 2]}, {"type": "number", "criteria": [1, 2, 3]}, "options_changed"),
    (None, {"type": "noul"}, "added"),
    ({"type": "noul"}, None, "removed"),
])
def test_comparability(a, b, cls):
    assert T.compare_question(a, b)["comparability"] == cls


def test_change_classes(support):
    added = json.loads(json.dumps(support))
    added["questions"]["wants_refund"] = {"type": "noul", "instructions": "Money back?"}
    assert T.classify_change(support, added)["class"] == "extended"
    reworded = json.loads(json.dumps(support))
    reworded["questions"]["churn_risk"]["instructions"] = "Will they leave?"
    assert T.classify_change(support, reworded)["class"] == "wording"
    removed = json.loads(json.dumps(support))
    del removed["questions"]["churn_risk"]
    ch = T.classify_change(support, removed)
    assert ch["class"] == "breaking" and ch["breaking_for_callers"]        # callers could skip it before
    tuned = json.loads(json.dumps(support))
    tuned["settings"]["act_threshold"] = 0.85
    assert T.classify_change(support, tuned)["class"] == "settings_only"
    required = json.loads(json.dumps(support))
    required["variables"]["region"] = {"type": "string", "required": True, "sensitive": False, "trusted": False}
    assert T.classify_change(support, required)["breaking_for_callers"]


def test_schema_export(support):
    s = T.schema(support, "support-triage@1")
    assert s["required"] == ["customer_message"]
    assert s["properties"]["customer_email"]["x-basal-sensitive"] is True
    assert s["properties"]["screenshot"]["x-basal-type"] == "image"


def test_builtins_file_in_sync():
    r = subprocess.run(["node", "scripts/export-builtins.mjs", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for it in json.loads((ROOT / "basal" / "builtin_templates.json").read_text(encoding="utf-8")):
        T.check_definition({"questions": it["questions"]})
