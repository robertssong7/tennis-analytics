"""
Headless-browser mobile audit. Renders each public page at iPhone SE,
iPhone Pro Max, and iPad portrait viewports. For each:
  - Saves a full-page screenshot to _session12_artifacts/mobile_*.png
  - Checks for horizontal scroll
  - Lists elements whose right edge overflows the viewport
  - Lists touch targets smaller than 44x44px

Run after deploy when checking prod, or against localhost for pre-deploy
verification (set TENNISIQ_BASE env var).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = os.environ.get("TENNISIQ_BASE", "https://tennisiq-one.vercel.app")
ARTIFACTS = Path(__file__).parent.parent / "_session12_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

PAGES = [
    "/",
    "/player.html?name=Sinner",
    "/player.html?name=Nadal",
    "/compare.html",
    "/tournament.html",
    "/methodology.html",
    "/status.html",
]
VIEWPORTS = [
    {"name": "iPhone-SE", "width": 375, "height": 667},
    {"name": "iPhone-Pro-Max", "width": 430, "height": 932},
    {"name": "iPad", "width": 768, "height": 1024},
]


async def audit():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        results = []
        for vp in VIEWPORTS:
            for path in PAGES:
                url = BASE + path
                ctx = await browser.new_context(viewport={"width": vp["width"], "height": vp["height"]})
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=25000)
                except Exception as e:
                    print(f"  [load-fail] {url} on {vp['name']}: {e}")
                    await ctx.close()
                    continue
                slug = path.replace("/", "_").replace("?", "_").replace("=", "_") or "home"
                shot = ARTIFACTS / f"mobile_{vp['name']}_{slug.strip('_') or 'home'}.png"
                try:
                    await page.screenshot(path=str(shot), full_page=True)
                except Exception:
                    pass

                has_h_scroll = await page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
                )
                overflow = await page.evaluate(f"""
                    Array.from(document.querySelectorAll('body *')).filter(el => {{
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.right > {vp['width']} + 5;
                    }}).map(el => el.tagName + '.' + (el.className || '').toString().slice(0, 30) + ' w=' + Math.round(el.getBoundingClientRect().width)).slice(0, 5)
                """)
                small_tap = await page.evaluate("""
                    Array.from(document.querySelectorAll('a, button, input, select')).filter(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) return false;
                        return r.width < 44 || r.height < 44;
                    }).map(el => el.tagName + ' "' + (el.textContent || '').slice(0,20).trim() + '"').slice(0, 5)
                """)
                msg = f"{vp['name']:<16} {path:<40} h_scroll={has_h_scroll:5} overflow={len(overflow)} small_tap={len(small_tap)}"
                print(msg)
                if overflow:
                    print(f"   overflow: {overflow}")
                if small_tap:
                    print(f"   small_tap: {small_tap}")
                results.append({
                    "viewport": vp["name"], "path": path,
                    "h_scroll": has_h_scroll,
                    "overflow_count": len(overflow), "small_tap_count": len(small_tap),
                    "overflow_examples": overflow,
                    "small_tap_examples": small_tap,
                })
                await ctx.close()
        await browser.close()

        import json
        (ARTIFACTS / "mobile_audit_results.json").write_text(json.dumps(results, indent=2))
        # Aggregate fail count
        fail = sum(1 for r in results if r["h_scroll"] or r["overflow_count"])
        print()
        print(f"Total page-viewport pairs: {len(results)}")
        print(f"Failures (h_scroll or overflow): {fail}")
        return fail


if __name__ == "__main__":
    fail = asyncio.run(audit())
    sys.exit(0 if fail == 0 else 1)
