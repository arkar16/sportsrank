from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cbb.conference_rebuild import _retained_results, _retained_schedule
from cbb.record_book import build
from cbb.replay import _replay
from cbb.source import Game, Season, Team
from cbb.team_schedule import schedule_views
from cfb.reference_archive import Standing


class BasketballConferenceFixturesTest(unittest.TestCase):
    def test_record_book_preserves_historical_date_and_masks_scores_after_cutoff(self):
        teams = {1: Team(1, "A", "East"), 2: Team(2, "B", "West")}
        old = _replay(Season(1982, teams, (
            Game(1, datetime.fromisoformat("1981-11-05T17:00Z"), 1, 2, False, "final", (60, 74)),
        )), (), 2., 1., 4.)
        old = {**old, "date_only": True, "forecasts": [{**old["forecasts"][0], "date_only": True}]}
        modern = _replay(Season(2027, teams, (
            Game(2, datetime.fromisoformat("2026-11-22T03:00Z"), 1, 2, False, "final", (80, 70)),
            Game(3, datetime.fromisoformat("2026-11-24T03:00Z"), 1, 2, False, "final", (70, 80)),
        )), (), 2., 1., 4.)
        with TemporaryDirectory() as temp, patch("cbb.record_book.build_conference_pages", return_value=[]) as pages:
            build(Path(temp), [modern], datetime.fromisoformat("2026-11-25T12:00Z"), set(),
                  historical_seasons=[old])
        historical, current = pages.call_args.args[0]
        self.assertIn("FINAL", historical.rankings)
        self.assertEqual(current.updated_at, "2026-11-23T11:00:00+00:00")
        self.assertEqual([(g.start_utc, g.tipoff_label, g.home_score, g.away_score) for g in historical.games], [
            ("1981-11-05T05:00:00+00:00", "Nov 05, 1981", 60, 74),
        ])
        self.assertEqual([(g.start_utc, g.tipoff_label, g.home_score, g.away_score) for g in current.games], [
            ("2026-11-22T03:00:00+00:00", "Nov 21, 2026 · 10:00 PM ET", 80, 70),
            ("2026-11-24T03:00:00+00:00", "Nov 23, 2026 · 10:00 PM ET", None, None),
        ])
        self.assertIsNone(current.games[-1].home_handicap)

    def test_dated_schedule_keeps_unpriced_fixtures_and_precedes_weekly_duplicate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "history.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                    CREATE TABLE teams(season, id, raw_json);
                    CREATE TABLE games(season, id, raw_json);
                    INSERT INTO teams VALUES (2027,1,'{"sourceId":"11"}'),(2027,2,'{"sourceId":"22"}');
                """)
            (root / "2027").mkdir()
            (root / "2027/2026-11-23.csv").write_text(
                "event_id,tipoff,time_known,neutral,home_id,home_name,away_id,away_name\n"
                "101,2026-11-26T02:00Z,1,0,11,A,22,B\n"
                "102,2027-01-02T17:00Z,0,0,11,A,33,Small College\n"
            )
            cutoff = "2026-11-23T11:00:00+00:00"
            checkpoint = {"cutoff_utc": cutoff, "week": 3, "rows": [
                {"team_id": 1, "rank": 1, "school": "A", "cors": 10.},
                {"team_id": 2, "rank": 2, "school": "B", "cors": 9.},
            ]}
            weekly = {"game_id": 51, "cutoff_utc": cutoff, "home_team_id": 1, "away_team_id": 2,
                      "neutral_site": False, "home_margin": 10, "status": "scheduled", "start_utc": "2026-11-26T03:00Z"}
            season = {"season": 2027, "forecasts": [weekly], "home_advantages": {1: 2, 2: 2}}
            schedules, _, fixtures = schedule_views(db, season, checkpoint, datetime.fromisoformat("2026-11-24T19:30Z"),
                                                    snapshots=root)
        self.assertEqual([(g.away, g.start_utc, g.tipoff_label, g.home_handicap) for g in fixtures], [
            ("B", "2026-11-26T02:00:00+00:00", "Nov 25, 2026 · 9:00 PM ET", Decimal("-3.0")),
            ("Small College", "2027-01-02T05:00:00+00:00", "Jan 02, 2027", None),
        ])
        self.assertEqual([row.game.tipoff_label for row in schedules[2027, "A"].fixtures], ["9:00 PM ET", "Time TBD"])

    def test_retained_schedule_merges_mirrored_rows_and_slate_with_honest_dates(self):
        routes = {name: f"/cbb/teams/{name}/index.html" for name in ("A", "B")}
        standings = [Standing(name, rank, Decimal(0), "0–0", league)
                     for rank, (name, league) in enumerate((("A", "East"), ("B", "West")), start=1)]
        with TemporaryDirectory() as temp:
            site = Path(temp)
            (site / "teams/A").mkdir(parents=True)
            (site / "teams/B").mkdir()
            (site / "teams/A/2027.html").write_text(
                '<meta charset="utf-8"><table><tbody><tr id="remaining-schedule">'
                '<th>Wed, Nov 25<br><small>9:00 PM ET</small></th>'
                '<td>—</td><td>—</td><td>—</td><td><a href="/cbb/teams/B/2027.html">B</a></td>'
                '<td>Neutral</td><td>-2.00</td><td>55%</td><td>—</td></tr></tbody>'
                '<tbody><tr><th>Sat, Jan 02<br><small>Time TBD</small></th>'
                '<td>—</td><td>—</td><td>—</td><td>Small College</td><td>Home</td><td>—</td><td>—</td><td>—</td>'
                '</tr></tbody></table>'
            )
            (site / "teams/B/2027.html").write_text(
                '<section id="remaining-schedule"><table><tbody><tr>'
                '<th>Wed, Nov 25</th><td>9:00 PM ET</td><td><a href="/cbb/teams/A/2027.html">A</a></td>'
                '<td>Neutral</td><td>+2.00</td></tr></tbody></table></section>'
            )
            week = site / "week.html"
            week.write_text(
                '<meta charset="utf-8"><main><table><thead><tr><th>Reconstructed pick</th></tr></thead><tbody>'
                '<tr><td>Nov 25, 2026 · 10:00 PM</td><td>A</td><td>B</td><td>Neutral</td><td>B by 8</td><td>—</td></tr>'
                '<tr><td>Nov 23, 2026 · 09:00 PM</td><td>B</td><td>A</td><td>Home</td><td>Pick’em</td><td>81–79</td></tr>'
                '</tbody></table></main>'
            )
            fixtures = _retained_schedule(site, 2027, standings, routes, week, {})
        chronological = sorted(fixtures, key=lambda game: game.start_utc)
        self.assertEqual([(g.home, g.away, g.start_utc, g.tipoff_label, g.home_handicap) for g in chronological], [
            ("A", "B", "2026-11-24T02:00:00+00:00", "Nov 23, 2026 · 9:00 PM ET", Decimal(0)),
            ("A", "B", "2026-11-26T02:00:00+00:00", "Nov 25, 2026 · 9:00 PM ET", Decimal("-2.00")),
            ("A", "Small College", "2027-01-02T05:00:00+00:00", "Jan 02, 2027", None),
        ])
        self.assertTrue(all(g.home_score is None and g.away_score is None for g in fixtures))

    def test_retained_results_keep_known_tipoff_and_reconcile_checkpoint_once(self):
        standings = [Standing("A", 1, Decimal(1), "1–0", "East"), Standing("B", 2, Decimal(0), "0–1", "West")]
        with TemporaryDirectory() as temp:
            site = Path(temp)
            folder = site / "performance/2027"
            folder.mkdir(parents=True)
            (folder / "games.csv").write_text(
                "week,start_utc,home,away,home_score,away_score,home_handicap,neutral_site,status\n"
                "2,2026-11-22T03:00Z,A,B,80,75,-2.00,False,Evaluated\n"
                "2,2026-11-21T22:00-05:00,A,B,80,75,-2.00,False,Evaluated\n"
                "3,2026-11-24T03:00Z,A,B,73,82,-3.00,False,Evaluated\n"
                "3,2026-11-25T03:00Z,A,B,,,-3.00,False,Pending\n"
            )
            game, = _retained_results(site, 2027, datetime.fromisoformat("2026-11-23T11:00Z"), standings, {})
        self.assertEqual((game.start_utc, game.tipoff_label), ("2026-11-22T03:00:00+00:00", "Nov 21, 2026 · 10:00 PM ET"))
        self.assertEqual((game.home_score, game.away_score), (80, 75))
