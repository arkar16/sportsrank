---
status: accepted
---

# Retain both operational and decision memory

SportsRank will keep Codex Workflow documents for current execution state and ADRs for durable architectural rationale, with `CONTEXT.md` owning domain language. These records overlap in subject matter but not purpose: workflow state must evolve as work advances, while ADRs preserve why hard-to-reverse tradeoffs were accepted. Removing either would force future agents to reconstruct either the present state or the reasons behind it from code and Git history.
