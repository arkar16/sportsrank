# Project Diary

- 2026-09-09 — Isolate repository cleanup from an active dirty release review;
  integrate onto its explicit accepted commit once available. Preserve accepted
  source and public bytes, and verify the cleanup delta independently.
- 2026-09-09 — NFL experiments can move together byte-for-byte because their
  imports are self-contained. CFB legacy modules and root generation helpers
  still have callers and path dependencies; consolidation belongs after the
  first safe 2026 Publication. An eight-test main baseline cannot substitute
  for the recovery branch's full locked suite.

Record only durable decisions, discarded approaches, and reusable lessons.

## Decisions and Lessons

- 2026-08-29 — Workflow installation initialized the six requested project
  documents from repository evidence. No deployment plan or prior workflow
  session evidence was available.
- 2026-08-29 — The repository is in a hybrid migration state: the Polars/
  Parquet/JSON/client ranking pipeline is wired through `cfb/calc.py`, while
  legacy pandas/static-HTML generators and historical artifacts remain. Future
  changes should verify both output paths when changing ranking behavior.
- 2026-08-29 — The tracked project does not include the ignored `cfb/api.py`
  credential module. Fresh environments need an out-of-band local credential
  setup before API-backed scripts can run.
- 2026-08-29 — `cfb/README.md` and `cfb/main.py` disagreed about the no-argument
  default mode. The recovery decision below resolves the ambiguity explicitly.
- 2026-09-04 — Consolidated all production CFBD access behind a metered
  `SeasonSnapshotService` with a fixture adapter, persistent request audit,
  budget gates, checksummed cache, and per-season locks. Teams are fetched once
  and downstream consumers share the immutable snapshot.
- 2026-09-04 — Initially implemented a staged Release boundary with provenance
  checks and atomic promotion. Independent review later proved that the
  artifact graph could be resealed after deletion and that the optional
  partial-site default could erase public history; ADR 0011 replaces those
  assumptions with a mandatory full-overlay and independently derived graph.
- 2026-09-04 — Resolved the no-argument legacy CLI contradiction in favor of an
  explicit season/recovery command. A no-argument invocation now returns a
  nonzero usage error rather than guessing from the calendar.
- 2026-09-04 — Replaced automatic Firebase triggers with a manual workflow, but
  later review found separate mutable-ref checkouts in validation and publish.
  ADR 0012 requires one content-addressed artifact from validation through
  Firebase publication.
- 2026-09-04 — Independent review invalidated the branch's publish-ready claim
  despite 59 passing tests. PRESEASON/Week 0 semantics, mandatory carryover,
  ATS grading, full-site preservation, independent artifact discovery, and
  exact-artifact deployment are now release blockers.
- 2026-09-04 — Defined PRESEASON as distinct from Week 0. PRESEASON retains the
  provisional `1.75` carryover and supplies Week 0 spreads; scored Week 0 games
  produce W0. The correction starts with the 2024 rebuild while older public
  pages remain frozen.
- 2026-09-08 — Implemented explicit PRESEASON, numbered Week, and FINAL ranking
  phases. Post-genesis Week 0 now requires an identified, complete prior FINAL;
  public compatibility APIs cannot bypass carryover validation with arbitrary
  mappings.
- 2026-09-08 — Release manifest v2 records cumulative run evidence and binds the
  candidate to both its original and immediate bases. Validation derives each
  artifact graph and field values from embedded canonical snapshots and run
  targets, so manifest resealing cannot authorize missing, changed, or forged
  output.
- 2026-09-08 — The one-call teams smoke now primes the normal cache. The P0
  recovery command enforces the exact scheduled/historical six-call ledger,
  builds the chained candidate without modifying `website/`, and performs
  direct cumulative validation against the original Published Site.
- 2026-09-08 — Firebase publication now accepts only full candidate/base commit
  SHAs, materializes the base site from the verified base commit, validates and
  packages once, and deploys the downloaded hash-verified artifact behind the
  protected production approval. Project-local direct deploy scripts were
  removed.
- 2026-09-08 — Independent adversarial verification repeatedly found gaps that
  ordinary green suites missed: missing CI base materialization, self-attested
  prior values, under-validated page fields, Week 0 API bypass, non-cumulative
  base validation, and forged Release identity. All are now covered by
  regression tests. Timestamp-only snapshot metadata resealing remains an
  output-neutral P1 caveat consistent with the deferred timestamp work.
