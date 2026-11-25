"""Generate assets/icon.png, icon.ico (Windows) and icon.icns (macOS).

Run once (or after changing the design):  python packaging/make_icons.py
Needs Pillow:  pip install pillow
Replace assets/icon.png with your own 1024×1024 artwork and re-run with
--from-png to rebuild the .ico/.icns from it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024


def rounded(size, box, radius, fill):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=fill)
    return layer


def draw_icon() -> Image.Image:
    s = SIZE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # background tile with a vertical gradient (macOS-style rounded square)
    grad = Image.new("RGBA", (s, s))
    top, bottom = (36, 52, 84), (18, 26, 44)
    for y in range(s):
        t = y / (s - 1)
        grad.paste(tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,), (0, y, s, y + 1))
    mask = Image.new("L", (s, s), 0)
    margin = int(s * 0.09)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, s - margin, s - margin), radius=int(s * 0.2), fill=255)
    img.paste(grad, (0, 0), mask)

    # three overlapping translucent "layers" — the stack idea
    cards = [
        ((300, 250, 760, 610), (64, 156, 255, 170)),
        ((230, 340, 690, 700), (255, 112, 67, 170)),
        ((340, 420, 800, 780), (102, 214, 140, 185)),
    ]
    for box, colour in cards:
        shadow = rounded((s, s), tuple(v + 14 for v in box), 46, (0, 0, 0, 90)).filter(ImageFilter.GaussianBlur(18))
        img = Image.alpha_composite(img, shadow)
        img = Image.alpha_composite(img, rounded((s, s), box, 46, colour))
        outline = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(outline).rounded_rectangle(box, radius=46, outline=(255, 255, 255, 200), width=10)
        img = Image.alpha_composite(img, outline)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-png", action="store_true", help="rebuild .ico/.icns from assets/icon.png")
    args = ap.parse_args()
    ASSETS.mkdir(exist_ok=True)
    png = ASSETS / "icon.png"
    if args.from_png:
        img = Image.open(png).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    else:
        img = draw_icon()
        img.save(png)
    img.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(ASSETS / "icon.icns")
    print(f"Wrote {png.name}, icon.ico, icon.icns to {ASSETS}")


if __name__ == "__main__":
    main()
