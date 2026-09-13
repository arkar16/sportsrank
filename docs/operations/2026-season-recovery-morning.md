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

## First-publication readiness

[ADR-0016](../adr/0016-bind-publication-to-verified-live-content.md) records the
accepted publication behavior. The current workflow still expects
`candidate_sha`/`base_sha` Git inputs and does not implement that behavior.
Do not run the existing dispatch procedure as a first-publication shortcut.
The sequence below is the delivery requirement, not a claim of readiness.

1. Capture the current Firebase live release/version, complete file inventory,
   recoverable content and serving configuration using authenticated read-only
   access. Preserve a content-addressed archive and capture evidence. An unknown
   original Git SHA is acceptable; missing content or unexplained differences
   are not. Recheck live identity after capture to exclude a mixed baseline.
2. Reconcile the capture with the historical repository archive and prepared
   recovery. Preserve every public path and unchanged inherited content. If
   the bases differ, prepare and validate a fresh offline overlay with correct
   provenance; keep the accepted recovery bytes/evidence preserved for comparison.
   No live CFBD allowance is implied.
   Apply [ADR-0017](../adr/0017-preserve-firebase-managed-resource-behavior.md)
   only to the two Firebase initialization resources: retain capture evidence,
   preserve availability/app identity and verify their generated configuration.
   Preserve the three never-live Schema 3 source snapshots as recovery inputs
   outside the rebuilt application tree, with verified source-checksum lookup.
3. Complete the publication implementation and configure `production` with the
   owner as required reviewer, self-review allowed, bypass disabled and branch
   `main` only. Scope deployment credentials exclusively to that environment
   and retire the legacy publication routes. Verify the actual configuration
   and credential capability before relying on the gate.
   Disable legacy automatic deploy/preview workflows and remove their usable
   repository-scoped deployment capability before the recovery merge. Audit
   shared key use before revoking a credential; do not affect unrelated services.
4. Enable and verify GitHub release immutability before publishing archival
   assets under [ADR-0018](../adr/0018-retain-publication-evidence-in-github-releases.md).
   Keep raw provider account metadata in private evidence; public derivatives
   include explicit redaction provenance and source hashes. Retain source
   snapshots and the original prepared recovery along with the live baseline.

## Gate 2 publication and recovery

After implementation, PR review and merge, use only the manually dispatched
**Publish validated static site to Firebase Hosting** workflow. Gate 2 production
approval remains separate from technical acceptance.

1. Validate the full merged `candidate_sha` against the verified baseline and
   package the site/configuration once. Bind its digest and provenance to the
   expected current Firebase live release/version; retain the archive and
   attempt evidence beyond temporary Actions artifact retention.
   Verify durable archive retrieval and hashes before deployment. Publish the
   artifact and intent as immutable assets, then preserve separate immutable
   deployment and verification records; never append to a sealed release or use
   its editable title, notes or latest label as authority.
2. Present the exact validated artifact for the owner's `production` approval.
   Re-read live identity after approval immediately before publication. A stale
   or unknown base stops the attempt. Coordinate the single publication path;
   the identity check does not lock out an uncoordinated external publisher.
3. Publish that exact artifact without re-checking out source. Record the
   provider release/version separately from the subsequent public-page checks.
   If the attempt is interrupted or its result is uncertain, reconcile Firebase
   state before any new publication.
4. On failed post-deployment verification, pause ordinary publication and retain
   the actual deployed identity plus failure evidence. The owner chooses recovery;
   automatic rollback is not authorized. A verification retry need not publish.
   A rollback or corrective deployment requires approval of its exact artifact.

The suspect release can remain live while recovery is investigated. Agents never
submit the owner's approval; GitHub account-based review cannot distinguish human
and agent actions made through the same account. Local hosting commands remain
emulator-only.
