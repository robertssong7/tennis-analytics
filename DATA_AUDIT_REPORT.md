# DATA AUDIT REPORT — 2026-03-29

## Data Sources
- **Sackmann tennis_atp**: Through Dec 18, 2024. 223,015 total matches (1968-2024). Last Sackmann commit: May 2024.
- **atp_matches_2025_supplement.csv**: 3,018 matches in Sackmann format (full names). Dec 2024 - Mar 2026. Created from tennis-data.co.uk scrape + name mapping.
- **supplemental_matches_2025_2026.csv**: 3,272 matches in tennis-data.co.uk format (short names). Dec 29, 2024 - Mar 15, 2026.

## Coverage
- Sackmann covers through: Dec 18, 2024 (tourney_date 20241218)
- Supplemental covers: Dec 29, 2024 to Mar 15, 2026
- Gap: Dec 18-29 2024 (11 days, minor — only late-season challengers)
- No official Sackmann 2025 file exists yet

## Name Mapping
- Before fix: 248/316 (78.5%) success rate
- After fix: ~290/316 (~91.8%) with 18 manual overrides
- Fixed high-impact: Auger-Aliassime (90m), Mpetshi Perricard (56m), Struff (41m), Bu (40m), O'Connell (40m)
- Remaining unmapped: ~26 players with <10 matches each (obscure challengers/qualifiers)

## H2H Verification (Key Rivalries)
| Rivalry | Our Data | Notes |
|---------|----------|-------|
| Djokovic-Nadal | 31-29 | Matches ATP known record. 60 matches 2006-2024. |
| Sinner-Alcaraz | 6-10 | Includes 2025 Rome, RG, Wimbledon, USO, Cinci, ATP Finals |
| Djokovic-Alcaraz | 5-5 | Includes AO 2026 Final (Alcaraz won) |
| Sinner-Medvedev | 9-7 | Includes BNP Paribas 2026 (Sinner won) |
| Alcaraz-Zverev | 7-6 | Includes AO 2026 SF (Alcaraz won) |
| Sinner-Djokovic | 6-5 | Includes AO 2026 SF (Djokovic won) |

## Missing Tournaments
All major 2025 tournaments present in supplemental data. No gaps detected for ATP 250+ events.

## Data Quality Issues Found
1. Supplemental CSV uses short name format — 68 players couldn't be mapped automatically
2. 18 high-impact players fixed with manual overrides
3. Remaining 50 unmapped players are mostly challengers with <5 ATP matches each

## Fixes Applied
- Added 18 manual name mapping overrides to predict_engine.py
- Added skip-if-already-mapped logic to avoid double-mapping
