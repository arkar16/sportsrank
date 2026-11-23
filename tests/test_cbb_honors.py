from pathlib import Path
import unittest

from bs4 import BeautifulSoup

SITE = Path(__file__).resolve().parents[1] / "website"


class BasketballHonorsTest(unittest.TestCase):
    def test_history_matches_the_final_standings_and_tournament_exceptions(self):
        def tables(path):
            page = BeautifulSoup((SITE / path).read_bytes(), "lxml")
            for link in page.select("main a[href^='/cbb/']"):
                self.assertTrue((SITE / link["href"].lstrip("/")).is_file(), link["href"])
            return [table.select("tbody tr") for table in page.select("main table")]

        championships, leaders = tables("cbb/history/champions.html")
        last, = tables("cbb/history/last-place.html")
        by_year = {row.td.get_text(): row for row in championships}
        self.assertEqual(len(by_year), len(championships))
        self.assertIn("Oregon", by_year["1938–39"].get_text())
        self.assertIn("Title vacated", by_year["2012–13"].get_text())
        self.assertIn("Tournament canceled", by_year["2019–20"].get_text())
        self.assertEqual([r.td.text for r in leaders], [r.td.text for r in last])
        for top, bottom in zip(leaders, last):
            dest = top.td.a["href"]
            season = BeautifulSoup((SITE / dest.lstrip("/")).read_bytes(), "lxml")
            standings = season.select_one(".season-standings table").select("tbody tr")
            for row, expected in [(top, standings[0]), (bottom, standings[-1])]:
                self.assertEqual(row.select("td")[1].text.strip(), expected.th.text.strip())
                self.assertEqual(row.select("td")[2].text, expected.td.text)
