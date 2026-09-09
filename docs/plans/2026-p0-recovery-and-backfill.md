# SportsRank P0 recovery and backfill — implementation handoff

Status: authoritative implementation goal. V1–V3 are superseded. The V4
human Gate 1 package is approved. The seven delegated repairs are implemented
and verified in the sealed V5 candidate: Pick’em grading, the numbered-week
PRESEASON graph, snapshot boundary trust, numeric manifest corruption, inherited
navigation readability, CI-pin/post-deploy smoke/concurrency behavior, and
approval-state documentation. Reviewer final acceptance of V5 was granted on
2026-09-09, and the exact candidate is promoted into tracked `website/`.
Metadata
repair completed under the separate allowance with exactly three explicit
metered GAMES-only requests—2024 historical, 2025 historical, and 2026
scheduled—with no retries or teams calls. The original six-call ledger, cache,
and audit bytes remain unchanged; the new
`.sportsrank/gate1-recovery-20260908/metadata-refresh-data` root records nine
cumulative successful rows. Schema 3 retains raw provider week, ID, date, and
completed fields, and the explicit project calendar policy verifies real W0
counts of 4, 5, and 8 for 2024, 2025, and 2026. The credential scan found zero
matches and zero read errors across all required scopes. This repair deployment
made zero provider/network calls; Gate 2 production approval remains pending.

## Mission

Use Goal Mode with the Heavy route to make the CFB/FBS static-site recovery
safe to run with the user's new CFBD key, rebuild 2024 and 2025 in carryover
order, prepare 2026 PRESEASON plus the Week 0 slate and spreads, and produce a
fully validated local diff. Work on
`feature/season-2026-recovery` from current commit `95e70409`; preserve
`baseline/static-cfb-v2-2026-09-03` and all unrelated user work.

The implementation task must keep its finished branch, commit list, complete
diff summary, exact test results, live-call ledger, release validation report,
known limitations, and review request in this task for local Astra review. This
in-task review is the independent review; acceptance remains separate from
publication approval, and no external review-thread handoff is required.

## Consistency gate

Before editing, read `AGENTS.md`, `docs/agents/project-memory.md`, `CONTEXT.md`,
ADRs 0001–0013, this document, current Codex Workflow state, and the decisive
source/tests. Codex Workflow records current execution and evidence;
`CONTEXT.md` defines domain language; ADRs explain durable decisions; this plan
defines the active implementation goal.

A contradiction among agent instructions, domain language, accepted ADRs,
this plan, workflow state, operator documentation, or executable interfaces is
a work blocker. Stop that lane, report the conflict to the main agent, resolve
the underlying decision, and update every affected source in the same change.
Do not choose one source silently, preserve stale commands as current, or
weaken an invariant to make the documents agree. Completion requires a final
contradiction scan across all human- and machine-facing guidance.

## Fixed product and architecture decisions

- Scope is CFB and FBS only. Keep classification-aware seams for later FCS and
  lower-level work without implementing those classifications now.
- The public product is complete static HTML/CSS. JavaScript may progressively
  enhance it; Python and Node are build-time tools and never public runtimes.
- Firebase remains the public host. Raspberry Pi, T7, `systemd`, backups, and
  notification work are outside this release.
- PRESEASON is a distinct published ranking derived from the previous Season's
  FINAL using the existing provisional `1.75` carryover adjustment.
- PRESEASON calculates the Week 0 slate and spreads. Week 0 contains real
  games; W0 is calculated only after completed Week 0 games using the normal
  first-week divisor of `1`.
- Correct PRESEASON/W0 semantics apply to rebuilt Seasons from 2024 onward.
  Pre-2024 public artifacts remain byte-for-byte unchanged.
- Every public path remains addressable indefinitely. A candidate Release is a
  full overlay of the Published Site, not a partial replacement.
- The current tracked `website/` is the publication base. Its homepage and
  2023 FINAL were verified byte-for-byte against the live site on 2026-09-04.
- Human Gate 1 approval was granted for the reviewed V4 package. The seven
  delegated repairs are implemented and frozen in the V5 candidate; offline
  verification and reviewer final acceptance are complete. This repair used
  cached inputs only, and isolated temporary promotion matched the candidate.
  The reviewer then promoted that exact candidate into tracked `website/`. No
  CFBD/network calls or Firebase deployment occurred. Gate 2 remains the
  separate protected Firebase production approval.

## Required P0 implementation

### Ranking lifecycle and correctness

