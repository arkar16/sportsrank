"""Reader-visible basketball excitement at the pre-tip checkpoint and final."""
from datetime import datetime
from decimal import Decimal
from math import exp
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cbb.game_excitement import PregameLedger, after_game
from cfb.excitement import BASKETBALL, QualifiedPregame, calculate_bev
from cfb.game_excitement import GameAppeal
from cfb.reference_archive import Game
from scripts.prepare_basketball_drama import confirms_periods, measure, period_totals
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
            target.write_text(_document(forecast))
            g = Game(3, "Home", "Away", 70, 72, Decimal("-3.00"))
            score = GameAppeal("66.2", (70, 72), 83.2, "full")
            complete_basketball_card(root, day, g, score)
            first = target.read_bytes()
            self.assertIn(b"Game drama (AEV) <strong>83.2", first)
            self.assertIn(b"Home by 3.00", first)
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
        def play(seq, period, clock, h, a, team=None, points=0):
            return (seq, period, ("1st Half", "2nd Half", "OT")[period-1],
                    clock, h, a, points > 0, points, team, 10, 11)
        events = [play(1, 1, "20:00", 0, 0), play(2, 1, "10:00", 0, 3, 11, 3),
                  play(3, 1, "0:00", 0, 3), play(4, 2, "20:00", 0, 3),
                  play(5, 2, "0:00", 3, 3, 10, 3), play(6, 3, "5:00", 3, 3),
                  play(7, 3, "0:00", 5, 3, 10, 2)]
        boxes = [(0, 3), (3, 3), (5, 3)]
        self.assertTrue(confirms_periods(events, (10, 11, 5, 3, 3), boxes))
        self.assertFalse(confirms_periods(events[:-1], (10, 11, 5, 3, 3), boxes))
        self.assertFalse(confirms_periods(events, (10, 11, 5, 3, 3), [(0, 2), *boxes[1:]]))
        measured = measure(events, (10, 11, 5, 3, 3), boxes)
        self.assertEqual(measured[:2], (0, 3))
        self.assertEqual(measured[-2:], (1, 3))
        # First ten minutes tied, then 30 minutes at a three-point margin.
        area = 10*(1+3*5/40) + 30*(1+3*25/40)*exp(-3/6)
        self.assertAlmostEqual(float(measured[2]), area / 100)
        for changed in (events[:-1], [*events[:1], play(2,1,"10:00",0,3,10,3), *events[2:]],
                        [*events[:3], play(4,2,"10:00",0,3), *events[4:]]):
            with self.assertRaises(ValueError):
                measure(changed, (10, 11, 5, 3, 3), boxes)
        with self.assertRaisesRegex(ValueError, "conflict"):
            period_totals("[{'value':20.0} {'value':21.0}]", "[{'value':19.0} {'value':20.0}]", (42, 39), 2)

    def test_future_final_cutoff_and_neutral_orientation(self):
        game = dict(home_team_id=12, away_team_id=18, home_points=53, away_points=50, status="final",
                    start_utc="2026-11-24T23:00:00Z", home_margin=-6)
        evidence = {("2026-11-24",frozenset(("12","18"))):[
            dict(game_id="99", home_id="18", away_id="12", home_score="50", away_score="53",
                 tension="0.5", lead_changes="3", winner_deficit="9", periods="3")]}
        ranks = {12: 1, 18: 2}
        expected = 40 + 8 + 4 + 2 + 3*exp(-1/100) + 3
        self.assertIsNone(after_game(game, "85.0", ranks, evidence, datetime.fromisoformat("2026-11-25T04:00Z")).aev)
        after = datetime.fromisoformat("2026-11-25T06:00Z")
        self.assertAlmostEqual(after_game(game, "85.0", ranks, evidence, after).aev, expected)
        self.assertIsNone(after_game(dict(game, away_points=49), "85.0", ranks, evidence, after).aev)
        self.assertIsNone(after_game(dict(game, start_utc="2026-11-20T23:00Z"), "85.0", ranks, evidence, after).aev)
