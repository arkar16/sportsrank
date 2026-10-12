---
status: accepted
---

# Authorize routine weekly publication through T3

On October 12, 2026 the owner selected Monday at 08:00 Eastern and explicitly
requested fully unattended weekly grading, forecasts, website publication and
model-contest submission. T3's persistent scheduler will run this operation in
the existing project thread. [#48](https://github.com/arkar16/sportsrank/issues/48)
owns delivery and the [Monday runbook](../operations/monday-automation.md) owns
operator procedure.

This decision narrowly amends ADR-0016's human-only approval boundary. For an
independently validated routine weekly update using the accepted model and
publication code, the owner delegates generated-output/source-pin/receipt PR
merge and ordinary GitHub production gate approval to T3. The agent records
that approval as automated under this standing delegation; it must not describe
it as a contemporaneous human click. The existing owner-account authorization,
protected environment, exact artifact binding, predecessor reconciliation,
separate first-attempt seal and execution, single-use claim and complete live
verification remain required. No protection is disabled or bypassed.

The delegation covers at most one current-season games request and one selected
provider-lines request per weekly cycle through the metered Season Snapshot
boundary, with cached teams/history and all prior request usage preserved. It
also covers authenticated contest submission and readback of the same issued
CORS forecasts. Preserve source versions, forecast cutoff and original issued
values; a newly fetched result or market line cannot rewrite pregame evidence.

Code/model/workflow changes, new-season membership or chronology policy gaps,
rollback, uncertain-write recovery and unexplained live-state differences remain
outside this delegation. Routine handling may reconcile an uncertain attempt
read-only; a new write after unexplained state requires an owner decision. The
historical correction release in #39 retains its original approval procedure.

This trades a separate human click each week for explicit standing authority
and recorded per-run checks. It does not create an independently hosted cron
service: execution depends on T3's host, credentials and private source storage
being available. Website and contest outcomes are recorded independently; a
partial failure is reported and cannot be represented as weekly success.
