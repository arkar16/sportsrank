# 2026 recovery publication runbook

Before any publication action, read the authoritative SportsRank task SR-7 (`bb tasks show SR-7`) and
verify the actual merged candidate SHA and complete live baseline. Historical acceptance
and artifact identities are indexed in [historical Gate 1 reports](../../agent_docs/latest_session_work.md).
This runbook is a procedure, not an approval or a current verification report.

The implementation and test contract is
[`../plans/2026-p0-recovery-and-backfill.md`](../plans/2026-p0-recovery-and-backfill.md).
The actual-key credential scan is recorded in the repair evidence and required
no API call. The three-call metadata-repair allowance is spent; preserve the
original six-call bytes and do not rerun that sequence or issue further CFBD
requests. Continue from the refreshed root with cached-input review and
validation only. Calendar semantics and migration provenance are owned by
[ADR-0014](../adr/0014-preserve-postseason-chronology.md). Use the supported
cache-only migration/rebuild interfaces in [CFB CLI documentation](../../cfb/README.md).
The completed recovery used immutable inputs under
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data/snapshots`.
The historical output/evidence root was
`.sportsrank/postseason-calendar-repair-20260909/{data,releases-v6,evidence}`;
its availability is recorded in the evidence index. Confirm the intended inputs,
new output directory, and task scope before running a new migration.

## Publication interface and current state

[ADR-0016](../adr/0016-bind-publication-to-verified-live-content.md) records the
accepted provider-backed behavior. The integrated path remains subject to SR-15
production-setup evidence and SR-16 final acceptance. This runbook is a
procedure, not authorization; it does not claim that publication is authorized,
deployed, or verified.

The cohesive interface is `python -m cfb.publication_cli`; its `--help` output
is the canonical argument reference. The CLI modes are:

- `prepare` validates the complete captured baseline and retained recovery
  inputs, packages `website/` with `firebase.json`, and binds those bytes to the
  actual merged commit SHA in a strict preparation context.
- `execute` performs one owner-approved normal publication from that exact
  package.
- `reconcile` makes a fresh configured-provider observation of a sealed attempt
  and records the result.
- `verify-only` rechecks a recorded deployment without a provider write.
- `rollback` and `correction` publish an owner-approved recovery package only
  after the predecessor context and sealed run manifest have been fully
  reconciled afresh through the configured provider.
- CLI-only `reconcile-external` binds an unknown live deployment to a complete, freshly
  captured provider baseline. It requires the capture derivative, sanitizer
  record and immutable archive references; archived history alone is not origin
  proof.

The workflow exposes `prepare`, `execute`, `reconcile`, `verify-only`,
`rollback`, and `correction` as `workflow_dispatch` choices. Preparation
transports the sealed `package.tar.gz`/`package.sha256`, `package.json`/
`package.json.sha256`, `baseline.json`, `publication-context.json`,
`publication-attestation.json`, `preparation-origin.json`,
`publication-preparation-manifest.json`, candidate Git bundle and its hash, and
`baseline-public.tar.gz`, `baseline-sanitizer.json`, immutable execution-source
archive with its hash. Mutating preparation uses
`sportsrank-preparation-<GITHUB_SHA>`; reconciliation and verification use
`sportsrank-publication-<RUN_ID>` state that retains
`publication-preparation-manifest.json`,
original preparation origin, all allowlisted transport files, and
`publication-run.json`.
The preparation manifest is the GitHub-attested subject binding
`arkar16/sportsrank/.github/workflows/firebase-hosting-publish.yml`,
`workflow_dispatch` on `refs/heads/main`, exact candidate/run/attempt
identities, and package archive/package-record digests. The
protected job's trusted bootstrap authenticates the preparation and state runs,
verifies the candidate bundle as the actual Git object graph, and regenerates
and compares the execution-source archive before extracting, installing the
runtime, or authenticating the secret. `preparation-origin.json` and hash files
support context checks but are not standalone proof; a checksum or attestation
produced only by the transported runtime cannot establish trust. The rehydrate
path verifies the attestation and actual `GitCommitTreeReader` before package
use. `baseline-public.tar.gz` is the allowlisted public baseline derivative,
paired with the schema-1 `baseline-sanitizer.json` record. The committed
schema-1 `config/sr7-recovery-inputs.json` manifest (`record_type`
`sr7_recovery_input_trust`) from the exact candidate tree independently pins
the baseline private/public/sanitizer and source-input
identities; downloaded artifact metadata cannot replace those pins.
`source-inputs.tar.gz` is excluded from this publication transport; its
separately retained archive is intentional public historical evidence under
SR-11. Raw `baseline.tar.gz` and provider actor/auth metadata remain private
task evidence and are never uploaded here. Public IDs, digests, `baseline.json`,
and accepted sanitized records are intentional evidence in the repository's
public Actions artifacts.
The job then invokes the CLI without checking out a source ref or rebuilding the
package. A staged package before merged-commit binding (including one with
`candidate_commit` absent or `null`) is not eligible for execution.

For a normal successor, a predecessor may be supplied. When supplied, the CLI
requires both `--prior-context` and `--prior-manifest`, retrieves the sealed
predecessor evidence, performs a fresh full reconciliation through the
configured provider, and rejects the successor unless the predecessor is
verified. `rollback` and `correction` always require that pair. The workflow's
predecessor transport uses `predecessor_run_id` and
`predecessor_artifact_name`; missing or incomplete recovery transport fails
closed.

Protected CLI modes use `--context`, `--attempt-id`, `--retrieval-directory`,
and `--result`; `reconcile` and `verify-only` also require
`--attempt-manifest`. `execute` may omit the predecessor pair only for an
initial publication. Workflow summaries expose sanitized target, operation,
state, and digest identity. The state artifact is uploaded only after a
successful protected run; durable recovery after a failure before that upload
remains unresolved pending the owner's two-dispatch decision, and no workaround
is implemented. Raw provider actor/auth evidence and the original
`baseline.tar.gz` remain private task evidence and are never uploaded as public
artifacts. The separately retained source-input archive is public historical
evidence under SR-11 and is outside this publication transport. The protected
runtime uses `GITHUB_TOKEN` for provenance/approval reads,
`FIREBASE_ACCESS_TOKEN` only inside production, and no `CFBD_API_KEY`.

## First-publication readiness

Complete these steps before presenting a package for production approval:

1. Capture the current Firebase release/version, complete public file inventory,
   recoverable content, and serving configuration through authenticated
   read-only access. Preserve a content-addressed archive and capture evidence.
   An unknown original Git SHA is acceptable; missing content or unexplained
   differences are not. Re-read live identity after capture to exclude a mixed
   baseline. Historical archives can support reconciliation but cannot establish
   the current live origin by themselves.
2. Reconcile the capture with the historical repository archive and prepared
   recovery. Preserve every public path and unchanged inherited byte. If the
   bases differ, build and validate a fresh offline overlay with explicit
   provenance while retaining the accepted recovery bytes for comparison. No
   live CFBD allowance is implied. Apply [ADR-0017](../adr/0017-preserve-firebase-managed-resource-behavior.md)
   only to the two Firebase initialization resources, preserving their
   availability and app identity with verified generated configuration. Keep
   the three never-live Schema 3 snapshots outside the rebuilt application tree
   with source-checksum lookup.
3. Complete SR-15 setup evidence: verify the `production` environment's actual
   owner review requirement, self-review and bypass settings, `main` restriction,
   credential isolation, retired automatic/alternate publication routes, and
   immutable release configuration. YAML describes intended behavior; setup
   acceptance must confirm the provider configuration and capability. Audit
   shared key use before revoking any old credential.
4. After the owner merges, prepare and independently revalidate the exact merged
   candidate SHA against the verified baseline. Keep the package, context,
   attestation, source transport, and sealed archive identities together for
   review. A pre-merge or unbound staged package cannot be presented for
   approval.

## Gate 2 publication and recovery

After SR-15 setup evidence, SR-16 integrated acceptance, PR review, and owner
merge, use only a fresh manually dispatched **Publish validated static site to
Firebase Hosting** run. The workflow guard requires the fixed repository,
`refs/heads/main`, the `workflow_dispatch` event, and run attempt `1`. The
protected job uses the `production` environment and `actions: read` to retrieve
artifacts and authenticated GitHub run/approval evidence. The coordinator
requires the owner's `production` approval; agents never submit it.

1. Select the exact preparation transport and verify its hashes, candidate SHA,
   target, context, and attestation. Persist immutable package and intent
   evidence before any provider write under
   [ADR-0018](../adr/0018-retain-publication-evidence-in-github-releases.md).
   Preserve separate provider-result and verification records and verify archive
   retrieval, hashes, and immutability. Mutable release notes, labels, or
   workflow success do not establish state.
2. Present that exact package for the owner's `production` approval. Immediately
   after approval, re-read the expected live release/version and reject a stale
   or unknown predecessor. A provider read does not lock out an external
   publisher, so coordinate the single authorized publication path.
3. Run `execute`, `rollback`, or `correction` through the transported CLI. The
   provider receives the already validated package and configuration; source is
   not checked out and the package is not rebuilt. Record the provider
   release/version separately from subsequent public-page verification.
4. If the attempt is interrupted or its result is uncertain, stop ordinary
   publication and run `reconcile` against the sealed attempt. Retain the
   observed or uncertain identity; never infer that the old site remains live.
   The current state artifact is emitted only after a successful protected run;
   durable recovery when that upload is skipped remains unresolved pending the
   owner's two-dispatch decision.
   On failed public checks, pause ordinary publication. `verify-only` may retry
   verification without another provider write; a rollback or correction is a
   new exact package with fresh predecessor reconciliation and owner approval.

The suspect release may remain live while recovery is investigated. Local
hosting commands remain emulator-only.
