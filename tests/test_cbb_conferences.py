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
                standings = doc.xpath('//table[@aria-label="Conference standings"]')[0]
                rows = [[cell.text_content() for cell in row] for row in standings.xpath("./tbody/tr")]
                actual["/" + page.relative_to(SITE).as_posix()] = [
                    tuple(row[i] for i in (0, 1, 2, 4)) for row in sorted(rows, key=lambda row: int(row[0]))
                ]
                self.assertEqual(standings.xpath("./thead/tr/th[4]/text()"), ["Conf"])
                self.assertTrue(doc.xpath('//dl[contains(@class, "conference-summary")]'))
                self.assertTrue(doc.xpath('//script[@src="/cfb/assets/sort-history.js"]'))
                ooc = doc.xpath('//table[@aria-label="Out-of-conference record"]')
                if ooc:
                    self.assertEqual(ooc[0].xpath("./tfoot/tr/th/text()"), ["Total OOC"])
                def order(row):
                    if row[3] == "—":
                        return 0, 0, 0, int(row[0])
                    wins, losses = map(int, row[3].split("–"))
                    pct = wins / (wins + losses) if wins + losses else -1
                    return -pct, -wins, losses, int(row[0])
                self.assertEqual(rows, sorted(rows, key=order), page)
        self.assertTrue(expected)
        self.assertEqual(dict(expected), actual)
