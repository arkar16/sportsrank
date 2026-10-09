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

## Safe offline verification

Python 3.12 and the locked dependencies are required:

```sh
env -u CFBD_API -u CFBD_API_KEY uv sync --locked
env -u CFBD_API -u CFBD_API_KEY uv run --locked python -m unittest discover -s tests -v
env -u CFBD_API -u CFBD_API_KEY uv run --locked python -m compileall -q cfb tools scripts tests
env -u CFBD_API -u CFBD_API_KEY npm ci
env -u CFBD_API -u CFBD_API_KEY npm run build
```

These commands do not require a CFBD key and must not publish. Ordinary
verification uses portable Schema 2/3 fixtures and does not depend on private
or ignored caches. Static hosting build success does not establish strict
Release, link, or HTML acceptance. The production adapter reads the project-
only bearer token from `CFBD_API`, supplied through the BB environment variables
settings. The adapter does not fall back to `CFBD_API_KEY` or load a dotenv file.
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
The command detects the earliest missing postseason season; `--from-season`
can select an explicit chain start. It writes corrected rankings, results,
records, progression, historical summaries, separate reconstructed predictions,
and retrospective performance. Original input and saved spread/issued-forecast
bytes remain unchanged. [ADR-0024](docs/adr/0024-reconstruct-history-with-archived-membership.md)
owns historical membership and calendar compatibility. No API request occurs.

This output is an **unsealed local review site**, not a production release.
Inherited release metadata does not validate its corrections; do not deploy it.
The delivery task owns acceptance and subsequent authenticated release integration.

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

The remote publication entry point is the manually dispatched
`Publish validated static site to Firebase Hosting` workflow after review and
merge. It provides `prepare`, `seal-only`, `execute`, `reconcile`, and
`verify-only`. `normal`, `rollback`, and `correction` are sealed purposes;
each provider write follows the same two-dispatch procedure.

Local preparation independently validates the complete provider-backed baseline
and privately retained recovery inputs, then exports safe public pages and a
reviewed hash receipt. GitHub-hosted preparation verifies that receipt against
the committed site/code/configuration and input pins, packages `website/` plus
`firebase.json`, and binds those exact bytes to the actual merged commit. The protected `seal-only`
dispatch retains the package, provenance and attempt intent in immutable GitHub
releases and returns the exact canonical sealed reference. A separate fresh
owner-approved `execute` dispatch consumes that reference once, rechecks the
live predecessor, and sends the exact package to Firebase. Any existing
execution claim blocks replay, including after a timeout or lost runner.

The workflow requires the fixed repository, `refs/heads/main`, manual dispatch,
and run attempt `1`. Deployment credentials are available only behind the
owner-reviewed `production` environment. Authenticated GitHub commit/tree and
attestation checks precede runtime extraction, installation and secret use.
The protected job never rebuilds the site. Agents never supply production
approval. A staged package with `candidate_commit` absent or `null` cannot be
published; repeat preparation against the actual merged SHA after owner merge.

Retain the exact sealed reference, including its canonical final newline.
`reconcile` and `verify-only` retrieve immutable evidence through this reference
and observe Firebase without deploying, even when an execution failed before
uploading state. Actions artifacts are optional transport, not recovery
authority. A consumed or uncertain attempt requires reconciliation and a newly
sealed, separately approved attempt for any further write. Failed verification
pauses ordinary publication; there is no automatic rollback.

The committed `config/sr7-recovery-inputs.json` records trusted input
identities; it does not make raw bytes public or replace private retrieval.
Public releases and artifacts are limited to intended generated static output
and safe hashes/provenance. Raw CFBD source snapshots, the original-prepared
archive containing them and provider actor/auth metadata remain in private
local storage. New transport excludes raw Actions inputs and Git ancestry.
Historical public copies remain subject to a separate cleanup decision.
The owner selected this computer for private retention;
local source validation with a reviewed hash receipt is approved; implementation
and acceptance are tracked in SR-7. See the
[ADR-0018 retention gate](docs/adr/0018-retain-publication-evidence-in-github-releases.md)
and [publication runbook](docs/operations/2026-season-recovery-morning.md).
Concrete operational commands may read or write GitHub/Firebase; offline tests
use fakes and no CFBD key. Local hosting commands are emulator-only.

See the [publication runbook](docs/operations/2026-season-recovery-morning.md)
for exact transport, approval and recovery steps, and run
`uv run --locked python -m cfb.publication_cli --help` for current arguments.
Setup evidence belongs to SR-15 and final acceptance to SR-16; implementation,
review, merge, production approval, deployment and verification are separate
states. [ADR-0016](docs/adr/0016-bind-publication-to-verified-live-content.md)
and [ADR-0018](docs/adr/0018-retain-publication-evidence-in-github-releases.md)
record the accepted behavior and retention limits.

## Working with agents

[AGENTS.md](AGENTS.md) is the portable repository entry point.
[Project memory](docs/agents/project-memory.md) assigns one home to each kind
of information. Installed ask-matt skills guide design and delivery; BB owns
thread and environment state. Historical workflow records are retained in the
[frozen archive](docs/archive/codex-workflow/2026-09-10/README.md).
