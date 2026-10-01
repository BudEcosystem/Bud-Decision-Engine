---
title: Let an agent choose its next action
description: Use a decision model inside an agent loop to choose the next tool or action from the options available right now, with the act threshold deciding when the agent may proceed alone.
lead: An agent repeatedly asks itself what to do next. A decision model answers that question in milliseconds, only ever with one of the actions you offer, and with a certainty you can turn into a rule about when the agent may act alone and when it should ask a person.
---

## The idea

Each step of an agent loop has the same shape: here is the goal, here is what happened so far, here is what the last action returned; which action now? That is a **Pick one** question whose options are the actions available at this step.

Two things make a decision model a good fit:

- **It cannot invent an action.** The answer is always one of the options you sent, so there is no output to parse and no tool name to validate.
- **It says how sure it is.** The act threshold becomes the agent's autonomy: above it, the agent proceeds; below it, a person decides. Actions that change things can demand more certainty than actions that only read.

The available actions usually change from step to step, so the template takes them as an `options` variable instead of fixing them in the question.

## The template

The template on the right has four variables:

| Variable | Type | Holds |
|---|---|---|
| `goal` | `string` | What the agent is trying to do. |
| `steps` | `json` | What it has done so far, oldest first. |
| `observation` | `string` | What the last action returned. |
| `actions` | `options` | The actions available now, each with a short description. |

`"criteria": "{{actions}}"` makes `next_action` take its options from the `actions` variable on every call. A second question, `goal_reached`, gives the loop an independent check on whether the work is done.

`PUT` creates the template (status 201; the response on the right is shortened) and, run again unchanged, returns 200 with `"change": "unchanged"`, so it is safe in a setup script.

The default model is **Intern-Decision 4B**, the studio's best all-rounder. **CLM 8B** is made for agent actions and ranks long candidate lists well, and **Lev** handles up to 500 options; see [the model table](/docs/concepts/decision-models#the-eleven-models). The act threshold is 0.7: the reading actions in this example are safe to take on a reasonable guess.

:::console PUT /v1/studio/templates/{id}
@@ curl
```bash
curl -s -X PUT http://127.0.0.1:8420/v1/studio/templates/agent-next-step -d '{
  "name": "Agent next step",
  "description": "Choose the next action from the actions the agent has right now.",
  "variables": {
    "goal": {"type": "string", "description": "What the agent is trying to do."},
    "steps": {"type": "json", "description": "What it has done so far, oldest first."},
    "observation": {"type": "string", "description": "What the last action returned."},
    "actions": {"type": "options", "description": "The actions available now: name and what it does."}
  },
  "state": {
    "goal": "{{goal}}",
    "done_so_far": "{{steps}}",
    "last_observation": "{{observation}}"
  },
  "questions": {
    "next_action": {
      "type": "choice",
      "instructions": "Which action should the agent take next?",
      "criteria": "{{actions}}"
    },
    "goal_reached": {
      "type": "noul",
      "instructions": "Has the agent already reached its goal?"
    }
  },
  "model": "intern-decision-4b",
  "settings": {"act_threshold": 0.7}
}'
```
@@ Response 201
```json
{
  "id": "agent-next-step",
  "object": "template",
  "version": 1,
  "change": "created",
  "model": "intern-decision-4b",
  "settings": {"act_threshold": 0.7},
  "warnings": []
}
```
:::

## One step

Each step sends the goal, the steps so far, the last observation and the actions on offer. Here an agent investigating slow checkouts has found that release v2.14 changed a retry policy, and has four actions plus "finish" to choose from.

The model puts 77% on reading the code change, above the 0.7 threshold, so `next_action.act` is `true` and the agent may go ahead. The answer carries `dynamic_options: true` because its options came from the request.

:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{
  "template": "agent-next-step",
  "variables": {
    "goal": "Find out why checkout latency doubled since yesterday",
    "steps": ["Opened the service dashboard: p95 latency 840 ms (was 410 ms)",
              "Checked recent deploys: checkout v2.14 went out 19 hours ago"],
    "observation": "v2.14 changed the payment client retry policy.",
    "actions": {"read_diff": "read the code change in v2.14",
                "query_logs": "search the service logs",
                "rollback": "roll back to the previous release",
                "ask_human": "escalate to the on-call engineer",
                "finish": "write up the root cause and stop"}
  },
  "metadata": {"run": "checkout-latency"}
}'
```
@@ Response 200
```json
{
  "id": "dec_01M3TF7RHCH91EMS5WN8D60812",
  "object": "decision",
  "status": "completed",
  "template": {"id": "agent-next-step", "version": 1, "ref": "agent-next-step",
               "resolved_from": "latest", "attribution": "explicit"},
  "model": "intern-decision-4b",
  "answers": {
    "next_action": {
      "type": "choice",
      "choice": "read_diff",
      "probabilities": {"read_diff": 0.7655, "query_logs": 0.1698, "rollback": 0.0259,
                        "ask_human": 0.0293, "finish": 0.0095},
      "confidence": 0.7069,
      "decision": "read_diff",
      "top_probability": 0.7655,
      "certainty": 0.7655,
      "act": true,
      "origin": "template",
      "dynamic_options": true
    },
    "goal_reached": {
      "type": "noul",
      "noul": 0.4531,
      "probabilities": {"false": 0.5469, "true": 0.4531},
      "decision": "no",
      "top_probability": 0.5469,
      "certainty": 0.5469,
      "act": false,
      "origin": "template"
    }
  },
  "act": false,
  "needs_review": ["goal_reached"],
  "metadata": {"run": "checkout-latency"}
}
```
:::

## The loop

The loop below runs until the agent finishes, escalates, or meets an answer it is not sure enough about. It reads `answers.next_action.act`, not the decision's overall `act`: the agent only needs to be sure of its next action, and `goal_reached` is there as a second opinion, not a gate.

`CAREFUL` raises the bar for actions that change production. A rollback needs 95% certainty, whatever the template's threshold, because undoing it costs more than asking.

The run below is real. The agent read the diff (77% sure), then searched the logs (72%), and then leaned towards finishing, but only at 56%. That is below the threshold, so it stopped and handed over to a person with its probabilities. At that point `goal_reached` was "yes" at 92%, so the person is mostly confirming the agent's conclusion.

:::console Agent loop
@@ Python
```python
import httpx

