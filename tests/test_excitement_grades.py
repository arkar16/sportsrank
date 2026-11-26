"""Verify the public grade scale against the independent historical score census."""
from datetime import datetime
from math import ceil
from pathlib import Path
import unittest

from lxml import html

from cbb.site import _forecasts
from cfb.excitement_pages import aev_label, bev_label
from cfb.game_excitement import GameAppeal
from excitement_grades import letter_grade, read_number


SITE = Path(__file__).resolve().parents[1] / "website"


def document(path):
    return html.parse(path, parser=html.HTMLParser(encoding="utf-8"))


class ExcitementGradeTests(unittest.TestCase):
    def test_public_cutoffs_reproduce_ten_seasons_and_include_the_lower_edge(self):
        page = document(SITE / "excitement/grades.html")
        rows = page.xpath("//tbody/tr")
        grades = [r[0].text for r in rows]
        self.assertEqual(grades, "A+ A A- B+ B B- C+ C C- D+ D D- F".split())
        percentiles = (99, 95, 90, 85, 75, 65, 55, 40, 30, 20, 15, 10)
        for sport, folder, start, index, col in (("cfb", "years", 2014, 3, 1),
                                                 ("cbb", "seasons", 2015, 4, 3)):
            samples = {"BEV": [], "AEV": []}
            for year in range(start, start + 10):
                guide = document(SITE / sport / folder / str(year) / "excitement/index.html")
                for game in guide.xpath("//tbody/tr"):
                    pre = game[index].text_content()
                    if "Pregame baseline" not in pre:
                        samples["BEV"].append(float(pre.split()[0]))
                    if "Play-by-play" in game[-1].text_content():
                        samples["AEV"].append(float(game[-1].text_content().split()[0]))
            for column, (metric, sample) in enumerate(samples.items(), col):
                sample.sort()
                minima = [float(r[column].text) for r in rows[:-1]]
                expected = [sample[ceil(len(sample) * p / 100) - 1] for p in percentiles]
                self.assertEqual(minima, expected, (sport, metric))
                self.assertEqual(rows[-1][column].text, f"Below {minima[-1]:.1f}")
                for i, cutoff in enumerate(minima):
                    self.assertEqual(letter_grade(sport, metric, f"{cutoff:.1f}"), grades[i])
                    self.assertEqual(letter_grade(sport, metric, f"{cutoff - .1:.1f}"), grades[i + 1])
                self.assertEqual(letter_grade(sport, metric, "100.0"), "A+")
                self.assertEqual(letter_grade(sport, metric, "0.0"), "F")

    def test_display_precision_evidence_and_unscored_game(self):
        game = GameAppeal("70.0", (65, 63), 71.46, "halftime", bev_basis="Pregame baseline")
        self.assertIn("70.0 (A)", bev_label(game, sport="cbb"))
        self.assertIn("Pregame baseline", bev_label(game, sport="cbb"))
        self.assertEqual(bev_label(game, plain=True), "70.0 · Pregame baseline")
        self.assertIn("71.5* (A+)", aev_label(game, sport="cbb"))
        self.assertIn("Halftime score", aev_label(game, sport="cbb"))
        self.assertEqual(aev_label(GameAppeal("70.0", None), sport="cbb"), "&mdash;")
        self.assertEqual(bev_label(None, sport="cfb"), "&mdash;")
        self.assertEqual(read_number("cfb", "BEV", "70.0"), "70.0")
        with self.assertRaises(ValueError):
            read_number("cfb", "AEV", "70.0* (A+)")

    def test_current_and_future_forecasts_show_grades_and_preserve_the_dated_edition(self):
        row = dict(home="Home", away="Away", tipoff="Nov 27", predicted_home_margin=2.0, bev="70.0")
        for stamp, current, expected in (
            ("2026-11-26T16:47:00Z", False, "70.0"),
            ("2026-11-26T16:47:00Z", True, "70.0 (A)"),
            ("2026-11-27T12:00:00Z", False, "70.0 (A)"),
        ):
            page = html.fromstring(_forecasts([row], datetime.fromisoformat(stamp), (), current))
            self.assertEqual(page.xpath("//tbody/tr/td[last()]/text()"), [expected])
