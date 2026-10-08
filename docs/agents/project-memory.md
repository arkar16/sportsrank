# Project knowledge and task context

Each fact has one maintained home. Links and short navigation summaries may
repeat; policies, acceptance criteria, and evolving status should not.

| Question | Canonical home |
| --- | --- |
| How should an agent work here? | Root `AGENTS.md` |
| How do I run a design/delivery process? | Installed ask-matt skills, loaded on demand |
| What do domain terms mean? | Root `CONTEXT.md`, glossary only |
| Why did we choose this? | Accepted `docs/adr/` records |
| How is the current implementation organized? | `docs/architecture/overview.md`; code owns exact interfaces |
| What should this task deliver? | Its BB task and linked canonical spec/plan; GitHub issue when using the tracker fallback |
| What is active and what happens next? | `docs/work/current.md`, a short pointer to active work |
| What proves a completed result? | Task/PR evidence and `docs/evidence/` references |
| What happened in the old workflow? | Frozen `docs/archive/codex-workflow/2026-09-10/` |
| Where is the execution running? | BB threads and environments |

## Design, then execution

Use ask-matt to select a skill. Design may stay in one conversation while
terms and decisions crystallize. Record hard-to-reverse, surprising trade-offs
as ADRs; use a spec for behavior and acceptance rather than turning every task
into an ADR. Supersede an old ADR when an accepted decision changes.

For a multi-session build, create self-contained tickets with blocking edges.
Each ticket names its canonical spec, relevant ADRs, scope, owned files or
boundaries, acceptance evidence, and unresolved decisions. A small task may
stay in the design conversation without producing tickets.

Start a fresh orchestration thread when the build contract is ready.
Supply the ticket/spec rather than the complete design transcript. The agent
reads relevant primary documents and follows the delegation policy in
`AGENTS.md`. BB manages execution; the linked task remains the portable scope
and acceptance record. A script is optional for a repeatable BB pipeline.

## Updating and resuming

Keep `docs/work/current.md` short: active task links, current position, next
decision/action, and blockers. Detailed evidence lives with its task; historical
test counts and experiment logs do not belong in always-loaded instructions.
Once a local spec becomes a GitHub issue, make one authoritative and replace
the other with a pointer rather than maintaining mirrored requirements.

A handoff is needed when relevant work must travel to another session, directory,
or person. Record missing evidence explicitly, including local or temporary
paths that are unavailable in a fresh checkout. Past verification is not a
claim that the current checkout was just tested.

Keep stable instructions stable; read task-specific material when needed and
re-read changed sections when freshness matters. Continue a coherent task while
its context remains useful; use a fresh ticket thread for independent work.
Avoid loading archives or creating workers merely to maintain the process.

## Migration boundary

ADR-0015 replaces SportsRank's Codex Workflow requirement. The repository has
no managed workflow entry points or local workflow state. Its 2026-10-08
amendment moves worker definitions to the user's shared `~/.codex/agents/`;
read `~/.codex/AGENTS.md` for dispatch policy. This shares Codex roles without
restoring workflow routes or lifecycle requirements. Task briefs supply the
project-specific constraints and acceptance criteria.
