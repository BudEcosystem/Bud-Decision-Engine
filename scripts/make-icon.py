"""Draws the Bud Decision Studio app icon from the Bud mark's geometry (no upscaling, so every size is crisp).

    python scripts/make-icon.py            writes ui/brand/app-icon-1024.png (macOS grid) and app-icon-full-1024.png

The mark is the Bud grid: three columns and rows of 70.67-unit cells with 22-unit gaps on a 256-unit square, in the
three brand violets. It sits on a dark superellipse tile (the macOS icon shape) with a faint top light and a soft
shadow. The macOS version keeps Apple's 824 px body on a 1024 px canvas; the "full" version fills more of the canvas
for Windows and Linux launchers, where padded icons look small.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
LIGHT, MID, DEEP = (217, 186, 249), (183, 133, 244), (140, 51, 239)
# (column, row, column span, row span, colour) on the 3 x 3 grid
CELLS = [(0, 0, 1, 1, LIGHT), (1, 0, 1, 1, LIGHT), (2, 0, 1, 2, MID), (0, 1, 1, 2, MID), (1, 1, 1, 1, MID),
         (1, 2, 1, 1, DEEP), (2, 2, 1, 1, DEEP)]
S = 4  # supersampling


def superellipse(cx, cy, r, n=5.0, steps=720):
    import math
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = abs(c) ** (2 / n) * r * (1 if c >= 0 else -1)
        y = abs(s) ** (2 / n) * r * (1 if s >= 0 else -1)
        pts.append((cx + x, cy + y))
    return pts


def draw(body: int, out: Path, size: int = 1024):
    W = size * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    r = body * S / 2
    c = W / 2
    # soft shadow under the tile
    sh = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(superellipse(c, c + 10 * S, r * 0.985), fill=(10, 6, 20, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18 * S)))
    # tile: vertical gradient, violet-black
    grad = Image.new("RGBA", (W, W))
    top, bottom = (40, 32, 56), (14, 11, 20)
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        t = min(1, max(0, (y - (c - r)) / (2 * r)))
        gd.line([(0, y), (W, y)], fill=tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3)) + (255,))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).polygon(superellipse(c, c, r), fill=255)
    img.paste(grad, (0, 0), mask)
    # faint light on the upper edge
    rim = Image.new("L", (W, W), 0)
    ImageDraw.Draw(rim).polygon(superellipse(c, c, r), fill=255)
    inner = Image.new("L", (W, W), 0)
    ImageDraw.Draw(inner).polygon(superellipse(c, c + 3 * S, r - 3 * S), fill=255)
    edge = ImageChops.subtract(rim, inner)   # a thin band along the top edge
    fade = Image.new("L", (W, W))
    fd = ImageDraw.Draw(fade)
    for y in range(W):
        fd.line([(0, y), (W, y)], fill=int(90 * max(0, 1 - (y - (c - r)) / (r * 0.9))))
    light = Image.new("RGBA", (W, W), (255, 255, 255, 0))
    light.putalpha(ImageChops.darker(edge, fade))
    img.alpha_composite(light)
    # the Bud mark, centred, about 52% of the tile body
    m = body * S * 0.52
    unit = m / 256
    cell, gap = 70.67 * unit, 22 * unit
    x0 = y0 = c - m / 2
    md = ImageDraw.Draw(img)
    for col, row, cs, rs, colour in CELLS:
        x = x0 + col * (cell + gap)
        y = y0 + row * (cell + gap)
        md.rectangle([x, y, x + cs * cell + (cs - 1) * gap, y + rs * cell + (rs - 1) * gap], fill=colour + (255,))
    img.resize((size, size), Image.LANCZOS).save(out)
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    brand = ROOT / "ui" / "brand"
    draw(824, brand / "app-icon-1024.png")        # macOS: Apple's icon grid
    draw(940, brand / "app-icon-full-1024.png")   # Windows and Linux launchers
    Image.open(brand / "app-icon-full-1024.png").resize((512, 512), Image.LANCZOS).save(brand / "app-icon.png")
    print("wrote ui/brand/app-icon.png")
