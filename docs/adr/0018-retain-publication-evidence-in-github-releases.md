---
status: accepted
---

# Retain publication evidence in GitHub immutable releases

Use immutable GitHub release assets in the existing repository for approved
public site archives and safe hashes/provenance. Sanitized publication records
are public only after the private-input proof gate; raw CFBD inputs require a
separate private retention path on the owner's computer for now.
Enable and verify immutability before publishing archive releases; temporary
Actions artifacts, editable release notes and the latest-release label are not
durable authority.

Seal the artifact and attempt intent before deployment, then preserve separate
immutable provider-result and verification records linked by attempt identity
and digests. Firebase remains authoritative for what is deployed under ADR-0016;
missing records require reconciliation, never an inference from workflow success.

Use two protected dispatches: seal the exact package and attempt intent first,
then execute its canonical immutable reference under a separate fresh owner
approval. The owner accepted this procedure during SR-14; the September 15
resume handoff records that approval. An execution runner can disappear before
uploading its result, so recovery must start from the reference supplied before
execution rather than a successful Actions run or state artifact. The operator
must retain that reference, including its canonical serialization.

Acquire an immutable single-use execution claim before the final live check
and provider write. Any existing claim blocks replay, including same-run
reentry. After a lost runner or uncertain write, reconcile actual provider
state; any further write uses a newly sealed attempt and fresh approval.
Verification and reconciliation make no deployment calls. This adds an
operator dispatch and permanent evidence per attempt in exchange for durable
recovery without blind retries. It does not provide an atomic lock against
unrelated Firebase publishers or repository administrators.

Public evidence is limited to intended generated static output and safe
hashes/provenance. Retain raw CFBD source snapshots and the original-prepared
archive that contains them in private durable storage, including nested copies
in candidate Git bundles or Git history. Provider actor metadata and
authentication material are private as well. A sanitized derivative is public
only after the private-input proof gate below establishes that it contains no
raw source snapshot or nested original-prepared content.

The owner accepts repository-based operational retention: immutable assets resist
modification, but whole-release or repository deletion remains possible. An
independent locked backup is deferred; this decision does not claim protection
against those deletions. Accepted in **Define the reviewable PR completion scope
and release evidence** (SR-5). See GitHub's
[immutability guarantees](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).

## Policy revision — 2026-09-24

The owner reverses the earlier interpretation that a separately retained
source-input archive could be public historical evidence. Raw CFBD source
snapshots and any original-prepared archive containing them must remain private
durable evidence. The same boundary applies to Git bundles and Git history when
they contain those snapshots, and to nested copies inside any archive or
transport. Public immutable releases remain appropriate for safe generated
static output and safe hashes/provenance; they are not a place to retain raw
inputs. Normalized snapshot copies retaining provider game/team records are
subject to the same private boundary; changing schema or removing credentials
does not turn source datasets into approved public ranking output.

The owner subsequently selected this computer for private retention for now.
The owner also approved local source validation with a reviewed, hash-pinned
receipt consumed by GitHub-hosted preparation.
Before publication can use retained inputs, an explicit gate
must prove all of the following against the exact pinned bytes and hashes:

1. An authorized private retrieval returns the expected source snapshots and
   original-prepared archive without exposing them through public Actions
   artifacts, logs, release assets, or generated pages.
2. Retrieval recomputes and checks the committed SHA-256 identities before
   preparation, and a restore drill reconstructs the same bytes from the
   private durable copy without changing the recorded hashes.
3. Access control, auditability, retention, and failure behavior prevent public
   readers and unapproved workflow contexts from retrieving the raw inputs;
   missing or mismatched private evidence fails closed.

The current prepare input path that downloads raw inputs from Actions artifacts
is incompatible with this revision and remains pending remediation. It must not
be described or treated as the approved private-retention solution. This
revision changes the data-retention policy and does not alter public URLs or
CORS behavior.

## Local retention selection — 2026-09-24

The owner authorized implementation and verification using this computer for
raw data for now. Keep exact original archives in a content-addressed local
store outside Git, temporary directories and BB thread storage. Owner-only
filesystem permissions, digest verification and an exact-byte restore drill
are required. Missing, corrupt or insecurely stored inputs stop preparation.
The local store does not provide an off-device backup or resilience to loss
of this computer; no cloud provider, paid dependency or automatic migration
is selected. Retain the prior copies until the new store passes restoration.

The owner approved full local source validation followed by a reviewed public
hash receipt. Existing recovery staging and source-derived validation run
privately. A separate validated export replaces source payloads with strict
provenance records while preserving reader pages and URLs. The reviewed receipt
binds public inventory, serving configuration, validator/runtime/workflow
source, trusted input identities, baseline and validation policy. Receipt
generation excludes the receipt itself from the source fingerprint to avoid
self-reference. Missing or mismatched bindings prevent hosted preparation.

