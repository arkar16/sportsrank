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
