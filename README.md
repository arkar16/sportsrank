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

## CORS backforecast performance

Run model-performance tracking from existing local rankings and final results:

```sh
uv run --locked python -m scripts.backforecast 2026
open .sportsrank/backforecasts/2026/report.html
```

The default range covers all locally available result weeks and grades games
with final scores. Week 0 uses PRESEASON ratings; every later week uses the
preceding week's CORS ratings.
The command uses the current CORS matchup and grading functions and reports
straight-up results, CORS line coverage (including pushes), MAE and RMSE. It
writes `games.csv`, `weekly.csv`, `report.json` and `report.html` below the output
directory. FBS-versus-FBS games with final scores are evaluated; omitted
classifications and missing scores are counted in the weekly CSV/JSON.

For Week 5 alone or a historical season:

```sh
uv run --locked python -m scripts.backforecast 2026 --from-week 5 --through-week 5
uv run --locked python -m scripts.backforecast 2025
uv run --locked python -m scripts.backforecast 2024 --output .sportsrank/backforecasts/2024
```

Inputs come from `website/` by default (`--website` selects another local site).
Missing preceding rankings or invalid data produce an error. This is a local
performance report using retained checkpoint ratings; no ratings are recomputed,
CFBD calls made, site files changed or publication performed. Issued-forecast
tracking remains independent.

This command fits a manually run T3 project script named **Backforecast performance**.
Use `uv run --locked python -m scripts.backforecast 2026` as its command; leave
automatic worktree/settle execution disabled.

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
