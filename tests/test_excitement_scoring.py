"""Independent behavioral checks for the ADR-0021 scoring contract.

The expected values in this file are calculated from the accepted equations,
without importing implementation helpers as an oracle.  Fixtures are synthetic
and do not establish historical calibration.
"""

from __future__ import annotations

from math import exp, isclose, isfinite
import unittest

from cfb.excitement import (
    AevComponents,
    ExcitementInputError,
    NormalizedTimeline,
    QualifiedFinal,
    QualifiedMarketReference,
    QualifiedPregame,
    TimelinePoint,
    TimelineQualificationError,
    calculate_bev,
    calculate_full_aev,
    normalize_timeline,
)


def _pregame(
    home_rank: int,
    away_rank: int,
    cors_margin: float,
    market_margin: float | None = None,
    *,
    forecast_id: str = "forecast-1",
) -> QualifiedPregame:
    market = None
    if market_margin is not None:
        market = QualifiedMarketReference(market_margin, "market-v1", "market-snapshot-1")
    return QualifiedPregame(
        home_rank,
        away_rank,
        cors_margin,
        forecast_id,
        "pregame-snapshot-1",
        market,
    )


def _final(
    home_score: int,
    away_score: int,
    *,
    overtime: bool = False,
    game_id: str = "game-1",
) -> QualifiedFinal:
    return QualifiedFinal(
        game_id,
        2024,
        home_score,
        away_score,
        "final-snapshot-1",
        overtime=overtime,
    )


def _timeline(states: list[tuple[float, int, int]]) -> list[TimelinePoint]:
    return [
        TimelinePoint(home, away, sequence, minute)
        for sequence, (minute, home, away) in enumerate(states)
    ]


def _bev_expected(home_rank: int, away_rank: int, cors_margin: float, market_margin: float | None) -> float:
    quality = exp(-((home_rank + away_rank - 2) / 100))
    competitiveness = exp(-((abs(cors_margin) / 14) ** 2))
    boost = 0.0
    if market_margin is not None:
        closer = min(1.0, max(0.0, (abs(market_margin) - abs(cors_margin)) / 14))
        upset = min(1.0, max(0.0, abs(cors_margin) / 2)) if cors_margin * market_margin < 0 else 0.0
        boost = competitiveness * (3 * closer + 2 * upset)
    base = 60 * quality + 40 * competitiveness
    remaining = 100 - base
    return base + boost * remaining / (5 + remaining)


class BevScoringTests(unittest.TestCase):
    def test_canonical_bev_examples_use_independent_arithmetic(self):
        examples = (
            (1, 2, 2, None, 98.6),
            (3, 5, 3, None, 94.7),
            (1, 2, 7, None, 90.6),
            (99, 100, 2, None, 47.6),
            (18, 22, 2, None, 80.2),
            (1, 80, 28, None, 28.0),
            (5, 25, 2, 14, 86.4),
            (5, 25, -2, 14, 87.9),
            (5, 25, -28, 14, 46.1),
            (5, 25, 2, None, 84.5),
        )
        for ranks_and_margins in examples:
            home_rank, away_rank, cors_margin, market_margin, display_value = ranks_and_margins
            with self.subTest(ranks_and_margins=ranks_and_margins):
                score = calculate_bev(_pregame(home_rank, away_rank, cors_margin, market_margin))
                expected = _bev_expected(home_rank, away_rank, cors_margin, market_margin)
                self.assertAlmostEqual(score.value, expected, places=12)
                self.assertAlmostEqual(score.value, display_value, places=1)

    def test_bev_is_bounded_and_invariant_under_team_swap(self):
        for home_rank in (1, 18, 50, 100):
            for away_rank in (1, 22, 75, 100):
                for cors_margin in (-40, -2, -0.01, 0, 0.01, 2, 40):
                    for market_margin in (None, -14, 0, 14, 40):
                        with self.subTest(home_rank=home_rank, away_rank=away_rank, cors_margin=cors_margin, market_margin=market_margin):
                            original = calculate_bev(_pregame(home_rank, away_rank, cors_margin, market_margin))
                            swapped = calculate_bev(_pregame(away_rank, home_rank, -cors_margin, None if market_margin is None else -market_margin))
                            self.assertTrue(0 <= original.value <= 100)
                            self.assertTrue(isclose(original.value, swapped.value, rel_tol=0, abs_tol=1e-12))
                            self.assertTrue(isfinite(original.value))

    def test_market_absence_is_not_a_zero_line_or_a_self_reference(self):
        missing = calculate_bev(_pregame(5, 25, 2, None))
        zero_line = calculate_bev(_pregame(5, 25, 2, 0))
        self.assertEqual(missing.components.market_boost, 0.0)
        self.assertEqual(missing.evidence.market_basis, "missing")
        self.assertEqual(zero_line.evidence.market_basis, "market")
        self.assertEqual(zero_line.evidence.market_snapshot_id, "market-snapshot-1")
        self.assertNotEqual(missing.evidence.market_basis, zero_line.evidence.market_basis)

    def test_zero_and_near_zero_forecasts_have_continuous_upset_behavior(self):
        zero = calculate_bev(_pregame(5, 25, 0, -14))
        tiny_positive = calculate_bev(_pregame(5, 25, 0.001, -14))
        tiny_negative = calculate_bev(_pregame(5, 25, -0.001, -14))
        self.assertEqual(zero.components.market_boost, 3.0)
        self.assertLess(abs(tiny_positive.value - tiny_negative.value), 0.01)
        self.assertLess(abs(tiny_positive.value - zero.value), 0.01)

    def test_missing_or_nonfinite_pregame_inputs_fail_loudly(self):
        with self.assertRaises(ExcitementInputError):
            _pregame(0, 2, 2)
        with self.assertRaises(ExcitementInputError):
            _pregame(1, 2, float("nan"))


