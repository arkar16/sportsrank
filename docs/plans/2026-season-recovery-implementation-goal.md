# SportsRank 2026 Season Recovery — Implementation Goal

Use this document as the complete opening prompt for a new Codex task. The
existing **ADR / Implementation Review** task is the independent final-review
lane and must not implement or repair this work.

## Goal

Use **Goal Mode with the Heavy route** to prepare a local, review-ready
implementation diff for SportsRank's 2026 CFB season recovery.

Work in `/Users/aryakarnik/Developer/sportsrank`. Start from the recovery
baseline commit `0f40dbd3` (tag
`baseline/static-cfb-v2-2026-09-03`) and create or use the local branch
`feature/season-2026-recovery`. Preserve the existing uncommitted planning
documents and reconcile them deliberately; they are user work, not disposable
scratch files.

The result must:

- update the overnight implementation plan and current progress evidence;
- keep every action and commit local;
- pause automatic GitHub Actions and Firebase deployment triggers while
  retaining an explicit manual path;
- upgrade supported dependencies and stale workflow actions;
- implement one deep CFBD **Season Snapshot** seam through which every CFBD
  data request passes;
- implement a persistent **Request Meter** with request budgets, gates,
  caching, audit records, and failures that clearly explain the problem
  without exposing credentials;
- provide a production CFBD adapter and a recorded-fixture adapter with the
  same contract;
- add meaningful offline behavioral tests, especially tests that prove call
  counts and prevent API-budget regressions;
- add staged static **Release** generation and validation wherever it can be
  completed safely without credentials;
- preserve the existing CORS ranking model and public URL contracts; and
- leave a precise morning checklist for the CFBD-key smoke test, ordered
  2024→2025→2026 catch-up, Raspberry Pi provisioning, Firebase publication,
  and all remaining credential-dependent work.

Do not make live CFBD calls, expose or invent credentials, push Git changes,
deploy Firebase, modify the Raspberry Pi, rewrite Git history, change the CORS
model, or perform a mass rewrite of generated public pages.

## Required Context

