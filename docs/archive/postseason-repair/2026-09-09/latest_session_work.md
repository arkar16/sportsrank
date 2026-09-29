# Latest Session Work

## Current Acceptance Follow-up

Deployment `gate1_2026_postseason_window_20260909` is locally complete. It
adds the official inclusive America/New_York postseason window December 12,
2026–January 25, 2027 and fixes historical registry membership incorrectly
blocking fresh provider-classified postseason IDs. The first FBS bowl is
December 15; the accepted broader Bowl Season bound includes the December 12
FCS Celebration Bowl. Phase and date choose the canonical week; names, seeds,
round labels, and bracket slots do not. The existing lattice and 2024/2025
recovery registry remain unchanged.

Fresh verification passes 225 tests in 17.978 seconds with the key unset and
provider access blocked; compilation passes. Six independent 2026 tests cover
normalization, persistence/reload, public Release validation, unknown matchups,
inclusive/outside boundaries, and resealed missing/contradictory raw-phase
rejection. The prior 2026-unsupported assertion now checks unsupported 2027.
The initial fresh-ID cache failure is retained in `tester/initial-focused.*`.
The fix does not relax exact historical recovery or unclassified late-Week-1
rejection. Test evidence is under `verification/2026-window/tester/`, especially
`full-suite-final-v2.*`, `focused-final.*`, and
`final-source-test-hashes.sha256`.

Fresh reconstruction after both production changes validates all three direct
and immediate stages and matches the V6 site and release.json bytes exactly:
zero added, changed, or deleted files. The final site still has 11,634 files
and manifest SHA256
`4e31e2e0c367ffecffb78a78b3887d3ed986a13ae3f4a2d0615358174fba7c9a`.
Evidence: `verification/2026-window/equivalence-v7-final.json`; reconstruction
root: `verification/2026-window/releases-v7-equivalence-final/`. The main and
acceptance reviewer inspected the decisive equivalence report. V7 denotes the
revised source/evidence handoff; the frozen V6 candidate remains reusable.
All paths in this paragraph are relative to the isolated worktree's
`.sportsrank/postseason-calendar-repair-20260909/`.

Official source observations are in
`verification/2026-window/official-schedule-evidence-v7.{json,md}`. Browsing
these public schedules was authorized. No CFBD calls occurred; reconstruction
reports key unset, transport blocked, and zero transport calls. The original
PR checkout, frozen candidate, and prior evidence remain untouched here.

The reviewer advanced the separate PR checkout to `4ed68400` through cleanup
work. This isolated branch stays on `379caab4283dacc940516be75967a0d1bbe7a4b1`.
No rebase, reset, commit, push, or main-checkout edit was performed. Current
continuation: return `evidence/human-review-v7.md`,
`evidence/proposed-pr-update-v7.md`, and the exact integration patch/index to
the reviewer for conflict-aware integration preserving the cleanup docs.
Gate 1 acceptance and Gate 2 publication remain reviewer/user decisions.
The detailed prior V6 historical correction and its unchanged identities
follow below; current test evidence is the 225-test result above.

## Prior Postseason Repair

Deployment `gate1_postseason_diagnosis_20260909` owns the PR #3 P1 repair,
cache-only rebuild, fresh evidence, and proposed PR update before any push.
Worktree: `/Users/aryakarnik/Developer/sportsrank-postseason-calendar-repair-20260909-src`;
branch `feature/postseason-calendar-repair-20260909-src`, base
`379caab4283dacc940516be75967a0d1bbe7a4b1`. The original PR checkout and accepted
V5/cache/evidence remain frozen. Official schedule browsing for provenance is
authorized; no CFBD call, push, merge, or Gate 2 operation is authorized.

The read-only reproducer in `/tmp/sportsrank-postseason-repro.8h3VoI/reproduce.py`
fails the exact assertion: 46 December/January postseason provider-W1 games per
2024/2025 season map to canonical W1. `impact-report.json` confirms the actual
W1 outputs contain them and FINAL recomputation matches the contaminated V5
weekly chain. The verified corrected FINAL and carryover deltas are recorded below.
Consumed input hashes remain unchanged.

Reviewer-approved design uses a continuous timezone-aware Week 1 calendar
lattice, with postseason as a game attribute rather than a whole-week label.
An exact-bound recovery registry may classify only the 92 known historical
rows, with official evidence and explicit recovery provenance. Future provider
inputs retain phase/playoff metadata and fail closed when required phase is
missing or unsupported. Schema, correction registry, and calendar are separately
versioned. Original inputs remain immutable; new roots hold derived data and
the rebuilt 2024 FINAL → 2025 FINAL → 2026 PRESEASON chain.

The official evidence audit verified all 92 registry rows (35 bowls and 11 CFP
games per Season) against NCAA team/local-date records and CFP schedules, with
no ambiguous or unverified entries. Its exact input bindings and source limits
are retained at
`/tmp/sportsrank-postseason-evidence-20260909-0835/proposed-postseason-registry.json`.
The proposal's `canonical_week=1` is original bug evidence, not the corrected
target. Corrected dates derive Weeks 16–22 from the fixed lattice. Declared
postseason windows are 2024-12-14 through 2025-01-20 and 2025-12-13 through
2026-01-19, inclusive in America/New_York. The follow-up above adds the now-published official 2026–27 window.

