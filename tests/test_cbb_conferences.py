from collections import defaultdict
from pathlib import Path
import unittest

from lxml import html

SITE = Path(__file__).resolve().parents[1] / "website"


class ConferenceArchiveTest(unittest.TestCase):
    def test_conference_membership_and_ranks_match_each_season(self):
        directory = SITE / "cbb/conferences"
        expected = defaultdict(list)
        for season in sorted((SITE / "cbb/seasons").glob("*/index.html")):
            doc = html.parse(str(season))
            for row in doc.xpath("//table[@aria-labelledby='cors-ranking']/tbody/tr"):
                cells = row.xpath("./*")
                conference = cells[6].xpath("./a/@href")
                if not conference:
                    self.assertEqual(cells[6].text_content(), "—")
                    continue
                expected[conference[0]].append(tuple(cells[i].text_content() for i in (0, 1, 5, 4)))
        actual = {}
        for page in directory.glob("*/*.html"):
            doc = html.parse(str(page))
            for url in doc.xpath("//main//a/@href"):
                self.assertTrue(url.startswith("/cbb/") and (SITE / url[1:]).is_file(), url)
            if page.name != "index.html":
                actual["/" + page.relative_to(SITE).as_posix()] = [
                    tuple(cell.text_content() for cell in row.xpath("./*"))
                    for row in doc.xpath("//table/tbody/tr")]
        self.assertTrue(expected)
        self.assertEqual(dict(expected), actual)
