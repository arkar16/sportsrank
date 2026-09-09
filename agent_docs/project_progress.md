# Project Progress

## Current Goal and Position

Heavy deployment `sportsrank_cleanup_integration_20260909` prepares the
conservative repository cleanup for a separate PR based on the accepted
recovery branch. The NFL experiments move byte-for-byte to `legacy/nfl/`;
repository navigation and the conditional cleanup roadmap document the
supported and legacy boundaries. Independent integrated verification passes all
193 tests, the static build, compilation, lock checks, and diff checks. The
cleanup is ready for human PR review.

The accepted recovery boundary is
`379caab4283dacc940516be75967a0d1bbe7a4b1`, including the reviewer's final
documentation correction. Cleanup preserves Gate 1 source, tests, workflow,
candidate bytes, and evidence. The larger CFB directory migration and moving
generated HTML out of Git remain deferred under the cleanup plan.

Review the cleanup as a separate PR from `chore/repository-structure-cleanup`
into `feature/season-2026-recovery`, following the accepted recovery PR #3.
Its delta leaves recovery code, tests, workflow, dependencies, and website
artifacts unchanged. No new human design decision is required; PR review/merge
and the later protected Gate 2 approval remain human actions.

## Accepted Recovery Baseline

Heavy deployment `gate1_review_repairs_20260909` implements the seven review
repairs, including the follow-up production URL and bounded smoke-retry fixes.
Source and CI changes are frozen. The full offline suite passes 193 tests;
fresh V5 release validation and historical-value checks pass. Isolated
promotion passes, with the promoted bytes matching the candidate exactly.
The actual-key scan is complete with zero matches or read errors. Local repair
and verification are complete. Reviewer-owned acceptance of V5 was granted on
2026-09-09, and the exact candidate is promoted into tracked `website/`.
Reviewed implementation commit `f058ecf513db518fcf891a9369f914a827edb271`
is pushed on `feature/season-2026-recovery`; review PR #3 is open at
`https://github.com/arkar16/sportsrank/pull/3`.

Human Gate 1 approval applies to the reviewed V4 package and reviewer final
acceptance applies to V5. Gate 2 production approval remains pending. No further
provider or network data calls are authorized; remote deployment remains
blocked until PR merge and the protected production approval.

## Implemented Repairs

- Pick'em games retain home/away/push outcomes, with no favorite or underdog and
  ungraded favorite-pick correctness. All 35 affected historical rows are fixed.
- Numbered releases include or preserve PRESEASON and exactly validate it.
- Completed-week boundaries derive from game dispositions and scores; resealed
  snapshot metadata cannot declare an unfinished week complete.
- Malformed manifest numeric fields return structured hard failures, and
  inherited navigation read errors abort the build.
- Publication pins `setup-uv@v9.0.0`, serializes runs, and carries a SHA-bound
  smoke helper with the validated artifact. It uses the explicit production
  URL and retries the complete six-page byte comparison within bounded limits.
- Durable guidance records the granted V4 approval and the separate V5 review
  and Gate 2 boundaries. Historical V4 evidence remains unchanged.

## Evidence and Continuation

Candidate: `.sportsrank/gate1-review-repairs-20260909/releases-v5/2026-preseason/site`.
Review index: `.sportsrank/gate1-review-repairs-20260909/evidence/index-v5.json`.
Human entry: `.sportsrank/gate1-review-repairs-20260909/evidence/human-review-v5.md`.
`latest_session_work.md` owns exact identities, verification paths, and the handoff.

The 2024 FINAL → 2025 FINAL → 2026 PRESEASON chain validates with zero current
failures and zero deleted paths. All earlier history values are preserved;
2026 contains forecasts only. Original and refreshed caches and the Published
Site match their prior sealed identities. This repair used zero data requests;
the audit remains nine cumulative successful requests from the earlier work.

PR #3 now carries the accepted source, tests, documentation, and promoted static
site for human review. Existing legacy-page findings remain deferred. Firebase
publication is still blocked by Gate 2. GitHub's publication concurrency does
not guarantee FIFO ordering.