Production changes are frozen. Twenty independent regressions pass, including
early-ranking isolation, fresh provider capture/reload, exact registry checks,
origin stripping, and resealed snapshot/release tampering. Deep migration
validation reverses only the authorized corrections and verifies the complete
original Schema 3 checksum. Production paths require either valid provider
phase or that pinned migration origin; canonical non-provider fixtures remain
compatible. The final full offline suite passes 218/218 tests. Compile,
dependency, npm build, hosting, and diff checks pass. Legacy archive values
remain unchanged on reconstruction; only legacy fixture constructors were
adjusted to omit new null Schema 4 fields while retaining every assertion.

The fresh V6 chain is built and frozen at
`.sportsrank/postseason-calendar-repair-20260909/releases-v6/2026-preseason/site`.
The three direct and immediate validations pass, checking 152, 298, and 306
owned artifacts with zero current failures and 335 inherited findings each.
Final manifest SHA256:
`4e31e2e0c367ffecffb78a78b3887d3ed986a13ae3f4a2d0615358174fba7c9a`.
Final site aggregate SHA256 (11,634 files, recorded path-map algorithm):
`4a17209066058439096faf7bdf83a95cee0d9a212a24c1fc6cec48029a325712`.
The build had the API key unset and transport blocked, with zero provider calls.
W0 slates retain 4/5/8 games. Current recalculated outputs contain 52 pick'em
rows, all ungraded; the earlier V5 count of 35 is not a V6 invariant.

The prior V6 implementation and verification completed locally. Its acceptance
is pending the 2026 window follow-up above.
P2 history/storage/performance proposals belong to the separate history task.

## Correction-Chain Boundary and Deltas

The main accepted an explicit full-chain correction over frozen V5 public
files. The staging manifest resets only cumulative run/ownership/checksum
metadata and binds the original frozen tree; its inode was unlinked before
replacement, preserving the source manifest. Original manifest SHA256:
`0ff932b26521b914de31e400aeacfa7e345008e16c581e04165effcabeb69f68`.
Staging manifest SHA256:
`14e40aa7f3e5806c0de1dc0f6b71866525c31630b49813262fb07c96bd2010e4`.
All 11,558 non-manifest base files remain identical. Every affected derived
2024/2025/2026 artifact is recalculated; unchanged 2024 teams data is explicitly
preserved and validated. Only three old Schema 3 snapshot archives leave current
ownership, remaining physically unchanged alongside the new Schema 4 archives.
This bounded correction does not relax ordinary weekly progression guards.
The exact staging delta and reproduction evidence are sealed in
`evidence/staging-boundary-v6.json` and `evidence/staging-recipe-v6.txt`.

Final diff against frozen V5: 75 added, 232 changed, zero deleted files.
Week 1 result rows change 142→96 for 2024 and 137→91 for 2025. The 2024
PRESEASON and W0 outputs are semantically unchanged; corrected carryover changes
2025 forecasts and 2026 PRESEASON. Compared with V5, 2024 has 132 changed CORS
values and two rank changes, 2025 has 135 and nine, and 2026 PRESEASON has 134
and sixteen. The top CORS teams remain Oregon for 2024 and Indiana for 2025;
2026 PRESEASON has Ohio State first. These are model rankings, not assertions
about the official championship winner.

Each history alias has 129 rows: all 127 pre-2024 rows are preserved and each
2024/2025 row occurs once. The old 35 pick'em identities reconcile to 14 still
zero and 21 now nonzero and appropriately graded; current V6 has 52 zero-line
rows, all ungraded. Original six-call and refreshed nine-call evidence remain
unchanged. The new data root retains the same nine-row audit ledger; no CFBD
calls occurred. Official public schedule browsing was separately authorized.

## Current Evidence Entry Points

All following paths are relative to
`.sportsrank/postseason-calendar-repair-20260909/` in this isolated worktree:

- `evidence/index-v6.json` and `evidence/human-review-v6.md`: current handoff.
- `evidence/build-v6-summary.json`: three-stage validation and artifact identities.
- `verification/full-suite/unittest-discover-final.log`: 218/218 offline tests.
- `verification/full-suite/final-source-hashes.sha256`: verified source identity.
- `verification/candidate/independent-v6-report.json`: independent candidate checks.
- `evidence/provenance/proposed-postseason-registry.json`: official evidence audit.

Independent candidate verification is green: all 92 corrected rows map to
Weeks 16–22, none contribute to W0–15, exact reverse-source checks pass, and
history, W0, carryover structure, current pick'em semantics, and forecast-only
2026 output checks pass. Public-CLI isolated promotion passes with
`changed=true`, `reason=promoted`; postvalidation checks 306 artifacts with zero
current failures and zero delta. Candidate and promoted target match for all
11,634 relative paths and bytes. Original and backup match for all 11,559 files,
including the unchanged V5 manifest. All 229 prior year-owned paths remain:
225 have regenerated bytes and four are unchanged (2024 teams data and the
three old snapshot archives). Reports are under `verification/candidate/`,
including `promotion-relative-byte-report.json` and `preservation-boundary.json`.
The final `evidence/credential-scan-v6.json` reports `present=true`,
`value_scanned=true`, and `scan_complete=true`, with zero matches and zero read
errors across 24 roots covering original/isolated source, old/new caches,
candidates, evidence, verification/promotion outputs, and frozen site copies.
No credential value was emitted. The earlier partial invalid chain remains
separate superseded evidence.

The prior V5 package was accepted and promoted into tracked files before PR #3;
this P1 finding reopens technical acceptance. The isolated repair starts from
`379caab4283dacc940516be75967a0d1bbe7a4b1`. No commit, push, merge, original-site
promotion, or Gate 2 action is authorized here. The separate history task owns
ADR 0015/0016 proposals and broader storage/performance work.
