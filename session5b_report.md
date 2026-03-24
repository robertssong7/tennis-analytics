# TennisIQ Session 5B Report
**Date:** 2026-03-24

## Summary
Targeted fixes: draw-based tournament predictions, surface-specific charted attributes in compare page, unique featured player data, homepage layout improvements, tournament CPI coloring.

## Checklist

| Request | Status | Notes |
|---------|--------|-------|
| Left text shifted right toward featured player | Done | Padding removed on both sides |
| Divider line matches text width | Done | max-width:440px on hero-stats |
| Key strengths above toughest profiles | Done | Swapped order in buildC |
| Unique toughest/strengths per featured player | Done | Relative ranking vs averages |
| Tournament feed 1/3 + 2/3 layout | Done | grid-template-columns: 1fr 2fr |
| Reduced gap below Tournament Feed heading | Done | margin cut 50% |
| ML predictions: no "Win Probabilities" in title | Done | Shows just tournament name |
| Only draw players in predictions | Done | 88 of 96 Miami players mapped |
| Dark horses: projected round instead of % | Done | win_prob removed from response |
| Dark horses expandable with 3 reasons | Done | Click expands ul list |
| Compare: charted attributes change per surface | Done | Fetches patterns?surface= for both players |
| Player: patterns change per surface | Done | Already working (verified) |
| Headshot cache cleared | Done | 81 PNGs deleted, will re-cache |
| Tournament legend: no white banner, larger | Done | Transparent bg, 20px dots, 12.5px text |
| Surface summary CPI numbers colored | Done | Speed-category colored badges |

## Git Commits (Session 5B)
- `9824ae59` Phase 1: Clear headshot cache, draw-based predictions, remove dark horse win_prob
- `f58da640` Phase 2: Homepage — text spacing, strengths first, 1/3+2/3 layout, predictions fixes
- `6dc23db6` Phase 3: Compare + Player — surface-specific charted attributes
- `98f7c644` Phase 4: Tournament — transparent legend, colored CPI numbers
