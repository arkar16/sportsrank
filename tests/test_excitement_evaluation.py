"""Independent checks for whole-season AEV* evaluation preparation.

All games here are synthetic fixtures.  The report must retain that evidence
kind and must never be mistaken for empirical production acceptance.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from cfb.excitement import QualifiedFinal, QualifiedPregame, TimelinePoint
from cfb.excitement_evaluation import (
    EvaluationGame,
    evaluate_season_holdouts,
    summarize_scores,
)


def _game(game_id: str, season: int, home: int, away: int, *, overtime: bool = False) -> EvaluationGame:
    if overtime:
        quarter_scores = ((7, 0), (14, 7), (14, 14))
        timeline = (
            TimelinePoint(0, 0, 0, 0),
            TimelinePoint(7, 0, 1, 15),
            TimelinePoint(14, 7, 2, 30),
            TimelinePoint(14, 14, 3, 45),
            TimelinePoint(14, 14, 4, 60),
            TimelinePoint(home, away, 5, None, True),
        )
    else:
        quarter_scores = (
            (home // 4, away // 4),
            (home // 2, away // 2),
            (home - 1 if home else 0, away - 1 if away else 0),
        )
        timeline = (
            TimelinePoint(0, 0, 0, 0),
            TimelinePoint(quarter_scores[0][0], quarter_scores[0][1], 1, 15),
            TimelinePoint(quarter_scores[1][0], quarter_scores[1][1], 2, 30),
            TimelinePoint(quarter_scores[2][0], quarter_scores[2][1], 3, 45),
            TimelinePoint(home, away, 4, 60),
        )
    final = QualifiedFinal(
        game_id,
        season,
        home,
        away,
        "final-snapshot-1",
        overtime=overtime,
        quarter_scores=quarter_scores,
    )
    pregame = QualifiedPregame(1, 2, 0, f"forecast-{game_id}", "pregame-snapshot-1")
    return EvaluationGame(pregame, final, timeline)


class EvaluationPreparationTests(unittest.TestCase):
    def setUp(self):
        self.games = (
            _game("2023-close", 2023, 10, 3),
            _game("2023-blowout", 2023, 24, 10),
            _game("2024-close", 2024, 14, 7),
            _game("2024-overtime", 2024, 17, 14, overtime=True),
        )

    def test_whole_season_folds_are_disjoint_and_cover_both_input_tiers(self):
        report = evaluate_season_holdouts(self.games, data_kind="synthetic")
        self.assertEqual(report["data_kind"], "synthetic")
        self.assertFalse(report["production_accepted"])
        self.assertEqual(report["mode"], "whole_season_development")
        self.assertEqual(len(report["folds"]), 2)
        for fold in report["folds"]:
            self.assertTrue(set(fold["training_game_ids"]).isdisjoint(fold["evaluation_game_ids"]))
            self.assertEqual(fold["training_games"] + fold["evaluation_games"], len(self.games))
            self.assertEqual(
                {(variant["tier"], variant["overtime_input"]) for variant in fold["variants"]},
                {("final", "known"), ("final", "hidden"), ("quarter", "known"), ("quarter", "hidden")},
            )
            for variant in fold["variants"]:
                self.assertEqual(variant["scored"], 2)
                self.assertEqual(variant["unavailable"], 0)
                self.assertEqual(variant["metrics"]["count"], 2)
                self.assertIn("mean_absolute_error", variant["metrics"]["estimate"])
                self.assertIn("rank_correlation", variant["metrics"]["estimate"])
                self.assertIn("baseline", variant["metrics"])

    def test_evaluation_is_stable_under_input_row_order(self):
        first = evaluate_season_holdouts(self.games, data_kind="synthetic")
        second = evaluate_season_holdouts(tuple(reversed(self.games)), data_kind="synthetic")
        self.assertEqual(first, second)

    def test_explicit_reserved_holdout_requires_and_records_partition_identity(self):
        report = evaluate_season_holdouts(
            self.games,
            data_kind="qualified_retained",
            training_seasons=(2023,),
            evaluation_seasons=(2024,),
            reservation_id="reserved-evaluation-2026-09-29",
        )
        self.assertEqual(report["data_kind"], "qualified_retained")
        self.assertEqual(report["mode"], "reserved_season_evaluation")
        self.assertEqual(report["reservation_id"], "reserved-evaluation-2026-09-29")
        self.assertEqual(report["folds"][0]["training_seasons"], [2023])
        self.assertEqual(report["folds"][0]["evaluation_season"], 2024)

    def test_quarter_variant_records_unavailable_when_training_has_no_quarter_references(self):
        training_without_quarters = tuple(
            replace(game, final=replace(game.final, quarter_scores=None))
            for game in self.games
            if game.final.season == 2023
        )
        evaluation_with_quarters = tuple(
            game for game in self.games if game.final.season == 2024
        )
        report = evaluate_season_holdouts(
            training_without_quarters + evaluation_with_quarters,
            data_kind="synthetic",
            training_seasons=(2023,),
            evaluation_seasons=(2024,),
            reservation_id="synthetic-quarter-gap",
        )
        quarter_variants = [variant for variant in report["folds"][0]["variants"] if variant["tier"] == "quarter"]
        self.assertEqual(len(quarter_variants), 2)
        for variant in quarter_variants:
            self.assertEqual(variant["scored"], 0)
            self.assertEqual(variant["unavailable"], 2)
            self.assertTrue(all("no qualified quarter-tier references" in row["reason"] for row in variant["unavailable_games"]))

    def test_summary_metrics_are_independent_of_row_order_and_include_cutoff_ties(self):
        rows = [
            {"game_id": "a", "observed": 100.0, "estimate": 90.0, "baseline": 80.0},
            {"game_id": "b", "observed": 90.0, "estimate": 80.0, "baseline": 90.0},
            {"game_id": "c", "observed": 90.0, "estimate": 70.0, "baseline": 100.0},
            {"game_id": "d", "observed": 10.0, "estimate": 110.0, "baseline": 10.0},
        ]
        first = summarize_scores(rows, top_fraction=0.5)
        second = summarize_scores(tuple(reversed(rows)), top_fraction=0.5)
        self.assertEqual(first, second)
        self.assertEqual(first["estimate"]["highest_observed_count"], 3)
        self.assertEqual(first["estimate"]["highest_predicted_count"], 2)
        self.assertEqual(first["estimate"]["entered_highest"], ["d"])
        self.assertEqual(first["estimate"]["left_highest"], ["b", "c"])
        self.assertAlmostEqual(first["estimate"]["signed_bias"], 15.0)

    def test_invalid_partition_and_evidence_kind_fail_loudly(self):
        with self.assertRaises(ValueError):
            evaluate_season_holdouts(self.games, data_kind="invented")
        with self.assertRaises(ValueError):
            evaluate_season_holdouts(self.games[:2], data_kind="synthetic")
        with self.assertRaises(ValueError):
            evaluate_season_holdouts(
                self.games,
                data_kind="qualified_retained",
                training_seasons=(2023,),
                evaluation_seasons=(2024,),
            )
        with self.assertRaises(ValueError):
            evaluate_season_holdouts(
                self.games,
                data_kind="qualified_retained",
                training_seasons=(2023, 2024),
                evaluation_seasons=(2024,),
                reservation_id="reserved",
            )

    def test_top_fraction_must_be_bounded(self):
        with self.assertRaises(ValueError):
            summarize_scores([], top_fraction=0)
        with self.assertRaises(ValueError):
            evaluate_season_holdouts(self.games, data_kind="synthetic", top_fraction=1.1)


if __name__ == "__main__":
    unittest.main()