- Replace the ambiguous weekly build contract with explicit `preseason`,
  numbered `week`, and `final` phases.
- `preseason` creates `YEAR_PRESEASON_FBS_cors.html` plus the Week 0 slate and
  spreads; it does not create a scored W0 ranking or results page.
- A numbered week includes only completed games through that week and creates
  the next week's slate/spreads when scheduled data exists.
- `final` requires a complete historical snapshot and creates the FINAL
  ranking plus required season-history effects.
- Require a valid prior FINAL before building any post-genesis Season. Missing,
  malformed, incomplete, or mismatched carryover is a hard error before any
  rendering; remove the silent all-team `-10` fallback. A team newly entering
  FBS is not an arbitrary omission when an exact, authoritative, source-cited
  Classification Entrant record exists: reconcile only that registered team at
  the existing explicit FCS baseline of `-10` with zero prior
  wins-vs-expected, seal the adjustment and its source in Release provenance,
  and continue to hard-fail every unregistered missing or extra team.
- Correct ATS grading with hand-verifiable favorite, underdog, home/away, push,
  tie, and missing-line behavior before rebuilding spread-result pages.

### Immutable overlay, validation, and promotion

- Make a valid Published Site base mandatory for every build. Remove the
  optional empty-site/`clone_published=False` path.
- Create each Release under a new immutable ID and refuse an existing release
  directory. Copy the complete base first, then change only paths owned by the
  requested Season and phase.
- Derive the expected artifact graph independently from classification,
  Season, phase/through-week, normalized snapshot, and public URL contracts.
  Manifest file lists are evidence to verify, never authority for what exists.
- Validate candidate against its actual base. Require every base path to remain
  present; require all non-owned inherited paths to remain byte-identical; allow
  changed/new paths only when the independent graph assigns ownership.
- Strictly validate every new or changed page. Grandfather known legacy defects
  only through unchanged bytes; a changed legacy file loses that exemption.
- Require valid rankings, records, completed-game boundaries, finite CORS and
  spread values, artifact checksums, internal links, HTML, canonical paths,
  and visible last-update timestamps.
- Promotion must revalidate, produce added/changed/deleted reports, require
  zero deleted public paths, and replace the target atomically. Build,
  validation, and promotion make zero CFBD calls.

### CFBD v2, cache, and request budget

- Read the project-only secret only from `CFBD_API_KEY` and send it as a bearer
  authorization header. Never accept it as an argument or write it to source,
  `.env`, fixtures, snapshots, manifests, pages, logs, exceptions, or CI
  artifacts.
- Route all production requests through the Season Snapshot/Request Meter
  boundary. Make the 2026 teams smoke populate the normal cache.
- Preflight budget before transport. Authentication, schema, quota, transport,
  or unexpected call-count failures stop without automatic retry; only an
  explicit operator refresh can spend another call.
- Preserve the 3,000 monthly provider ceiling, 2,500 application stop, 500
  historical reserve, and 100 scheduled reserve.
- The original approved live sequence is exactly six calls: 2026 teams smoke
  (1), 2024 teams/games (2 historical), 2025 teams/games (2 historical), and
  2026 games (1 scheduled). Its ledger, cache, and audit bytes are immutable
  evidence; do not rerun that sequence.
- The separate metadata-repair allowance is spent: a new task data root was
  seeded from the original ledger and cache, all cached teams were reused, and
  exactly three metered GAMES-only refreshes succeeded—one each for historical
  2024, historical 2025, and scheduled 2026. The resulting cumulative count is
  nine successful rows. No further CFBD requests are authorized for this task;
  rebuild, verification, validation, promotion, and publication are cache-only.
- Week normalization is an explicit SportsRank policy, not a CFBD-provided
  conversion. Only provider Week 1 games dated before the timezone-aware
  midnight America/New_York canonical Week 1 boundary map to Week 0: 2024-08-26,
  2025-08-25, and 2026-08-31 respectively. Other provider weeks remain
  unchanged; unsupported seasons and deficient legacy metadata fail closed. A
  failed refresh has no silent refresh or automatic fallback.
- Schema 3 preserves raw `provider_week`, provider ID, date, and completion
  fields. `cfb/week_calendar.py` owns the explicit policy. Each Release
  independently rederives the mapping and binds the `week_calendar` policy
  identity, Season, boundary, timezone, and primary source URLs in its
  provenance.

### Completed delegated repair scope

