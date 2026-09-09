---
status: proposed
---

# Import legacy history with explicit provenance

Extend accepted ADR 0008 with a versioned SQLite import of preserved historical source tables and published ranking artifacts, keeping their origin and trust level distinct: an imported published result is evidence of what SportsRank published, not proof that its underlying games or calendar can be reconstructed. Historical reads must use an explicit supported repository path rather than sending pre-2024 seasons through the 2024–2026 provider calendar; recalculation requires sufficient source evidence, a supported calendar, and a verified carryover chain.

Preserve original artifact bytes, canonical URLs, source checksums, importer version, and unresolved identity or numeric ambiguities. Import every available weekly/final ranking, spread, result, and supporting artifact; do not reduce the archive to FINAL tables or silently replace missing values with zero. Keep SQLite as generation-time state and static Releases as the public interface, consistent with ADRs 0003, 0006, and 0008.

## Tradeoff

A provenance-aware import costs more than scraping HTML whenever a calculation runs, but permits indexed reads, repeatable migrations, and explicit correction history. Re-fetching all history would consume the Call Budget and would not reproduce the original published inputs; extending calendar support by guessing boundaries would create false confidence. Preserve imported legacy semantics separately until an independently verified recalculation is available.

## Cutover condition

An idempotent import, complete URL inventory, independent row reconciliation, representative historical replay, database backup/restore, and immutable Release restoration must pass before SQLite becomes authoritative for migrated data. Missing raw source data remains a visible recalculation limitation. Generated HTML remains in Git until ADR 0008's existing cutover conditions are satisfied.
