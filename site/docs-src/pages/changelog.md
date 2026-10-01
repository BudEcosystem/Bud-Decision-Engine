---
title: Changelog
description: Every release of Bud Decision Studio, newest first, with what changed and how to update.
lead: Every release, newest first. To update, install the new version over the old one, or run the one-line install command again; your engine, settings, models, templates and history are kept.
---

## Unreleased

Fixed on the `main` branch and included in the next release. Until then, 0.2.1 behaves as described under each item.

- **A sensitive file is never kept.** An image, audio or video variable marked `sensitive` was stored with the decision: its contents, its file name and its content hash. The model now reads it and History keeps only a keyed hash, as for every other sensitive value. A queued background decision no longer holds inline file data either, and temporary copies are removed even when a decision fails or is cancelled.
- **Background decisions no longer report a false save failure.** Each one came back with a `history_write_failed` warning although it was saved, and failed outright when the studio was set to refuse unsaved decisions.
- **A template's default model is no longer shown as unable to run it.** A variable that may hold more text than a model reads is now a note on that model, not a problem that greys it out.
- **A version that narrows a variable is marked breaking.** A new list of allowed values, a lower maximum, a higher minimum or a new pattern can refuse values callers already send; such versions were classed as wording changes.
- **A variable with a default is never required.** Adding a default with a patch left `required: true` behind.
- **Cancelling a background decision stops the model load it started**, unless another request is waiting for that model or you loaded it yourself.
- **Evaluate recommends an act threshold only within 50% to 99%.** It could suggest a lower one that the Playground's slider cannot show. A model that never reaches 50% certainty gets its own message.
- **Save as template in a fresh window** names and saves the model the Playground uses, instead of saving no default model.
- **The template id and alias fields are checked in the browser again.** Their pattern was invalid in current browsers, so the check was skipped; the server always validated.
- **Leaving the Playground straight after opening it** no longer throws an error.
- **A second studio started with `BASAL_NO_DOWNLOADS=1`** answers download requests with the reason instead of an error 500.
- **The environment check** reports a GPU that is out of memory as that, not as "PyTorch failed to import".

## 0.2.1

*30 September 2026.* The decisions endpoint is easy to find, and so is what gets saved.

- **The API page opens on the studio API.** `POST /v1/studio/decisions` comes first, and its quick start runs a starter template. Before, it sat behind a fourth tab and looked missing.
- **Every decision endpoint says "Saved to History"**, in every format (the studio API, TypeSafe, OpenRouter and Vercel), and the page says how to keep nothing: send `"store": false`. Each saved call's id comes back in the `x-basal-decision-id` response header.
- **The interactive reference at `/docs`** documents the studio API's request bodies, with examples you can run from the page, the main History filters and each endpoint's path parameters. **Try it out** works; before, it offered no body to fill in.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.2.1)

## 0.2.0

*30 September 2026.* Templates and history: decisions you make once can be reused, and every decision is kept, so you can see how they went and improve them.

- **History replaces Activity.** Every decision is kept on this computer, from the Playground or from your code through any API format: the situation, the questions, the answers, the model and the timing. Filter by template, model, answer or time; open the decisions that asked a person; label the right answers; rerun one on another model; pin the ones to keep. Decisions are kept for 30 days by default, and the History page sets what is kept and for how long.
- **Templates** are decisions you reuse on any model: the questions, **variables** that fill in the situation (a message, a plan tier, a screenshot), and a default model and settings. Every save is a new **version**. Code can pin `support-triage@3`, follow an alias such as `support-triage@production`, or take the latest. Callers can add questions, add options or skip questions where the template allows it.
- **Compare versions on real traffic**: how the answers are spread, how often each version acts on its own, its accuracy on the decisions you labelled, and how the answer changed for the same input.
- **Test examples**: inputs with their right answers, added from History in one click.
- **In the Playground**, **Templates** opens a template to fill in its variables and run it, **Save as template** keeps a decision you built, and **Edit questions** with **Save version** improves a template. **Keep in history** turns saving off for your own experiments.
- **30 starter templates**, one for each Playground example.
- **The studio API** (`/v1/studio`) beside the TypeSafe-compatible `/v1/systemone`: templates, decisions with history, feedback, test examples and settings.
- **`/v1/systemone` is unchanged.** Responses are byte for byte what they were, and the official TypeSafe SDKs pass every conformance test. Calls are now saved to History too; send `"store": false` or `X-Basal-Store: 0` to keep nothing. Variables marked `sensitive` are used for a decision and never written to disk.
- **Safer by default.** Web pages from other sites open in your browser can no longer send decisions to the studio.
- Your Activity log is imported into History the first time 0.2.0 starts.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.2.0)

