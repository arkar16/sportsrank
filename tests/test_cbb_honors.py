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

        championships, = tables("cbb/history/ncaa-champions.html")
        leaders, = tables("cbb/history/champions.html")
        last, = tables("cbb/history/last-place.html")
        by_year = {row.td.get_text(): row for row in championships}
        self.assertEqual(len(by_year), len(championships))
        self.assertNotIn("1938–39", by_year)
        self.assertNotIn("2019–20", by_year)
        for year, winner, runner, score in (("2020–21", "Baylor", "Gonzaga", "86–70"),
                                           ("2023–24", "UConn", "Purdue", "75–60"),
                                           ("2024–25", "Florida", "Houston", "65–63")):
            cells = [cell.get_text(strip=True) for cell in by_year[year].select("td")]
            self.assertEqual((cells[1], cells[3], cells[4]), (winner, runner, score))
        self.assertEqual([r.td.text for r in leaders], [r.td.text for r in last])
        for top, bottom in zip(leaders, last):
            dest = top.td.a["href"]
            season = BeautifulSoup((SITE / dest.lstrip("/")).read_bytes(), "lxml")
            standings = season.select_one(".season-standings table").select("tbody tr")
            for row, expected in [(top, standings[0]), (bottom, standings[-1])]:
                self.assertEqual(row.select("td")[1].text.strip(), expected.th.text.strip())
                self.assertEqual(row.select("td")[2].text, expected.td.text)
