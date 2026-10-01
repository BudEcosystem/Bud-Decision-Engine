---
title: Errors
description: The studio API's error envelope and every error code it returns, with the HTTP status, what causes it and how to fix it.
lead: Every error from the studio API has the same shape and a stable code to branch on. Validation errors list every problem in one response, and messages say what to change, often with the name you probably meant.
---

This page covers `/v1/studio`. The TypeSafe and gateway routes keep their own error shapes; see [Errors on these routes](/docs/api/systemone#errors-on-these-routes).

## The error envelope

| Field | Description |
|---|---|
| `type` | The broad kind, from the HTTP status: `invalid_request_error`, `authentication_error`, `permission_error`, `not_found_error`, `conflict_error`, `rate_limit_error`, `api_error` or `model_error`. |
| `code` | A stable name for the exact problem, such as `unknown_variable`. Branch on this. |
| `message` | A sentence for people: what is wrong and how to fix it. Messages may be reworded between releases; codes are not. |
| `param` | The field or parameter at fault, as a path: `variables.account_tier`, `questions.urgency.criteria`, `media[0].file_id`, or `questions["odd key"]` for keys with dots or spaces. `null` when no single field is to blame. |
| `details` | Every problem found in the same pass, each `{code, param, message}`. The top-level fields repeat the first. |
| `request_id` | The request's id, also in the `x-request-id` header. Quote it when you report a problem. |
| `decision_id` | Set when a failed decision was stored in history, so you can open it. |

Some errors carry one more field: `current_version` on `version_conflict`, and `existing_id` on `example_exists`.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "support-triage",
  "variables": {"customer_mesage": "Refund please", "acount_tier": "pro"}
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)
r = studio.post("/decisions", json={
    "template": "support-triage",
    "variables": {"customer_mesage": "Refund please", "acount_tier": "pro"},
})
if r.is_error:
    err = r.json()["error"]
    for d in err["details"]:
        print(f'{d["param"]}: {d["message"]}')
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    template: "support-triage",
    variables: { customer_mesage: "Refund please", acount_tier: "pro" },
  }),
});
if (!res.ok) {
  const { error } = await res.json();
  for (const d of error.details) console.log(`${d.param}: ${d.message}`);
}
```
@@ Response 400
```json
{
  "error": {
    "type": "invalid_request_error",
    "code": "unknown_variable",
    "message": "This template has no variable 'customer_mesage'. Did you mean 'customer_message'?",
    "param": "variables.customer_mesage",
    "details": [
      {
        "code": "unknown_variable",
        "param": "variables.customer_mesage",
        "message": "This template has no variable 'customer_mesage'. Did you mean 'customer_message'?"
      },
      {
        "code": "unknown_variable",
        "param": "variables.acount_tier",
        "message": "This template has no variable 'acount_tier'. Did you mean 'account_tier'?"
      },
      {
        "code": "missing_variable",
        "param": "variables.customer_message",
        "message": "customer_message is required (a string of up to 8,000 characters)."
      }
    ],
    "request_id": "req_0777aa3c4b2c48eaa65850aed9aa314f",
    "decision_id": null
  }
}
```
:::

## Which errors to retry

| Status | Retry? |
|---|---|
| `400`, `404`, `412`, `413`, `415` | No. Change the request first. |
| `401`, `403` | No. Fix the key, the origin or the address. |
| `409` | Depends on the code. `idempotency_in_progress` clears in a moment (`retry-after: 1`); `model_not_loaded` clears when the model is loaded; the others need a change. |
| `503`, `504` | Yes, after a pause. These are model and storage failures; a decision that failed this way was usually stored, with its `decision_id`. |

Send the same `Idempotency-Key` on every retry of one logical call, so a retry after a lost response does not decide twice. See [Retries and idempotency](/docs/api/index#retries-and-idempotency).

## Request errors (400)

The body, a parameter or a field is not valid. Fix the request and send it again.

| Code | Cause | Fix |
|---|---|---|
| `invalid_json` | The body is missing, is not valid JSON, or is not an object. | Send a JSON object. |
| `invalid_field` | A body field has the wrong type or an out-of-range value, such as `act_threshold: 1.5`, an unknown setting, or `"stream": true`. | Read `param` and `message`. |
| `missing_field` | A required field is absent: `questions` without a template, the `file` of an upload, or both `expected` and `rating` in feedback. | Add it. |
| `read_only_field` | A patch tried to change something that cannot change, such as a decision's answers or a template's `version`. | Leave it out. Decisions accept only `metadata` and `pinned`; archive templates with their own endpoint. |
| `invalid_parameter` | A query parameter or header is not valid: `limit` above 100, an unknown `group_by`, a bad time in `created_after`, an `after` cursor that matches nothing, a malformed `If-Match`, an `Idempotency-Key` over 255 characters. | Read `param`. |
| `invalid_reference` | `template` is not a string. | Send `"support-triage"`, `"support-triage@2"` or `"support-triage@production"`. |
| `invalid_metadata` | `metadata` is not an object of strings, has more than 16 keys, or a key or value is too long. | Up to 16 keys of 64 characters, string values of up to 512. |
| `filter_required` | A bulk delete or redact had an empty filter. | Send a filter, or `"all": true` if you mean every decision. |
| `confirmation_required` | A template delete lacked `?confirm=<id>`. | Repeat the template id in `confirm`. |
| `search_unavailable` | `q` was used, but this computer's SQLite has no full-text search. | Filter without `q`. |
| `too_many_items` | An import had more than 5,000 examples. | Split it. |
| `background_requires_store` | `background: true` with `store: false`. | Store at least `answers_only`. |
| `invalid_expected` | A label does not fit its question: an unknown option, a level out of range, a key that is not a question. | Use one of the options the message lists. |

### Variables and the situation

| Code | Cause | Fix |
|---|---|---|
| `missing_variable` | A required variable was not sent. | Send it; the message describes what it takes. |
| `unknown_variable` | A variable the template does not declare. | Check the spelling; the message suggests the closest name. |
| `invalid_variable` | A value does not fit its variable: wrong type, not in `enum`, too long, outside `minimum` and `maximum`. | Send a valid value; [the schema](/docs/api/templates#variables-as-json-schema) lists the rules. |
| `variables_need_template` | `variables` without a template, or for a template that declares none. | Send the situation as `state`. |
| `state_required` | No `state` for a decision without a template, or for a template without variables. | Send the situation. |
| `state_not_allowed` | `state` for a template that builds it from variables. | Send `variables` instead. |
| `empty_state` | The situation is empty. | Describe the situation, or send at least one text variable. |

### Questions and extensions

| Code | Cause | Fix |
|---|---|---|
| `unknown_question` | `skip`, `add_options`, settings or extensions name a question that does not exist. | Check the key. |
| `question_conflict` | An extra question uses the key of a template question. | Choose another key, or use `add_options`. |
| `extension_not_allowed` | The template does not allow extra questions, or new options for this question. | Change the template's `extensions`, or save a new version. |
| `options_not_extensible` | Options were added to a `score` or `noul` question. | New levels change what an answer means; save a new version instead. |
| `option_conflict` | An added option already exists. | Leave it out. |
| `skip_not_allowed` | The template does not let callers skip this question. | List it in the template's `extensions.skip`. |
| `too_many_extra_questions` | More extra questions than `extensions.max_questions`. | Send fewer, or raise the limit in the template. |
| `no_questions` | Skipping left no questions. | Keep at least one. |
| `requires_template` | `add_options` or `skip` without a template. | They apply to template questions only. |

### Models and media

| Code | Cause | Fix |
|---|---|---|
| `model_incompatible` | The model cannot run this decision. `details` say why: `type_not_supported`, `too_many_options`, `too_many_questions` or `modality_not_supported` (media it cannot read). | Choose another model; [compatibility](/docs/api/templates#check-which-models-can-run-a-template) lists the ones that fit. |
| `modality_not_allowed` | Media of a type the template does not accept. | Add the type to the template's `modalities`. |
| `model_rejected_input` | The model's worker refused the input after it was checked. The failed decision is stored, with its `decision_id`. | Read the message; simplify the input. |

### Saving a template

| Code | Cause | Fix |
|---|---|---|
| `undeclared_variable` | The state or a question uses `{{name}}` with no such variable. | Declare it, or fix the spelling. |
| `placeholder_not_allowed` | A placeholder where values must be fixed: option names, number values, or the options of a question type that cannot take them from a variable. | Put the variable in an option's description, or use an `options` variable on a `choice`, `multi` or `rank` question. |
| `media_variable_in_text` | An image, audio or video variable appears in text. | Media is attached to decisions, never written into text; remove the placeholder. |
| `modality_not_declared` | A media variable's type is missing from `modalities`. | Add it. |
| `untrusted_variable_in_question` | Free text from a variable would change a question's wording. | Mark the variable `"trusted": true` if only your code sets it, or give it an `enum`. |
| `invalid_definition` | The definition contradicts itself: a `state` without variables, a sensitive variable in a question, an options variable used as text. | Read `param` and `message`. |

## Access errors (401, 403, 415)

| Status | Code | Cause | Fix |
|---|---|---|---|
| 401 | `missing_api_key` | The studio requires a key and none was sent. | Send `Authorization: Bearer <key>`. |
| 401 | `invalid_api_key` | The key does not match `BASAL_API_KEY`. | Check the key. |
| 403 | `remote_access_requires_key` | A remote caller reached history, templates or settings on a studio started without `BASAL_API_KEY`. | Set a key on the studio, or call from its own computer. |
| 403 | `cross_site_request` | A web page on another site tried to change the studio. | Call from a server, or list the site's origin in `BASAL_CORS_ORIGINS`. |
| 403 | `template_read_only` | A change to a starter template (`builtin/...`). | Clone it and change the copy. |
| 415 | `unsupported_media_type` | A browser sent a write that is not JSON, or an upload is not an image, audio or video file. | Send `Content-Type: application/json`; upload a supported file. |

## Not found (404)

| Code | Cause |
|---|---|
| `decision_not_found` | No stored decision has that id. It may have been deleted, removed by retention, or made with `store: "none"`. |
| `template_not_found` | No template has that id, or it was deleted (the message gives the date). |
| `template_version_not_found` | The version does not exist; the message lists the versions that do. |
| `alias_not_found` | The template has no such alias; the message lists the aliases it has. |
| `example_not_found` | The template has no such test example. |
| `feedback_not_found` | No feedback has that id. |
| `file_not_found` | No file has that id, or its bytes are no longer kept. Uploads no decision uses are removed after a day. |
| `model_not_found` | No model has that name; the message lists the model ids. |
| `not_found` | No endpoint has that path. |

A request with the wrong method for a path answers `405` with the code `method_not_allowed`.

## Conflicts (409 and 412)

| Code | Cause | Fix |
|---|---|---|
| `template_exists` | `POST /templates` with an existing id and a different definition. | Use `PUT` or `PATCH` to save a new version. |
| `template_id_reserved` | The id belonged to a deleted template whose decisions are still in history. | Choose another id, or delete that history. |
| `template_archived` | A new version for an archived template. | Unarchive it first. |
| `example_exists` | The input is already a test example; `existing_id` names it. | Update that example instead. |
| `model_not_loaded` | No model is loaded and none was named, or the named model is not loaded and auto-load is off in the app. | Load a model in the app, or name one in `model`. |
| `model_not_downloaded` | The named model is not downloaded. The failed decision is stored. | Download it on the Models page. |
| `decision_finished` | Cancelling a decision that has finished or already reached the model, or rendering `?format=` for a decision without answers. | Nothing to cancel. |
| `input_unavailable` | The decision's input is not kept (answers only, redacted, sensitive values, or media no longer stored), so it cannot be read back, rerun or made an example. | Send the input again as a new decision. |
| `idempotency_in_progress` | A request with the same `Idempotency-Key` is still running. | Retry after `retry-after` seconds. |
| `idempotency_key_reused` | The `Idempotency-Key` was used with a different request. | Use a new key for each logical call. |
| `version_conflict` (412) | `If-Match` or `base_version` names an older version than the latest; `current_version` gives the latest. | Reload the template, reapply your change and save again. |

## Size (413)

`payload_too_large`: a file over 200 MB, uploaded or sent inline as a data URL.

## Model and storage failures (500, 503, 504)

These happen after a request was accepted. A decision that failed while running is stored with `status: "failed"`, so the response carries its `decision_id`.

| Status | Code | Cause |
|---|---|---|
| 503 | `model_load_failed` | The model could not load, for example because memory ran out. The app's Models page shows why. |
| 503 | `model_ejected` | The model was ejected while the request waited for it. |
| 503 | `model_crashed` | The model's worker process failed while answering. |
| 503 | `history_unavailable` | History could not be opened, so endpoints that read or write it are off. Decisions still work. |
| 503 | `store_failed` | The decision could not be saved and `on_store_error` is set to `fail`. |
| 504 | `model_load_timeout` | The model was still loading after 15 minutes. |
| 504 | `model_timeout` | The model took too long to answer. |
| 500 | `internal_error` | An unexpected failure in a background decision. Its `error.message` names it. |

A background decision that was queued or running when the studio stopped is marked failed with `error.code: "interrupted"` at the next start.

## Warnings

Problems that do not stop a request come back in `warnings`, each `{code, message, param}`, on decisions, previews and template saves. On the wire formats, their codes are in the `x-basal-warning` header instead.

| Code | Meaning |
|---|---|
| `unknown_field_ignored` | The body had a field the endpoint does not know. |
| `store_downgraded` | The template or the studio keeps less than the request asked for. |
| `state_may_be_truncated` | The input is longer than the model reads; the end may be cut off. |
| `template_archived` | The template is archived; it still works. |
| `no_model_loaded` | A preview has no model to check against. |
| `per_question_settings_not_portable` | A preview's `systemone_request` uses one temperature for every question, because `/v1/systemone` takes only one. |
| `history_write_failed` | The answer is correct, but it could not be saved to history. |
| `variable_unreferenced` | A template variable is not used by the state or any question. |
| `default_model_incompatible` | The template's default model cannot run it. |
| `metadata_ignored`, `store_ignored` | On the wire formats: a `metadata` or `store` value that could not be used. |
| `template_mismatch` | On the wire formats: the questions differ from the `X-Basal-Template` version, so the call was stored without it. |
