---
status: accepted
---

# Preserve pregame boundaries for matchup estimates

On 2026-09-28 the owner required pregame-only inputs for championship matchup
estimates to prevent future information from contaminating the historical
dataset. Use the last eligible completed pregame CORS checkpoint for actual
games; standings-based Projected Championship Matchups use the pregame
checkpoint being viewed. FINAL or later ratings must never replace that basis
for an earlier matchup, even though FINAL remains appropriate for descriptive
season and conference views.

Retain the Forecast Cutoff and the rating, source and model provenance so that
a later rebuild cannot silently substitute later information. Excluding later
game results is necessary but does not, by itself, prove a forecast or its
inputs existed before kickoff. Reconstructed estimates must never be presented
as originally recorded predictions.

On 2026-09-29 the owner accepted clearly labeled historical reconstructions for
exploration, kept separate from the recorded-prediction evaluation dataset.
An estimate whose pregame cutoff cannot be enforced remains unavailable.
Participant selection and qualification status for a historical projection
must also respect its checkpoint; later confirmed participants must not be
inserted into earlier projections as if they were already known.

The [canonical specification](../plans/2025-ranking-progression-and-conferences.md)
owns the standings-based presentation and its acceptance examples. This
decision does not authorize changing CORS or the evaluation method under
[ADR-0019](0019-evaluate-predictions-against-market-lines.md).
