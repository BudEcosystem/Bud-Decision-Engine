# Bud Decision Studio product page and documentation

A static page (HTML, CSS and JavaScript, no build step and no dependencies) that presents Bud Decision Studio and
offers the right download for the visitor's computer, and the documentation in `docs/` (user manual, guides, API
reference and developer docs), built from Markdown in `docs-src/`.

```bash
cd site && python3 -m http.server 8931   # then open http://127.0.0.1:8931
```

Any static host works (GitHub Pages, Netlify, S3, Cloudflare Pages): publish this folder as it is.

## How the download button chooses a file

`assets/js/site.js` reads the operating system and processor from the browser (Chromium's client hints when
available, otherwise the user agent) and the graphics chip from WebGL. It then:

1. renders at once from the release built into `assets/js/data.js` (`RELEASE_FALLBACK`), so the button never waits;
2. asks the GitHub API for the latest release and re-renders if it is newer. The answer is cached for the browser
   session, so repeat visits don't use GitHub's hourly limit of 60 anonymous requests.

| Visitor | Offered |
|---|---|
| Mac with Apple Silicon | `.dmg` |
| Intel Mac | an explanation (PyTorch doesn't support Intel Macs) and the full list |
| Windows | `x64-setup.exe`, with `.msi` as the alternative |
| Linux | `.deb` (or `.rpm` when the browser says Fedora, openSUSE and similar) plus the AppImage, for x64 or ARM64 |
| Phone or tablet | a note that it is a desktop app, and a button to copy the link |

The one-line installers (`get.sh`, `get.ps1` at the repository root) are offered alongside.

## Content and where it comes from

| What | Source |
|---|---|
| The three hero replays and the API response | real answers from Intern-Decision 4B on an NVIDIA GB10 (`POST /v1/systemone`), captured 2026-09-30 |
| The eleven models | `basal/catalog.py` (sizes, memory, taglines, the maker's headline result) |
| Feature tour screenshots | `docs/img/manual/` (the 0.2.1 captures described below) |
| Demo video | `docs/media/` in the repository; ships as H.264 MP4 plus a VP9 WebM, because some Linux Chromium builds cannot play H.264 |
| Icons | `assets/js/icons.js`: Phosphor (regular), the same family the app vendors in `ui/js/phosphor.js`, plus four brand logos from `@phosphor-icons/core` 2.1.1 |
| Test counts (14 of 14, 21 of 21) | the root README's Tested table |

## Page structure

Below the hero, each section uses a different layout, and every chart is captioned "Fig. n" like the answers in the app: a comparison figure with margin notes, a pinned four-step walkthrough, six small-multiple charts on one plate, one screenshot frame driven by a segmented control, a diagram of the request path, a model table with an inspector (after the app's Models page), a setup screenshot over a hardware strip, endpoints beside a request and response, the download panel over a four-column file table, and the FAQ.

Update `assets/js/data.js` when the catalog or a release changes; `RELEASE_FALLBACK` only matters when GitHub can't
be reached.

## Documentation

`docs/` is generated; edit `docs-src/pages/*.md` and rebuild:

```bash
uv run --no-project --with markdown --with pygments python site/docs-src/build.py
```

`docs-src/README.md` describes the page format (consoles beside the prose, tabs, figures, callouts) and the voice.
The build checks that every page in `docs-src/nav.py` exists and writes `docs/search-index.js` for the search box.

### Where the screenshots come from

Every image in `docs/img/` is a capture of the released 0.2.1 app (an export of the `v0.2.1` tag, not a working
checkout), taken with headless Chromium at 1440x900 and 2x scale and saved as WebP about 2000 px wide.

| Images | Captured from | Changes after capture |
|---|---|---|
| `manual/`, `quickstart/` | a studio with sample data (the support-triage template and a few hundred earlier decisions), Laya on an NVIDIA GB10 | the address shown in the app reads `127.0.0.1:8420`, the default, instead of the capture port (set in the page before capture); small panels and dialogs are placed at their natural size on the app's grey (#F5F5F7) |
| `guides/` | a copy of that sample data, and a fresh studio while building the support-triage template step by step | the status bar's port digits replaced with the `8420` digits from a `manual/` capture (the chip is otherwise identical) |
| `install/` | the desktop setup's built-in browser preview with a simulated GB10 | its version label set to 0.2.1; the "Ready" screen is omitted because its install time is simulated |

Every request and response in the text was sent to a running 0.2.1 studio, with the port written as 8420.
