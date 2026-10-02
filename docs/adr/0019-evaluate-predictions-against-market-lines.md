---
status: accepted
---

# Evaluate CORS forecasts against outcomes and market lines

The owner clarified on 2026-09-27 that improving CORS means improving spread
forecasting. Establish historical forecast accuracy against actual results and
use published market lines as an additional benchmark.

On 2026-09-28 the owner settled two further choices: scoring-margin accuracy is
the primary measure, and performance against the spread (ATS) remains a secondary
measure so that future changes can be compared on both. ESPN was an example,
not a required source; use bookmaker lines available through the API. These
decisions settle the purpose, metric priority and source flexibility. Subsequent
choices below settle forecast precision and margin-error metrics. The October 1
clarification below settles use of the returned API line; provider selection
and comparison implementation remain open.

On 2026-10-01 the owner accepted the market line returned by the API without
requiring quote timestamps or closing-line evidence: "whatever the api gives,
we use." Use the returned `spread` as the market benchmark; opening fields may
be retained as additional data but do not replace it. Retain the actual provider,
Game identity, response and retrieval provenance for reproducibility. Missing
or non-finite spreads remain unavailable rather than becoming zero. This
supersedes the market-line timing requirements below; preservation of issued
CORS forecasts remains unchanged. The [historical provider survey](../evidence/2026-10-01-market-provider-coverage.md)
recommends Bovada for current comparisons, with `teamrankings` for older
comparisons if needed. This recommendation does not yet select a provider or
authorize comparison implementation.

The owner also confirmed on 2026-09-28 that improving accuracy over current CORS
comes first; outperforming the market is a later goal, not a prerequisite for
adopting an improvement. The updated owner proposal prioritizes reliable
postgame tracking of predicted winners, coverage of CORS's own line and margin
accuracy. That work need not wait for the bookmaker comparison.

On 2026-09-29 the owner reaffirmed the sequence: establish the metrics before
optimizing CORS. The initial evaluation does not authorize tuning ratings,
home-field advantage or rating-to-margin conversion. The accepted forecast
precision change below remains in scope.

On 2026-09-28 the owner accepted Q6's sequencing: calibrated win probabilities
follow the initial work on reliable predicted winners, natural margin forecasts
and postgame grading. Probability modeling and calibration are later work and
do not block that initial evaluation. No probability method was selected.

The owner accepted natural forecasts and legitimate pushes. Do not move an
integer forecast merely to avoid a push. On 2026-09-28 the owner accepted Q7:
for new forecasts, use the matchup margin before half-point rounding for both
display and grading, display up to two decimal places and identify the Predicted
Winner explicitly. Preserve the home-handicap sign convention. This retains
small directional advantages such as 0.02; it does not increase the precision
of the input CORS Ratings, which already have two decimal places.

The owner accepted Q8's narrow exception to the earlier every-matchup winner
requirement: an exactly zero Model Spread is Pick'em, with no forced winner.
Include otherwise eligible Pick'em games in margin-error evaluation, exclude
them from winner accuracy and report their count. A small nonzero forecast is
not Pick'em. Its coverage treatment is settled below.

The owner accepted Q9: mean absolute error (MAE) is the headline margin-accuracy
measure, with root mean squared error (RMSE) alongside it to give larger misses
more weight. Both measure error in points and lower is better. Compare the
predicted and actual margins in the same home-team orientation: a predicted
two-point win followed by a three-point loss is a five-point absolute error.
Straight-up results and coverage of CORS's own line remain separate measures.

On 2026-09-29 the owner accepted Q10: the last forecast published before each
Game's kickoff is the Graded Forecast. Preserve it for later grading, including
its original precision; a later rebuild must not substitute a recalculated
forecast. Keep historical reconstructions separate unless evidence establishes
the forecast actually published before kickoff. The pregame-input boundary in
[ADR-0020](0020-preserve-pregame-boundaries-for-matchup-estimates.md) remains
applicable; reconstructing from a pregame checkpoint alone does not prove
publication before kickoff.

On 2026-09-29 the owner clarified the intended cadence: CORS runs weekly,
ideally on Sunday after the preceding week's games finish, producing the next
week's schedule and forecasts. Evaluation must preserve those issued forecasts
for grading after their games finish. The pre-kickoff publication rule applies
within this weekly cycle; it does not call for continuously updating forecasts
up to each game's kickoff. Sunday is the intended cadence, not permission to
include unfinished games as completed results.

The owner accepted revised Q17: an explicit correction published before a Game
is played replaces the earlier weekly forecast for grading under the
last-published-before-kickoff rule. Retain both versions and their publication
evidence. Ordinary site rebuilds preserve the issued weekly forecasts unchanged.
After the Game begins, a forecast correction cannot replace its Graded Forecast.

The owner accepted Q11: initial evaluation covers FBS versus FBS, including
Week 0, bowls and playoffs. Require valid ratings, a qualifying forecast and
final scores. Report missing forecasts and pending or canceled games separately
so omissions remain visible. Expansion to FCS opponents is outside this scope.

The owner accepted Q12: call the existing diagnostic **CORS line coverage** and
reserve **Market ATS** for bookmaker comparisons. CORS line coverage records
Cover, No cover or Push. Its percentage is covers divided by covers plus
non-covers; report pushes separately. Pick'em has no selected favorite and is
ungraded for coverage while retaining its otherwise eligible margin error.

The owner accepted Q13: accompany individual game results with weekly and
season totals showing evaluated-game counts, straight-up record, CORS line
coverage, MAE and RMSE. Team and conference breakdowns follow later.

