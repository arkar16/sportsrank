# SportsRank agent guide

## Load project memory

Before planning or changing architecture, module boundaries, hosting,
deployment, data ownership, public URLs, or ranking contracts:

1. Read [`docs/agents/project-memory.md`](../docs/agents/project-memory.md) to understand how project memory is divided.
2. Read [`CONTEXT.md`](../CONTEXT.md) for canonical domain language.
3. Read the relevant accepted records under [`docs/adr/`](../docs/adr/).
4. Codex agents also follow the local `AGENTS.md` and Codex Workflow state under `agent_docs/`.

Codex Workflow documents describe current execution state and verified evidence.
ADRs preserve why hard-to-reverse choices were accepted. Keep both current in
their respective roles; neither replaces the other.

## Current architectural guardrails

- Make CFB, beginning with FBS, reliable before implementing another sport or classification.
- Keep sport-specific ingestion and ranking rules inside the sport boundary; share only release, validation, and publication capabilities.
- Publish complete static HTML and CSS. JavaScript may progressively enhance pages but does not own core content.
- Run scheduled generation on the Raspberry Pi and serve the public site from Firebase Hosting.
- Preserve existing public historical URLs or provide explicit redirects.
- Preserve the current CORS model unless a task explicitly authorizes a model change.
- Load the CFBD bearer token only from `CFBD_API_KEY`; keep credentials out of source, arguments, generated pages, fixtures, and logs.
- Validate a complete candidate Release before publication and retain the last known-good public Release on failure.

## Verification standard

Prefer tests of observable behavior: recorded CFBD contracts, manually verified
ranking examples, mathematical invariants, stable URL contracts, and complete
offline Release generation. Source-text assertions are temporary migration
guards, not long-term proof. A test earns its place by catching a plausible
regression in a user-visible or operational contract.
