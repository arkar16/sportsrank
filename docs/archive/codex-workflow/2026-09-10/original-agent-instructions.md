<!-- codex-workflow-id: viettran-edgeAI/codex_workflow -->
<!-- codex-workflow-managed-start -->
# AGENTS.md

## Design Principles

- Keep modules cohesive, interfaces explicit, coupling minimal, and behavior
  testable, replaceable, and reusable.
- Define proportionate acceptance and verification before implementation. Never
  weaken coverage, assertions, or failure visibility to save time or tokens.
- Avoid unnecessary process or safeguards; preserve unrelated user work and use
  verified facts in durable documentation.

## Working State

- `deployment state`: planning or executing a broad, possibly multi-session
  deployment plan.
- `leaf state`: otherwise, including general questions and small bounded
  operations.

## Project Documentation

Use the durable project documents under `agent_docs/`:

- `project_overview.md`: goals, architecture, workflow, and major decisions.
- `project_core_tech.md`: concise special technology or architecture notes.
- `project_structure.md`: layout, modules, components, and ownership.
- `project_progress.md`: goal, overall progress, current position, next milestone.
- `project_diary.md`: distilled decisions, discarded approaches, mistakes, and
  reusable lessons.
- `latest_session_work.md`: detailed handoff evidence and continuation point.
- Module-specific documents, when present.

In deployment state, you own `project_progress.md`, `project_diary.md`, and
`latest_session_work.md`. Before closure, directly record the current goal and
continuation state, concise lasting lessons, and the verified deployment
handoff in their canonical documents. Archivist owns other assigned project and
public documentation from verified facts, including overview, structure, core
technologies, and module documents, and performs the closing documentation and
reporting handoff. Require concise edits that remove stale or redundant detail,
assign module documents explicitly, and perform a direct user-requested
document edit yourself outside deployment.

Keep raw logs, temporary reasoning, and short-lived checkpoints out of durable
documents; give each fact one canonical home. Never delete a main project
document without warning and a second explicit confirmation.

## Route Selection

Select one of these routes: **Light** works directly in leaf state without subagents;
**Medium** keeps planning, diagnosis, implementation, and verification with the
main agent and uses bounded support from `~/.codex/codex_workflow/medium_route.md`;
**Heavy** delegates bounded production, verification, documentation,
project-context, and Internet research under `~/.codex/codex_workflow/heavy_route.md`.

Follow the user's route selection. Use Light when none is selected; do not infer
Medium or Heavy. Keep the route until the user changes it or the session ends.
Enter deployment state for Medium or Heavy only when the work is substantive.

## Rollout Efficiency

Batch independent reads, searches, metadata checks, and other known-input
operations. Keep dependencies and overlapping mutations sequential. In Medium or
Heavy, dispatch independent workers, wait for the
relevant set, and synthesize their reports once.

Read personalization and project-local instructions from the protected regions
at the end of this file. Apply them over workflow defaults subject to higher
instruction priority.

## Required Documentation Read

On the first `deployment state` entry under either Medium or Heavy, immediately
create one persistent Companion with `agent_type="companion"`,
`task_name="companion"`, and `fork_turns="none"`, or reuse the existing target.
Do this before planning, modifying files, or dispatching any other worker. Reuse
that Companion after route changes; do not create a second one.

Give its first assignment the current route, goal, relevant constraints, and a
bounded diary/module intake or other substantial context consolidation. It
retains supporting detail and returns only a task-relevant director brief.

If you have not already completed the session-level intake, directly read the
complete current `agent_docs/` framework exactly once: overview, core
technology, structure, progress, diary, latest session work, and every
module-specific Markdown document. This one direct read is shared across Medium
and Heavy. Never repeat it later in the session. Use retained context or assign
Companion a bounded diary/module intake, large synthesis, delta, or conflict
check when freshness or detailed supporting context matters. Missing or
unreadable required documents leave deployment entry incomplete; report the
intake blocker.

Do not overuse Companion. Each rollout reloads its persistent context. Combine
related questions, reuse earlier findings, and avoid status-only requests, tiny
lookups already answerable from main context, or repeated broad summaries. Use
it when one consolidated result replaces multiple main reads or tool turns,
suppresses bulky evidence, or will be reused later.

## Platform Paths

Interpret `/` as a platform-neutral separator and translate paths for the
current operating system and shell.
<!-- codex-workflow-managed-end -->

<!-- codex-workflow-project-personalization-start -->
<!-- codex-workflow-project-personalization-end -->

<!-- codex-workflow-project-local-instructions-start -->
## Project Memory Model

Before planning or changing architecture, module boundaries, hosting,
deployment, data ownership, or ranking contracts, read
`docs/agents/project-memory.md`, `CONTEXT.md`, and the relevant records under
`docs/adr/`. Codex Workflow documents and ADRs are complementary sources of
truth: workflow documents carry current execution state and evidence; ADRs
carry durable decision rationale.

Before implementation or publication, treat any contradiction among agent
instructions, `CONTEXT.md`, accepted ADRs, active plans, workflow state, and
executable interfaces as a work blocker. Reconcile every affected source in
the same change so human and agent readers receive one complementary contract;
never resolve the conflict by silently choosing a preferred document.
<!-- codex-workflow-project-local-instructions-end -->
