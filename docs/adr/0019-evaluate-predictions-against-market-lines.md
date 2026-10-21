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
and October 5 decisions below settle the returned line and provider selection;
the October 11 decision settles Market ATS selection and resumes delivery.

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

On 2026-09-30 the owner confirmed that the retained weekly spread pages in the
existing Published Site were published before kickoff and calculated from the
preceding week's data. For this bounded contract upgrade, those exact retained
page bytes, stable Game identity and source orientation, original decimal
precision, and a pinned owner attestation qualify the retained values for
grading even when later automated publication-audit metadata is absent. The
attestation records the page and the owner's confirmation; it does not invent a
provider event or timestamp. Future forecasts still require the normal
verifiable automated publication evidence, and unconfirmed historical values
remain unverified or missing.

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
semantics. Historical comparisons must preserve those evidence limits. Sources:
[CFBD data availability](https://apinext.collegefootballdata.com/data-availability#betting-lines-weather-and-media)
and [GameLine schema](https://github.com/CFBD/cfbd-python/blob/main/docs/GameLine.md).

Carry into the initial implementation contract:

- Bind each Graded Forecast to its Game, original numerical precision,
  rating/model/source provenance and the artifact and publication evidence that
  establish it was issued before the Game. A build timestamp alone is not
  publication evidence. Report unverified cases under the accepted
  missing-forecast policy; for the owner-confirmed retained-page migration,
  record bounded owner-attested provenance instead of manufacturing a provider
  publication claim.
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

Apply the later decisions below to bookmaker-comparison delivery:

- Use the selected provider's returned spread and retain response/retrieval
  provenance. Retrieval time is not a quote timestamp or closing-line claim.
- Grade every nonzero model advantage against the provider line, with no pick
  for equal lines and separate pushes, using the October 11 rule below.
- Compare CORS and market errors on the same eligible games. Preserve issued
  CORS values; pregame market-line timing is not an eligibility gate under the
  accepted October 1 rule.

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

## Recreational model performance — October 9, 2026

The owner clarified that performance should be visible across the entire
historical website using saved predictions and final scores. Historical-source
qualification and proof of pregame publication are not prerequisites for these
recreational reports. Preserve each saved spread's original value and rounding;
calculate missing predictions from available preceding-checkpoint ratings with
unchanged CORS arithmetic, identifying those reconstructed values and any gaps.
A historical ranking rerun is not required to evaluate existing predictions.

When an archived preceding checkpoint retains more than two decimal places,
the retrospective gap calculation uses those retained values in the same
home-minus-away-plus-HFA arithmetic. It does not round or rewrite the archive,
change the production engine's two-decimal input contract, or replace any saved
spread. The report retains the resulting precision and reconstruction label.

Apply the same winner, CORS line coverage, MAE and RMSE definitions. Keep the
existing authenticated issued-forecast records intact; model-performance pages
can evaluate saved or reconstructed predictions without relabeling them as
verified pregame publications. Reports are static derived output in the normal
public export. This direction supersedes the earlier local-report-only scope of
[#33](https://github.com/arkar16/sportsrank/issues/33); task acceptance and
implementation evidence remain there. It does not authorize model tuning,
additional live requests, merge or production publication.

## Selected provider benchmark — October 1 and 5, 2026

The owner accepted the API's returned `spread` without quote timestamps or
closing-line evidence: "whatever the api gives, we use." Opening fields do not
replace it. Retain Game identity, actual provider and response/retrieval
provenance. A missing or non-finite selected-provider spread stays unavailable;
finite zero is a valid line.

The owner selected `teamrankings` for 2013–2020 and `Bovada` from 2021 onward,
without per-Game provider substitution. No provider is selected before 2013.
The research and coverage evidence are retained in
[historical provider survey](../evidence/2026-10-01-market-provider-coverage.md)
and [PR #23](https://github.com/arkar16/sportsrank/pull/23). These choices prioritize a
reproducible benchmark over maximizing coverage by mixing providers. They
supersede the earlier open selection/timing questions. Excitement's market
policy remains separate.

## Market ATS selection — October 11, 2026

The owner resumed forecast/provider comparison as part of publishing the complete
corrected website, then explicitly accepted grading every nonzero difference
between CORS and the provider line. There is no minimum edge threshold. If CORS
predicts Team A by 3 and the provider favors A by 7, select Team B +7. Equal
lines produce no pick; pushes are counted separately. With home handicaps,
`provider handicap - CORS handicap` selects home when positive and away when
negative. Grade against the actual home margin plus provider home handicap,
reversing its sign for an away selection. Neutral games require no extra
home-field adjustment after the CORS forecast has been calculated.

Market ATS win rate is wins divided by wins plus losses. Pending/canceled games,
missing lines/predictions and equal-line no-picks have no graded selection.
Paired MAE/RMSE include all completed games with both forecasts, including
equal-line games and pushes, and use the same sample for CORS and the provider.
Aggregate individual games rather than averages of weekly or season metrics.
Retrospective forecasts remain labeled and separate from issued CORS forecasts.

The owner also approved API requests if retained survey responses could not be
found. Fetches remain explicit, metered and privately retained; rebuilds remain
offline. Request counts and delivery acceptance belong to
[#37](https://github.com/arkar16/sportsrank/issues/37). This does not change merge
or protected production approval authority.

## Personal reference presentation — October 20, 2026

The owner explicitly removed reconstruction/pregame-publication warnings from
the website. Historical CORS forecasts are ordinary dated reference content.
Show the rankings, forecasts, results and performance without reconstruction
banners, saved-versus-reconstructed counters or prediction-source columns.
Keep underlying numerical values, timestamps, URLs, original forecasts and
source records; internal provenance does not require a public disclaimer. This
supersedes the presentation-label requirements above and applies to ADR-0024's
corrected output. [#71](https://github.com/arkar16/sportsrank/issues/71) owns delivery.
