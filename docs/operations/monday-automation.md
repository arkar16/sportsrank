# Monday SportsRank operation

The T3 schedule runs Mondays at 08:00 in the host's Eastern timezone
(`America/Indiana/Indianapolis`), posting into the existing project thread.
[#48](https://github.com/arkar16/sportsrank/issues/48) owns the scheduler ID,
next-run evidence, delivery acceptance and first-run results.
[ADR-0025](../adr/0025-authorize-routine-weekly-publication.md) records the owner's
standing authority and its limits. This is an agent task using existing tools,
not a standalone weekly executable or an always-on hosted service. Keep the T3
host available and verify the next run if its timezone changes.

## Start or resume a cycle

Read the task, current work and the current publication/contest receipts. Use
one cycle in flight, keyed by season and upcoming slate, with private output
under `.sportsrank/weekly/<cycle>/`. A timer firing again is not permission to
repeat a completed upload or deployment. Resume from retained attempt journals
and authoritative remote state. Never replace a pinned source or package.

The initial corrected site in #39 must be fully published and verified before a
new weekly website successor. Acquire the current authenticated predecessor and
latest complete private request ledger; do not infer either from a filename or
an old success log. The completed #47 package is dated reference evidence for
contest preparation; do not rerun its hardcoded Week 7 scripts or payload.

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

Use the existing full-site preparation/export procedure and independent source,
model, forecast-preservation and URL checks. Follow
[the publication runbook](2026-season-recovery-morning.md) for exact transport,
immutable reference handling and verification. Make one generated-data PR per
weekly publication, with receipt and necessary source-pin updates. Run the
required checks, register the PR in T3, and merge only when the diff is within
this scope. Source/model/workflow edits require separate owner review.

Prepare the actual merged SHA, then seal and execute in separate fresh runs.
Before each production approval, verify the repository, workflow, main branch,
first attempt, operation/purpose, candidate/package hashes, current predecessor
and the cycle's scope. Approve only that run's pending `production` environment
through the owner's authenticated GitHub account using the ordinary pending-
deployment review API. Include an approval comment identifying the weekly cycle,
#48 and automated approval under ADR-0025. Do not change environment protection,
use an administrative bypass, or imply that the owner manually clicked.

Retain the actual emitted sealed reference including its newline. Never guess
an intent tag, rerun a consumed attempt or deploy from a mutable branch. After
execution, require complete inventory/configuration and managed-resource
verification plus representative public result/ranking/forecast/performance
pages. A job conclusion alone does not establish website success.

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
