"""Builds desktop/build/icon.ico (and icon.png) from Convexa's isotipo (isotipo_transparente.png, designed 2026-08-03, the same mark the dashboard
uses as frontend/public/logo-watermark.png): the red -> green arc with the dot above it, cropped to the mark and centred on the app's black
rounded square. Run: python desktop/build/make_icon.py"""
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

icon.resize((512, 512), Image.LANCZOS).save(HERE / "icon.png")
icon.resize((256, 256), Image.LANCZOS).save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
