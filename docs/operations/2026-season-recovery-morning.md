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

The canonical delivery contract is SportsRank task SR-7; setup evidence belongs
to SR-15 and final integrated acceptance to SR-16. Read their current evidence
before operating. Implementation, acceptance, merge, production approval,
deployment and verification are separate states. This runbook authorizes none
of them.

The workflow exposes five manual operations on `main`:

| Operation | Effect |
| --- | --- |
| `prepare` | Once the private-input gate is satisfied, independently validate privately retrieved inputs and the complete baseline; package site/configuration once and bind it to the actual merged commit. |
| `seal-only` | Retain the exact package, provenance and intent in immutable archives; return its canonical sealed reference. |
| `execute` | Under fresh owner approval, consume that exact reference once and publish its validated bytes. |
| `reconcile` | Retrieve sealed evidence and freshly observe actual Firebase state without deploying. |
| `verify-only` | Recheck the observed deployment without deploying. |

`normal`, `rollback`, and `correction` are immutable purposes of a sealed
attempt. Every purpose uses seal-only followed by a separate approved execute
dispatch. There are no one-shot rollback/correction publication routes. The
CLI-only `reconcile-external` operation requires a complete fresh capture and
sanitized retained evidence for an unknown live predecessor. It emits a canonical
`external_predecessor_reference` in its result. Supply that reference (or the
complete canonical result) as `prior_external_reference` to both sealing and
execution; the CLI equivalent is `--prior-external-reference`. Retrieval verifies
its immutable capture, sanitizer and observation before freshly checking live state.

Use `uv run --locked python -m cfb.publication_cli --help` and each operation's
`--help` for exact arguments. Concrete commands may read or write GitHub and
Firebase; they are operational interfaces, not offline test commands.

## Trusted preparation and transport

Preparation must identify the actual merged candidate SHA. A staged package
with `candidate_commit` absent or `null` is ineligible. After owner merge,
repeat preparation and independent validation against the actual merged SHA,
even when its site bytes match the reviewed branch.

The existing `sportsrank-preparation-<GITHUB_SHA>` design describes exact
package and record bytes/hashes, `baseline.json`,
`publication-context.json`, `publication-attestation.json`,
`preparation-origin.json`, `publication-preparation-manifest.json`, a
candidate Git bundle/hash, and a runtime source archive/hash. That Actions
input transport is **incompatible with the 2026-09-24 retention policy and
pending remediation** whenever it carries raw CFBD snapshots, an
original-prepared archive, or Git bundles/history containing them. The same
boundary applies to nested copies in archives and transports. It must not be
used as the approved source of raw inputs until the private-input gate below
has passed.

The committed schema-1 `config/sr7-recovery-inputs.json`
(`sr7_recovery_input_trust`) records expected identities for private baseline,
source-input, sanitizer, and related evidence; hashes and downloaded metadata
cannot authorize or replace private byte retrieval. The private storage
provider, transport, and restore mechanism remain undecided.

Before extracting runtime code, installing dependencies or using credentials,
the trusted workflow bootstrap verifies authenticated GitHub run provenance,
package and package-record attestation, the actual candidate commit/tree and
bundle, and regenerated execution-source bytes. The attested manifest binds
`arkar16/sportsrank/.github/workflows/firebase-hosting-publish.yml`,
`workflow_dispatch` on `refs/heads/main`, and exact candidate/run/attempt
identities. Hash files and preparation-origin metadata support these checks;
they do not establish trust alone. The runtime reauthenticates provenance and
Git objects before package use. The protected job never checks out a mutable
source ref or rebuilds the site package.

Public transport may contain intended generated static output and safe
hashes/provenance only. Raw CFBD source snapshots, `baseline.tar.gz`,
`source-inputs.tar.gz`, and any original-prepared archive containing them remain
private durable evidence. Candidate Git bundles or Git history are private
whenever they contain that raw data; provider actor/auth metadata and
credentials are private as well. A sanitized derivative is eligible for public
retention only after the private-input gate proves that it contains no raw
snapshot or nested original-prepared content. Never upload raw inputs or
account metadata to public Actions artifacts, logs, release assets, or pages.

Normalized snapshots retaining provider game/team records are also private;
schema conversion alone is not sanitization. Audit current packages and Git
history as well as new uploads. Existing exposure requires an explicit
remediation decision before claiming this boundary is satisfied.

### Private-input proof gate

Before `prepare` can be approved, document an owner-approved private durable
store and verify authorized retrieval, SHA-256 checks against the committed
pins, a restore drill that reproduces the exact bytes, and access
control/audit behavior that blocks public readers and unapproved workflow
contexts. Missing, inaccessible, mismatched, or unexpectedly exposed private
evidence fails closed. Until that evidence exists, the current `input_run_id`
and `input_artifact_name` Actions-artifact path is a pending implementation
gap, not an approved transport.

## First-publication readiness

1. Confirm the current Firebase project/site/live identity, complete content
   inventory and serving configuration through authenticated read-only access.
   Capture observations before and after collection must agree. The original
   Git SHA may remain explicitly unknown under ADR-0016; missing bytes,
   unexplained differences or a mixed capture block use of the baseline.
