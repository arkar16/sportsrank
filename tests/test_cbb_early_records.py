from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from cbb.early_records import read_early_records
from scripts.backfill_cbb_records import read_scores

SITE = Path(__file__).resolve().parents[1] / "website"


class EarlyRecordsTest(unittest.TestCase):
    def test_score_file_line_wrap_and_unknown_school(self):
        raw = b"<p>AAA 72 BBB\n60 H<br>BBB 80 AAA 77 N</p>"
        self.assertEqual(list(read_scores(raw, {"AAA", "BBB"})),
                         [("AAA", 72, "BBB", 60), ("BBB", 80, "AAA", 77)])
        with self.assertRaises(ValueError):
            list(read_scores(raw, {"AAA"}))

    def test_historical_records_and_season_navigation(self):
        seasons = read_early_records(1985)
        self.assertEqual(list(seasons), list(range(1950, 1985)))
        self.assertEqual(read_early_records(1950), {})
        links = {}
        for name in ("index.html", "seasons/index.html", "history/champions.html", "history/last-place.html"):
            page = BeautifulSoup((SITE / "cbb" / name).read_bytes(), "lxml")
            links[name] = {a["href"] for a in page.select("main a[href]")}
        for year, records in seasons.items():
            path = f"/cbb/seasons/{year}/index.html"
            self.assertIn(path, links["index.html"])
            self.assertIn(path, links["seasons/index.html"])
            self.assertEqual(path in links["history/champions.html"], year == 1984)
            self.assertEqual(path in links["history/last-place.html"], year == 1984)
            self.assertEqual(len({r[0] for r in records}), len(records))
            for i in (1, 3, 5):
                self.assertEqual(sum(r[i] for r in records), sum(r[i + 1] for r in records))
        season = BeautifulSoup((SITE / "cbb/seasons/1950/index.html").read_bytes(), "lxml")
        displayed = [tuple(c.get_text(strip=True) for c in row.select("td")) for row in season.select("tbody tr")]
        self.assertIn(("Arizona", "0–1", "—", "—", "0–1"), displayed)
        self.assertIn(("City College of New York", "21–5", "14–5", "3–0", "4–0"), displayed)
