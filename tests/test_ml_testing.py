"""Independent archive-to-experiment contracts for the offline ML starter."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.ml_testing import main


class MLTestingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "website"

    def season(self, year, margins, actuals):
        root = self.site / "cfb/years" / str(year)
        spreads, results = [], []
        for index, (margin, actual) in enumerate(zip(margins, actuals)):
            home, away = f"Home{index}", f"Away{index}"
            base = dict(week=0, home_team=home, away_team=away, neutral_site=True)
            spreads.append({**base, "spread": f"{home} {-margin}"})
            results.append({**base, "home_division": "fbs", "away_division": "fbs",
                            "home_score": 50 + actual, "away_score": 50})
        for relative, rows in ((f"spread/{year}_W0_FBS_spread.html", spreads),
                               (f"data/results/weekly_results/{year}_W0_FBS_results.html", results)):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(pd.DataFrame(rows).to_html(index=False))

    def run_experiment(self, name, *extra):
        output = self.root / name
        with redirect_stdout(io.StringIO()), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(main(["--website", str(self.site), "--output", str(output),
                                   "--min-training-games", "2", *extra]), 0)
        return output, json.loads((output / "report.json").read_text())

    def assert_failure(self, message, output, *extra):
        errors = io.StringIO()
        with self.assertRaises(SystemExit) as caught, redirect_stderr(errors), redirect_stdout(io.StringIO()):
            main(["--website", str(self.site), "--output", str(output),
                  "--min-training-games", "2", *extra])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(message, errors.getvalue())

    def test_paired_held_out_errors_and_deterministic_output_preserve_sources(self):
        # Earlier seasons teach y=2x+1. Held-out season has residuals +1,-1.
        self.season(2020, [-2, 2], [-3, 5])
        self.season(2021, [-1, 1], [-1, 3])
        self.season(2022, [-3, 3], [-4, 6])
        original = {p: p.read_bytes() for p in self.site.rglob("*.html")}
        first, report = self.run_experiment("first")
        self.assertEqual(report["summary"]["game_count"], 2)
        self.assertAlmostEqual(report["summary"]["calibration"]["mae"], 1)
        self.assertAlmostEqual(report["summary"]["cors"]["mae"], 2)
        self.assertAlmostEqual(report["summary"]["zero_margin"]["mae"], 5)
        self.assertEqual(report["folds"][0]["training_seasons"], [2020, 2021])
        self.assertAlmostEqual(report["games"][0]["calibration_home_margin"], -5)
        self.assertAlmostEqual(report["games"][1]["calibration_home_margin"], 7)
        second, _ = self.run_experiment("second")
        for filename in ("report.json", "games.csv"):
            self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertTrue(report["sources"])
        self.assertTrue(all(len(row["sha256"]) == 64 for row in report["sources"]))

    def test_current_and_future_scores_cannot_change_an_earlier_fold_forecast(self):
        self.season(2020, [-2, 2], [-3, 5])
        self.season(2021, [-1, 1], [-1, 3])
        self.season(2022, [-3, 3], [-4, 6])
        self.season(2023, [-3, 3], [-4, 6])
        _, before = self.run_experiment("before")
        self.season(2022, [-3, 3], [40, 40])
        self.season(2023, [-3, 3], [49, 49])
        _, after = self.run_experiment("after")
        for report in (before, after):
            predictions = [r["calibration_home_margin"] for r in report["games"] if r["season"] == 2022]
            self.assertAlmostEqual(predictions[0], -5)
            self.assertAlmostEqual(predictions[1], 7)
        # Changing 2022 labels DOES affect the subsequent fit, confirming the
        # perturbed scores reached the loader rather than an inert test fixture.
        self.assertNotEqual(before["folds"][1]["intercept"], after["folds"][1]["intercept"])

    def test_metrics_weight_games_and_test_filter_retains_earlier_training(self):
        self.season(2020, [-2, 2], [-3, 5])
        self.season(2021, [-1, 1], [-1, 3])
        self.season(2022, [-3], [-5])
        self.season(2023, [-1, 1, 0], [-1, 3, 1])
        _, report = self.run_experiment("weighted")
        self.assertAlmostEqual(report["summary"]["cors"]["mae"], 1.25)
        self.assertAlmostEqual(report["summary"]["cors"]["rmse"], 1.5)
        _, filtered = self.run_experiment("filtered", "--from-season", "2023", "--through-season", "2023")
        self.assertEqual(filtered["folds"][0]["training_seasons"], [2020, 2021, 2022])
        self.assertEqual(filtered["summary"]["game_count"], 3)

    def test_reconstructed_pending_and_non_fbs_rows_are_excluded(self):
        self.season(2020, [-2, 2], [-3, 5])
        self.season(2021, [-1, 1], [-1, 3])
        self.season(2022, [-3, 3, 0, 1, 2, 3], [-4, 6, 0, 0, 0, 0])
        root = self.site / "cfb/years/2022"
        spread = root / "spread/2022_W0_FBS_spread.html"
        spreads = pd.read_html(io.StringIO(spread.read_text()))[0]
        spreads.iloc[:2].to_html(spread, index=False)
        ratings = root / "rankings/2022_PRESEASON_FBS_cors.html"
        ratings.parent.mkdir(parents=True)
        pd.DataFrame([{"school": "Home2", "cors": 10}, {"school": "Away2", "cors": 10}]).to_html(ratings, index=False)
        results = root / "data/results/weekly_results/2022_W0_FBS_results.html"
        rows = pd.read_html(io.StringIO(results.read_text()))[0]
        rows["home_score"] = rows["home_score"].astype(object)
        rows.loc[3, "home_score"] = ""
        rows.loc[4, "away_division"] = "fcs"
        rows.to_html(results, index=False)
        _, report = self.run_experiment("excluded")
        self.assertEqual(report["summary"]["game_count"], 2)
        self.assertEqual(report["coverage"][-1], {
            "season": 2022, "eligible_saved_games": 2, "reconstructed_predictions": 1,
            "missing_predictions": 1, "missing_scores": 1, "non_fbs_games": 1})

    def test_output_overlap_and_reuse_fail_without_modifying_any_sources(self):
        self.season(2020, [-2, 2], [-3, 5])
        self.season(2021, [-1, 1], [-1, 3])
        self.season(2022, [-3, 3], [-4, 6])
        original = {p: p.read_bytes() for p in self.site.rglob("*.html")}
        for output in (self.site, self.site / "experiment", self.root):
            with self.subTest(output=output):
                self.assert_failure("outside and separate", output)
        output, _ = self.run_experiment("existing")
        original_report = (output / "report.json").read_bytes()
        self.assert_failure("File exists", output)
        self.assertEqual((output / "report.json").read_bytes(), original_report)
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_insufficient_history_degenerate_training_and_invalid_data_fail_visibly(self):
        for year in (2020, 2021, 2022):
            self.season(year, [0, 0], [1, 2])
        for args, message in (([], "no held-out games"),
                              (["--min-training-games", "100"], "no held-out games"),
                              (["--from-season", "2023", "--through-season", "2020"], "from-season")):
            with self.subTest(args=args):
                self.assert_failure(message, self.root / "invalid", *args)
        self.season(2020, [float("nan"), 2], [1, 2])
        self.assert_failure("non-finite", self.root / "nonfinite")
        self.assertFalse((self.root / "nonfinite").exists())


if __name__ == "__main__":
    unittest.main()