On 2026-09-29 the owner accepted Q14: the first delivery contains straight-up
results, CORS line coverage, MAE and RMSE. Bookmaker comparisons follow in a
separate delivery after their provider and line-selection rules are settled.

The owner accepted Q15: begin the graded record with verifiable pre-kickoff
2026 publications and accumulate new games. Report missing historical forecasts
explicitly. Older-season audits and reconstructed backtests are later work;
reconstructions must not fill gaps in the published record.

The owner accepted Q16: corrected final scores update grades and summaries with
a correction timestamp, while the Graded Forecast remains fixed. Discovering a
forecasting bug afterward does not replace the original forecast or erase its
miss; fixes affect future forecasts.

The website must stay clean and numbers-focused: concise team names, numerical
forecasts and results, and necessary labels. Avoid explanatory filler and
qualitative edge or confidence copy.

A [bounded local artifact check](../evidence/2026-09-28-zero-spread-audit.md)
found that all 52 zero spreads in the inspected 2024/2025 and 2026 Week 0
forecasts arose from half-point rounding, with no exact pre-rounding ties in
that sample. Preserving the pre-rounding matchup margin supports the accepted
winner rule. The sample did not establish that genuine exact ties are impossible
or provide calibrated confidence percentages.

The current `ats_correct` grades whether the model's favorite covered
its own predicted spread. Retain that diagnostic under the accepted CORS line
coverage label alongside straight-up results and margin error, while separately
tracking Market ATS when bookmaker lines are added.

Current-state inspection on 2026-09-28 confirms that the modern Release path
already grades coverage of CORS's own line, including pushes, and publishes
per-game results. It lacks an explicit straight-up result field and margin-error
metrics. The legacy weekly SU/ATS summaries use different grading logic and
cannot substitute for consistent modern evaluation. See
[spread generation](../../cfb/ranking_engine.py),
[Release grading and output](../../cfb/release.py), and
[legacy summary generation](../../cfb/spread_results.py).

For example, Georgia Southern favored by 2 and winning by 19 counts as correct
in that current sense. It does not establish how a pick performed against a
published bookmaker line, which could differ from the model's line.

Source inspection on 2026-09-28: CFBD documents betting-line coverage from
2013 onward, varying by game and provider. Its line model exposes `spread` and
`spread_open` without a quote timestamp or an explicit definition of closing-line
semantics. Those metadata gaps do not block comparison under the owner's
October 1 rule, and returned spreads need not be certified as closing lines. Sources:
[CFBD data availability](https://apinext.collegefootballdata.com/data-availability#betting-lines-weather-and-media)
and [GameLine schema](https://github.com/CFBD/cfbd-python/blob/main/docs/GameLine.md).

Carry into the initial implementation contract:

- Bind each Graded Forecast to its Game, original numerical precision,
  rating/model/source provenance and the artifact and publication evidence that
  establish it was issued before the Game. A build timestamp alone is not
  publication evidence. Report unverified cases under the accepted
  missing-forecast policy; do not manufacture historical publication claims.
- Demonstrate that weekly progression, ordinary rebuilds, explicit pregame
  replacements and postgame score corrections preserve the accepted rules.
  Retain PRESEASON as the rating basis for Week 0 under ADR-0010 and the existing
  completed-checkpoint rules for subsequent weeks.
- Give each metric its own eligible-game count. MAE and RMSE include otherwise
  eligible Pick'em games; straight-up and coverage grading require a known
  selected side. Coverage percentages exclude pushes. Aggregate season metrics
  over individual games, not averages of weekly percentages or errors. Empty
  samples display a dash with count zero.
- Preserve an issued legacy rounded-zero forecast as zero for margin-error
  measurement. An unknown original winner is excluded from straight-up and
  coverage grading and counted separately from a confirmed exact-zero Pick'em;
  do not reconstruct an unrecorded winner and present it as an issued pick.

Resolve for the later bookmaker-comparison delivery:

- Select the provider and any explicit historical fallback policy from the
  measured API coverage. Use the returned `spread`, retaining provider identity
  and response/retrieval provenance. No quote timestamp, opening-line selection,
  or closing-line certification is required. Keep missing spreads unavailable;
  do not silently mix providers or infer a zero from null.
- Define how the model's predicted margin becomes a pick against that market
  line, then grade the actual result. Specify home/away signs, neutral sites,
  pushes, missing lines and canceled games.
- Compare CORS and market errors on the same eligible games using the preserved
  CORS forecasts and the selected provider's returned API spreads. Market-line
  availability before kickoff is not an eligibility gate under the October 1
  rule. Continue preserving the original CORS forecast and its issued values.

In the later probability work, establish empirical calibration before expressing
confidence as a win probability; an unsupported rating-ratio percentage is not
sufficient.

On 2026-09-29 the owner confirmed shared understanding and accepted the initial
forecast, metric, weekly lifecycle and delivery choices above, then requested
an implementation-ready specification. Later market-line and probability
choices do not block the initial delivery. Implementation requires its own
delivery contract; current calculations are unchanged.

This discussion stays focused on CORS forecasts and their evaluation. The owner
routed additional matchup context to @thread:thr_8gver5dnci and ranking
progression to @thread:thr_hfah4fx964. Historical-versus-current forecast
comparisons remain an area of interest here, but must keep original predictions
separate from retrospective estimates and must not delay the current evaluation
priority.

Design inputs: [owner proposal](../inbox/proposed_adr0019.md) and
[original CORS workbook](<../inbox/CORS CFB 2020_21.xlsx>). The workbook is a
historical model and feature reference; its formulas and saved results require
comparison with current behavior before reuse. See the
[workbook inspection](../evidence/2026-09-28-original-cors-workbook.md) for exact
sheet references, retained feature ideas and grading limitations.