Before planning or editing, read:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/agents/project-memory.md`
- every relevant record under `docs/adr/`, especially ADRs 0001–0009
- `docs/plans/2026-season-recovery.md`
- `REPOSITORY_AUDIT.md`
- the Heavy-route, Companion, investigation-team, and Closure Steward workflow
  guides referenced by `AGENTS.md`

Treat Codex Workflow documents and ADRs as complementary:

- Codex Workflow records live execution state, evidence, progress, and
  handoffs.
- `CONTEXT.md` defines the canonical domain vocabulary.
- ADRs retain the reasons behind architectural decisions that future agents
  must not casually reverse.

## Product and Architecture Constraints

- Scope is CFB, beginning with FBS. Preserve modular boundaries for later FCS,
  lower-division, and other-sport expansion.
- Future classifications receive separate rankings, while their scores should
  eventually use a comparable scale. The calibration mechanism remains an
  unfinished decision.
- The public product is a barebones statistical-reference website. Core
  content is complete static HTML and CSS; JavaScript is progressive
  enhancement only.
- Firebase remains the public static host. The Raspberry Pi will run
  deterministic scheduled generation using `systemd`; OpenClaw is another Pi
  workload, not a production dependency.
- Every published weekly ranking, final ranking, spread, result, supporting
  page, and release remains available permanently with a last-update timestamp.
- Corrections may update a canonical URL, but prior revisions belong in
  immutable Release archives.
- SQLite may become the build-time structured store on the Pi/T7. It must not
  become a runtime dependency for the public website.
- Continue serving the last-known-good release whenever fetching, generation,
  or validation fails.

## CFBD Request Contract

The CFBD account limit is 3,000 calls per month. Make efficient use of it an
enforced behavior, not a convention.

- All CFBD access crosses one deep Season Snapshot and Request Meter boundary;
  no module may bypass it.
- Fetch teams once at season initialization.
- Share the same cached snapshot across records, results, slates, rankings,
  spreads, rendering, and validation.
- Rerender, revalidate, and redeploy consume zero CFBD calls.
- Start with tunable limits of 100 scheduled-production calls per month, 500
  historical-maintenance calls per month, and an absolute stop at 2,500 calls,
  preserving 500 calls as reserve.
- Do not automate CFBD's usage-info endpoint yet. Usage reconciliation remains
  a deliberate manual calibration task.
- Gate before the outbound request. Record attempts and outcomes durably.
- Failures must return a nonzero status, preserve the last-known-good release,
  and make the reason easy to find. Logs and artifacts must never reveal the
  API key or authorization header.

Behavioral coverage must prove at least:

- a fresh season requires no more than two data calls;
- a cached weekly calculation requires zero calls;
- a Monday refresh requires one games call once teams exist;
- rerender, revalidation, and redeployment require zero calls;
- historical backfill requires no more than two calls per season;
- budget exhaustion stops before any HTTP request;
- post-fetch failures can retry without refetching;
- incomplete upstream data refetches games only;
- all request paths are metered; and
- fixture and production adapters satisfy the same consumer contract.

Use tests that exercise observable behavior. Replace temporary source-text
guards when a behavioral assertion can prove the same contract. Do not add
tests merely to increase the count.

## Release and Validation Contract

Build a candidate Release in staging, validate it completely, and promote it
only when every invariant passes. Implement as much of this path as is safely
possible offline.

The validation gate must prove:

- identical inputs and model version produce deterministic outputs;
- every expected FBS team appears exactly once;
- ranks are unique and contiguous;
- required CORS, record, and spread values are present and finite;
- ordering and tie-breaking follow a documented rule;
- records reconcile with completed games;
- future or incomplete games are excluded;
- spreads reference valid teams;
- all required ranking, spread, history, navigation, and metadata artifacts
  exist;
- internal links resolve;
- every public page displays its last-update timestamp; and
- any failed invariant prevents publication and returns a nonzero status.

Preserve existing public URLs. An unchanged candidate must not trigger a
deployment.

## Season Recovery Order

The latest complete final is 2023. The 2024 archive stops at Week 9, 2025 is
only an empty shell, and 2026 is absent. Because the ranking model carries
season history forward, recovery must run in this order:

1. complete 2024 and establish its final ranking;
2. generate and finalize 2025 from the corrected 2024 state;
3. generate 2026 Week 0 from the corrected 2025 final state.

Record follow-up work for auditing and correcting prior-season carryover. Do
not perform live catch-up without the project-only CFB key and explicit morning
review.

## Morning Handoff

Produce a concise, exact checklist covering:

1. how the user supplies `CFBD_API_KEY` locally without committing or pasting
   it into chat;
2. one minimal authenticated smoke test and its expected call count;
3. how to inspect Request Meter records and confirm no credential leakage;
4. the dry-run and validation commands for 2024, 2025, and 2026 in order;
5. the explicit approval point before any public Firebase publication;
6. Raspberry Pi/T7 prerequisites and later `systemd` provisioning steps;
7. backup and recovery work still awaiting a decision, including possible
   off-device Google Drive storage; and
8. every remaining item blocked on credentials or external infrastructure.

Prepare logical local commits when useful, but do not push them. Keep unrelated
user changes intact.

## Verification and Completion

Use Heavy-route workers for bounded implementation and independent testing,
with explicit ownership and the repository's coordination rules. Complete the
required Closure Steward handoff exactly once at the end.

Before claiming completion, audit every requirement in this document against
the current diff, local commits, tests, generated fixture artifacts, and
documentation. A narrow green test is not evidence for a broader requirement.
Report any incomplete requirement plainly.

When the local implementation and independent verification are complete, send
a handoff to the existing reviewer task with `send_message_to_thread`:

- reviewer task ID: `01a04ff4-cff1-7170-91bb-2c235632d2d3`
- include the branch name, baseline, commit list, diff summary, tests and exact
  results, fixture Release location, known limitations, credential-blocked
  steps, and the precise review request

The reviewer task must inspect and report findings independently. It must not
repair the implementation.
