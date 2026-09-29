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
