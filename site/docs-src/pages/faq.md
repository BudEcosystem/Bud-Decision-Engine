---
title: FAQ
description: Short answers about Bud Decision Studio: privacy, hardware, choosing a model, Jev compatibility, licences, serving other machines and turning history off.
lead: Short answers to the questions people ask most, with links to the full explanation.
---

## Privacy

### Does anything I send leave my computer?

No. Models run on your computer, and requests, answers and history stay in the studio's data folder. The studio sends nothing about your decisions anywhere and has no telemetry.

It does use the internet for three things, all to fetch software: installing the engine (Python, PyTorch and the model libraries), downloading the models you choose from Hugging Face, and reading each model's file list, size and likes from Hugging Face at startup (cached for six hours, and the studio works offline without it). Loading and running a model never touches the network.

### Where is my history kept, and how do I delete it?

In `studio.db` in the [data folder](/docs/dev/storage#where-the-data-lives). Delete single decisions or everything matching a filter on the History page, or with `DELETE /v1/studio/decisions/{id}` and the bulk endpoints. Decisions older than 30 days are removed automatically; you can change the period or keep them forever.

### How do I stop the studio keeping decisions?

At whichever level suits you:

- **One call:** send `"store": false` in the body, or the header `X-Basal-Store: 0`. TypeSafe's own servers ignore the field, so the same code runs against both.
- **One template:** set the template's `storage` to `none`.
- **Everything:** on the History page, choose **Nothing** for what history keeps, or send `{"history": {"store": "none"}}` with `PATCH /v1/studio/settings`.

`"answers_only"` is the middle ground: it keeps the answers, model and timing, but not the situation, variables or media. Variables marked `sensitive` are never written to disk at any level. Details: [History and privacy](/docs/concepts/history).

## Hardware

### Do I need a GPU?

No. Every model except Jev-Omni runs on the processor, and small models such as Laya and Julia 1 answer in about a second there. A GPU makes the 4B to 12B models practical.

| Hardware | Models run on |
|---|---|
| NVIDIA GPU (GeForce, RTX, GB10 and DGX Spark, data-centre cards) | CUDA 12.6, 12.8 or 13.0, chosen from your driver |
| Mac with Apple Silicon (M1 or newer) | The Apple GPU, through Metal |
| Intel Core Ultra or Arc graphics | The Intel GPU (XPU) |
| AMD GPU on Linux | ROCm, experimental |
| Anything else | The processor |

Intel Macs are not supported, because current PyTorch releases do not support them.

### How much memory does a model need?

The memory each model needs once loaded, as the Models page shows it:

| Model | Memory |
|---|---|
| Julia 1, Laya Multilingual, Laya, Laya Typed-Decisions, Kev 0.5B | 0.8 to 1.3 GB |
| GLiNER2.5 Decide | 2 GB |
| Kev 4B, Lev | 9.5 GB |
| Intern-Decision 4B | 10 GB |
| CLM 8B | 17 GB |
| Jev-Omni | 26 GB, on a GPU only |

Several models can be loaded at once. A model too large for your device is marked **Too large here** on the Models page and the loader says why instead of loading it; the System page shows what each loaded model uses.

## Models

### Which model should I start with?

| If you want | Start with |
|---|---|
| Fast English triage, routing and guardrails | Laya |
| Customer messages in other languages | Laya Multilingual, or Julia 1 for routing |
| The best all-round answers, and images | Intern-Decision 4B |
| Careful, well-calibrated answers on long policies | Kev 4B |
| Questions with hundreds of options | Lev |
| An AI agent choosing its next action | CLM 8B |
| The hardest questions, or audio and video | Jev-Omni |

Each model comes with examples made for it: choosing a model in the Playground shows what it is good at. [Decision models](/docs/concepts/decision-models) explains how they differ.

### Are the answers the same as Jev's?

No. These are open models from different makers, trained differently from TypeSafe's Jev; the studio runs them behind Jev's API. The Models page shows each model's published results next to Jev's on the same benchmarks, labelled as the publishers' own figures. Probabilities are comparable across models in shape, but each model's calibration differs; the Evaluate page measures it on your own examples.

### What licence do the models have?

Each model keeps its own licence. The studio's catalog lists all eleven under Apache-2.0; each model's page in the app links to its Hugging Face card, which is the authority. Weights come from each publisher's Hugging Face repository and are not redistributed by the studio.

## Using it from code

### Does my code written for Jev work?

Yes, by changing the base URL to `http://127.0.0.1:8420`. The studio serves TypeSafe's `POST /v1/systemone` and `GET /v1/models` with the same request and response shapes, and the official SDKs, `typesafe-sdk` for Python and `@typesafe-ai/sdk` for JavaScript, pass every conformance check unchanged. OpenRouter's and Vercel AI Gateway's formats are served too. Responses are exactly TypeSafe's shape unless you ask for more with `X-Basal-Extensions: 1`. See [Move from Jev or a gateway](/docs/guides/migrate).

### What is the difference between /v1/systemone and /v1/studio?

`/v1/systemone` is stateless and TypeSafe-compatible: you send the situation and questions every time. `/v1/studio` adds what a local studio can offer: templates you call by name with variables, versions, a decision object that says whether to act, history you can query, feedback and test examples. Both record decisions in history. See the [API overview](/docs/api/index).

### Can it serve other machines?

Yes. Start it with `--host 0.0.0.0` and set `BASAL_API_KEY`; callers then send `Authorization: Bearer <key>`. Without a key, other machines can make decisions but cannot reach history, templates or settings. The studio speaks plain HTTP and has one workspace; to serve beyond a trusted network, put it behind a reverse proxy that adds HTTPS.

### Can a web page call it from the browser?

Only pages you allow. Other websites open in your browser cannot send the studio requests or change anything. List your own web app's origin in `BASAL_CORS_ORIGINS` when starting the studio.

## The app

### How do I update?

Install the new version over the old one: download it from the [latest release](https://github.com/BudEcosystem/Bud-Decision-Engine/releases/latest), or run the one-line install command again, then quit and reopen the app. The engine, settings, models, templates and history are kept. The app does not update itself yet.

### How do I uninstall it?

Remove the app as you would any other. The engine and your data are in the app's application-data folder (`ai.bud.decisionstudio`, listed on [Data and storage](/docs/dev/storage#where-the-data-lives)); delete it to remove them too. Models are in the Hugging Face cache, `~/.cache/huggingface/hub`, which other tools may share; delete them on the Models page before uninstalling, or remove their folders from the cache.

### Something is not working.

[Troubleshooting](/docs/troubleshooting) lists the studio's messages with their causes and fixes. If yours is not there, [report a problem](https://github.com/BudEcosystem/Bud-Decision-Engine/issues).
