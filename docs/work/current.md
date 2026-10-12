# Current work

The active outcome is site-wide CORS model performance from retained predictions
and results, including prior-rating reconstruction where predictions are missing.
[#33](https://github.com/arkar16/sportsrank/issues/33) owns scope and acceptance;
[PR #34](https://github.com/arkar16/sportsrank/pull/34) owns implementation and
review. The owner clarified that recreational performance does not require
historical-source qualification or proof of pregame publication. Preserve the
CORS model and existing forecast records; see ADR-0019's October 9 clarification.

The Week 5 rankings/results and Week 6 slate were deployed October 8 and passed
[full immutable verification](https://github.com/arkar16/sportsrank/releases/tag/run-37822116361-verification).
The owner accepted [#27](https://github.com/arkar16/sportsrank/issues/27) on
October 9; it is closed. Its prerequisite repairs #30 and #32 are merged.

## Merged prerequisites and parked work

- Forecast preservation [#14](https://github.com/arkar16/sportsrank/pull/14),
  permanent 2025 ranking progression [#15](https://github.com/arkar16/sportsrank/pull/15),
  and retained published-spread grading [#22](https://github.com/arkar16/sportsrank/pull/22)
  are merged. ADR-0019 and ADR-0020 remain their domain authorities.
- The SR-24 native Firebase audit parser [#24](https://github.com/arkar16/sportsrank/pull/24)
  and exact public-workbook transport repair [#25](https://github.com/arkar16/sportsrank/pull/25)
  are merged. Read the current audit/publication evidence before asserting that
  deployed state is fully verified; a merge alone does not establish that.
- Excitement, conference projections and market-comparison implementation are
  parked by owner direction. Preserve their existing branches and evidence.
  The selected forecast-comparison providers are recorded in
  [#23](https://github.com/arkar16/sportsrank/pull/23); that decision does not
  resume implementation or settle excitement’s separate market policy.

## Current-season source and publication boundaries

The October 7 allowance of one metered 2026 games refresh is spent. It reused
cached teams and completed successfully; subsequent builds are offline. Retain
the original source versions and new snapshot privately on the owner’s computer.
The full historical chain is reconstructed under the expanded source bundle,
as required by the current-season extension procedure. Preserve issued forecasts;
reconstructed performance values do not overwrite authenticated pregame forecasts.

The owner explicitly selected T3-injected `CFBD_API` for this continuation and
prohibited BB CLI/Tasks use. Keep task evidence in the delivery PR/local fallback;
do not infer live tracker status from historical exports. GitHub’s protected
production approvals remain human actions.

## Operational and historical sources

- [Recovery contract](../plans/2026-p0-recovery-and-backfill.md)
- [Publication runbook](../operations/2026-season-recovery-morning.md)
- [ADR index](../adr/README.md): 0011/0012/0016/0017/0018 for publication
- [Historical Gate 1 evidence](../evidence/2026-gate1.md)
- [Frozen postseason repair handoff](../archive/postseason-repair/2026-09-09/README.md)
- [Tracker configuration](../agents/issue-tracker.md)

Tasks own current status, verification and residual risk. These pointers do not
authorize a new fetch, deployment, rollback or claim replay.
