"""Behavioral checks for the static season and checkpoint pages."""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cfb.reference_archive import Game, Season, Standing
from cfb.season_pages import build_season_pages


def standing(school, rank, rating):
    return Standing(school, rank, Decimal(rating), "12-2", "Big Ten")


def game(week, home, away, home_score, away_score, handicap):
    return Game(week, home, away, home_score, away_score, Decimal(handicap) if handicap is not None else None)


def cells(page, section):
    return [
        [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        for row in page.select(f'table[aria-labelledby="{section}"] tbody tr')
    ]


class SeasonPageTests(unittest.TestCase):
    def setUp(self):
        self.site = Path(self.enterContext(TemporaryDirectory()))
        self.routes = {"Oregon": "/cfb/teams/oregon/index.html", "Georgia Tech": "/cfb/teams/georgia-tech/index.html"}

    def write(self, relative, text):
        path = self.site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def render(self, year, rankings, games=()):
        return dict(build_season_pages(self.site, Season(year, rankings, list(games)), self.routes))

    def test_completed_season_compares_from_preseason_and_lists_top_three_across_weeks(self):
        rankings = {
            "FINAL": [standing("Oregon", 2, "72.35"), standing("New & State", 7, "67.30")],
            "W11": [standing("Oregon", 3, "70.40")],
            "W2": [standing("Oregon", 8, "61.00")],
            "PRESEASON": [standing("Oregon", 10, "56.60")],
        }
        games = [
            game(2, "Favorite A", "Winner A", 14, 21, "-16.04"),
            game(11, "Favorite B", "Winner B", 10, 20, "-8"),
            game(11, "Winner C", "Favorite C", 21, 14, "18"),
            game(20, "Favorite D", "Winner D", 0, 7, "-12"),
        ]
        base = "cfb/years/2025"
        pages = self.render(2025, rankings, games)
        landing = BeautifulSoup(pages[f"{base}/2025_CFB.html"], "html.parser")
        self.assertEqual(landing.select_one("#cors-ranking").get_text(), "Final CORS Ranking")
        self.assertIn("Change since Preseason.", landing.get_text())
        self.assertEqual(cells(landing, "cors-ranking"), [
            ["2", "Oregon", "+8", "+15.75", "72.35", "12-2", "Big Ten"],
            ["7", "New & State", "—", "—", "67.30", "12-2", "Big Ten"],
        ])
        self.assertEqual(landing.find("a", string="Oregon")["href"], self.routes["Oregon"])
        self.assertEqual(cells(landing, "cors-upsets"), [
            ["11", "Favorite C by 18.00", "Winner C 21–14 Favorite C"],
            ["2", "Favorite A by 16.04", "Winner A 21–14 Favorite A"],
            ["20", "Favorite D by 12.00", "Winner D 7–0 Favorite D"],
        ])
        self.assertEqual(
            [a["href"] for a in landing.select('nav[aria-label="Ranking checkpoints"] a')],
            ["checkpoints/PRESEASON.html", "checkpoints/W2.html", "checkpoints/W11.html", "checkpoints/FINAL.html"],
        )
        self.assertIsNone(landing.find("script"))
        self.assertIsNone(landing.find("form"))
        self.assertEqual(landing.find("link", rel="stylesheet")["href"], "/cfb/assets/record-book.css")

        week = BeautifulSoup(pages[f"{base}/checkpoints/W11.html"], "html.parser")
        self.assertIn("Change since Week 2.", week.get_text())
        self.assertEqual(cells(week, "cors-ranking")[0][2:4], ["+5", "+9.40"])
        self.assertEqual([row[1] for row in cells(week, "cors-upsets")], ["Favorite C by 18.00", "Favorite B by 8.00"])
        self.assertEqual(week.select_one('a[aria-current="page"]')["href"], "W11.html")
        self.assertEqual(week.find("a", string="2025 season")["href"], "../2025_CFB.html")

    def test_active_season_uses_latest_checkpoint_exact_spreads_and_one_click_game_links(self):
        rankings = {
            "PRESEASON": [standing("Clemson", 1, "71.00")],
            "W9": [standing("Clemson", 1, "72.00")],
            "W10": [standing("Clemson", 1, "71.29")],
            "W11": [standing("Clemson", 4, "71.29")],
            "W12": [],
        }
        games = [
            game(11, "Clemson", "Georgia Tech", 6, 20, "-15.09"),
            game(11, "Washington State", "Colorado State", 73, 7, "3.18"),
            game(11, "Just below", "Underdog", 7, 14, "-6.999"),
            game(11, "First winner", "First favorite", 10, 7, "7.00"),
            game(11, "Second favorite", "Second winner", 7, 10, "-7.00"),
            game(11, "Third favorite", "Third winner", 7, 10, "-7.00"),
            game(11, "Favorite won", "Covered", 10, 7, "-45"),
            game(11, "Tie favorite", "Tie underdog", 7, 7, "-45"),
            game(11, "Pending", "Pending opponent", 7, None, "-45"),
            game(11, "No spread", "Winner", 0, 14, None),
            game(10, "Other week", "Other winner", 7, 17, "-25"),
        ]
        base = "cfb/years/2026"
        result = "data/results/weekly_results/2026_W11_FBS_results.html"
        slate = "data/slate/weekly_slate/2026_W12_FBS_slate.html"
        forecast = "spread/2026_W12_FBS_spread.html"
        for relative in (result, slate, forecast):
            self.write(f"{base}/{relative}", "<h1>Archived game data</h1>")
        pages = self.render(2026, rankings, games)
        landing = BeautifulSoup(pages[f"{base}/2026_CFB.html"], "html.parser")
        self.assertEqual(landing.select_one("#cors-ranking").get_text(), "Week 11 CORS Ranking")
        self.assertIn("Change since Week 10.", landing.get_text())
        self.assertEqual(cells(landing, "cors-ranking")[0][2:4], ["-3", "0.00"])
        self.assertEqual(cells(landing, "cors-upsets"), [
            ["11", "Clemson by 15.09", "Georgia Tech 20–6 Clemson"],
            ["11", "First favorite by 7.00", "First winner 10–7 First favorite"],
            ["11", "Second favorite by 7.00", "Second winner 10–7 Second favorite"],
        ])
        self.assertEqual(landing.find("a", string="Georgia Tech")["href"], self.routes["Georgia Tech"])
        self.assertEqual(
            [a["href"] for a in landing.select('nav[aria-label="Games and forecasts"] a')], [result, slate, forecast]
        )
        week = BeautifulSoup(pages[f"{base}/checkpoints/W11.html"], "html.parser")
        self.assertEqual(
            [a["href"] for a in week.select('nav[aria-label="Games and forecasts"] a')],
            ["../" + path for path in (result, slate, forecast)],
        )
        self.assertNotIn(f"{base}/checkpoints/W12.html", pages)
        for relative, html in pages.items():
            self.write(relative, html)
        self.assertEqual(self.render(2026, rankings, games), pages)
        rankings["W12"] = [standing("Clemson", 3, "73")]
        following = self.render(2026, rankings, games)
        self.assertIn(f"{base}/checkpoints/W11.html", following)
        self.assertIn("Week 12 CORS Ranking", following[f"{base}/2026_CFB.html"])

    def test_legacy_landing_retains_metadata_and_links_in_week_order_across_reruns(self):
        base = "cfb/years/1897"
        landing_path = f"{base}/1897_CFB.html"
        final = "rankings/1897_FINAL_FBS_cors.html"
        first = "rankings/1897_W1_FBS_cors.html"
        second = "rankings/1897_W2_FBS_cors.html"
        forecast = "spread/1897_W1_FBS_spread.html"
        unavailable = "spread/1897_W2_FBS_spread.html"
        progression = "history/1897_FBS_progression.html"
        self.write(
            landing_path,
            '<html><head><title>Historic CORS v0.2.0</title><meta name="description" content="CFB in 1897"></head>'
            '<body><h1>Historic CORS Results</h1><p>Last updated: 2001-05-07</p>'
            f'<p><a href="{progression}">Ranking progression</a></p></body></html>'
            f'<a href="{final}">Legacy final</a><br><a href="{first}">Legacy Week 1 rankings</a><br>'
            f'<a href="{second}">Legacy Week 2 rankings</a><br>'
            f'<a href="{forecast}">Legacy Week 1 spread</a><br><a href="{forecast}">Legacy Week 1 spread</a><br>'
            f'<a href="{unavailable}">Legacy Week 2 spread</a><br>',
        )
        source = "<table><tr><td>Original ranking</td></tr></table>"
        for relative in (final, first, second, forecast, progression):
            self.write(f"{base}/{relative}", source)
        rankings = {
            "FINAL": [standing("Oregon", 1, "70")],
            "W2": [standing("Oregon", 3, "65")],
            "W1": [standing("Oregon", 5, "60")],
        }
        pages = self.render(1897, rankings)
        landing = BeautifulSoup(pages[landing_path], "html.parser")
        self.assertEqual(landing.title.get_text(), "Historic CORS v0.2.0")
        self.assertEqual(landing.h1.get_text(), "Historic CORS Results")
        self.assertEqual(landing.find("meta", attrs={"name": "description"})["content"], "CFB in 1897")
        self.assertIn("Last updated: 2001-05-07", landing.get_text())
        self.assertIn("Change since the earliest available ranking (Week 1).", landing.get_text())
        self.assertEqual(cells(landing, "cors-ranking")[0][2:4], ["+4", "+10.00"])
        self.assertEqual(
            [(a["href"], a.get_text()) for a in landing.select("#season-archive a")],
            [(progression, "Ranking progression"), (final, "Legacy final"), (first, "Legacy Week 1 rankings"),
             (forecast, "Legacy Week 1 spread"), (second, "Legacy Week 2 rankings")],
        )
        self.assertIsNone(landing.find("a", href=unavailable))
        self.assertIn("Legacy Week 2 spread", landing.select_one("#season-archive").get_text())
        self.assertEqual(cells(
            BeautifulSoup(pages[f"{base}/checkpoints/W1.html"], "html.parser"), "cors-ranking"
        )[0][2:4], ["—", "—"])
        for relative, html in pages.items():
            self.write(relative, html)
        self.assertEqual(self.render(1897, rankings), pages)
        self.assertEqual((self.site / base / first).read_text(encoding="utf-8"), source)

    def test_final_only_season_has_no_invented_comparison_or_week_links(self):
        pages = self.render(1900, {"FINAL": [standing("Oregon", 8, "57")]})
        landing = BeautifulSoup(pages["cfb/years/1900/1900_CFB.html"], "html.parser")
        self.assertIn("Earlier rankings are unavailable.", landing.get_text())
        self.assertEqual(cells(landing, "cors-ranking")[0][2:4], ["—", "—"])
        self.assertEqual(
            [a["href"] for a in landing.select('nav[aria-label="Ranking checkpoints"] a')],
            ["checkpoints/FINAL.html"],
        )
