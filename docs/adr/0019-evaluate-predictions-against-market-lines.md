---
status: proposed
---

# Evaluate model predictions against published betting lines

Placeholder requested by the owner on 2026-09-25. Future analysis should decide
how to evaluate CORS predictions against real betting lines available through
an API. The current `ats_correct` grades whether the model's favorite covered
its own predicted spread; that may not be the comparison we want this metric
to represent.

For example, Georgia Southern favored by 2 and winning by 19 counts as correct
in that current sense. It does not establish how a pick performed against a
published bookmaker line, which could differ from the model's line.

Resolve before implementation:

- Verify available API feeds and bookmaker/provider identities. The owner
  mentioned ESPN; confirm availability instead of assuming it is a supported
  source. Choose a consistent line and observation time (for example, opening,
  decision-time or closing), retaining the source and timestamp.
- Define how the model's predicted margin becomes a pick against that market
  line, then grade the actual result. Specify home/away signs, neutral sites,
  pushes, missing lines and canceled games.
- Decide the reported metrics and eligible-game denominator, distinguish
  market-line performance from current model-line coverage and margin error,
  and evaluate using predictions and lines available before kickoff. Determine
  whether `ats_correct` should be renamed or replaced.

The evaluation method, provider and naming decision remain open. Analysis and
implementation are deferred; current calculations are unchanged.
