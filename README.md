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
env -u CFBD_API_KEY uv sync --locked
env -u CFBD_API_KEY uv run --locked python -m unittest discover -s tests -v
env -u CFBD_API_KEY uv run --locked python -m compileall -q cfb tools tests
env -u CFBD_API_KEY npm ci
env -u CFBD_API_KEY npm run build
```

These commands do not require a CFBD key and must not publish. Ordinary
verification uses portable Schema 2/3 fixtures and does not depend on private
or ignored caches. Static hosting build success does not establish strict
Release, link, or HTML acceptance. The production adapter reads the project-
only bearer token from `CFBD_API_KEY`; the value must never enter source,
command arguments, tracked environment files, generated artifacts, logs,
issues, or chat.

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

The only remote publication entry point is the manually dispatched
`Publish validated static site to Firebase Hosting` workflow after review and
merge. Its guard requires the fixed repository, `refs/heads/main`, the
`workflow_dispatch` event, and a first run attempt. The workflow offers
`prepare`, `execute`, `reconcile`, `verify-only`, `rollback`, and `correction`.
The protected job uses the `production` environment, reads GitHub approval and
artifact evidence with `actions: read`, and exposes deployment credentials only
inside that gated job. The owner must approve `production`; an agent cannot
approve for the owner. Setup and production protection remain readiness work
owned by SR-15, and final integrated evidence remains pending SR-16.

Preparation checks the complete verified live baseline and retained inputs,
packages `website/` plus `firebase.json`, and binds those exact bytes to the
actual merged commit SHA. The workflow transports the sealed package,
publication context, attestation, preparation-origin record, candidate Git
bundle and their hashes, plus the immutable execution source. The preparation
transport includes `package.tar.gz`/`package.sha256`, `package.json`/
`package.json.sha256`, `baseline.json`, `publication-context.json`,
`publication-attestation.json`, `preparation-origin.json`,
`publication-preparation-manifest.json`,
`baseline-public.tar.gz`, `baseline-sanitizer.json`,
`candidate.bundle`/`candidate.bundle.sha256`, and
`execution-source.tar.gz`/`execution-source.sha256`. A shell bootstrap must
authenticate the preparation/state workflow runs, verify the GitHub attestation
and candidate Git bundle against the authenticated commit/tree, and compare
regenerated execution-source bytes before extraction, dependency installation,
or secret authentication; a checksum or attestation produced only by the
transported runtime cannot establish trust. The GitHub attestation subject
binds the fixed
`arkar16/sportsrank/.github/workflows/firebase-hosting-publish.yml`,
`workflow_dispatch` on `refs/heads/main`, exact candidate/run/attempt
identities, and the package archive and package-record digests.
`preparation-origin.json` and hash files support context checks but are not
standalone proof. The protected rehydrate path verifies the attestation and
reads the actual Git object graph through `GitCommitTreeReader` before package
use. `baseline-public.tar.gz` is the allowlisted public baseline derivative,
paired with the schema-1 `baseline-sanitizer.json` record. The committed
schema-1 `config/sr7-recovery-inputs.json` manifest (`record_type`
`sr7_recovery_input_trust`) from the exact candidate tree pins the baseline
private/public/sanitizer digests and source-input identities;
downloaded artifact metadata cannot replace those pins. Raw `baseline.tar.gz`,
`source-inputs.tar.gz` is excluded from this publication transport. The
separately retained source-input archive is intentional public historical
evidence under SR-11; raw `baseline.tar.gz` and provider actor/auth metadata
remain private task evidence and are never uploaded here.
Because repository Actions artifacts are public, IDs, digests, `baseline.json`,
and accepted sanitized records are intentional public evidence.
The protected job then runs the cohesive CLI without checking out a ref or
rebuilding the package. A staged package before commit binding (for example one
with `candidate_commit` absent or `null`) is not eligible for an approved
operation. After owner merge, re-run preparation and revalidate the exact
merged SHA before presenting the package for approval.

The CLI is the portable operation surface; `--help` is the canonical argument
reference:

```sh
uv run --locked python -m cfb.publication_cli --help
uv run --locked python -m cfb.publication_cli prepare --help
uv run --locked python -m cfb.publication_cli execute --help
uv run --locked python -m cfb.publication_cli reconcile --help
uv run --locked python -m cfb.publication_cli verify-only --help
uv run --locked python -m cfb.publication_cli rollback --help
uv run --locked python -m cfb.publication_cli correction --help
uv run --locked python -m cfb.publication_cli reconcile-external --help
```

`prepare` produces the strict context consumed by later modes. `execute`
performs a normal approved publication. `reconcile` makes a fresh configured-
provider observation of a sealed attempt. `verify-only` retries verification
without a provider write. `rollback` and `correction` consume an exact package
only after the supplied predecessor context and run manifest have each been
fully reconciled afresh. `reconcile-external` is CLI-only for an unknown live
predecessor and requires a complete fresh capture plus its sanitized evidence;
archived history alone cannot establish origin. The concrete CLI adapters may
make live provider, GitHub archive, and approval reads, so these are operational
commands rather than offline tests. Local hosting commands are emulator-only.

Protected modes use `--context` and `--attempt-id`; the workflow also supplies
`--retrieval-directory` and `--result`. `reconcile` and `verify-only` require
`--attempt-manifest`. `rollback` and `correction` require both
`--prior-context` and `--prior-manifest`; `execute` may omit that pair only for
an initial publication. Mutating operations consume the
`sportsrank-preparation-<current-GITHUB_SHA>` artifact. Reconciliation and
verification consume the `sportsrank-publication-<run-id>` state artifact,
which retains `publication-preparation-manifest.json`, the original preparation
origin, and all allowlisted transport files alongside `publication-run.json`.
The state artifact is uploaded only after a successful protected run; durable
recovery when a run fails before that upload remains unresolved pending the
owner's two-dispatch decision, and no second-dispatch workaround is documented
as implemented. The protected runtime uses
`GITHUB_TOKEN` for run provenance/approval reads and `FIREBASE_ACCESS_TOKEN`
only inside the gated production job; `CFBD_API_KEY` is empty. Workflow
summaries expose sanitized target, operation, state, and digest identity.
Raw provider actor/auth evidence and the original `baseline.tar.gz` remain
private task evidence and are never uploaded as public artifacts. The
separately retained source-input archive is public historical evidence under
SR-11 and is outside this publication transport.

For the protected modes, the verified parser shape uses safe path and identity
placeholders; consult each `--help` output before execution:

```sh
uv run --locked python -m cfb.publication_cli execute \
  --context <publication-context.json> --attempt-id <new-attempt-id>
