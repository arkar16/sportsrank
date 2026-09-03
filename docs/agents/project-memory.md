# Project memory for agents

SportsRank keeps three different kinds of memory because each answers a
different question. Treating them as interchangeable destroys context.

## Codex Workflow: where are we now?

The files under `agent_docs/` are operational memory. They describe the current
goal, verified repository state, work completed, evidence collected, next
milestone, and continuation point. Use the selected Codex Workflow route to
coordinate execution and keep this state coherent across sessions and agents.

A workflow document may change whenever the project advances. Its value is an
accurate handoff, not a permanent historical record.

## Context: what do our words mean?

`CONTEXT.md` is the domain glossary. Read it before introducing terminology or
interpreting terms such as Classification, Ranking Run, Release, and
Publication. Update it when the domain meaning is clarified. Keep
implementation choices out of it.

## ADRs: why is the system shaped this way?

The files under `docs/adr/` are decision memory. They preserve accepted,
hard-to-reverse choices whose tradeoffs are not obvious from the code. Read the
relevant ADR before proposing a conflicting architecture. Add or supersede an
ADR when a real tradeoff produces a new durable decision; preserve the old
record so future agents can understand the change.

An ADR is not a task log, progress report, implementation plan, or description
of whatever the code happens to do today.

## Working sequence

1. Read the current Codex Workflow state to locate the active goal and evidence.
2. Read `CONTEXT.md` and relevant ADRs before making domain or architecture decisions.
3. Execute through the selected workflow route and verify observable outcomes.
4. Update operational state through the workflow handoff.
5. Record only qualifying durable tradeoffs as ADRs and clarified domain language in `CONTEXT.md`.

Completion means a future agent can answer all three questions without
guessing: where the project is, what its terms mean, and why its durable
architecture was chosen.
