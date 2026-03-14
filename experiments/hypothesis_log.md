t patterns more heavily to capture tactical adaptation on hard courts where serve direction shifts are the primary variance driver, directly targeting the unexamined temporal weighting in serve placement that masks point-construction signals after 22 consecutive neutral/revert experiments.
  Cal error: 0.112

---
## 2026-03-13 14:52
**20260313-090** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `decay.shot_type_mix.half_life` 24→12: Shot type mix decay is completely untested; aggressive reduction from 24mo to 12mo will weight recent forehand/backhand/slice distributions more heavily to capture tactical adaptation on hard courts where stroke-type selection changes rapidly within matches.
  Cal error: 0.1067

---
## 2026-03-13 14:53
**20260313-091** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `confidence.min_n_shrinkage` 30→50: min_n_shrinkage is completely untested; increasing from 30 to 50 will apply Bayesian shrinkage to more observations, reducing overconfidence on sparse hard-court archetype samples and targeting miscalibration signals that have persisted across 23 neutral/revert experiments.
  Cal error: 0.1067

---
## 2026-03-13 14:56
**20260313-092** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `decay.pressure_win_rate.half_life` 12→6: Pressure win-rate decay is completely untested; aggressive reduction from 12mo to 6mo will weight recent high-pressure performance more heavily to capture match-specific momentum and clutch-point patterns on hard courts where psychological state shifts rapidly.
  Cal error: 0.1067

---
## 2026-03-13 14:57
**20260313-093** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `decay.net_tendency.half_life` 24→12: Net tendency decay is completely untested; aggressive reduction from 24mo to 12mo will weight recent net-approach patterns more heavily to capture tactical adaptation on hard courts where serve-and-volley or approach-shot frequency shifts are rapid and point-construction critical.
  Cal error: 0.1067

---
## 2026-03-13 15:00
**20260313-094** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `decay.error_rate.half_life` 12→6: Error rate decay is completely untested; aggressive reduction from 12mo to 6mo will weight recent unforced-error patterns more heavily to capture fatigue and pressure-induced error variance on hard courts where surface-dependent error spikes are rapid and predictive of break-point vulnerability.
  Cal error: 0.1067

---
## 2026-03-13 15:02
**20260313-095** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `confidence.low_threshold` 10→20: Low threshold is completely untested; increasing from 10 to 20 will raise the confidence gate for sparse archetype samples, reducing false-positive high-confidence predictions that inflate Brier score on hard courts where archetype coverage is uneven.
  Cal error: 0.1067

---
## 2026-03-13 15:03
**20260313-096** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `cluster.rally_weight` 1.0→1.5: Rally pattern weighting in clustering is untested; increasing from 1.0 to 1.5 will amplify rally-structure discrimination to capture point-construction sequences on hard courts where baseline rally patterns are more predictive than serve-dominated archetypes, targeting the calibration plateau after 24 consecutive neutral/revert experiments across decay, window, and confidence.
  Cal error: 0.1067

---
## 2026-03-13 15:04
**20260313-097** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `cluster.k` 7→5: Cluster count is completely untested; reducing from 7 to 5 will merge over-fragmented archetypes and reduce sparse-sample miscalibration on hard courts where archetype diversity may be artificially inflated.
  Cal error: 0.1067

---
## 2026-03-13 15:05
**20260313-098** [REVERT] Brier=0.2529 Δ=+0.0008
  `decay.serve_direction.half_life` 36→18: Serve direction is the only decay parameter untested; aggressive reduction from 36mo to 18mo will weight recent serving patterns more heavily to capture tactical serve placement adaptation on hard courts where first-serve direction against specific opponents shifts rapidly within matches.
  Cal error: 0.112

---
## 2026-03-13 15:07
**20260313-099** [NEUTRAL] Brier=0.2521 Δ=+0.0000
  `decay.shot_type_mix.half_life` 24→12: Shot-type mix decay is the only untested decay parameter; aggressive reduction from 24mo to 12mo will weight recent forehand/backhand/slice distributions more heavily to capture tactical adaptation on hard courts where shot selection variance within pressure sequences is rapid and predictive.
  Cal error: 0.1067