class FullAevScoringTests(unittest.TestCase):
    def test_canonical_timeline_examples(self):
        examples = (
            (
                "repeated lead changes",
                _pregame(99, 100, 2),
                _final(15, 12),
                [(0, 0, 0), (5, 3, 0), (15, 3, 6), (25, 6, 6), (35, 9, 6), (45, 9, 12), (55, 12, 12), (60, 15, 12)],
                78.7,
            ),
            (
                "routine win",
                _pregame(1, 2, 7),
                _final(28, 0),
                [(0, 0, 0), (5, 7, 0), (10, 14, 0), (20, 21, 0), (30, 28, 0), (60, 28, 0)],
                21.3,
            ),
            (
                "comfortable away upset",
                _pregame(5, 25, 2, 14),
                _final(0, 28),
                [(0, 0, 0), (5, 0, 7), (10, 0, 14), (20, 0, 21), (30, 0, 28), (60, 0, 28)],
                23.6,
            ),
            (
                "sustained close game",
                _pregame(30, 35, 3),
                _final(9, 6),
                [(0, 0, 0), (10, 3, 0), (20, 3, 3), (30, 6, 3), (40, 6, 6), (50, 9, 6), (60, 9, 6)],
                73.1,
            ),
        )
        for name, pregame, final, states, display_value in examples:
            with self.subTest(name=name):
                score = calculate_full_aev(pregame, final, _timeline(states))
                self.assertAlmostEqual(score.value, display_value, places=1)
                self.assertTrue(0 <= score.value <= 100)

    def test_first_lead_does_not_count_and_ties_preserve_last_nonzero_lead(self):
        # Home leads first (no change), ties, then away leads (one change),
        # ties again, then home leads (second change).
        states = [
            (0, 0, 0),
            (10, 7, 0),
            (20, 7, 7),
            (30, 7, 14),
            (40, 14, 14),
            (50, 21, 14),
            (60, 21, 14),
        ]
        score = calculate_full_aev(_pregame(20, 21, 1), _final(21, 14), _timeline(states))
        self.assertIsInstance(score.components, AevComponents)
        self.assertEqual(score.components.lead_changes, 2 / 3)

    def test_comeback_uses_winners_largest_observed_deficit(self):
        states = [
            (0, 0, 0),
            (10, 0, 10),
            (20, 0, 10),
            (30, 7, 10),
            (60, 21, 14),
        ]
        score = calculate_full_aev(_pregame(5, 10, 3), _final(21, 14), _timeline(states))
        self.assertEqual(score.components.comeback, 10 / 21)

    def test_exact_clock_integration_and_same_clock_duplicates(self):
        states = [(0, 0, 0), (30, 14, 0), (30, 14, 7), (60, 14, 7)]
        normalized = normalize_timeline(_timeline(states), _final(14, 7))
        # Both same-clock scoring states remain observable; the zero-length
        # interval cannot add tension, while lead/comeback evidence is retained.
        expected_tension = (30 * (1 + 3 * 15 / 60) + 30 * exp(-7 / 14) * (1 + 3 * 45 / 60)) / 150
        self.assertEqual([(point.elapsed_minute, point.home_score, point.away_score) for point in normalized.regulation], [(0, 0, 0), (30, 14, 0), (30, 14, 7), (60, 14, 7)])
        renormalized = normalize_timeline(normalized.regulation, _final(14, 7))
        self.assertEqual(renormalized, normalized)
        score = calculate_full_aev(_pregame(10, 11, 7), _final(14, 7), normalized)
        self.assertAlmostEqual(score.components.tension, expected_tension, places=12)

    def test_overtime_is_a_single_bonus_and_has_no_fabricated_clock(self):
        regulation = [(0, 0, 0), (30, 7, 7), (60, 14, 14)]
        points = _timeline(regulation) + [TimelinePoint(17, 14, 3, None, True)]
        final = _final(17, 14, overtime=True)
        normalized = normalize_timeline(points, final)
        self.assertEqual(normalized.overtime[0].elapsed_minute, None)
        score = calculate_full_aev(_pregame(1, 2, 0), final, normalized)
        self.assertEqual(score.components.overtime, 1.0)
        self.assertLessEqual(score.value, 100)

    def test_invalid_ordering_duplicates_and_final_mismatch_are_rejected(self):
        final = _final(7, 0)
        with self.assertRaises(TimelineQualificationError):
            normalize_timeline(_timeline([(0, 0, 0), (40, 7, 0), (20, 7, 0), (60, 7, 0)]), final)
        conflicting = _timeline([(0, 0, 0), (30, 7, 0), (60, 7, 0)])
        conflicting.append(TimelinePoint(6, 1, 1, 30))
        with self.assertRaises(TimelineQualificationError):
            normalize_timeline(conflicting, final)
        with self.assertRaises(TimelineQualificationError):
            normalize_timeline(_timeline([(0, 0, 0), (60, 6, 0)]), final)
        with self.assertRaises(TimelineQualificationError):
            normalize_timeline(_timeline([(0, 0, 0), (59, 7, 0)]), final)

    def test_partial_timeline_cannot_be_treated_as_full_aev(self):
        with self.assertRaises(TimelineQualificationError):
            normalize_timeline(_timeline([(0, 0, 0), (30, 7, 0)]), _final(7, 0))

    def test_calculator_revalidates_a_public_normalized_timeline(self):
        final = _final(7, 0)
        forged = NormalizedTimeline(
            regulation=tuple(_timeline([(0, 0, 0), (60, 999, 0)])),
            overtime=(),
            observed_states=((0, 0), (999, 0)),
        )
        with self.assertRaises(TimelineQualificationError):
            calculate_full_aev(_pregame(1, 2, 2), final, forged)

    def test_actual_surprise_uses_pregame_underdog_reference(self):
        market = calculate_full_aev(
            _pregame(1, 2, 2, -14),
            _final(24, 21),
            _timeline([(0, 0, 0), (60, 24, 21)]),
        )
        cors_fallback = calculate_full_aev(
            _pregame(25, 5, -7),
            _final(24, 21),
            _timeline([(0, 0, 0), (60, 24, 21)]),
        )
        self.assertEqual(market.components.surprise, 1.0)
        self.assertEqual(market.evidence.surprise_basis, "market")
        self.assertEqual(cors_fallback.components.surprise, 0.5)
        self.assertEqual(cors_fallback.evidence.surprise_basis, "cors")

    def test_actual_surprise_records_missing_basis_when_both_references_are_absent(self):
        score = calculate_full_aev(
            _pregame(1, 2, None),
            _final(24, 21),
            _timeline([(0, 0, 0), (60, 24, 21)]),
        )
        self.assertEqual(score.components.surprise, 0.0)
        self.assertEqual(score.evidence.surprise_basis, "missing")


if __name__ == "__main__":
    unittest.main()
