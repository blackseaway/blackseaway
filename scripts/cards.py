"""Social image cards (1080×1350, Instagram portrait) rendered with Pillow."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import ROOT

FONT_DIR = ROOT / "assets" / "fonts"
W, H = 1080, 1350
NAVY = (0, 20, 47)
NAVY_2 = (11, 45, 85)
SEA = (56, 152, 214)
ALERT = (230, 57, 70)
WHITE = (255, 255, 255)
GOLD = (205, 154, 89)
ASSETS = ROOT / "assets"
MUTED = (170, 195, 215)


def font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def wrap(draw, text, fnt, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=fnt) <= width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _background(img_w=W, img_h=H):
    img = Image.new("RGB", (img_w, img_h), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(img_h):  # vertical gradient
        t = y / img_h
        c = tuple(int(NAVY[i] * (1 - t) + NAVY_2[i] * t) for i in range(3))
        d.line([(0, y), (img_w, y)], fill=c)
    # waves at the bottom
    for k, alpha in enumerate((60, 40, 25)):
        pts = []
        base = img_h - 150 + k * 38
        for x in range(0, img_w + 20, 20):
            pts.append((x, base + 18 * math.sin(x / 90 + k * 1.7)))
        pts += [(img_w, img_h), (0, img_h)]
        layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(pts, fill=SEA + (alpha,))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    return img


def news_card(path: Path, headline: str, label: str, footer: str, alert: bool = False) -> Path:
    img = _background()
    d = ImageDraw.Draw(img)
    pad = 84
    mark = Image.open(ASSETS / "logo-mark.png").convert("RGB")
    mark = mark.resize((int(mark.width * 110 / mark.height), 110), Image.LANCZOS)
    img.paste(mark, (pad - 10, 50))
    d.text((pad + mark.width + 6, 82), "BLACK", font=font(40, True), fill=WHITE)
    x = pad + mark.width + 6 + d.textlength("BLACK ", font=font(40, True))
    d.text((x, 82), "SEA", font=font(40, True), fill=GOLD)
    x += d.textlength("SEA ", font=font(40, True))
    d.text((x, 82), "WAY", font=font(40, True), fill=WHITE)
    # topic pill
    f_label = font(34, True)
    lw = d.textlength(label.upper(), font=f_label)
    d.rounded_rectangle([pad, 220, pad + lw + 48, 282], radius=31, fill=ALERT if alert else SEA)
    d.text((pad + 24, 230), label.upper(), font=f_label, fill=WHITE)
    # headline: shrink until it fits
    for size in (78, 70, 62, 56, 50, 44):
        f = font(size, True)
        lines = wrap(d, headline, f, W - 2 * pad)
        if len(lines) * size * 1.22 <= 720:
            break
    y = 340
    for ln in lines[:12]:
        d.text((pad, y), ln, font=f, fill=WHITE)
        y += int(size * 1.22)
    d.text((pad, H - 250), footer, font=font(32), fill=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=90, optimize=True)  # Instagram API accepts JPEG only
    return path


def og_card(path: Path, title: str, subtitle: str) -> Path:
    """1200×630 Open Graph image for link previews: the logo on brand navy."""
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), NAVY)
    logo = Image.open(ASSETS / "logo-full.jpg").convert("RGB").resize((600, 600), Image.LANCZOS)
    img.paste(logo, ((w - 600) // 2, 15))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path
