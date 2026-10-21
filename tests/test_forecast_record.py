"""Independent arithmetic and evidence checks for the ADR-0019 domain seam."""

from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
import unittest

from cfb.forecast_record import (
    CoverageResult,
    EvidenceRef,
    FinalScore,
    ForecastCandidate,
    ForecastContractError,
    ForecastProvenance,
    ForecastSelection,
    GameIdentity,
    GameTimingEvidence,
    PublicationReceipt,
    ScoreRevision,
    StraightUpResult,
    aggregate_grades,
    grade_forecast,
    select_graded_forecast,
)
from cfb.ranking_engine import RankingContractError, natural_matchup, spreads_for_week
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam


UTC = timezone.utc
DIGEST = "sha256:" + "a" * 64


def instant(hour: int) -> datetime:
    return datetime(2026, 9, 1, hour, tzinfo=UTC)


def evidence(label: str = "fixture") -> EvidenceRef:
    return EvidenceRef("synthetic-test", label, DIGEST)


def game(suffix: str = "A", week: int = 1) -> GameIdentity:
    return GameIdentity(
        f"2026-{suffix}", 2026, week, f"Home {suffix}", f"Away {suffix}",
        "fbs", "fbs", False,
    )


def provenance(*, margin: str = "2.24") -> ForecastProvenance:
    return ForecastProvenance(
        "WEEK_0", "through-week-0", DIGEST, DIGEST, "v0.4.0",
        "season-snapshot", "fb45258", Decimal("20.24"), Decimal("20.00"),
        Decimal("2.00"), 1, 2,
    )


def candidate(identity: GameIdentity, margin: str, *, predecessor=None, selection=None) -> ForecastCandidate:
    return ForecastCandidate.create(
        game=identity, home_margin=margin, precision=2,
        provenance=provenance(margin=margin), predecessor_version_id=predecessor,
        replacement_reason="pregame correction" if predecessor else None,
        selection=selection,
    )


def receipt(item: ForecastCandidate, hour: int, suffix: str = "1") -> PublicationReceipt:
    return PublicationReceipt(
        f"receipt-{suffix}", item.version_id, item.artifact_digest, DIGEST,
        f"attempt-{suffix}", f"verification-{suffix}", evidence(f"receipt-{suffix}"),
        provider_published_at=instant(hour),
    )


def score(identity: GameIdentity, home: int, away: int, hour: int = 20) -> FinalScore:
    return FinalScore(identity, (ScoreRevision(home, away, instant(hour), evidence("score")),))


class NaturalMatchupTests(unittest.TestCase):
    def test_natural_margin_retains_hundredths_and_opposite_handicap(self):
        result = natural_matchup("20.24", "20.00", neutral_site=False)
        self.assertEqual(result.home_margin, Decimal("2.24"))
        self.assertEqual(result.home_handicap, Decimal("-2.24"))
        self.assertEqual(result.predicted_winner, "home")
        tiny = natural_matchup("18.02", "20.00", neutral_site=False)
        self.assertEqual((tiny.home_margin, tiny.predicted_winner), (Decimal("0.02"), "home"))

    def test_invalid_or_overprecise_rating_is_rejected(self):
        for bad in (None, "nan", float("inf"), "1.001", True):
            with self.subTest(bad=bad), self.assertRaises(RankingContractError):
                natural_matchup(bad, "2.00", neutral_site=True)

    def test_spreads_default_natural_with_explicit_archive_compatibility(self):
        identity = SourceGame(1, "Home", "fbs", None, "Away", "fbs", None, False, provider_id="g1")
        snapshot = SeasonSnapshot("cfb", "FBS", 2026, (SourceTeam("Home", "X"), SourceTeam("Away", "X")), (identity,), MappingProxyType({}), "fixture")
        rankings = [{"school": "Home", "cors": 20.24}, {"school": "Away", "cors": 20.0}]
        natural = spreads_for_week(snapshot, 1, rankings)[0]
        legacy = spreads_for_week(snapshot, 1, rankings, legacy_half_point=True)[0]
        self.assertEqual((natural["spread_value"], natural["home_margin"], natural["home_handicap"], natural["predicted_winner"]), (2.24, "2.24", "-2.24", "home"))
        self.assertEqual(legacy["spread_value"], 2.0)
        self.assertEqual(legacy["home_margin"], "2.24")