The reviewed V4 package is approved at Gate 1, and the following seven repairs
are implemented and verified in the sealed V5 candidate. V5 reviewer final
acceptance was granted on 2026-09-09:

1. Pick’em line 0 keeps the observed result side home/away, including tie/push;
   favorite and underdog remain absent, and `ats_correct` is `null` for all
   zero lines.
2. The numbered-week artifact graph includes PRESEASON and exactly validates
   that PRESEASON artifact.
3. Snapshot completion derives from completed/canceled dispositions rather
   than provider metadata claims.
4. Numeric manifest corruption hard-fails without an uncaught crash.
5. Inherited navigation read failures hard-fail validation.
6. CI uses the reviewer-supplied existing `9.0.0` pin; post-deploy smoke covers
   six pages, with production concurrency set to `cancel-in-progress: false`.
7. Approval-state documentation records the V4 human-package approval and the
   2026-09-09 V5 reviewer acceptance, while Gate 2 production approval remains
   pending.

This repair used cached inputs only: no CFBD or network data calls, publication,
commit, push, PR, or deployment occurred within the delegated repair task.
Source, tests, and workflow changes are frozen; isolated temporary promotion
matched the V5 candidate. V4 is superseded for repaired acceptance; V5 evidence
is sealed and reviewer-accepted.

### Metadata repair and candidate construction

Metadata repair and the V4 rebuild are complete without touching `website/`:

1. Preserve the original six-call data root, ledger, cache, and audit bytes.
2. Use the refreshed root
   `.sportsrank/gate1-recovery-20260908/metadata-refresh-data`, whose nine-row
   ledger contains the three successful GAMES-only refreshes. The initial
   six-call sequence was not rerun and no further CFBD request is authorized.
3. Verify schema 3 raw metadata, the source-backed calendar policy, and the
   normalized W0 counts (4 for 2024, 5 for 2025, 8 for 2026). Do not apply a
   general provider-week conversion or silently refresh deficient metadata.
4. The historical V4 candidate was
   `.sportsrank/gate1-recovery-20260908/releases-v4/2026-preseason/site`, built
   through 2024 FINAL → 2025 FINAL → 2026 PRESEASON. It contains forecasts only
   for 2026 Week 0; no scored 2026 Week 0 artifacts are generated.
5. All V4 build, verification, and validation stages used cached inputs and
   made zero provider calls. Do not process completed 2026 Week 0 games in this
   goal.

The historical V4 Gate 1 evidence package presents the preserved six-call ledger
and bytes, the separate three-call repair ledger with nine cumulative successful
rows, credential-leak scan, schema 3 metadata and policy provenance, normalized
W0 counts, release checksums, artifact validation, added/changed/deleted URL
report, and full static-site diff. This evidence is indexed by
`.sportsrank/gate1-recovery-20260908/evidence/evidence-summary-v4.json`; the
human review entry point is
`.sportsrank/gate1-recovery-20260908/evidence/human-review-v4.md`. The sealed
V5 candidate and evidence are recorded under
`.sportsrank/gate1-review-repairs-20260909/`; its isolated temporary promotion
matched the candidate and left the Published Site unchanged. Reviewer final
acceptance is now complete and the exact candidate is in tracked `website/`;
Gate 2 remains pending.

### Exact-artifact publication

- Replace mutable workflow input with required full `candidate_sha` and
  `base_sha` commit identifiers. Validate their relationship and reject branch
  names, tags, short SHAs, or an unmerged candidate.
- Validate and package `website/` once. Carry that content-addressed artifact
  and its attestation into the protected publish job; do not re-checkout a ref.
- Disable direct `npm run deploy` and equivalent hosting publication bypasses.
- After PR review and merge, validate the exact merged SHA against the currently
  deployed base SHA. Gate 2 is the GitHub `production` environment approval.
- Deploy the already-validated artifact, then verify the homepage, 2023 FINAL,
  rebuilt 2024/2025 FINAL pages, 2026 PRESEASON, and the Week 0 slate.

## Meaningful verification

- Keep ordinary tests fully offline with portable Schema 2/3 fixtures; no
  private or ignored cache is required.
- Prove exact call counts for empty, partial, complete, failed, and explicitly
  refreshed caches; prove budget rejection occurs before transport.
- Prove secrets are redacted from every failure and generated artifact.
- Prove PRESEASON, scored W0, Week 1 inputs, missing carryover, incomplete games,
  complete-season FINAL, and history updates with hand-checked examples.
