---
title: Troubleshooting
description: Messages you may see in Bud Decision Studio, what causes each one, and how to fix it. Installing, starting, models, the API and history.
lead: Each problem below starts with the message the studio shows, word for word, so you can search this page for it. Most come with their own fix in the message; this page adds the cause and what to check when the fix is not enough.
---

## Where to look first

- **A model's log.** On the Models page, select the model and choose **View log**. The same file is `logs/worker-<model>.log` in the [data folder](/docs/dev/storage#where-the-data-lives).
- **The server's log.** The desktop app writes it to `logs/studio.log` in its application-data folder. From a source checkout, it is the terminal running `./run.sh`.
- **A download's log.** `logs/download-<model>.log` in the data folder.
- **The environment check.** From a source checkout, `.venv/bin/python -m basal.doctor` checks that PyTorch sees the GPU and that every model library imports. A line reading *out of memory right now* means other programs are using the GPU, not that PyTorch is broken: close them or eject models, and run it again. (Versions before 0.3.0 reported that case as "PyTorch failed to import".)

## Installing

### macOS says the app is from an unidentified developer

The macOS build is not code-signed yet. Right-click the app and choose **Open** once, or install with the one-line command, which avoids the warning.

:::console Terminal
```bash
curl -fsSL https://raw.githubusercontent.com/BudEcosystem/Bud-Decision-Engine/main/get.sh | sh
```
:::

### Windows SmartScreen warns about the installer

The Windows build is not code-signed yet. Choose **More info**, then **Run anyway**, or install from PowerShell, which avoids the warning.

:::console PowerShell
```powershell
irm https://raw.githubusercontent.com/BudEcosystem/Bud-Decision-Engine/main/get.ps1 | iex
```
:::

### Setup stops with an error

Setup installs Python, PyTorch and the model libraries into the app's own folder; it changes nothing outside it. When a step fails, setup says which one. Press **Try again**: setup reuses whatever it already installed and continues where it stopped. **Details** shows the full log, and **Copy details** copies it for a bug report.