## 0.1.5

*30 September 2026.* Updates always show.

- **No stale interface after an update.** The studio tells the app window, and any browser, to check for newer scripts and styles every time; unchanged files cost only a quick check. Before, a window could keep running the previous version's files after an update.
- **The desktop app keeps the same address.** When port 8420 was taken, the app picked a random port on every launch, and the window lost what it saves per address: your draft, the theme and choices already made. It now reuses the port it used last time (8420 when free) and remembers it.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.5)

## 0.1.4

*30 September 2026.* Every model comes with its own examples, each run on its model and checked to give the right answers.

- **Choosing a model shows what it is made for.** CLM 8B opens on a web agent choosing its next action, Lev on a question with 77 banking intents, Kev 4B on a refund policy with an exception, Laya Multilingual on a message in Japanese, Jev-Omni on a receipt photo.
- **In the Playground**, choosing a model while an example is showing swaps in that model's own example; **Undo** brings the previous one back. Anything you have written yourself is never replaced.
- The **Examples** menu opens with "Made for" the selected model, and the placeholders follow the model. A blank decision shows what the model is made for and its examples.
- The **Models** page shows "Made for" and **Try it** links for each model.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.4)

## 0.1.3

*30 September 2026.* Start a new decision from scratch.

- **New**, next to Examples (and **Blank decision** at the top of the Examples menu), clears the situation, questions, attachments and answers, and keeps your model and settings. **Undo** brings everything back.
- A blank Playground shows **three steps** (describe the situation, add a question, press Decide), ticked off as you go.
- Pressing **Decide** before adding a question takes you to **Create new Decision** and highlights it.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.3)

## 0.1.2

*30 September 2026.* Fixes the "Choose models to download" sheet on macOS.

- **The model list is visible in Safari's engine.** On 0.1.1 it collapsed to a thin strip showing half of one row, so you could not tick models. The list and the other scrolling areas now start at their content's height in every browser engine.
- **Tested in real Safari** on macOS 14 and 15 on every interface change, through Apple's `safaridriver`.
- The Learn page no longer assumes a GPU.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.2)

## 0.1.1

*30 September 2026.* Fixes first-run setup on macOS.

- **Setup finishes on macOS.** On 0.1.0 it stopped at "Installing the model libraries" with *error: File not found: /Users/&lt;you&gt;/Library/Application*, because uv split a file path at the space in "Application Support". Setup now names the PyTorch build directly, with no file path involved. The same could have affected Windows user names with a space.
- Setup reads PyTorch's version and the final device check without being confused by warnings PyTorch prints.
- The Details log quotes commands correctly, and **Copy details** no longer repeats the error.
- A real first-run install into a folder named "Application Support" now runs on macOS, Windows and Linux on every installer change.

If setup failed on 0.1.0, install 0.1.1 over it and run setup again; it continues where it stopped.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.1)

## 0.1.0

*30 September 2026.* The first release: run open decision models on your own computer.

- **Eleven models**: Julia 1, Laya, Laya Multilingual, Laya Typed-Decisions, Kev 0.5B, Kev 4B, Lev, GLiNER2.5 Decide, Intern-Decision 4B, CLM 8B and Jev-Omni.
- **Six kinds of question**: pick one, rate on a scale, yes or no, pick any, put in order, estimate a number.
- **Pages**: Playground, Models, Evaluate, Activity, API, Learn and System.
- **The API**: TypeSafe's Jev API at `http://127.0.0.1:8420/v1/systemone`, plus OpenRouter's and Vercel AI Gateway's formats. The official TypeSafe SDKs work unchanged.
- **Installers** for macOS (Apple Silicon), Windows, and Linux x64 and ARM64. The first launch checks the hardware, asks where models should run, and installs the matching engine: PyTorch for CUDA, Apple Metal, Intel XPU, ROCm or the processor.
- **Tested**: 14 of 14 API conformance checks, and 21 of 21 end-to-end checks with every model answering all six question types on an NVIDIA GB10.

Known limits: the macOS and Windows builds are not code-signed, Intel Macs are not supported, and AMD GPUs (ROCm) are experimental.

[Release on GitHub](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/tag/v0.1.0)
