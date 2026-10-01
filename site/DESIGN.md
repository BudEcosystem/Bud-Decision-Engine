---
name: Bud Decision Studio website
description: The product page and documentation for Bud Decision Studio, on a dark violet-tinted ink ground.
colors:
  ink: "#0F0C17"
  ink-raised: "#140F1E"
  plate: "#19142A"
  plate-high: "#211A34"
  console: "#0B0911"
  overlay-panel: "#16111F"
  hairline: "rgba(230, 218, 255, 0.10)"
  hairline-faint: "rgba(230, 218, 255, 0.06)"
  paper: "#F4F1FA"
  reading-text: "#DCD6E8"
  mist: "#ADA6C0"
  dim: "#8C85A2"
  violet: "#8C33EF"
  violet-pressed: "#7D24E2"
  orchid: "#B785F4"
  lavender: "#D9BAF9"
  amber: "#FFB547"
  green: "#5BD98A"
  sky: "#9ED0FF"
  coral: "#FF8A80"
  app-grey: "#F5F5F7"
typography:
  display:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(54px, 9.6vw, 140px)"
    fontWeight: 650
    lineHeight: 0.9
    letterSpacing: "-0.058em"
    fontVariation: "\"opsz\" 32"
  headline:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(32px, 4.3vw, 54px)"
    fontWeight: 620
    lineHeight: 1.04
    letterSpacing: "-0.036em"
  docs-title:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "clamp(34px, 3.6vw, 46px)"
    fontWeight: 640
    lineHeight: 1.07
    letterSpacing: "-0.038em"
  docs-section:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "27px"
    fontWeight: 620
    lineHeight: 1.2
    letterSpacing: "-0.028em"
  title:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "19.5px"
    fontWeight: 620
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  lead:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "-0.006em"
    fontFeature: "\"cv11\", \"ss01\", \"ss03\""
  body-docs:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "16.5px"
    fontWeight: 400
    lineHeight: 1.72
    letterSpacing: "-0.004em"
    fontFeature: "\"cv11\", \"ss01\", \"ss03\""
  caption:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 550
    lineHeight: 1.4
  code:
    fontFamily: "Geist Mono, SF Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "13.2px"
    fontWeight: 400
    lineHeight: 1.72
  code-label:
    fontFamily: "Geist Mono, SF Mono, ui-monospace, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.02em"
rounded:
  chip: "6px"
  control-sm: "8px"
  control: "10px"
  button: "12px"
  block: "14px"
  plate: "16px"
  dialog: "18px"
  pill: "999px"
spacing:
  gutter: "24px"
  gutter-phone: "16px"
  docs-edge: "28px"
  row-gap: "52px"
  shell-gap: "56px"
  row-rhythm: "64px"
  section: "152px"
