# SportsRank

SportsRank publishes College Football CORS rankings as a barebones static HTML
website under `website/`. Firebase Hosting serves those files; the public site
has no Python, Node, database, or server runtime.

## Current work

Start with [current work](docs/work/current.md) for active tasks and their
acceptance records. Historical recovery evidence is indexed in
[Gate 1 evidence](docs/evidence/2026-gate1.md). The
[recovery contract](docs/plans/2026-p0-recovery-and-backfill.md) and
[publication runbook](docs/operations/2026-season-recovery-morning.md) retain
the implementation and operating requirements.

## Repository navigation

| Area | Purpose |
| --- | --- |
| [CFB CLI/runtime](cfb/) | Supported college-football ranking command-line interface and runtime. See [cfb/README.md](cfb/README.md) for usage. |
| [Website](website/) | Permanent public static HTML artifacts and their supporting files. |
| `tests/` | Automated tests for the ranking runtime and published-site contracts. |
| [Docs](docs/) | ADRs (`docs/adr/`), plans (`docs/plans/`), and operational notes (`docs/operations/`). |
| [Architecture](docs/architecture/overview.md) | Current module boundaries and deferred architecture. |
| [Current work](docs/work/current.md) | Active tasks and acceptance records. |
| [Legacy NFL](legacy/nfl/) | Unsupported historical standalone NFL experiments and their preserved direct script entry point. |
| [`mainpage.py`](mainpage.py), [`webconfig.py`](webconfig.py) | Path-sensitive legacy batch helpers retained at the repository root. |

The recovery contract above owns product requirements. Use
[CFB CLI documentation](cfb/README.md) for supported recovery interfaces.

## Local working files

Keep private inputs and generated review output in the ignored `.sportsrank/`
directory. The root `.scratch/` directory holds local investigation and release
evidence; the root `error.log` is a legacy runtime log. Both are ignored to keep
them out of accidental staging. Ignoring these paths does not delete or back up
their contents. Preserve evidence needed by active tasks and publication.

## Safe offline verification

Python 3.12 and the locked dependencies are required:

```sh
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv sync --locked
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv run --locked python -m unittest discover -s tests -v
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv run --locked python -m compileall -q cfb tools scripts tests
test -f website/index.html && test -f website/404.html
```

