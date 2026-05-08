"""
Generate the Open Graph card image used by social-media link previews.
1200x630 PNG, brand-aligned (cream background, teal accent, Playfair title,
DM Sans subtitle, three numeric callouts at the bottom).

Output: frontend/public/dashboard/og-image.png

Tries to use Playfair Display + DM Sans if installed locally; falls back
to a serif/sans-serif system font.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).parent.parent
OUTPUT = BASE / "frontend" / "public" / "dashboard" / "og-image.png"

W, H = 1200, 630
BG = (245, 240, 235)        # #F5F0EB
INK = (44, 44, 44)           # #2C2C2C
ACCENT = (10, 186, 181)      # #0ABAB5

FONT_CANDIDATES_SERIF = [
    "/Library/Fonts/Playfair Display.ttc",
    "/System/Library/Fonts/Supplemental/PlayfairDisplay.ttc",
    str(Path.home() / "Library/Fonts/PlayfairDisplay-Bold.ttf"),
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
]
FONT_CANDIDATES_SANS = [
    str(Path.home() / "Library/Fonts/DMSans-Medium.ttf"),
    str(Path.home() / "Library/Fonts/DMSans-Regular.ttf"),
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial.ttf",
]
FONT_CANDIDATES_MONO = [
    str(Path.home() / "Library/Fonts/DMMono-Medium.ttf"),
    str(Path.home() / "Library/Fonts/DMMono-Regular.ttf"),
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Courier New.ttf",
]


def _load_font(candidates, size):
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def main():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    title_font = _load_font(FONT_CANDIDATES_SERIF, 124)
    subtitle_font = _load_font(FONT_CANDIDATES_SANS, 34)
    stat_value_font = _load_font(FONT_CANDIDATES_SERIF, 54)
    stat_label_font = _load_font(FONT_CANDIDATES_MONO, 16)

    # Top accent bar
    draw.rectangle([(0, 0), (W, 6)], fill=ACCENT)

    # Title centered
    title = "TennisIQ"
    tw, th = _text_size(draw, title, title_font)
    draw.text(((W - tw) / 2, 150), title, fill=INK, font=title_font)

    # Subtitle
    subtitle = "ATP Analytics. Predictions. Player DNA."
    sw, sh = _text_size(draw, subtitle, subtitle_font)
    draw.text(((W - sw) / 2, 308), subtitle, fill=ACCENT, font=subtitle_font)

    # Three stat callouts at the bottom, evenly spaced
    stats = [
        ("919K", "matches"),
        ("17", "percentile stats"),
        ("0.184", "Brier score"),
    ]
    col_w = W // 3
    base_y = 470
    for i, (val, label) in enumerate(stats):
        cx = i * col_w + col_w // 2
        vw, _ = _text_size(draw, val, stat_value_font)
        draw.text((cx - vw / 2, base_y), val, fill=INK, font=stat_value_font)
        lw, _ = _text_size(draw, label.upper(), stat_label_font)
        draw.text((cx - lw / 2, base_y + 70), label.upper(), fill=(122, 138, 150), font=stat_label_font)

    # Footer URL
    url_font = _load_font(FONT_CANDIDATES_MONO, 18)
    url = "tennisiq-one.vercel.app"
    uw, _ = _text_size(draw, url, url_font)
    draw.text(((W - uw) / 2, H - 50), url, fill=(122, 138, 150), font=url_font)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, format="PNG", optimize=True)
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    sys.exit(main() or 0)
