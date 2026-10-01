---
title: Bud Decision Studio documentation
description: Install Bud Decision Studio, learn the app, and call it from your code. The user manual, guides, API reference and developer documentation.
lead: Bud Decision Studio runs open decision models on your own computer. You describe a situation, ask questions about it, and get calibrated probabilities back, with every decision kept in a history you can review. Start in the app, or call it from your code.
layout: home
---

:::columns
## Use the app
- [Install the studio](/docs/install) Windows, macOS and Linux, and what your computer needs.
- [Ask your first question](/docs/quickstart) Load a model, describe a situation and read the answer.
- [Save it as a template](/docs/manual/templates) Reuse the same questions with new details each time.
- [Review what was decided](/docs/manual/history) Every decision, what the model saw, and what needed a person.
- [Choose a model](/docs/manual/models) The eleven models, what each is good at and the memory it needs.
- [Teach it your decisions](/docs/manual/train) Train a model on your own examples; keep it only if it got better.

## Build with the API
- [Call the studio from your code](/docs/guides/from-code) Send a situation and questions, act on the answers.
- [Decisions](/docs/api/decisions) Create, wait for, rerun, label and redact decisions.
- [Templates](/docs/api/templates) Versions, aliases and safe changes from a script or CI.
- [Move from Jev or a gateway](/docs/guides/migrate) Change the base URL; your SDK code keeps working.
- [Errors](/docs/api/errors) Every error code, what causes it and how to fix it.
:::

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "state": {
    "ticket": "We were billed twice for March. Refund the duplicate today or we cancel.",
    "plan": "enterprise"
  },
  "questions": {
    "department": {
      "type": "choice",
      "instructions": "Which team should handle this ticket?",
      "criteria": {"billing": "invoices, payments, refunds",
                   "technical": "bugs and outages",
                   "sales": "pricing and upgrades"}
    },
    "churn_risk": {
      "type": "noul",
      "instructions": "Does the customer threaten to cancel?"
    }
  }
}'
```
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)

d = studio.post("/decisions", json={
    "state": {
        "ticket": "We were billed twice for March. Refund the duplicate today or we cancel.",
        "plan": "enterprise",
    },
    "questions": {
        "department": {
            "type": "choice",
            "instructions": "Which team should handle this ticket?",
            "criteria": {"billing": "invoices, payments, refunds",
                         "technical": "bugs and outages",
                         "sales": "pricing and upgrades"},
        },
        "churn_risk": {"type": "noul",
                       "instructions": "Does the customer threaten to cancel?"},
    },
}).raise_for_status().json()

print(d["answers"]["department"]["choice"], d["needs_review"])
```
@@ JavaScript
```js
const res = await fetch("http://127.0.0.1:8420/v1/studio/decisions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    state: {
      ticket: "We were billed twice for March. Refund the duplicate today or we cancel.",
      plan: "enterprise",
    },
    questions: {
      department: {
        type: "choice",
        instructions: "Which team should handle this ticket?",
        criteria: { billing: "invoices, payments, refunds",
                    technical: "bugs and outages",
                    sales: "pricing and upgrades" },
      },
      churn_risk: { type: "noul",
                    instructions: "Does the customer threaten to cancel?" },
    },
  }),
});
const d = await res.json();
console.log(d.answers.department.choice, d.needs_review);
```
@@ Response 200
```json
{
  "id": "dec_01M3TDKH7M3GAHDWDK87SV6NYB",
  "object": "decision",
  "status": "completed",
  "model": "laya",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {"billing": 0.9815, "technical": 0.0094, "sales": 0.0091},
      "certainty": 0.9815,
      "act": true
    },
    "churn_risk": {
      "type": "noul",
      "noul": 0.8696,
      "probabilities": {"false": 0.1304, "true": 0.8696},
      "certainty": 0.8696,
      "act": false
    }
  },
  "act": false,
  "needs_review": ["churn_risk"],
  "store": "full"
}
```
:::

## Everything else

:::columns
### Concepts
- [Decision models](/docs/concepts/decision-models)
- [The six question types](/docs/concepts/question-types)
- [Probabilities, certainty and acting](/docs/concepts/acting)
- [Templates and versions](/docs/concepts/templates)
- [History and privacy](/docs/concepts/history)
- [Fine-tuning and its safeguards](/docs/concepts/fine-tuning)

### Guides
- [Build a support-triage template](/docs/guides/support-triage)
- [Improve a template safely](/docs/guides/improve)
- [Teach a model your own decisions](/docs/guides/teach)
- [Let an agent choose its next action](/docs/guides/agents)
- [Decide about images, audio and video](/docs/guides/media)

### Developers
- [Architecture](/docs/dev/architecture)
- [Run from source](/docs/dev/source)
- [Add a model](/docs/dev/adding-a-model)
- [The trainer](/docs/dev/trainer)
- [Data and storage](/docs/dev/storage)
- [Testing](/docs/dev/testing)

### Help
- [Troubleshooting](/docs/troubleshooting)
- [FAQ](/docs/faq)
- [Changelog](/docs/changelog)
- [Report a problem](https://github.com/BudEcosystem/Bud-Decision-Engine/issues)
:::
