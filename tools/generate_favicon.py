"""
Generate favicon.ico and apple-touch-icon.png for TennisIQ.
Letter T in white on the brand teal #0ABAB5.
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).parent.parent
DEST_DIR = BASE / "frontend" / "public" / "dashboard"

ACCENT = (10, 186, 181)
WHITE = (255, 255, 255)


def _font(size: int):
    candidates = [
        str(Path.home() / "Library/Fonts/PlayfairDisplay-Bold.ttf"),
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/Library/Fonts/Georgia.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (10, 186, 181, 255))
    draw = ImageDraw.Draw(img)
    # Letter T centered. Pick font size as ~70% of image
    fsize = int(size * 0.7)
    f = _font(fsize)
    text = "T"
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # Manually adjust for font baseline (textbbox returns top-left of ink)
    cx = (size - tw) // 2 - bbox[0]
    cy = (size - th) // 2 - bbox[1]
    draw.text((cx, cy), text, fill=WHITE, font=f)
    return img


def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    # Favicon (multi-size .ico)
    icon_sizes = [(16, 16), (32, 32), (48, 48)]
    icon = _make(48)
    icon.save(DEST_DIR / "favicon.ico", format="ICO", sizes=icon_sizes)
    print(f"Wrote {DEST_DIR / 'favicon.ico'}")

    # Apple touch icon (180x180)
    apple = _make(180)
    apple.convert("RGB").save(DEST_DIR / "apple-touch-icon.png", format="PNG", optimize=True)
    print(f"Wrote {DEST_DIR / 'apple-touch-icon.png'}")


if __name__ == "__main__":
    main()
