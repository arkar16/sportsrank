---
status: accepted
---

# Adopt two-timescale Bayesian state-space basketball CORS v1.0.0

SportsRank promotes the locked two-timescale Bayesian state-space Kalman model
(`cbb-kalman-2scale`) as men's Division I basketball **CORS v1.0.0**, replacing
basketball v0.6.0 across the 1980–81 through 2026–27 static archive.

## Context

Basketball v0.6.0 was the opponent-adjusted margin blend selected by the
20-parameter search (configuration `3578`). It inherited the season and forecast
boundaries in [ADR-0028](0028-start-basketball-cors-in-2019-20.md). Four properties
limited its use for a long replay.

1. Its `carry = 1.0261 > 1.0` amplifies the previous season's state before new
   results enter.
2. Its fixed league home-court advantage (`HFA = 3.5`) cannot adapt to changes
   such as the empty arenas in 2020–21.
3. Its schedule filter omits roughly 450–550 Division I vs. non-Division I
   games per season from the rating updates.
4. Its carryover resets teams to `0.0` after a season away, including the Ivy
   League teams that missed 2020–21 and returned in 2021–22.

The locked offline study paired the new model's forecasts with the frozen
baselines on the same games. The promotion brings basketball to architectural
and predictive parity with football CORS v1.0.0 by using the shared state-space
approach and requiring a predictive improvement on held-out games.
[Basketball experiment #100](https://github.com/arkar16/sportsrank/issues/100) ·
[ADR-0029](0029-adopt-kalman-two-scale-football-cors-v1.md)

## Decision

Freeze `cbb-kalman-2scale` in `cbb/cors_v1.py` as basketball **CORS v1.0.0**
(`cbb-kalman-2scale/v1`). Use it for the weekly rankings, reconstructed spreads,
final standings, honors, team histories, and model performance in the static
basketball record book.

### Frozen specification

Ratings are unbounded and expressed directly in **expected neutral-court points
above or below an average Division I team (`0.00`)** in that season.

- **Preseason carryover** (`carry_total = 0.96`, `w_slow = 0.45`,
  `alpha_slow = 0.22`, `rho_var = 0.80`, `sigma_off = 3.8`)
  - For a team returning from the previous season (`gap = 1`), use
    `mu = 0.96 * (0.55 * prev_fast + 0.45 * prev_slow)`,
    `var = 0.80^2 * prev_var + 3.8^2`.
  - For a team returning after a multi-year absence (`gap > 1`, capped at `3`),
    use `mu = (0.96 ** min(gap, 3)) * prev_slow`,
    `var = min(100.0, prev_var + min(gap, 3) * 3.8^2)`. The slow state
    persists through the hiatus, as for the Ivy League's 2021–22 return.
  - First-time Division I Classification Entrant uses `entrant_prior = -12.0`
    and `var = 100.0`. At genesis, all teams start at `0.0` with `var = 100.0`.
  - Zero-center preseason means and published ratings across the season's
    Division I membership. At season end, store each centered final rating
    as the fast state and update the slow state with `alpha_slow = 0.22`.
    A team's first final rating initializes both states.
- **In-season weekly updates** (`sigma_wk = 0.55`, `sigma_g_base = 11.0`,
  `nu = 14.0`)
  - Use the Monday 06:00 Eastern checkpoint strictly before tipoff for each
    forecast. A final contributes once its tipoff plus the six-hour completion
    buffer is strictly before the checkpoint. Date-only archive games use
    their Eastern calendar week.
  - Add `sigma_wk^2` to Division I diagonal variances before an update batch.
    Use an exact block-weekly precision (Kalman) update. The Student-t scale
    is `sigma_g_base * sqrt((nu - 2) / nu)` to match the target variance.
    Compute each weight `(nu + 1) / (nu + z^2)` from the same pre-update state
    and predictive variance. The fixed weights make the batch posterior
    invariant to within-week game order.
  - Forecast the home margin as `mu[home] - mu[away] + site`, rounded to
    hundredths. Use `site = 0` on neutral courts and the checkpoint's league
    plus home-team advantage elsewhere.
- **Latent `Non-D1` tier** (`sigma_nond1 = 21.0`)
  - Initialize the shared tier at `-26.0` with variance `25.0` and add `9.0`
    offseason variance drift. Use the same variance-matched Student-t rule
    for available Division I vs. non-Division I finals.
  - Adjust those margins using the pre-update home-court advantage. Keep
    Division I vs. Division I record counts and forecast coverage unchanged.
- **Adaptive home-court advantage** (`sigma_hfa_off = 0.40`,
  `sigma_hfa_wk = 0.08`, `hfa_shrink = 40.0`, `hfa_team_mem = 0.95`)
  - Initialize the Bayesian `HFA_league` node at `3.6` points with variance
    `0.5^2`. It carries between seasons with `sigma_hfa_off^2` added to its
    variance. When active, add `sigma_hfa_wk^2` before the weekly update and
    estimate the node jointly from non-neutral Division I games. Track A
    enables this update starting with the 2008 season. The historical archive
    enables it at the 1980–81 genesis. The Track A estimate reached `2.46`
    points in the 2020–21 empty-arena season.
  - Compute team-specific home-court effects once per season from prior-season
    non-neutral Division I forecast residuals measured before the team effect.
    Downweight the retained sums and counts each year by `hfa_team_mem`. Take
    half the difference of the mean home and away residuals from the team's
    perspective, shrink it by
    `n_eff / (n_eff + hfa_shrink)`, where `n_eff` is the smaller of the weighted
    home and away counts, and zero-center across teams with both counts.
- **Replay initialization**
  - The modern archive (seasons ending 2020–27) starts from
    `cbb/cors_v1_seed.json`, the end-of-2019 state warmed up with 2002–19
    `hoopR` results as in Track A.
  - The historical archive (seasons ending 1981–2019) replays from the
    1980–81 genesis. The same `carry_total = 0.96` shrinks prior ratings
    between seasons throughout the replay.

## Evidence

The retained offline study is in `.sportsrank/cbb-kalman-experiment/results.json`.
Its companion `run_experiment.py` records the grid, reference scores, bootstrap,
and invariant checks. Years below refer to season ending years. Confidence
intervals use 5,000 paired weekly block bootstrap draws stratified by season.

- **Strict 2010–16 lock (38,163 games).** The pre-specified 24-point grid
  selected **10.7971** RMSE and **74.17%** straight-up accuracy before any
  held-out evaluation. The reference RMSEs were `10.8323` for `3578` and
  `10.8375` for `4605`.
- **Unseen 2017–19 (16,493 games).** RMSE **11.2833** vs. `11.3257` for
  `3578` (`-0.0424`, 95% CI `[-0.0596, -0.0250]`, `P(<0) = 1.000`) and
  `13.2509` for v0.4.0.
- **Unseen 2022–26 (28,432 games).** RMSE **11.4256** vs. `11.4745` for
  `3578` (`-0.0489`, 95% CI `[-0.0674, -0.0315]`, `P(<0) = 1.000`) and
  `13.3157` for v0.4.0.
- **Pooled unseen 2017–26 (54,197 games).** RMSE **11.3971**, MAE **8.9739**,
  and **72.47%** straight-up accuracy. Compared with `3578`, the model picked
  `87` more winners and had lower RMSE in nine of ten seasons. The pooled
  RMSE difference was `-0.0462` (95% CI `[-0.0583, -0.0345]`,
  `P(<0) = 1.000`).
- **Track B cold start from 2019–20.** Without `hoopR` warmup, 2022–26 RMSE
  was **11.4553**, a difference of `-0.0597` points against cold-start `3578`
  (`11.5150`). It also beat `3578` with the 2002–19 warmup (`11.4745`).
- **March Madness 2022–26 (335 games).** RMSE **11.6112** with `242/335`
  winners (**72.24%**), vs. `11.7896` with `240/335` winners for `3578`
  and `14.8112` RMSE for v0.4.0.
- **Prospective Nov. 19–23, 2026 ledger (153 games).** On the ledger's games,
  reconstructed RMSE was **13.2489** with a 2002 start and **13.2150** with
  a 2020 start, vs. `13.4827` for `3578` and `16.7659` for issued v0.4.0.
- **Ablations and invariants.** Against the pooled 2017–26 RMSE of `11.3971`,
  removing two-timescale carryover raised RMSE to `11.4512` (`+0.0541`),
  removing team HFA raised it to `11.4100` (`+0.0129`), and removing the
  `Non-D1` node raised it to `11.4072` (`+0.0101`). These differences use
  the displayed four-decimal scores. The Division I and `Non-D1` score
  perturbations passed the cutoff checks. Shuffling the 2024 observation
  batches produced a maximum forecast difference of `0.00e+00` points.

## Consequences

- `cbb/replay.py` and `cbb/blend.py` remain intact for historical v0.4.0 and
  v0.6.0 contract tests respectively.
- The rebuild covers all 47 CORS seasons from 1980–81 through 2026–27. Historical
  public URLs and the issued editions at
  `website/cbb/editions/2026-11-19T185800Z.html` and
  `website/cbb/editions/2026-11-23T161743Z.html` are preserved.
