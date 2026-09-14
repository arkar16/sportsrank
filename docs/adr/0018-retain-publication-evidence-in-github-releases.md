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
