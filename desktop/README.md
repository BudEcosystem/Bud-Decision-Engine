# Bud Decision Studio desktop app

A Tauri 2 app. It is deliberately small: the setup screens, the studio's own code, and a bundled copy of
[uv](https://docs.astral.sh/uv/). PyTorch and the models are not bundled; setup downloads the PyTorch build that
matches the computer it runs on, and the person chooses which models to download.

```
desktop/
  installer/        setup screens (plain HTML/CSS/JS; open index.html in a browser to preview with simulated hardware:
                    ?hw=gb10 | mac | intel | cpu, ?installed=1 for the launch screen)
  scripts/stage.mjs copies the engine code into src-tauri/engine and fetches uv for the target (runs before every build)
  src-tauri/        the Rust shell: detect hardware, run the installer, start and stop the studio server
```

What happens on a person's computer:

| | |
|---|---|
| First launch | setup screens: hardware check, device choice, install (`installer/engine.py install`, progress streamed to the window), then the studio opens and offers models to download |
| Later launches | a short splash while the studio server starts, then the studio |
| Closing the app | the server is stopped with SIGTERM, which ejects every loaded model first |
| Files | app data folder (`~/.local/share/ai.bud.decisionstudio`, `~/Library/Application Support/ai.bud.decisionstudio`, `%APPDATA%\ai.bud.decisionstudio`): `engine-env/` (Python and PyTorch), `data/`, `logs/studio.log`. Model weights stay in the shared Hugging Face cache |

## Build

```bash
cd desktop
npm install
npx tauri build            # Linux: .deb, .rpm, .AppImage; macOS: .app, .dmg; Windows: .msi, .exe
```

Needs Rust, Node 18+, and on Linux the WebKitGTK 4.1 development packages. `.github/workflows/desktop.yml` builds
all four targets (macOS Apple Silicon, Windows x64, Linux x64, Linux ARM64) on GitHub Actions. Releases for macOS and
Windows should be signed (Apple Developer ID and notarisation, a Windows code-signing certificate); unsigned builds
work but the operating system warns on first open.

Developing against a checkout without reinstalling: `BUD_STUDIO_ENGINE=/path/to/BasalStudio
BUD_STUDIO_PYTHON=/path/to/BasalStudio/.venv/bin/python BUD_STUDIO_DATA=/path/to/BasalStudio/data npx tauri dev`.

## Notes from building it

* **Blank window on NVIDIA Linux.** WebKitGTK's DMA-BUF renderer draws nothing on many NVIDIA systems (the GB10
  included). The app sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` on Linux unless it is already set.
* **AppImage environment.** An AppImage's launcher points `PYTHONHOME`, `LD_LIBRARY_PATH`, GTK and GStreamer paths at
  its own files. The app removes them for every process it starts (the studio's Python, uv, the browser); without
  that, Python cannot find its standard library.
* **AppImage tools download.** `tauri build` fetches `AppRun` and `linuxdeploy` from GitHub and times out on slow
  links (`timeout: global`). Download them once into `~/.cache/tauri/` (`AppRun-<arch>`,
  `linuxdeploy-<arch>.AppImage` from the pinned `linuxdeploy-07333c6` release, and
  `linuxdeploy-plugin-appimage.AppImage`), make them executable, and build again.
* **Unattended setup.** `BUD_STUDIO_DEVICE=cuda|mps|xpu|rocm|cpu|recommended` runs the same setup screens without
  questions, for managed rollouts and tests.
* **If the app is force-quit.** The studio server watches the app's process and shuts down cleanly (ejecting every
  model) within two seconds of the app disappearing.
