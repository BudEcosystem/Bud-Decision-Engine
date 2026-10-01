---
title: Desktop app and releases
description: How the Bud Decision Studio desktop app is built with Tauri, how it bundles and starts the engine, how to build installers, and how a release is made.
lead: The desktop app is a thin Tauri shell around the same server you can run from source. It ships the studio's code, the setup screens and a copy of uv; PyTorch and the models are downloaded on the person's computer, matched to its hardware.
---

## What the app does

The app is deliberately small. It carries three things: the setup screens (`desktop/installer/`, plain HTML, CSS and JavaScript), the studio's code, and [uv](https://docs.astral.sh/uv/) for the target platform. The Rust shell (`desktop/src-tauri/src/main.rs`) detects the hardware, runs the installer, and starts and stops the studio server.

| When | What happens |
|---|---|
| First launch | The setup screens check the hardware, ask where models should run, and install the engine with `installer/engine.py install`, streaming its progress into the window. Then the studio opens and offers models to download |
| Later launches | A short splash while the server starts, then the studio in the window |
| Closing the app | The server is asked to stop (SIGTERM, or `taskkill` on Windows), which ejects every model first. After eight seconds it is ended |
| A force quit or crash | The server watches the app's process (`BASAL_PARENT_PID`) and shuts down cleanly within two seconds of it disappearing |

The server listens on `127.0.0.1` on the port it used last time if that is free, else `8420`, else the first free port from `8421` to `8440`. The port is remembered in `studio-port`, because the window's saved state (your draft, the theme, choices already made) belongs to the page's address. The first start can take a while on a cold disk, because the server imports PyTorch; the shell waits up to three minutes and shows the server's log if it does not come up.

On Linux, the AppImage adds itself to the applications menu, as the `.deb` and `.rpm` packages do. On macOS, the app offers to move itself into Applications when it runs from elsewhere.

## How the engine is bundled

`desktop/scripts/stage.mjs` runs before every `tauri dev` and `tauri build`. It copies:

- `basal/`, `ui/`, `installer/engine.py` and the `requirements*.txt` files into `src-tauri/engine/`, which the app bundles as a resource;
- the brand mark, the Inter font, the icons and the interface's colour tokens into the setup screens, so setup looks like the studio;
- uv for the target platform into `src-tauri/binaries/bud-uv-<target>`, from your `PATH` when it matches, otherwise downloaded from uv's latest release.

On the person's computer the code stays inside the app, and only the libraries are installed into the app-data folder (`engine-env/`). The server runs with the bundled code as its working directory. So installing a new version of the app updates the studio's code at once, while PyTorch, the models, settings and history are kept.

The app removes `PYTHONHOME`, `PYTHONPATH` and an AppImage's own library paths from every process it starts, so the engine's Python finds its own standard library. On Linux it also sets `WEBKIT_DISABLE_DMABUF_RENDERER=1`, because WebKitGTK's DMA-BUF renderer draws a blank window on many NVIDIA systems, the GB10 included.

:::console The desktop folder
```text
desktop/
  installer/          setup screens (HTML, CSS, JS)
  scripts/stage.mjs   copies the engine and uv before a build
  src-tauri/
    src/main.rs       detect, install, start and stop the studio
    tauri.conf.json   bundle settings and the version
    engine/           staged: basal/, ui/, installer/, requirements
    binaries/         staged: bud-uv-<target>
```
:::

## Work on the app

To work on the app against a checkout, point it at the checkout's code, Python and data with three variables, so nothing is reinstalled. The setup screens can be previewed in any browser by opening `desktop/installer/index.html`: `?hw=gb10`, `?hw=mac`, `?hw=intel` or `?hw=cpu` simulates a computer, and `?installed=1` shows the launch screen.

| Variable | What it does |
|---|---|
| `BUD_STUDIO_ENGINE` | Run the studio's code from this folder instead of the bundled copy |
| `BUD_STUDIO_PYTHON` | Use this Python instead of the one in `engine-env/` |
| `BUD_STUDIO_DATA` | Keep the studio's data here instead of the app-data folder |
| `BUD_STUDIO_DEVICE` | Run setup without questions: `cuda`, `mps`, `xpu`, `rocm`, `cpu` or `recommended`. For managed rollouts and tests |

:::console Terminal
```bash
cd desktop
npm install

BUD_STUDIO_ENGINE=$PWD/.. \
BUD_STUDIO_PYTHON=$PWD/../.venv/bin/python \
BUD_STUDIO_DATA=$PWD/../data \
npx tauri dev
```
:::

## Build the installers

`npx tauri build` builds for the computer you run it on. It needs Rust, Node 18 or newer, and on Linux the WebKitGTK 4.1 development packages.

| Platform | Built on | Files |
|---|---|---|
| macOS, Apple Silicon | macOS | `.app`, `.dmg` |
| Windows x64 | Windows | `.msi`, `-setup.exe` |
| Linux x64 and ARM64 | Linux | `.deb`, `.rpm`, `.AppImage` |

On Linux, `tauri build` downloads AppImage tools from GitHub and can time out on a slow connection. Download them once into `~/.cache/tauri/` (`AppRun-<arch>`, `linuxdeploy-<arch>.AppImage` and `linuxdeploy-plugin-appimage.AppImage`), make them executable, and build again.

macOS and Windows builds are not code-signed yet. They work, but the system warns the first time they are opened; the one-line installers avoid the warning.

:::console Terminal
@@ Linux
```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev \
  libayatana-appindicator3-dev librsvg2-dev patchelf file
cd desktop
npm install
npx tauri build
ls src-tauri/target/release/bundle/
```
@@ macOS and Windows
```bash
cd desktop
npm install
npx tauri build
```
:::

## Make a release

A release is a version bump, a tag, and GitHub Actions. The version appears in five files, plus the two lock files that follow them:

| File | Field |
|---|---|
| `basal/__init__.py` | `__version__`, shown by the server and recorded with every decision |
| `desktop/src-tauri/tauri.conf.json` | `version`, the app's version |
| `desktop/src-tauri/Cargo.toml` | `version` |
| `desktop/package.json` | `version` |
| `site/docs-src/nav.py` | `VERSION`, shown in these docs |
| `desktop/src-tauri/Cargo.lock`, `desktop/package-lock.json` | Updated with the two above |

:::steps
1. **Test.** Run the studio tests, conformance and the end-to-end check ([Testing](/docs/dev/testing)), and update `docs/testing.md`.
2. **Bump the version** in the files above and commit.
3. **Tag and push.** Pushing a `v*` tag starts `desktop.yml`, which builds macOS (Apple Silicon), Windows x64, Linux x64 and Linux ARM64 (NVIDIA GB10, DGX Spark) in parallel and attaches every installer to one draft release.
4. **Publish.** When all four builds succeed, the workflow publishes the draft and marks it the latest release. The one-line installers, `get.sh` and `get.ps1`, always install the latest release.
5. **Write the notes.** Replace the generated text with notes for people: what changed, why it matters, and how to update.
:::

:::console Terminal
```bash
git commit -am "Bud Decision Studio 0.2.1"
git tag -a v0.2.1 -m "Bud Decision Studio 0.2.1"
git push origin main v0.2.1

# after the four builds finish
gh release view v0.2.1 --repo BudEcosystem/Bud-Decision-Engine
gh release edit v0.2.1 --repo BudEcosystem/Bud-Decision-Engine \
  --title "Bud Decision Studio 0.2.1" --notes-file notes.md
```
:::

The app does not update itself yet. People update by installing the new version over the old one, or by running the one-line command again; the engine, settings, models, templates and history are kept.
