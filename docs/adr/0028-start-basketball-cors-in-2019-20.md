---
status: accepted
---

# Start men's Division I basketball CORS in 2019–20

SportsRank will add men's Division I basketball alongside FBS football. The owner approved starting basketball before completion of the earlier football-only sequence in ADR-0001. Each sport continues to own its source normalization and rating rules.

The basketball baseline starts all teams at zero in 2019–20. Returning teams carry their preceding final ratings and wins-versus-expected adjustment into the next season. The baseline copies the CFB v0.4.0 scoring formula and uses weekly Monday rankings and a two-point home advantage, with no home advantage on a neutral court. Games between two season members count toward the baseline.

Historical forecasts reconstruct the margin for a matchup using the preceding checkpoint. Issued forecasts retain the prediction actually made before tipoff. Test candidate changes on the same games in chronological order, using pooled margin RMSE as the primary criterion and straight-up accuracy as a companion. Each sport has its own selection and promotion decision. A historical comparison alone does not change the published model.
