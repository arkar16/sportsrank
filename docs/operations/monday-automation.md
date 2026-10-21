# Monday SportsRank operation

The T3 Monday timer was deleted October 18; there is no active recurring writer.
[#54](https://github.com/arkar16/sportsrank/issues/54) owns the unactivated hosted
replacement. This document retains grading/contest guidance; publication uses
[the direct runbook](direct-publication.md) under ADR-0026.

## Start or resume a cycle

Read the task, current work and the current publication/contest receipts. Use
one cycle in flight, keyed by season and upcoming slate, with private output
under `.sportsrank/weekly/<cycle>/`. A timer firing again is not permission to
repeat a completed upload or deployment. Resume from retained attempt journals
and authoritative remote state. Never replace a pinned source or package.

Require T3-injected `CFBD_API` for source acquisition and T3-injected
`CFBD_PRED_API` for the contest, an authenticated GitHub session and retained private inputs. Never
print or persist keys. Each destination may continue independently if only the
other destination's credential is missing.

## Refresh, grade and forecast

1. Determine the season and completed/upcoming games from kickoff times,
   completion status and accepted regular/postseason chronology. Week is a
   competition checkpoint, not an ISO calendar number. Skip cycles with no new
   results or upcoming work; do not manufacture a next-week slate.
2. Refresh games once and selected-provider lines once at most, using
   `SeasonSnapshotService` and `RequestMeter`. Reuse cached teams/history;
   preserve all earlier ledger entries and private snapshots. Journal allowance
   use before acquisition, count failed attempts and never blindly retry. Stop
   on unsupported seasons, missing roster/carryover or exhausted allowance.
3. Grade the original issued forecasts for completed games. Leave incomplete
   or postponed games pending. Rebuild the corrected ratings through completed
   games with the accepted CORS engine, prior FINAL and entrant registry.
4. Produce the upcoming pregame forecasts and retain their exact values,
   source/checkpoint identities and timestamps before either publication.
   Attach selected provider lines with capture time and coverage. Apply
   ADR-0019: every nonzero model/provider difference produces an ATS pick;
   equality produces no pick, pushes remain separate, and win percentage is
   wins divided by wins plus losses. Missing provider lines remain unavailable.
   Never replace an originally issued line with a later fetched line while
   representing it as the original pregame comparison.

## Publish the website

Review the changed output and use [direct publication](direct-publication.md).
Commit the generated website through a scoped PR, then publish from main and
check the updated pages. No seal/execute, source-bound receipt regeneration or
full live inventory is required. Preserve model/forecast/URL and private-input
boundaries. New source/model changes remain separately reviewable.

## Submit the model contest

Use the authenticated contest slate to match every game ID, home/away team,
season, game phase/week and future kickoff. The validated current protocol is
Bearer GET/POST `https://predictionsapi.collegefootballdata.com/api/picks` with
an explicit SportsRank User-Agent, no redirects and payload
`{"picks":[{"gameId":123,"pick":-3.5}]}`. Recheck the current server schema;
stop on incompatibility. The contest pick is **negative home margin**, not the
side selected for market ATS grading. See #47 for verified sign/mapping evidence.

Preserve identical existing picks and stop on conflicts. Retain an attempt
record before submitting only missing picks. Read every intended pick back and
require exact values before declaring success. After a timeout or uncertain
POST response, reconcile read-only; never repeat the write solely because a
local receipt is missing. If games have started, do not upload the stale full
slate. Preserve any already completed website publication while reporting the
contest exception.

## Finish, retry or report a partial outcome

Keep website and contest receipts separately with per-step state. Reconcile
remote state before resuming an incomplete lane; do not repeat the successful
lane. Report source freshness, completed-game count and grading totals,
upcoming forecast count, provider coverage, site/run links and contest verified
count in the current T3 thread and linked weekly task. Keep raw source/account
records private. No external messages are authorized by the schedule.

The first scheduled end-to-end success requires actual source/meter evidence,
graded and issued artifacts, verified live website and exact contest readback.
A configured timer or offline test is not that evidence. New code, unexplained
live drift, failed post-deployment verification or recovery writes stop for the
owner; do not automatically roll back or weaken validation.
