# Latest Session Work

## Deployment and Disposition

- Deployment: `gate1_review_repairs_20260909`, Heavy route.
- Goal: repair all seven reviewer findings, including the later CI URL/retry
  correction, with cached inputs only and return fresh evidence for acceptance.
- Source, workflow, and tests are frozen. Full offline verification passes
  193 tests. V5's three release validations and history-value checks pass.
  Isolated promotion and the final actual-key scan pass. Local implementation
  and verification are complete. Reviewer final acceptance of the sealed V5
  package was granted on 2026-09-09, and the exact candidate is promoted into
  tracked `website/`.
- Human approval of the V4 Gate 1 package and reviewer final acceptance of V5
  are granted; Gate 2 production approval is pending.
- Branch: `feature/season-2026-recovery`; prior verified HEAD
  `95e70409f1d3d2c30e5279a95f774fff0768e15a`. The delegated repair made no Git
  or deployment mutation; the reviewer owns the accepted promotion, commit,
  push, and PR. Firebase deployment remains unauthorized.

## Repairs and Regressions

Pick'em lines retain the actual home/away result or tied-game push, while
favorite, underdog, and `ats_correct` remain absent. All 35 real historical
zero-line rows now have blank favorite-pick correctness rather than `True`.
Home win, away win, and tie behavior have explicit regressions.

Numbered-week overlays emit or preserve the Season's PRESEASON artifact and
validate its exact fields, including when inherited. Resealed deletion is
rejected. Existing same-season checkpoint, timestamp, and history protections
remain covered.

`SeasonSnapshot.complete_through_week` derives its result from game completion
and canceled/not-played dispositions. Release checks reject forged metadata or
targets beyond the derived boundary. Malformed numeric manifest fields return
structured validation failures instead of uncaught conversion errors. An
`OSError` reading inherited `cfb/cfb.html` now aborts rather than losing links.

The publication workflow uses reviewer-supplied `astral-sh/setup-uv@v9.0.0` and
stable concurrency group `sportsrank-production-publication` with
`cancel-in-progress: false`. GitHub concurrency does not guarantee FIFO order.
`tools/scripts/postdeploy_smoke.py` is bundled and SHA-bound in the validated
artifact chain, verified before execution, and receives the explicit
`https://www.sportsrank.top` URL instead of unsupported `job.environment.url`.
It compares homepage, 2023/2024/2025 FINAL, 2026 PRESEASON, and the W0 slate
byte-for-byte with the validated artifact. Defaults are three full-check
attempts, a two-second delay, ten-second per-page timeout, and a 180-second
window. CLI caps are five attempts, thirty-second delay, and 180-second window.
HTTP/network/content failures may retry; missing/empty local artifacts and
invalid URLs fail before transport. Exhaustion fails the job. All behavior was
verified offline; no GitHub workflow or production deployment was executed.

## Verification Evidence

Evidence root: `.sportsrank/gate1-review-repairs-20260909/`.
The final full suite passed 193/193 tests; see `verification/full-suite-final2.log`.
CI focused tests pass 10/10, including exact invoked URL, full retry restart,
transient recovery, exhaustion, and bounds with fake fetch/clock/sleep.
Independent tests cover the five release defects and publication contracts.
Compilation, locked dependency checks, npm build, and diff checks pass.
`verification/source-hashes-final.txt` records the final verified source.

The three completed V5 validator reports have zero structural failures, with
116, 226, and 234 checked artifacts respectively. Final overlay diff:
142 added, 93 changed, zero deleted paths. The final candidate retains 335
inherited legacy findings on unchanged pages. All 35 pick'em rows are ungraded;
W0 slates contain 4/5/8 games for 2024/2025/2026, and no scored 2026 W0 artifacts
are present.

