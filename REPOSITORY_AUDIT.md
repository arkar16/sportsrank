# SportsRank Repository Audit

Date: 2026-08-29
Scope: current `main` branch (`3b673487`), with emphasis on the CFB ranking pipeline, CFBD integration, generated site, tests, and deployment.

## Remediation update — 2026-09-03

The audit below records the repository state before the requested rollback. The
working tree now restores the legacy static-HTML ranking path and removes the
Polars/Vite/TypeScript client-rendering migration. It also:

- removes tracked Python bytecode, Firebase cache state, and the debug log;
- reads the bearer token lazily from `CFBD_API_KEY`;
- uses CFBD v2's `classification` request parameter and response fields;
- keeps repository-relative output paths; and
- adds focused tests for the static-site contract and CFBD adapter.

The exposed key must still be revoked/rotated, and removing the current `.pyc`
does not erase it from existing Git history or old clones.

Everything after this update is the original pre-remediation analysis and is
retained as the evidence and longer-term backlog.

## Executive assessment

The ranking math is the strongest part of the repository. The new Polars implementation has a compact public entry point, deterministic calculation tests, structured Parquet/JSON outputs, and a safer browser renderer. However, the surrounding system is in a half-migrated state and is not currently operational end to end from a clean checkout.

The immediate risks are:

1. A CFBD credential is recoverable from tracked Python bytecode. Rotate it immediately.
2. Both CFBD ingestion paths use the pre-v2 client interface even though the lockfile installs `cfbd==5.7.0`. Authentication, request parameters, and response fields are incompatible.
3. The test suite fails during collection on a clean checkout because calculation code imports the ignored credential module at import time.
4. The new cache can silently freeze live-season game data because normal ranking runs never request a refresh.
5. The new JSON/client-rendered site path is not actually populated in the checked-in site; current public artifacts are stale and contain broken navigation.

The best sequence is: secure the key, restore one tested CFBD v2 adapter, make current-season data freshness explicit, put end-to-end verification in CI, and only then continue the larger architecture migration.

## Interpretation of “AI key”

I interpreted “AI key” as “API key.” No OpenAI, Anthropic, Gemini, or other LLM integration is present in the tracked application source. The relevant credential is the CFBD bearer key.