2. Independently reconstruct and validate the retained recovery against both
   captured live bytes and original prepared output. Preserve every application
   URL and exact unowned/pre-2024 bytes. Only `/__/firebase/init.js` and
   `/__/firebase/init.json` use ADR-0017's provider-generated-byte exception;
   retain capture evidence and verify public availability and app identity.
   Preserve original Schema 3 inputs outside the rebuilt public tree with
   source-checksum lookup. No new CFBD allowance is implied.
3. Verify actual `production` protection: required owner review, self-review
   allowed, administrator bypass disabled, main-only deployment, dedicated
   publisher capability confined to the environment, legacy routes/credentials
   retired, and immutable release protection enabled. Recheck dated setup
   evidence instead of assuming YAML establishes account protection.
4. Finish SR-16 acceptance, including strict release/link/HTML preservation,
   exact-head review and real immutable archive retrieval proof. Obtain owner
   merge, then prepare and validate the actual merged candidate.

## Seal, approve and execute

The workflow guard requires the fixed repository, `refs/heads/main`, manual
`workflow_dispatch`, and run attempt `1`. Production jobs require the owner's
`production` approval. Agents never submit approval. `GITHUB_TOKEN` supplies
provenance/archive/approval access; Firebase credentials are available only
inside the protected job, and `CFBD_API_KEY` is absent.

1. Select the exact validated preparation, expected baseline and purpose.
   Normal successors, rollback and correction need freshly reconciled verified
   predecessor evidence. Only the explicitly unknown-historical first baseline
   may omit it, using `initial_baseline: true` on both dispatches (CLI
   `--initial-baseline`). This exception is pinned to the accepted historical
   baseline record; an unknown source on a later capture does not qualify.
   Provider reads needed to establish that predecessor happen
   before sealing; the seal module itself performs zero Firebase reads/writes.
2. Run `seal-only`. It retains and verifies immutable package, validation,
   preparation and attempt-intent evidence before returning a canonical
   `SealedAttemptReference`. Retain the complete JSON value including its one
   final newline. Preserve it outside the execution run; an Actions artifact
   may duplicate it but is not recovery authority. Do not derive a replacement
   from a latest-release label, guessed tag or successful workflow record.
3. Start a separate fresh `execute` dispatch with that exact reference and the
   same purpose. Review its candidate/package/baseline identities before owner
   approval. The sealing run cannot execute its own attempt. Execution retrieves
   and reauthenticates exact immutable evidence, validates target and predecessor,
   and creates an immutable single-use claim. Any existing claim blocks replay,
   including identical same-run reentry or a new-run retry.
4. After approval and archive work, the coordinator re-reads live identity
   immediately before the write. Stale or unknown state stops publication.
   Firebase receives the exact validated package and serving configuration.
   Record deployment identity separately from verification and retain each
   later record in a separate sealed archive. Never append to a sealed release.
5. Verify full inventory/configuration correspondence, both managed resources,
   and representative public pages: homepage, 2023 FINAL, 2024/2025 FINAL,
   2026 PRESEASON and Week 0. A failed check pauses ordinary publication.

## Supplying the exact reference

After merge and the required review, preserve the file emitted by `seal-only`
as `sealed-reference.json`. Send workflow inputs through JSON stdin so the
canonical reference's final newline survives. Do not use shell command
substitution around `cat`, which removes trailing newlines.

For an initial normal execution, the dispatch shape is:

```sh
python3 -c 'import json, pathlib; print(json.dumps({"operation": "execute", "purpose": "normal", "initial_baseline": True, "sealed_reference": pathlib.Path("sealed-reference.json").read_text()}))' |
  gh workflow run firebase-hosting-publish.yml \
    --repo arkar16/sportsrank --ref main --json
```

This creates a fresh run; it does not approve the production environment.
For successors or recovery, also supply the exact prior reference and required
predecessor evidence described by the workflow inputs. For observation only,
select `reconcile` or `verify-only` with the same sealed reference. Check the
workflow inputs and CLI `--help` before execution; these examples are not
production authorization.

## Interrupted attempts and recovery

Use the same canonical reference with `reconcile` or `verify-only`, even if the
execution run failed, was cancelled, lost its runner or never uploaded state.
These operations recover exact intent evidence and freshly observe Firebase;
they perform zero deployments. Workflow success or missing receipts never
establish what is live.

An execution claim consumes the attempt even if the process died before the
provider call. If live state still matches the predecessor, reconciliation may
record a pre-write interruption, but does not make the claim reusable. If the
candidate is live, verify it without redeploying. If state or evidence remains
uncertain, pause. For any further write, seal a new exact attempt against the
reconciled predecessor and obtain fresh owner approval.

A failed public check does not restore the previous site. The suspect release
may remain live while the owner chooses recovery. Rollback/correction requires
its exact recovery artifact, freshly verified predecessor and the same
seal/execute protections. There is no automatic rollback or blind retry.
Unknown external content requires complete fresh capture, reconciliation and
validation before it becomes a verified predecessor.

A Firebase read cannot lock out unrelated publishers. GitHub immutable archives
cannot prevent whole-release or repository deletion. Coordinate the single
publication path and retain these accepted residual limits; neither mechanism
is a cross-system atomic lock. GitHub/Firebase read unavailability fails closed.
Local hosting commands remain emulator-only.
