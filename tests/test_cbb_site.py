from datetime import datetime, timezone
import unittest

from bs4 import BeautifulSoup

from cbb.site import render_rankings


class BasketballPageTest(unittest.TestCase):
    def test_rankings_and_home_margin_are_visible_in_static_html(self):
        ranking = [{"rank": 1, "team": "A & M", "wins": 3, "losses": 1, "rating": 20.5}]
        game = {"tipoff": "Nov 20, 07:00 PM", "away": "A & M", "home": "B",
                "predicted_home_margin": -3.25}
        page = BeautifulSoup(render_rankings(2027, "2026-11-16T11:00:00Z", ranking, [game],
                                             datetime(2026, 11, 19, tzinfo=timezone.utc)), "html.parser")
        self.assertIn("2026–27", page.select_one(".edition").get_text())
        tables = page.select("tbody tr")
        self.assertEqual(list(tables[0].stripped_strings), ["1", "A & M", "3–1", "20.50"])
        self.assertEqual(list(tables[1].stripped_strings)[-2:], ["A & M", "3.25"])
