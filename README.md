# SportsRank

SportsRank publishes College Football CORS rankings as a barebones static HTML
website under `website/`. Firebase Hosting serves those files; the public site
has no Python, Node, database, or server runtime.

## Current recovery delivery

SportsRank BB task SR-7 (`bb tasks show SR-7`) owns the remaining recovery and
protected-publication work in PR #3. Earlier Gate 1 reports are historical;
fresh reconstruction and independent acceptance are required. The owner retains
merge and production approval. See the [recovery contract](docs/plans/2026-p0-recovery-and-backfill.md)
and [publication runbook](docs/operations/2026-season-recovery-morning.md).

## Repository navigation

| Area | Purpose |
| --- | --- |
| [CFB CLI/runtime](cfb/) | Supported college-football ranking command-line interface and runtime. See [cfb/README.md](cfb/README.md) for usage. |
| [Website](website/) | Permanent public static HTML artifacts and their supporting files. |
| `tests/` | Automated tests for the ranking runtime and published-site contracts. |
| [Docs](docs/) | ADRs (`docs/adr/`), plans (`docs/plans/`), and operational notes (`docs/operations/`). |
| `agent_docs/` | Current Codex Workflow project state and handoff records. |
| [Legacy NFL](legacy/nfl/) | Unsupported historical standalone NFL experiments and their preserved direct script entry point. |
| [`mainpage.py`](mainpage.py), [`webconfig.py`](webconfig.py) | Path-sensitive legacy batch helpers retained at the repository root. |

The recovery contract above owns product requirements. Use
[CFB CLI documentation](cfb/README.md) for supported recovery interfaces.

## Safe offline verification

Python 3.12 and the locked dependencies are required:

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
npm ci
npm run build
```

These commands do not require a CFBD key and must not publish. Ordinary
verification uses portable Schema 2/3 fixtures and does not depend on private
or ignored caches. The production adapter reads the project-only bearer token
from `CFBD_API_KEY`; the value must never enter source, command arguments,
tracked environment files, generated artifacts, logs, issues, or chat.

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

The intended publication path is the manually dispatched
`Publish validated static site to Firebase Hosting` workflow after PR review
and merge. Its current `candidate_sha`/`base_sha` interface still assumes a Git
publication base; the accepted [live-content decision](docs/adr/0016-bind-publication-to-verified-live-content.md)
requires implementation before the first publication. See the
[publication runbook](docs/operations/2026-season-recovery-morning.md) and
SportsRank task SR-7 for readiness and approval state.
The protected `production` job must deploy the exact validated artifact after
owner approval. Local hosting commands are emulator-only.

## Project memory

- `CONTEXT.md` defines SportsRank domain language.
- `docs/adr/` records durable architectural decisions and rationale.
- `docs/plans/` records active implementation contracts.
- `agent_docs/` records current Codex Workflow execution state and evidence.

These sources are complementary. A contradiction among them or with executable
interfaces is a work blocker and must be reconciled before implementation or
publication continues.
