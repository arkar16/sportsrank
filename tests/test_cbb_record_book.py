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
    def test_best_rank_links_open_weekly_and_final_only_standings(self):
        schools = {90001: Team(90001, "A & M"), 90002: Team(90002, "B")}
        completed = _replay(Season(1982, schools, (
            Game(10, instant("1981-11-05T22:00Z"), 90001, 90002, False, "final", (60, 74)),
        )), (), 2., 1., 4.)
        future = _replay(Season(1983, schools, (
            Game(11, instant("1982-11-16T22:00Z"), 90001, 90002, False, "final", (71, 65)),
        )), (), 2., 1., 4.)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, routes, _ = build(root, [completed, future], instant("1982-11-10T23:00Z"), set(),
                                 include_early_finals=True)
            for school, rank, href, title, aria_label, destination_heading in (
                ("A & M", "1", "/cbb/seasons/1982/weeks/0.html", "Preseason",
                 "1981–82 best rank 1, Preseason", "1981–82 basketball · Preseason"),
                ("B", "1", "/cbb/seasons/1982/weeks/1.html", "Week 1",
                 "1981–82 best rank 1, Week 1", "1981–82 basketball · Week 1"),
                ("Indiana", "1", "/cbb/seasons/1981/index.html", "Final",
                 "1980–81 best rank 1, Final", "1980–81 Division I basketball"),
            ):
                with self.subTest(school=school):
                    history = BeautifulSoup((root / routes[school].removeprefix("/cbb/")).read_bytes(), "html.parser")
                    links = history.select("table tbody td:nth-of-type(4) a")
                    self.assertEqual(len(links), 1)
                    link = links[0]
                    self.assertEqual((link.text, link["href"], link["title"], link["aria-label"]),
                                     (rank, href, title, aria_label))
                    destination = root / link["href"].removeprefix("/cbb/")
                    page = BeautifulSoup(destination.read_bytes(), "html.parser")
                    self.assertEqual(page.h1.text, destination_heading)
            final = BeautifulSoup((root / "seasons/1982/weeks/final.html").read_bytes(), "html.parser")
            self.assertEqual(final.select_one('a[aria-current="page"]')["href"], "/cbb/seasons/1982/weeks/final.html")
            self.assertFalse((root / "seasons/1981/weeks").exists())
            self.assertFalse((root / "seasons/1983/weeks").exists())

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
            self.assertIn("A & M first", page.select_one("thead").text)
            self.assertEqual(scores, ["74–60", "—"])
            self.assertIn("1–0", page.get_text())
            directory = read("teams/index.html")
            history = read("teams/1/index.html")
            self.assertIn("A & M", directory.select_one(".program-list").text)
            self.assertTrue(history.select_one('a[href="/cbb/teams/1/2020.html"]'))
            sorter = 'script[src="/cfb/assets/sort-history.js"][defer]'
            self.assertIsNotNone(history.select_one(sorter))
            self.assertIsNone(directory.select_one(sorter))
            self.assertIsNone(page.select_one(sorter))
            self.assertTrue(page.select_one('a[href="/cbb/teams/2/2020.html"]'))
            week1 = read("seasons/2020/weeks/1.html")
            self.assertEqual(len(week1.select("table")[0].select("tbody tr")), 2)
            self.assertEqual(week1.select_one('a[aria-current="page"]')["href"], "/cbb/seasons/2020/weeks/1.html")
            self.assertTrue(week1.select_one('a[href="/cbb/teams/1/2020.html"]'))
            self.assertFalse((root / "seasons/2020/weeks/2.html").exists())
            self.assertTrue((root / "seasons/2020/progression.html").is_file())
            self.assertTrue((root / "seasons/2020/progression.json").is_file())
            perf = read("performance/index.html")
            self.assertEqual(
                [c.text for c in perf.select("tbody tr")[0].select("td")][1:],
                ["1", "1", "0", "0", "100.0%", "1", "0", "0", "100.0%", "12.00", "12.00"],
            )
            self.assertEqual(perf.select("tbody tr")[0].td.a["href"], "/cbb/performance/2020/index.html")
            for artifact in ("performance/seasons.csv", "performance/2020/index.html",
                             "performance/2020/weekly.csv", "performance/2020/games.csv",
                             "performance/2020/report.json"):
                self.assertTrue((root / artifact).is_file(), artifact)

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
