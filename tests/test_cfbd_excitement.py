"""Verify the native scoring evidence independently of the public table renderer."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cfb.cfbd_appeal import load
from cfb.early_excitement import build_guides
from cfb.reference_archive import Game, _official_schedule
from scripts.prepare_cfbd_excitement import measure


class CollegeFootballDataEvidenceTests(unittest.TestCase):
    def test_measured_score_needs_all_four_verified_quarter_boundaries(self):
        game=dict(id="19",season=2024,home_team="H",away_team="A",h=7,a=3,
                  home_line_scores="[0,7,0,0]",away_line_scores="[0,0,0,3]",notes="")
        def snap(i,quarter,h,a,scoring):
            return dict(id=str(i),driveNumber=i,playNumber=1,period=quarter,home="H",away="A",
                        offense="H",defense="A",offenseScore=h,defenseScore=a,
                        clock="{'seconds': 0, 'minutes': 1}",scoring=scoring)
        plays=[snap(1,1,0,0,False),snap(2,2,7,0,True),snap(3,3,7,0,False),snap(4,4,7,3,True)]
        self.assertGreater(measure(game,plays)[0],0)
        self.assertIsNone(measure(game,plays[:-1]))
        self.assertIsNone(measure(dict(game,home_line_scores="[7,0,0,0]"),plays))
        self.assertIsNone(measure(game,plays[:2]+plays[3:]))

    def test_reserved_result_and_earliest_api_games(self):
        reference=load()
        self.assertEqual(reference.policy["provider"],"CollegeFootballData")
        self.assertNotIn(2025,reference.policy["training"])
        self.assertEqual(reference.policy["training_games"],1649)
        for actual,maximum in ((reference.policy["quarter"]["mae"],5),
                               (abs(reference.policy["quarter"]["bias"]),2)):
            self.assertLessEqual(actual,maximum)
        self.assertGreaterEqual(reference.policy["quarter"]["spearman"],.90)
        year,_,ledger=next(build_guides())
        self.assertEqual((year,len(ledger["games"])),(1869,2))
        self.assertEqual([(r["week"],r["checkpoint"]) for r in ledger["games"]],[(1,0),(2,1)])

    def test_live_football_fixture_uses_its_own_completed_snapshot(self):
        import json
        with TemporaryDirectory() as tmp:
            directory=Path(tmp)
            (directory/"excitement").mkdir()
            entry=dict(id="10",week=3,home="Home",away="Away",neutral=False,home_division="fbs",
                       away_division="fbs",postseason=False,start="2026-09-19",final=[7,3])
            (directory/"excitement/cfbd-schedule-v1.json").write_text(json.dumps(
                dict(provider="CollegeFootballData",year=2026,games=[entry])))
            before=Game(3,"Home","Away",None,None,None)
            updated,=_official_schedule(directory,2026,[before])
            self.assertEqual((updated.home_score,updated.away_score),(7,3))
            older=Game(3,"Home","Away",7,6,None)
            corrected,=_official_schedule(directory,2026,[older])
            self.assertEqual((corrected.home_score,corrected.away_score),(7,3))
