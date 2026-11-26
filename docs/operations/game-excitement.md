# Game excitement scores

The football record-book build reads pregame CORS checkpoints and the reviewed
CollegeFootballData evidence in `cfb/cfbd_excitement`. Each result shows its
evidence level. A scheduled game shows BEV. A retained final that corrects a
previous score requires requalification against its scoring plays.

## Build and verify football pages

```sh
env -u CFBD_API -u CFBD_PRED_API uv run --locked python -m cfb.record_book
```

Each season guide lives under `website/cfb/years/YEAR/excitement/`. The pregame
ledger captures ranks and the spread at the preceding checkpoint. An early game
whose schools lack both CORS ranks uses a labeled pregame baseline. The active
season also uses the dated CollegeFootballData game inventory for newly
announced matchups and recent finals.

## Acquire and prepare football evidence

Live calls require an authorized allowance. Games, weekly plays and historical
rosters cross the Season Snapshot request meter. Keep the returned game and
play responses in the private working directory. The earlier official Starter
Pack supplies inputs for original measurements through 2024.

```sh
uv run --locked python -m scripts.cache_cfbd_excitement \
  --output .sportsrank/cfbd-api.sqlite3 --max-requests 48 2026 2025
uv run --locked python -m scripts.prepare_cfbd_excitement \
  --export /path/to/official/starter-pack/data --private .sportsrank/cfbd-reduced
uv run --locked python -m scripts.prepare_cfbd_live_excitement \
  --database .sportsrank/cfbd-api.sqlite3 --private .sportsrank/cfbd-reduced
uv run --locked python -m scripts.freeze_cfbd_excitement \
  --private .sportsrank/cfbd-reduced --output cfb/cfbd_excitement
uv run --locked python -m scripts.prepare_cfbd_schedule \
  --database .sportsrank/cfbd-api.sqlite3 \
  --output website/cfb/years/2026/excitement/cfbd-schedule-v1.json 2026
```

Early game guides use the direct official games and team rosters. The cache
command supports `--games-only` and `--rosters` for selected seasons.
`scripts.prepare_early_cfbd_games` writes the reviewed early inventory.

The frozen comparison sample uses 2024 and 2026. The held-out 2025 season tests
quarter AEV against measured AEV. A quarter estimate qualifies if its mean
absolute error is at most 5 points, its absolute bias is at most 2 points and
Spearman rank correlation reaches 0.90. The policy records the achieved errors
and reference identity. Final-only scores have their own error measurements.
[Scoring method](../plans/2026-game-excitement-scoring-v1.md)

## Build and verify basketball pages

The basketball build accepts retained CollegeBasketballData responses. It
checks the provider host, query ranges, response lengths and stable game IDs
before replaying CORS from the earliest available roster. The dated edition,
team pages and season guides are rebuilt together.

```sh
env -u CFBD_API -u CFBD_PRED_API uv run --locked python -m cbb.site \
  --database .sportsrank/cbbd-history.sqlite3 --season 2027 \
  --as-of 2026-11-26T16:47:00+00:00 --output website/cbb
```

Use `scripts.cache_basketball_history` for authorized games and roster requests.
It records each attempt and splits queries that reach the provider’s row limit.
Use `scripts.cache_basketball_plays` for scoring plays. The date, team and
tournament endpoints return both sides of each matchup. Historical acquisitions
can use `--cover` to group source requests. The shared account's rate and
monthly limits apply to both sports. Historical play acquisition accepts
`--reserve` to preserve the balance needed for scheduled work.

```sh
uv run --locked python -m cbb.api_history --database .sportsrank/cbbd-history.sqlite3 \
  --season 2027 --output .sportsrank/cbbd-normalized.sqlite3
uv run --locked python -m scripts.prepare_cbbd_excitement \
  --database .sportsrank/cbbd-normalized.sqlite3 --plays .sportsrank/cbbd-plays.sqlite3 \
  --output cbb/cbbd_excitement
```

The first build freezes the 2023–24 and 2025–26 comparison games before
evaluating 2024–25. `--extend` adds other seasons from a separate play cache
using the existing reference. Every clock-based measurement must reconcile
the scoring team, score changes, halftime, final and overtime boundaries.