class ForecastEvidenceTests(unittest.TestCase):
    def test_candidate_round_trip_and_content_identity(self):
        item = candidate(game(), "2.24")
        self.assertEqual(ForecastCandidate.from_dict(item.to_dict()), item)
        altered = item.to_dict()
        altered["forecast"] = {**altered["forecast"], "home_margin": "2.25", "home_handicap": "-2.25"}
        with self.assertRaisesRegex(ForecastContractError, "version_id"):
            ForecastCandidate.from_dict(altered)
        with self.assertRaisesRegex(ForecastContractError, "unknown fields"):
            ForecastCandidate.from_dict({**item.to_dict(), "scheduled_kickoff": "2026-09-01T12:00:00Z"})

    def test_evidence_score_grade_and_aggregate_records_round_trip(self):
        item = candidate(game(), "2.24")
        publication = receipt(item, 10)
        timing = GameTimingEvidence(item.game, evidence("actual"), actual_started_at=instant(12))
        final = score(item.game, 21, 19)
        grade = grade_forecast(item, final)
        aggregate = aggregate_grades([grade])
        self.assertEqual(PublicationReceipt.from_dict(publication.to_dict()), publication)
        self.assertEqual(GameTimingEvidence.from_dict(timing.to_dict()), timing)
        self.assertEqual(type(grade).from_dict(grade.to_dict()), grade)
        self.assertEqual(type(aggregate).from_dict(aggregate.to_dict()), aggregate)

    def test_temporal_evidence_is_separate_and_unresolved_order_omits(self):
        item = candidate(game(), "2.24")
        actual = GameTimingEvidence(item.game, evidence("actual-start"), actual_started_at=instant(12))
        self.assertEqual(select_graded_forecast([item], [receipt(item, 10)], actual), item)
        self.assertIsNone(select_graded_forecast([item], [receipt(item, 13)], actual))
        observed = GameTimingEvidence(item.game, evidence("not-started"), observed_not_started_at=instant(11))
        self.assertEqual(select_graded_forecast([item], [receipt(item, 10)], observed), item)
        self.assertIsNone(select_graded_forecast([item], [receipt(item, 12)], observed))

    def test_candidate_bytes_and_explicit_replacement_chain_are_required(self):
        first = candidate(game(), "2.24")
        second = candidate(first.game, "1.50", predecessor=first.version_id)
        timing = GameTimingEvidence(first.game, evidence("actual"), actual_started_at=instant(15))
        selected = select_graded_forecast([first, second], [receipt(first, 9, "a"), receipt(second, 10, "b")], timing)
        self.assertEqual(selected, second)
        wrong = PublicationReceipt("wrong", first.version_id, DIGEST, DIGEST, "a", "v", evidence("wrong"), provider_published_at=instant(8))
        self.assertIsNone(select_graded_forecast([first], [wrong], timing))
        sibling = candidate(first.game, "1.25", predecessor=first.version_id)
        with self.assertRaisesRegex(ForecastContractError, "sibling"):
            select_graded_forecast([first, second, sibling], [receipt(first, 8, "a"), receipt(second, 9, "b"), receipt(sibling, 10, "c")], timing)

    def test_score_correction_changes_grade_without_forecast_mutation(self):
        item = candidate(game(), "2.24")
        original = score(item.game, 21, 19)
        first_grade = grade_forecast(item, original)
        corrected = original.corrected(17, 20, instant(22), evidence("correction"))
        second_grade = grade_forecast(item, corrected)
        self.assertEqual(first_grade.absolute_error, Decimal("0.24"))
        self.assertEqual(second_grade.absolute_error, Decimal("5.24"))
        self.assertEqual(second_grade.forecast_version_id, item.version_id)
        self.assertEqual(second_grade.score_corrected_at, instant(22))
        self.assertEqual(FinalScore.from_dict(corrected.to_dict()), corrected)


class ForecastArithmeticTests(unittest.TestCase):
    def test_required_examples_a_through_e_and_game_weighted_totals(self):
        examples = [
            ("A", "2.24", 2, ForecastSelection.HOME),
            ("B", "-2", -2, ForecastSelection.AWAY),
            ("C", "-2", 3, ForecastSelection.AWAY),
            ("D", "0", -7, ForecastSelection.PICKEM),
            ("E", "0.02", 1, ForecastSelection.HOME),
        ]
        grades = []
        for suffix, predicted, actual, selection in examples:
            identity = game(suffix, 1 if suffix in "AB" else 2)
            item = candidate(identity, predicted, selection=selection)
            home = 20 + actual if actual >= 0 else 20
            away = 20 if actual >= 0 else 20 - actual
            grades.append(grade_forecast(item, score(identity, home, away)))
        self.assertEqual([row.straight_up for row in grades], [StraightUpResult.CORRECT, StraightUpResult.CORRECT, StraightUpResult.INCORRECT, StraightUpResult.UNGRADED, StraightUpResult.CORRECT])
        self.assertEqual([row.coverage for row in grades], [CoverageResult.NO_COVER, CoverageResult.PUSH, CoverageResult.NO_COVER, CoverageResult.UNGRADED, CoverageResult.COVER])
        total = aggregate_grades(grades)
        self.assertEqual(total.mae, Decimal("2.644"))
        self.assertAlmostEqual(float(total.rmse), 3.87344807633, places=10)
        self.assertEqual((total.straight_up_wins, total.straight_up_losses, total.straight_up_count), (3, 1, 4))
        self.assertEqual((total.covers, total.no_covers, total.pushes, total.coverage_count), (1, 2, 1, 3))
        self.assertEqual(total.coverage_percentage, Decimal(1) / Decimal(3))
        weekly = [aggregate_grades(grades[:2]), aggregate_grades(grades[2:])]
        self.assertNotEqual(sum((row.mae for row in weekly), Decimal(0)) / 2, total.mae)

    def test_legacy_unknown_zero_and_empty_sample(self):
        identity = game("legacy")
        item = candidate(identity, "0", selection=ForecastSelection.LEGACY_UNKNOWN)
        grade = grade_forecast(item, score(identity, 24, 17))
        self.assertEqual((grade.straight_up, grade.coverage, grade.absolute_error), (StraightUpResult.UNGRADED, CoverageResult.UNGRADED, Decimal(7)))
        total = aggregate_grades([grade])
        self.assertEqual((total.margin_count, total.unknown_selections, total.straight_up_count), (1, 1, 0))
        empty = aggregate_grades([])
        self.assertIsNone(empty.mae)
        self.assertIsNone(empty.coverage_percentage)
        self.assertEqual(empty.margin_count, 0)

    def test_duplicate_game_cannot_inflate_aggregate(self):
        item = candidate(game(), "2.24")
        one = grade_forecast(item, score(item.game, 21, 19))
        with self.assertRaisesRegex(ForecastContractError, "at most once"):
            aggregate_grades([one, one])


if __name__ == "__main__":
    unittest.main()
