from dataclasses import replace
from math import sqrt
import unittest

from cbb.experiment import BASELINE, CANDIDATE_GRID, ForecastRow, evaluate_experiment


LOWER_HOME_ADVANTAGE = next(c for c in CANDIDATE_GRID if c.home_advantage_delta == -1)
HIGHER_HOME_ADVANTAGE = next(c for c in CANDIDATE_GRID if c.home_advantage_delta == 1)


def paired_rows(games, predictions):
    """Build independent replay rows from (season, game_id, actual, rest) facts."""
    return {
        candidate: [
            ForecastRow(season, game_id, predictions(candidate, season, game_id), actual, rest)
            for season, game_id, actual, rest in games
        ]
        for candidate in CANDIDATE_GRID
    }


class ExperimentTests(unittest.TestCase):
    def test_candidate_grid_changes_one_predeclared_factor_at_a_time(self):
        self.assertEqual(
            [(c.home_advantage_delta, c.preseason_carryover_scale, c.margin_weight_scale)
             for c in CANDIDATE_GRID],
            [(0, 1, 1), (-1, 1, 1), (1, 1, 1), (0, .5, 1), (0, 1.5, 1),
             (0, 1, .5), (0, 1, 1.5)],
        )

    def test_targets_cannot_select_either_of_their_own_parameters(self):
        games = [(season, "a", 10, 50) for season in range(2021, 2025)]
        games.append((2022, "b", 10, 50))

        def prediction(candidate, season, game_id):
            if candidate == LOWER_HOME_ADVANTAGE:
                return {2020: 0, 2021: 8.5, 2022: 14, 2023: 20, 2024: 20}[season]
            if candidate == HIGHER_HOME_ADVANTAGE:
                return {2020: 1000, 2021: 5, 2022: 11.5, 2023: 10, 2024: 10}[season]
            return 0 if candidate == BASELINE else 30

        original = evaluate_experiment(paired_rows(games, prediction))
        changed = evaluate_experiment(paired_rows(
            [(season, game, 15.5 if season == 2022 else actual, rest)
             for season, game, actual, rest in reversed(games)] + [(2020, "a", 1000, 50)],
            prediction,
        ))

        self.assertEqual([fold.season for fold in original.seasons], [2022, 2023, 2024])
        first, second, third = original.seasons
        self.assertEqual(first.models["tuned"].candidate, LOWER_HOME_ADVANTAGE)
        self.assertEqual(first.models["tuned_rest"].rest_coefficient, .5)
        self.assertEqual(first.models, changed.seasons[0].models)
        self.assertEqual(first.scores["tuned"].rmse, 4)
        self.assertEqual(first.scores["tuned_rest"].rmse, 5.5)
        self.assertEqual(changed.seasons[0].scores["tuned_rest"].rmse, 0)
        self.assertEqual(second.models["tuned"].candidate, HIGHER_HOME_ADVANTAGE)
        self.assertEqual(second.models["tuned_rest"].rest_coefficient, .25)
        self.assertEqual(changed.seasons[1].models["tuned"].candidate, LOWER_HOME_ADVANTAGE)
        self.assertEqual(third.models["tuned"].candidate, HIGHER_HOME_ADVANTAGE)

    def test_rest_is_clipped_and_winning_strategy_refits_through_2024(self):
        games = [
            (2021, 1, 10, 50), (2022, 1, 10, 50), (2023, 1, 7, -50),
            (2024, 1, 9, 50), (2024, 2, 8, -50), (2024, 3, 9, 50),
            (2024, 4, 8.5, None),
        ]
        forecasts = paired_rows(games, lambda candidate, *_: 8.5 if candidate == LOWER_HOME_ADVANTAGE else 0)
        result = evaluate_experiment(forecasts)

        self.assertEqual([fold.paired_games for fold in result.seasons], [1, 1, 4])
        self.assertEqual(result.paired_games, 6)
        self.assertEqual(result.seasons[-1].models["tuned_rest"].rest_coefficient, .5)
        self.assertEqual(result.pooled["tuned"].rmse, sqrt(5.25 / 6))
        self.assertEqual(result.pooled["tuned_rest"].rmse, sqrt(3 / 6))
        self.assertEqual(result.finalist.strategy, "tuned_rest")
        self.assertEqual(result.finalist.through_season, 2024)
        self.assertEqual(result.finalist.model.candidate, LOWER_HOME_ADVANTAGE)
        self.assertEqual(result.finalist.model.rest_coefficient, .25)

        without_rest = evaluate_experiment(forecasts, include_rest=False)
        self.assertEqual(set(without_rest.pooled), {"baseline", "tuned"})
        self.assertEqual(without_rest.finalist.strategy, "tuned")
        self.assertEqual(without_rest.finalist.model.rest_coefficient, 0)

    def test_pooled_scores_weight_each_game_and_score_pick_ems_explicitly(self):
        games = [
            (2021, 1, 20, None), (2022, 1, 4, None), (2022, 2, -1, None), (2022, 3, 0, None),
            (2023, 1, 2, None), (2024, 1, -1.125, None),
        ]
        predictions = {(2021, 1): 10, (2022, 1): -0.0, (2022, 2): 1, (2022, 3): 0,
                       (2023, 1): 2, (2024, 1): -.125}
        result = evaluate_experiment(paired_rows(games, lambda _, season, game: predictions[season, game]))

        first = result.seasons[0].scores["baseline"]
        self.assertEqual((first.games, first.pick_ems, first.actual_ties), (3, 2, 1))
        self.assertEqual(first.accuracy, 0)
        self.assertEqual(first.rmse, sqrt(20 / 3))
        pooled = result.pooled["baseline"]
        self.assertEqual(
            (result.paired_games, pooled.games, pooled.pick_ems, pooled.actual_ties), (5, 5, 2, 1)
        )
        self.assertEqual(pooled.accuracy, 2 / 3)
        self.assertEqual(pooled.rmse, sqrt(21 / 5))
        self.assertEqual(result.seasons[0].models["tuned"].candidate, BASELINE)
        self.assertEqual(result.seasons[0].models["tuned_rest"].rest_coefficient, 0)
        self.assertEqual(result.finalist.strategy, "baseline")

    def test_final_candidate_can_change_only_after_scoring_2024(self):
        games = [(season, 1, 10, None) for season in range(2021, 2025)]

        def prediction(candidate, season, _):
            if candidate == LOWER_HOME_ADVANTAGE:
                return 6 if season == 2024 else 10
            if candidate == HIGHER_HOME_ADVANTAGE:
                return {2021: 8, 2022: 9, 2023: 9, 2024: 10}[season]
            return 0

        result = evaluate_experiment(paired_rows(games, prediction), include_rest=False)
        self.assertEqual(result.seasons[-1].models["tuned"].candidate, LOWER_HOME_ADVANTAGE)
        self.assertEqual(result.seasons[-1].scores["tuned"].rmse, 4)
        self.assertEqual(result.finalist.strategy, "tuned")
        self.assertEqual(result.finalist.model.candidate, HIGHER_HOME_ADVANTAGE)

    def test_invalid_coverage_or_facts_fail_closed(self):
        games = [(season, 1, 3, 1) for season in range(2021, 2025)]
        forecasts = paired_rows(games, lambda *_: 2)
        candidate = LOWER_HOME_ADVANTAGE
        first, *remaining = forecasts[candidate]

        invalid_rows = (
            (remaining, "1 missing, 0 extra"),
            ([first, *remaining, replace(first, game_id=2)], "0 missing, 1 extra"),
            ([first, first, *remaining], "duplicate forecast"),
            ([replace(first, actual_home_margin=4), *remaining], "inconsistent game facts"),
            ([replace(first, rest_days_diff=2), *remaining], "inconsistent game facts"),
            ([replace(first, predicted_home_margin=float("nan")), *remaining], "non-finite margin"),
            ([replace(first, rest_days_diff=float("inf")), *remaining], "non-finite rest differential"),
            ([first, *remaining, replace(first, season=2025)], "outside the 2020–24 development window"),
        )
        for rows, message in invalid_rows:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                evaluate_experiment({**forecasts, candidate: rows})

        with self.assertRaisesRegex(ValueError, "exactly CANDIDATE_GRID"):
            evaluate_experiment({BASELINE: forecasts[BASELINE]})
        with self.assertRaisesRegex(ValueError, "missing baseline games for 2021"):
            evaluate_experiment({**forecasts, BASELINE: remaining})


if __name__ == "__main__":
    unittest.main()
