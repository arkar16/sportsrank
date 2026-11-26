from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from cbb.program_records import read_program_records

SITE = Path(__file__).resolve().parents[1] / "website/cbb"


class ProgramRecordsTest(unittest.TestCase):
    def test_private_founding_season_registry_does_not_supply_public_routes(self):
        def destinations(relative):
            doc = BeautifulSoup((SITE / relative).read_bytes(), "lxml")
            return {a["href"] for a in doc.select("main a[href]")}

        seasons = read_program_records(1950)
        self.assertEqual(set(seasons), set(range(1893, 1950)))
        self.assertEqual(set(read_program_records(1894)), {1893})
        before_api = {f"/cbb/seasons/{y}/index.html" for y in range(1893, 1949)}
        for index in ("index.html", "seasons/index.html", "history/champions.html",
                      "history/last-place.html", "history/ncaa-champions.html"):
            self.assertTrue(before_api.isdisjoint(destinations(index)), index)
        for path in before_api:
            self.assertFalse((SITE / path.removeprefix("/cbb/")).exists(), path)

    def test_school_totals_are_absent_from_the_public_season_archive(self):
        for path in (SITE / "seasons").glob("*/index.html"):
            source = path.read_bytes()
            self.assertNotIn(b"school-reported", source.lower(), path)
            self.assertNotIn(b"source-notes.md", source, path)
