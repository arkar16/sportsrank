# CFB Ranking System Engineering Review

## Scope

This review focuses only on the current College Football (`cfb/`) ranking flow, how it produces website artifacts, and where the codebase should be refactored next.

## Current System Design

The CFB ranking system is a script-driven batch pipeline that pulls live data from the CollegeFootballData API, computes weekly CORS ratings, writes every intermediate artifact to HTML inside `website/cfb/years/...`, and then reuses those generated files as inputs for later steps.

### Primary entrypoints

- `cfb/main.py`
  - CLI entrypoint.
  - Chooses `single_week`, `full_season`, or `history`.
  - Owns global defaults for year, week, division, HFA, and history range.
- `cfb/run_cfb_rankings.bat`
  - Runs `cfb/main.py`, then regenerates the root website index with `mainpage.py`.

### Core weekly flow

For a normal in-season week, the flow is:

1. `calc.regular_season_week(...)`
2. `records.get_current_records(...)`
3. `teams.get_teams(...)`
4. `games.get_results(...)`
5. `games.get_weekly_results(...)`
6. `cors.weekly_cors(...)`
7. `games.get_week_slate(...)`
8. `spread.weekly_spread(...)`
9. `spread_results.analyze_last_week_spreads(...)` for the prior week

### Week 0 flow

`calc.if_week_zero_true(...)` seeds the season by:

1. Fetching teams and full slate.
2. Fetching week-0 records.
3. Copying the previous season's final rankings to `W0` for all non-1897 seasons.
4. Applying `readjust.week_zero_readjust(...)` to regress prior-year CORS and add new teams.
5. Generating next-week spreads from the adjusted W0 rankings.

### Ranking formula inputs

`cors.cors_calc(...)` builds CORS from:

- current win percentage
- average margin of victory
- current strength of schedule based on prior-week opponent CORS
- a carry-forward term from last week's CORS
- pythagorean expected wins for reporting only

### Output model

The system writes HTML at nearly every step:

- team lists
- season results
- weekly results
- weekly records
- weekly rankings
- spreads
- spread accuracy reports
- history pages

The website is not a separate consumer of structured data. The ranking job itself is also the content-generation job.

## What The System Does Well

- The flow is straightforward to run manually.
- The CORS formula is centralized in one module instead of spread across many files.
- Weekly and historical runs share most of the same computation path.
- Generated website output is easy to inspect because every step leaves visible artifacts behind.

## Engineering Findings

### 1. The pipeline uses HTML as both presentation and storage

This is the biggest architectural constraint in the repo.

- `cors.weekly_cors(...)` reads last week's rankings back from HTML.
- `end_week.get_end_week(...)` derives season length from a generated slate HTML file.
- `spread_results.analyze_last_week_spreads(...)` reloads predictions from HTML and reparses them with `pandas.read_html`.
- History generation scrapes FINAL ranking HTML pages to reconstruct summaries.

Impact:

- business logic is tightly coupled to the website layout
- small HTML shape changes can break the ranking pipeline
- reruns are slower and harder to reason about
- there is no clean machine-readable source of truth

### 2. Global working-directory mutation is everywhere

Nearly every module calls `os.chdir(config.owd)` and then moves again into nested folders.

Impact:

- functions are not referentially transparent
- order of calls matters more than it should
- tests will be brittle
- parallelization becomes unsafe
- failures can leave the process in the wrong directory

This is currently one of the main reasons the code behaves like a script bundle instead of a composable system.

### 3. Paths and runtime assumptions are inconsistent with the current workspace

There are hardcoded macOS paths in active code:

- `cfb/config.py`
- `cfb/html_grab.py`
- `mainpage.py`

The repo is currently being worked in a Windows path, but several modules still assume `/Users/aryak/...`.

Impact:

- the code is not portable across machines
- local runs depend on the exact author's filesystem layout
- automation and deployment are fragile

### 4. The API layer repeatedly refetches the same data

The current weekly run fans out into repeated CFBD calls:

- `get_current_records(...)` calls `get_teams(...)` and `get_results(...)`
- `regular_season_week(...)` separately calls `get_weekly_results(...)` and `get_results(...)`
- `weekly_spread(...)` calls `get_week_slate(...)` after earlier steps have already fetched schedule data elsewhere

Impact:

- unnecessary network overhead
- harder to cache or replay runs
- more failure points
- slower history builds

### 5. Configuration is split, hardcoded, and partially duplicated

Configuration currently lives across:

- module-level constants in `cfb/main.py`
- `cfb/config.py`
- `webconfig.py`
- implicit constants inside functions

Examples:

- year/week defaults are in `cfb/main.py`
- `config.week_count` is separate from the `end_week = 15` constant in `cfb/main.py`
- sport/version values exist in more than one config file

Impact:

