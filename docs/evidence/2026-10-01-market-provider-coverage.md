# Historical CFBD betting-provider coverage

The recommended source for current CORS comparison is **Bovada**. It has a usable
spread for 3,944 of 3,945 returned FBS-versus-FBS games across 2021–2025
(99.975%), and all 57 games in the retained 2026 Week 3 sample. The one 2024 row
without Bovada, App State–Liberty, has no line from any provider. Bovada covers
every returned game that has any usable spread in 2021–2025.

No single provider has consistent coverage across the whole inspected history.
For older comparisons, `teamrankings` covers 7,328 of 7,424 returned FBS games
in 2013–2022 (98.707%), versus 97.037% for `consensus`. Both collapse in 2023
and are absent in 2024–2025. Bovada starts in 2019 but covers only 41.09% that
year and 94.01% in 2020; near-complete coverage starts in 2021. A reasonable
historical extension is `teamrankings` through 2020, Bovada from 2021, with the
actual source visible per game. Provider selection and fallback remain owner
choices; no comparison code was implemented.

## Coverage by season

Inspected on October 1, 2026 local time (October 2 UTC) under **SR-39** in
@thread:thr_meiabehepk. Fourteen metered `GET /lines?year=YEAR&seasonType=both`
calls inspect every year 2012–2025, including regular season and postseason.
The 2026 row reuses the earlier Week 3 response; it is not a full-season count.
Only explicitly FBS-versus-FBS rows enter this table. Values are distinct Game
IDs with a finite numeric `spread`, followed by percentage of returned FBS
rows. An absent provider is shown as a dash.

| Season | Returned FBS games | teamrankings | consensus | numberfire | Bovada | DraftKings |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2012 | 732 | — | — | — | — | — |
| 2013 | 737 | 725 (98.37%) | 730 (99.05%) | 709 (96.20%) | — | — |
| 2014 | 760 | 748 (98.42%) | 705 (92.76%) | 736 (96.84%) | — | — |
| 2015 | 765 | 752 (98.30%) | 728 (95.16%) | 755 (98.69%) | — | — |
| 2016 | 760 | 754 (99.21%) | 736 (96.84%) | 749 (98.55%) | — | — |
| 2017 | 776 | 766 (98.71%) | 762 (98.20%) | 765 (98.58%) | — | — |
| 2018 | 772 | 753 (97.54%) | 703 (91.06%) | 758 (98.19%) | — | — |
| 2019 | 774 | 774 (100.00%) | 773 (99.87%) | 771 (99.61%) | 318 (41.09%) | — |
| 2020 | 534 | 521 (97.57%) | 534 (100.00%) | 534 (100.00%) | 502 (94.01%) | — |
| 2021 | 770 | 769 (99.87%) | 768 (99.74%) | 145 (18.83%) | 770 (100.00%) | — |
| 2022 | 776 | 766 (98.71%) | 765 (98.58%) | — | 776 (100.00%) | — |
| 2023 | 792 | 52 (6.57%) | 29 (3.66%) | — | 792 (100.00%) | 728 (91.92%) |
| 2024 | 799 | — | — | — | 798 (99.87%) | 786 (98.37%) |
| 2025 | 808 | — | — | — | 808 (100.00%) | 761 (94.18%) |
| 2026 Week 3 | 57 | — | — | — | 57 (100%) | 57 (100%) |

2012 returned schedule rows but no usable betting lines. CFBD documents betting
coverage from 2013 onward, varying by game and provider. This survey does not
assert coverage before the inspected range. DraftKings begins in 2023 in these
responses and covers 91.92%, 98.37%, and 94.18% in 2023, 2024, and 2025;
Bovada is more complete in all three seasons.

## Interpretation and accepted rule

[ADR-0019](../adr/0019-evaluate-predictions-against-market-lines.md) records the
owner's October 1 clarification: use the returned API `spread` without quote
timestamps or closing-line evidence. Opening values are optional additional
data. A negative spread favors the home side; converting the home handicap
to predicted home margin uses its opposite sign. Preserve provider identity
and the fetched value. This market-line rule does not replace or recalculate
original issued CORS forecasts.

The denominator is the FBS schedule returned by `/lines`, not an independent
`/games` audit. No games request was made. All 2013–2025 responses contain
explicit classifications; the one unclassified 2012 row is excluded. The API's
`both` value applies no phase filter: the 2020 response also contains two spring
FCS rows, excluded from the regular/postseason FBS sample. Provider labels are
literal; Caesars and regional William Hill variants were not silently combined.

The API source uses truthiness checks that can serialize a zero spread as null.
Accordingly, counts measure usable returned numeric values; null does not prove
there was no underlying quote. Under the accepted rule it stays unavailable
rather than being converted to zero. A future comparison should show its
eligible-game count and use the same games for CORS and market margin errors.

## Evidence and validation

All fourteen responses were cached privately, with individual SHA256 hashes,
exclusive request claims and a shared seeded Request Meter. Thirteen attempts
have succeeded audit outcomes. The 2020 attempt is conservatively recorded as
failed because its retained successful response included the unexpected spring
phase; it was handled offline without another call. No provider failure was
retried. The original private ledger and snapshots were not modified.

Offline validation checks exact season, supported phase, unique Game IDs,
FBS classifications, finite numeric spreads, provider identity, and provider
counts as distinct games. No duplicate provider rows were observed. The raw
provider responses and credential-bearing environment remain private; this
report publishes aggregate coverage only. Response hashes and all provider
counts are attached to SR-39, which owns verification/status and residual limits.
No CORS tuning, runtime changes, merge or publication occurred.

## Primary sources

- [CFBD data availability](https://github.com/CFBD/cfb-api-v2/blob/main/docs-site/pages/data-availability.mdx): betting lines begin in 2013; game/provider/field coverage varies.
- [CFBD line service](https://github.com/CFBD/cfb-api-v2/blob/main/src/app/lines/service.ts): `both` and omitted phase apply no filter; spread orientation, zero-to-null mapping, provider matching, and FBS schedule retention.
- [CFBD line types](https://github.com/CFBD/cfb-api-v2/blob/main/src/app/lines/types.ts): returned provider, spread, opening values, totals and moneylines.
- Authorized live `/lines` responses: the coverage matrix above is measured data, not an inference from the documentation.
