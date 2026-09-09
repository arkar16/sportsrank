# Repository structure cleanup

## Current scope

Keep this pass conservative and byte preserving:

- retain the unsupported NFL experiments at `legacy/nfl/`;
- keep their direct script entry points and document them in `legacy/README.md`;
- keep repository navigation in `README.md` aligned with the supported CFB
  runtime, website, tests, docs, and legacy helpers.

This pass does not move CFB code, runtime or data, tests, website artifacts,
workflow files, or shared helpers, and does not refactor behavior. It does not
introduce a plugin framework.

## Acceptance

Before review, verify that the NFL relocation preserves file bytes, references
resolve from a repository-root checkout, and the documented invocation
`python legacy/nfl/nfl_spread.py` remains portable. Run the locked test suite
after the Gate1 repairs are integrated; its result is a later acceptance input,
not evidence supplied by this plan. Preserve public website URLs throughout.

Gate1 correctness and review remain owned by the recovery task; this structural
plan cannot waive that acceptance.

## Sequenced roadmap

1. After the first safe 2026 Publication, inspect and then consolidate callers
   of the legacy CFB modules and root helpers, preserving their observable
   contracts before retiring any duplicate path.
2. Introduce shallow `sportsrank/` shared operations and `sports/cfb/` sport
   rules only when verified callers and ownership justify each move; keep
   interfaces explicit and behavior stable.
3. Group site-generation templates and assets under `site/`, and organize tests
   around observable boundaries. Keep generated public URLs unchanged.
4. Consider removing generated HTML from Git only after backups, immutable
   Release archives, and URL compatibility are proven end to end under the
   accepted SQLite/Release storage decision carried by the recovery branch.
