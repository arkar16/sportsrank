# Game excitement

The owner accepted BEV, full-data AEV and the comparable-game estimated-AEV
approach on 2026-09-29. [ADR-0021](../adr/0021-define-versioned-game-excitement-scores.md)
records the decision and its trade-offs.

[Football game excitement #179](https://github.com/arkar16/sportsrank/issues/179)
tracks the current and historical FBS rollout, initially covering 2024–2026.
The earlier SR-27 specification established that scope. The
[v1 calculation contract](2026-game-excitement-scoring-v1.md) defines the
formulas, comparable-game estimator and examples. [CONTEXT.md](../../CONTEXT.md)
owns the glossary.

The [game evidence runbook](../operations/game-excitement.md) describes the
static tables, frozen pregame records and reproducible offline preparation.
The issue owns reference validation and acceptance for estimated scores.

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
