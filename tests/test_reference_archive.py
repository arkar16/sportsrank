import json
from decimal import Decimal
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lxml import html

from cfb.program_pages import build_program_pages
from cfb.reference_archive import program_routes, read_archive
from cfb.reference_markup import PagePresentation, source_bytes


def table(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "".join(f"<th>{value}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>" for row in rows)
    path.write_text(f"<html><head></head><body><table><tr>{head}</tr>{body}</table></body></html>")


class ArchiveTests(unittest.TestCase):
    def test_game_log_uses_saved_forecast_and_keeps_unranked_opponents(self):
        site = Path(self.enterContext(TemporaryDirectory()))
        season = site / "cfb/years/2026"
        table(season / "rankings/2026_W1_FBS_cors.html",
              ["rank", "school", "record", "cors", "conference"],
              [(1, "Miami", "1-0", "15.27", "ACC"), (2, "Miami (OH)", "0-1", "9.00", "MAC")])
        table(season / "data/results/2026_FBS_results.html",
              ["week", "home_team", "away_team", "home_score", "away_score", "neutral_site"],
              [(1, "Miami", "Miami (OH)", 27, 23, "True"), (0, "Miami", "Town College", 21, 0, "False")])
        table(season / "spread/2026_W1_FBS_spread.html",
              ["week", "home_team", "away_team", "spread", "neutral_site"],
              [(1, "Miami", "Miami (OH)", "Miami -3.0", "True")])
        table(season / "data/slate/weekly_slate/2026_W2_FBS_slate.html",
              ["week", "home_team", "away_team", "neutral_site", "game_date"],
              [(2, "Town College", "Miami", "False", "2026-09-12")])
        report = site / "cfb/performance/2026/report.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({"games": [
            {"week": 1, "home": "Miami", "away": "Miami (OH)", "home_score": 27, "away_score": 23,
             "neutral_site": True, "home_handicap": "-7.89", "game_date": "2026-09-06 01:00:00"}
        ], "upcoming": [
            {"week": 2, "home": "Town College", "away": "Miami", "home_handicap": "2.5", "neutral_site": False}
        ]}))
        seasons = read_archive(site)
        routes = program_routes(seasons)
        self.assertEqual(routes, {
            "Miami": "/cfb/teams/miami/index.html", "Miami (OH)": "/cfb/teams/miami-oh/index.html"
        })
        games = {game.week: game for game in seasons[0].games}
        self.assertEqual(games[1].home_handicap, Decimal("-3.0"))
        self.assertTrue(games[1].neutral_site)
        self.assertEqual((games[0].away, games[0].away_score, games[0].home_handicap), ("Town College", 0, None))
        self.assertIsNone(games[2].home_score)
        self.assertEqual(len(seasons[0].games), 3)
        self.assertEqual([games[w].start_utc for w in range(3)], [
            "", "2026-09-06T01:00:00+00:00", "2026-09-12T04:00:00+00:00",
        ])
        mark = "/cfb/assets/program-marks/town.png"
        pages = dict(build_program_pages(seasons, routes))
        miami_mark = "/cfb/assets/program-marks/miami-oh.png"
        view = PagePresentation(routes, {"Town College": mark, "Miami (OH)": miami_mark})
        log = html.fromstring(view.render(pages["cfb/teams/miami/2026.html"].encode(), archive=False))
        opponents = log.xpath('//td[normalize-space(.)="Town College"]')
        self.assertEqual(len(opponents), 2)
        self.assertEqual(opponents[0].xpath('.//img/@src'), [mark])
        self.assertEqual(opponents[1].xpath('.//img/@src'), [mark])
        self.assertFalse(any(cell.xpath('.//a') for cell in opponents))
        self.assertEqual(log.xpath('//a[@href="/cfb/teams/miami-oh/2026.html"]/img/@src'), [miami_mark])

    def test_presentation_round_trip_preserves_archival_bytes_and_numeric_values(self):
        routes = {"San José State": "/cfb/teams/san-jose-state/index.html"}
        marks = {"San José State": "/cfb/assets/program-marks/sjsu.png"}
        view = PagePresentation(routes, marks)
        original = ("<html>\r\n<head><title>Game</title></head><body></body></html>"
                    "<table><tr><td>San José State</td><td>+3.00</td></tr></table>").encode("latin-1")
        rendered = view.render(original, archive=True)
        self.assertIn(b'href="/cfb/teams/san-jose-state/index.html"', rendered)
        self.assertIn(b'alt=""', rendered)
        self.assertEqual(source_bytes(rendered), original)
        self.assertEqual(view.render(rendered, archive=True), rendered)
