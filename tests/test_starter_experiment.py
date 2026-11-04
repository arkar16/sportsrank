"""Synthetic end-to-end proof for private starter-pack experiments."""
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from cfb.starter_pack import load_features
from scripts.ml_testing import main


class StarterExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pack, self.site = self.root / "pack", self.root / "site"
        self.games, self.stats = [], {}
        for year in (2020, 2021, 2022):
            self.add_season(year)
        self.save_pack()

    @staticmethod
    def write_csv(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def add_season(self, year):
        first = datetime(year, 9, 1, 12, tzinfo=timezone.utc)
        first += timedelta(days=(5 - first.weekday()) % 7)
        self.stats[year] = []
        schedule = ((('A', 'B'), ('C', 'D')), (('A', 'C'), ('B', 'D')), (('A', 'D'), ('B', 'C')))
        for week, pairs in enumerate(schedule, 1):
            results, spreads = [], []
            for index, (home, away) in enumerate(pairs):
                identifier = str(year * 100 + week * 10 + index)
                actual, cors = (-3, -2) if index == 0 else (5, 2)
                self.games.append(dict(id=identifier, season=year,
                    start_date=(first + timedelta(days=7 * (week - 1))).isoformat(),
                    start_time_tbd="false", status="completed", neutral_site="true",
                    home_team=home, away_team=away, home_classification="fbs", away_classification="fbs",
                    home_points=50 + actual, away_points=50))
                for team, opponent in ((home, away), (away, home)):
                    self.stats[year].append(dict(gameId=identifier, season=year, team=team, opponent=opponent,
                        offense_ppa=0.5, defense_ppa=0.5, offense_successRate=0.5, defense_successRate=0.5))
                base = dict(game_id=identifier, week=week, home_team=home, away_team=away, neutral_site=True)
                results.append({**base, "home_division": "fbs", "away_division": "fbs",
                                "home_score": 50 + actual, "away_score": 50})
                spreads.append({**base, "spread": f"{home} {-cors}"})
            root = self.site / "cfb/years" / str(year)
            for relative, rows in ((f"data/results/weekly_results/{year}_W{week}_FBS_results.html", results),
                                   (f"spread/{year}_W{week}_FBS_spread.html", spreads)):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(rows).to_html(path, index=False)

    def save_pack(self):
        self.write_csv(self.pack / "data/games.csv", self.games)
        for year, rows in self.stats.items():
            self.write_csv(self.pack / f"data/advanced_game_stats/{year}.csv", rows)

    def run_experiment(self, name, *extra):
        output = self.root / ".sportsrank" / name
        with redirect_stdout(io.StringIO()), patch("socket.socket", side_effect=AssertionError("offline only")):
            self.assertEqual(main(["--website", str(self.site), "--starter-pack", str(self.pack),
                                   "--output", str(output), "--min-training-games", "2", *extra]), 0)
        return output, json.loads((output / "report.json").read_text())

    def assert_failure(self, text, output=None):
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            main(["--website", str(self.site), "--starter-pack", str(self.pack),
                  "--output", str(output or self.root / "failed"), "--min-training-games", "2"])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn(text, errors.getvalue())

    def test_known_paired_predictions_scaling_and_artifacts_without_source_writes(self):
        original = {p: p.read_bytes() for directory in (self.pack, self.site) for p in directory.rglob("*") if p.is_file()}
        output, report = self.run_experiment("first")
        # Four training games: CORS +/-2, targets -3/+5. Stats differences
        # and neutral home-field features are all zero. OLS is y=1+2x;
        # alpha=1 ridge shrinks the standardized slope from 4 to 16/5.
        summary = report["summary"]
        self.assertEqual(summary["game_count"], 2)
        self.assertAlmostEqual(summary["cors"]["mae"], 2)
        self.assertAlmostEqual(summary["calibration"]["mae"], 0)
        self.assertAlmostEqual(summary["advanced"]["mae"], 4)
        self.assertAlmostEqual(summary["combined"]["mae"], 0.8)
        self.assertAlmostEqual(report["games"][0]["combined_home_margin"], -2.2)
        self.assertEqual(report["games"][0]["home_history_games"], 2)
        self.assertEqual(report["games"][0]["home_field"], 0)
        fold = report["folds"][0]
        self.assertEqual(fold["training_seasons"], [2020, 2021])
        self.assertEqual(fold["training_game_count"], 4)
        self.assertEqual(fold["models"]["combined"]["means"], [0] * 6)
        self.assertEqual(fold["models"]["combined"]["scales"], [2, 1, 1, 1, 1, 1])
        self.assertEqual(report["coverage"][0]["insufficient_prior_games"], 4)
        again, _ = self.run_experiment("again")
        for name in ("report.json", "games.csv"):
            self.assertEqual((output / name).read_bytes(), (again / name).read_bytes())
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual({s["source"] for s in report["sources"]}, {"website", "starter_pack"})
        self.assert_failure("outside and separate", self.pack / "output")
        self.assert_failure("File exists", output)

    def test_current_game_and_future_stats_and_scores_do_not_change_predictions(self):
        _, before = self.run_experiment("before")
        for row in self.stats[2022]:
            if row["gameId"] == "202230" and row["team"] == "A":
                row["offense_ppa"] = 100
        # The same test-season label change reaches both independent sources,
        # so it remains paired and changes errors, but cannot change its fit.
        for row in self.games:
            if row["id"] == "202230":
                row["home_points"] = 90
        result = self.site / "cfb/years/2022/data/results/weekly_results/2022_W3_FBS_results.html"
        table = pd.read_html(io.StringIO(result.read_text()))[0]
        table.loc[0, "home_score"] = 90
        table.to_html(result, index=False)
        self.add_season(2023)
        self.save_pack()
        _, after = self.run_experiment("after")
        self.assertEqual(before["folds"][0]["models"], after["folds"][0]["models"])
        for old, new in zip(before["games"], after["games"][:2]):
            self.assertEqual(old["offense_ppa_difference"], new["offense_ppa_difference"])
            for model in ("calibration", "advanced", "combined"):
                self.assertEqual(old[f"{model}_home_margin"], new[f"{model}_home_margin"])
        self.assertNotEqual(before["summary"]["cors"]["mae"], after["folds"][0]["summary"]["cors"]["mae"])

    def test_prior_metrics_are_learned_and_held_out_feature_values_do_not_fit_scaling(self):
        # Prior offensive PPA differences become -2 and +2. Four training
        # targets are again 1+2x: stats-only ridge predicts -2.2/+4.2.
        # Combined ridge has two identical standardized predictors, giving
        # total standardized slope 32/9 and absolute error 4/9.
        for rows in self.stats.values():
            for row in rows:
                row["offense_ppa"] = {"A": -1.5, "B": 2.5, "C": 0.5, "D": 0.5}[row["team"]]
        self.save_pack()
        _, before = self.run_experiment("learned")
        self.assertEqual([r["offense_ppa_difference"] for r in before["games"]], [-2, 2])
        self.assertAlmostEqual(before["games"][0]["advanced_home_margin"], -2.2)
        self.assertAlmostEqual(before["summary"]["advanced"]["mae"], 0.8)
        self.assertAlmostEqual(before["summary"]["combined"]["mae"], 4 / 9)
        for row in self.stats[2022]:
            if row["team"] == "A":
                row["offense_ppa"] = 100
        self.save_pack()
        _, after = self.run_experiment("test-feature-shift")
        self.assertEqual(after["games"][0]["offense_ppa_difference"], 99.5)
        self.assertNotEqual(before["games"][0]["advanced_home_margin"], after["games"][0]["advanced_home_margin"])
        self.assertEqual(before["folds"][0]["models"], after["folds"][0]["models"])

    def test_weekly_cutoff_not_source_week_controls_history_and_home_field(self):
        # Move one previous game to the target week's Monday: even a completed
        # game earlier in that week must not enter the week's frozen features.
        for row in self.games:
            if row["id"] == "202220":
                row["start_date"] = "2022-09-12T00:00:00+00:00"
            if row["id"] == "202231":
                row["neutral_site"] = "false"
        self.save_pack()
        data = load_features(self.pack)
        self.assertFalse(any(row["game_id"] in {"202230", "202231"} for row in data["games"]))
        # Restore it to before the cutoff, including the 24-hour allowance.
        for row in self.games:
            if row["id"] == "202220":
                row["start_date"] = "2022-09-10T00:00:00+00:00"
                row["start_time_tbd"] = ""  # Legacy blank flag is audited.
        self.save_pack()
        data = load_features(self.pack)
        target = next(row for row in data["games"] if row["game_id"] == "202231")
        self.assertEqual(target["home_field"], 1)
        self.assertEqual(target["feature_cutoff"], "2022-09-12T00:00:00+00:00")
        self.assertEqual(data["coverage"][-1]["unspecified_start_time_tbd"], 1)

    def test_unambiguous_fallback_works_but_conflicting_ids_and_scores_are_counted(self):
        result = self.site / "cfb/years/2022/data/results/weekly_results/2022_W3_FBS_results.html"
        spread = self.site / "cfb/years/2022/spread/2022_W3_FBS_spread.html"
        for path in (result, spread):
            table = pd.read_html(io.StringIO(path.read_text()))[0]
            table["game_id"] = table["game_id"].astype(object)
            table.loc[0, "game_id"] = "archive:synthetic"
            table.to_html(path, index=False)
        _, report = self.run_experiment("fallback")
        self.assertEqual(report["cors_join_coverage"][-1]["unique_teams_and_orientation"], 1)
        # An earlier matchup absent from the website and feature sample still
        # makes the name join ambiguous; it must not disappear during filtering.
        earlier = dict(next(row for row in self.games if row["id"] == "202230"))
        earlier.update(id="202200", start_date="2022-08-01T12:00:00+00:00")
        self.games.append(earlier)
        self.save_pack()
        _, report = self.run_experiment("ambiguous")
        self.assertEqual(report["summary"]["game_count"], 1)
        self.assertEqual(report["cors_join_coverage"][-1]["ambiguous_identity"], 1)
        self.games.pop()
        self.save_pack()
        for path in (result, spread):
            table = pd.read_html(io.StringIO(path.read_text()))[0]
            table.loc[0, "game_id"] = "99999999"
            table.to_html(path, index=False)
        _, report = self.run_experiment("conflict")
        self.assertEqual(report["summary"]["game_count"], 1)
        self.assertEqual(report["cors_join_coverage"][-1]["conflicting_game_id"], 1)
        # Restore IDs, then introduce a real source disagreement in one score.
        for path in (result, spread):
            table = pd.read_html(io.StringIO(path.read_text()))[0]
            table["game_id"] = table["game_id"].astype(object)
            table.loc[0, "game_id"] = "202230"
            if path == result:
                table.loc[0, "home_score"] = 10
            table.to_html(path, index=False)
        _, report = self.run_experiment("score-conflict")
        self.assertEqual(report["summary"]["game_count"], 1)
        self.assertEqual(report["cors_join_coverage"][-1]["conflicting_scores"], 1)

    def test_duplicate_identity_and_invalid_metrics_fail_before_output(self):
        self.stats[2020].append(dict(self.stats[2020][0]))
        self.save_pack()
        self.assert_failure("duplicate starter-pack game/team")
        self.stats[2020].pop()
        for field, value, error in (("offense_ppa", "NaN", "non-finite"),
                                     ("offense_successRate", "2", "outside [0, 1]"),
                                     ("opponent", "Unknown", "disagree with game identity")):
            prior = self.stats[2020][0][field]
            self.stats[2020][0][field] = value
            self.save_pack()
            self.assert_failure(error)
            self.stats[2020][0][field] = prior
        self.assertFalse((self.root / "failed").exists())


if __name__ == "__main__":
    unittest.main()
