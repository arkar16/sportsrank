from datetime import datetime, timezone
import unittest

from bs4 import BeautifulSoup

from cbb.site import render_rankings


class BasketballPageTest(unittest.TestCase):
    def test_rankings_and_home_margin_are_visible_in_static_html(self):
        ranking = [{"rank": 1, "team_id": 19, "team": "A & M", "wins": 3, "losses": 1, "rating": 20.5}]
        game = {"tipoff": "Nov 20, 07:00 PM", "away": "A & M", "away_id": 19, "home_id": 20, "home": "B",
                "predicted_home_margin": -3.25}
        page = BeautifulSoup(render_rankings(2027, "2026-11-16T11:00:00Z", ranking, [game],
                                             datetime(2026, 11, 19, tzinfo=timezone.utc), logo_ids={19}), "html.parser")
        self.assertIn("2026–27", page.select_one(".edition").get_text())
        self.assertEqual([link["href"] for link in page.select(".masthead nav a")][:2],
                         ["/cfb/cfb.html", "/cbb/index.html"])
        tables = page.select("tbody tr")
        self.assertEqual(list(tables[0].stripped_strings), ["1", "A & M", "3–1", "20.50"])
        self.assertEqual(list(tables[1].stripped_strings)[-2:], ["A & M", "3.25"])
        images = page.select("img")
        self.assertEqual([image.parent.get_text() for image in images], ["A & M", "A & M"])
        for image in images:
            self.assertEqual(image["src"], "/cbb/assets/team-logos/19.png")
            self.assertEqual(image["alt"], "")


class PublishedLayoutTest(unittest.TestCase):
    def test_basketball_uses_football_record_book_layouts(self):
        from pathlib import Path
        site = Path(__file__).resolve().parents[1] / "website"
        def read(path):
            return BeautifulSoup((site / path).read_bytes(), "lxml")
        football = read("cfb/cfb.html")
        basketball = read("cbb/index.html")
        for selector in (".home-columns > section", ".year-archive"):
            self.assertEqual(len(basketball.select(selector)), len(football.select(selector)), selector)
        basketball_table = basketball.select_one(".home-columns table")
        football_table = football.select_one(".home-columns table")
        basketball_columns = [next(th.stripped_strings) for th in basketball_table.select("thead th")]
        football_columns = [next(th.stripped_strings) for th in football_table.select("thead th")]
        self.assertEqual(basketball_columns, football_columns)
        rows = basketball_table.select("tbody tr")
        self.assertEqual([int(row.td.text) for row in rows], list(range(1, 26)))
        self.assertTrue(basketball.select_one("details.home-all table.home-rankings"))
        self.assertTrue(basketball.select_one('a[href="/cbb/current.html#forecasts"]'))
        for row in rows:
            self.assertRegex(row.th.a["href"], r"^/cbb/teams/\d+/\d{4}\.html$")
            self.assertTrue((site / row.th.a["href"].lstrip("/")).is_file())
            program = row.th.a["href"].split("/")[3]
            if (site / f"cbb/assets/team-logos/{program}.png").is_file():
                self.assertEqual(row.th.img["src"], f"/cbb/assets/team-logos/{program}.png")
        self.assertTrue(basketball_table.select_one(".home-result:last-child .home-game-score"))
        self.assertTrue(basketball.select_one(".year-archive a"))
        for path, selector in (("cbb/teams/index.html", ".program-list li"),
                               ("cbb/teams/295/index.html", ".rank-trend svg a"),
                               ("cbb/teams/295/2027.html", ".archive-nav + main table"),
                               ("cbb/seasons/2027/index.html", ".season-standings #cors-upsets"),
                               ("cbb/seasons/2027/weeks/1.html", ".week-links li"),
                               ("cbb/current.html", ".sr-report #forecasts"),
                               ("cbb/performance/index.html", ".sr-report-nav")):
            self.assertIsNotNone(read(path).select_one(selector), path)
