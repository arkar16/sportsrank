from datetime import datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cbb.team_schedule import remaining_schedules


class TeamScheduleTest(unittest.TestCase):
    def test_fixture_identity_weekly_pick_and_later_projections(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "history.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                    CREATE TABLE teams(season, id, raw_json);
                    CREATE TABLE games(season, id, raw_json);
                    INSERT INTO teams VALUES (2027,1,'{"sourceId":"11"}'),(2027,2,'{"sourceId":"22"}');
                    INSERT INTO games VALUES (2027,51,'{"sourceId":"101"}'),(2027,52,'{"sourceId":"104"}');
                """)
            folder = root / "2027"
            folder.mkdir()
            (folder / "2026-11-23.csv").write_text(
                "event_id,tipoff,time_known,neutral,home_id,home_name,away_id,away_name\n"
                "101,2026-11-24T22:00Z,1,1,11,A,22,B\n"
                "102,2027-03-02T02:00Z,1,0,22,B,11,A\n"
                "103,2027-03-06T05:00Z,0,0,11,A,33,Small College\n"
                "104,2027-03-06T21:00Z,1,0,11,A,22,B\n"
                "105,2026-11-23T12:00Z,1,0,11,A,22,B\n")
            cutoff = "2026-11-23T11:00:00Z"
            checkpoint = {"cutoff_utc": cutoff, "rows": [
                {"team_id": 1, "rank": 1, "school": "A", "cors": 14.5},
                {"team_id": 2, "rank": 2, "school": "B", "cors": 10.0}]}
            game = {"game_id": 51, "cutoff_utc": cutoff, "home_team_id": 1, "away_team_id": 2,
                    "neutral_site": True, "home_margin": 4.49, "status": "scheduled"}
            final = {**game, "game_id": 52, "status": "final", "start_utc": "2026-11-20T12:00Z"}
            season = {"season": 2027, "forecasts": [game, final], "home_advantages": {1: 2.5, 2: 3.25}}
            def render(stamp):
                return remaining_schedules(db, season, checkpoint, datetime.fromisoformat(stamp), snapshots=root)
            self.assertEqual(render("2026-11-22T21:00Z"), ({}, set()))
            sections, moved = render("2026-11-23T16:00Z")
            self.assertEqual(moved, {51})
            def rows(team):
                page = BeautifulSoup(sections[2027, team], "html.parser")
                return [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                        for row in page.select("tbody tr")]
            self.assertEqual(rows("A"), [
                ["Tue, Nov 24", "5:00 PM ET", "#2 B", "Neutral", "-4.49"],
                ["Mon, Mar 01", "9:00 PM ET", "#2 B", "Away", "-1.25"],
                ["Sat, Mar 06", "Time TBD", "Small College", "Home", "—"]])
            self.assertEqual([row[-2:] for row in rows("B")], [["Neutral", "+4.49"], ["Home", "+1.25"]])
            self.assertIn('href="/cbb/teams/2/2027.html"', sections[2027, "A"])
