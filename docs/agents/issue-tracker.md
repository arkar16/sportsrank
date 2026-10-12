# Issue tracker: active GitHub fallback

Use GitHub Issues in `arkar16/sportsrank` for current task management. The owner
selected T3 orchestration and prohibited BB CLI/Tasks unless explicitly requested;
this activates the existing GitHub/local-draft fallback. T3 owns execution state,
GitHub issues own task scope and acceptance, and PRs own code review.

## Task workflow

Read the relevant issue before implementation with `gh issue view NUMBER --repo
arkar16/sportsrank --comments`. Inspect existing issues before creating one;
continue the same delivery task rather than duplicating it for each review round.

Use one coherent delivery outcome per atomic PR. Its issue records acceptance,
non-goals, ownership, dependencies, applicable ADRs, meaningful progress, and
residual risks. Link the real PR in the issue. When native dependency links are
unavailable, record explicit `Blocked by: #N` relationships; parent/subtask
relationships alone do not establish readiness. Human acceptance and merge are
required before marking implementation done; a publication task additionally
requires its specified live verification.

Use normal `gh issue create/edit/comment/close` operations for authorized task
work. Write multiline content to a body file and use `--body-file`. When tracker
access is unavailable, retain a local draft under `.scratch/` and report that
publication remains pending. Once published, keep one authoritative requirements
record and replace the local draft with a pointer.

Historical BB/SR identifiers remain references to dated evidence and the
canonical recovery contract. Do not infer current task status from old exports
or migrate those records just to populate the active tracker. Consult
[the work index](../work/current.md) for current deliveries.

PRs as a request surface: no. Triage vocabulary: [triage labels](triage-labels.md).
This configuration defines task handling; it does not grant new API, merge, or
publication authority.
