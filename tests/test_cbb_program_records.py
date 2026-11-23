from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from cbb.program_records import read_program_records

SITE = Path(__file__).resolve().parents[1] / "website/cbb"


class ProgramRecordsTest(unittest.TestCase):
    def test_archive_links_to_all_founding_seasons(self):
        def destinations(relative):
            doc = BeautifulSoup((SITE / relative).read_bytes(), "lxml")
            return {a["href"] for a in doc.select("main a[href]")}

        seasons = read_program_records(1950)
        self.assertEqual(set(seasons), set(range(1893, 1950)))
        self.assertEqual(set(read_program_records(1894)), {1893})
        links = {f"/cbb/seasons/{y}/index.html" for y in seasons}
        self.assertLessEqual(links, destinations("index.html") & destinations("seasons/index.html"))
        self.assertTrue(links.isdisjoint(destinations("history/champions.html") | destinations("history/last-place.html")))
        self.assertIn("/cbb/seasons/1939/index.html", destinations("history/ncaa-champions.html"))

    def test_records_distinguish_ties_and_school_totals_from_game_logs(self):
        def row(year, school):
            doc = BeautifulSoup((SITE / f"seasons/{year}/index.html").read_bytes(), "lxml")
            return next(r for r in doc.select("tbody tr") if r.td.get_text(strip=True) == school)

        self.assertEqual(row(1894, "Chicago").select("td")[1].text, "6–1")
        self.assertEqual(row(1897, "Minnesota").select("td")[1].text, "3–6–1")
        asu = row(1918, "Arizona State")
        self.assertEqual(asu.select("td")[1].text, "14–4")
        self.assertIn("Individual game results unavailable", asu.text)
        self.assertIn("/schools/arizona-state/source-notes.md", asu.select("td")[-1].a["href"])
