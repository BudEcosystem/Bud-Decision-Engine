"""Renders every page, dialog and setup screen in WebKit (the engine of Safari and of the macOS and Linux desktop
apps) and reports page errors, so layout differences from Chromium are caught before release.

    python scripts/webkit-sweep.py --url http://127.0.0.1:8420 [--fresh http://127.0.0.1:8432] [--setup http://127.0.0.1:8440]
                                   [--out docs/webkit] [--sizes 1325x810,1391x884,760x900]

--fresh is a studio with no models on disk (for the first-run model chooser); --setup serves desktop/installer.
Needs Playwright's WebKit: python -m playwright install webkit
"""
import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PAGES = ["playground", "templates", "models", "evaluate", "history", "api", "learn", "system"]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420")
    ap.add_argument("--fresh", default="")
    ap.add_argument("--setup", default="")
    ap.add_argument("--out", default="webkit-sweep")
    ap.add_argument("--sizes", default="1325x810,760x900")
    ap.add_argument("--themes", default="light,dark")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    url = a.url.rstrip("/") + "/"
    problems: list[str] = []

    async with async_playwright() as p:
        b = await p.webkit.launch()
        for size in a.sizes.split(","):
            w, h = (int(x) for x in size.split("x"))
            for theme in a.themes.split(","):
                ctx = await b.new_context(viewport={"width": w, "height": h}, color_scheme=theme, reduced_motion="reduce")
                pg = await ctx.new_page()
                tag = f"{w}x{h}-{theme}"
                pg.on("pageerror", lambda e, t=tag: problems.append(f"[{t}] page error: {e}"))
                pg.on("console", lambda m, t=tag: problems.append(f"[{t}] console.{m.type}: {m.text}") if m.type == "error" else None)
                for name in PAGES:
                    await pg.goto(f"{url}#/{name}")
                    await pg.wait_for_timeout(2200)
                    await pg.screenshot(path=str(out / f"{tag}-{name}.png"))
                # the model loader palette and the "Help me choose" dialog
                await pg.goto(f"{url}#/playground")
                await pg.wait_for_timeout(1500)
                await pg.keyboard.press("Control+l")
                await pg.wait_for_timeout(700)
                await pg.screenshot(path=str(out / f"{tag}-loader.png"))
                await pg.keyboard.press("Escape")
                await pg.goto(f"{url}#/models")
                await pg.wait_for_timeout(1500)
                await pg.click("#help")
                await pg.wait_for_timeout(600)
                await pg.screenshot(path=str(out / f"{tag}-help.png"))
                await pg.keyboard.press("Escape")
                # the Download models sheet, from the Models page
                await pg.click("#dlall")
                await pg.wait_for_timeout(900)
                await pg.screenshot(path=str(out / f"{tag}-download-sheet.png"))
                box = await pg.locator("#chlist").bounding_box()
                if not box or box["height"] < 120:
                    problems.append(f"[{tag}] download sheet: model list is {box and round(box['height'])} px tall")
                await ctx.close()
                if a.fresh:   # the first-run chooser, in a studio with nothing downloaded
                    ctx = await b.new_context(viewport={"width": w, "height": h}, color_scheme=theme, reduced_motion="reduce")
                    pg = await ctx.new_page()
                    await pg.goto(a.fresh.rstrip("/") + "/#/playground")
                    await pg.wait_for_timeout(3500)
                    await pg.screenshot(path=str(out / f"{tag}-first-run.png"))
                    box = await pg.locator("#chlist").bounding_box()
                    if not box or box["height"] < 120:
                        problems.append(f"[{tag}] first-run chooser: model list is {box and round(box['height'])} px tall")
                    await ctx.close()
                if a.setup:   # the desktop setup screens (preview mode)
                    ctx = await b.new_context(viewport={"width": w, "height": h}, color_scheme=theme, reduced_motion="reduce")
                    pg = await ctx.new_page()
                    s = a.setup.rstrip("/") + "/index.html"
                    await pg.goto(s + "?hw=mac&delay=100")
                    await pg.wait_for_timeout(1500)
                    await pg.screenshot(path=str(out / f"{tag}-setup-welcome.png"))
                    await pg.click("#next")
                    await pg.wait_for_timeout(900)
                    await pg.screenshot(path=str(out / f"{tag}-setup-device.png"))
                    await pg.click("#next")
                    await pg.wait_for_timeout(2500)
                    await pg.screenshot(path=str(out / f"{tag}-setup-install.png"))
                    await pg.goto(s + "?hw=mac&delay=100&stop=libs&speed=60")
                    await pg.wait_for_timeout(1200)
                    await pg.click("#next"); await pg.wait_for_timeout(700); await pg.click("#next")
                    await pg.wait_for_timeout(2500)
                    await pg.screenshot(path=str(out / f"{tag}-setup-error.png"))
                    await pg.goto(s + "?installed=1&startfail=1")
                    await pg.wait_for_timeout(2000)
                    await pg.screenshot(path=str(out / f"{tag}-setup-splash-error.png"))
                    await ctx.close()
        await b.close()
    print(f"screenshots in {out}/")
    print("\n".join(problems) if problems else "no page errors and no collapsed lists")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
