---
title: Decision models
description: What a decision model is, how it differs from a chat model, what it reads, and the eleven models Bud Decision Studio runs.
lead: A decision model reads a situation and the questions you ask about it, and returns a probability for every answer you allowed. It never writes free text, so its output is always one of your options, with a number that says how sure it is.
---

## What a decision model does

You give the model two things:

- **A situation**, called the **state**: an email, a support ticket, a log line, a JSON record, or the steps an agent has taken so far.
- **Typed questions** about it. Each question says what kind of answer it wants (pick one option, yes or no, a point on a scale) and lists the answers you allow.

The model reads both in one pass and returns, for every question, a probability for each allowed answer. The probabilities of one question add up to 1. The studio then adds a few fields of its own: which answer won, how certain that answer is, and whether it is certain enough to act on without a person.

This family of models is often called **System One** models, after the fast, intuitive kind of judgement in Daniel Kahneman's work, and after TypeSafe's Jev, the model that defined the API most of them speak. The example on the right is TypeSafe's own request shape, sent to the studio.

:::console POST /v1/systemone
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/systemone -d '{
  "model": "laya",
  "state": "The package arrived crushed and the screen is cracked.",
  "questions": {
    "damaged": {"type": "noul", "instructions": "Was the item damaged?"},
    "team": {"type": "choice", "instructions": "Who should handle this?",
             "criteria": {"returns": "refunds and replacements",
                          "shipping": "carriers and delivery",
                          "sales": null}}
  }
}'
```
@@ Response 200
```json
{
  "model": "laya",
  "answers": {
    "damaged": {"type": "noul", "noul": 0.8961},
    "team": {
      "type": "choice",
      "choice": "shipping",
      "probabilities": {"returns": 0.3393, "shipping": 0.553, "sales": 0.1077},
      "confidence": 0.3294
    }
  },
  "usage": {"input_tokens": 78, "output_tokens": 0}
}
```
:::

## How it differs from a chat model

A chat model (an LLM you prompt for text) can answer the same questions, but you get prose back and have to parse it. A decision model is built for one job, and that changes what you can rely on.

| | Chat model | Decision model |
|---|---|---|
| Output | Free text you parse | A probability for every allowed answer |
| Answers outside your options | Possible | Impossible: it can only score the options you gave |
| How sure it is | Not reported, or reported in words | A number per answer, comparable across calls |
| Speed | Grows with the length of the reply | One forward pass; most answers take tens of milliseconds on a GPU |
| Several questions | Usually one call each, or one long prompt | All questions in one pass |
| Typical size | Billions of parameters | 144 million to 12 billion |

Because the output is a probability, you can set a rule such as "act automatically when the model is at least 90% sure, otherwise ask a person". That rule is the **act threshold**, explained in [Probabilities, certainty and acting](/docs/concepts/acting).

Decision models do not write replies, summaries or extracted text. Use them to route, label, rate, check and choose; use a generative model for the words.

## The situation

The state can be plain text, a JSON object or a JSON array. The studio turns JSON into readable text before the model sees it: field names become labels, nested fields are indented, and list items get a dash. Naming fields well (`last_observation`, not `obs`) therefore helps the model.

The example on the right is an agent's state from the [agent guide](/docs/guides/agents), and the text the model actually read. Each model can read a limited amount of text, its **context**: from 512 tokens for Laya to 8,192 for the larger models (a token is roughly four characters of English). Longer states are cut, and the studio warns you with `state_may_be_truncated`.

Images, audio and video go beside the state, not inside it. Two models read them; see [Decide about images, audio and video](/docs/guides/media).

:::console What the model reads
@@ State
```json
{
  "goal": "Find out why checkout latency doubled since yesterday",
  "done_so_far": [
    "Opened the service dashboard: p95 latency 840 ms (was 410 ms)",
    "Checked recent deploys: checkout v2.14 went out 19 hours ago"
  ],
  "last_observation": "v2.14 changed the payment client retry policy."
}
```
@@ Output
```text
goal: Find out why checkout latency doubled since yesterday
done_so_far:
  - Opened the service dashboard: p95 latency 840 ms (was 410 ms)
  - Checked recent deploys: checkout v2.14 went out 19 hours ago