uv run --locked python -m cfb.publication_cli reconcile \
  --context <publication-context.json> --attempt-id <new-attempt-id> \
  --attempt-manifest <sealed-publication-run.json>
uv run --locked python -m cfb.publication_cli rollback \
  --context <publication-context.json> --attempt-id <new-attempt-id> \
  --prior-context <predecessor-context.json> \
  --prior-manifest <predecessor-publication-run.json>
```

These examples do not authorize a run or supply credentials. Readiness is
separate from planning and technical review; this documentation does not claim
that publication is authorized, deployed, or verified. See the [publication
runbook](docs/operations/2026-season-recovery-morning.md), [ADR-0012](docs/adr/0012-deploy-the-exact-validated-artifact.md),
[ADR-0016](docs/adr/0016-bind-publication-to-verified-live-content.md), and
[ADR-0018](docs/adr/0018-retain-publication-evidence-in-github-releases.md).

## Project memory

- `CONTEXT.md` defines SportsRank domain language.
- `docs/adr/` records durable architectural decisions and rationale.
- `docs/plans/` records active implementation contracts.
- `agent_docs/` records current Codex Workflow execution state and evidence.

These sources are complementary. A contradiction among them or with executable
interfaces is a work blocker and must be reconciled before implementation or
publication continues.