components:
  button-primary:
    backgroundColor: "{colors.violet}"
    textColor: "#FFFFFF"
    rounded: "{rounded.button}"
    padding: "0 24px"
    height: "54px"
    typography: "{typography.label}"
  button-primary-hover:
    backgroundColor: "{colors.violet-pressed}"
  button-primary-small:
    backgroundColor: "{colors.violet}"
    textColor: "#FFFFFF"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "36px"
  button-ghost:
    backgroundColor: "rgba(255, 255, 255, 0.055)"
    textColor: "{colors.paper}"
    rounded: "{rounded.button}"
    padding: "0 24px"
    height: "54px"
  button-ghost-hover:
    backgroundColor: "rgba(255, 255, 255, 0.09)"
  segmented-control:
    backgroundColor: "rgba(255, 255, 255, 0.04)"
    rounded: "{rounded.button}"
    padding: "4px"
  segmented-option:
    backgroundColor: "transparent"
    textColor: "{colors.mist}"
    rounded: "{rounded.control-sm}"
    padding: "8px 16px"
  segmented-option-selected:
    backgroundColor: "rgba(255, 255, 255, 0.10)"
    textColor: "{colors.paper}"
  plate:
    backgroundColor: "{colors.ink-raised}"
    rounded: "{rounded.plate}"
  console:
    backgroundColor: "{colors.console}"
    textColor: "#D8D2E6"
    rounded: "{rounded.plate}"
    typography: "{typography.code}"
  console-tab-selected:
    backgroundColor: "rgba(255, 255, 255, 0.08)"
    textColor: "{colors.paper}"
    rounded: "{rounded.control-sm}"
    padding: "6px 12px"
  method-post:
    backgroundColor: "rgba(140, 51, 239, 0.22)"
    textColor: "#E5CCFF"
    rounded: "{rounded.chip}"
    padding: "0 9px"
    height: "24px"
    typography: "{typography.code-label}"
  method-get:
    backgroundColor: "rgba(158, 208, 255, 0.10)"
    textColor: "{colors.sky}"
    rounded: "{rounded.chip}"
    padding: "0 9px"
    height: "24px"
  method-patch:
    backgroundColor: "rgba(255, 181, 71, 0.10)"
    textColor: "{colors.amber}"
    rounded: "{rounded.chip}"
    padding: "0 9px"
    height: "24px"
  method-delete:
    backgroundColor: "rgba(255, 138, 128, 0.10)"
    textColor: "{colors.coral}"
    rounded: "{rounded.chip}"
    padding: "0 9px"
    height: "24px"
  inline-code:
    backgroundColor: "rgba(183, 133, 244, 0.10)"
    textColor: "#EAD8FF"
    rounded: "{rounded.chip}"
    padding: "2px 6px"
  callout-note:
    backgroundColor: "rgba(140, 51, 239, 0.08)"
    textColor: "{colors.reading-text}"
    rounded: "{rounded.block}"
    padding: "16px 18px"
  callout-tip:
    backgroundColor: "rgba(91, 217, 138, 0.06)"
    rounded: "{rounded.block}"
    padding: "16px 18px"
  callout-warning:
    backgroundColor: "rgba(255, 181, 71, 0.07)"
    rounded: "{rounded.block}"
    padding: "16px 18px"
  callout-danger:
    backgroundColor: "rgba(255, 138, 128, 0.07)"
    rounded: "{rounded.block}"
    padding: "16px 18px"
  link-card:
    backgroundColor: "{colors.ink-raised}"
    textColor: "{colors.reading-text}"
    rounded: "{rounded.block}"
    padding: "18px 18px 20px"
  link-card-hover:
    backgroundColor: "{colors.plate}"
  sidebar-link:
    backgroundColor: "transparent"
    textColor: "{colors.mist}"
    rounded: "{rounded.control-sm}"
    padding: "6px 12px"
  sidebar-link-current:
    backgroundColor: "rgba(140, 51, 239, 0.16)"
    textColor: "{colors.paper}"
  search-field:
    backgroundColor: "rgba(255, 255, 255, 0.05)"
    textColor: "{colors.dim}"
    rounded: "{rounded.control}"
    height: "36px"
    width: "220px"
  search-panel:
    backgroundColor: "{colors.overlay-panel}"
    rounded: "{rounded.dialog}"
    width: "680px"
  search-result-selected:
    backgroundColor: "rgba(140, 51, 239, 0.16)"
    rounded: "{rounded.control}"
    padding: "11px 12px"
  step-marker:
    backgroundColor: "rgba(140, 51, 239, 0.22)"
    textColor: "{colors.paper}"
    rounded: "{rounded.pill}"
    size: "30px"
  field-table:
    backgroundColor: "{colors.ink-raised}"
    rounded: "{rounded.block}"
    typography: "{typography.caption}"
  diagram-part:
    backgroundColor: "{colors.plate}"
    rounded: "{rounded.button}"
    padding: "13px 15px 14px"
  filter-chip:
    backgroundColor: "rgba(255, 255, 255, 0.045)"
    textColor: "{colors.mist}"
    rounded: "{rounded.pill}"
    padding: "7px 14px"
  filter-chip-on:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
