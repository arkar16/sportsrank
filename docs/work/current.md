# Current work

The recovery and publication repairs are merged through
[PR #11](https://github.com/arkar16/sportsrank/pull/11). **SR-28** owns the
consolidation of remaining documentation and finished branches onto `main`.
Start new delivery branches from current `main`; old recovery worktrees retain
historical evidence, not the current implementation.

## Next product deliveries

- **SR-26 — Preserve published CORS forecasts and report 2026 evaluation
  metrics** owns the accepted evaluation specification. Read
  `bb tasks show SR-26` and [ADR-0019](../adr/0019-evaluate-predictions-against-market-lines.md).
- **SR-25 — 2025 ranking progression, conference views, and championship
  projections** owns that separate feature. Its
  [plan](../plans/2025-ranking-progression-and-conferences.md) and
  [ADR-0020](../adr/0020-preserve-pregame-boundaries-for-matchup-estimates.md)
  preserve scope and pregame constraints.
- **SR-27 — Add versioned BEV and AEV to 2024–2026 game tables** owns the
  excitement-score delivery. Begin with the task and
  [plan pointer](../plans/2026-game-excitement.md); ADR-0021 and the linked
  calculation contract own accepted scoring decisions. Issued BEV depends on
  SR-26's forecast-preservation contract; data qualification and empirical
  acceptance remain required.

These records support delivery preparation. Their current task records govern
execution authority; merging the documents grants no new CFBD fetch or
production-publication allowance. The older
[history/storage proposal](../plans/history-storage-and-performance.md) remains
proposed future work, with unresolved choices in ADRs 0022/0023.

## Recovery acceptance and production evidence

**SR-7** owns the recovery outcome; **SR-24** owns the latest publication evidence.
Its September 29 handoff records successful deployment of the reviewed recovery
and independent representative HTTP checks. The final full audit subsequently
failed; the recorded Firebase initialization-script parser issue remains a
verification follow-up. Read `bb tasks show SR-24` before asserting full
acceptance or starting any successor publication. A merged PR or a deployed site
alone does not establish verified coordinator state.

The published recovery uses the September 25 snapshot: 2024/2025 FINAL,
2026 PRESEASON, completed Weeks 0–3 and Week 4 forecasts. SR-17 consumed its
one authorized 2026 games refresh. Raw source retention remains private on the
owner's computer under ADR-0018. Historical public-release deletion and
Git-history rewriting remain separate owner decisions.

## Operational and historical sources

- [Recovery contract](../plans/2026-p0-recovery-and-backfill.md)
- [Publication runbook](../operations/2026-season-recovery-morning.md)
- [ADR index](../adr/README.md): 0011/0012/0016/0017/0018 for publication
- [Historical Gate 1 evidence](../evidence/2026-gate1.md)
- [Frozen postseason repair handoff](../archive/postseason-repair/2026-09-09/README.md)
- [Tracker configuration](../agents/issue-tracker.md)

Tasks own current status, verification and residual risk. These pointers do not
replace a fresh task read or authorize deployment, rollback or claim replay.
