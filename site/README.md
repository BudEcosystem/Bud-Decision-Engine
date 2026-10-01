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

1. shows at once what this browser last saw, or, on a first visit, a link to GitHub's own "latest release" address;
2. takes the latest release from GitHub and re-renders when the answer arrives.

No version number is written into the site. `assets/js/release.js`, shared with the docs, asks two places at the same
time and always re-checks with GitHub:

| Source | What it is | Why |
|---|---|---|
| `api.github.com/.../releases/latest` | GitHub's live answer, with file sizes | Authoritative. Limited to 60 anonymous requests an hour per network address; an unchanged answer does not count |
| `raw.githubusercontent.com/.../main/site/release.json` | A copy in the repository, written by the release workflow the moment a release is published (`scripts/release_info.py`) | Not limited that way, so the site still shows the latest release when the API refuses |

The API's answer wins when it arrives. If neither answers, the page shows the release this browser remembers; if it
remembers none, every download link goes to `github.com/.../releases/latest`, which GitHub resolves itself. The docs
use the same answer for the version beside their name and for the installer names and sizes on the installation page.

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
| The training results (Fig. 5) and the results table in `docs-src/pages/concepts/fine-tuning.md` | the trainer's own runs on an NVIDIA GB10 on 2026-10-01, listed with their settings in `docs/trainer/RESULTS.md` |
| The Train screenshots (`assets/img/train.webp`, `docs/img/manual/train-*.webp`) | the unreleased source checkout, from a fresh studio that trained Laya on the built-in example file; same capture settings as the others, with the API address shown as 8420 |

## Page structure

The hero mirrors the closing section: the Bud mark in particles (it assembles, bursts and springs back; click it to burst it again), the headline and the tagline, then a replay of the app that walks the pointer through Playground, Save as template, Templates, Train and History. A features bento with crops of real screens follows the chat-versus-decision comparison.

Below the hero, each section uses a different layout, and every chart is captioned "Fig. n" like the answers in the app: a comparison figure with margin notes, a pinned four-step walkthrough, six small-multiple charts on one plate, one screenshot frame driven by a segmented control, a diagram of the request path, a model table with an inspector (after the app's Models page), the training results as before-and-after bars, a setup screenshot over a hardware strip, endpoints beside a request and response, the download panel over a four-column file table, and the FAQ.

Update `assets/js/data.js` when the model catalog changes. A release needs no edit here: `site/release.json` is
written by `.github/workflows/desktop.yml` (and `release-info.yml` for releases edited by hand).

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