---

# Design System: Bud Decision Studio website

This file covers the public website in `site/`: the product page (`index.html`, `site.css`) and the documentation (`docs/`, `docs.css` layered on `site.css`). The desktop app's own light-theme system lives in the root `DESIGN.md`; the website borrows it only where it shows the app (the hero replay and the screenshot frames).

## Overview

**Creative North Star: "The Lit Instrument in a Dark Room"**

The website is a dark room tinted toward Bud violet, and the product sits inside it as a lit object: the hero replay and every screenshot are the app's own light interface on the app's grey, framed and shadowed so they read as a real screen. Everything around them is ink, quiet text in three greys, and plates that are told apart by tone and a one-pixel inset hairline, not by borders or heavy shadow. Violet marks the one thing to press or the thing that is selected; lavender marks what to notice (links, code, emphasis).

The product page is the louder register: a 140px display headline, particles, a scroll-tilted app window, a different layout per section and every chart captioned "Fig. n". The documentation is the same world at reading pace. It is a two-column shell (contents rail, page), and every section with code is a row: prose on the left, that section's console pinned on the right while you read it. Pages with no code collapse to one reading column whose screenshots run wider than the text. In the docs nothing moves on load; the language switch and the parameter trace are the only authored motion.

Density is generous on the product page (152px between sections) and calm-but-dense in the docs (64px between rows, a 576px reading line, tables at 14.5px). Inter carries all prose and interface; Geist Mono appears only where the reader is looking at something a machine reads.

