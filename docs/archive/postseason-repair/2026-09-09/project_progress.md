# Project Progress

## Current Goal and Position

Heavy deployment `gate1_2026_postseason_window_20260909` completes the
reviewer's remaining Gate 1 acceptance fixes: the official 2026–27 postseason
window and acceptance of fresh authoritative provider postseason IDs without
historical recovery-registry membership. Local implementation and verification
are complete. The full offline suite passes 225/225 tests; all three rebuilt
Release stages validate and match V6 byte for byte. The revised V7 source and
evidence handoff reuses the frozen V6 candidate.

The isolated worktree stays on base `379caab4283dacc940516be75967a0d1bbe7a4b1`.
The reviewer owns conflict-aware integration onto the updated PR branch at
`4ed68400`, candidate promotion, and acceptance. Official schedule browsing
was authorized; this deployment made zero CFBD calls. No commit, push, rebase,
reset, main-checkout edit, or Gate 2 operation was performed here.

## Verified Repair

- The inclusive 2026 postseason calendar is December 12, 2026–January 25, 2027
  in America/New_York. Team names, seeds, rounds, and bracket slots do not
  determine the week. The first FBS bowl is December 15; the December 12 bound
  includes the FCS Celebration Bowl without changing classification filtering.
- Novel provider IDs pass normalization, cache persistence/reload, and Release
  validation with authoritative postseason phase. Missing or contradictory raw
  provider phase, unclassified late Week 1, and invalid recovery still reject.
- Six independent 2026 regressions cover CFP Weeks 16/18/20/22, placeholders,
  both date boundaries, unsupported seasons, and resealed provenance tampering.

- Exactly 92 verified postseason games move out of provider Week 1 into fixed
  America/New_York checkpoints 16–22. Phase is a game attribute; regular and
  postseason games may share a numbered Week without renumbering URLs.
- Provider phase/playoff metadata is retained. Historical recovery uses a
  pinned, exact-bound registry, explicitly distinct from provider phase.
- Schema 4 migration reverses only authorized corrections and checks the full
  original Schema 3 checksum. Missing or stripped origin and resealed tampering
  fail at snapshot and release boundaries.
- The full 2024 FINAL → 2025 FINAL → 2026 PRESEASON chain is rebuilt. An explicit
  staged correction-chain boundary preserves all frozen public files and old
  snapshot archives while recalculating every affected derived artifact.
- All three direct/immediate release validations pass with zero current
  failures. Independent candidate checks pass. Final diff is 75 added,
  232 changed, zero deleted; 335 inherited legacy findings remain deferred.

## Evidence and Continuation

Worktree: `/Users/aryakarnik/Developer/sportsrank-postseason-calendar-repair-20260909-src`.
Candidate: `.sportsrank/postseason-calendar-repair-20260909/releases-v6/2026-preseason/site`.
Review entry: `.sportsrank/postseason-calendar-repair-20260909/evidence/human-review-v7.md`.
Index: `.sportsrank/postseason-calendar-repair-20260909/evidence/index-v7.json`.
`latest_session_work.md` owns exact identities, deltas, verification paths, and
staging rationale. Original/new audit counts remain six/nine/nine; no CFBD calls.

Return the revised acceptance package, exact integration patch, and proposed PR update to the reviewer. Do not push, merge, change the
original Published Site, or deploy. P2 history/storage/performance proposals are
owned by the separate history task.