studio = httpx.Client(base_url="http://127.0.0.1:8420/v1/studio", timeout=120)

ACTIONS = {"read_diff": "read the code change in v2.14",
           "query_logs": "search the service logs",
           "rollback": "roll back to the previous release",
           "ask_human": "escalate to the on-call engineer",
           "finish": "write up the root cause and stop"}
CAREFUL = {"rollback": 0.95}          # actions that change production need more certainty

def run_tool(name: str) -> str:       # your tools; canned results here
    return {"read_diff": "The diff raises payment client retries from 1 to 5, with no backoff between attempts.",
            "query_logs": "Logs show 4 to 5 payment retries per checkout since v2.14; each retry waits for a 90 ms timeout.",
            "rollback": "Rolled back to v2.13. p95 latency is 420 ms."}[name]

goal = "Find out why checkout latency doubled since yesterday"
steps = ["Opened the service dashboard: p95 latency 840 ms (was 410 ms)",
         "Checked recent deploys: checkout v2.14 went out 19 hours ago"]
observation = "v2.14 changed the payment client retry policy."

for _ in range(10):
    d = studio.post("/decisions", json={
        "template": "agent-next-step",
        "variables": {"goal": goal, "steps": steps, "observation": observation, "actions": ACTIONS},
        "metadata": {"run": "checkout-latency"},
    }).raise_for_status().json()
    a = d["answers"]["next_action"]
    print(f'{a["choice"]:<11} certainty {a["certainty"]:.2f}  act {a["act"]}')
    if not a["act"] or a["certainty"] < CAREFUL.get(a["choice"], 0):
        print("ask a person:", d["id"], a["probabilities"])
        break
    if a["choice"] in ("finish", "ask_human"):
        break
    observation = run_tool(a["choice"])
    steps.append(f'{a["choice"]}: {observation}')
```
@@ Output
```text
read_diff   certainty 0.77  act True
query_logs  certainty 0.72  act True
finish      certainty 0.56  act False
ask a person: dec_01M3TF7SBWM3C82TMC2CR0W3WM {'read_diff': 0.0481, 'query_logs': 0.1688, 'rollback': 0.1313, 'ask_human': 0.096, 'finish': 0.5559}
```
:::

## What the model read

The variables become one situation, with field names as labels. Check this text in History (the decision's **Input** tab) or with `include=input.rendered_state`, especially when an agent behaves unexpectedly: the model only knows what is written here.

Keep each step short and factual. The context of the model is limited (8,192 tokens for Intern-Decision 4B), so a long run should summarise older steps rather than send everything.

:::console GET /v1/studio/decisions/{id}
@@ curl
```bash
curl -s 'http://127.0.0.1:8420/v1/studio/decisions/dec_01M3TF7RHCH91EMS5WN8D60812?include=input.rendered_state'
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

## Practical rules

- **Name actions as your code does.** The option name is what comes back, so use your tool names (`read_diff`, not "Read the diff"), and put the explanation in the description.
- **Offer only what is possible now.** If a tool is unavailable at this step, leave it out of `actions`. The model can only choose among what you send.
- **Always offer a way out.** An `ask_human` or `finish` option gives the model somewhere to put its probability when no tool fits.
- **Watch the option limit.** Each model has a maximum number of options per question: 62 for Intern-Decision 4B, 500 for Lev, 1,000 for CLM 8B. More candidates than that are refused with `400 model_incompatible` before the model runs.
- **Gate by risk.** Set the template threshold for the cheapest actions and raise it in code, as `CAREFUL` does, for anything hard to undo.
- **Tag the run.** `metadata.run` groups every step of one run; filter History with `metadata.run=checkout-latency` to replay what the agent saw and chose.

## Review a run in History

Every step is a stored decision. Filter History, or the API, by the run's metadata to see each step in order with its probabilities. Label the steps where the agent chose badly, then promote them to test examples, as [Improve a template safely](/docs/guides/improve) describes, so the next version of the template, or the next model you try, is measured on exactly those situations.

:::console GET /v1/studio/decisions
@@ curl
```bash
curl -s 'http://127.0.0.1:8420/v1/studio/decisions?metadata.run=checkout-latency&order=asc'
```
@@ Output
```text
dec_01M3TF7RHCH91EMS5WN8D60812  next_action read_diff   0.7655  goal_reached no  0.5469
dec_01M3TF7RXSF0D5A02AAMYTWG02  next_action query_logs  0.7192  goal_reached yes 0.5
dec_01M3TF7SBWM3C82TMC2CR0W3WM  next_action finish      0.5559  goal_reached yes 0.9203
```
:::
