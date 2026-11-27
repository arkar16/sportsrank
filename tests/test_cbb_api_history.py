"""Exercise the API-only public archive from independent source responses."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
import zlib

from cbb.replay import replay_seasons
from cbb.cors_v1 import cors_v1_seasons
from cbb.game_excitement import weekly_scores
from cbb.api_history import HOST, materialize
from cbb.public_tree import install_pages, record_artifacts, restore_artifacts


def insert_reply(db, endpoint, rows, *, host=HOST):
    url = f"https://{host}/{endpoint}"
    db.execute("INSERT INTO responses VALUES(?,?,?,?)", (url, "2026-11-19", len(rows),
                                                         zlib.compress(json.dumps(rows).encode())))


def game(gid, day, home_points, away_points):
    return dict(id=gid, season=1949, sourceId=None, status="final", homeTeamId=1, awayTeamId=2,
                startDate=f"1949-01-{day}T18:00:00Z", homeTeam="East", awayTeam="West", neutralSite=True,
                homePoints=home_points, awayPoints=away_points, startTimeTbd=False)


class PublicBasketballSourcesTest(unittest.TestCase):
    def make_source(self, root, games):
        path = root / "source.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE responses(url,acquired_utc,rows,compressed_json)")
            db.execute("CREATE TABLE games(id,raw_json)")
            db.execute("INSERT INTO games VALUES(37,'unprovenanced operator override')")
            insert_reply(db, "games?season=1949", games)
            insert_reply(db, "teams?season=1949", [dict(id=1,school="East"),dict(id=2,school="West")])
            insert_reply(db, "games?season=1949", [game(99, "10", 100, 0)], host=HOST + ".example")
        return path

    def test_api_games_before_1980_only_use_prior_days_and_verify_the_final(self):
        cutoff = datetime.fromisoformat("1949-04-01T00:00Z")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self.make_source(root, [game(10, "05", 55, 48), game(12, "05", 64, 80), game(11, "11", 0, 0)])
            db = root / "public.db"
            self.assertEqual(materialize(src, db, 1949), [1949])
            with sqlite3.connect(db) as public:
                self.assertEqual(list(public.execute("SELECT id,status FROM games ORDER BY id")),
                                 [(10, "final"), (11, "unverified"), (12, "final")])
            seasons = replay_seasons(db,1949,first_season=1949)
            season, = cors_v1_seasons(db,seasons,api_only=True)
            scores, _ = weekly_scores(root/"site",season,season["rankings"],cutoff)
            first, second = scores[10], scores[12]
            self.assertEqual(first.bev,second.bev)
            self.assertEqual((first.final,second.final),((55,48),(64,80)))
            self.assertTrue(all(0<=v.aev<=100 and v.tier=="final" for v in (first,second)))
            self.assertTrue((root/"site/seasons/1949/excitement/bev-v1.csv").is_file())

    def test_date_only_monday_game_cannot_see_a_result_from_that_day(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            monday = dict(game(10,"10",55,48),startTimeTbd=True,startDate="1949-01-10T00:00:00Z")
            later = game(12,"10",64,80)
            src = self.make_source(root,[monday,later])
            db=root/"api.db"
            materialize(src,db,1949)
            season,=cors_v1_seasons(db,replay_seasons(db,1949,first_season=1949),api_only=True)
            early,afternoon=season["forecasts"]
            self.assertTrue(early["date_only"])
            self.assertLess(early["cutoff_utc"],afternoon["cutoff_utc"])
            self.assertGreater(early["result_cutoff_utc"],afternoon["cutoff_utc"])
            self.assertEqual(early["home_margin"],afternoon["home_margin"])

    def test_announced_preseason_schedule_has_bev_before_opening_week(self):
        from argparse import Namespace
        from lxml import html
        from cbb.site import _build_api_site
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.db"
            with sqlite3.connect(src) as db:
                db.execute("CREATE TABLE responses(url,acquired_utc,rows,compressed_json)")
                for year in (2027,2028):
                    roster = [dict(id=1,school="East",conference="League"),dict(id=2,school="West",conference="League")]
                    insert_reply(db, f"teams?season={year}", roster)
                    row = dict(game(year,"10",65,60),season=year,startDate=f"{year-1}-11-10T21:00:00Z",
                               status="final" if year==2027 else "scheduled")
                    if year==2028:
                        row.update(homePoints=None,awayPoints=None)
                    insert_reply(db,f"games?season={year}",[row])
            db=root/"public.db"
            materialize(src,db,2028)
            site=root/"site"
            _build_api_site(db,site,Namespace(season=2028, as_of="2027-08-01T15:00:00+00:00"),2027)
            guide=html.parse(site/"seasons/2028/excitement/index.html")
            fixture,=guide.xpath('//*[@data-cbbd-fixture="2028"]')
            number, grade = fixture.text_content().split()
            self.assertTrue(0 <= float(number) <= 100)
            self.assertRegex(grade, r"^\([ABCDF][+-]?\)$")
            self.assertEqual(guide.xpath('//*[@data-cbbd-game]'),[])
            self.assertIn("2027–28",guide.xpath("//title")[0].text)

    def test_saturated_response_without_complete_splits_cannot_publish(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self.make_source(root, [game(i, "05", 55, 48) for i in range(3000)])
            with self.assertRaisesRegex(ValueError, "incomplete CBBD"):
                materialize(src, root / "public.db", 1949)

    def test_rebuild_retires_old_public_results_and_preserves_verified_editions(self):
        cutoff = datetime.fromisoformat("2026-11-20T12:00Z")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination, stage = root / "public", root / "stage"
            (stage / "seasons").mkdir(parents=True)
            (stage / "index.html").write_text("Basketball")
            (stage / "seasons/index.html").write_text("API games")
            (destination / "seasons/1893").mkdir(parents=True)
            stale = destination / "seasons/1893/index.html"
            stale.write_text("School record")
            (destination / "editions").mkdir()
            unverified = destination / "editions/2026-11-18T120000Z.html"
            unverified.write_text("unsupported rows")
            restore_artifacts(destination, stage, cutoff)
            record_artifacts(stage, cutoff)
            install_pages(stage, destination)
            self.assertFalse(stale.exists())
            self.assertFalse(unverified.exists())
            verified = destination / "editions/2026-11-19T185800Z.html"
            self.assertTrue(verified.is_file())
            restore_artifacts(destination, root / "again", cutoff)
            verified.write_text("changed forecast")
            with self.assertRaisesRegex(ValueError, "archived API evidence changed"):
                restore_artifacts(destination, root / "corrupt", cutoff)
