"""Independent SR-26 acceptance checks for forecast identity and grading.

Expected values in this file are calculated from the accepted contract rather
than by calling evaluator helpers to derive the oracle.  Fixtures are synthetic
and never become production release input.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
    receipt_qualifies,
    select_graded_forecast,
)
from cfb.forecast_publication import VerifiedForecastPublication
from cfb.publication import PublicationExecutionError
from cfb.ranking_engine import RankingContractError, natural_matchup
from cfb.forecast_release import validate_evaluation_semantics


UTC = timezone.utc


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 1, hour, minute, tzinfo=UTC)


def _evidence(name: str) -> EvidenceRef:
    # The digest is deliberately a fixture value.  These tests exercise the
    # record's binding and identity checks; no provider bytes are fetched.
    return EvidenceRef("synthetic", name, "sha256:" + (name.encode().hex() * 64)[:64])


def _game(label: str) -> GameIdentity:
    return GameIdentity(
        provider_id=f"game-{label}",
        season=2026,
        week=0,
        home_team=f"Home {label}",
        away_team=f"Away {label}",
        home_classification="FBS",
        away_classification="FBS",
        neutral_site=False,
    )


def _provenance(label: str) -> ForecastProvenance:
    return ForecastProvenance(
        rating_checkpoint="PRESEASON",
        rating_cutoff="2026-08-30T12:00:00Z",
        rating_artifact_digest="sha256:" + ("1" * 64),
        source_snapshot_digest="sha256:" + ("2" * 64),
        model_version="cors-v1",
        source_kind="synthetic-acceptance",
        code_revision=f"fixture-{label}",
        home_rating=Decimal("10.00"),
        away_rating=Decimal("8.00"),
        home_field_advantage=Decimal("0.00"),
    )


def _candidate(
    label: str,
    margin: str,
    precision: int,
    *,
    selection: ForecastSelection | None = None,
    predecessor_version_id: str | None = None,
    replacement_reason: str | None = None,
) -> ForecastCandidate:
    value = Decimal(margin)
    if selection is None:
        selection = (
            ForecastSelection.PICKEM
            if value == 0
            else ForecastSelection.HOME
            if value > 0
            else ForecastSelection.AWAY
        )
    return ForecastCandidate(
        game=_game(label),
        home_margin=value,
        home_handicap=-value,
        precision=precision,
        selection=selection,
        provenance=_provenance(label),
        predecessor_version_id=predecessor_version_id,
        replacement_reason=replacement_reason,
    )


def _score(candidate: ForecastCandidate, home: int, away: int) -> FinalScore:
    # The oracle is stated as a home scoring margin.  Convert negative
    # margins to a valid nonnegative final score while preserving that margin.
    if away == 0 and home < 0:
        home, away = 0, -home
    return FinalScore(
        game=candidate.game,
        revisions=(
            ScoreRevision(
                home,
                away,
                _at(13),
                _evidence(f"score-{candidate.game.provider_id}"),
            ),
        ),
    )


def _receipt(candidate: ForecastCandidate, *, hour: int = 10) -> PublicationReceipt:
    return PublicationReceipt(
        receipt_id=f"receipt-{candidate.game.provider_id}-{hour}",
        candidate_version_id=candidate.version_id,
        candidate_artifact_digest=candidate.artifact_digest,
        publication_artifact_digest="sha256:" + ("3" * 64),
        attempt_id=f"attempt-{candidate.game.provider_id}-{hour}",
        verification_id=f"verification-{candidate.game.provider_id}-{hour}",
        source=_evidence(f"publication-{candidate.game.provider_id}-{hour}"),
        provider_published_at=_at(hour),
    )


def _timing(candidate: ForecastCandidate, *, kickoff_hour: int = 12) -> GameTimingEvidence:
    return GameTimingEvidence(
        game=candidate.game,
        source=_evidence(f"timing-{candidate.game.provider_id}"),
        actual_started_at=_at(kickoff_hour),
    )


class ForecastNumericalAcceptanceTests(unittest.TestCase):
    def test_natural_matchup_preserves_margin_precision_and_sign(self):
        home_favored = natural_matchup("20.24", "20.00", neutral_site=False)
        self.assertEqual(home_favored.home_margin, Decimal("2.24"))
        self.assertEqual(home_favored.home_handicap, Decimal("-2.24"))
        self.assertEqual(home_favored.predicted_winner, "home")

        tiny_edge = natural_matchup("18.02", "20.00", neutral_site=False)
        self.assertEqual(tiny_edge.home_margin, Decimal("0.02"))
        self.assertEqual(tiny_edge.home_handicap, Decimal("-0.02"))
        self.assertEqual(tiny_edge.predicted_winner, "home")

        neutral_tie = natural_matchup("20.00", "20.00", neutral_site=True)
        self.assertEqual((neutral_tie.home_margin, neutral_tie.predicted_winner), (Decimal("0"), "pickem"))

        with self.assertRaises(RankingContractError):
            natural_matchup("20.001", "20.00", neutral_site=True)

    def test_hand_calculated_a_to_e_oracle_and_unequal_week_aggregation(self):
        # Home scoring margins and final scores from SR-26's accepted table.
        cases = (
            ("A", "2.24", 2, 0, Decimal("0.24"), StraightUpResult.CORRECT, CoverageResult.NO_COVER),
            ("B", "-2", -2, 0, Decimal("0"), StraightUpResult.CORRECT, CoverageResult.PUSH),
            ("C", "-2", 3, 0, Decimal("5"), StraightUpResult.INCORRECT, CoverageResult.NO_COVER),
            ("D", "0", -7, 0, Decimal("7"), StraightUpResult.UNGRADED, CoverageResult.UNGRADED),
            ("E", "0.02", 1, 0, Decimal("0.98"), StraightUpResult.CORRECT, CoverageResult.COVER),
        )
        grades = []
        for label, margin, home, away, error, straight_up, coverage in cases:
            precision = 2 if label in {"A", "D", "E"} else 0
            candidate = _candidate(label, margin, precision)
            self.assertEqual(candidate.home_handicap, -Decimal(margin))
            grade = grade_forecast(candidate, _score(candidate, home, away))
            grades.append(grade)
            with self.subTest(game=label):
                self.assertEqual(grade.absolute_error, error)
                self.assertEqual(grade.straight_up, straight_up)
                self.assertEqual(grade.coverage, coverage)

        # Independent arithmetic oracle: sum(error)=13.22 and sum(error^2)=75.018.
        expected_errors = [Decimal("0.24"), Decimal("0"), Decimal("5"), Decimal("7"), Decimal("0.98")]
        self.assertEqual(sum(expected_errors), Decimal("13.22"))
        expected_squared = sum((value * value for value in expected_errors), Decimal("0"))
        self.assertEqual(expected_squared, Decimal("75.0180"))

        season = aggregate_grades(grades)
        self.assertEqual(season.game_count, 5)
        self.assertEqual(season.margin_count, 5)
        self.assertEqual(season.mae, Decimal("2.644"))
        self.assertAlmostEqual(float(season.rmse), 3.87345, places=4)
        self.assertEqual((season.straight_up_wins, season.straight_up_losses, season.straight_up_count), (3, 1, 4))
        self.assertEqual((season.covers, season.no_covers, season.pushes, season.coverage_count), (1, 2, 1, 3))
        self.assertEqual(season.coverage_percentage, Decimal(1) / Decimal(3))
        self.assertEqual((season.pickems, season.unknown_selections), (1, 0))

        # A different, unequal weekly partition must preserve season weighting.
        week_one = aggregate_grades(grades[:1])
        week_two = aggregate_grades(grades[1:4])
        week_three = aggregate_grades(grades[4:])
        self.assertEqual(
            sum((week.mae * week.margin_count for week in (week_one, week_two, week_three)), Decimal("0")),
            season.mae * season.margin_count,
        )
        self.assertAlmostEqual(
            float(sum((week.rmse * week.rmse * week.margin_count for week in (week_one, week_two, week_three)), Decimal("0"))),
            float(season.rmse * season.rmse * season.margin_count),
            places=12,
        )

    def test_new_precision_and_exact_pickem_are_preserved(self):
        positive = _candidate("precision-positive", "0.02", 2)
        negative = _candidate("precision-negative", "-2", 0)
        pickem = _candidate("pickem", "0", 2)
        self.assertEqual(positive.home_margin, Decimal("0.02"))
        self.assertEqual(positive.home_handicap, Decimal("-0.02"))
        self.assertEqual(positive.selection, ForecastSelection.HOME)
        self.assertEqual(negative.home_handicap, Decimal("2"))
        self.assertEqual(negative.selection, ForecastSelection.AWAY)
        self.assertEqual(pickem.selection, ForecastSelection.PICKEM)

    def test_legacy_unknown_zero_is_separate_from_pickem(self):
        legacy = _candidate(
            "legacy-zero",
            "0",
            0,
            selection=ForecastSelection.LEGACY_UNKNOWN,
        )
        pickem = _candidate("exact-zero", "0", 0)
        legacy_grade = grade_forecast(legacy, _score(legacy, 21, 14))
        pickem_grade = grade_forecast(pickem, _score(pickem, 21, 14))
        self.assertEqual(legacy_grade.absolute_error, Decimal("7"))
        self.assertEqual(legacy_grade.straight_up, StraightUpResult.UNGRADED)
        self.assertEqual(legacy_grade.coverage, CoverageResult.UNGRADED)
        self.assertEqual(pickem_grade.straight_up, StraightUpResult.UNGRADED)
        self.assertEqual(pickem_grade.coverage, CoverageResult.UNGRADED)
        aggregate = aggregate_grades((legacy_grade, pickem_grade))
        self.assertEqual((aggregate.pickems, aggregate.unknown_selections), (1, 1))


class ForecastLifecycleAcceptanceTests(unittest.TestCase):
    def test_verified_publication_cannot_be_constructed_from_caller_supplied_receipts(self):
        candidate = _candidate("raw-receipt", "2.24", 2)
        receipt = _receipt(candidate)
        with self.assertRaises(PublicationExecutionError):
            VerifiedForecastPublication((candidate,), (receipt,), {})

    def test_last_qualifying_pregame_replacement_is_selected(self):
        original = _candidate("replacement", "2.24", 2)
        corrected = _candidate(
            "replacement",
            "3.00",
            0,
            predecessor_version_id=original.version_id,
            replacement_reason="pregame model correction",
        )
        # The correction is explicitly published before kickoff and must be
        # selected while retaining the predecessor for audit.
        selected = select_graded_forecast(
            (original, corrected),
            (_receipt(original, hour=10), _receipt(corrected, hour=11)),
            _timing(original),
        )
        self.assertIs(selected, corrected)
        self.assertEqual(corrected.predecessor_version_id, original.version_id)
        self.assertEqual(corrected.replacement_reason, "pregame model correction")

        # A postgame model change has no qualifying publication and cannot
        # replace the already graded forecast.
        late = _candidate(
            "replacement",
            "8.00",
            0,
            predecessor_version_id=corrected.version_id,
            replacement_reason="postgame bug discovery",
        )
        selected_after_play = select_graded_forecast(
            (original, corrected, late),
            (_receipt(original, hour=10), _receipt(corrected, hour=11), _receipt(late, hour=13)),
            _timing(original),
        )
        self.assertIs(selected_after_play, corrected)

    def test_republishing_an_ancestor_after_correction_does_not_rewind_selection(self):
        original = _candidate("ancestor-republish", "2.24", 2)
        corrected = _candidate(
            "ancestor-republish",
            "3.00",
            0,
            predecessor_version_id=original.version_id,
            replacement_reason="pregame model correction",
        )
        timing = _timing(original, kickoff_hour=14)
        selected = select_graded_forecast(
            (original, corrected),
            (_receipt(original, hour=10), _receipt(corrected, hour=11), _receipt(original, hour=13)),
            timing,
        )
        self.assertIs(selected, corrected)

    def test_rebuild_and_score_correction_keep_forecast_identity(self):
        candidate = _candidate("score-correction", "2.24", 2)
        initial = _score(candidate, 21, 14)
        corrected = initial.corrected(20, 14, _at(14), _evidence("corrected-score"))
        before = grade_forecast(candidate, initial)
        after = grade_forecast(candidate, corrected)
        self.assertEqual(before.forecast_version_id, after.forecast_version_id)
        self.assertEqual(before.predicted_home_margin, after.predicted_home_margin)
        self.assertEqual(before.actual_home_margin, Decimal("7"))
        self.assertEqual(after.actual_home_margin, Decimal("6"))
        self.assertEqual(after.score_corrected_at, _at(14))
        self.assertNotEqual(before.score_revision_id, after.score_revision_id)

    def test_missing_or_corrupt_publication_and_game_bindings_do_not_qualify(self):
        candidate = _candidate("evidence", "2.24", 2)
        timing = _timing(candidate)
        receipt = _receipt(candidate)
        self.assertTrue(receipt_qualifies(candidate, receipt, timing))

        # Publication bindings are content identities, not arbitrary trusted
        # labels.  A malformed artifact digest must be rejected at the schema
        # boundary before any receipt can qualify.
        with self.assertRaises(ForecastContractError):
            PublicationReceipt(
                receipt_id="malformed-artifact",
                candidate_version_id=candidate.version_id,
                candidate_artifact_digest=candidate.artifact_digest,
                publication_artifact_digest="untrusted-flag",
                attempt_id=receipt.attempt_id,
                verification_id=receipt.verification_id,
                source=receipt.source,
                provider_published_at=receipt.provider_published_at,
            )

        wrong_digest = PublicationReceipt(
            receipt_id="wrong-digest",
            candidate_version_id=candidate.version_id,
            candidate_artifact_digest="sha256:" + ("f" * 64),
            publication_artifact_digest=receipt.publication_artifact_digest,
            attempt_id=receipt.attempt_id,
            verification_id=receipt.verification_id,
            source=receipt.source,
            provider_published_at=receipt.provider_published_at,
        )
        self.assertFalse(receipt_qualifies(candidate, wrong_digest, timing))

        other_game = _candidate("other-game", "2.24", 2)
        self.assertFalse(receipt_qualifies(other_game, receipt, _timing(other_game)))
        self.assertIsNone(select_graded_forecast((candidate,), (), timing))

    def test_semantic_candidate_tampering_cannot_reuse_original_version_id(self):
        candidate = _candidate("tamper", "2.24", 2)
        encoded = candidate.to_dict()
        encoded["forecast"]["home_margin"] = "9.99"
        with self.assertRaises(ForecastContractError):
            ForecastCandidate.from_dict(encoded)

    def test_independent_validation_binds_evaluation_row_identity_to_its_grade(self):
        candidate = _candidate("row-identity", "2.24", 2)
        final_score = _score(candidate, 21, 14)
        grade = grade_forecast(candidate, final_score)
        aggregate = aggregate_grades((grade,)).to_dict()
        receipt = _receipt(candidate)
        timing = _timing(candidate)
        ledger = {
            "candidates": [candidate.to_dict()],
            "score_history": [final_score.to_dict()],
            "receipts": [receipt.to_dict()],
            "timing_evidence": [timing.to_dict()],
        }
        evaluation = {
            "games": [{
                "game": candidate.game.to_dict(),
                "disposition": "evaluated",
                "grade": grade.to_dict(),
            }],
            "season_summary": aggregate,
            "weekly": {"0": aggregate},
        }
        evaluation["games"][0]["game"]["home"]["name"] = "Tampered Home"
        with self.assertRaises(ForecastContractError):
            validate_evaluation_semantics(ledger, evaluation)

    def test_invalid_score_correction_order_and_duplicate_game_are_rejected(self):
        candidate = _candidate("invalid", "2.24", 2)
        score = _score(candidate, 10, 7)
        with self.assertRaises(ForecastContractError):
            score.corrected(11, 7, _at(12), _evidence("too-early"))
        grade = grade_forecast(candidate, score)
        with self.assertRaises(ForecastContractError):
            aggregate_grades((grade, grade))


if __name__ == "__main__":
    unittest.main()
