---
status: accepted
---

# Retain publication evidence in GitHub immutable releases

Use immutable GitHub release assets in the existing repository for permanent
site archives and sanitized publication records. This avoids introducing a
separate cloud storage service for the prepared recovery. Enable and verify
immutability before publishing archive releases; temporary Actions artifacts,
editable release notes and the latest-release label are not durable authority.

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

Public evidence contains site content and sanitized provenance. Retain original
provider records privately, including actor metadata, and bind public derivatives
to their source hashes with explicit redaction provenance. Never publish
authentication material.

The owner accepts repository-based operational retention: immutable assets resist
modification, but whole-release or repository deletion remains possible. An
independent locked backup is deferred; this decision does not claim protection
against those deletions. Accepted in **Define the reviewable PR completion scope
and release evidence** (SR-5). See GitHub's
[immutability guarantees](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).
