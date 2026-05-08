"""
Expand headshot coverage by trying multiple sources for each player without
a cached PNG. Order: Wikipedia (most reliable), Tennis Explorer, ATP CDN.

Outputs:
  - PNG files at data/processed/headshots/<code>.png (lowercase code)
  - Updates data/player_headshots.json with the canonical name -> URL mapping
    using the same atptour.com/-/media/alias path convention so the API
    proxy can find them via /api/player-image/<code>

Codes: when no canonical ATP code exists, we synthesize one from the player's
name (first letter of last name + 3-char hash suffix) so the proxy works.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

BASE = Path(__file__).parent.parent
HEADSHOTS_DIR = BASE / "data" / "processed" / "headshots"
HEADSHOTS_JSON = BASE / "data" / "player_headshots.json"
TARGETS_FILE = BASE / "_session12_artifacts" / "headshot_targets.json"

# Wikimedia requires a meaningful User-Agent with contact info per
# https://meta.wikimedia.org/wiki/User-Agent_policy. Generic browser strings
# get aggressively rate-limited. This UA is per their guidance.
UA = "TennisIQ-headshot-scraper/1.0 (https://github.com/robertssong7/tennis-analytics; tennisiq-bot@users.noreply.github.com) python-requests"
HEADERS = {
    "User-Agent": UA,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _slugify_for_code(name: str) -> str:
    """Synthetic code: 1 letter + 3-char hash suffix. Matches our regex."""
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:3]
    last = name.split()[-1].lower()
    first = re.sub(r"[^a-z]", "", last)[0:1] or "x"
    return first + h


def _save_png(content: bytes, dest: Path) -> bool:
    """Re-encode to a clean 300x300 PNG. Returns True on success."""
    try:
        img = Image.open(io.BytesIO(content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Resize to a square fitting 300x300
        img.thumbnail((600, 600))
        # Pad to square if needed
        w, h = img.size
        if w != h:
            side = max(w, h)
            new = Image.new("RGB", (side, side), (245, 240, 235))
            new.paste(img, ((side - w) // 2, (side - h) // 2))
            img = new
        img = img.resize((300, 300), Image.LANCZOS)
        img.save(dest, format="PNG", optimize=True)
        return dest.stat().st_size > 5_000
    except Exception as e:
        print(f"    [save-err] {e}")
        return False


def try_wikipedia(player_name: str):
    """Use Wikipedia REST API to find the player's infobox thumbnail."""
    candidates = [
        player_name.replace(" ", "_"),
        player_name.replace(" ", "_") + "_(tennis)",
        player_name.replace(" ", "_") + "_(tennis_player)",
    ]
    for slug in candidates:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            # Make sure it's actually a tennis player page
            desc = (data.get("description") or "").lower()
            extract = (data.get("extract") or "").lower()
            if "tennis" not in (desc + " " + extract):
                continue
            thumb = data.get("originalimage", {}).get("source") or data.get("thumbnail", {}).get("source")
            if not thumb:
                continue
            img = requests.get(thumb, headers=HEADERS, timeout=12)
            if img.status_code == 200 and len(img.content) > 5000:
                return img.content
        except Exception as e:
            print(f"    [wiki-err {slug}] {e}")
            continue
    return None


def main():
    if not TARGETS_FILE.exists():
        print(f"Target list missing at {TARGETS_FILE}. Run the build step in Phase D first.")
        return 1
    targets = json.loads(TARGETS_FILE.read_text())
    headshot_map = json.loads(HEADSHOTS_JSON.read_text())
    HEADSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    pulled = 0
    skipped = 0
    failed = []
    for i, name in enumerate(targets, 1):
        if name in headshot_map:
            existing_url = headshot_map[name]
            existing_code = existing_url.rsplit("/", 1)[-1].lower()
            if (HEADSHOTS_DIR / f"{existing_code}.png").exists():
                skipped += 1
                continue

        code = _slugify_for_code(name)
        dest = HEADSHOTS_DIR / f"{code}.png"
        if dest.exists() and dest.stat().st_size > 5000:
            headshot_map.setdefault(
                name,
                f"https://www.atptour.com/en/-/media/alias/player-headshot/{code}",
            )
            skipped += 1
            continue

        print(f"[{i}/{len(targets)}] {name}")
        # Source 1: Wikipedia
        content = try_wikipedia(name)
        if content and _save_png(content, dest):
            print(f"    [wiki ok] {dest.stat().st_size:,}B -> {code}.png")
            headshot_map[name] = (
                f"https://www.atptour.com/en/-/media/alias/player-headshot/{code}"
            )
            pulled += 1
            time.sleep(2.0)  # Wikimedia rate limits aggressively
            continue

        # Source 2 (best effort): ATP origin still tried — usually fails on Cloudflare
        atp_code = code  # If we had the real ATP code we'd use it; this is a fallback
        atp_url = f"https://www.atptour.com/-/media/alias/player-headshot/{atp_code}"
        try:
            r = requests.get(atp_url, headers=HEADERS, timeout=8)
            if r.status_code == 200 and "image" in r.headers.get("content-type", "") and _save_png(r.content, dest):
                print(f"    [atp ok] {dest.stat().st_size:,}B")
                headshot_map[name] = (
                    f"https://www.atptour.com/en/-/media/alias/player-headshot/{atp_code}"
                )
                pulled += 1
                time.sleep(1.0)
                continue
        except Exception:
            pass

        failed.append(name)
        time.sleep(0.5)

    HEADSHOTS_JSON.write_text(json.dumps(headshot_map, indent=2, ensure_ascii=False))
    print()
    print(f"Pulled: {pulled}  Skipped (already had): {skipped}  Failed: {len(failed)}")
    if failed:
        print("Failed players:")
        for n in failed:
            print(f"  - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
