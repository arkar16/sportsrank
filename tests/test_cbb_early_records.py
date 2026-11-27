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

    def test_research_records_balance_and_do_not_supply_public_pages(self):
        seasons = read_early_records(1985)
        self.assertEqual(list(seasons), list(range(1950, 1985)))
        self.assertEqual(read_early_records(1950), {})
        for records in seasons.values():
            self.assertEqual(len({r[0] for r in records}), len(records))
            for i in (1, 3, 5):
                self.assertEqual(sum(r[i] for r in records), sum(r[i + 1] for r in records))
        for path in (SITE / "cbb/seasons").glob("*/index.html"):
            page = BeautifulSoup(path.read_bytes(), "lxml")
            self.assertFalse(page.select('a[href*="academics.smcvt.edu"]'), path)
            self.assertNotIn("Trono", page.get_text(), path)
