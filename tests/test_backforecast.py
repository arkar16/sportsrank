"""Behavioral checks for local backforecast performance tracking."""

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.backforecast import backforecast, write_report


class BackforecastTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "website"
        self.season = self.site / "cfb/years/2026"

    def rankings(self, checkpoint, ratings):
        path = self.season / "rankings" / f"2026_{checkpoint}_FBS_cors.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pd.DataFrame([
            {"school": name, "cors": rating} for name, rating in ratings
        ]).to_html(index=False))

    def results(self, week, rows):
        path = self.season / "data/results/weekly_results" / f"2026_W{week}_FBS_results.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(pd.DataFrame(rows).to_html(index=False))

    @staticmethod
    def game(week=0, home="A", away="B", home_score=24, away_score=21, neutral=False):
        return {"week": week, "home_team": home, "away_team": away,
                "home_division": "fbs", "away_division": "fbs",
                "home_score": home_score, "away_score": away_score,
                "neutral_site": neutral}

    def sample(self):
        self.rankings("PRESEASON", [("A", 20), ("B", 18), ("C", 20), ("D", 20)])
        self.rankings("W0", [("A", 10), ("B", 15), ("E", 20.02), ("F", 20)])
        self.rankings("W1", [("A", 100), ("B", 0), ("E", 100), ("F", 0)])
        other_class = self.game(home="X", away="Y")
        other_class["away_division"] = "fcs"
        self.results(0, [self.game(), self.game(home="C", away="D", home_score=7, away_score=0, neutral=True),
                         other_class, self.game(home="Pending", away="Unknown", home_score="", away_score="")])
        self.results(1, [self.game(1, home_score=10, away_score=13),
                         self.game(1, "E", "F", 7, 6, True)])

    def test_preceding_checkpoints_and_independent_arithmetic_without_network(self):
        self.sample()
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            report = backforecast(self.site, 2026)
        self.assertEqual([row["rating_checkpoint"] for row in report["games"]],
                         ["PRESEASON", "PRESEASON", "W0", "W0"])
        self.assertEqual([Decimal(row["home_handicap"]) for row in report["games"]],
                         [Decimal(-4), Decimal(0), Decimal(3), Decimal("-0.02")])
        self.assertEqual(report["games"][2]["predicted_winner"], "B")
        self.assertEqual(report["games"][2]["cors_line_coverage"], "push")
        self.assertEqual(report["games"][3]["straight_up"], "correct")
        self.assertEqual(report["games"][3]["cors_line_coverage"], "cover")
        first, second = report["weekly"]
        self.assertEqual(Decimal(first["mae"]), Decimal(4))
        self.assertEqual(Decimal(first["rmse"]), Decimal(5))
        self.assertEqual(first["pickems"], 1)
        self.assertEqual(first["straight_up_count"], 1)
        self.assertEqual(first["non_fbs_games"], 1)
        self.assertEqual(first["missing_scores"], 1)
        self.assertEqual(Decimal(second["mae"]), Decimal("0.49"))
        self.assertEqual(second["coverage_percentage"], "1")
        summary = report["summary"]
        self.assertEqual(summary["game_count"], 4)
        self.assertEqual(summary["straight_up_wins"], 3)
        self.assertEqual(summary["pushes"], 1)
        self.assertEqual(summary["coverage_percentage"], "0.5")

    def test_week_five_uses_week_four_without_publication_receipts(self):
        self.rankings("W4", [("A", 10), ("B", 15)])
        self.rankings("W5", [("A", 100), ("B", 0)])
        self.results(5, [self.game(5, home_score=10, away_score=13)])
        report = backforecast(self.site, 2026, from_week=5, through_week=5)
        self.assertEqual(report["summary"]["game_count"], 1)
        self.assertEqual(report["games"][0]["rating_checkpoint"], "W4")
        self.assertEqual(report["summary"]["pushes"], 1)
        self.assertEqual(Decimal(report["summary"]["mae"]), Decimal(0))

    def test_missing_preceding_rankings_never_fall_back_to_later_rankings(self):
        self.rankings("W5", [("A", 100), ("B", 0)])
        self.results(5, [self.game(5)])
        with self.assertRaises(FileNotFoundError):
            backforecast(self.site, 2026, from_week=5, through_week=5)

    def test_duplicate_games_missing_ratings_and_invalid_inputs_fail(self):
        self.rankings("PRESEASON", [("A", 20), ("B", 18)])
        cases = (
            ([self.game(), self.game()], "duplicate game"),
            ([self.game(home="Unrated")], "missing PRESEASON rating"),
            ([self.game(home_score="12.5")], "nonnegative integer"),
            ([{**self.game(), "neutral_site": "maybe"}], "neutral_site"),
            ([{**self.game(), "week": 2}], "wrong week"),
        )
        for rows, message in cases:
            with self.subTest(message=message):
                self.results(0, rows)
                with self.assertRaisesRegex(ValueError, message):
                    backforecast(self.site, 2026)
        self.rankings("PRESEASON", [("A", 20), ("A", 21), ("B", 18)])
        self.results(0, [self.game()])
        with self.assertRaisesRegex(ValueError, "duplicate school"):
            backforecast(self.site, 2026)

    def test_report_output_preserves_input_bytes_and_rejects_site_destination(self):
        self.sample()
        original = {path: path.read_bytes() for path in self.site.rglob("*") if path.is_file()}
        report = backforecast(self.site, 2026)
        output = self.root / "reports"
        write_report(report, output, self.site)
        self.assertEqual({path: path.read_bytes() for path in self.site.rglob("*") if path.is_file()}, original)
        self.assertEqual({path.name for path in output.iterdir()},
                         {"report.html", "report.json", "games.csv", "weekly.csv"})
        self.assertIn("CORS line coverage", (output / "report.html").read_text())
        with self.assertRaisesRegex(ValueError, "outside the website"):
            write_report(report, self.site / "reports", self.site)
        self.assertFalse((self.site / "reports").exists())


if __name__ == "__main__":
    unittest.main()
