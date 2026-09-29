# Game excitement

The owner accepted BEV, full-data AEV and the comparable-game estimated-AEV
approach on 2026-09-29. [ADR-0021](../adr/0021-define-versioned-game-excitement-scores.md)
records the decision and its trade-offs.

**SR-27 — Add versioned BEV and AEV to 2024–2026 game tables** is the canonical
implementation specification. Open it with `bb tasks show SR-27`. The task owns
scope, acceptance, dependencies, execution readiness and verification evidence;
its attached documents are dated supporting snapshots. This page is a pointer,
not a second specification.

The [v1 calculation contract](2026-game-excitement-scoring-v1.md) remains the
canonical definition of the formulas, comparable-game estimator and synthetic
arithmetic examples. [CONTEXT.md](../../CONTEXT.md) owns the BEV/AEV glossary.
Changes to consequential scoring decisions update the calculation contract and
ADR together.

Preparation can use the accepted contract. Issued-forecast/BEV integration
requires SR-26's preserved forecast contract; data qualification and empirical
reference acceptance remain explicit prerequisites in SR-27. The spec does not
grant a live CFBD allowance, dispatch implementation or authorize publication.

## Design sources

- Owner discussion: BB thread `thr_8gver5dnci`, following the forecasting
  discussion in `thr_y64c7nk2f5`.
- [Original CORS workbook](<../inbox/CORS CFB 2020_21.xlsx>) and its
  [formula/provenance inspection](../evidence/2026-09-28-original-cors-workbook.md#before-and-after-excitement-value).
  The workbook is a historical reference, not the new calculation's oracle.
- [CFBD game-drama data review](../evidence/2026-09-28-cfbd-game-drama.md).
  Documentation capability does not establish actual game-level coverage.
- [ADR-0019](../adr/0019-evaluate-predictions-against-market-lines.md) and
  [ADR-0020](../adr/0020-preserve-pregame-boundaries-for-matchup-estimates.md)
  preserve the distinction between viewing appeal, forecast evaluation and
  historical reconstructions.
