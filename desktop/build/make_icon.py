"""Builds desktop/build/icon.ico (and icon.png) from the approved logo's "icon only" variant (Downloads/convexa_logo_v5.svg, 2026-09-04): dark
rounded square, the convexity arc with its teal -> lilac -> red gradient, and the gold dot at the top of the arc. Drawn at 1024 px and scaled down
for antialiasing. Run: python desktop/build/make_icon.py"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
STOPS = [(0.0, (0x4F, 0xD1, 0xC5)), (0.55, (0xC9, 0xA6, 0xE8)), (1.0, (0xE8, 0x54, 0x6B))]


def gradient(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
    return STOPS[-1][1]


img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.rounded_rectangle((24, 24, SIZE - 24, SIZE - 24), radius=150, fill=(0x11, 0x13, 0x19, 255), outline=(0x22, 0x26, 0x2F, 255), width=6)

cx, cy, r, width = SIZE / 2, 690, 330, 58
steps = 720
for i in range(steps + 1):
    a = math.pi - math.pi * i / steps  # left end (180 deg) -> right end (0 deg), over the top
    x, y = cx + r * math.cos(a), cy - r * math.sin(a)
    # The logo's gradient runs from the bottom-left to the top-right corner: colour by position along that diagonal.
    t = min(1.0, max(0.0, ((x - (cx - r)) / (2 * r)) * 0.8 + ((cy - y) / r) * 0.2))
    draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=gradient(t) + (255,))

dot = 52
draw.ellipse((cx - dot, cy - r - dot, cx + dot, cy - r + dot), fill=(0xF3, 0xC7, 0x7A, 255), outline=(0xD4, 0xA2, 0x4C, 255), width=10)

out = Path(__file__).parent
img.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
img.resize((256, 256), Image.LANCZOS).save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