Portable CI runs the offline suite on every pull request and main push, with
superseded runs cancelled. Feature-branch pushes do not duplicate PR checks.
CI checks the static entry files without installing Node/Firebase dependencies.
Deployment remains independent and does not require a release receipt; see
[Publication](#publication). Node dependencies are needed for deployment and the
local Firebase emulator.

These commands do not require a CFBD key and must not publish. Ordinary
verification uses portable Schema 2/3 fixtures and does not depend on private
or ignored caches. Static hosting build success does not establish strict
Release, link, or HTML acceptance. The production adapter reads the project-
only bearer token from `CFBD_API`, injected by T3 for authorized local fetches.
The adapter does not fall back to `CFBD_API_KEY` or load a dotenv file.
The value must never enter source,
command arguments, tracked environment files, generated artifacts, logs,
issues, or chat.

## Historical postseason reconstruction

Saved forecasts/results cannot reveal games absent from both. To restore
available postseason games from a local CFBD historical games CSV and rebuild
the affected carryover chain through the latest completed archived checkpoint:

```sh
env -u CFBD_API -u CFBD_API_KEY uv run --locked python -m scripts.rebuild_history \
  --games-csv /path/to/games.csv --output .sportsrank/historical-review
open .sportsrank/historical-review/cfb/performance/index.html
```

The output must be a new directory separate from the source website and CSV.
The command detects the earliest missing or relocated postseason season; `--from-season`
can select an explicit chain start. It writes corrected rankings, results,
records, progression, historical summaries, separate reconstructed predictions,
and retrospective performance. Original input and saved spread/issued-forecast
bytes remain unchanged. [ADR-0024](docs/adr/0024-reconstruct-history-with-archived-membership.md)
owns historical membership and calendar compatibility. No API request occurs.

This output is an **unsealed local review site**, not a production release.
Inherited release metadata does not validate its corrections; do not deploy it.
The delivery task owns acceptance and subsequent authenticated release integration.

For a complete release candidate, use the public export boundary with
`--historical-games-csv /private/path/games.csv --historical-games-sha256 SHA256`
and `--market-inputs /private/path/inputs.json`. It first validates the ordinary
private release, then independently checks restored results, rankings, records,
carryover, progression, original forecast preservation and complete reports.
The resulting correction and its input identities are bound into the reviewed
public receipt. Passing local validation does not authorize publication.

To keep those inputs together, retain one **private** JSON configuration (no
credentials) and run:

```sh
env -u CFBD_API -u CFBD_API_KEY uv run --locked python -m scripts.prepare_site \
  --config /private/path/site-preparation.json --output /new/preparation
```

The configuration's `schema` is `sportsrank-site-preparation/v1`. Required path
fields are `private_candidate`, `baseline_archive`, `source_input_root`,
`source_archive`, `trusted_input_manifest`, `firebase_json`, `code_root`,
`historical_games_csv` and `market_inputs`; relative paths resolve beside the
configuration. Required digest fields are `baseline_sha256`,
`source_archive_sha256` and `historical_games_sha256`. Select the authenticated
current predecessor with `successor_baseline` for successor publication.
Omitting it supports an initial-baseline draft, not a replacement for current
live-predecessor reconciliation. Calculations and tests use cached inputs and
make zero CFBD requests. Successor authentication retrieves existing immutable
GitHub evidence using `GITHUB_TOKEN`, `GH_TOKEN`, or the existing `gh` login,
without printing or saving the credential; it does not read or
write Firebase. Results are `site/` and `receipt.json` under a new output folder.

## CORS model performance

Evaluate every archived season using the predictions and final scores already
on the website:

```sh
uv run --locked python -m scripts.backforecast
open .sportsrank/backforecasts/all/cfb/performance/index.html
```

For one season or week:

```sh
uv run --locked python -m scripts.backforecast 2023
uv run --locked python -m scripts.backforecast 2026 --from-week 5 --through-week 5
open .sportsrank/backforecasts/2026/report.html
```

Saved spreads keep their original rounding. Where a scored FBS matchup has no
saved prediction, the command uses preceding-checkpoint ratings with the current
unchanged CORS arithmetic (PRESEASON for Week 0). Missing predictions/ratings,
pending scores and non-FBS games are counted explicitly. Legacy HTML encodings
are supported. No ranking rerun, historical-source validation or API request is
needed. Reports show winner accuracy, CORS line coverage/pushes, MAE, RMSE,
weekly summaries and game-level results with CSV/JSON downloads.

The normal `cfb.public_site export` step also generates these reports at
`cfb/performance/index.html` and `cfb/performance/<year>/index.html`, links them
from CORS home, and binds them into the validated public manifest and receipt.
Publication uses the existing reviewed-site workflow. Running the report command
alone makes a local preview; it does not deploy. Existing predictions, rankings,
result inputs and issued-forecast records are preserved.

For a manual T3 project script, use `uv run --locked python -m
scripts.backforecast` from the repository root, with automatic execution disabled.

### Machine-learning testing with the CFBD Starter Pack

Use the privately retained CollegeFootballData.com Starter Pack in your dev
directory to compare forecasts based on prior-game advanced metrics with CORS:

```sh
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv run --locked python -m scripts.ml_testing \
  --starter-pack ../starter_pack --output .sportsrank/ml-testing/starter-run-01
```

The adapter reads `data/games.csv` and `data/advanced_game_stats/<year>.csv`.
It uses the existing NumPy dependency and runs entirely offline. Keep the bundle
and generated research output private: the pack's license permits personal use
but prohibits redistribution of its data and notebook code. Repository tests use
synthetic inputs. Neither source tree is modified, and output must be a new
directory separate from both source trees.

Four forecasts are scored on the **same eligible games**: original saved CORS,
a learned CORS-only calibration, advanced-stat regression, and CORS plus advanced
stats. The latter two use fixed ridge regularization (`alpha=1`); no parameter
search occurs. Features are home-minus-away differences in prior-game offensive
and defensive PPA and success rate, plus a home-field indicator (zero at neutral
sites). Each team's history uses equal-weighted, completed FBS-versus-FBS games
from the same season, with at least two usable prior games required.

Features freeze at Monday 00:00 UTC of the target game's week. A prior game's
kickoff plus a 24-hour availability allowance must be strictly before that cutoff.
Postseason dates determine chronology even when provider week numbers restart.
Timezone-free bundle timestamps are interpreted as UTC. Legacy blank TBD flags
are counted and use their recorded timestamps; explicitly TBD or missing kickoff
times are excluded. This is a conservative historical availability **proxy**:
the bundle has no original metric-retrieval timestamps. Full-season aggregates,
postgame win probabilities and end-of-game Elo never enter the features.

Each held-out season fits its models and feature scaling using only earlier
seasons, with at least two training seasons and 100 paired games by default.
Use `--from-season 2023 --through-season 2024` to narrow evaluation while retaining
earlier training history. The local bundle determines the available years;
missing recent seasons are not downloaded. Game IDs match saved CORS when
possible. Legacy IDs can use a unique team/orientation match across the whole
season; ambiguous identities, missing forecasts and score disagreements are
counted and excluded from every model's paired sample.

`report.json` retains source hashes, coverage and join exclusions, model settings,
training seasons, scaling and coefficients, per-game features/predictions, and
per-season and game-weighted MAE/RMSE. `games.csv` holds the same held-out rows.
These outputs are deterministic in the same environment; `runtime.json` records
variable elapsed time separately. Inspect recent folds as well as totals, since
coverage and football eras vary. Keep future data untouched before selecting a
production model; retrospective results alone do not establish live improvement.
Production CORS, site generation and publication do not use these challengers.

### Archive-only calibration baseline

Run a local, offline experiment against the saved forecast archive:

```sh
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv run --locked python -m scripts.ml_testing \
  --output .sportsrank/ml-testing/first-run
```

Use a **new output directory** for each run. No API requests, publication or new
ML dependencies are needed. The experiment learns a linear calibration,
`actual home margin = intercept + slope * CORS home margin`, using NumPy's
least-squares solver. It compares that challenger with unchanged CORS and a
zero-margin baseline on exactly the same games. Positive margin favors home;
the saved home handicap has the opposite sign.

Each season is a held-out fold trained on all eligible **earlier seasons**,
with at least two training seasons and 100 games by default. There is no random
split, within-season fitting or parameter search. Only completed FBS versus FBS
games with saved forecasts enter training or scoring. Reconstructed forecasts,
missing predictions/scores and non-FBS games are counted separately. A saved
archive value is not proof it was issued before kickoff; this experiment does
not certify historical feature timing or change authenticated forecast records.

To score a narrower period, retain earlier training history with:

```sh
env -u CFBD_API -u CFBD_API_KEY -u CFBD_PRED_API uv run --locked python -m scripts.ml_testing \
  --from-season 2023 --through-season 2025 --output .sportsrank/ml-testing/2023-2025
```

`report.json` records configuration, source paths/SHA-256 hashes, NumPy version,
coverage, skipped folds, training seasons, fitted coefficients and paired
game-weighted MAE/RMSE. `games.csv` holds every held-out prediction and outcome.
Those files reproduce from the same inputs and environment; `runtime.json`
separately records variable elapsed time. Source/output overlap and reusing an
existing output directory are rejected. Early seasons with insufficient training
are skipped explicitly; a run with no held-out games fails.

Treat the results as exploratory. Archive gaps and changes between football
eras can bias the aggregate; inspect individual recent folds as well. Keep a
future season untouched before selecting a production model, and do not select
parameters on the test results and then describe them as an independent test.
Production CORS, site generation and publication do not use this challenger.

### Provider comparison

The same reports can include provider spreads, Market ATS, and paired CORS/provider
MAE and RMSE. Upcoming retained forecasts appear on the current-season report,
linked from the all-season index. Original spread files are preserved. The
selection policy is teamrankings in 2013–2020 and Bovada from 2021; equal lines
produce no pick, every nonzero difference selects a side, and pushes are separate.
Missing quotes remain unavailable; comparisons never substitute another provider.

Reuse a private manifest of retained API responses for both preview and release
preparation:

```sh
env -u CFBD_API -u CFBD_API_KEY uv run --locked python -m scripts.backforecast \
  --market-inputs /private/path/market-inputs.json
```

`market-inputs/v1` contains `responses`, each with `season`, local `path`
(relative to the manifest or absolute), exact `sha256`, and timezone-bearing
`retrieved_at`. Each response is the unchanged CFBD `/lines` JSON array. Raw
responses stay private; the site contains only selected-line observations and
safe provenance at `cfb/performance/market-lines.json`. Retrieval time is not a
claim that the line was quoted then or was the closing line.

Pass the same `--market-inputs` option to `cfb.public_site export` to bind the
comparison pages and selected observations into the validated manifest/receipt.
Pass it to `scripts.rebuild_history` to include provider comparison in the
corrected-history review. To refresh reports on an existing corrected review,
use `scripts.backforecast --website /path/to/review --reconstruct-history
--market-inputs /path/to/inputs.json --output /new/preview`. That review remains
unsealed until historical-correction publication integration is accepted.

Acquisition is a separate, explicitly authorized operation:

```sh
uv run --locked python -m scripts.fetch_market_lines --seasons 2026 \
  --meter /private/path/cfbd_requests.sqlite3 --output /private/new-observation
```

Use the existing accounted request audit, never an empty replacement ledger.
Each selected season consumes one metered request through the Season Snapshot
boundary. A new directory is claimed before transport; interrupted/failed
observations are retained and cannot be replayed by rerunning the same command.
There is no automatic retry or fetch during reporting/export. An explicit
`--historical-reserve N` conservatively accounts for known current-month
historical attempts absent from the selected audit. A previous allowance or
example command does not authorize another fetch.

## Postseason calendar repair

Historical Schema 3 snapshots are migrated offline into new Schema 4
snapshots with exact source-checksum, calendar, and correction-registry
provenance. The migration makes no provider calls:

```sh
uv run python -m cfb.recovery migrate-postseason \
  --source-root <directory-containing-cfb-fbs-YEAR.json> \
  --destination-root <new-empty-directory>
```

The pinned registry is only for the 92 historical recovery rows. Fresh
provider-backed postseason games use the provider's explicit phase plus the
fixed local-date window; missing, unsupported, or contradictory phase metadata
fails closed.

## Publication

Push reviewed generated `website/` changes to `main` to publish directly to
Firebase Hosting. The workflow deploys the triggering commit and checks five
current public pages against committed bytes. It can also be dispatched manually
on `main` to publish the existing website. Broad offline CI runs independently;
full-site receipt regeneration, immutable packaging, sealing and exhaustive live
inventory verification are not routine publication gates.

Keep raw CFBD inputs and credentials private. Preserve historical public URLs,
the CORS model and originally issued forecasts. The existing production
credential environment remains protected. This publishes files already generated
and committed; unattended weekly generation remains separate work.

See [the current runbook](docs/operations/direct-publication.md) and
[ADR-0026](docs/adr/0026-restore-direct-static-publication.md). The recovery CLI
and old evidence remain available for historical inspection, not routine release.

## Working with agents

[AGENTS.md](AGENTS.md) is the portable repository entry point.
[Project memory](docs/agents/project-memory.md) assigns one home to each kind
of information. Installed ask-matt skills guide design and delivery; T3 owns
thread and execution state. [GitHub issues](docs/agents/issue-tracker.md) own
current task scope, dependencies, and acceptance. Historical workflow records are retained in the
[frozen archive](docs/archive/codex-workflow/2026-09-10/README.md).
