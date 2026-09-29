# Issue tracker: BB Tasks, with GitHub fallback

Use the **SportsRank** BB Tasks project (`SR`, linked to BB project
`proj_dmp29kzewi`) for new design and delivery tasks. The task owns scope,
acceptance, dependencies, progress, and readiness; attach the working BB thread.
Existing recovery requirements remain in their canonical `docs/plans/` contract.
GitHub PRs remain the code review surface. Do not duplicate BB tasks as issues.

If BB Tasks is unavailable, use the GitHub/local-draft fallback below.

## BB Tasks workflow

Read tasks with `bb tasks show SR-N`; discover work with
`bb tasks list --project SR`. Create authorized tasks with
`bb tasks create --project SR --title TITLE --description-file FILE`.
Use `bb tasks update`, `bb tasks comment`, and `bb tasks attach` to maintain
status, evidence, and the working thread. Inspect command help before use.

Keep each implementation task scoped to one coherent, reviewable PR. Record
acceptance, ownership, applicable ADRs, and explicit `Blocked by: SR-N` edges
in its description when native blocking links are unavailable. Parent/subtask
relationships alone do not express blocking dependencies. Design tasks may
remain in progress while dependent implementation is not ready; readiness
requires resolved blockers and the applicable execution authorization.

Use the installed Tasks skill for command details and task links. Triage labels
are defined in `docs/agents/triage-labels.md`; labels do not authorize dispatch.

## Wayfinding operations

A `wayfinder:map` task is the canonical decision index. Its child tasks carry
`wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or
`wayfinder:task` labels. Keep accepted answers in child resolution comments;
the map links them by title. Research reports are task attachments.

The current CLI exposes neither assignees nor native blocking links. Claim a
child by setting it `in_progress`, attaching the driving thread, and recording
ownership in a comment before work. Use explicit `Blocked by: SR-N` entries in
child descriptions. From `bb tasks show <map> --json`, inspect open children;
the frontier contains those with resolved blockers and no active claim, ordered
by task number. A parent relationship alone is not a blocking edge.

On resolution, post the answer, mark the decision task `done`, detach its
working thread, and add a title link to the map. Research may resolve with a
documented evidence gap; any resulting owner decision remains open in a
separate child. Close canceled/out-of-scope children as `canceled`. Delivery
tasks retain their separate acceptance and merge requirements.

## GitHub fallback

Use GitHub Issues in `arkar16/sportsrank` for published specs and tickets.
This follows the repository's GitHub remote. Local design drafts may live under
`.scratch/<feature>/`; existing recovery requirements remain in the linked
`docs/plans/` contract. Do not migrate or duplicate existing requirements merely
to populate the tracker. BB threads link to the authoritative work record.

When a skill asks to fetch a ticket, use `gh issue view NUMBER --repo
arkar16/sportsrank --comments`. When the user authorizes publishing specs/tickets,
use `gh issue create --repo arkar16/sportsrank --title TITLE --body-file FILE`.
Use body files for multiline issue/PR content and comments. Preserve native
blocking relationships for dependent tickets; if unavailable, record explicit
`Blocked by: #N` links. A ticket is executable only when its blockers are resolved.

When publishing a local draft, choose one requirements source and make the
other a pointer. Include scope, acceptance, ADR links, ownership, and dependencies.
Use normal `gh issue` read/edit/close operations for authorized tracker work.
This configuration itself sends no messages and creates no issues or labels.

PRs as a request surface: no.
Triage vocabulary: `docs/agents/triage-labels.md`.
