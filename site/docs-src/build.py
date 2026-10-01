"""Builds the documentation (site/docs/) from the Markdown pages in site/docs-src/pages/.

    uv run --no-project --with markdown --with pygments python site/docs-src/build.py

The output is plain static HTML, like the product page: no build step is needed to host it. Each page gets the site's
header and footer, the section sidebar, an "On this page" outline, highlighted code with copy buttons, and an entry in
the search index (docs/search-index.js).
Screenshots live in docs/img/ itself (WebP), so the published folder holds the only copy.

Markdown extensions (see docs-src/README.md for examples):
    :::note|tip|warning|danger [Title] ... :::       a callout
    :::tabs [group] ... @@ Label ... :::              tabs; tabs with the same group switch together (lang, os)
    :::figure path/to/image.webp ... :::              a screenshot in a window frame, numbered "Fig. n", with a caption
    :::cards ... :::                                  a grid of links: "- icon-name [Title](/docs/page) Description"
    :::steps ... :::                                  an ordered list drawn as numbered steps
    :::console [METHOD /path | Title] ... :::         code shown beside the prose: "@@ Label" sections hold code fences
                                                      (curl, Python, JavaScript...), "@@ Response 200" the response
    :::paths ... :::                                  the docs home's two task lists (each "## Title" then "- [Job](/docs/x) Text")
    ::endpoint METHOD /path                           an endpoint heading line
    :icon-name:                                       an inline Phosphor icon
    links and images starting with /docs/ or /assets/ become relative, so the site works from any folder

The pages are written for one version (nav.py's VERSION), but they do not go stale between builds: in the browser,
docs.js asks GitHub for the latest release (assets/js/release.js) and updates the version beside the name and the
installer file names and sizes in the text.

Every H2 and H3 starts a row. A page with a console lays each row out as prose on the left and that row's console on
the right, pinned while the row is in view; parameter tables in a row are linked to its console, so hovering a
parameter lights its line in the request. Pages without a console are one reading column, and screenshots run wider.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
ROOT = SITE.parent
PAGES = HERE / "pages"
OUT = SITE / "docs"
sys.path.insert(0, str(HERE))
from nav import NAV, SITE_NAME, VERSION  # noqa: E402


def _icons() -> dict:
    out = {}
    for f, var in ((ROOT / "ui/js/phosphor.js", "ICONS"), (SITE / "assets/js/icons.js", "BUD_ICONS"), (HERE / "icons.js", "DOCS_ICONS")):
        if f.exists():
            line = next(ln for ln in f.read_text(encoding="utf-8").splitlines() if f"{var} = {{" in ln)
            out.update(json.loads(line[line.index("{"):line.rindex("}") + 1]))
    return out


ICONS = _icons()


def icon(name: str, size: int = 16, cls: str = "") -> str:
    body = ICONS.get(name)
    if body is None:
        raise SystemExit(f"unknown icon {name!r}")
    return (f'<svg class="ph {cls}" viewBox="0 0 256 256" width="{size}" height="{size}" fill="currentColor" '
            f'aria-hidden="true">{body}</svg>')


# ----------------------------------------------------------------------------------------------------------- pages

class Page:
    def __init__(self, slug: str, title: str, section: str):
        self.slug, self.nav_title, self.section = slug, title, section
        self.src = PAGES / f"{slug}.md"
        self.out = OUT / f"{slug}.html"
        self.meta: dict = {}
        self.body = ""
        self.toc: list = []
        self.text: list = []
        self.console = False


def all_pages() -> list[Page]:
    return [Page(slug, title, sec) for sec, items in NAV for slug, title in items]


def front_matter(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---\n"):
        head, text = text[4:].split("\n---\n", 1)
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, text


def rel(page: Page, target: str) -> str:
    """A site-absolute link (/docs/x#y, /assets/x, /) as a path relative to this page."""
    depth = page.slug.count("/") + 1          # docs/ plus any subfolders
    up = "../" * depth
    anchor = ""
    if "#" in target:
        target, anchor = target.split("#", 1)
        anchor = "#" + anchor
    if target.startswith("/docs/") or target == "/docs":
        t = target[len("/docs/"):].strip("/") or "index"
        if "." not in t.rsplit("/", 1)[-1]:      # a page, written without .html
            t += ".html"
        return up + "docs/" + t + anchor
    if target.startswith("/"):
        return up + target.lstrip("/") + anchor
    return target + anchor


# ----------------------------------------------------------------------------------------------------------- code

LANG_NAMES = {"bash": "Shell", "sh": "Shell", "console": "Shell", "python": "Python", "py": "Python", "javascript": "JavaScript",
              "js": "JavaScript", "json": "JSON", "http": "HTTP", "sql": "SQL", "text": "Text", "powershell": "PowerShell",
              "toml": "TOML", "yaml": "YAML", "html": "HTML", "rust": "Rust", "ts": "TypeScript", "typescript": "TypeScript"}


def highlighted(lang: str, code: str) -> str:
    try:
        lexer = get_lexer_by_name({"console": "bash", "sh": "bash", "js": "javascript"}.get(lang, lang))
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    body = highlight(code, lexer, HtmlFormatter(nowrap=True))
    # Pygments closes its spans at each line end, so every line can be wrapped on its own. Each line carries its
    # indent (--i, in characters) so a long line that wraps continues under its own text, not at the margin.
    src = code.rstrip("\n").split("\n")
    out = []
    for n, ln in enumerate(body.rstrip("\n").split("\n")):
        raw = src[n] if n < len(src) else ""
        ind = len(raw) - len(raw.lstrip(" "))
        out.append(f'<span class="ln" style="--i:{ind}">{ln}</span>' if ind else f'<span class="ln">{ln}</span>')
    return "".join(out)


def code_block(lang: str, code: str, title: str = "") -> str:
    lang = (lang or "text").lower()
    body = highlighted(lang, code)
    label = html.escape(title) if title else LANG_NAMES.get(lang, lang.upper())
    return (f'<div class="code-block"><div class="code-bar"><span class="code-lang">{label}</span>'
            f'<button class="copy" type="button" data-copy aria-label="Copy code">{icon("copy", 14)}<span>Copy</span></button></div>'
            f'<pre class="hl"><code class="lang-{html.escape(lang)}">{body}</code></pre></div>')


# ----------------------------------------------------------------------------------------------------------- markdown

FENCE = re.compile(r"^(```+)([\w+-]*)(?:[ \t]+title=\"([^\"]*)\")?[ \t]*\n(.*?)^\1[ \t]*$", re.M | re.S)
ENDPOINT = re.compile(r"^::endpoint[ \t]+([A-Z, ]+?)[ \t]+(\S+)(?:[ \t]+(.*))?$", re.M)
CONSOLE = re.compile(r"^:::console[ \t]*(.*?)\n(.*?)^:::[ \t]*$", re.M | re.S)
DIAGRAM = re.compile(r"^:::diagram[ \t]*(.*?)\n(.*?)^:::[ \t]*$", re.M | re.S)
SECTION = re.compile(r"^@@[ \t]+(.+)$", re.M)
STATUS = {"200": "OK", "201": "Created", "202": "Accepted", "204": "No Content", "400": "Bad Request", "401": "Unauthorized",
          "403": "Forbidden", "404": "Not Found", "409": "Conflict", "410": "Gone", "412": "Precondition Failed",
          "413": "Content Too Large", "422": "Unprocessable Content", "429": "Too Many Requests", "500": "Internal Server Error",
          "503": "Service Unavailable"}
INLINE_ICON = re.compile(r"(?<![\w:]):([a-z][a-z0-9-]+):(?![\w:])")


class Renderer:
    def __init__(self, page: Page):
        self.page = page
        self.slots: dict[str, str] = {}
        self.fig = 0

    def slot(self, html_: str) -> str:
        key = f"SLOT{len(self.slots)}X"
        self.slots[key] = html_
        return f"\n\n<!--{key}-->\n\n"

    def render(self, text: str, top: bool = False) -> str:
        text = CONSOLE.sub(lambda m: self.slot(self.console(m.group(1).strip(), m.group(2))), text)
        text = DIAGRAM.sub(lambda m: self.slot(self.diagram(m.group(1).strip(), m.group(2))), text)
        text = FENCE.sub(lambda m: self.slot(code_block(m.group(2), m.group(4).rstrip("\n") + "\n", m.group(3) or "")), text)
        text = self.containers(text)
        text = ENDPOINT.sub(lambda m: self.slot(self.endpoint(m.group(1), m.group(2), m.group(3) or "")), text)
        text = INLINE_ICON.sub(lambda m: icon(m.group(1), 16, "inline") if m.group(1) in ICONS else m.group(0), text)
        md = markdown.Markdown(extensions=["tables", "attr_list", "sane_lists", "toc", "def_list", "md_in_html"],
                               extension_configs={"toc": {"permalink": "#", "permalink_class": "anchor",
                                                          "permalink_title": "Link to this section", "toc_depth": "2-3"}})
        out = md.convert(text)
        if top:
            self.page.toc = [t for t in md.toc_tokens]
        # slots can contain slots (a code block inside a tab inside a callout)
        for _ in range(6):
            new = re.sub(r"<p>\s*<!--(SLOT\d+X)-->\s*</p>|<!--(SLOT\d+X)-->", lambda m: self.slots[m.group(1) or m.group(2)], out)
            if new == out:
                break
            out = new
        out = re.sub(r'(href|src)="(/[^"]*)"', lambda m: f'{m.group(1)}="{rel(self.page, m.group(2))}"', out)
        if top:      # once, after every nested block is in place (a table inside tabs is rendered twice)
            out = re.sub(r"<table>(.*?)</table>", lambda m: f'<div class="table-wrap"><table{field_class(m.group(1))}>{m.group(1)}</table></div>',
                         out, flags=re.S)
            # long inline code (paths, file names) may wrap at its own seams, never inside a word
            out = re.sub(r"<code>([^<]{25,})</code>", lambda m: "<code>" + re.sub(r"([/._-])(?=[^/._-])", r"\1<wbr>", m.group(1)) + "</code>", out)
        return out

    def containers(self, text: str) -> str:
        lines = text.split("\n")
        out, i = [], 0
        while i < len(lines):
            m = re.match(r"^:::([a-z]+)[ \t]*(.*)$", lines[i])
            if not m:
                out.append(lines[i])
                i += 1
                continue
            kind, arg = m.group(1), m.group(2).strip()
            depth, j, inner = 1, i + 1, []
            while j < len(lines):
                if re.match(r"^:::[a-z]+", lines[j]):
                    depth += 1
                elif lines[j].strip() == ":::":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(lines[j])
                j += 1
            if depth:
                raise SystemExit(f"{self.page.slug}: unclosed :::{kind}")
            out.append(self.slot(self.container(kind, arg, "\n".join(inner))))
            i = j + 1
        return "\n".join(out)

    def container(self, kind: str, arg: str, inner: str) -> str:
        if kind in ("note", "tip", "warning", "danger"):
            ic = {"note": "info", "tip": "lightbulb", "warning": "warning", "danger": "warning-octagon"}[kind]
            title = arg or {"note": "Note", "tip": "Tip", "warning": "Important", "danger": "Warning"}[kind]
            return (f'<aside class="callout {kind}"><div class="callout-ic">{icon(ic if ic in ICONS else "info", 18)}</div>'
                    f'<div class="callout-body"><p class="callout-t">{html.escape(title)}</p>{self.render(inner)}</div></aside>')
        if kind == "tabs":
            group = arg or ""
            parts = re.split(r"^@@[ \t]+(.+)$", inner, flags=re.M)
            labels, panels = parts[1::2], parts[2::2]
            tid = f"t{len(self.slots)}"
            tabs = "".join(f'<button role="tab" type="button" aria-selected="{"true" if n == 0 else "false"}" data-tab="{html.escape(l.strip())}"'
                           f' id="{tid}-{n}" aria-controls="{tid}-p{n}">{html.escape(l.strip())}</button>' for n, l in enumerate(labels))
            body = "".join(f'<div class="tab-panel" role="tabpanel" id="{tid}-p{n}" aria-labelledby="{tid}-{n}"{"" if n == 0 else " hidden"}>'
                           f'{self.render(p)}</div>' for n, p in enumerate(panels))
            return (f'<div class="tabs" data-tabs data-group="{html.escape(group)}"><div class="tab-list" role="tablist">{tabs}</div>'
                    f'{body}</div>')
        if kind == "figure":
            self.fig += 1
            src = arg.split()[0]
            alt = html.escape(re.sub(r"<[^>]+>", "", markdown.markdown(inner.strip()))).strip()[:180]
            cap = self.render(inner).strip()
            cap = re.sub(r"^<p>|</p>$", "", cap)
            return (f'<figure class="shot"><div class="frame"><img src="{src}" alt="{alt}" loading="lazy" decoding="async"></div>'
                    f'<figcaption><span class="fig-n">Fig. {self.fig}</span>{cap}</figcaption></figure>')
        if kind == "cards":
            cards = []
            for line in inner.strip().splitlines():
                m = re.match(r"^-\s+(?:([a-z][a-z0-9-]*)\s+)?\[([^\]]+)\]\(([^)]+)\)\s*(.*)$", line.strip())
                if not m:
                    continue
                ic, title, href, desc = m.groups()
                cards.append(f'<a class="card" href="{href}">{icon(ic, 20, "card-ic") if ic else ""}'
                             f'<span class="card-t">{html.escape(title)}{icon("arrow-right", 14, "card-go")}</span>'
                             f'<span class="card-d">{markdown.markdown(desc)[3:-4]}</span></a>')
            return f'<div class="cards">{"".join(cards)}</div>'
        if kind == "columns":
            cols = []
            for block in re.split(r"^(?=#{2,3} )", inner.strip(), flags=re.M):
                if not block.strip():
                    continue
                first, _, rest = block.partition("\n")
                level = len(first) - len(first.lstrip("#"))
                items = []
                for line in rest.strip().splitlines():
                    m = re.match(r"^-\s+\[([^\]]+)\]\(([^)]+)\)\s*(.*)$", line.strip())
                    if m:
                        t, href, d = m.groups()
                        desc = f'<span class="col-d">{markdown.markdown(d)[3:-4]}</span>' if d else ""
                        items.append(f'<li><a href="{href}"><span class="col-t">{html.escape(t)}</span>{desc}</a></li>')
                cols.append(f'<div class="col-list"><h{level} class="col-h">{html.escape(first.lstrip("# ").strip())}</h{level}>'
                            f'<ul class="col-ul">{"".join(items)}</ul></div>')
            return f'<div class="columns n{len(cols)}">{"".join(cols)}</div>'
        if kind == "steps":
            return f'<div class="steps">{self.render(inner)}</div>'
        raise SystemExit(f"{self.page.slug}: unknown container :::{kind}")

    def console(self, title: str, inner: str) -> str:
        """Code that sits beside the prose: request tabs (one per "@@ Label"), then the response or output pane."""
        parts = SECTION.split(inner)
        if not parts[0].strip() and len(parts) > 1:
            pairs = list(zip(parts[1::2], parts[2::2]))
        else:                                   # a single block with no labels
            pairs = [("", inner)]
        reqs, resp = [], None
        for label, body in pairs:
            m = FENCE.search(body)
            if not m:
                raise SystemExit(f"{self.page.slug}: console section {label!r} has no code block")
            lang, code = (m.group(2) or "text").lower(), m.group(4).rstrip("\n") + "\n"
            if re.match(r"(?i)^(response|output)\b", label.strip()):
                resp = (label.strip(), lang, code)
            else:
                reqs.append((label.strip() or LANG_NAMES.get(lang, lang.upper()), lang, code))
        cid = f"c{len(self.slots)}"
        head = ""
        em = re.match(r"^([A-Z]+(?:,\s*[A-Z]+)*)\s+(/\S*)$", title)
        if em:
            ms = "".join(f'<span class="method {x.strip().lower()}">{x.strip()}</span>' for x in em.group(1).split(","))
            head = f'<div class="dc-head">{ms}<code>{html.escape(em.group(2))}</code></div>'
        elif title:
            head = f'<div class="dc-head"><span class="dc-title">{html.escape(title)}</span></div>'
        if len(reqs) > 1:
            tabs = "".join(f'<button role="tab" type="button" id="{cid}-t{n}" aria-controls="{cid}-p{n}" data-tab="{html.escape(l)}"'
                           f' aria-selected="{"true" if n == 0 else "false"}"{"" if n == 0 else " tabindex=-1"}>{html.escape(l)}</button>'
                           for n, (l, _, _) in enumerate(reqs))
            left = f'<div class="dc-tabs" role="tablist" aria-label="Language">{tabs}</div>'
        else:
            left = f'<span class="dc-lang">{html.escape(reqs[0][0])}</span>' if reqs else ""
        bar = (f'<div class="console-bar">{left}<button class="copy" type="button" data-copy aria-label="Copy the example">'
               f'{icon("copy", 14)}<span>Copy</span></button></div>') if reqs else ""
        panels = "".join(
            f'<div class="dc-panel" role="{"tabpanel" if len(reqs) > 1 else "group"}" id="{cid}-p{n}"'
            f'{f" aria-labelledby={cid}-t{n}" if len(reqs) > 1 else ""}{"" if n == 0 else " hidden"}>'
            f'<pre class="hl" tabindex="0"><code class="lang-{lang}">{highlighted(lang, code)}</code></pre></div>'
            for n, (_, lang, code) in enumerate(reqs))
        out = ""
        if resp:
            label, lang, code = resp
            words = label.split()
            status = ""
            if len(words) > 1 and words[1].isdigit():
                code_ = words[1]
                ok = "ok" if code_.startswith("2") else "err"
                status = f'<span class="dc-status {ok}">{code_} {STATUS.get(code_, "")}</span>'
            out = (f'<div class="console-split"><span>{html.escape(words[0])}</span>{status}</div>'
                   f'<pre class="hl dc-resp" tabindex="0"><code class="lang-{lang}">{highlighted(lang, code)}</code></pre>')
        # tabs named after systems switch with the page's other system tabs; any other set is a choice of language
        systems = {"Windows", "macOS", "Linux"}
        group = (' data-group="os"' if {l for l, _, _ in reqs} <= systems else ' data-group="lang"') if len(reqs) > 1 else ""
        return f'<!--con--><div class="console dc" data-tabs{group}>{head}{bar}{panels}{out}</div><!--/con-->'

    def diagram(self, title: str, inner: str) -> str:
        """Parts stacked top to bottom, joined by labelled connectors; sits beside the prose like a console.
            # Name | where | what it does        a part
            - Name | file                        a module inside the part above
            > label                              the connection to the next part"""
        inline = lambda t: markdown.markdown(t.strip())[3:-4] if t.strip() else ""   # noqa: E731
        items, mods = [], []

        def close_mods():
            if mods and items:
                rows = "".join(f"<div><dt>{inline(n)}</dt><dd>{inline(f)}</dd></div>" for n, f in mods)
                items[-1] = items[-1].replace("</div></li>", f'<dl class="dg-mods">{rows}</dl></div></li>', 1)
            mods.clear()
        for line in inner.strip().splitlines():
            line = line.strip()
            if line.startswith("# "):
                close_mods()
                name, where, what = (line[2:].split("|") + ["", ""])[:3]
                items.append(f'<li class="dg-node"><div class="dg-box"><p class="dg-name">{inline(name)}'
                             f'{f"<span>{inline(where)}</span>" if where.strip() else ""}</p>'
                             f'{f"<p class=dg-what>{inline(what)}</p>" if what.strip() else ""}</div></li>')
            elif line.startswith("- "):
                n, f = (line[2:].split("|") + [""])[:2]
                mods.append((n, f))
            elif line.startswith("> "):
                close_mods()
                items.append(f'<li class="dg-link" aria-hidden="true"><span>{inline(line[2:])}</span></li>')
        close_mods()
        head = f'<div class="dc-head"><span class="dc-title">{html.escape(title)}</span></div>' if title else ""
        return f'<!--con--><figure class="console dc dg">{head}<ol class="dg-stack">{"".join(items)}</ol></figure><!--/con-->'

    def endpoint(self, method: str, path: str, note: str) -> str:
        ms = "".join(f'<span class="method {m.strip().lower()}">{m.strip()}</span>' for m in method.split(","))
        return (f'<div class="endpoint">{ms}<code>{html.escape(path)}</code>'
                f'{f"<span class=endpoint-note>{html.escape(note)}</span>" if note else ""}</div>')


# ----------------------------------------------------------------------------------------------------------- rows

def field_class(table: str) -> str:
    first = re.search(r"<th[^>]*>(.*?)</th>", table, re.S)
    return ' class="fields"' if first and re.sub(r"<[^>]+>", "", first.group(1)).strip().lower() in TRACED else ""


TRACED = {"field", "parameter", "header", "name", "query parameter", "path parameter", "variable", "setting", "option", "key"}


def trace_table(m: re.Match) -> str:
    """Rows of a table that lists fields (its first heading is Field, Parameter, Header...) take part in the trace."""
    table = m.group(0)
    if not table.startswith('<table class="fields">'):
        return table
    return re.sub(r"<tr>\s*<td><code>((?:[^<]|<wbr>)+)</code>",
                  lambda r: f'<tr data-param="{r.group(1).replace("<wbr>", "")}" tabindex="0">\n<td><code>{r.group(1)}</code>', table)


def endpoint_index(body: str) -> str:
    """Every endpoint on a reference page, each linked to its section: the console column of the page's opening row."""
    items = []
    for chunk in re.split(r'(?=<h[23] id=")', body):
        hid = re.match(r'<h[23] id="([^"]+)"', chunk)
        for ep in re.finditer(r'<div class="endpoint">(.*?)<code>(.*?)</code>', chunk):
            if hid:
                items.append(f'<li><a href="#{hid.group(1)}">{ep.group(1)}<code>{ep.group(2)}</code></a></li>')
    if len(items) < 2:
        return ""
    return (f'<!--con--><nav class="console dc ep-index" aria-label="Endpoints on this page"><div class="dc-head">'
            f'<span class="dc-title">Endpoints on this page</span></div><ul>{"".join(items)}</ul></nav><!--/con-->')


def compose(body: str, index: bool = False) -> tuple[str, bool]:
    """Lays a page with consoles out as rows: each H2 (or an H3 with a console of its own) starts a row whose console
    sits to the right of its prose. Parameter rows in that prose are linked to the console for the parameter trace."""
    if "<!--con-->" not in body:
        return body, False
    if index and not body.startswith("<h2"):
        first = re.search(r'<h[23] id="', body)
        if first:
            body = body[:first.start()] + endpoint_index(body) + body[first.start():]
    rows: list[str] = []
    for chunk in re.split(r'(?=<h[23] id=")', body):
        if rows and chunk.startswith("<h3") and "<!--con-->" not in chunk:
            rows[-1] += chunk                    # an H3 without its own console stays in the row above
        else:
            rows.append(chunk)
    out = []
    for chunk in rows:
        cons = re.findall(r"<!--con-->(.*?)<!--/con-->", chunk, re.S)
        text = re.sub(r"<p>\s*</p>", "", re.sub(r"<!--con-->.*?<!--/con-->", "", chunk, flags=re.S)).strip()
        if not text and not cons:
            continue
        if cons:
            text = re.sub(r"<table[^>]*>.*?</table>", trace_table, text, flags=re.S)
        kind = " sub" if chunk.startswith("<h3") else ""
        code = f'<div class="dsec-code"><div class="dsec-pin">{"".join(cons)}</div></div>' if cons else ""
        out.append(f'<section class="dsec{kind}{" has-code" if cons else ""}"><div class="dsec-text">{text}</div>{code}</section>')
    return "\n".join(out), True


# ----------------------------------------------------------------------------------------------------------- layout

def header(page: Page) -> str:
    r = lambda t: rel(page, t)   # noqa: E731
    links = [("/index.html", "Product"), ("/docs/index", "Docs"), ("/docs/api/index", "API"), ("/docs/changelog", "Changelog")]
    active = "API" if page.slug.startswith("api/") else "Changelog" if page.slug == "changelog" else "Docs"
    nav = "".join(f'<a href="{r(h)}"{" aria-current=page" if t == active else ""}>{t}</a>' for h, t in links)
    return f'''<header class="nav scrolled" id="nav">
  <div class="nav-in docs-nav-in">
    <button class="side-btn" type="button" aria-controls="docs-side" aria-expanded="false" aria-label="Open the contents">{icon("list", 20) if "list" in ICONS else icon("list-bullets", 20)}</button>
    <a class="brand" href="{r('/index.html')}" aria-label="Bud Decision Studio home">
      <img src="{r('/assets/brand/bud-mark.png')}" width="26" height="26" alt="">
      <span>Bud <b>Decision Studio</b></span><span class="brand-docs" data-version title="Documentation for version {VERSION}">{VERSION}</span>
    </a>
    <nav class="nav-links" aria-label="Site">{nav}</nav>
    <div class="nav-end">
      <button class="search-btn" type="button" data-search-open aria-label="Search the documentation">{icon("magnifying-glass", 16)}<span>Search</span><kbd data-kbd>Ctrl K</kbd></button>
      <a class="icon-link" href="https://github.com/BudEcosystem/Bud-Decision-Engine" aria-label="Source on GitHub">{icon("github-logo", 20)}</a>
      <a class="btn btn-sm btn-primary" href="{r('/index.html#download')}">Download</a>
    </div>
  </div>
</header>'''


def sidebar(page: Page) -> str:
    parts = []
    for sec, items in NAV:
        links = "".join(
            f'<a href="{rel(page, "/docs/" + slug)}"{" aria-current=page" if slug == page.slug else ""}>{html.escape(title)}</a>'
            + (outline(page) if slug == page.slug else "")
            for slug, title in items)
        parts.append(f'<div class="side-sec"><p class="side-h">{html.escape(sec)}</p>{links}</div>')
    return (f'<aside class="docs-side" id="docs-side" aria-label="Documentation"><div class="side-in">'
            f'{"".join(parts)}</div></aside>')


def outline(page: Page) -> str:
    """The page's H2 sections, listed under its own link in the contents and followed as you read."""
    items = [f'<a href="#{t["id"]}">{html.escape(html.unescape(t["name"]))}</a>' for t in page.toc]
    if len(items) < 2:
        return ""
    return f'<nav class="side-toc" aria-label="On this page">{"".join(items)}</nav>'


def pager(page: Page, pages: list[Page]) -> str:
    i = [p.slug for p in pages].index(page.slug)
    prev_, next_ = (pages[i - 1] if i else None), (pages[i + 1] if i + 1 < len(pages) else None)
    a = lambda p, cls, lab: (f'<a class="pg {cls}" href="{rel(page, "/docs/" + p.slug)}"><span class="pg-l">{lab}</span>'   # noqa: E731
                             f'<span class="pg-t">{html.escape(p.nav_title)}</span></a>') if p else "<span></span>"
    return f'<nav class="pager" aria-label="Previous and next">{a(prev_, "prev", "Previous")}{a(next_, "next", "Next")}</nav>'


def footer(page: Page) -> str:
    r = lambda t: rel(page, t)   # noqa: E731
    return f'''<footer class="foot">
  <div class="foot-in wrap">
    <div class="foot-brand">
      <img src="{r('/assets/brand/bud-lockup-white.png')}" alt="Bud Ecosystem" width="220" height="36">
      <p>Bud Decision Studio runs open decision models on your own computer.</p>
    </div>
    <nav class="foot-cols" aria-label="Footer">
      <div><p class="foot-h">Product</p><a href="{r('/index.html#how')}">How it works</a><a href="{r('/index.html#models')}">Models</a><a href="{r('/index.html#download')}">Download</a></div>
      <div><p class="foot-h">Documentation</p><a href="{r('/docs/quickstart')}">Quickstart</a><a href="{r('/docs/manual/index')}">User manual</a><a href="{r('/docs/api/index')}">API reference</a><a href="{r('/docs/dev/architecture')}">Developers</a></div>
      <div><p class="foot-h">Bud Ecosystem</p><a href="https://github.com/BudEcosystem/Bud-Decision-Engine">Source on GitHub</a><a href="https://github.com/BudEcosystem/Bud-Decision-Engine/releases">Releases</a><a href="https://github.com/BudEcosystem/Bud-Decision-Engine/issues">Report a problem</a></div>
    </nav>
  </div>
  <p class="foot-fine wrap">Model names and logos belong to their makers. Benchmark figures are each maker's published claims. Jev is a model by TypeSafe AI.</p>
</footer>'''


def layout(page: Page, pages: list[Page]) -> str:
    r = lambda t: rel(page, t)   # noqa: E731
    title = page.meta.get("title", page.nav_title)
    desc = page.meta.get("description", "")
    crumb = "" if page.slug == "index" else (f'<p class="crumb"><a href="{r("/docs/index")}">Docs</a>{icon("caret-right", 12)}'
                                             f'<span>{html.escape(page.section)}</span></p>')
    lead = f'<p class="lead">{page.meta["lead"]}</p>' if page.meta.get("lead") else ""
    body_cls = " ".join(["docs"] + (["has-console"] if page.console else []) + (["home"] if page.meta.get("layout") == "home" else []))
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{html.escape(title)} · {SITE_NAME} Docs</title>
  <meta name="description" content="{html.escape(desc)}">
  <meta name="theme-color" content="#0F0C17">
  <meta property="og:title" content="{html.escape(title)} · {SITE_NAME}">
  <meta property="og:description" content="{html.escape(desc)}">
  <meta property="og:type" content="article">
  <link rel="icon" type="image/png" href="{r('/assets/brand/favicon.png')}">
  <link rel="preload" href="{r('/assets/fonts/InterVariable.woff2')}" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="{r('/assets/css/site.css')}">
  <link rel="stylesheet" href="{r('/assets/css/docs.css')}">
</head>
<body class="{body_cls}" id="top">
<a class="skip" href="#main">Skip to content</a>
{header(page)}
<div class="docs-shell">
{sidebar(page)}
<div class="side-scrim" hidden></div>
<main class="docs-main" id="main">
  <article class="prose">
    <header class="page-head">
      {crumb}
      <h1>{html.escape(title)}</h1>
      {lead}
    </header>
    {page.body}
  </article>
  {pager(page, pages)}
</main>
</div>
{footer(page)}
<div class="search" id="search" hidden>
  <div class="search-panel" role="dialog" aria-modal="true" aria-label="Search the documentation">
    <div class="search-bar">{icon("magnifying-glass", 18)}<input type="search" placeholder="Search the documentation" aria-label="Search" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>
    <div class="search-results" role="listbox" aria-label="Results"></div>
    <p class="search-foot"><span><kbd>↑</kbd><kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span></p>
  </div>
</div>
<script>window.DOCS_ROOT = "{r('/docs/')}"; window.DOCS_VERSION = "{VERSION}";</script>
<script src="{r('/docs/search-index.js')}" defer></script>
<script src="{r('/assets/js/release.js')}" defer></script>
<script src="{r('/assets/js/docs.js')}" defer></script>
</body>
</html>
'''


# ----------------------------------------------------------------------------------------------------------- search

def search_entries(page: Page) -> list[dict]:
    """One entry per section: the page, its headings and their text, for the client-side search."""
    body = re.sub(r'<div class="code-block">.*?</pre></div>', " ", page.body, flags=re.S)
    body = re.sub(r"<!--con-->.*?<!--/con-->", " ", body, flags=re.S)
    body = re.sub(r'<a class="anchor"[^>]*>.*?</a>', "", body)
    chunks = re.split(r'<h([23]) id="([^"]+)">(.*?)</h\1>', body)
    title = page.meta.get("title", page.nav_title)
    out = [{"t": title, "s": page.section, "u": page.slug + ".html", "h": "", "x": _plain(chunks[0])[:600],
            "k": _names(chunks[0]), "f": _fields(chunks[0])}]
    for i in range(1, len(chunks) - 3 + 1, 4):
        heading = _plain(chunks[i + 2])
        out.append({"t": title, "s": page.section, "u": f"{page.slug}.html#{chunks[i + 1]}", "h": heading,
                    "x": _plain(chunks[i + 3])[:400], "k": _names(chunks[i + 2] + chunks[i + 3]), "f": _fields(chunks[i + 3])})
    return out


def _fields(h: str) -> str:
    """The fields a section defines: the first column of its field tables."""
    names = set()
    for table in re.findall(r'<table class="fields">(.*?)</table>', h, re.S):
        names |= {re.sub(r"<wbr>", "", n).split(".")[-1] for n in re.findall(r"<tr>\s*<td><code>((?:[^<]|<wbr>)+)</code>", table)}
    return " ".join(sorted(names, key=str.lower))


def _names(h: str) -> str:
    """The field, header and command names a section documents (its inline code), so a search for one finds it."""
    names = {n for n in (re.sub(r"<wbr>", "", c) for c in re.findall(r"<code>([^<]{2,48})</code>", h))
             if re.fullmatch(r"[A-Za-z_][\w.-]*", n)}
    return " ".join(sorted(names, key=str.lower))


def _plain(h: str) -> str:
    h = re.sub(r"<wbr>", "", h)
    h = re.sub(r"<thead>.*?</thead>", " ", h, flags=re.S)  # column headings are not content
    h = re.sub(r"</td>\s*<td[^>]*>", " ", h)               # a table row reads as one phrase...
    h = re.sub(r"</(p|li|tr|h[1-6]|dt|dd)>", "\x00", h)    # ...and every block or row ends a sentence
    t = re.sub(r"[ \t\r\n]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))
    t = re.sub(r"\s+([.,;:)])(?=\s|$|\x00)", r"\1", t)    # "word ." but not ", .venv"
    t = re.sub(r"([.:;!?])\s*\x00", r"\1 ", t)            # no full stop after text that already ends in one
    t = re.sub(r"\s*\x00\s*", ". ", t)
    return re.sub(r"\.(\s+\.)+", ".", t).strip(" .")         # stray stops between blocks, never an ellipsis


# ----------------------------------------------------------------------------------------------------------- main

def main() -> int:
    pages = all_pages()
    missing = [p.slug for p in pages if not p.src.exists()]
    if missing and "--partial" in sys.argv:      # a preview while pages are being written
        pages = [p for p in pages if p.src.exists()]
    elif missing:
        print("missing pages:", ", ".join(missing))
        return 1
    if OUT.exists():
        for f in OUT.rglob("*.html"):
            f.unlink()
    index = []
    for p in pages:
        p.meta, text = front_matter(p.src.read_text(encoding="utf-8"))
        p.body = Renderer(p).render(text, top=True)
        index += search_entries(p)
        p.body, p.console = compose(p.body, index=p.slug.startswith("api/"))
    for p in pages:
        p.out.parent.mkdir(parents=True, exist_ok=True)
        p.out.write_text(layout(p, pages), encoding="utf-8")
    (OUT / "search-index.js").write_text("window.DOCS_INDEX = " + json.dumps(index, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"built {len(pages)} pages and {len(index)} search entries into {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