**Key Characteristics:**
- Ink ground (#0F0C17) with raised plates (#140F1E, #19142A) outlined by inset hairlines, never solid borders.
- Bud's three violets as the only accent family: violet to act, orchid to mark, lavender to emphasise.
- The app shown as itself: light screenshots and replay in grey (#F5F5F7) frames on the dark page.
- Code beside prose: sticky consoles with segmented language tabs and a response pane.
- Figures numbered "Fig. n" with a dim tabular numeral before the caption, on both surfaces.
- Phosphor (regular) icons only, inlined as SVG.

## Colors

A near-monochrome violet-black world in which colour is either the brand's violets or a signal with a fixed meaning.

### Primary
- **Bud Violet** (violet): the action and selection colour. Primary buttons, the selected row in the model table, the active step numeral, the "This computer" strip, top bars in charts, the range slider fill. At 16 to 22% alpha it becomes the selected tint (current sidebar page, selected search result, POST chip, trace highlight).
- **Pressed Violet** (violet-pressed): the primary button's hover and pressed state only.

### Secondary
- **Orchid** (orchid): small markers. List bullets (6px rounded squares), the release dot, the active outline rule in the sidebar, the segmented scenario underline, the `$` prompt, chart lines and secondary bars at half alpha.
- **Lavender** (lavender): emphasis on ink. Links in prose, code in headings and notes, callout icons, the version chip, method paths in diagrams, ordered-list numerals, the focus ring.

### Tertiary (signals)
- **Amber** (amber): "ask a human", warnings, PATCH/PUT chips, a model that does not fit, error status in a console.
- **Green** (green): "act automatically", tips, success status (`200 OK`) and the copied state.
- **Sky** (sky): GET chips and JSON keys / tag names in syntax colour. Docs and code only.
- **Coral** (coral): DELETE chips and danger callouts. Docs only.

### Neutral
- **Ink** (ink): the page ground everywhere, and the translucent header (82% in docs, 72% on the product page once scrolled).
- **Raised Ink** (ink-raised): the default plate: figures, tables, link cards, pager, inspector, download card.
- **Plate** (plate): a plate one step up: hovered cards, diagram parts, step numerals, node boxes.
- **High Plate** (plate-high): toast and rank badges.
- **Console Black** (console): consoles, code blocks and the one-line installer; darker than the ground so code reads as a well.
- **Overlay Panel** (overlay-panel): the search dialog. The phone contents drawer uses a sibling tone (#120E1B).
- **Hairline / Faint Hairline** (hairline, hairline-faint): inset outlines of plates, and dividers between rows and sections.
- **Paper** (paper): headings, strong text, selected labels.
- **Reading Text** (reading-text): docs body copy; softer than paper for long reading on ink.
- **Mist** (mist): secondary text, ledes, captions, idle controls.
- **Dim** (dim): tertiary text, Fig. numerals, breadcrumbs, metadata, placeholders.
- **App Grey** (app-grey): the ground of every screenshot frame and the hero replay; it belongs to the app, not the page.

### Named Rules
**The Three Violets Rule.** Violet is pressed or selected, orchid is a marker, lavender is emphasis. Don't swap their jobs: a lavender button or a violet link reads as a mistake.

**The Fixed Signal Rule.** Amber always means "a person decides" or "careful", green always means "acts on its own" or "worked". They carry the app's meaning onto the page and are never used as decoration.

## Typography

**Display Font:** Inter (variable, self-hosted, with -apple-system and system-ui fallback)
**Body Font:** Inter, with stylistic sets `cv11`, `ss01`, `ss03` on the body
**Label/Mono Font:** Geist Mono (self-hosted, with SF Mono and ui-monospace fallback)

**Character:** One grotesque doing everything, tightened hard at large sizes (down to -0.058em) and opened to near-neutral at reading size, with intermediate variable weights (550, 620, 640, 650) instead of the stock 500/600/700. Geist Mono is the machine's voice beside it.

### Hierarchy
- **Display** (650, clamp(54px, 9.6vw, 140px), 0.9): the product page hero headline only; words rise in with a blur on first load.
- **Headline** (620, clamp(32px, 4.3vw, 54px), 1.04): product page section headings; the closing call grows to clamp(40px, 6vw, 84px) at 640.
- **Docs Title** (640, clamp(34px, 3.6vw, 46px), 1.07): one H1 per docs page, under a breadcrumb.
- **Docs Section** (620, 27px, 1.2; 23px on phones): each H2, which opens a row.
- **Title** (620, 19.5px, 1.3): H3 in docs; 19px for FAQ questions, notes and figure titles on the product page; 27px for step and inspector titles.
- **Lead** (400, 19px, 1.6, mist, max 620px): the one or two sentences under a docs title; the product page's section ledes are 18.5px.
- **Body** (400, 17px, 1.55) on the product page; **Body Docs** (400, 16.5px, 1.72, reading-text) in the docs, with paragraphs held to 576px (about 76 characters).
- **Caption** (400, 14.5px, 1.55, mist): figure captions, table text, card descriptions.
- **Label** (550, 13.5px): tabs, segmented options, sidebar outline, console titles, footers of dialogs.
- **Code** (Geist Mono 400, 13.2px, 1.72 in docs blocks; 12.8px in consoles; 13px on the product page).
- **Code Label** (Geist Mono 600, 12px, +0.02em): HTTP method chips and `kbd` keys.

### Named Rules
**The Mono For Machines Rule.** Geist Mono is only for code, commands, paths, field names, HTTP methods and keyboard keys. Headings, labels and numbers in prose stay in Inter with `tabular-nums`.

**The Tabular Figures Rule.** Every number that can change or be compared (Fig. numerals, probabilities, memory, versions, step counters, status codes) sets `font-variant-numeric: tabular-nums`.

## Layout

**Product page.** A centred 1224px container with a 24px gutter (16px under 760px). Sections are 152px apart (104px under 900px) and each uses its own composition: a figure with margin notes (8/4), a pinned walkthrough beside scrolling steps, a three-across small-multiples plate, one frame driven by a segmented control, a dashed-boundary pipeline diagram, a table with a sticky inspector (1fr / 380px), a five-cell hardware strip, endpoints beside a sticky console (5/7), the download card over a four-column file matrix, and a sticky-heading FAQ (4/8). Grids collapse to one column at 1080px and 900px.

**Docs shell.** A fixed 66px header, then a grid of a 264px contents rail and the page, 56px apart, max 1520px with 28px edges (240px rail and 44px gap under 1240px). The rail is sticky, scrolls on its own and fades its top and bottom edges with a mask; under 900px it becomes a 320px off-canvas drawer over a scrim, opened from a button in the header.

**Rows.** On a page with a console, each H2 (and any H3 with its own console) is a two-equal-column row with a 52px gap; rows are 64px apart with 44px padding above a faint hairline. The console column is pinned at 24px below the header. A section without code spans both columns so its tables can use the width. Under 1180px rows stack and the console follows its prose, unpinned. The docs home widens the first row to 1.6fr / 1fr (task columns beside the first console), then lets the next rows run full width.

**Measures.** Blocks hold 700px, paragraphs and list items 576px, screenshots and the pager 1040px.

**Breakpoints** (as shipped): 1240, 1180, 1080, 900, 760 (product page), 640 (docs phones).

### Named Rules
**The Code Beside Prose Rule.** A request or command the reader will send sits in the console beside the section that explains it, never between its paragraphs. Samples the reader types into the app (a JSON State, a template sentence, rows of test data) may stay inline next to their steps and screenshots.

## Elevation & Depth

Depth is tonal first. The ground is ink; plates step up through #140F1E and #19142A; code steps down to #0B0911. Every plate is outlined with an inset one-pixel hairline (`box-shadow: inset 0 0 0 1px`) rather than a border, so outlines never change a box's size. Real cast shadows are reserved for objects that sit above the page: the app replay, screenshot frames, consoles, the download card, the search dialog, the phone drawer and the toast. They are long, soft and dark, falling below the object (large negative spread), never offset to one side.

### Shadow Vocabulary
- **Inset hairline** (`box-shadow: inset 0 0 0 1px rgba(230, 218, 255, .10)`): every plate, table, card, segmented control and console. Hover may brighten it to `rgba(183, 133, 244, .34)`.
- **Console drop** (`box-shadow: inset 0 0 0 1px rgba(230,218,255,.10), 0 36px 80px -40px rgba(0,0,0,.9)`): product page console; docs code blocks use `0 24px 60px -40px`.
- **Screen frame** (`box-shadow: 0 0 0 1px rgba(255,255,255,.12), 0 36px 80px -36px rgba(0,0,0,.95)`): screenshot frames, on the app grey.
- **Replay glow** (`0 0 0 1px rgba(255,255,255,.14), 0 50px 120px -30px rgba(66,20,140,.85), 0 30px 60px -30px rgba(0,0,0,.9)`): the hero app window only, over a violet pool of light.
- **Dialog** (`inset 0 0 0 1px rgba(230,218,255,.14), 0 40px 100px -30px rgba(0,0,0,.95), 0 0 0 1px rgba(0,0,0,.5)`): the search panel, over an 8px-blurred scrim at 66%.
- **Primary lift** (`inset 0 1px 0 rgba(255,255,255,.22), 0 8px 18px -8px rgba(0,0,0,.55)`): primary buttons.

### Named Rules
**The Inset Hairline Rule.** Plates are outlined by an inset 1px hairline, not a CSS border. Solid borders are for dividers between rows, columns and sections, and are always the faint hairline.

**The Light From Above Rule.** Glows are violet radial light falling from the top of the hero and the closing call. They belong to those two moments, not to cards or buttons.

## Shapes

Soft, consistent rounding that grows with the size of the object: 6px for chips, inline code and keys; 8px for tab and sidebar options; 10px for small controls (small buttons, search field, icon links, search results); 12px for buttons, segmented controls and diagram parts; 14px for docs blocks (tables, code blocks, callouts, cards, frames) and the app window; 16px for product-page plates and consoles; 18px for the search dialog; 20px for the download card; full pills for filter chips, badges and the release link. Corners are always concentric: an option inside a segmented control has a smaller radius than its track. Lists use 6px rounded-square orchid bullets; the docs' numbered steps use 30px circles joined by a 1px vertical rule; the product page's step numerals are 36px rounded squares.

## Components

### Buttons
Confident, compact, and few.
- **Shape:** gently rounded (12px; 10px for the 36px small size).
- **Primary:** violet with white 600 text, 54px tall with 24px sides (36px and 14px in headers), an inner top highlight and a soft lift; hover darkens to the pressed violet; active nudges down 1px and scales to .99.
- **Ghost:** a 5.5% white wash with a 12% inset outline and paper text; hover raises the wash to 9%.
- **Icon link:** 36px square, 10px radius, mist icon turning paper on a 6% wash.
- **Copy:** a small 6% wash button at the right of console and code bars; turns green when copied.

### Segmented Controls and Tabs
- **Style:** a 4% white track with the inset hairline (12px radius, 4px padding; 3px and 11px in docs tabs); options are mist at 550, paper on hover; the selected option sits on a 10% white wash.
- **Use:** the hero scenario picker, the feature tour, OS and language tabs in docs. Tabs of the same group switch together across a page and are remembered.

### Chips
- **Filter chips:** pills on a 4.5% wash with a hairline; on is a paper pill with ink text at 600.
- **Method chips:** Geist Mono 600 12px uppercase HTTP verbs in a 6px chip with a tinted fill and matching inset outline: POST violet, GET sky, PATCH/PUT amber, DELETE coral. 21px tall and 52px minimum in the endpoint index.
- **Version chip:** lavender 12.5px tabular figures on a 12% orchid wash beside the docs wordmark.

### Cards / Containers
- **Plate:** raised ink, 16px (product) or 14px (docs) radius, inset hairline, no cast shadow.
- **Link cards:** a 36px icon tile (10px radius, violet wash, lavender Phosphor icon), a 16px 620 title with an arrow that slides 3px on hover, and a mist description; hover lifts the card 1px, steps it to plate and brightens the hairline to orchid.
- **Pager:** two cards (previous, next) at the foot of every page, a dim 13px label over a 16px title.
- **Callouts:** a 22px icon column and body on a tinted plate: note in violet with a lavender icon, tip green, warning amber, danger coral. The title is paper at 620; the tint is 6 to 8% and the outline 20 to 24% of the same colour.

### Inputs / Fields
- **Search field (header):** a 36px, 220px-wide button styled as a field: 5% wash, hairline, dim placeholder text, a search icon and `Ctrl K` keys on the right; collapses to a 36px icon button under 1080px.
- **Keys:** Geist Mono 12px on a 6% wash with the hairline and a 1px dark lower edge, 22px tall.
- **Range slider:** a 6px track filled violet up to the value, a 22px white thumb with a 5px violet halo.
- **Focus:** every focusable element shows a 2px lavender outline offset 3px; inside consoles and table rows the offset turns inward.

### Navigation
- **Header:** 66px, translucent ink with an 18px saturate-blur backdrop and a faint bottom hairline; brand lockup (mark, mist "Bud", paper "Decision Studio"), mist links at 14.5px on 8px-radius hover washes, current page on a 6% wash; GitHub icon and a small primary Download on the right. Under 900px links fold into a menu (product page) or the contents drawer (docs).
- **Contents rail:** section headings in paper 13px 600; page links in mist 14.5px on 8px radius, current page on a 16% violet wash in paper 550. Under the current page, its outline is indented behind a 1px hairline; the section in view turns lavender with an orchid left rule.

### Console (signature)
The product page's request console, reused once per docs section. A darker well (console black, 16px radius, inset hairline) with an optional head (method chip and path in Geist Mono, or a plain title), a bar of language tabs (curl, Python, JavaScript) with Copy at the right, the request in Geist Mono 12.8px capped at min(460px, 52vh), then a split bar (label in paper, status in mono green, amber for errors) and the response pane capped at min(340px, 38vh). Syntax colours: strings #F0B3FF, keywords #C49CFF, keys sky, numbers #FFD68A, functions #8BE0C8, comments #857E99 italic. Switching language brings the new code into focus from a 3px blur and 35% opacity over 180ms on the exponential ease-out; this is the one authored motion in the docs, removed under reduced motion.

### Parameter Trace (signature)
In a row with a console, a fields table whose first column is code is linked to the request: hovering or focusing a row tints it with 9% violet, dims every other console line to 38% and lights the matching lines on a 24% violet band, easing over 180ms. Code references in prose do the same and show a dotted lavender underline.

### Endpoint Index and Diagrams
- **Endpoint index:** in the console column beside a reference page's introduction, a console plate titled "Endpoints on this page" listing method chips and mono paths; rows wash 5% white on hover.
- **Diagrams:** sit on the page, not in a well: a mist caption title, then parts as plates (#19142A, 12px radius) each with a 620 name, a mono dim role, a mist sentence and a two-column mono key/value list under a faint rule; parts are joined by a 1px orchid connector ending in a small arrowhead, with a mono label beside it.

### Field Tables
A raised plate (14px radius, hairline) holding a tabular-figure table: 13px mist headers on a 2% wash, 14.5px cells with faint row dividers, first column in paper with unbroken code. Under 640px a fields table stops being a table: each row becomes a block with name and type on one line (type dim, 13.5px) and the description below at full width.

### Steps
Numbered steps in docs: a 30px circle with a paper tabular numeral on a 22% violet fill and 45% orchid outline, joined to the next by a 1px hairline, the bold first phrase as the step's title.

### Figures
Screenshots in an app-grey frame (12px radius in docs, 14px on the product page) with the screen-frame shadow, up to 1040px wide in docs. Captions are mist 14.5px, held to the reading width, opened by a dim tabular "Fig. n" with 12px after it. The product page's charts follow the same numbering.

### Search Dialog
Opened by the header field or Ctrl K: a centred 680px panel at 12vh over a blurred scrim, 18px radius. A 62px input bar (17px Inter, lavender caret), results as 10px-radius rows (paper title, dim section at the right, two-line mist excerpt, lavender field label, matches marked with a 22% lavender wash), the selected row on 16% violet, and a footer of key hints in dim 12.5px.

## Do's and Don'ts

### Do:
- **Do** outline plates with the inset hairline (`inset 0 0 0 1px rgba(230, 218, 255, .10)`) and step depth by tone: ink, #140F1E, #19142A, and #0B0911 for code.
- **Do** keep violet for the thing to press or the thing selected, and use 16 to 22% violet washes for selection states.
- **Do** put every request and command in a console beside its section, with curl / Python / JavaScript tabs that switch together.
- **Do** show the app as itself: real light-theme captures in app-grey frames, numbered "Fig. n" with a caption saying what to notice.
- **Do** set Geist Mono only for code, paths, field names, HTTP methods and keys, and tabular figures for every changing number.
- **Do** hold docs paragraphs to 576px and let figures and tables run wider.
- **Do** use Phosphor (regular) icons, inlined as SVG, at 16 to 20px in lavender, mist or the signal colour.
- **Do** keep the docs still on load; the language switch (180ms blur-to-sharp) and the trace (180ms) are the motion.

### Don't:
- **Don't** use amber or green for anything but their meanings ("a person decides" / careful, "acts on its own" / worked).
- **Don't** draw plates with solid borders or give resting plates a cast shadow; cast shadows are for the replay, frames, consoles, dialogs and the drawer.
- **Don't** put a request or command block between paragraphs of prose on a page that has consoles.
- **Don't** recolour or redraw the Bud mark; use the lockup as provided.
- **Don't** add hues outside the violets, the four signals and the syntax palette; sky and coral stay inside code, method chips, danger callouts and errors.
- **Don't** set a small label line above a heading; sections open with their heading (the hero's release pill is a link to the release, not a label).
