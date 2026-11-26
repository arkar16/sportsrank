"""Verify complete CBB website feature parity with CFB."""
import csv
import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from lxml import html

from cbb.grade import grade_game, grade_performance

SITE = Path(__file__).resolve().parents[1] / "website"


class PerformanceGradingTests(unittest.TestCase):
    def test_grade_game_and_performance_metrics(self):
        # Home favored by 5, wins by 8 -> correct, cover, abs err 3
        self.assertEqual(grade_game(5.0, 8), ("correct", "cover", 3.0))
        # Home favored by 5, wins by 5 -> correct, push, abs err 0
        self.assertEqual(grade_game(5.0, 5), ("correct", "push", 0.0))
        # Away favored by 4 (-4), away wins by 2 (-2) -> correct, no_cover, abs err 2
        self.assertEqual(grade_game(-4.0, -2), ("correct", "no_cover", 2.0))
        # Away favored by 4 (-4), home wins by 3 (+3) -> incorrect, no_cover, abs err 7
        self.assertEqual(grade_game(-4.0, 3), ("incorrect", "no_cover", 7.0))
        # Pick'em -> pick_em, no_pick, abs err 6
        self.assertEqual(grade_game(0.0, 6), ("pick_em", "no_pick", 6.0))

        summary = grade_performance([(5.0, 8), (5.0, 5), (-4.0, -2), (-4.0, 3), (0.0, 6)])
        self.assertEqual(summary.games, 5)
        self.assertEqual((summary.wins, summary.losses, summary.pick_ems), (3, 1, 1))
        self.assertEqual((summary.covers, summary.no_covers, summary.pushes), (1, 2, 1))
        self.assertAlmostEqual(summary.winner_accuracy, 0.75)
        self.assertAlmostEqual(summary.line_coverage, 1 / 3)
        self.assertAlmostEqual(summary.mae, (3 + 0 + 2 + 7 + 6) / 5)


class TeamHistoryLinksTests(unittest.TestCase):
    def test_every_best_rank_links_to_a_published_checkpoint(self):
        for sport, route in (
            ("cfb", r"/cfb/years/\d{4}/checkpoints/(?:PRESEASON|W\d+|FINAL)\.html"),
            ("cbb", r"/cbb/seasons/\d{4}/(?:weeks/(?:\d+|final)|index)\.html"),
        ):
            directory = SITE / sport / "teams"
            paths = set(directory.glob("*/index.html"))
            self.assertTrue(paths, directory)
            directory_page = html.parse(directory / "index.html")
            self.assertEqual(paths, {
                SITE / href.lstrip("/") for href in directory_page.xpath('//ul[@class="program-list"]/li/a/@href')
            })
            for path in sorted(paths):
                tables = html.parse(path).xpath('//table[caption="Season history"]')
                self.assertEqual(len(tables), 1, path)
                table = tables[0]
                headings = table.xpath("thead/tr/th/text()")
                self.assertIn("Best rank", headings, path)
                column = headings.index("Best rank")
                rows = table.xpath("tbody/tr")
                self.assertTrue(rows, path)
                for row in rows:
                    cell = row[column]
                    links = cell.xpath("a")
                    self.assertEqual(len(links), 1, path)
                    link = links[0]
                    href = link.get("href")
                    rank = link.text_content().strip()
                    self.assertEqual(cell.text_content().strip(), rank, path)
                    self.assertRegex(rank, r"^[1-9]\d*$", path)
                    self.assertTrue(href, path)
                    self.assertRegex(href, f"^{route}$", path)
                    self.assertTrue((SITE / href.lstrip("/")).is_file(), f"{path} links to {href}")


