# Supported history and measured performance

## Intent and current status

The user endorsed addressing the legacy history regression, durable database storage, API efficiency, and code speed on 2026-09-09. ADR 0008 already accepts SQLite behind static Releases; ADRs 0015 and 0016 propose the additional provenance and reuse decisions. ADR 0014 is reserved for the independently owned postseason calendar repair. This is a design proposal, not a completed migration or benchmark result. Work is isolated from the Gate 1 recovery branch.

## Verified starting points

- PR #3's P2 comment identifies `cfb/calc.py:history_calc` calling `get_end_week` with the production snapshot service from 1897 onward. `cfb/week_calendar.py:require_supported_season` only accepts 2024–2026. The existing file-backed `get_end_week` branch is offline, but routing only that function around the service is insufficient: teams, games, records, and spreads also receive the service.
- `cfb/release.py:ReleaseBuilder.build` calls `season_rankings`, then calls `final_ranking` for FINAL; the latter calls `season_rankings` again. Eliminate that duplicate traversal while retaining FINAL completeness checks.
- Prior FINAL and history rows are parsed from HTML. `SeasonSnapshotService.get` locks and loads persisted state for each call. Both deserve measurement before choosing a caching strategy.
- Releases copy the complete Published Site, hash trees, and scan inherited HTML links. These preserve accepted invariants; their elapsed time and I/O cost have not yet been measured here.
- The Request Meter uses SQLite today; durable source and ranking storage from ADR 0008 is still unimplemented. The snapshot service already reuses cached teams and games, so optimization must distinguish provider traffic from local cache reads.
- PR #3 also has a new P1 postseason-calendar finding. `canonical_week` returns 1 for every provider Week 1 game after the August boundary, without a season-type argument. The reviewer reports bowl/playoff contamination of historical W1. This was sent to the original recovery task for offline verification; earlier Gate 1 acceptance must not be treated as resolving that finding.

## Delivery sequence and acceptance

1. Resolve the calendar correctness blocker before choosing golden ranking fixtures. Record provider season type and chronological checkpoint policy through the recovery repair; never benchmark correctness against a known contaminated output. Preserve prior evidence and distinguish superseded candidates.
2. Capture offline MacBook baselines for PRESEASON, numbered Week, FINAL, file regeneration from stored results, and representative multi-season replay. Report hardware/runtime, input digest and size, call counts, cache loads/parses, calculation counts, wall time, peak memory, and bytes read/written by stage. Separate cold and warm runs; repeat under the same conditions and retain medians. Current optimization targets the MacBook development workload; Pi targets are deferred.
3. Repair the entire legacy history data path through an explicit repository/adapter seam. Inventory archive coverage from 1897 onward and resolve missing evidence and the genesis rule so every historical season supports recalculation. Add offline tests for 1897, a pre-2024 season, and a supported modern season, plus full-range replay acceptance. Unsupported reconstruction fails before mutation or HTTP with the missing evidence identified; read-only import is an intermediate state, not completion.
4. Implement versioned SQLite migrations and an idempotent importer. Store stable identities and aliases, source revisions, raw provider fields including season type, normalized games, checkpoint rankings, spreads/results, and artifact provenance. Retain both raw and normalized values. Test import interruption/resumption, duplicate IDs, missing/NaN/zero distinctions, corrected inputs, foreign-key integrity, and restore from backup.
5. Remove duplicate FINAL calculation and repeated input parsing in one run. Compare all numerical fields, ordering, ties, carryover, and rendered meaning against independently checked fixtures. Rendering must not fetch. A warm replay uses zero provider calls; a games-only refresh with cached teams uses exactly the explicitly budgeted games request sequence, with no hidden teams or per-week requests.
6. Persist versioned ranking results for file regeneration. Treat optional cross-run checkpoint skipping as a separate decision after profiling and discussion; do not infer approval from the requirement to store results. Exercise invalidation for source/identity corrections, model/calendar changes, prior FINAL changes, and changed link targets. Rebuild dependent weeks and seasons when calculation inputs change. Keep independent numerical verification and full public-path preservation.
7. Validate migration before cutover: complete artifact inventory, URL compatibility, full historical recalculation, independently checked representative results, and local database/archive restore. Set MacBook latency/memory targets from the baseline before optimization implementation; no unsupported speedup claims. Pi runtime/memory targets, operational retention, and restore objectives wait for Pi provisioning. Keep publication subject to its existing separate approval.

## User requirements clarified on 2026-09-09

- Full historical coverage must support recalculation. The expected long-term reason to rerun historical calculations is a formula update; current fixes are also in scope. New-season processing remains normal work. Source or identity corrections must still invalidate affected results.
- File regeneration is required now and in the future. Separate it from recalculation so template/layout changes can render stored calculation outputs without provider calls or unnecessary formula execution.
- Optimize on the current MacBook first. Pi-specific performance and operational backup/restore objectives are deferred, while basic migration integrity and local restore verification remain required.

## Decisions still to settle

- Identity policy needs further discussion. Proposed approach: stable internal team IDs, source/provider ID mappings, season-scoped aliases and membership, and reviewable manual corrections with reason and provenance. A spelling correction need not merge two programs; a program merger or identity reassignment is a separate explicit decision. SQLite stores and enforces the chosen policy but cannot decide historical identity by itself. Preserve raw source values and revision history, and invalidate affected results when a correction changes calculation inputs.
- Whether measured workload warrants cross-run checkpoint skipping beyond storing results for rendering and reusing inputs within one process. Explain and agree the reuse/invalidation behavior before adopting it.

The user requests recalculation capability and current regeneration/fixes. Execute historical reruns once input coverage and correctness are verified; this does not grant additional provider requests or supersede the existing Call Budget. Removal of generated HTML from Git remains subject to ADR 0008's migration conditions.