| Message | Cause and fix |
|---|---|
| *PyTorch could not be installed. Check the internet connection and try again.* | The PyTorch download (1.5 GB for the processor, up to 4.4 GB for an NVIDIA GPU) failed or timed out. Check the connection and free disk space, then try again |
| *The model libraries could not be installed.* | A library download or build failed. The Details log names the package |
| *The model packages could not be installed.* | One of the model packages installed from GitHub (Kev, Lev) or PyPI (GLiNER2, CLM) failed to download |
| *NVIDIA GB10 could not be used: ...* (your device's name) | PyTorch installed, but the final check could not run on the chosen device. Update the graphics driver, or run setup again and choose the processor |

On 0.1.0 only, macOS setup stopped at "Installing the model libraries" with *error: File not found: /Users/&lt;you&gt;/Library/Application*. Version 0.1.1 fixed it; install the current version over it and run setup again.

### The app window is blank on Linux

WebKitGTK's DMA-BUF renderer draws nothing on many NVIDIA systems. The app turns it off by itself (`WEBKIT_DISABLE_DMABUF_RENDERER=1`) unless that variable is already set in your environment. If you set it yourself, set it to `1`.

### The studio did not start

The desktop app shows this, followed by the last lines of the server's log, when the server exits or does not answer within three minutes. The first start after installing imports PyTorch, which can be slow on a cold disk; open the app again. If it fails again, the log lines name the cause; the most common is a missing library, fixed by running setup again (**System** page, **Run setup again**).

## Starting from source

### Run ./install.sh first.

`run.sh` found no `.venv` in the checkout. Run `./install.sh` (or `install.ps1` on Windows) once.

### address already in use

:::console What the terminal shows
```text
ERROR:    [Errno 98] error while attempting to bind on address ('127.0.0.1', 8420): address already in use
```
:::

Another program is listening on port 8420, most often another studio: the desktop app or a second `./run.sh`. Use the one that is running, stop it, or start this one on another port with `./run.sh --port 8421`. The desktop app avoids this itself by picking a free port from 8421 to 8440.

To run two studios side by side on purpose, give the second its own data folder and turn its downloads off.

:::console A second studio on another port
```bash
BASAL_DATA=~/studio-b BASAL_NO_DOWNLOADS=1 ./run.sh --port 8421
```
:::

## Models

### Downloads are slow

Hugging Face limits anonymous downloads. Run `hf auth login` once in a terminal and restart the studio. Downloads run one at a time, smallest first, so a large model waits behind smaller ones.

| Message | Cause and fix |
|---|---|
| *download of &lt;repository&gt; failed (exit 1); see data/logs/download-&lt;model&gt;.log* | The download process failed: often the connection or the disk. The log has the reason. Press Download again; files already complete are kept |
| *download finished but some files are still missing; try again* | The download ended early. Press Download again |
| *Some model files are missing on disk. Open the Models page and download this model again.* | Files in the Hugging Face cache were removed or never finished. Download the model again |
| *Downloads are switched off for this studio (it was started with BASAL_NO_DOWNLOADS=1 ...)* | This is a second studio started beside the main one, without a download queue. Download the model in the main studio, or restart this one without `BASAL_NO_DOWNLOADS`. (Versions before 0.3.0 answered these requests with an unexplained error 500) |

### A model does not load

Most load failures are memory. The studio runs each model in its own process and reports why it stopped.

| Message | Cause and fix |
|---|---|
| *The GPU ran out of memory. Eject another model to free space, then try again.* | Not enough free GPU memory. Eject other models (sidebar, or **Eject all models** on the System page) or close other programs using the GPU |
| *The model process was killed, most likely because the system ran out of memory. Eject other models and try again.* | The operating system stopped the model to save the computer. Same fix |
| *A Python package this model needs is not installed (name). Run ./install.sh again.* | The model's library is missing. From source, run `./install.sh`; in the app, **System**, then **Run setup again** |
| *The model process stopped unexpectedly (exit code N). Open 'Details' to see its log.* | Anything else. The model's log has the full error |
| *Needs a GPU. This computer runs models on the CPU.* | Jev-Omni runs only on a GPU. Choose another model, or install for the GPU on a computer that has one |
| *Needs about N GB of memory; your device has M GB.* | The Models page's estimate says the model does not fit your device's memory. Choose a smaller model |

### Answers are slow, and the answer says it ran on the processor

*Running on the processor, about ten times slower: the GPU did not have enough free memory when this model loaded. Eject other models (or close other GPU programs) and load it again.*

The model asked for the GPU, but the GPU was full when it loaded, and its library fell back to the processor. The answers are the same, only slower. Free GPU memory, then eject the model and load it again. Until it is reloaded, the note appears with every answer from that model: in the Playground, in the studio API's `notes`, and in the wire formats' `notes` when you send `X-Basal-Extensions: 1`.

### A call says no model is loaded

| Message | Cause and fix |
|---|---|
| *No model is loaded. Load one on the Models page (or POST /api/models/{id}/load), or name a downloaded model in the request's `model` field.* | The request named no model and none is loaded. Name a model in `"model"`, or load one |
| *Laya isn't loaded. Load it first (auto-load is off).* | **Load models on demand** is off on the System page. Load the model first, or turn it back on |
| *Laya isn't downloaded. Download it on the Models page first.* | The named model is not on disk |
| *Unknown model 'nope'. Use one of: julia-1, laya-multilingual, laya, ...* | The `model` value is not a studio id, a Hub repository or a supported alias. The message lists the valid ids |
| *Laya failed to load: ... Fix the cause, then load it again.* | The model failed to load moments ago; the studio does not retry for a minute. The rest of the message is the reason |
| *Laya took longer than 10 minutes to answer. Try a shorter input.* | One request ran past ten minutes, usually on the processor with a long input or many questions |

In the studio API these come back as `409 model_not_loaded`, `409 model_not_downloaded`, `404 model_not_found`, `503 model_load_failed` and `504 model_timeout`; see [Errors](/docs/api/errors).

### Run on the processor, or switch back to the GPU

*This computer cannot run models on 'cuda'. Run setup again to install support for it.* The engine was installed for another device. Setup installs one PyTorch build at a time; run it again and choose the device (**System**, then **Run setup again**, or `./install.sh --device cuda` from source). Everything else is kept.

## Training

### The Train page says training isn't available

*Training needs a GPU. This computer runs models on the processor.* Training is off on computers without a supported GPU; every model still runs there. If the computer has a GPU, the engine was installed for the processor: run setup again and choose the GPU (**System**, then **Run setup again**, or `./install.sh` from source). On Intel Arc and Core Ultra graphics, AMD on Linux, NVIDIA RTX 20 series and Apple M1, choose **Try training on this GPU**, or turn on **Allow experimental training** on the System page.

### The file can't be used

The review screen lists each problem in plain words; one shown in red stops training until the file is fixed.

| Message | Fix |
|---|---|
| *Every example in 'label' has the same answer ('spam'). The model needs examples of at least two different answers.* | Add examples of the other answers |
| *The file has only 12 examples. Add at least 30 (a few hundred works much better).* | Add examples |
| *Some answers to 'team' don't match its options, e.g. ...* | A few answers are misspelled or use another name. Fix them, or leave them: up to a fifth are skipped |
| *The column 'notes' has 412 different answers. A decision model picks from a fixed list; use at most 64 answers.* | That column is free text, not an answer. Remove it, or name the text column `text` |
| *After keeping some examples aside to test the result, too few are left to train on.* | The file has too few answered questions once a third is set aside. Add examples |

### Training stops or waits

| Message | What happens, and what to do |
|---|---|
| *Waiting for another training to finish* | One training runs at a time on a computer. It starts by itself; **Cancel** removes it from the queue |
| *Other programs left only 2.8 GB of memory free. It will continue by itself when there is room.* | Training waits instead of letting the computer swap. Close programs or eject models; it continues by itself 30 seconds after memory is free, and gives up with a resume point after an hour (**Continue** carries on) |
| *Training Laya needs about 6 GB of memory and 3 GB is free right now.* | Not enough memory to start. Eject loaded models or close other programs |
| *Memory was tight; using smaller batches* | A note, not an error: training continues with smaller batches |
| *Some examples are long, so it is training in a slower way that needs much less memory.* | A note: one example didn't fit, so it switched to a method that recomputes instead of storing; slower, same result |
| *Too many of your examples are too long to learn from with this computer's memory.* | More than a tenth of the examples don't fit even then. Split long documents, or choose a smaller model |
| *The GPU stopped responding, so training is starting again.* | The supervisor restarted a training whose GPU work stopped answering. After three tries it stops with *The GPU stopped responding several times*; close programs that use a lot of memory and choose **Try again** |
| *The computer ran out of memory and stopped the training.* | The operating system ended it. Close other programs, then **Try again** |

### The result wasn't kept

The page names the reason; the original model is unchanged. See [When the result isn't kept](/docs/guides/teach#when-the-result-isnt-kept) for what helps, or choose **Teach another model instead**. *The saved model did not answer exactly like the trained one* is a bug: the job folder (`training/jobs/<id>` in the studio's data folder) keeps the rejected files and the log; please report it.

### Importing a trained model fails

| Message | Fix |
|---|---|
| *That file isn't a .zip exported from Bud Decision Studio.* | Choose the .zip that **Export** saved |
| *It was trained from 'kev-4b', which this studio doesn't have. Update the studio, then import it again.* | The model it was trained from isn't in this version's catalog |
| *That .zip doesn't hold a fine-tuned model exported from Bud Decision Studio (unexpected: ...).* | The archive holds other files; export it again rather than editing it |

## Calling the API

| Message | Cause and fix |
|---|---|
| *Management requests need the header 'X-Basal-Client: 1' (it protects the studio from other websites).* | A call to a management endpoint under `/api/` that changes something (load, eject, download, settings) came without the header. Add `-H 'X-Basal-Client: 1'` |
| *Requests from https://example.com may not change this studio. Other websites can't write to it; to allow one, list it in BASAL_CORS_ORIGINS.* | A web page on another site tried to send a decision or change something. To allow your own web app, start the studio with `BASAL_CORS_ORIGINS=https://your.app`. Calls from code, curl and the SDKs are not affected |
| *Send JSON (Content-Type: application/json) from a browser.* | A browser sent a studio API request without a JSON content type. Set the header |
| *Requests must be addressed to localhost, not 'my-machine.local'.* | The studio listens on this computer only and refuses other host names, to block DNS rebinding. Use `127.0.0.1` or `localhost`, or start it with `--host 0.0.0.0` and a key |
| *History, templates and settings are only reachable from this computer unless the studio runs with BASAL_API_KEY set.* | Another machine called a studio API endpoint other than making a decision. Start the studio with `BASAL_API_KEY` and send `Authorization: Bearer <key>` |
| *Must supply an API key! Check your request and try again.* (403) or *Cannot authenticate with the server. Please check your API key and try again.* (401) | The studio runs with `BASAL_API_KEY` and the request had no key or a wrong one. These match TypeSafe's own messages |

Every studio API error has a code, the parameter at fault and often a suggestion, all listed on [Errors](/docs/api/errors).

## History

### History does not save, and the server's log says why

When history cannot open, decisions keep working and nothing is saved, and the history endpoints answer `503 history_unavailable`. The server prints the reason at startup, in its log.

| Message | Cause and fix |
|---|---|
| *This history was written by a newer version of the studio, so it opens read-only. Update the studio to keep saving decisions.* | You opened your data with an older studio after using a newer one. Install the newer version again |
| *Migration 0002_import_activity.py changed after it was applied to .../studio.db. Restore the original file; migrations are never edited once released.* | A migration file differs from the one that created the database. Released versions never change one, so this means a modified checkout, or a database made by a development build. Restore the released files (reinstall, or `git checkout` the migration). For a database from a development build, move `studio.db` aside to start a new one |
| *History needs SQLite 3.37.0 or newer; this Python has 3.31.1. Decisions still work, but nothing is saved.* (your versions) | The Python running the studio has an old SQLite. The app and `install.sh` install a Python 3.12 that has a recent one; use it |

Before applying an upgrade to your database, the studio copies it to `backups/` in the data folder, keeping the last three, so an upgrade can always be undone by copying one back.

### The answer is correct, but it could not be saved to history.

A decision came back with this warning (`history_write_failed`) when writing to the database failed, usually a full disk. The answer is unaffected. `GET /v1/studio/settings` lists recent write errors under `storage.store_errors`. If you would rather get an error than an unsaved answer, set `history.on_store_error` to `fail`; the studio then answers `503 store_failed`.

### Decisions disappear from History

Decisions are kept for **30 days** by default, and history is capped at **20 GB**, oldest first. Change both on the History page or with `PATCH /v1/studio/settings`. Pinned decisions and labelled ones (while **keep labelled** is on) are never removed; see [Data and storage](/docs/dev/storage#retention).

### A background decision failed with `interrupted`

The studio stopped while the decision was queued or running. At the next start it is marked failed with `error.code: "interrupted"` rather than run again, so no request ever runs twice. Send it again, or rerun it from History.

### Search says it is unavailable

*Full-text search needs an SQLite build with FTS5, which this one lacks.* The `q` filter needs SQLite's full-text search, which some builds leave out. Every other filter works.

## Still stuck

[Report a problem](https://github.com/BudEcosystem/Bud-Decision-Engine/issues) on GitHub with the message, what you did, your operating system and device, and the relevant log: the model's log for a model problem, the server's log for anything else, or setup's **Copy details** for an install.
