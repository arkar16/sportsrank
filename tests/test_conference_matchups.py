from dataclasses import replace
from decimal import Decimal
import unittest

from lxml import html

from cfb.conference_pages import build_conference_pages
from cfb.reference_archive import Game, Season, Standing


def _standing(school, rank, conference):
    return Standing(school, rank, Decimal(0), "0–0", conference)


def _pages(seasons, sport="football"):
    prefix = "cfb" if sport == "football" else "cbb"
    schools = sorted({row.school for season in seasons for rows in season.rankings.values() for row in rows})
    routes = {name: f"/{prefix}/teams/{index}/index.html" for index, name in enumerate(schools)}
    return dict(build_conference_pages(seasons, routes, sport=sport,
                                       year_label=str if sport == "football" else lambda y: f"{y-1}–{y%100:02}"))


def _ooc_row(page, opponent):
    row, = page.xpath('//table[@aria-label="Out-of-conference record"]/tbody/tr[th=$opponent]', opponent=opponent)
    return row


def _game_rows(page):
    return [tuple(cell.text_content() for cell in row) for row in page.xpath("//tbody/tr")]


class ConferenceMatchupsTest(unittest.TestCase):
    def test_football_records_and_schedule_share_canonical_log(self):
        east, west, coast = [_standing(*args) for args in (("A", 1, "East"), ("B", 2, "West"),
                                                           ("C", 3, "Coast"))]
        upcoming = Game(3, "A", "B", None, None, None)
        games = [
            upcoming,
            Game(2, "B", "A", 10, 10, None),
            Game(2, "A", "B", None, None, None),  # Missing past result.
            Game(1, "A", "B", 21, 14, None, True, start_utc="2026-09-06T00:00:00+00:00"),
            Game(4, "B", "A", 7, 3, None),  # Result beyond W2.
            replace(upcoming, start_utc="2026-09-20T00:00:00Z"),
            Game(5, "C", "A", None, None, None, True),
            Game(2, "A", "Other & <State>", 17, 3, None, away_division="fcs"),
        ]
        previous_coast = replace(coast, conference="West")
        pages = _pages([
            Season(2025, {"FINAL": [east, west, previous_coast]}, [*games[:1], Game(3, "A", "C", 3, 7, None)]),
            Season(2026, {"PRESEASON": [east, west, previous_coast], "W2": [east, west, coast]}, games),
        ])
        east_page = html.fromstring(pages["cfb/conferences/east/2026.html"])
        west_page = html.fromstring(pages["cfb/conferences/west/2026.html"])
        row = _ooc_row(east_page, "West")
        self.assertEqual(row.xpath("./th/a/@href"), ["/cfb/conferences/west/2026.html"])
        record, = row.xpath("./td/a")
        self.assertEqual(record.text, "1–0–1")
        log = record.get("href")
        self.assertEqual(_ooc_row(west_page, "East").xpath("td/a/@href"), [log])
        self.assertEqual(_ooc_row(west_page, "East").xpath("td/a/text()"), ["0–1–1"])
        page = html.fromstring(pages[log.lstrip("/")])
        self.assertCountEqual(page.xpath("//nav/a/@href"), ["/cfb/conferences/east/2026.html",
                                                          "/cfb/conferences/west/2026.html"])
        self.assertEqual(_game_rows(page), [
            ("Sep 05, 2026 · Week 1", "B", "A", "Neutral", "14–21"),
            ("Week 2", "A", "B", "Home", "10–10"),
            ("Sep 19, 2026 · Week 3", "B", "A", "Home", "Scheduled"),
        ])
        self.assertNotIn("Scheduled", pages[log.replace("2026", "2025").lstrip("/")])

        coast_page = html.fromstring(pages["cfb/conferences/coast/2026.html"])
        future, = _ooc_row(coast_page, "East").xpath("td/a")
        self.assertEqual(future.text, "0–0")
        self.assertEqual(_ooc_row(east_page, "Coast").xpath("td/a/@href"), [future.get("href")])
        future_page = html.fromstring(pages[future.get("href").lstrip("/")])
        self.assertEqual(_game_rows(future_page), [("Week 5", "A", "C", "Neutral", "Scheduled")])
        self.assertFalse(coast_page.xpath("//tfoot"))

        fcs = _ooc_row(east_page, "FCS")
        self.assertFalse(fcs.xpath("./th/a"))
        fcs_page = html.fromstring(pages[fcs.xpath("td/a/@href")[0].lstrip("/")])
        self.assertEqual(fcs_page.xpath("//nav/a/@href"), ["/cfb/conferences/east/2026.html"])
        self.assertEqual(_game_rows(fcs_page), [("Week 2", "Other & <State>", "A", "Home", "3–17")])
        self.assertFalse(fcs_page.xpath("//state"))

    def test_basketball_orders_local_dates_and_keeps_non_division_i_schedule(self):
        east, west = _standing("A", 1, "East & <League>"), _standing("B", 2, "West")
        games = [
            Game(2, "A", "B", None, None, None, start_utc="2026-01-12T02:00:00+00:00"),
            Game(0, "A", "B", 75, 80, None, True, start_utc="2025-12-31T22:00:00Z"),
            Game(1, "B", "A", None, None, None, True, start_utc="2025-12-31T22:00:00+00:00"),
            Game(2, "Outside", "A", None, None, None, start_utc="2026-01-03T03:00:00Z"),
            Game(0, "A", "Outside", 61, 50, None, start_utc="2025-11-12T03:00:00Z"),
            Game(1, "B", "A", 56, 59, None, postseason=True, start_utc="2026-01-01T22:00:00Z"),
        ]
        pages = _pages([Season(2026, {"W2": [east, west]}, games)], "basketball")
        season_page = html.fromstring(pages["cbb/conferences/east-league/2026.html"])
        west_row = _ooc_row(season_page, "West")
        self.assertEqual(west_row.xpath("td/a/text()"), ["1–1"])
        west_log = html.fromstring(pages[west_row.xpath("td/a/@href")[0].lstrip("/")])
        self.assertEqual(west_log.xpath("//h1/text()"), ["2025–26 East & <League> vs. West"])
        self.assertEqual(_game_rows(west_log), [
            ("Dec 31, 2025", "B", "A", "Neutral", "80–75"),
            ("Jan 01, 2026", "A", "B", "Home", "59–56"),
            ("Jan 11, 2026", "B", "A", "Home", "Scheduled"),
        ])
        other_row = _ooc_row(season_page, "Non-D-I")
        self.assertEqual(other_row.xpath("td/a/text()"), ["1–0"])
        other_log = html.fromstring(pages[other_row.xpath("td/a/@href")[0].lstrip("/")])
        self.assertEqual(_game_rows(other_log), [
            ("Nov 11, 2025", "Outside", "A", "Home", "50–61"),
            ("Jan 02, 2026", "A", "Outside", "Home", "Scheduled"),
        ])

    def test_independent_pair_counts_both_results_but_lists_each_game_once(self):
        standings = [_standing("A", 1, "Indep."), _standing("B", 2, "—")]
        games = [Game(1, "A", "B", 10, 7, None), Game(2, "B", "A", 7, 7, None)]
        pages = _pages([Season(2026, {"FINAL": standings}, games)])
        source = html.fromstring(pages["cfb/conferences/indep/2026.html"])
        row = _ooc_row(source, "Indep.")
        self.assertEqual(row.xpath("td/a/text()"), ["1–1–2"])
        page = html.fromstring(pages[row.xpath("td/a/@href")[0].lstrip("/")])
        self.assertEqual(page.xpath("//nav/a/@href"), ["/cfb/conferences/indep/2026.html"])
        self.assertEqual(_game_rows(page), [
            ("Week 1", "B", "A", "Home", "7–10"),
            ("Week 2", "A", "B", "Home", "7–7"),
        ])
