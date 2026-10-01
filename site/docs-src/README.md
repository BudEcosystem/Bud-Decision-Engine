# Writing the documentation

The documentation is Markdown in `pages/`, built into static HTML in `../docs/`:

```bash
uv run --no-project --with markdown --with pygments python site/docs-src/build.py
```

`nav.py` holds the order of sections and pages; every slug there must have a file in `pages/`. Screenshots live in
`../docs/img/` as WebP (the build leaves that folder alone) and are written `/docs/img/...` in pages.

## Front matter

```markdown
---
title: Templates
description: One sentence for search engines and link previews.
lead: One or two plain sentences under the title, saying what the page helps the reader do.
---
```

## How a page is laid out

- Every `##` starts a section. If a section has a `:::console`, the page is laid out in rows: the section's prose on
  the left, its console pinned on the right while the section is in view. A `###` with a console of its own starts a
  row too; a `###` without one stays in the row above.
- A page with no console is one reading column; its screenshots run wider than the text.
- In a row with a console, a table whose first column is `` `code` `` is linked to the console: pointing at a row
  lights the lines that set or return that field (`"store":` in JSON, `store=` in Python, `store:` in JavaScript).
  Name the field as it appears in the code; `settings.temperature` matches `temperature`.

## Blocks

````markdown
:::console POST /v1/studio/decisions
@@ curl
```bash
curl -s http://127.0.0.1:8420/v1/studio/decisions -d '{"template": "support-triage"}'
```
@@ Python
```python
...
```
@@ JavaScript
```js
...
```
@@ Response 200
```json
{"id": "dec_..."}
```
:::
````

The title is either `METHOD /path` (drawn as an endpoint) or plain words (`Terminal`). Sections named `Response` or
`Output` (optionally with a status code) go in the lower pane; every other section is a tab. Tabs named alike switch
together across the page and are remembered (curl, Python, JavaScript). A console may hold a single unlabeled code
block.

```markdown
:::note Optional title          (also :::tip, :::warning, :::danger)
Text.
:::

:::tabs os                      (tabs with the same group switch together: os, lang)
@@ Windows
...
@@ macOS
...
:::

:::figure /docs/img/playground.webp
Caption: what the reader should notice, in a sentence.
:::

:::steps
1. **Do this.** Explanation.
2. **Then this.** Explanation.
:::

:::columns
## Use the app
- [Install the studio](/docs/install) What the page helps with.
:::

::endpoint POST /v1/studio/decisions
```

Blocks (`:::` and code fences) start at the beginning of a line, so they cannot sit inside a list item. In a
procedure, put the code after the `:::steps` block or give each step its own `###` section.

Inline: `:icon-name:` draws a Phosphor icon. Links to other pages are written `/docs/page` or `/docs/api/decisions#id`
and become relative at build time.

## Voice

- Plain words for a capable reader who is new to this product. Define a term the first time it appears.
- Sentence case for headings. Name controls exactly as the app does (**Save version**, **Keep in history**).
- Say what happens, then why. Errors: name the problem and the fix.
- No marketing language, no exclamation marks, no emoji, no "simply" or "just".
- Every example is real: requests and responses are captured from a running studio, not written by hand.
