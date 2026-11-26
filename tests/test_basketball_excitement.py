"""Reader-visible basketball excitement at the pre-tip checkpoint and final."""
from datetime import date, datetime
from decimal import Decimal
from math import exp
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from cbb.game_excitement import PregameLedger, after_game
from cbb.halftime import HalftimePool
from cfb.excitement import BASKETBALL, QualifiedPregame, calculate_bev
from cfb.excitement_pages import ESTIMATE_NOTE, aev_label
from cfb.game_excitement import GameAppeal
from cfb.reference_archive import Game
from scripts.evaluate_basketball_excitement import passes_halftime_standard
from scripts.prepare_basketball_drama import matched_box, measure, period_totals, qualified_halftime
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
        def play(seq, period, clock, h, a, team=None, points=0):
            return (seq, period, ("1st Half", "2nd Half", "OT")[period-1],
                    clock, h, a, points > 0, points, team, 10, 11)
        events = [play(1, 1, "20:00", 0, 0), play(2, 1, "10:00", 0, 3, 11, 3),
                  play(3, 1, "0:00", 0, 3), play(4, 2, "20:00", 0, 3),
                  play(5, 2, "0:00", 3, 3, 10, 3), play(6, 3, "5:00", 3, 3),
                  play(7, 3, "0:00", 5, 3, 10, 2)]
        boxes = [(0, 3), (3, 3), (5, 3)]
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

    def test_verified_break_can_survive_missing_plays_but_conflicting_scores_cannot(self):
        event = (10, 11, 65, 72, 2)
        boxes = [(27, 31), (65, 72)]
        end = (71, 1, "1st Half", "0:00", 27, 31, False, 0, None, 10, 11, "End Period")
        start = (72, 2, "2nd Half", "19:42", 27, 33, True, 2, 11, 10, 11, "LayupShot")
        # A complete reconciled box or an explicit break verified from both halves suffices.
        self.assertEqual(qualified_halftime([], event, boxes), (27, 31))
        self.assertEqual(qualified_halftime([end, start], event, None), (27, 31))
        self.assertIsNone(qualified_halftime([end], event, None))
        self.assertIsNone(qualified_halftime([end, start], event, [(26, 31), boxes[1]]))
        wrong_scorer = (*start[:8], 10, *start[9:])
        self.assertIsNone(qualified_halftime([end, wrong_scorer], event, None))
        wrong_format = (*start[:2], "2nd Quarter", *start[3:])
        self.assertIsNone(qualified_halftime([end, wrong_format], event, boxes))
        wrong_final = (180, 2, "2nd Half", "0:00", 65, 73, False, 0, None, 10, 11, "End Game")
        self.assertIsNone(qualified_halftime([end, start, wrong_final], event, boxes))

    def test_cached_halves_match_date_programs_and_final_in_either_orientation(self):
        day = date(2020, 1, 15)
        record = day.isoformat(), (12, 18), (65, 72), ([27, 38], [31, 41])
        self.assertEqual(matched_box(record, day, (18, 12), (72, 65), 2), [(31, 27), (72, 65)])
        self.assertIsNone(matched_box(record, date(2020, 1, 16), (12, 18), (65, 72), 2))
        self.assertIsNone(matched_box(record, day, (12, 17), (65, 72), 2))
        self.assertIsNone(matched_box(record, day, (12, 18), (65, 73), 2))
        with self.assertRaisesRegex(ValueError, "conflict"):
            matched_box((*record[:3], ([27, 31], [31, 41])), day, (12, 18), (65, 72), 2)

    def test_checked_in_reference_with_neutral_site_swap_and_known_overtime(self):
        game = dict(home_team_id=12, away_team_id=18, home_points=65, away_points=72, home_margin=6,
                    status="final", start_utc="2025-02-01T20:00:00Z")
        key = "2025-02-01", frozenset(("12", "18"))
        row = dict(game_id="99", home_id="18", away_id="12", home_score="72", away_score="65",
                   half_home="31", half_away="27", periods="2", tension="")
        as_of = datetime.fromisoformat("2025-02-02T10:00Z")
        ranks = {12: 1, 18: 2}
        def score(*evidence):
            return after_game(game, "33.0", ranks, {key: evidence}, as_of)
        # Direct calculation from the frozen CSV yields two extra cutoff ties in regulation.
        regular = score(row)
        self.assertEqual((aev_label(regular), regular.support), ("55.0*", 52))
        self.assertAlmostEqual(regular.aev, 55.041353946323746)
        self.assertAlmostEqual(regular.reference_distance, 0.18402299532916355)
        overtime = score(dict(row, periods="3"))
        self.assertAlmostEqual(overtime.aev, 66.60161493453799)
        self.assertEqual(overtime.support, 50)
        for unsupported in ((dict(row, half_away=""),), (dict(row, periods=""),), (row, row)):
            self.assertIsNone(score(*unsupported).aev)
        early = datetime.fromisoformat("2025-02-01T21:00Z")
        self.assertIsNone(after_game(game, "33.0", ranks, {key: [row]}, early).aev)

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
