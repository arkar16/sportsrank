---
status: accepted
---

# Start men's Division I basketball CORS in 2019–20

SportsRank will add men's Division I basketball alongside FBS football. The owner approved starting basketball before completion of the earlier football-only sequence in ADR-0001. Each sport continues to own its source normalization and rating rules.

The basketball baseline starts all teams at zero in 2019–20. Returning teams carry their preceding final ratings and wins-versus-expected adjustment into the next season. The baseline copies the CFB v0.4.0 scoring formula and uses weekly Monday rankings and a two-point home advantage, with no home advantage on a neutral court. Games between two season members count toward the baseline.

Historical forecasts reconstruct the margin for a matchup using the preceding checkpoint. Issued forecasts retain the prediction actually made before tipoff. Test candidate changes on the same games in chronological order, using pooled margin RMSE as the primary criterion and straight-up accuracy as a companion. Each sport has its own selection and promotion decision. A historical comparison alone does not change the published model.

On November 23 the owner promoted basketball’s opponent-adjusted margin blend
(configuration 3578) to v0.6.0. The v0.5.0 label identifies the earlier spread
calibration experiment. The record book replays the promoted model across the
available seasons using each matchup’s preceding Monday checkpoint. Scores can
be displayed and used for grading after the game. Dated editions retain the
figures actually shown at preparation. The project-wide rule for reconstructed
picks is in AGENTS.md.

The replay starts with the retained end-of-2018–19 model state in
`cbb/blend_seed.json`. For each ESPN team ID its row contains the last season
played, final CORS component, adjusted margin rating, wins versus expected,
home and away residual sums, and corresponding game counts. The two global
sums carry the spread calibration into the next year. The historical and
prospective comparison is recorded in [experiment #100](https://github.com/arkar16/sportsrank/issues/100).

## Earlier historical weekly archive

The historical backfill begins with level ratings in 1980–81 and carries the
v0.6.0 state forward through 2018–19. Each season uses its own Division I
roster and games between two members, including postseason tournaments. The
source gives game dates, so each date belongs to its Eastern calendar week
without inventing tipoff clock times. The site publishes full weekly
checkpoints, game logs, upsets, ranking progression tables, and retrospective
model performance from this replay, matching the football record-book
structure. The modern weekly series retains its original 2019–20
initialization. This preserves previously published rankings and spreads as
the historical archive grows. The basketball CORS championship belongs to the
team ranked first in the published final standings.
