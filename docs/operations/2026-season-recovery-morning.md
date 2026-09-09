# 2026 recovery publication runbook

Do not run the old live-fetch, promotion, or Firebase instructions. V3 is
superseded. Metadata repair completed with exactly three explicit metered
GAMES-only requests (2024 and 2025 historical, 2026 scheduled), no retries or
teams calls, and nine cumulative successful audit rows in
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data`. The original
six-call ledger, cache, and audit bytes remain unchanged. Schema 3 now retains
raw provider week, ID, date, and completion fields; the source-backed calendar
policy verifies W0 counts of 4, 5, and 8 for 2024, 2025, and 2026. The V4 human
Gate 1 package is approved. V5 implementation and offline verification are
complete: 193 tests pass, CI-focused tests pass 10/10, and three V5 validations
check 116/226/234 artifacts with zero structural failures. Reviewer final
acceptance of the sealed V5 candidate was granted on 2026-09-09, and the exact
candidate is now in tracked `website/`; Gate 2 production approval remains
pending. See `agent_docs/latest_session_work.md` for current
deployment state.

The implementation and test contract is
[`../plans/2026-p0-recovery-and-backfill.md`](../plans/2026-p0-recovery-and-backfill.md).
The actual-key credential scan is recorded in the repair evidence and required
no API call. The three-call metadata-repair allowance is spent; preserve the
original six-call bytes and do not rerun that sequence or issue further CFBD
requests. Continue from the refreshed root with cached-input review and
validation only. The normalization is an
explicit SportsRank policy, not a CFBD conversion: only provider Week 1 games
before the midnight America/New_York boundaries 2024-08-26, 2025-08-25, and
2026-08-31 map to Week 0; later provider weeks remain unchanged. Unsupported
seasons and deficient legacy metadata fail closed, with no silent refresh or
automatic fallback after a failed refresh. See the supported staged commands in
[`../../cfb/README.md`](../../cfb/README.md) for the cache-only rebuild path.
`cfb/week_calendar.py` owns the explicit policy; each Release independently
rederives it and binds the policy identity, Season, boundary, timezone, and
primary source URLs in provenance.
This repair completed from cached inputs with source, tests, and workflow
changes frozen. Isolated temporary promotion matched the V5 candidate, and the
reviewer accepted and promoted that exact candidate into tracked `website/`.
No CFBD/network request or Firebase deployment occurred. Gate 1 is complete;
Gate 2 production approval remains pending.

## Gate 2 publication path

After PR review and merge, use only the manually dispatched **Publish validated static site to Firebase Hosting** workflow. Gate 1 and V5 acceptance are
complete. Enter
the full 40-character `candidate_sha` of the merged candidate and the full
40-character `base_sha` of the currently deployed commit. The workflow rejects
mutable refs and unmerged candidates, validates the candidate site once,
creates a content-addressed artifact plus attestation, and carries that exact
artifact into the protected GitHub `production` environment. The publish job
has no source checkout. Do not invoke local hosting publication commands; Gate
2 is the pending production approval.
