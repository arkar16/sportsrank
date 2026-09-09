---
status: accepted
---

# Preserve one Season chronology through postseason

SportsRank treats phase as an attribute of each Game, not as a Week label. A
positively classified postseason Game uses the existing Season Week 1 boundary
and its America/New_York local date to derive `canonical_week = 1 +
floor((local_date - week1_boundary_date).days / 7)`, so regular and postseason
games may share a numbered Week, gaps are valid, there is no separate W17
lattice, and public Week URLs do not change. Adapters preserve raw
`season_type` and `playoff` fields; newly
provider-backed missing or unknown phase fails closed. The bounded registry for
92 known rows may classify only an exact Season, provider ID, date, notes, teams,
and snapshot checksum with official source evidence and
`phase_source=recovery_registry`; fresh provider-backed postseason IDs do not
require membership in this historical registry, but raw provider phase
mismatches reject at cache and Release validation. Unaffected legacy rows may
remain unknown, while suspicious late provider Week 1 games fail closed.
Calendar policy, registry, and schema versions are separate; original Schema 3
caches remain immutable and new data and Release roots hold the repair. This
decision does not alter the separate history-storage or SQLite proposals.

The bounded implementation reads those immutable inputs through
`python -m cfb.recovery migrate-postseason --source-root <directory containing
cfb-fbs-YEAR.json> --destination-root <new empty snapshots directory>` with
repeatable `--season` selection, defaulting to 2024/25/26. It writes derived
Schema 4 data without provider calls. Schema 4 retains available raw
`season_type` and `playoff` fields and records source Schema 3/checksum, target
schema, calendar identity, and registry provenance. The regular and postseason
calendar IDs are `cfb-provider-week-v1` and `cfb-postseason-week-lattice-v1`;
the pinned `postseason-recovery-v2` registry checksum is
`244b5ed82b7d0add35cae95cf48ce664639f17a9243fbda8acc83e6720467549`. Fixed
recovery windows are 2024-12-14 through 2025-01-20, 2025-12-13 through
2026-01-19, and the inclusive 2026 window 2026-12-12 through 2027-01-25 in
America/New_York. December 12 is the FCS Celebration Bowl; December 15 is the
first FBS bowl. Official source URLs are pinned by `cfb/week_calendar.py`.
Reverse validation must reproduce the complete Schema 3 checksum. The current
window/provider-phase follow-up has passed 225/225 tests and compilation; all
three direct/immediate reconstructions have zero failures and zero path deltas,
with exact bytes matching V6. Reviewer acceptance remains pending.

The completed V6 correction uses a bounded full-chain boundary: reset only
cumulative run and ownership metadata on a hard-linked copy of the frozen V5
tree, unlink the staged manifest before replacement, and preserve all other
base bytes. The original and staged manifest hashes are respectively
`0ff932b26521b914de31e400aeacfa7e345008e16c581e04165effcabeb69f68` and
`14e40aa7f3e5806c0de1dc0f6b71866525c31630b49813262fb07c96bd2010e`; all 11,558
other files remain byte-identical. The 229 prior year-owned paths remain
physically present while the fresh graph regenerates 225 and preserves four;
three obsolete Schema 3 archives leave current ownership. This preserves the
public base without bypassing ordinary weekly progression checks. V6 local
verification is complete and reviewer acceptance remains pending.
