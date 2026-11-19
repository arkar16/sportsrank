"""Behavior and source checks for the offline football development replay."""

from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from cfb.historical_rebuild import RESULT_COLUMNS
from cfb.ranking_engine import PreviousFinal
from cfb.release import _row_table
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam
from scripts.experiment_cfb import (
    BASELINE, GRID, load_season, prepare_weeks, replay_season, rolling_scores,
    run_experiment, score, verify_cutoff,
)


class FootballDevelopmentTests(unittest.TestCase):
    def test_archived_replay_matches_all_checkpoints_and_forecast_rows(self):
        archive = Path(__file__).resolve().parents[1] / "website"
        report = run_experiment(archive)
        checks = report["checks"]
        self.assertEqual([row["year"] for row in checks], [2022, 2023, 2024, 2025])
        self.assertEqual([row["ranking_checkpoints_matched"] for row in checks], [23, 23, 25, 25])
        self.assertEqual([row["reconstructed_margins_matched"] for row in checks], [776, 792, 798, 808])
        self.assertEqual([row["dated_results"] for row in checks], [896, 910, 0, 0])
        self.assertTrue(all(row["neutral_margins_invariant_to_hfa"] > 0 for row in checks))
        self.assertEqual([row["fit_years"] for row in report["folds"]], [[2023], [2023, 2024]])
        self.assertEqual(report["pooled"]["baseline"]["games"], 1606)
        self.assertEqual(report["pooled"]["rolling_tuned"]["games"], 1606)

    def test_replay_ignores_same_week_and_later_scores_for_every_grid_candidate(self):
        games = (
            SourceGame(0, "A", "fbs", 35, "B", "fbs", 7, True, "a", completed=True),
            SourceGame(1, "A", "fbs", 14, "B", "fbs", 7, False, "b", completed=True),
            SourceGame(2, "B", "fbs", 35, "A", "fbs", 14, False, "c", completed=True),
        )
        teams = (SourceTeam("A", "Independent"), SourceTeam("B", "Independent"))
        snapshot = SeasonSnapshot("cfb", "FBS", 2022, teams, games, {}, "")
        prior = PreviousFinal({"A": 16.0, "B": 9.0}, {"A": 1.0, "B": -2.0}, 2021, "FBS")
        weeks = prepare_weeks(snapshot)
        replays = {}
        for candidate in GRID:
            with self.subTest(candidate=candidate):
                replay = replay_season(snapshot, weeks, prior, candidate)
                replays[candidate] = replay
                self.assertEqual(verify_cutoff(snapshot, prior, candidate, replay), [0, 1, 2])
                reversed_w0 = replace(snapshot, games=(replace(games[0], home_points=0), *games[1:]))
                alternative = replay_season(reversed_w0, prepare_weeks(reversed_w0), prior, candidate)
                self.assertEqual(alternative.margins["a"], replay.margins["a"])
                self.assertNotEqual(alternative.margins["b"], replay.margins["b"])

        baseline = replays[BASELINE]
        self.assertEqual(baseline.margins["a"], Decimal("1.75"))
        self.assertEqual(replays[GRID[1]].margins["a"], baseline.margins["a"])
        self.assertEqual(replays[GRID[1]].margins["b"], baseline.margins["b"] - 1)
        self.assertEqual(replays[GRID[3]].margins["a"], baseline.margins["a"])
        self.assertNotEqual(replays[GRID[3]].margins["b"], baseline.margins["b"])
        self.assertEqual(replays[GRID[5]].margins["a"], Decimal("0.87"))

    def test_unscored_week_zero_alias_preserves_preseason_and_entrant_baseline(self):
        teams = (SourceTeam("A", "Independent"), SourceTeam("B", "Independent"))
        game = SourceGame(1, "A", "fbs", 10, "B", "fbs", 9, False, "game", completed=True)
        snapshot = SeasonSnapshot("cfb", "FBS", 2022, teams, (game,), {}, "")
        prior = PreviousFinal({"A": 16.0}, {"A": 0.0}, 2021, "FBS")
        replay = replay_season(snapshot, prepare_weeks(snapshot), prior, GRID[5])
        self.assertEqual(replay.rankings[0], replay.preseason)
        self.assertEqual({row["school"]: row["cors"] for row in replay.preseason}, {"A": 8.0, "B": -10.0})
        self.assertEqual(replay.margins["game"], Decimal("20.00"))

    def test_rejects_weekly_results_that_disagree_with_season_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            season_path = root / "data/results/2022_FBS_results.html"
            weekly_path = root / "data/results/weekly_results/2022_W0_FBS_results.html"
            final_path = root / "rankings/2022_FINAL_FBS_cors.html"
            game = dict(zip(RESULT_COLUMNS, (0, "A", "fbs", 20, "B", "fbs", 10, False, "a", "")))
            for path, rows in (
                (season_path, [game]), (weekly_path, [{**game, "home_score": 21}]),
                (final_path, [{"school": school, "conference": "Independent"} for school in ("A", "B")]),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_row_table(rows), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"2022 Week 0: weekly and season result tables disagree"):
                load_season(root, 2022)

    def test_folds_use_prior_years_and_never_refit_on_the_evaluation_year(self):
        first, second = GRID[1:3]
        rows = {candidate: {2023: [(22, 10)], 2024: [(20, 10)], 2025: [(10, 10)]}
                for candidate in GRID}
        rows[first][2023] = [(10, 10)]
        rows[first][2024] = [(26, 10)]
        rows[second][2024] = [(10, 10)]
        initial = rolling_scores(rows)
        self.assertEqual([fold["selected"] for fold in initial["folds"]], [asdict(first), asdict(second)])
        rows[second][2025] = [(1000, 10)]
        changed = rolling_scores(rows)
        self.assertEqual(changed["folds"][0], initial["folds"][0])
        self.assertEqual(changed["folds"][1]["selected"], asdict(second))
        self.assertEqual(changed["folds"][1]["scores"]["rolling_tuned"]["rmse"], 990)

    def test_pick_ems_and_actual_ties_only_remove_straight_up_decisions(self):
        stats = score([(0, -3), (4, 0), (-2, -7), (2, -3)])
        self.assertEqual(stats["games"], 4)
        self.assertEqual(stats["decided_picks"], 2)
        self.assertEqual(stats["straight_up_accuracy"], 0.5)
        self.assertEqual(stats["pick_ems"], 1)
        self.assertEqual(stats["actual_ties"], 1)
        self.assertAlmostEqual(stats["rmse"], (75 / 4) ** 0.5)


if __name__ == "__main__":
    unittest.main()