class CbbWebsiteParityTests(unittest.TestCase):
    def read(self, relative):
        return BeautifulSoup((SITE / relative).read_bytes(), "lxml")

    def test_public_team_history_pages_are_the_only_team_pages_with_the_sorter(self):
        script_path = b"/cfb/assets/sort-history.js"
        sorter = b'<script src="' + script_path + b'" defer></script>'
        for sport in ("cfb", "cbb"):
            teams = SITE / sport / "teams"
            self.assertNotIn(script_path, (teams / "index.html").read_bytes())
            histories = list(teams.glob("*/index.html"))
            self.assertTrue(histories, sport)
            for path in histories:
                self.assertIn(sorter, path.read_bytes(), path)
            for path in teams.glob("*/[0-9][0-9][0-9][0-9].html"):
                self.assertNotIn(script_path, path.read_bytes(), path)

    def test_cached_api_cors_seasons_have_weekly_progression_and_performance(self):
        for year in range(2020, 2028):
            base = SITE / f"cbb/seasons/{year}"
            self.assertTrue((base / "index.html").is_file(), f"missing season {year}")
            self.assertTrue((base / "weeks/0.html").is_file(), f"missing week 0 for {year}")
            self.assertTrue((base / "weeks/1.html").is_file(), f"missing week 1 for {year}")
            if year < 2027:
                self.assertTrue((base / "weeks/final.html").is_file(), f"missing final week for {year}")
            self.assertTrue((base / "progression.html").is_file(), f"missing progression.html for {year}")
            self.assertTrue((base / "progression.json").is_file(), f"missing progression.json for {year}")
            perf_dir = SITE / f"cbb/performance/{year}"
            for name in ("index.html", "weekly.csv", "games.csv", "report.json"):
                self.assertTrue((perf_dir / name).is_file(), f"missing performance/{year}/{name}")

    def test_api_seasons_have_weekly_changes_upsets_and_timed_games(self):
        for year in (2020, 2022, 2024, 2026):
            season = self.read(f"cbb/seasons/{year}/index.html")
            self.assertIn("Change since Preseason.", season.get_text())
            self.assertIsNotNone(season.select_one("#cors-upsets"))
            self.assertIsNotNone(season.select_one(f'a[href="/cbb/seasons/{year}/progression.html"]'))
            self.assertIsNotNone(season.select_one(f'a[href="/cbb/performance/{year}/index.html"]'))
            first_team_link = season.select_one('table[aria-labelledby="cors-ranking"] tbody tr th a')
            self.assertRegex(first_team_link["href"], rf"^/cbb/teams/[^/]+/{year}\.html$")

            week5 = self.read(f"cbb/seasons/{year}/weeks/5.html")
            self.assertEqual(week5.select_one('a[aria-current="page"]')["href"], f"/cbb/seasons/{year}/weeks/5.html")
            self.assertIn("Tipoff (Eastern)", [th.text for th in week5.select("table")[-1].select("thead th")])
            self.assertRegex(week5.select("table")[-1].tbody.get_text(), r"\d{1,2}:\d{2} [AP]M")

            prog = json.loads((SITE / f"cbb/seasons/{year}/progression.json").read_text(encoding="utf-8"))
            self.assertEqual(prog["season"], year)
            self.assertTrue(prog["rows"])

    def test_historical_team_season_pages_have_full_weekly_game_logs_and_season_opponent_links(self):
        page = self.read("cbb/teams/57/2020.html")
        rows = page.select("table tbody tr")
        self.assertGreater(len(rows), 20)
        opp_links = [a["href"] for a in page.select("table tbody td a")]
        self.assertTrue(opp_links)
        for href in opp_links:
            self.assertRegex(href, r"^/cbb/teams/[^/]+/2020\.html$")
            self.assertTrue((SITE / href.lstrip("/")).is_file(), href)
        self.assertRegex(page.get_text(), r"\d{1,2}:\d{2} [AP]M")

        history = self.read("cbb/teams/57/index.html")
        self.assertTrue(history.find("a", string="2019–20"))

    def test_modern_seasons_and_current_forecasts_use_season_specific_team_links(self):
        season2027 = self.read("cbb/seasons/2027/index.html")
        first_link = season2027.select_one('table[aria-labelledby="cors-ranking"] tbody tr th a')
        self.assertRegex(first_link["href"], r"^/cbb/teams/\d+/2027\.html$")
        self.assertIsNotNone(season2027.select_one('nav[aria-label="Games and forecasts"]'))

        current = self.read("cbb/current.html")
        for a in current.select('#forecasts tbody a[href^="/cbb/teams/"]'):
            self.assertRegex(a["href"], r"^/cbb/teams/\d+/2027\.html$")

    def test_performance_index_and_season_reports_match_cfb_structure(self):
        index = self.read("cbb/performance/index.html")
        self.assertIsNotNone(index.select_one(".metrics"))
        self.assertIsNotNone(index.select_one('a[href="seasons.csv"]'))
        self.assertIsNotNone(index.select_one('a[href="/cbb/performance/2027/index.html"]'))
        with (SITE / "cbb/performance/seasons.csv").open(encoding="utf-8") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual([int(r["season"]) for r in csv_rows], list(range(2020, 2028)))

        report_2026 = self.read("cbb/performance/2026/index.html")
        self.assertIsNotNone(report_2026.select_one(".metrics"))
        self.assertIsNotNone(report_2026.select_one('a[href="games.csv"]'))
        self.assertIsNotNone(report_2026.select_one('a[href="weekly.csv"]'))
        self.assertIsNotNone(report_2026.select_one('a[href="report.json"]'))
        self.assertIsNotNone(report_2026.select_one("#weekly-performance"))
        self.assertIsNotNone(report_2026.select_one("#game-results"))

    def test_honors_pages_are_sortable_and_link_conferences(self):
        for name in ("champions.html", "last-place.html", "ncaa-champions.html"):
            page = self.read(f"cbb/history/{name}")
            self.assertIsNotNone(page.select_one('script[src="/cfb/assets/sort-history.js"][defer]'), name)
        for name in ("champions.html", "last-place.html"):
            page = self.read(f"cbb/history/{name}")
            conf_links = page.select('tbody a[href^="/cbb/conferences/"]')
            self.assertEqual(len(conf_links), 7, name)
            for link in conf_links:
                self.assertTrue((SITE / link["href"].lstrip("/")).is_file(), link["href"])

    def test_big_sky_conference_is_unified_and_404_covers_both_sports(self):
        self.assertFalse((SITE / "cbb/conferences/big-sky-conference").exists())
        self.assertTrue((SITE / "cbb/conferences/big-sky/2020.html").is_file())
        self.assertTrue((SITE / "cbb/conferences/big-sky/2021.html").is_file())
        self.assertTrue((SITE / "cbb/conferences/big-sky/2027.html").is_file())

        missing = self.read("404.html")
        hrefs = {a["href"] for a in missing.select("main a[href]")}
        self.assertIn("/cbb/index.html", hrefs)
        self.assertIn("/cbb/teams/index.html", hrefs)
        self.assertIn("/cbb/seasons/index.html", hrefs)
        for href in hrefs:
            self.assertTrue((SITE / href.lstrip("/")).is_file(), href)

        home = self.read("cbb/index.html")
        self.assertIsNotNone(home.select_one('a[href="/cbb/history/ncaa-champions.html"]'))


if __name__ == "__main__":
    unittest.main()