CFBD’s current guidance is to store the key in `CFBD_API_KEY`, construct `cfbd.Configuration(access_token=...)`, and create the generated client inside an `ApiClient` context. The API still uses the exact `Authorization: Bearer <key>` HTTP contract. Sources: [CFBD getting started](https://api.collegefootballdata.com/getting-started), [authentication](https://api.collegefootballdata.com/authentication), and [Python quickstart](https://api.collegefootballdata.com/libraries/python).

## What is working well

- `generate_rankings_through_week(...)` is a useful deep module interface: one call performs calculation and produces the ranking artifacts.
- The Polars calculation implementation is materially easier to test than the legacy pandas/HTML loop.
- The JSON payload uses a small, explicit browser contract, and the TypeScript renderer writes external values with `textContent` rather than injecting HTML.
- Paths in `cfb/site_paths.py` and `cfb/config.py` are repository-relative and portable.
- The current six calculation/output tests pass when the credential import is replaced by a harmless test stub.
- Legacy ranking pages can be parsed into the new normalized ranking shape, which gives the migration a workable compatibility bridge.

## Critical findings

### P0 — A credential is embedded in tracked bytecode

`cfb/__pycache__/api.cpython-311.pyc` is tracked. Inspection without printing the value confirmed that its module assigns a non-empty string constant to `api_key`. Ignoring `cfb/api.py` does not protect a secret compiled into a committed `.pyc`, and removing the current file alone does not remove it from Git history.

Impact:

- Anyone who can read the repository can recover the key.
- If the repository has ever been public or broadly shared, assume compromise.
- A replacement key would be exposed again if bytecode remains tracked.

Action:

1. Revoke/rotate the exposed CFBD key before any code cleanup.
2. Remove all 17 tracked `__pycache__`/`.pyc` files and the tracked Firebase cache/debug artifacts.
3. Purge the credential-bearing bytecode from Git history if history exposure matters; rotation is still required even after a history rewrite.
4. Use only `CFBD_API_KEY` locally and secret storage in automation. Never recreate `cfb/api.py` as the application interface.

### P0 — The installed CFBD client and application code disagree in three places

The lockfile installs `cfbd==5.7.0`. Runtime inspection of that exact version showed:

- `Configuration(access_token=...)` produces bearer authentication; assigning `configuration.api_key["Authorization"]` produces no auth setting.
- `GamesApi.get_games(...)` accepts `classification`, not `division`.
- `Game` exposes `home_classification` and `away_classification`, not `home_division` and `away_division`.

The incompatible code is present in both the legacy modules and the new pipeline:

- `cfb/games.py:8-11`, `23`, `81`, `143`, `199`
- `cfb/teams.py:8-11`
- `cfb/rankings_pipeline.py:103-114`, `543-555`

The current official client documents the same contract and is now at package/API version 5.24.x: [official Python client](https://github.com/CFBD/cfbd-python), [Games operation](https://github.com/CFBD/cfbd-python/blob/main/docs/GamesApi.md), and [Game model](https://github.com/CFBD/cfbd-python/blob/main/docs/Game.md).

Expected failure modes are unauthorized requests, rejected `division` arguments, and missing response attributes.

### P0 — Clean-checkout tests fail before running

`cfb/rankings_pipeline.py` imports `.api.api_key` at module import time. Because `cfb/api.py` is intentionally absent, the import handler catches the error and then attempts top-level `import config`, which also fails under package-style test imports.

Observed result:

```text
ModuleNotFoundError: No module named 'cfb.api'
...
ModuleNotFoundError: No module named 'config'
```

With a synthetic `cfb.api` test module, all six tests pass. This means the calculation tests are useful, but credential loading is coupled to code that should be pure.

The broad `except ImportError` also masks import errors from any of the imports in the block, not just the intended script/package compatibility case.

### P0/P1 — Live data can remain permanently stale

`load_or_fetch_sources(...)` returns cached teams and games whenever both Parquet files exist and `refresh=False`. Every call from `cfb/calc.py` uses the default `refresh=False`.

Typical failure sequence:

1. Week 0 fetches the full scheduled season, with future scores still null.
2. The source Parquet files are created.
3. Week 1 and later runs reuse the same snapshot indefinitely.
4. New scores never reach the ranking calculation unless a human deletes the cache or calls an undocumented refresh path.

This is a correctness failure, not just a cache optimization issue.

## High-priority findings

### P1 — Two ingestion systems duplicate calls and disagree on contracts

The normal weekly orchestrator still calls legacy `get_teams`, `get_results`, `get_weekly_results`, and `get_week_slate`, then calls the new pipeline, which has its own teams/games clients and cache. `get_current_records` itself refetches teams and all games.

Consequences:

- Multiple CFBD calls retrieve overlapping data during one run.
- Quota use and failure exposure are unnecessarily high. CFBD explicitly meters usage and exposes account/usage operations; current access rules can change ([usage guidance](https://api.collegefootballdata.com/usage-and-access)).
- The legacy and Polars paths can compute from different snapshots during the same run.
- Every CFBD schema migration must be implemented more than once.

Deepening opportunity: define one external seam, `load_season(season, classification, refresh_policy) -> SeasonSnapshot`, with a CFBD v2 adapter and an in-memory/fixture adapter. The snapshot should contain normalized teams, games, fetch time, source/client version, and a checksum. All records, rankings, slates, and spreads should derive from that one snapshot.

### P1 — The new browser path is not live in the checked-in site

The repository contains compiled `rankings-app.js` and CSS, but no checked-in `website/assets/data/cfb/...` ranking payloads or manifests. The existing 2024 ranking pages are still legacy HTML tables. Therefore the newest architecture exists in source and tests but is not represented by a complete deployable artifact set.

The current site is also stale:

- `website/index.html` says it was last updated in December 2024.
- The CFB hub stops at 2024 and labels itself CORS v0.2.0.
- The root page labels itself v0.4.0.
- The 2024 year page labels itself v0.3.2.
- The history pages label themselves v0.2.0.
- A 2025 year shell exists but is not linked from the CFB hub and points to a nonexistent final ranking.

### P1 — Final-season side effects regressed in the migration

The old final-week calculation updated national-champion and worst-team history. The new `generate_rankings_through_week` writes the final ranking, payload, and manifest only. `process_rankings(...)` is called by `main.py` only in `history` mode.

As a result, a normal `single_week` final or `full_season` run does not update the all-time pages. Normal runs also do not regenerate year navigation because the `html_grab` call is limited to history mode.

### P1 — The 1897 migration changes the model’s initial condition

The legacy genesis season initialized existing 1897 teams at CORS `0.0`. The new pipeline receives no prior rankings and fills every team with `fcs_constant == -10`.

That change is not covered by tests or recorded as a formula-version decision. It can cascade through a full historical rebuild because each season starts from the previous final. Either preserve the 1897 zero baseline or deliberately version and validate the changed model.

### P1 — Errors are swallowed and successful exit status is unreliable

`main.run_calculations` catches every exception, logs it, and returns. A failed ranking job can therefore finish with a success exit code. `weekly_spread` has a bare `except` that reports “no FBS games” for any failure, including programming errors and file corruption.

This undermines local automation and CI. Expected “no games” cases should be explicit results; unexpected exceptions should propagate and fail the job.

### P1 — There is no CI quality gate before deployment

Both Firebase workflows build and deploy the browser bundle but do not run Python tests, type checking, an API-contract test, artifact validation, or a local-link check. They also use `actions/checkout@v2`, do not pin a Node version, and run the redundant sequence `npm i && npm ci`.

The package scripts are inconsistent with the Firebase configuration: `serve`, `deploy`, and `logs` target Functions, while `firebase.json` defines Hosting only.

## Medium-priority cracks

### P2 — HTML and the process working directory remain application state

Legacy modules repeatedly call `os.chdir` and use generated HTML as storage. A failure can leave the process in an unexpected directory, functions cannot safely run concurrently, and presentation changes can break calculation inputs.

The Polars path is a good start, but the orchestrator still invokes the legacy path, so the architectural risk remains active.

### P2 — Site generation creates invalid and broken pages

Legacy generators close `</body></html>` before appending timestamps, tables, and links. Browsers recover, but the documents are invalid and brittle.

A static scan checked 4,146 local `href`/`src` references and found 207 missing targets across 126 HTML files. Some missing historical spread pages may reflect seasons without games, but they should not be linked. The prominent 2024 and 2025 “FINAL” links are also missing.

### P2 — Season/week selection is not trustworthy around season boundaries

`main.py` uses a fixed `end_week = 15`, while `config.week_count = 16` and a dynamic helper exists but is commented out. Its current-week heuristic assumes the first Saturday in September and uses the calendar year, which can select the next season during January postseason play and does not model Week 0.

Use explicit CLI inputs for automation and derive defaults from CFBD calendar/game data, not wall-clock arithmetic.

### P2 — Cache and publication are not reproducible or atomic

Source Parquet caches do not record fetch time, CFBD client version, request parameters, schema version, or checksum. Outputs are written directly to final paths. A crash can leave a partial season, and an old source snapshot cannot be distinguished from a current one.

Historical reruns also depend on mutable live upstream data unless a local snapshot happens to exist.

### P2 — Test coverage does not match the failure surface

Current tests cover formula examples, pre-1996 ties, payload/shell structure, output paths, and legacy-page parsing. Missing tests include:

- credential absence and environment loading
- CFBD v2 request and response normalization
- refresh/staleness behavior
- current-season orchestration
- final-week naming and history updates
- 1897 genesis behavior
- spread generation and push grading
- CLI exit codes
- link/manifest integrity
- browser payload validation and rendering behavior

The “legacy baseline” test checks parsing and output size, not numerical equivalence between the old and new algorithms.

### P2 — Generated and runtime files dominate the repository

The tree contains about 11,419 tracked files, including about 11,416 HTML files. `website/` is about 173 MB, and Python bytecode, Firebase cache state, and a debug log are tracked. This makes review noisy and creates opportunities for secrets and stale runtime state to enter Git.

The historical site can remain static, but its canonical source should be structured, versioned data plus deterministic generation—not thousands of hand-committed intermediate pages.

### P2 — Documentation and package metadata are contradictory

- The root README is only a placeholder.
- `cfb/README.md` references a nonexistent `requirements.txt`, says Python 3.7+, and documents a different default mode than the code.
- `pyproject.toml` requires Python 3.12+.
- The npm package is version 1.0.0, the Python project is 0.1.0, and the CORS model is v0.4.0.
- The Python dependency list explicitly includes several transitive/runtime support packages, obscuring which dependencies the project actually owns.

## Quick wins

### Immediate, less than one day

1. Rotate the leaked key; remove tracked bytecode/cache/log files.
2. Replace `cfb/api.py` with a lazy `CFBD_API_KEY` environment read inside the CFBD adapter.
3. Use `cfbd.Configuration(access_token=key)` and `with cfbd.ApiClient(...)`.
4. Map `division` at the internal interface to `cfbd.DivisionClassification(division.lower())`, pass `classification=...`, and normalize `home_classification.value`/`away_classification.value` into stable internal field names.
5. Remove credential imports from calculation modules so offline tests collect with no key.
6. Make unexpected job errors propagate to a nonzero exit code.
7. Change live-season runs to refresh source data by default; retain explicit cache reuse for historical/offline runs.

### One to three days

1. Add a CFBD adapter contract test using generated model objects or captured redacted fixtures. It should fail if auth settings are empty, if request names drift, or if response mapping changes.
2. Add freshness metadata and atomic source/output writes.
3. Add CI steps for `pytest`, TypeScript checking/build, a representative end-to-end fixture season, and local-link/manifest validation before Firebase deploy.
4. Fix Firebase scripts to target Hosting and modernize the workflows (`checkout@v4`, explicit supported Node, `npm ci` only).
5. Repair the CFB hub/current-year pages and stop generating links to absent outputs.
6. Add the 1897 baseline and final-history behavior to regression tests.
7. Rewrite the README around `uv`, Python 3.12, `CFBD_API_KEY`, supported commands, refresh semantics, and publish flow.

### Three to seven days

1. Replace repeated legacy fetches with one normalized `SeasonSnapshot` per run.
2. Derive records, weekly results, slates, rankings, and spreads from that snapshot.
3. Generate all navigation from the season manifest rather than guessed week ranges.
4. Add staging validation, then publish a complete artifact set only after all checks pass.
5. Decide whether the Polars pipeline exactly preserves CORS v0.4.0. If not, version the formula and backtest before publishing.

## Long but important work

### 1. Finish the single-pipeline migration

Keep one deep external ranking interface and split its implementation at real internal seams:

- CFBD v2 adapter: authentication, retries, quota-aware fetching, response normalization
- season snapshot repository: freshness, provenance, offline replay
- ranking engine: pure records/CORS calculations
- spread engine: prediction and grading, including pushes
- artifact publisher: Parquet/JSON/manifests and compatibility HTML
- site renderer: navigation and browser presentation

Retire legacy modules as each behavior moves behind the new interface; do not leave two production implementations indefinitely.

### 2. Establish a reproducible model-validation suite

Create small but representative fixture seasons covering:

- 1897 genesis
- a pre-1996 tied game
- an FBS/FCS matchup
- a team joining/leaving FBS
- the irregular 2020 season
- the current expanded-playoff era
- a postponed/cancelled/uncompleted game
- neutral-site spreads and ATS pushes

Compare full ranking frames, not only top/bottom teams. Store model version, input checksum, and expected output checksum.

### 3. Separate generation from publication

Build into a staging directory, validate schemas/row counts/links/manifests, and publish atomically. A failed fetch or calculation should leave the currently served site untouched. The deploy job should consume a verified artifact rather than whatever happens to be in `website/` after a partial script run.

### 4. Replace Git as the primary generated-data store

Keep source, fixtures, and compact canonical snapshots in Git. Produce the large static site as a CI artifact or deploy output. This reduces repository churn while keeping historical pages reproducible.

### 5. Add operations and data provenance

Record run ID, season/week, CFBD client/API version, fetch time, request classification, model version, source checksum, output checksum, and failure stage. Add bounded retries for transient CFBD failures and surface quota/authorization errors distinctly.

## Recommended implementation order

1. **Security:** rotate and remove leaked credentials/artifacts.
2. **Compatibility:** create the CFBD v2 adapter and environment-based auth.
3. **Confidence:** make clean-checkout tests pass and add adapter/freshness/final-flow tests.
4. **Correctness:** refresh live data, preserve or deliberately version 1897 behavior, restore final history updates.
5. **Delivery:** stage, validate, and atomically publish a complete current-season site.
6. **Consolidation:** replace duplicate legacy fetch/calculation paths with one season snapshot pipeline.
7. **Scale/reproducibility:** move generated site output out of day-to-day source control and add provenance/backtests.

## Verification performed

- Inspected tracked source, configuration, project docs, Git history, current generated artifacts, and deployment workflows.
- Introspected the locked `cfbd==5.7.0` client’s authentication settings, method signatures, and `Game` fields without making an authenticated request.
- Ran the Python suite from a clean credential state: collection failed because of `cfb.api`/`config` imports.
- Re-ran the suite with a synthetic, non-secret credential module: 6 tests passed.
- Scanned tracked bytecode without printing string values and confirmed the `api_key` constant exposure.
- Scanned local site links: 4,146 references checked; 207 missing targets across 126 files.
- The frontend build was not re-run because npm is not available in the normal workspace shell; the compiled client artifacts already present in the repository were inspected.

No live CFBD request was made, no credential value was printed, and no production/deployment state was changed.
