---
status: accepted
---

# Adopt two-timescale Bayesian state-space football CORS v1.0.0

SportsRank promotes the locked two-timescale Bayesian state-space multi-tier
Kalman model (`kalman-2scale (+Other)`) as college football **CORS v1.0.0**,
replacing football v0.4.0 across the full 1897–2026 static archive.

## Context

In the November 2026 model study ([football experiment #102](https://github.com/arkar16/sportsrank/issues/102)),
opponent-adjusted margin models reduced 2024–25 spread RMSE from **18.312**
(football v0.4.0) to **15.662** (`0/fcs`). An independent pre-adoption audit
identified two structural defects in unmodified `0/fcs` for a 130-year replay:

1. Its `carry = 1.1` offseason carryover diverges exponentially when replayed
   from 1897 (reaching 73.95 RMSE in 2017–26).
2. Its three-band step-function spread scaling (`stage = 1`) causes teams on a
   bye week to jump by 3–4 points between Week 8 and Week 9 without playing.

A locked 3-way offline experiment selected all hyperparameters strictly on
2010–16 before touching 2017–26, comparing `0/fcs`, a repaired `0/fcs-clean`,
and `kalman-2scale (+Other)`.

## Decision

Freeze `kalman-2scale (+Other)` in `cfb/cors_v1.py` as college football
**CORS v1.0.0** (`cors-kalman-2scale/v1`) and replay the entire football archive
chronologically from 1897 through the active 2026 season (including weekly
rankings, final standings, national champions, worst teams, team histories,
conference histories, model performance reports, and weekly spreads through the
current upcoming slate).

### Frozen specification

Ratings are unbounded and expressed directly in **expected neutral-field points
above or below an average FBS team (`0.00`)** in that season.

- **Preseason carryover (`w_slow = 0.50`, `carry_total = 0.96`, `sigma_off = 7.5`, `alpha_slow = 0.28`):**
  - Returning consecutive-season team (`gap = 1`):
    `mu = 0.96 * (0.50 * prev_fast + 0.50 * prev_slow)`,
    `var = 0.85^2 * prev_var + 7.5^2`.
  - Returning after a multi-year absence (`gap > 1`, capped at `3`):
    `mu = (0.96 ** min(gap, 3)) * prev_slow`,
    `var = min(144.0, prev_var + min(gap, 3) * 7.5^2)`.
  - First-time FBS Classification Entrant (`year > 1897`):
    `mu = -10.0`, `var = 144.0`. Genesis season (`1897`) starts all teams at
    `0.0`, `var = 144.0`.
  - Zero-center the FBS mean vector across current-season members before Week 0.
- **In-season weekly updates (`sigma_wk = 1.3`, `nu = 14.0`, `sigma_g = 15.0`):**
  - Generate Week `w` spreads strictly from pre-week `(mu, P)` and season
    `hfa_est`: `home_margin = round_half_away(mu[home] - mu[away] + site, 2)`
    where `site = 0` on neutral fields and `hfa_est` otherwise.
  - Add `sigma_wk^2` to FBS diagonal variances for `week > 0`.
  - Assimilate completed Week `w` FBS–FBS games (`sigma_g = 15.0`), explicit
    FBS–FCS games from `2003` onward (`sigma_fcs = 22.0`, latent node
    `FCS`), and non-FBS / pre-2003 FCS games (`sigma_other = 24.0`, latent node
    `Other`).
  - Compute Student-t observation weights `(nu + 1) / (nu + z^2)` strictly from
    pre-week `(mu, P)` so the batch posterior is invariant to within-week game
    order.
- **Latent tiers and adaptive league HFA (`hfa_memory = 0.65`):**
  - Latent tier nodes initialize at `fcs_mean = -26.0` (`var = 25.0`) and
    `other_mean = -22.0` (`var = 49.0`) with `+16.0` offseason variance drift.
  - League HFA starts at `3.0` points and updates at the end of each season
    (`year > 1897`) from strictly pre-week neutral-adjusted non-neutral
    FBS–FBS residuals using exponentially weighted memory `0.65`.

## Evidence

- **Out-of-sample 2017–26 (7,495 FBS–FBS games, 2002 start):**
  - RMSE **15.9766** vs `16.0630` for `0/fcs` (`-0.0864`, 95% weekly block
    bootstrap CI `[-0.1498, -0.0288]`, `P(<0) = 0.999`), beating `0/fcs` in
    9 of 10 seasons and closing 14.5% of the remaining gap to closing market
    lines.
  - Neutral-field higher-rated winner accuracy improved by `+21` wins.
  - Bye-week rating jump (2024 W8→W9 for `|r| > 15`) dropped from `3.27` pts
    mean (`4.08` max) under `0/native` to `0.44` pts mean (`0.88` max).
- **Full 1897–2026 replay (130 seasons, 65,128 FBS–FBS games):**
  - Beats stable `0/fcs-clean (carry=1.0)` in all 8 historical eras
    (**15.6974** vs `15.8710` RMSE, **74.30%** vs `73.87%` straight-up, `+279`
    winners).
  - Start-year invariance: replaying from 1897 vs 2002 shifts 2017–26 RMSE by
    only `+0.0011` (`mean |Δspread| = 0.023` pts).

## Consequences

- `cfb/ranking_engine.py` remains intact for historical v0.4.0 contract tests.
- Historical public URLs and retained 2026 `forecasts/` issued-edition ledgers
  remain preserved; reader-facing rankings, spreads (including the current
  week), performance reports, honors, team pages, conference pages, and
  homepages are rebuilt under CORS v1.0.0 (`python -m cfb.cors_v1`).
- Individual FCS team rankings remain out of scope until FCS-vs-FCS schedules
  are ingested; the Kalman state-space graph accommodates them natively on the
  same Comparable Scale (ADR-0005) when data is added.