last_observation: v2.14 changed the payment client retry policy.
```
:::

## The eleven models

Every model below speaks the same request format and returns answers in the same shape, so you can switch models without changing code. They differ in size, speed, what they read and how many options they handle.

| Model | Id | Size | Memory | Reads | Options per question | Context | Good at |
|---|---|---|---|---|---|---|---|
| Julia 1 | `julia-1` | 144M | 0.8 GB | text | 20 | 8,192 | Fast multilingual routing |
| Laya Multilingual | `laya-multilingual` | 322M | 1.0 GB | text | 20 | 1,024 | Decisions in 100+ languages |
| Laya | `laya` | 421M | 1.2 GB | text | 20 | 512 | English triage and guardrails |
| Laya Typed-Decisions | `laya-typed-decisions` | 421M | 1.2 GB | text | 20 | 1,024 | Invoices, security and support workflows |
| Kev 0.5B | `kev-0.5b` | 0.5B | 1.3 GB | text | 255 | 8,192 | Learning how the architecture works |
| GLiNER2.5 Decide | `gliner2.5-decide` | 340M | 2.0 GB | text | 64 | 2,048 | Operational labels; fast on a processor |
| Intern-Decision 4B | `intern-decision-4b` | 4B | 10 GB | text, images | 62 | 8,192 | The best all-rounder |
| Kev 4B | `kev-4b` | 4B | 9.5 GB | text | 255 | 8,192 | Careful and well calibrated; long policies |
| Lev | `lev` | 4B | 9.5 GB | text | 500 | 8,192 | Hundreds of options per question |
| CLM 8B | `clm-v0.1-8b` | 8B | 17 GB | text | 1,000 | 2,048 | Agent actions; ranking many candidates |
| Jev-Omni | `jev-omni` | 12B | 26 GB | text, images, audio, video | 256 | 8,192 | The hardest questions; needs an NVIDIA GPU |

All eleven answer up to 64 questions in one pass, except Intern-Decision 4B, which answers up to 16. Jev-Omni is the only one that may need more than one pass for a long list of questions. The Models page in the app shows each model's published results, its license and the examples it was checked on; see [Models](/docs/manual/models).

## Limits are checked before the model runs

The studio knows each model's limits and checks your request against the model that will answer, before loading or running anything. A request the model cannot handle gets one `400 model_incompatible` error that lists every problem: an unsupported question type, too many options, too many questions, or media the model cannot read.

The example on the right sends an image to Laya, which reads text only.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "model": "laya",
  "state": "Proof of purchase.",
  "questions": {"shows_item": {"type": "noul", "instructions": "Does the receipt list olive oil?"}},
  "media": [{"type": "image", "file_id": "file_01M3TF8QS1XJT4SYYEDJRPDZ7A"}]
}'
```
@@ Response 400
```json
{
  "error": {
    "type": "invalid_request_error",
    "code": "model_incompatible",
    "message": "Laya cannot run this decision: Laya cannot read image input.",
    "param": "media",
    "details": [
      {"code": "modality_not_supported", "param": "media", "message": "Laya cannot read image input."}
    ],
    "request_id": "req_4db8548f8cc24c208ece026bc9c9ff3c",
    "decision_id": null
  }
}
```
:::

## How the studio runs a model

Each loaded model runs in its own process, on the GPU or on the processor. Ejecting a model ends its process, which is the only way to give every byte of memory back, and a crash in one model cannot take down the studio or the others.

- **Loading.** A model must be downloaded and loaded before it answers. A request for a model that is downloaded but not loaded loads it first, which can take from a few seconds to several minutes; the request waits up to 15 minutes. See [Call the studio from your code](/docs/guides/from-code#slow-starts-background-and-waiting) for clients with short timeouts.
- **Choosing the model.** A request's `model` wins; then a template's default model; then the most recently loaded model. TypeSafe's model names (`jev-latest`, `jev-1.13`, `typesafe/jev-latest` and their variants), `default`, `auto` and an empty string all mean the most recently loaded model, so code written for Jev runs unchanged. An unknown model name is an error, never a silent substitute, so History always says truthfully which model answered.
- **One answer format.** Each model family has an adapter that returns raw probabilities in the order of your options. The studio turns those into answers itself, so `confidence`, `certainty` and `act` mean the same thing for every model.

:::note The GPU and the processor
Small models answer in about a second on a processor. When a model was meant for the GPU but the GPU did not have enough free memory, it runs on the processor instead, about ten times slower, and every answer carries a note saying so. Eject other models, or close other programs that use the GPU, and load it again.
:::

## When to use one

Decision models fit work where the answer is one of a known set and you want a number you can set rules on:

- routing tickets, emails and leads to a team;
- rating urgency, risk, sentiment or quality on a scale;
- checking a rule: is this a refund request, does this break the policy, is this a prompt injection;
- choosing an agent's next action or tool from a list;
- labelling records with every tag that applies.

They are a poor fit when the answer is open-ended text, a number you cannot list possible values for in advance, or a fact the situation does not contain.