History checks preserve the 127 pre-2024 rows and add 2024/2025 for 129 rows.
Finite numeric values are compared independently of trailing-zero formatting;
legacy NaN/blank equivalence never treats zero as missing. See
`evidence/history-independent.log` and `evidence/cache-independent.log`.
Earlier monolithic verifier attempts had formatting assumptions and execution
failures; completed successful stage evidence is retained, while the isolated
promotion check is recorded under `verification/promotion-check/`.
Its corrected `promotion-check-report.json` confirms CLI promotion succeeded,
the initial temporary copy and backup matched the original, and the promoted
files matched the candidate exactly by relative-path SHA maps and byte checks.
Post-promotion validation checked 234 artifacts with zero structural failures.
The reviewer independently confirmed the same public-CLI result at
`/tmp/sportsrank-v5-promotion-review.RxjjKi/website`, with `diff -qr` exit zero.
Absolute-root metadata caused false comparison flags in the original helper
report; a later timed-out rerun is superseded and did not change product inputs.

The final `evidence/credential-scan-v5.json` reports `present=true`,
`value_scanned=true`, and `scan_complete=true`, with zero matches and zero read
errors across all thirteen scopes. These cover source, original/refreshed caches,
V5 and superseded candidates, evidence and verifier outputs, and the Published
Site. The actual environment value was scanned privately and never recorded.
The sealed evidence index retains its contemporaneous status
`local_verification_complete_pending_reviewer_v5_acceptance`; reviewer
acceptance was granted afterward on 2026-09-09.

## Candidate and Input Identities

Candidate:
`.sportsrank/gate1-review-repairs-20260909/releases-v5/2026-preseason/site`.
Review index: `evidence/index-v5.json`; human entry: `evidence/human-review-v5.md`;
exact repair paths: `evidence/changed-source-paths-v5.txt`.

Final site aggregate SHA256 (11,559 files, operator path-record algorithm):
`06f66ee7a91abb3b508ab59993ff76ce047defb02cecb56c2890541919c971c3`.
Final manifest SHA256:
`0ff932b26521b914de31e400aeacfa7e345008e16c581e04165effcabeb69f68`.
Verified generator `cfb/release.py` SHA256:
`91bb943d750a5b7ca489cf79c4a9120a6ce6f748fdde28fe3795a924ba8819a7`.
Verified snapshot module SHA256:
`775b73ce39ad33a96f9a7c987edc9f84fa433a10cdd0b0a209a8332e0637c8b7`.
Smoke helper SHA256:
`c08d4fd4e415d6cee52f3817e6b46a93d630ea64f60796b2604627b66e21358b`.
Workflow SHA256:
`7b941d48701336962ec731e77d31ff930001f0a5c72071a7ef34af01fe343576`.
The candidate records `working-tree`; later CI/documentation-only edits do not
imply an HTML rebuild. This is not a merged commit or deployment attestation.

Builds used the existing
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data/snapshots` inputs,
with `CFBD_API_KEY` unset and socket transport blocked. No data calls occurred.
The nine-row ledger still contains the original six calls plus the three prior
metadata-repair refreshes. Existing input identities are preserved:

- Original cache: seven files, aggregate
  `a21b77321e2c202a05f42fbf67bd5ced17a275ca83c92ee06bb829f30b0baacc`.
- Refreshed cache: seven files, aggregate
  `8fca32c605ac2265dca42c1088d99eb166d936f069bc399beabb71469a1ab341`.
- Published Site: 11,417 files, aggregate
  `5e42b5eec90653072783d056b3f87d0df2ab36497e0f2c923c897a7524a9c922`.

An initial offline helper pointed at the cache package root and created one
empty lock. Only that known temporary lock was removed; all snapshot and audit
bytes stayed unchanged, and the complete cache digest returned to the V4 value.
`evidence/cache-reconciliation-v5.json` records the comparison. V4 historical
candidate and evidence files were not rewritten.

## Exact Continuation

The reviewer accepted the sealed V5 candidate and promoted it byte-for-byte into
tracked `website/`. Commit and push the accepted recovery package, then open the
review PR. Do not perform further provider/network requests. Gate 2 remains
separate and is not authorization for Firebase deployment.
