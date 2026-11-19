"""Offline program-page checks using the shared archive's input contract."""

from decimal import Decimal
import unittest

from bs4 import BeautifulSoup

from cfb.program_pages import build_program_pages
from cfb.reference_archive import Game, Season, Standing


TEAM = 'A&M "State" <Tech>'
FORMER = "Former State"
OPPONENT = "Visitors & Co."
ROUTES = {
    FORMER: "/cfb/teams/former-state/index.html",
    TEAM: "/cfb/teams/am-stable-slug/index.html",
}
TEAM_PATH = "cfb/teams/am-stable-slug/"


def standing(school, rank, rating="17.25", record="1-0"):
    return Standing(school, rank, Decimal(rating), record, "A&B")


def rendered_pages():
    seasons = [
        Season(1903, {"FINAL": [standing(TEAM, 9)]}, []),
        Season(1905, {
            "W2": [standing(TEAM, 2)],
            "FINAL": [standing(TEAM, 13, "8.630", "8-3"), standing(FORMER, 20)],
        }, [Game(19, TEAM, OPPONENT, 10, 10, None)]),
        Season(1904, {
            "W1": [standing(TEAM, 4)],
            "FINAL": [standing(FORMER, 16)],
        }, []),
        Season(1902, {"FINAL": [standing(TEAM, 10)]}, []),
        Season(2026, {
            "PRESEASON": [standing(TEAM, 6), standing(FORMER, 14)],
            "W0": [standing(TEAM, 8)],
            "W1": [standing(TEAM, 12)],
            "W2": [standing(TEAM, 15, "-2.40", "2-1")],
            "W3": [],
        }, [
            Game(1, TEAM, FORMER, 31, 17, Decimal("-7.25")),
            Game(1, OPPONENT, TEAM, 21, 0, Decimal("-3.50")),
            Game(2, TEAM, OPPONENT, 7, 6, Decimal("-0.0")),
            Game(3, OPPONENT, TEAM, None, None, Decimal("6")),
        ]),
    ]
    return {
        path: BeautifulSoup(html, "html5lib") for path, html in build_program_pages(seasons, ROUTES)
    }


def cell_text(row):
    return [cell.get_text(strip=True) for cell in row.find_all(["th", "td"], recursive=False)]


class ProgramPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = rendered_pages()

    def test_directory_and_season_routes_cover_archived_ranked_programs(self):
        directory = self.pages["cfb/teams/index.html"]
        links = directory.select("main li a")
        self.assertEqual([(link.text, link["href"]) for link in links], [
            (TEAM, ROUTES[TEAM]), (FORMER, ROUTES[FORMER]),
        ])
        self.assertEqual(set(self.pages), {
            "cfb/teams/index.html",
            *(TEAM_PATH + name for name in (
                "index.html", "1902.html", "1903.html", "1904.html", "1905.html", "2026.html"
            )),
            *("cfb/teams/former-state/" + name for name in (
                "index.html", "1904.html", "1905.html", "2026.html"
            )),
        })
        for page in self.pages.values():
            self.assertEqual(page.title.text.split(" | ")[1], "SportsRank")
            self.assertEqual(page.link["href"], "/cfb/assets/record-book.css")
            self.assertIsNotNone(page.select_one('meta[name="viewport"]'))
            self.assertIsNone(page.script)

    def test_history_uses_final_or_latest_checkpoint_and_keeps_rank_gaps(self):
        page = self.pages[TEAM_PATH + "index.html"]
        self.assertEqual(page.select_one("h1 span[data-program]")["data-program"], TEAM)
        rows = [cell_text(row) for row in page.select("table tbody tr")]
        self.assertEqual(rows, [
            ["2026", "Through Week 2", "15", "-2.40", "6", "2-1", "A&B"],
            ["1905", "Final", "13", "8.630", "2", "8-3", "A&B"],
            ["1904", "Final", "—", "—", "4", "—", "—"],
            ["1903", "Final", "9", "17.25", "9", "1-0", "A&B"],
            ["1902", "Final", "10", "17.25", "10", "1-0", "A&B"],
        ])
        page_years = [link["href"] for link in page.select("table tbody th a")]
        self.assertEqual(page_years, [
            "/" + TEAM_PATH + str(year) + ".html" for year in (2026, 1905, 1904, 1903, 1902)
        ])
        former_history = self.pages[ROUTES[FORMER].lstrip("/")]
        self.assertEqual(
            cell_text(former_history.select_one("table tbody tr")),
            ["2026", "Through Week 2", "—", "—", "14", "—", "—"],
        )

        trend = page.select_one('svg[role="group"]')
        self.assertEqual(page.find(id=trend["aria-labelledby"]).text, "Final national rank")
        observations = trend.select("circle")
        self.assertEqual(len(observations), 3)
        for dot, year, rank in zip(observations, (1902, 1903, 1905), (10, 9, 13), strict=True):
            link = dot.find_parent("a")
            self.assertEqual(link["aria-label"], f"{year} season, final rank {rank}")
            self.assertEqual(link["href"], f"/{TEAM_PATH}{year}.html")
            self.assertEqual(self.pages[link["href"].lstrip("/")].h1.text, f"{year} {TEAM}")
        first, second, third = observations
        self.assertLess(float(second["cy"]), float(first["cy"]))
        self.assertLess(float(first["cy"]), float(third["cy"]))
        segment, = trend.select("g[stroke] line")
        self.assertEqual((segment["x1"], segment["x2"]), (first["cx"], second["cx"]))
        self.assertAlmostEqual(
            float(third["cx"]) - float(second["cx"]),
            2 * (float(second["cx"]) - float(first["cx"])),
            places=1,
        )

    def test_games_keep_multiple_matchups_bye_and_pending_forecast_in_order(self):
        page = self.pages[TEAM_PATH + "2026.html"]
        headings = page.select("main [data-program]")
        self.assertEqual([(tag.text, tag["data-program"]) for tag in headings], [(TEAM, TEAM)])
        season_nav = {link["href"] for link in page.select("nav a")}
        self.assertTrue({ROUTES[TEAM], "/cfb/years/2026/2026_CFB.html"}.issubset(season_nav))
        table = page.select_one('div.table-scroll[tabindex="0"] table')
        spread, score = table.select("thead th")[-2:]
        self.assertEqual(spread.get_text(" ", strip=True), "CORS spread Negative if favored")
        self.assertEqual(score.get_text(" ", strip=True), f"Score {TEAM} first")
        self.assertEqual([cell_text(row) for row in table.select("tbody tr")], [
            ["Preseason", "6", "17.25", "1-0", "—", "—", "—", "—"],
            ["Week 0", "8", "17.25", "1-0", "No game listed", "—", "—", "—"],
            ["Week 1", "12", "17.25", "1-0", FORMER, "Home", "-7.25", "31–17"],
            [OPPONENT, "Away", "+3.50", "0–21"],
            ["Week 2", "15", "-2.40", "2-1", OPPONENT, "Home", "0.0", "7–6"],
            ["Week 3", "—", "—", "—", OPPONENT, "Away", "-6", "—"],
        ])
        week_one = table.find("th", string="Week 1")
        self.assertEqual(week_one["rowspan"], "2")
        self.assertEqual(week_one.find_parent("tbody").a["href"], "/cfb/teams/former-state/2026.html")
        self.assertEqual([a.get_text() for a in table.select("tbody td a")], [FORMER])

    def test_postseason_game_without_checkpoint_precedes_final(self):
        page = self.pages[TEAM_PATH + "1905.html"]
        self.assertEqual([cell_text(row) for row in page.select("table tbody tr")], [
            ["Week 2", "2", "17.25", "1-0", "No game listed", "—", "—", "—"],
            ["Week 19", "—", "—", "—", OPPONENT, "Home", "—", "10–10"],
            ["Final", "13", "8.630", "8-3", "—", "—", "—", "—"],
        ])


if __name__ == "__main__":
    unittest.main()
