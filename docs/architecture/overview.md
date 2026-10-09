# Current architecture

SportsRank generates CFB/FBS CORS rankings and complete static HTML/CSS. Firebase
Hosting serves `website/` without an application or database runtime. Optional
JavaScript progressively enhances the published content.

## Boundaries

- `cfb/season_source.py`, `season_snapshot.py`, `snapshot_cache.py`, and
  `request_meter.py` form the CFBD v2 source boundary. Explicit fetch/refresh
  operations create shared snapshots; calculation and release operations reuse
  them. SQLite currently holds the local request audit.
- `cfb/week_calendar.py` normalizes provider weeks using source-backed season
  calendars. `postseason_registry.py` holds the bounded historical correction
  registry; it is not an allowlist for future games. Schema 4 retains provider
  phase/playoff fields and separates calendar, registry, and migration provenance.
- `cfb/ranking_engine.py` owns CORS, records, spreads, and Season Carryover.
  `cfb/recovery.py` exposes explicit PRESEASON, numbered Week, and FINAL stages.
- `cfb/release.py` builds full-site overlays and validates independently derived
  artifact requirements. Promotion and remote publication are separate actions.
- `cfb/publication_cli.py` and the publication coordinator separate preparation,
  immutable sealing, single-use execution, reconciliation and verification.
  `cfb/firebase.py` implements the provider boundary. The protected workflow
  carries the reviewed package through separate sealing and execution dispatches;
  ADR-0016's live predecessor and separate deployment/verification states are
  implemented. Current operational acceptance belongs to the linked task records.
- `cfb/successor.py` authenticates existing immutable sealed package/audit evidence
  into a distinct successor baseline without reading Firebase. Local export keeps
  the historical capture as independent preservation authority and binds both in
  the reviewed receipt. Fresh protected reconciliation and execution remain in
  the publication coordinator.

Use [CFB CLI documentation](../../cfb/README.md) for commands and
[ADRs](../adr/README.md) for the reasoning and domain contracts. Current approval
and continuation state belongs in [current work](../work/current.md).

`cfb/historical_rebuild.py` is a separate offline archive adapter and reconstruction
runner under ADR-0024. It combines retained regular scores/membership with a
supplied historical games CSV into Season Snapshots, calls the existing ranking
engine, and writes an isolated retrospective review site. It does not fetch,
promote, create publication receipts, or weaken production carryover/Release
validation. Corrected-history performance is an explicit optional policy;
ordinary saved-forecast reporting is unchanged.

## Compatibility and deferred architecture

The modern source/ranking boundary coexists with legacy pandas/static-HTML
generators and Polars/Parquet/JSON paths. Trace callers and verify the affected
output paths before consolidating them. Root `mainpage.py` and `webconfig.py`
remain path-sensitive compatibility helpers. The unsupported NFL experiments
live in `legacy/nfl/`; they are not a model for new shared infrastructure.

ADRs 0003 and 0008 describe the intended Raspberry Pi scheduling and broader
build-time SQLite architecture. They are accepted direction, not implemented
capabilities. Pi/T7 provisioning, scheduling, backups, notifications, and moving
generated HTML out of Git remain deferred. The
[cleanup roadmap](../plans/repository-structure-cleanup.md) sequences further
changes after the first safe 2026 Publication. Comparable Scale calibration,
classification expansion, and a replacement for provisional Season Carryover
require their own design and validation.

The [history/storage proposal](../plans/history-storage-and-performance.md)
preserves the earlier design work. Proposed ADRs 0022/0023 extend the accepted
SQLite direction with provenance and reuse choices that remain unapproved.
