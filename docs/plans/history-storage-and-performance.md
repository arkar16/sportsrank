# Supported history and measured performance

## Intent and current status

The user endorsed addressing the legacy history regression, durable database storage, API efficiency, and code speed on 2026-09-09. ADR 0008 already accepts SQLite behind static Releases; ADRs 0014 and 0015 propose the additional provenance and reuse decisions. This is a design proposal, not a completed migration or benchmark result. Work is isolated from the Gate 1 recovery branch.

## Verified starting points

- PR #3's P2 comment identifies `cfb/calc.py:history_calc` calling `get_end_week` with the production snapshot service from 1897 onward. `cfb/week_calendar.py:require_supported_season` only accepts 2024–2026. The existing file-backed `get_end_week` branch is offline, but routing only that function around the service is insufficient: teams, games, records, and spreads also receive the service.
- `cfb/release.py:ReleaseBuilder.build` calls `season_rankings`, then calls `final_ranking` for FINAL; the latter calls `season_rankings` again. Eliminate that duplicate traversal while retaining FINAL completeness checks.
- Prior FINAL and history rows are parsed from HTML. `SeasonSnapshotService.get` locks and loads persisted state for each call. Both deserve measurement before choosing a caching strategy.
- Releases copy the complete Published Site, hash trees, and scan inherited HTML links. These preserve accepted invariants; their elapsed time and I/O cost have not yet been measured here.
- The Request Meter uses SQLite today; durable source and ranking storage from ADR 0008 is still unimplemented. The snapshot service already reuses cached teams and games, so optimization must distinguish provider traffic from local cache reads.
- PR #3 also has a new P1 postseason-calendar finding. `canonical_week` returns 1 for every provider Week 1 game after the August boundary, without a season-type argument. The reviewer reports bowl/playoff contamination of historical W1. This was sent to the original recovery task for offline verification; earlier Gate 1 acceptance must not be treated as resolving that finding.

## Delivery sequence and acceptance

1. Resolve the calendar correctness blocker before choosing golden ranking fixtures. Record provider season type and chronological checkpoint policy through the recovery repair; never benchmark correctness against a known contaminated output. Preserve prior evidence and distinguish superseded candidates.
2. Capture offline baselines for PRESEASON, numbered Week, FINAL, and representative multi-season replay. Report input digest and size, call counts, cache loads/parses, calculation counts, wall time, peak memory, and bytes read/written by stage. Separate cold and warm runs; repeat under the same conditions and retain medians. Repeat representative cases on the Pi before claiming production speedups.
3. Repair the entire legacy history data path through an explicit repository/adapter seam. Inventory archive coverage first. Add offline tests for 1897, a pre-2024 season, and a supported modern season. Unsupported reconstruction fails before mutation or HTTP with the missing evidence identified. Preserve inspection/import access even where recalculation is not yet justified.
4. Implement versioned SQLite migrations and an idempotent importer. Store stable identities and aliases, source revisions, raw provider fields including season type, normalized games, checkpoint rankings, spreads/results, and artifact provenance. Retain both raw and normalized values. Test import interruption/resumption, duplicate IDs, missing/NaN/zero distinctions, corrected inputs, foreign-key integrity, and restore from backup.
5. Remove duplicate FINAL calculation and repeated input parsing in one run. Compare all numerical fields, ordering, ties, carryover, and rendered meaning against independently checked fixtures. Rendering must not fetch. A warm replay uses zero provider calls; a games-only refresh with cached teams uses exactly the explicitly budgeted games request sequence, with no hidden teams or per-week requests.
6. Add persistent reuse only after profiling demonstrates value. Exercise invalidation for source corrections, model/calendar changes, prior FINAL changes, and changed link targets. Rebuild dependent weeks and seasons. Keep independent numerical verification and full public-path preservation.
7. Validate migration and operational recovery before cutover: complete artifact inventory, URL compatibility, historical reconciliation, database and archive restore, and observed Pi performance. Set numeric latency/memory targets from the baseline before optimization implementation; no unsupported speedup claims. Keep publication subject to its existing separate approval.

## Decisions still to settle

- Historical coverage that supports recalculation versus preservation/read-only import, especially the 1897 genesis and missing source evidence.
- Identity reconciliation and correction policy for legacy team names and provider IDs.
- Whether measured workload warrants persisted checkpoint caching beyond run-local reuse.
- Concrete Pi runtime and memory targets, backup retention, and restore objectives.

The storage and reuse proposals do not authorize further CFBD calls, a full historical rerun, or removal of generated HTML from Git. Those steps depend on evidence and the existing budget/migration contracts.
