"""Builds desktop/build/icon.ico (and icon.png) from Convexa's isotipo (isotipo_transparente.png, designed 2026-08-03, the same mark the dashboard
uses as frontend/public/logo-watermark.png): the red -> green arc with the dot above it, cropped to the mark and centred on the app's black
rounded square; 16-48 px use a bolder redraw of the same mark. Run: python desktop/build/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
SOURCE = HERE.parents[1] / "frontend" / "public" / "logo-watermark.png"
SIZE = 1024
MARK_WIDTH = 0.80  # share of the square the arc spans

mark = Image.open(SOURCE).convert("RGBA")
mark = mark.crop(mark.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox())
scale = SIZE * MARK_WIDTH / mark.width
mark = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.LANCZOS)

icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
ImageDraw.Draw(icon).rounded_rectangle((24, 24, SIZE - 24, SIZE - 24), radius=180, fill=(0, 0, 0, 255), outline=(0x22, 0x26, 0x2F, 255), width=8)
icon.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))

# Small sizes (16-48 px: taskbar, window corner, menus): the same isotipo redrawn bolder and larger in the square, since the thin original
# vanishes there (Lester 2026-10-29 "si"). Colours sampled from the isotipo: red -> grey -> green, white dot above the arc's top.
RED, MID, GREEN = (235, 22, 32), (182, 140, 140), (8, 212, 100)


def arc_colour(t):
    if t <= 0.42:
        return RED
    if t >= 0.58:
        return GREEN
    f = (t - 0.42) / 0.16
    a, b = (RED, MID) if f < 0.5 else (MID, GREEN)
    f = f * 2 if f < 0.5 else (f - 0.5) * 2
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


bold = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(bold)
draw.rounded_rectangle((24, 24, SIZE - 24, SIZE - 24), radius=180, fill=(0, 0, 0, 255))
left, right, base, apex, stroke = 120, SIZE - 120, 690, 545, 92
half = (right - left) / 2
radius = (half**2 + (base - apex) ** 2) / (2 * (base - apex))
centre_y = apex + radius
steps = 900
for i in range(steps + 1):
    x = left + (right - left) * i / steps
    y = centre_y - (radius**2 - (x - SIZE / 2) ** 2) ** 0.5
    draw.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=arc_colour(i / steps) + (255,))
dot = 66
draw.ellipse((SIZE / 2 - dot, 375 - dot, SIZE / 2 + dot, 375 + dot), fill=(255, 255, 255, 255))

icon.resize((512, 512), Image.LANCZOS).save(HERE / "icon.png")
bold.resize((512, 512), Image.LANCZOS).save(HERE / "icon-small.png")
small = [bold.resize((s, s), Image.LANCZOS) for s in (16, 24, 32, 48)]
large = [icon.resize((s, s), Image.LANCZOS) for s in (64, 128)]
icon.resize((256, 256), Image.LANCZOS).save(
    HERE / "icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    append_images=small + large,
)
print("ok")