- 2026-09-08 — A historical snapshot with a missing score is not necessarily
  incomplete: the 2024 App State–Liberty game was officially canceled. Preserve
  source lifecycle metadata in the snapshot, require explicit canceled or
  not-played evidence, and never infer cancellation from a null score alone.
- 2026-09-08 — Classification entrants are an explicit carryover domain case,
  not a silent missing-team fallback. The exact source-cited registry uses the
  established `-10` FCS baseline and `0` WVE, while Release validation derives
  and seals entrant/source evidence and rejects every unregistered mismatch.
- 2026-09-08 — Gate 1 used exactly six successful CFBD calls and then rebuilt
  entirely from the unchanged cache. Independent validation passed the chained
  2024 FINAL → 2025 FINAL → 2026 PRESEASON candidate with zero deletions
  and no mutation of the tracked Published Site. The workflow stops here for
  ADR / Implementation Review; successful validation is not publication
  authorization.
- 2026-09-08 — Independent Gate 1 review rejected the candidate. A validator
  that derives the same incomplete graph as its renderer can report green while
  omitting required historical PRESEASON and Week 0 spread artifacts. Release
  acceptance must assert the domain-required graph independently, including the
  next scheduled week's slate/spread for numbered builds.
- 2026-09-08 — The locked workflow is the authoritative test environment. An
  ad hoc pytest installation passing 108 tests does not compensate for the
  declared unittest workflow failing or for tests coupled to ignored local
  evidence. Schema normalization must also be inside the metered failure
  boundary so malformed responses cannot be audited as successful and retried.

- 2026-09-08 — Gate 1 repairs now have separate regression assertions for the
  domain-required historical PRESEASON/W0 graph and cumulative resealed
  deletion. A clean export with all intended untracked source, but no ignored
  evidence, passed the declared unittest suite after portable test repairs.
- 2026-09-08 — Cache-only repair and rebuild preserved the original six-call
  ledger and Published Site bytes. A missing current key is not credential
  absence evidence: report an unperformed value scan explicitly and finish it
  locally without a new provider request. The user completed that private scan;
  all five scopes returned zero matches and read errors.

- 2026-09-08 — The user redirected Gate 1 acceptance review to Astra within this
  task and canceled the planned cross-task handoff. No review message was sent.
- 2026-09-08 — Local Astra specification review found lifecycle gaps beyond the
  green suite: finite scores do not override explicit unfinished metadata;
  immutable release evidence must support multiple checkpoints in one Season;
  Week 0's normal-week semantics include ATS results from PRESEASON forecasts.
  The standards axis found no reason to require a broad module refactor.

- 2026-09-08 — Same-season progression requires immutable per-run inputs and
  per-run timestamps, with latest ownership only for regenerated output fields.
  Whole-page ownership must never suppress a prior FINAL or historical outcome
  check. A corrective FINAL must validate directly against both bases before
  advancing to the next Season.
- 2026-09-08 — Portable fixtures must include the actual legacy table schema.
  New canonical columns cannot justify dropping old supplied values: normalize
  legacy headings and filename-derived kinds, preserve win percentages, and
  test tampering in each history alias independently.
- 2026-09-08 — A renderer and validator can agree on empty Week 0 tables while
  both are wrong about the games. Retain raw provider weeks and timestamps,
  check known real matchups against primary schedules, and seal an explicit
  season-specific normalization policy. Do not globally subtract one from
  provider weeks or infer a missing date. A failed refresh cannot make a
  deficient legacy cache acceptable. Real-cache integration evidence belongs
  outside the portable suite, whose fixtures must work in a clean checkout.
- 2026-09-09 — A zero model line names no favorite: retain the observed winning
  side or push, but leave favorite-pick correctness ungraded. Regression checks
  must assert the domain meaning independently of the shared renderer/grader.
- 2026-09-09 — Every numbered release must retain and exactly validate its
  PRESEASON checkpoint. Compute eligibility boundaries from game dispositions
  before trusting snapshot metadata; rechecksumming a claim cannot make an
  unfinished game complete. Malformed metadata and unreadable inherited
  navigation must fail visibly rather than accepting partial evidence.
- 2026-09-09 — A successful HTTP status does not prove publication correctness.
  Compare smoke-test pages with the validated artifact and bind the smoke
  helper to that artifact's evidence. GitHub concurrency serializes active
  publication runs but does not promise FIFO ordering.
- 2026-09-09 — Compare copied release trees using relative paths and content
  hashes. An aggregate containing absolute-root metadata cannot establish
  equality across different directories; retain successful operational evidence
  when correcting that verification-harness assumption.
