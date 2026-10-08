---
status: accepted
---

# Separate project knowledge from agent orchestration

SportsRank retains the operational, domain, and decision memory separation of
ADR-0004 while replacing its Codex Workflow dependency. The owner approved this
migration on 2026-09-10: use ask-matt for deliberate design, canonical specs and
tickets for delivery, and an Astra orchestrator with configured Codex workers
inside BB. Requiring every task to maintain and load overlapping workflow
documents adds context and maintenance cost without making their facts more
authoritative.

`AGENTS.md` provides essential constraints and selective pointers. `CONTEXT.md`
owns vocabulary, ADRs own decision rationale, architecture docs describe current
boundaries, and task records own scope and acceptance. One compact work index
points to current tasks and evidence. Skills own reusable process; model and
reasoning settings stay in project-local `.codex/agents/` configuration. BB owns execution state, not a
second copy of project requirements. The document ownership rules live in
`docs/agents/project-memory.md`.

The alternative was to retain Codex Workflow and add ask-matt/BB on top, keeping
multiple coordination and handoff obligations. We instead preserve its useful
bounded assignments, independent verification, and durable lessons without
requiring routes, Companion startup, whole-project intake, or closure reports.
Historical workflow records remain frozen and discoverable. This changes the
execution/documentation mechanism; existing product decisions, acceptance
criteria, source budgets, and publication approvals are not relaxed.

Supersedes [ADR-0004](0004-retain-operational-and-decision-memory.md).

## Shared role amendment — 2026-10-08

The owner requested a single source of Codex roles across projects. Repository
copies had retained older model settings after the shared roles changed. This
supersedes the project-local role ownership above: `~/.codex/agents/` owns worker
behavior, models, and reasoning; `~/.codex/AGENTS.md` owns shared dispatch policy.
`AGENTS.md` and project-memory guidance point to these sources, and the local
worker copies are removed. Explicit task-level user overrides remain authoritative;
the main agent uses the session's selected model.

This trades self-contained worker configuration in a clone for one maintained
user-level definition. Delegation requires those role files on the executing
machine; missing configuration must be reported rather than recreated from old
project pins. Sharing role definitions does not restore Codex Workflow routes or
change project constraints, acceptance, or merge/publication authority.
[Delivery task #28](https://github.com/arkar16/sportsrank/issues/28) owns validation
and merge status.
