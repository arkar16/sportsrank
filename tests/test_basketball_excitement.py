"""Reader-visible basketball excitement at the pre-tip checkpoint and final."""
from datetime import datetime
from decimal import Decimal
from math import exp
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from cbb.game_excitement import PregameLedger, after_game
from cbb.halftime import HalftimePool, passes_halftime_standard
from cfb.excitement import BASKETBALL, QualifiedPregame, calculate_bev
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label
from cfb.game_excitement import GameAppeal
from cfb.reference_archive import Game
from cbb.source import period_evidence
from scripts.prepare_cbbd_excitement import measure
from spread_cards import Spread, Team, _document, complete_basketball_card


class BasketballExcitementTests(unittest.TestCase):
    def test_an_earlier_week_individual_pick_can_receive_its_verified_final(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = "2026-11-23"
            teams = [Team(name, "", "", "0-0") for name in ("Home", "Away")]
            forecast = Spread("cbb", day, day, "/editions/old.html", *teams, False,
                              Decimal("3.00"), "OOC", "66.2")
            target = root / forecast.path.removeprefix("/cbb/")
            target.parent.mkdir(parents=True)
            target.write_text(_document(forecast, None))
            g = Game(3, "Home", "Away", 70, 72, Decimal("-3.00"))
            estimate = GameAppeal("66.2", (70, 72), 72.5, "halftime", "99", 50, .5)
            complete_basketball_card(root, day, g, estimate)
            complete_basketball_card(root, day, g, estimate)
            self.assertIn("Game drama (AEV) <strong>72.5*", target.read_text())
            self.assertEqual(target.read_text().count(ESTIMATE_NOTE), 1)
            score = GameAppeal("66.2", (70, 72), 83.2, "full")
            complete_basketball_card(root, day, g, score)
            first = target.read_bytes()
            self.assertIn(b"Game drama (AEV) <strong>83.2", first)
            self.assertIn(b"Home by 3.00", first)
            self.assertNotIn(ESTIMATE_NOTE.encode(), first)
            complete_basketball_card(root, day, g, score)
            complete_basketball_card(root, day, g, GameAppeal("45.2", (70, 72), 81.2, "full"))
            self.assertEqual(target.read_bytes(), first)

    def test_possession_scale_side_symmetry_and_frozen_inputs(self):
        for gap in (0, 3, 6, 9, 12):
            expected = 60 * exp(-1 / 100) + 40 * exp(-(gap / 6)**2)
            for hr, ar, sign in ((1, 2, 1), (2, 1, -1)):
                p = QualifiedPregame(hr, ar, sign * gap, "g", "pre")
                self.assertAlmostEqual(calculate_bev(p, scale=BASKETBALL).value, expected)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "bev-v1.csv"
            book = PregameLedger(path)
            cutoff, tip = "2026-11-23T11:00:00Z", "2026-11-24T03:00:00Z"
            args = (cutoff, 101, tip, 1, 2, False, {1: 2, 2: 1})
            value = book.capture(*args, 6)
            book.write()
            saved = PregameLedger(path)
            self.assertEqual(saved.capture(*args, 6), value)
            with self.assertRaisesRegex(ValueError, "frozen"):
                saved.capture(*args, 3)
            with self.assertRaisesRegex(ValueError, "precede"):
                saved.capture(tip, 99, cutoff, 1, 2, False, {1: 1, 2: 2}, 0)

    def test_two_halves_timed_overtime_and_scoring_team(self):
        def play(seq, period, seconds, h, a, team=None, points=0):
            return [seq, 99, period, f"{seconds // 60}:{seconds % 60:02}", seconds,
                    h, a, team, team == 10, points > 0, points, "2024-12-01T12:00:00Z"]
        game = dict(id=99, homeTeamId=10, awayTeamId=11, homePoints=5, awayPoints=3,
                    homePeriodPoints=[0, 3, 2], awayPeriodPoints=[3, 0, 0])
        events = [play(1, 1, 1200, 0, 0), play(2, 1, 600, 0, 3, 11, 3),
                  play(3, 2, 1200, 0, 3), play(4, 2, 0, 3, 3, 10, 3),
                  play(5, 3, 300, 3, 3), play(6, 3, 0, 5, 3, 10, 2)]
        measured = measure(game, events)
        self.assertEqual((measured["periods"], measured["lead_changes"], measured["winner_deficit"]), (3, 1, 3))
        # First ten minutes tied, then 30 regulation minutes with a three-point margin.
        area = 10*(1+3*5/40) + 30*(1+3*25/40)*exp(-3/6)
        self.assertAlmostEqual(measured["tension"], area / 100)
        self.assertEqual(measure(game, [*reversed(events), events[1]]), measured)
        bad_clock = [*events[1]]
        bad_clock[3] = "11:00"
        for changed in (events[:-1], [*events[:1], play(2,1,600,0,3,10,3), *events[2:]],
                        [*events[:1], bad_clock, *events[2:]], [*events[1:3], *events[4:]]):
            self.assertIsNone(measure(game, changed))

    def test_final_cutoff_and_cbbd_game_identity(self):
        game = dict(game_id=99, home_team_id=12, away_team_id=18, home_points=53, away_points=50,
                    status="final", start_utc="2026-11-24T23:00:00Z", home_margin=-6)
        row = dict(game_id="99", home_id="12", away_id="18", home_score="53", away_score="50",
                   tension="0.5", lead_changes="3", winner_deficit="9", periods="3")
        ranks = {12: 1, 18: 2}
        expected = 40 + 8 + 4 + 2 + 3 * exp(-1/100) + 3
        early = datetime.fromisoformat("2026-11-25T04:00Z")
        after = datetime.fromisoformat("2026-11-25T06:00Z")
        self.assertIsNone(after_game(game, "85.0", ranks, {"99": row}, early).aev)
        self.assertAlmostEqual(after_game(game, "85.0", ranks, {"99": row}, after).aev, expected)
        for stale in (dict(game, away_points=49), dict(game, game_id=23)):
            self.assertEqual(after_game(stale, "85.0", ranks, {"99": row}, after).tier, "final")

    def test_halftime_uses_both_official_period_lines_and_qualifies_overtime(self):
        game = dict(homePoints=75, awayPoints=71, homePeriodPoints=[30,35,10], awayPeriodPoints=[32,33,6])
        self.assertEqual(period_evidence(game), (True,(30,32)))
        for update in ({"homePoints":74}, {"homePeriodPoints":[30,34,11]}, {"awayPeriodPoints":[32,39]},
                       {"homePeriodPoints":None}, {"homePeriodPoints":[30,46,-1]}):
            self.assertEqual(period_evidence(dict(game,**update)), (None,None))

    def test_halftime_and_final_use_frozen_official_references(self):
        import csv
        from statistics import fmean
        from cbb.halftime import DATA, policy
        game = dict(game_id=99, home_team_id=12, away_team_id=18, home_points=65, away_points=72,
                    home_margin=6, halftime=(27,31), overtime=False, status="final", start_utc="2025-02-01T20:00Z")
        ranks = {12: 1, 18: 2}
        as_of = datetime.fromisoformat("2025-02-02T10:00Z")
        ref = policy()
        self.assertEqual((ref["provider"],ref["reserved"]),("CollegeBasketballData",2025))
        with (DATA/ref["file"]).open() as stream:
            records=list(csv.DictReader(stream))
        for overtime in (False,True):
            actual=after_game(dict(game,overtime=overtime), "33.0", ranks, {}, as_of)
            values=[]
            widths=ref["widths"][str(int(overtime))]
            for r in records:
                if bool(int(r["overtime"])) != overtime or r["game"]=="99":
                    continue
                distance=sum(((v-int(r[k]))/w)**2 for k,v,w in
                             zip(("abs_margin","total","halftime_margin"),(7,137,4),widths))
                values.append((distance,float(r["drama"])))
            cutoff=sorted(v[0] for v in values)[min(49,len(values)-1)]
            neighbors=[d for distance,d in values if distance <= cutoff]
            self.assertEqual((actual.tier,actual.support),("halftime",len(neighbors)))
            self.assertAlmostEqual(actual.aev,fmean(neighbors)+2*overtime+3*exp(-.01)+3)
        self.assertEqual(after_game(dict(game,halftime=None), "33.0", ranks, {}, as_of).tier,"final")
        self.assertIsNone(after_game(game,"33.0",ranks,{},datetime.fromisoformat("2025-02-01T21:00Z")).aev)

    def test_target_is_excluded_and_all_fiftieth_place_ties_are_included(self):
        games = [[3, 120, 0]] * 50 + [[2, 120, 0], [4, 120, 0], [8, 120, 0]]
        targets = [10] * 49 + [90, 21, 40, 0]
        pool = HalftimePool({str(i): i for i in range(len(games))}, np.array(games), np.array(targets), (1, 1, 1))
        drama, support, distance = pool.estimate("49", 3, 120, 0)
        self.assertEqual((support, distance), (51, 1))
        self.assertAlmostEqual(drama, (49 * 10 + 21 + 40) / 51)

    def test_halftime_release_standard_checks_all_three_inclusive_limits(self):
        for bias in (2, -2):
            self.assertTrue(passes_halftime_standard(7, bias, .85))
        for values in ((7.001, 0, .90), (6.5, -2.01, .90), (6.5, 2.01, .90), (6.5, 0, .84999)):
            self.assertFalse(passes_halftime_standard(*values))