GitHub-hosted preparation verifies the committed receipt against the actual
merged candidate, packages exact public-safe bytes and creates the hosted
attestation. That attestation proves receipt verification and exact packaging;
it does not assert that raw calculations ran on GitHub. Owner review/merge
accepts the local validation receipt. This avoids a new cloud store, signing
key or self-hosted runner while retaining the full local validation obligation.

Retained candidate runtime evidence contains only the authenticated current
tree and required commit identity, never ancestor payloads. Public records
retain private evidence hashes and reviewed receipts rather than retrievable
raw-input assets. Seal, execute and recovery do not fetch private input roles.
Fresh owner production approval, immutable safe evidence, exact merged
candidate binding, exclusive execution claims and live-predecessor checks
remain required. Reconciliation and verification never deploy.

Existing public release deletion and Git-history rewriting remain separate
owner decisions. No complete historical-exposure cleanup is claimed by the
prospective export change. Merge and production approval remain separate from
this implementation request.

## Source versions for the current-season extension — 2026-09-25

The owner requested current 2026 results in SR-17. Retain the original source
versions unchanged alongside the new native Schema 4 snapshot in private local
storage. Select each version by its verified source checksum; a year alone is
ambiguous when multiple versions exist. Rebuild cumulative validation evidence
against the expanded bundle instead of tolerating mismatched historical bundle
hashes. This preserves strict provenance and old public archive URLs while
allowing current results. The same local validation and public receipt boundary
applies; no additional public raw-data retention or fetch allowance is implied.

## Receipt-tree binding and validated used coverage — 2026-09-30

The current artifact-contract-4 receipt records the private source coverage that
was actually used to validate and derive the candidate. An available source-input
bundle is not evidence that every source in the bundle was used. The exporter
derives the canonical versioned `used_coverage` value only after trusted private
release validation succeeds, using the validated progression origin and run
identity rather than inferring coverage from the newest run.

For current or retained-2025 output, `used_coverage` identifies season, sport,
classification, the source run and origin, the source snapshot and checksum, and
the required progression, season-navigation and public-provenance paths. It must
also preserve the ownership of refreshes and corrections. A genuine pre-2025
release may use a versioned `used_coverage` descriptor whose `status` is `none`
when no 2025 progression coverage was used; this is a descriptor, not JSON null.
The presence of future-2025 inputs in the available bundle does not change that
status. The limited `none` case cannot authorize current or retained-2025 output.

Contract-4 receipts add top-level `used_coverage` and carry its canonical
identity wherever package or validation evidence binds the receipt. Literal
contract-2 and contract-3 release manifests, run dictionaries, public bytes and
legacy receipt shapes remain readable and byte-preserving. A legacy receipt can
be inspected or re-sealed under its historical rules, but it cannot authorize a
current contract-4 preparation; the exporter must generate a new current receipt
from the real validated export.

Offline export, verification and direct preparation establish draft
self-consistency only. They do not authenticate an alternate fully re-sealed
receipt, coverage value or package. Final hosted preparation takes its authority
from the actual `GITHUB_SHA`. The existing `_candidate_commit` resolver must use
that runtime value, and `_verify_candidate_tree` must authenticate the
corresponding Git tree.
Caller-supplied receipt bytes must equal the receipt blob at the fixed path
`config/sr7-local-validation-receipt.json` in `candidate_commit`; callers cannot
select another receipt path. The final binding must carry both the authenticated
receipt identity and the authenticated `used_coverage` identity; a missing,
replacement or mismatched receipt or coverage value fails closed. A draft receipt
or draft coverage value is never a final fallback. Package records, validated
records, rehydration and merged-candidate rebinds preserve those same identities.

When source coverage or the validated candidate changes, regenerate the receipt
through the real export and review the resulting safe site and receipt together.
Do not add the receipt digest to the trusted input manifest. The receipt is
excluded from the runtime source fingerprint and trusted configuration identity
so receipt generation does not create a circular hash.
These bindings establish verification evidence only. They grant no merge,
production-approval or publication authority, which remains with the owner.

## Successor baseline provenance — 2026-10-08

Under the owner-approved #27 repair and ADR-0016, a local receipt may additionally
carry a `verified_successor_baseline`. Keep its historical capture record and
safe derivative pins unchanged. The successor record identifies the prior sealed
reference, authenticated package, observed release/version, and all six distinct
immutable provider-result, verification and reconciliation record/source assets.
Derive identities from retrieved records, never from the local run manifest's
state or a green workflow. Authenticate original hosted preparation, its exact
current Git tree, package bytes, full inventory/configuration verification,
managed resources and all public smoke findings before issuing the successor.

The exporter must rerun genuine private source validation and preservation checks
before binding this record. Hosted preparation reauthenticates the existing
immutable evidence using its existing GitHub read boundary. No Firebase read,
capture transport, deployment credential or additional raw-data retention is
needed for this derivation. Fresh protected reconciliation and separate seal and
execute approvals remain required; the pinned archived audit cannot substitute
for those live checks. Legacy capture records and sealed references keep their
original interpretation.
