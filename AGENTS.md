# SportsRank

Build CFB/FBS CORS rankings and a permanent static statistical-reference site.

## Start with the task

- For resuming project work or choosing the next design topic, read
  `docs/work/current.md`. For a specific ticket, start with that ticket.
- Read `CONTEXT.md` for domain work and the relevant ADRs using `docs/adr/README.md`.
  Read `docs/architecture/overview.md` when changing module boundaries.
- Before changing recovery or publication, read the applicable sections of
  `docs/plans/2026-p0-recovery-and-backfill.md` and the publication runbook linked
  from current work.
- For documentation changes or handoffs, use `docs/agents/project-memory.md`.

## Product and authority boundaries

- Preserve historical public URLs and the current CORS model unless explicitly
  authorized to change them. Keep core content in static HTML/CSS.
- Every CFBD request crosses the metered Season Snapshot boundary. Ordinary
  tests and cached rebuilds make zero live calls. Fetch allowances require
  explicit authorization; an old plan is not a fresh allowance.
- Read source-data credentials only from T3-injected `CFBD_API` and model-contest
  credentials only from T3-injected `CFBD_PRED_API`. Keep both values out of
  source, arguments, fixtures, generated pages, logs, and messages.
- Owner approval governs consequential product and
  architecture changes, merges, and production publication. Technical acceptance
  does not authorize those actions.

## Delivery and delegation

Use ask-matt skills for design and delivery. Resolve consequential choices in
ADRs, then define scope, acceptance, dependencies, and ownership in a spec/ticket.
Use T3's native orchestration for authorized delegation. Read the selected role
from `~/.codex/agents/` and the shared routing policy in
`~/.codex/coding-policy.md`; use `~/.codex/worker-handoff.md` for assignments.
Keep project constraints in the task brief; user-level files own role behavior
and model settings. Continue the current thread unless the owner requests another.
Run the offline checks in `README.md` for code/build changes; add focused
regressions for changed contracts. Keep acceptance independent of renderer
assumptions.

## Agent skills

### Issue tracker

Use GitHub issues for task management under the active tracker fallback;
see `docs/agents/issue-tracker.md`. BB CLI/Tasks requires an explicit owner request.

### Triage labels

Use the default five-role vocabulary when triaging incoming issues;
see `docs/agents/triage-labels.md`.

### Domain docs

Use the single-context glossary and ADR layout; see `docs/agents/domain.md`.