- Prove ATS grading across both sides, pushes, ties, missing lines, and venue
  orientation with expected outcomes calculated independently of production
  code.
- Prove manifest tampering cannot hide a deleted required file.
- Prove a candidate cannot delete or modify an inherited path, while permitted
  owned replacements and new canonical paths pass.
- Prove the 2024 FINAL → 2025 FINAL → 2026 PRESEASON chain end to end against
  fixtures, including links, timestamps, HTML, checksums, and zero downstream
  API calls.
- Prove CI publishes the byte-identical artifact attested by validation and
  rejects mutable refs or mismatched SHAs.
- Run the complete Python suite, compilation, lock checks, hosting verification,
  static-link/HTML validation, diff checks, and an independent tester pass.
  Tests must exercise observable risk and must not be weakened merely to pass.

## P0/P1 disposition ledger

No known P0 may be deferred. Discovery of another credential, deletion,
incorrect-ranking, validation-bypass, or unvalidated-publication path stops the
goal and adds it to the P0 work before repaired technical acceptance.

### Included in this goal

- **P0:** A partial candidate can erase thousands of historical public paths.
- **P0:** The validator trusts the manifest's self-declared artifact graph;
  deleting a required file and resealing the manifest can pass.
- **P0:** The recovery must be runnable with the new CFBD v2 key without leaking
  it or spending unmetered calls.
- **P1 promoted to release blocker:** Missing prior FINAL fails open and can
  initialize every team at `-10`.
- **P1 promoted to release blocker:** PRESEASON and Week 0 have incorrect domain
  semantics in code and prior documentation.
- **P1 promoted to release blocker:** ATS result grading is incorrect.
- **P1 promoted to release blocker:** Validation and publication independently
  checkout a mutable ref rather than sharing one validated artifact.
- **Publication bypass promoted to release blocker:** Direct Firebase scripts
  can publish without the required validation and approval chain.

### Already mitigated; retain regression coverage

- The exposed historical CFBD key is revoked, credential-bearing bytecode is no
  longer tracked, and the replacement key remains outside Git.
- The supported adapter uses the CFBD v2 bearer/classification contract and
  clean-checkout tests no longer import a credential module.
- Automatic push/PR/scheduled Firebase triggers are disabled.
- No `upstream` branch or remote remains.

### Known P1 not fixed by this goal

- Unchanged inherited legacy pages may contain existing HTML, link, or timestamp
  defects. They are frozen by hash and cannot acquire new defects; systematic
  repair requires a separately approved migration.
- Operational timestamps currently make equivalent builds produce different
  release trees, so no-op detection is not reliably content-semantic.
- Legacy orchestration remains alongside the recovery CLI, retains broad error
  handling, and is not the approved production path. Consolidation and removal
  require a later migration after the new recovery path is proven.
- The 1897 genesis baseline and full-history numerical equivalence are not
  re-adjudicated because this backfill begins with the existing 2023 FINAL.
- The long-term preseason regression model is undecided; `1.75` is retained as
  an explicit provisional compatibility rule.
- Pi/T7 provisioning, Monday `systemd` automation, retry scheduling, off-device
  backups, restore drills, and failure notifications remain unimplemented.
- SQLite as the durable build-time store remains accepted architecture but is
  not introduced before URL preservation and release recovery are proven.
- Moving generated HTML out of Git, reducing repository size, and purging old
  bytecode from Git history remain later work.
- FCS/lower-classification expansion and Comparable Scale calibration remain
  deliberately unresolved and out of scope.

## Completion and review handoff

Use bounded Heavy-route ownership: CFB ranking semantics, shared release
integrity, shared CI/publication, and independent testing are separate lanes.
Workers must not revert another lane's edits and must escalate cross-boundary
conflicts. The main agent owns architecture, integration, root-cause decisions,
the two gates, and final evidence. Run the required Closure Steward handoff once
after implementation and verification.

The V5 local repair goal is complete: all seven findings are fixed; the full
offline suite passes 193/193 tests; CI-focused checks pass 10/10; compile,
locked-dependency, npm, and diff checks pass; the three V5 releases validate
116/226/234 artifacts with zero structural failures; the final overlay has
142 added / 93 changed / 0 deleted paths; the original six-call sequence and
prior nine-row ledger are preserved; and this deployment made zero provider
calls. The sealed V5 candidate has reviewer final acceptance and is promoted
into tracked `website/`. Gate 1 is complete; Gate 2 remains the protected
production approval.
