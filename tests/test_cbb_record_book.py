from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cbb.issued import grade_edition
from cbb.record_book import build
from cbb.replay import _replay
from cbb.source import Game, Season, Team


def instant(stamp):
    return datetime.fromisoformat(stamp)


class BasketballBookTests(unittest.TestCase):
    def test_navigation_and_results_follow_the_visible_checkpoint(self):
        schools = {1: Team(1, "A & M"), 2: Team(2, "B")}
        season = Season(2020, schools, (
            Game(10, instant("2019-11-05T22:00Z"), 1, 2, False, "final", (74, 60)),
            Game(11, instant("2019-11-12T22:00Z"), 2, 1, True, "final", (71, 65)),
        ))
        replay = _replay(season, (), 2., 1., 4.)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build(root, [replay], instant("2019-11-12T23:00Z"), {1})
            def read(path):
                return BeautifulSoup((root / path).read_bytes(), "html.parser")
            page = read("teams/1/2020.html")
            scores = [row.find_all("td")[-1].text for row in page.select("table")[0].select("tbody tr")]
            self.assertEqual(scores, ["60–74", "—"])
            self.assertIn("1–0", page.get_text())
            self.assertIn("A & M", read("teams/index.html").text)
            self.assertTrue(read("teams/1/index.html").select_one('a[href="/cbb/teams/1/2020.html"]'))
            self.assertEqual(len(read("seasons/2020/weeks/1.html").select("table")[0].select("tbody tr")), 2)
            self.assertFalse((root / "seasons/2020/weeks/2.html").exists())
            self.assertEqual([c.text for c in read("performance/index.html").select("tbody tr")[0].select("td")][1:],
                             ["1", "12.00", "1/1", "100.0%", "0"])

    def test_issued_grade_uses_printed_margin_and_verified_public_time(self):
        with TemporaryDirectory() as tmp:
            page = Path(tmp) / "edition.html"
            page.write_text('<h2>Game forecasts</h2><table><tbody><tr><td>tip</td><td>A</td>'
                            '<td>B</td><td>A</td><td>3.25</td></tr></tbody></table>')
            game = dict(start_utc="2026-11-19T20:00Z", actual_home_margin=-7, home_margin=8)
            fixtures = {("tip", "A", "B"): [(game, True)]}
            self.assertEqual(grade_edition(page, instant("2026-11-19T19:59Z"), fixtures),
                             ([(-3.25, -7)], 0, 0))
            self.assertEqual(grade_edition(page, instant(game["start_utc"]), fixtures), ([], 0, 1))
            fixtures[("tip", "A", "B")].append((game, True))
            self.assertEqual(grade_edition(page, instant("2026-11-19T19:59Z"), fixtures), ([], 1, 0))
