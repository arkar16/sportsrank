# SportsRank

SportsRank publishes College Football CORS rankings as a barebones static HTML
website under `website/`. Firebase Hosting serves those files; the public site
has no Python, Node, database, or server runtime.

## Current status

The V4 human Gate 1 package is approved. The repaired V5 implementation and
offline verification are complete: 193 tests pass, CI-focused tests pass 10/10,
locked compile/dependency/npm/diff checks pass, and three V5 release validations
check 116/226/234 artifacts with zero structural failures. The final overlay has
142 added / 93 changed / 0 deleted paths; real Week 0 slates contain 4/5/8
games; all 35 affected pick'em rows are corrected; and 127 pre-2024 history
rows are preserved while the candidate carries 129 rows. Reviewer final
acceptance of the sealed V5 candidate was granted on 2026-09-09, and that exact
candidate has been promoted into the tracked `website/` tree; 335 inherited
legacy findings remain deferred. The original six-call bootstrap and prior
nine-row audit remain unchanged; this repair deployment made zero provider/network
calls. The actual-key scan found zero matches and zero read errors across all
13 scopes. Isolated and tracked promotion both matched the candidate. The live
Firebase site is unchanged, and Gate 2 production approval remains pending. See
the [V5 human review](.sportsrank/gate1-review-repairs-20260909/evidence/human-review-v5.md)
and the finalized workflow handoff.

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

The authoritative implementation and live-verification handoff is
[`docs/plans/2026-p0-recovery-and-backfill.md`](docs/plans/2026-p0-recovery-and-backfill.md).
The operator runbook remains paused at the separate Gate 2 production boundary;
Gate 1 approval applies to the repaired V5 package. The staged
interface and repair evidence are documented in
[`cfb/README.md`](cfb/README.md) and the active workflow handoff.

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

## Publication

After PR review and merge, public publication is the manually dispatched
`Publish validated static site to Firebase Hosting` workflow. Gate 1 approval
and V5 reviewer acceptance are complete; Gate 2 production approval remains
pending. Supply the
full 40-character `candidate_sha` for the merged candidate and `base_sha` for
the currently deployed commit. The workflow verifies ancestry and merge
status, validates and attests one content-addressed site artifact, and carries
that artifact into the protected `production` environment for publication.
The publish job does not check out a ref. Local hosting commands are
emulator-only; there is no direct publication shortcut.

## Project memory

- `CONTEXT.md` defines SportsRank domain language.
- `docs/adr/` records durable architectural decisions and rationale.
- `docs/plans/` records active implementation contracts.
- `agent_docs/` records current Codex Workflow execution state and evidence.

These sources are complementary. A contradiction among them or with executable
interfaces is a work blocker and must be reconciled before implementation or
publication continues.
