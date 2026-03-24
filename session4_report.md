# TennisIQ Session 4 Report
**Date:** 2026-03-23

## Summary
Post-pipeline fixes: expanded headshot mapping (82 players, 37 cached), guaranteed 5 active matchups via on-the-fly predictions, surface-specific conditions/matchups/scenarios, split tournament feed, tournament win probability predictions, surface toggle now updates all page sections, carousel dots fixed with inline styles.

## Phase 1: Backend
- **Headshot expansion**: 82 players mapped (was 33). 37 headshots cached to disk. ATP scraping blocked (manual codes added).
- **5-active matchups**: On-the-fly prediction generation when precomputed grid lacks enough active players. Sinner: T:5 E:5.
- **Surface conditions**: ?surface= parameter on /player/{name}/conditions filters match data by surface.
- **Split tournament feed**: /api/live-tournament returns {finished: BNP Paribas Open, current: Miami Open}.
- **Tournament predictions**: /api/tournament-predictions returns favorites (Sinner 48.9%, Alcaraz 19.1%) and dark horses with reasons.

## Phase 2: Player Profile
- **Surface toggle**: Now re-fetches conditions, matchups, AND scenarios with ?surface= parameter. Fade transition during loading.
- **Surface note**: "Surface-specific conditions and matchups" shown when a specific surface is selected.

## Phase 3: Homepage
- **Split tournament feed**: 2-column layout — "Just Finished" + "Live Now/Up Next".
- **Tournament predictions**: Favorites (Tiffany blue) + Dark Horses (Gold) cards with win probability bars and reasons.
- **Carousel dots**: Nuclear rewrite with inline styles — all 5 dots now enlarge and fill correctly.

## Checklist
| Request | Status | Notes |
|---------|--------|-------|
| Headshot codes for 50+ players | Done | 82 mapped, 37 cached |
| Surface toggle changes ALL sections | Done | Conditions, matchups, scenarios all re-fetch |
| 5 toughest + 5 easiest always | Done | On-the-fly backfill from Glicko roster |
| Split tournament feed (finished + current) | Done | BNP Paribas + Miami Open |
| Tournament win probabilities | Done | 5 favorites + 5 dark horses |
| Carousel dots work for all 5 players | Done | Inline styles, no CSS class issues |
| Data reflects 2025-2026 matches | Done | Sinner: last_match=2026-03-20, 472W |

## Git Commits
- `4089d694` Phase 1: Headshot expansion, 5-active matchups, surface conditions, split tournament, predictions
- `[hash]` Phase 2: Surface toggle updates all sections
- `a8b61caf` Phase 3: Homepage — split tournament feed, favorites/dark horses, carousel dots fix
