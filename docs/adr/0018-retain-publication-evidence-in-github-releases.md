---
status: accepted
---

# Retain publication evidence in GitHub immutable releases

Use immutable GitHub release assets in the existing repository for approved
public site archives and safe hashes/provenance. Sanitized publication records
are public only after the private-input proof gate; raw CFBD inputs require a
separate private durable-retention path whose provider is not selected here.
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

The private storage provider, transport, and restore mechanism have not yet
been selected. Before publication can use retained inputs, an explicit gate
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
revision changes the data-retention policy; it does not choose a replacement
provider or transport and does not alter public URLs or CORS behavior.
