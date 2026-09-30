"""Draws the three sample flowchart images (samples/*.png) with Pillow.

Run:  python samples/generate_samples.py
These are ordinary images: the app sends them to the vision model exactly like any upload.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
INK = (20, 20, 20)
W_LINE = 3


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class Canvas:
    def __init__(self, w: int, h: int):
        self.img = Image.new("RGB", (w, h), "white")
        self.d = ImageDraw.Draw(self.img)
        self.font = _font(26)
        self.small = _font(24)

    def text(self, xy, s, font=None):
        self.d.text(xy, s, fill=INK, font=font or self.font, anchor="mm")

    def oval(self, cx, cy, s, w=200, h=70):
        self.d.ellipse([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=INK, width=W_LINE, fill="white")
        self.text((cx, cy), s)

    def rect(self, cx, cy, s, w=260, h=70):
        self.d.rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], outline=INK, width=W_LINE, fill="white")
        self.text((cx, cy), s)

    def para(self, cx, cy, s, w=260, h=70, skew=28):
        pts = [(cx - w / 2 + skew, cy - h / 2), (cx + w / 2 + skew, cy - h / 2),
               (cx + w / 2 - skew, cy + h / 2), (cx - w / 2 - skew, cy + h / 2)]
        self.d.polygon(pts, outline=INK, fill="white", width=W_LINE)
        self.d.line(pts + [pts[0]], fill=INK, width=W_LINE)
        self.text((cx, cy), s)

    def diamond(self, cx, cy, s, w=300, h=150):
        pts = [(cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)]
        self.d.polygon(pts, outline=INK, fill="white", width=W_LINE)
        self.d.line(pts + [pts[0]], fill=INK, width=W_LINE)
        self.text((cx, cy), s)

    def arrow(self, points, label=None, label_at=None, head=True):
        self.d.line(points, fill=INK, width=W_LINE, joint="curve")
        if label:
            (a0, b0), (a1, b1) = points[0], points[1]
            self.text(label_at or ((a0 + a1) / 2 + 30, (b0 + b1) / 2), label, self.small)
        if not head:
            return
        (x0, y0), (x1, y1) = points[-2], points[-1]
        ang = math.atan2(y1 - y0, x1 - x0)
        size = 16
        left = (x1 - size * math.cos(ang - 0.4), y1 - size * math.sin(ang - 0.4))
        right = (x1 - size * math.cos(ang + 0.4), y1 - size * math.sin(ang + 0.4))
        self.d.polygon([(x1, y1), left, right], fill=INK)

    def save(self, name: str):
        self.img.save(OUT / name)


def even_odd():
    c = Canvas(900, 1060)
    cx = 450
    c.oval(cx, 70, "Start")
    c.arrow([(cx, 105), (cx, 170)])
    c.para(cx, 205, "Input n")
    c.arrow([(cx, 240), (cx, 320)])
    c.diamond(cx, 395, "n % 2 == 0 ?", w=340, h=150)
    # Yes -> left, No -> right
    c.arrow([(cx - 170, 395), (230, 395), (230, 520)], "Yes", (255, 368))
    c.arrow([(cx + 170, 395), (670, 395), (670, 520)], "No", (645, 368))
    c.para(230, 555, "Even", w=200)
    c.para(670, 555, "Odd", w=200)
    c.arrow([(230, 590), (230, 800), (cx, 800)], head=False)
    c.arrow([(670, 590), (670, 800), (cx, 800)], head=False)
    c.arrow([(cx, 800), (cx, 880)])
    c.oval(cx, 915, "End")
    c.save("even_odd.png")


def sum_loop():
    c = Canvas(900, 1400)
    cx = 400
    c.oval(cx, 70, "Start")
    c.arrow([(cx, 105), (cx, 160)])
    c.para(cx, 195, "Input n")
    c.arrow([(cx, 230), (cx, 290)])
    c.rect(cx, 325, "sum = 0")
    c.arrow([(cx, 360), (cx, 420)])
    c.rect(cx, 455, "i = 1")
    c.arrow([(cx, 490), (cx, 545)])
    c.diamond(cx, 620, "i <= n ?", w=300, h=150)
    # Yes -> down through loop body
    c.arrow([(cx, 695), (cx, 770)], "Yes", (cx + 40, 730))
    c.rect(cx, 805, "sum = sum + i")
    c.arrow([(cx, 840), (cx, 900)])
    c.rect(cx, 935, "i = i + 1")
    # loop back to the diamond
    c.arrow([(cx + 130, 935), (700, 935), (700, 620), (cx + 150, 620)])
    # No -> left then down
    c.arrow([(cx - 150, 620), (110, 620), (110, 1130), (cx - 130, 1130)], "No", (200, 595))
    c.para(cx + 20, 1130, "Print sum", w=230)
    c.arrow([(cx, 1165), (cx, 1245)])
    c.oval(cx, 1280, "End")
    c.save("sum_loop.png")


def grade_check():
    c = Canvas(900, 1060)
    cx = 450
    c.oval(cx, 70, "Start")
    c.arrow([(cx, 105), (cx, 170)])
    c.para(cx, 205, "Input marks")
    c.arrow([(cx, 240), (cx, 320)])
    c.diamond(cx, 395, "marks >= 40 ?", w=360, h=150)
    c.arrow([(cx - 180, 395), (230, 395), (230, 520)], "Yes", (255, 368))
    c.arrow([(cx + 180, 395), (670, 395), (670, 520)], "No", (645, 368))
    c.para(230, 555, "Print Pass", w=220)
    c.para(670, 555, "Print Fail", w=220)
    c.arrow([(230, 590), (230, 800), (cx, 800)], head=False)
    c.arrow([(670, 590), (670, 800), (cx, 800)], head=False)
    c.arrow([(cx, 800), (cx, 880)])
    c.oval(cx, 915, "End")
    c.save("grade_check.png")


if __name__ == "__main__":
    even_odd()
    sum_loop()
    grade_check()
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
