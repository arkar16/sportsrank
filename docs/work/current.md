# Current work

Offline machine-learning evaluation with the local CFBD Starter Pack:
[#75](https://github.com/arkar16/sportsrank/issues/75),
[PR #76](https://github.com/arkar16/sportsrank/pull/76). Prior-game advanced metrics
and saved CORS forecasts feed paired, earlier-season-only model comparisons.
Task/PR evidence owns results and acceptance. Hosted weekly generation remains
separate in #54.

Direct Firebase publication and publishing the retained Week 7/8 website are
tracked in [#68](https://github.com/arkar16/sportsrank/issues/68), under
[ADR-0026](../adr/0026-restore-direct-static-publication.md). Use the
[current publication runbook](../operations/direct-publication.md). The old
prepare/seal/execute release procedure is retired. Publication evidence belongs
to #68 and parent #64/#66; do not infer live success from a prepared package.

The T3 Monday timer was deleted October 18. There is no active recurring writer;
hosted generation in #54 remains separate. The retained October 18 data contains
Week 7 results/ratings and all 56 Week 8 forecasts; the last contest readback
verified 55/56. Publishing those files requires no additional source requests.

## Earlier work references

- Weekly data/publication: [#64](https://github.com/arkar16/sportsrank/issues/64),
  merged #65; credential repair [#66](https://github.com/arkar16/sportsrank/issues/66),
  merged #67. #68 replaces the pending sealed recovery with direct publication.
- Accepted season-page layout: #50 / merged #51; live acceptance follows #68.
- Weekly generation/contest: #48. Hosted migration: #54, not activated.
- Corrected historical baseline, performance and provider reporting: merged
  #34/#36/#38/#40; preserve those accepted values and original forecasts.

## Merged prerequisites and parked work

- Forecast preservation [#14](https://github.com/arkar16/sportsrank/pull/14),
  permanent 2025 ranking progression [#15](https://github.com/arkar16/sportsrank/pull/15),
  and retained published-spread grading [#22](https://github.com/arkar16/sportsrank/pull/22)
  are merged. ADR-0019 and ADR-0020 remain their domain authorities.
- The SR-24 native Firebase audit parser [#24](https://github.com/arkar16/sportsrank/pull/24)
  and exact public-workbook transport repair [#25](https://github.com/arkar16/sportsrank/pull/25)
  are merged. Read the current audit/publication evidence before asserting that
  deployed state is fully verified; a merge alone does not establish that.
- Excitement and conference projections are
  parked by owner direction. Preserve their existing branches and evidence.
  The selected forecast-comparison providers are recorded in
  [#23](https://github.com/arkar16/sportsrank/pull/23) and now reconciled into
  ADR-0019 with the October 11 implementation direction. Excitement’s separate
  market policy remains outside this delivery.

## Current-season source and publication boundaries

The October 7 allowance of one metered 2026 games refresh is spent. It reused
cached teams and completed successfully; subsequent builds are offline. Retain
the original source versions and new snapshot privately on the owner’s computer.
The full historical chain is reconstructed under the expanded source bundle,
as required by the current-season extension procedure. Preserve issued forecasts;
reconstructed performance values do not overwrite authenticated pregame forecasts.

On October 11 the owner approved needed API requests after a bounded search for
the provider-survey cache. The selected-line refresh and private retention are
tracked in #37. Reporting/export still make zero live calls; do not infer another
allowance from completed acquisition evidence.

The owner explicitly selected T3-injected `CFBD_API` for this continuation and
prohibited BB CLI/Tasks use. Keep task evidence in the delivery PR/local fallback;
do not infer live tracker status from historical exports. Production approvals follow ADR-0026 and the explicit authority
recorded in each delivery.

## Operational and historical sources

- [Recovery contract](../plans/2026-p0-recovery-and-backfill.md)
- [Publication runbook](../operations/direct-publication.md)
- [ADR index](../adr/README.md): 0026 for current publication; earlier records retain historical context
- [Historical Gate 1 evidence](../evidence/2026-gate1.md)
- [Frozen postseason repair handoff](../archive/postseason-repair/2026-09-09/README.md)
- [Tracker configuration](../agents/issue-tracker.md)

Tasks own current status, verification and residual risk. These pointers do not
authorize a new fetch, deployment, rollback or claim replay.
