from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from bs4 import BeautifulSoup

from cbb.conference_rebuild import _upcoming_rows
from cbb.team_schedule import schedule_views
from cfb.conference_pages import build_conference_pages, projected_overall_records
from cfb.program_pages import build_program_pages
from cfb.reference_archive import Game, Season, Standing


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
                    INSERT INTO games VALUES (2027,51,'{"sourceId":"101"}'),
                        (2027,52,'{"sourceId":"104"}'),(2027,53,'{"sourceId":"106"}');
                """)
            folder = root / "2027"
            folder.mkdir()
            (folder / "2026-11-23.csv").write_text(
                "event_id,tipoff,time_known,neutral,home_id,home_name,away_id,away_name\n"
                "101,2026-11-24T22:00Z,1,1,11,A,22,B\n"
                "102,2027-03-02T02:00Z,1,0,22,B,11,A\n"
                "103,2027-03-06T05:00Z,0,0,11,A,33,Small College\n"
                "104,2027-03-06T21:00Z,1,0,11,A,22,B\n"
                "105,2026-11-24T12:00Z,1,0,11,A,22,B\n"
                "106,2027-03-07T21:00Z,1,0,11,A,22,B\n")
            cutoff = "2026-11-23T11:00:00Z"
            checkpoint = {"cutoff_utc": cutoff, "rows": [
                {"team_id": 1, "rank": 1, "school": "A", "cors": 14.5},
                {"team_id": 2, "rank": 2, "school": "B", "cors": 10.0}]}
            game = {"game_id": 51, "cutoff_utc": cutoff, "home_team_id": 1, "away_team_id": 2,
                    "neutral_site": True, "home_margin": 4.49, "status": "scheduled", "start_utc": "2026-11-24T22:00Z"}
            final = {**game, "game_id": 52, "status": "final", "start_utc": "2026-11-20T12:00Z"}
            pick_em = {**game, "game_id": 53, "neutral_site": False, "home_margin": 0,
                       "season_type": "postseason", "start_utc": "2027-03-07T21:00Z"}
            weekly_only = {**game, "game_id": 54, "start_utc": "2026-11-23T15:00Z", "home_margin": 0}
            season = {"season": 2027, "forecasts": [game, final, pick_em, weekly_only],
                      "home_advantages": {1: 2.5, 2: 3.25}}

            def render(stamp):
                return schedule_views(db, season, checkpoint, datetime.fromisoformat(stamp), snapshots=root)

            self.assertEqual(render("2026-11-22T21:00Z"), ({}, set(), []))
            schedules, moved, projected_games = render("2026-11-24T16:00Z")
            self.assertEqual(moved, {51, 53})
            rankings = {"W3": [
                Standing("A", 1, Decimal("14.50"), "2–1", "Test"),
                Standing("B", 2, Decimal("10.00"), "1–2", "Test"),
            ]}
            past_games = [
                Game(3, "A", "B", 80, 78, Decimal("-1.5"), tipoff_label="Nov 22 · 7:00 PM ET"),
                Game(3, "A", "B", None, None, Decimal("0"), neutral_site=True, tipoff_label="Nov 23 · 10:00 AM ET"),
                Game(3, "A", "B", None, None, Decimal("-7.0"), tipoff_label="Nov 24 · 7:00 AM ET"),
            ]
            program = Season(2027, rankings, past_games)
            conference = Season(2027, rankings, [past_games[0], *projected_games])
            routes = {name: f"/cbb/teams/{team_id}/index.html" for team_id, name in enumerate("AB", 1)}
            pages = dict(build_program_pages(
                [program], routes, sport="basketball", remaining_schedules=schedules,
                projections=projected_overall_records(conference, sport="basketball"),
            ))
            conference_pages = dict(build_conference_pages([conference], routes, sport="basketball"))
            conference_page = BeautifulSoup(conference_pages["cbb/conferences/test/2027.html"], "html5lib")

            def rows(team):
                href = routes[team].replace("index", "2027")
                page = BeautifulSoup(pages[href.lstrip("/")], "html5lib")
                table, = page.select("main table")
                self.assertEqual([th.get_text(" ", strip=True) for th in table.select("thead th")][-2:],
                                 ["Win chance", f"Score {team} first"])
                self.assertEqual([row.select("td")[-1].text for row in table.select("tbody tr")[:3]],
                                 ["80–78" if team == "A" else "78–80", "—", "—"])
                anchor, = page.select("#remaining-schedule")
                self.assertIs(anchor, table.select("tbody tr")[3])
                self.assertIsNotNone(page.select_one('a[href="#remaining-schedule"]'))
                self.assertEqual(anchor.a["href"], routes["B" if team == "A" else "A"].replace("index", "2027"))
                footer = table.parent.find_next_sibling()
                self.assertEqual(footer["id"], "projected-season-record")
                conference_row = conference_page.find("a", href=href).find_parent("tr")
                self.assertEqual(footer.strong.text, conference_row.select("td")[-1].text)
                # Include both elapsed forecasts, including the one absent from the dated schedule.
                # Exclude Small College and the future postseason pick'em.
                self.assertEqual(footer.strong.text, "4.5–2.5" if team == "A" else "2.5–4.5")
                upcoming = table.select("tbody tr")[3:]
                self.assertEqual({row.select("td")[-1].text for row in upcoming}, {"—"})
                published = root / "published.html"
                published.write_text(pages[href.lstrip("/")])
                self.assertEqual([day.text for day, *_ in _upcoming_rows(published)],
                                 [row.th.contents[0] for row in upcoming])
                return [[row.th.get_text(" ", strip=True),
                         *(cell.get_text(" ", strip=True) for cell in row.select("td")[-5:-1])]
                        for row in upcoming]

            self.assertEqual(rows("A"), [
                ["Tue, Nov 24 5:00 PM ET", "#2 B", "Neutral", "-4.49", "67%"],
                ["Mon, Mar 01 9:00 PM ET", "#2 B", "Away", "-1.25", "55%"],
                ["Sat, Mar 06 Time TBD", "Small College", "Home", "—", "—"],
                ["Sun, Mar 07 4:00 PM ET", "#2 B", "Home", "Pick’em", "50%"]])
            self.assertEqual([row[-3:] for row in rows("B")], [
                ["Neutral", "+4.49", "33%"], ["Home", "+1.25", "45%"], ["Away", "Pick’em", "50%"]])

    def test_retained_conference_reader_accepts_older_separate_schedule_tables(self):
        with TemporaryDirectory() as tmp:
            page = Path(tmp) / "team.html"
            row = ('<th>Tue, Nov 24</th><td>5:00 PM ET</td><td><a href="/cbb/teams/2/2027.html">B</a></td>'
                   '<td>Neutral</td><td>-4.49</td>')
            for chance in ("", "<td>67%</td>"):
                page.write_text(f'<section id="remaining-schedule"><table><tbody><tr>{row}{chance}</tr>'
                                '</tbody></table></section>')
                self.assertEqual([[cell.text_content() for cell in fixture] for fixture in _upcoming_rows(page)],
                                 [["Tue, Nov 24", "B", "Neutral", "-4.49"]])
