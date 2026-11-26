# Football game excitement

Run the normal offline `uv run --locked python -m cfb.record_book` after
updating CORS game inputs. The season watch guide orders upcoming games by BEV
and completed games by AEV. Weekly forecast and score sheets, forecast results,
and team-season logs use the same values. The archive covers every FBS season with retained checkpoint-matched CORS v1 forecasts. [ADR-0021](../adr/0021-define-versioned-game-excitement-scores.md)
and the [scoring contract](../plans/2026-game-excitement-scoring-v1.md) define the scores.

## Pregame inputs

The build joins each retained CORS v1.0.0 spread to both teams’ ranks at the
preceding weekly checkpoint (PRESEASON for Week 0). The ratings on the forecast
must match that ranking. Each season retains the score version, ranks, margin,
checkpoint and original source-page digests in `excitement/bev-v1.json`.
The build adds new games and preserves existing entries across timestamp or
presentation changes. A changed scoring input for a captured matchup requires
a versioned correction preserving the prior entry. This rollout uses the
accepted missing-market policy. These retrospective scores refer to the model
at the pregame checkpoint. Issued forecast editions keep their own history.

## After-game evidence

The build reads compact normalized scoring changes, quarter totals and source
identities from [the retained evidence](../../cfb/excitement_data/). New data
preparation is separate. The source manifest pins a schedule CSV by SHA-256
and archived ESPN game summaries by Git revision. For each scheduled game,
preparation checks the original Git blob ID against the tree at that revision,
the home and away teams against the SportsRank catalog, and the final against
the CORS result. Raw provider responses stay outside the repository.

For measured AEV, the summary must establish a normal completed four-quarter
game. The importer checks the game format and observed quarter clocks,
reconciles each scoring event with the scoring team and scoring type, checks
clock order, then verifies the cumulative scores against every period total
and the final. The official scoring summary can combine a touchdown and its
conversion. They share the reported scoring time. Overtime is untimed.
A missing clock or unexplained score change precludes a measured score.
A missing or contradictory game identity or final precludes both evidence tiers.

With the matching public archive and season schedule CSVs retained locally,
reproduce the normalized evidence without a network request. The input directory
contains `schedule-YEAR.csv`. The checkout must include the listed summaries
under `cfb/json/raw/`. Individually retained summaries from the same revision can be supplied with `--summaries DIRECTORY`. Public
[ESPN summary archive](https://github.com/sportsdataverse/cfbfastR-cfb-raw) ·
[ESPN season schedules](https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_cfb_schedules).

```sh
uv run --locked python -m scripts.prepare_game_drama \
  --inputs /path/to/season-csvs --espn-repo /path/to/espn-archive \
  --output .sportsrank/reviewed-game-drama 2024 2025 2026
```

For a new snapshot, pin the new archive revision and schedule digest, review
the reproduced changes and retain the prior source identities. Later model
updates and final scores never enter the BEV calculation.

## Comparable-game estimates

Preparation can fit an inspectable reference and evaluate both reduced-input
tiers in whole-season development folds. Keep evaluation reports and candidate
references in the private working directory.

```sh
uv run --locked python -m scripts.evaluate_game_excitement \
  --training 2024 2026 --report .sportsrank/excitement-development.json \
  --reference .sportsrank/excitement-reference.json
```

The 2025 confirmation uses the reference frozen from 2024 and 2026. Reproduce
it offline with the original checksum. The evaluator verifies every reference
game against the declared training seasons and hides each test game's timeline.

```sh
uv run --locked python -m scripts.evaluate_game_excitement \
  --training 2024 2026 --holdout 2025 --reservation-id owner-2026-11-26-quarter-known \
  --frozen-reference cfb/excitement_data/reference-2024-2026.json \
  --checksum 40dda2d3f20be6da72f6fd5e965374434ede94dd7e277e015e8eade0658fde96 \
  --report .sportsrank/excitement-confirmation-2025.json
```

The [football delivery issue](https://github.com/arkar16/sportsrank/issues/179)
records the thresholds and results. The site reads the approved checksum and
quarter-score tier from `reference-policy.json`. An asterisk identifies each
estimate in the watch guide, score sheets and team game logs.