- changing one runtime assumption requires edits in multiple places
- production behavior is hard to predict
- historical and weekly runs are more error-prone than they need to be

### 6. A live API key is committed in the repo

`cfb/api.py` contains a raw bearer token.

Impact:

- secret leakage
- revocation risk
- no safe way to collaborate or deploy

This should be treated as an immediate security issue, even if the key is already rotated later.

### 7. There are logic and correctness risks hidden inside the batch flow

Observed examples:

- `history_calc(...)` asks `get_end_week(i)` before the current year's slate is guaranteed to exist.
- `history_calc(...)` mutates `year` inside a loop over `i`, which works only because of the current control flow and is harder than necessary to trust.
- `spread_results.calculate_spread_result(...)` appears to evaluate positive spreads incorrectly for ATS outcomes when the away team is favored.
- several places use broad `except:` blocks and swallow root causes.
- many functions rely on `None` checks instead of robust `pandas` null handling.

Impact:

- historical reruns can fail in surprising ways
- prediction evaluation may be inaccurate
- debugging production issues is much harder than necessary

### 8. The domain model is implicit, not explicit

Right now "team", "game", "record", "ranking row", and "spread prediction" mostly exist as DataFrames with ad hoc columns.

Impact:

- no clear contract between stages
- column drift can silently break downstream steps
- the ranking formula is harder to verify than it should be

## Recommended Refactor Direction

### Priority 0: stabilize and secure the current system

Do these first:

1. Move the CFBD API key to environment-based configuration.
2. Remove hardcoded absolute paths and derive all roots from the repo or a single configured output directory.
3. Replace broad `except:` blocks with targeted exceptions and better logging.
4. Fix correctness bugs in spread-result evaluation and history execution order.

### Priority 1: separate data generation from HTML rendering

Introduce a structured storage layer:

- write canonical weekly artifacts as CSV, Parquet, or JSON
- keep HTML as a final rendering/output step only
- make ranking logic read structured data, not HTML pages

The cleanest first cut would be:

- `artifacts/{year}/teams.csv`
- `artifacts/{year}/results.csv`
- `artifacts/{year}/weekly_results/week_{n}.csv`
- `artifacts/{year}/rankings/week_{n}.csv`
- `artifacts/{year}/spreads/week_{n}.csv`

Then render website HTML from those files.

### Priority 2: introduce a real service boundary inside `cfb/`

Recommended package split:

- `cfb/cli.py`
  - argument parsing and job kickoff
- `cfb/settings.py`
  - all config, env loading, paths
- `cfb/clients/cfbd_client.py`
  - all API access and response normalization
- `cfb/domain/models.py`
  - typed schemas or dataclasses for teams, games, rankings, spreads
- `cfb/services/records.py`
  - record aggregation
- `cfb/services/cors.py`
  - rating calculations only
- `cfb/services/spreads.py`
  - spread generation and evaluation
- `cfb/services/history.py`
  - history rollups
- `cfb/render/html.py`
  - website output only
- `cfb/storage/repository.py`
  - reads and writes structured artifacts

That would let the ranking system evolve without dragging presentation logic through every function.

### Priority 3: stop passing control through global filesystem state

Refactor functions so they:

- take explicit input objects or file paths
- return DataFrames or typed objects
- never call `os.chdir`

Use `pathlib.Path` and pass a root output directory once.

This one change would improve portability, testability, and composability immediately.

### Priority 4: cache API responses and make runs reproducible

Add a season cache layer so repeated history runs do not hit the live API for everything.

Recommended behavior:

- fetch once from CFBD
- normalize to a stable tabular schema
- persist locally
- rerun ranking logic from cached inputs unless refresh is requested

This matters a lot for historical backfills and formula tuning.

### Priority 5: add tests around the formula and the orchestration

Minimum useful coverage:

- `margin_of_victory`
- `current_sos`
- `pythagorean_exp`
- `cors_calc`
- week-0 carry-forward behavior
- spread generation and ATS grading
- history run on a tiny fixture season

Without these, refactoring the formula safely will stay slow.

## Suggested Near-Term Refactor Plan

### Phase 1

- centralize settings
- remove hardcoded paths
- move secrets to env vars
- eliminate `os.chdir` from one vertical slice first

### Phase 2

- create structured artifact writes for teams, results, rankings, and spreads
- switch downstream readers from HTML to structured files

### Phase 3

- isolate rendering into dedicated HTML builders
- add tests against a fixture season
- add a single orchestrator class or function for weekly runs

## Practical Interpretation Of The Current Repo

Today, this repo is best understood as:

- a working research pipeline for CFB rankings
- tightly coupled to one developer machine and one website output layout
- strong enough for manual iteration
- not yet shaped like a maintainable application or reusable ranking engine

The next best investment is not changing the formula first. It is creating a cleaner execution boundary so formula changes become easier and safer afterward.
