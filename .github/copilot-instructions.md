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

If agent guidance, domain language, accepted ADRs, active plans, workflow
state, or executable interfaces contradict one another, stop before changing
code or publishing. Reconcile every affected source in the same change; the
conflict is a work blocker rather than permission to select whichever source
is convenient.

## Current architectural guardrails

- Make CFB, beginning with FBS, reliable before implementing another sport or classification.
- Keep sport-specific ingestion and ranking rules inside the sport boundary; share only release, validation, and publication capabilities.
- Publish complete static HTML and CSS. JavaScript may progressively enhance pages but does not own core content.
- Run scheduled generation on the Raspberry Pi and serve the public site from Firebase Hosting.
- Preserve existing public historical URLs or provide explicit redirects.
- Preserve the current CORS model unless a task explicitly authorizes a model change.
- Load the CFBD bearer token only from `CFBD_API_KEY`; keep credentials out of source, arguments, generated pages, fixtures, and logs.
- Route every CFBD request through the metered Season Snapshot seam; enforce the 3,000-call monthly ceiling and make ordinary tests use recorded adapters rather than live calls.
- Publish PRESEASON before Week 0; Week 0 contains real games and W0 is the ranking after those games.
- Build every candidate as a full overlay of the Published Site, derive its required artifacts independently, and retain the last-known-good site on failure.
- Deploy only the immutable static-site artifact that passed validation for the reviewed commit SHA.

## Verification standard

Prefer tests of observable behavior: recorded CFBD contracts, manually verified
ranking examples, mathematical invariants, stable URL contracts, and complete
offline Release generation. Source-text assertions are temporary migration
guards, not long-term proof. A test earns its place by catching a plausible
regression in a user-visible or operational contract.
