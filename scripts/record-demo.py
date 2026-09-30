"""Records the short README demo of Bud Decision Studio (docs/media/demo.mp4 and demo.gif).

    python scripts/record-demo.py --url http://127.0.0.1:8420 --model laya

Needs a running studio with the model downloaded, Playwright, and ffmpeg. The walkthrough: open the Playground,
choose an example, press Decide and watch the answers arrive, look at the question types, then the Models and
Activity pages.
"""
import argparse
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "media"


async def record(url: str, model: str, browser: str | None) -> Path:
    tmp = Path(tempfile.mkdtemp())
    async with async_playwright() as p:
        b = await p.chromium.launch(**({"executable_path": browser} if browser else {}))
        ctx = await b.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=str(tmp),
                                  record_video_size={"width": 1280, "height": 800})
        pg = await ctx.new_page()
        # warm the model first so the video shows answers, not loading
        await pg.goto(f"{url}#/playground?model={model}&example=support")
        await pg.wait_for_timeout(2500)
        await pg.click("#decide")
        await pg.wait_for_selector("#out .fig", timeout=300000)
        await pg.goto(f"{url}#/playground?model={model}&example=tour")
        await pg.wait_for_timeout(2500)
        # the take
        await pg.goto(f"{url}#/playground?model={model}&example=support")
        await pg.wait_for_timeout(2200)
        await pg.click("#decide")
        await pg.wait_for_selector("#out .fig", timeout=120000)
        await pg.wait_for_timeout(3200)
        await pg.click("#examples")
        await pg.wait_for_timeout(1200)
        await pg.locator('[data-pick="tour"], [data-example="tour"]').first.click()
        await pg.wait_for_timeout(1500)
        await pg.click("#decide")
        await pg.wait_for_selector("#out .fig >> nth=5", timeout=120000)
        await pg.wait_for_timeout(2500)
        await pg.locator("#out").evaluate("el => el.closest('.results-body')?.scrollBy({top: 500, behavior: 'smooth'})")
        await pg.wait_for_timeout(2200)
        await pg.locator(".create-dec").scroll_into_view_if_needed()
        await pg.wait_for_timeout(2200)
        await pg.goto(f"{url}#/models")
        await pg.wait_for_timeout(2200)
        await pg.locator('tr[data-id="intern-decision-4b"] td').first.click()
        await pg.wait_for_timeout(1500)
        await pg.click('[data-tab="jev"]')
        await pg.wait_for_timeout(2600)
        await pg.goto(f"{url}#/activity")
        await pg.wait_for_timeout(3500)
        video = await pg.video.path()
        await ctx.close()
        await b.close()
    return Path(video)


def encode(webm: Path, skip_seconds: float):
    OUT.mkdir(parents=True, exist_ok=True)
    mp4, gif = OUT / "demo.mp4", OUT / "demo.gif"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{skip_seconds}", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "26", "-preset", "slow", "-movflags", "+faststart", "-an", str(mp4)], check=True)
    pal = webm.with_suffix(".png")
    vf = "fps=10,scale=880:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{skip_seconds}", "-i", str(webm), "-vf", f"{vf},palettegen=max_colors=128:stats_mode=diff", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{skip_seconds}", "-i", str(webm), "-i", str(pal), "-lavfi",
                    f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", str(gif)], check=True)
    for f in (mp4, gif):
        print(f"wrote {f.relative_to(ROOT)} ({f.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8420/")
    ap.add_argument("--model", default="laya")
    ap.add_argument("--browser", default=None)
    ap.add_argument("--skip", type=float, default=None, help="seconds of warm-up to cut from the start")
    a = ap.parse_args()
    url = a.url.rstrip("/") + "/"
    t = asyncio.run(record(url, a.model, a.browser))
    # cut the warm-up: everything before the second visit to the support example
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(t)], capture_output=True, text=True)
    total = float(probe.stdout.strip() or 0)
    encode(t, a.skip if a.skip is not None else max(0.0, total - 30))
    shutil.rmtree(t.parent, ignore_errors=True)
