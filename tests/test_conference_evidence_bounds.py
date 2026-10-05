from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from cfb.conference_reference import derive_conference_reference
from cfb.conference_sources import (
    ChampionshipRule,
    ConferenceSourceError,
    DatedConfirmation,
    SitePolicy,
    SourceEvidence,
    snapshot_content_checksum,
    validate_conference_supplement,
    _evidence_payload,
    _supplement_payload,
)
from tests.test_conference_reference import _rankings
from tests.test_conference_sources import _snapshot, _supplement


UTC = timezone.utc
BOUND = datetime(2025, 8, 26, 12, tzinfo=UTC)


def _earlier_games_fixture():
    """Keep game timing before the evidence boundary for isolated cutoff tests."""

    original = _snapshot()
    games = tuple(
        replace(game, date="2025-01-01T00:00:00+00:00") for game in original.games
    )
    provisional = replace(original, games=games, checksum="0" * 64)
    snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
    supplement = replace(_supplement(original), snapshot_checksum=snapshot.checksum)
    return snapshot, supplement


class ConferenceEvidenceBoundsTests(unittest.TestCase):
    def test_exact_timestamp_callers_keep_exact_semantics(self) -> None:
        evidence = _supplement(_snapshot()).evidence[0]
        self.assertEqual(evidence.published_precision, "instant")
        self.assertEqual(evidence.known_precision, "instant")
        self.assertEqual(evidence.known_at_bound, evidence.known_at)
        self.assertIsNone(evidence.known_at_upper_bound)
        self.assertEqual(evidence.timing_basis, "exact_timestamp")

    def test_date_only_source_keeps_raw_date_and_uses_latest_timezone_bound(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        dated = replace(
            supplement.evidence[0],
            published_at="2025-08-25",
            known_at="2025-08-25",
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            timing_basis="official publication date; timezone not stated",
        )
        candidate = replace(supplement, evidence=(dated,))
        validated = validate_conference_supplement(candidate, snapshot)
        self.assertIsNone(validated.supplement.evidence[0].known_at)
        self.assertEqual(validated.supplement.evidence[0].known_date, date(2025, 8, 25))
        self.assertEqual(validated.supplement.evidence[0].known_at_bound, BOUND)
        self.assertEqual(validated.supplement.evidence[0].known_raw, "2025-08-25")
        payload = _evidence_payload(validated.supplement.evidence[0])
        self.assertIsNone(payload["known_at"])
        self.assertEqual(payload["known_at_upper_bound"], BOUND.isoformat())
        self.assertEqual(payload["known_date"], "2025-08-25")
        self.assertEqual(payload["known_precision"], "date")
        self.assertEqual(payload["known_raw"], "2025-08-25")
        reconstructed = SourceEvidence(**payload)
        self.assertEqual(reconstructed, validated.supplement.evidence[0])
        inferred_precision = dict(payload)
        inferred_precision.pop("published_precision")
        inferred_precision.pop("known_precision")
        self.assertEqual(
            SourceEvidence(**inferred_precision), validated.supplement.evidence[0]
        )

        before = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 8, 26, 11, 59, tzinfo=UTC),
            dataset_id="bounds-before",
            model_version="v0.4.0",
        )
        after = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=BOUND,
            dataset_id="bounds-after",
            model_version="v0.4.0",
        )
        before_red = next(item for item in before.projections if item.conference == "Red")
        after_red = next(item for item in after.projections if item.conference == "Red")
        self.assertEqual(before_red.reason, "rule_evidence_unavailable")
        self.assertEqual(after_red.status, "projected")
        self.assertEqual(before.standings, after.standings)

    def test_naive_clock_keeps_raw_text_but_uses_calendar_date_precision(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        naive = replace(
            supplement.evidence[0],
            published_at="2025-08-25T23:30:00",
            known_at="2025-08-25T23:30:00",
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            timing_basis="official local clock; timezone not stated",
        )
        validated = validate_conference_supplement(
            replace(supplement, evidence=(naive,)), snapshot
        )
        evidence = validated.supplement.evidence[0]
        self.assertEqual(evidence.published_precision, "local_datetime")
        self.assertEqual(evidence.known_precision, "local_datetime")
        self.assertIsNone(evidence.known_at)
        self.assertIsNone(evidence.known_date)
        self.assertEqual(
            evidence.known_local_datetime, datetime(2025, 8, 25, 23, 30)
        )
        self.assertEqual(
            evidence.known_at_bound, datetime(2025, 8, 26, 11, 30, tzinfo=UTC)
        )
        self.assertEqual(
            evidence.known_at_lower_bound, datetime(2025, 8, 25, 9, 30, tzinfo=UTC)
        )
        self.assertEqual(evidence.known_raw, "2025-08-25T23:30:00")
        payload = _evidence_payload(evidence)
        self.assertIsNone(payload["known_at"])
        self.assertEqual(payload["known_raw"], "2025-08-25T23:30:00")
        self.assertEqual(payload["known_local_datetime"], "2025-08-25T23:30:00")

        naive_object = replace(
            supplement.evidence[0],
            published_at=datetime(2025, 8, 25, 23, 30),
            known_at=datetime(2025, 8, 25, 23, 30),
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            timing_basis="official local clock object; timezone not stated",
        )
        self.assertEqual(naive_object.known_raw, "2025-08-25T23:30:00")
        self.assertEqual(
            naive_object.known_local_datetime, datetime(2025, 8, 25, 23, 30)
        )
        self.assertEqual(
            _evidence_payload(naive_object)["known_local_datetime"],
            "2025-08-25T23:30:00",
        )

        # Normalized date-only values can be copied/reconstructed without
        # relying on the preserved raw text as the canonical parser input.
        copied = replace(evidence)
        self.assertEqual(copied, evidence)
        self.assertEqual(
            copied.known_at_bound, datetime(2025, 8, 26, 11, 30, tzinfo=UTC)
        )

    def test_unknown_timing_cannot_fall_back_to_retrieval_or_creation_metadata(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        unknown = replace(
            supplement.evidence[0],
            published_at=None,
            known_at=None,
            published_precision="unknown",
            known_precision="unknown",
            published_raw="PDF CreationDate: 2025-09-04T00:00:00Z",
            known_raw="official publication/version date not established",
            timing_basis="captured file metadata only",
            timing_reason="file metadata does not prove public availability",
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        candidate = replace(supplement, evidence=(unknown,))
        validated = validate_conference_supplement(candidate, snapshot)
        evidence = validated.supplement.evidence[0]
        self.assertIsNone(evidence.known_at)
        self.assertIsNone(evidence.known_at_bound)
        self.assertEqual(evidence.retrieved_at, datetime(2026, 1, 2, tzinfo=UTC))
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            dataset_id="unknown-timing",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(red.reason, "rule_evidence_unavailable")
        self.assertEqual(reference.standings, derive_conference_reference(
            snapshot,
            validate_conference_supplement(_supplement(snapshot), snapshot),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            dataset_id="known-timing",
            model_version="v0.4.0",
        ).standings)

    def test_temporal_precision_rejects_missing_reason_and_contradictory_bounds(self) -> None:
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                "unknown",
                "https://example.test/unknown",
                "a" * 64,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC).date(),
                None,
                known_precision="unknown",
            )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                "bad-bound",
                "https://example.test/bad-bound",
                "a" * 64,
                "2025-08-25",
                datetime(2025, 1, 1, tzinfo=UTC).date(),
                "2025-08-25",
                known_at_upper_bound=datetime(2025, 8, 26, 11, 59, tzinfo=UTC),
            )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                "bad-precision",
                "https://example.test/bad-precision",
                "a" * 64,
                "2025-08-25",
                datetime(2025, 1, 1, tzinfo=UTC).date(),
                "2025-08-25",
                known_precision="instant",
            )

    def test_unknown_selection_rule_preserves_standings_but_disables_projection(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        red_rule = next(rule for rule in supplement.rules if rule.conference == "Red")
        unknown_championship = ChampionshipRule(
            selection="unknown",
            eligibility=(),
            divisions=(),
            site=SitePolicy("unresolved", None, red_rule.championship.site.evidence_ids),
            evidence_ids=red_rule.championship.evidence_ids,
            reason="official selection rule was not established",
        )
        candidate = replace(
            supplement,
            rules=tuple(
                replace(red_rule, championship=unknown_championship)
                if rule.conference == "Red" else rule
                for rule in supplement.rules
            ),
        )
        validated = validate_conference_supplement(candidate, snapshot)
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 1, tzinfo=UTC),
            dataset_id="unknown-rule",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(red.reason, "selection_rule_unavailable")
        self.assertEqual(red.participants, ())
        self.assertTrue(reference.standings)

    def test_unknown_eligibility_preserves_records_but_disables_projection(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        red_rule = next(rule for rule in supplement.rules if rule.conference == "Red")
        unknown_eligibility = tuple(
            replace(row, eligible=None, reason="title eligibility source unresolved")
            for row in red_rule.championship.eligibility
        )
        candidate = replace(
            supplement,
            rules=tuple(
                replace(
                    red_rule,
                    championship=replace(
                        red_rule.championship, eligibility=unknown_eligibility
                    ),
                )
                if rule.conference == "Red" else rule
                for rule in supplement.rules
            ),
        )
        validated = validate_conference_supplement(candidate, snapshot)
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 1, tzinfo=UTC),
            dataset_id="unknown-eligibility",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(red.reason, "eligibility_evidence_unavailable")
        self.assertTrue(reference.standings)

    def test_date_only_confirmation_is_bound_and_unknown_confirmation_never_resolves(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        dated_evidence = replace(
            supplement.evidence[0],
            published_at="2025-08-25",
            known_at="2025-08-25",
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
        )
        confirmation = DatedConfirmation(
            confirmation_id="red-date-only",
            conference="Red",
            participants=("Alpha", "Beta"),
            known_at="2025-08-25",
            evidence_ids=("fixture-source",),
        )
        candidate = replace(
            supplement,
            evidence=(dated_evidence,),
            confirmations=(confirmation,),
        )
        validated = validate_conference_supplement(candidate, snapshot)
        validated_confirmation = validated.supplement.confirmations[0]
        self.assertIsNone(validated_confirmation.known_at)
        self.assertEqual(validated_confirmation.known_date, date(2025, 8, 25))
        self.assertEqual(validated_confirmation.known_at_bound, BOUND)
        self.assertEqual(
            validated_confirmation.known_at_lower_bound,
            datetime(2025, 8, 24, 10, tzinfo=UTC),
        )
        confirmation_wire = _supplement_payload(candidate)["confirmations"][0]
        self.assertIsNone(confirmation_wire["known_at"])
        self.assertEqual(confirmation_wire["known_date"], "2025-08-25")
        self.assertEqual(confirmation_wire["known_at_upper_bound"], BOUND.isoformat())
        reconstructed_confirmation = DatedConfirmation(**confirmation_wire)
        self.assertEqual(reconstructed_confirmation, validated_confirmation)
        before = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 8, 26, 11, 59, tzinfo=UTC),
            dataset_id="confirmation-before",
            model_version="v0.4.0",
        )
        self.assertNotEqual(
            next(item for item in before.projections if item.conference == "Red").confirmation_id,
            "red-date-only",
        )
        unknown_confirmation = replace(
            confirmation,
            confirmation_id="red-unknown",
            known_at=None,
            known_precision="unknown",
            known_at_upper_bound=None,
            known_date=None,
            known_local_datetime=None,
            known_raw="date not established",
            timing_reason="official confirmation timing unresolved",
        )
        unknown_candidate = replace(candidate, confirmations=(unknown_confirmation,))
        unknown_validated = validate_conference_supplement(unknown_candidate, snapshot)
        after = derive_conference_reference(
            snapshot,
            unknown_validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            dataset_id="confirmation-unknown",
            model_version="v0.4.0",
        )
        self.assertNotEqual(
            next(item for item in after.projections if item.conference == "Red").confirmation_id,
            "red-unknown",
        )

    def test_overlapping_confirmation_intervals_do_not_choose_largest_upper_bound(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        date_confirmation = DatedConfirmation(
            confirmation_id="red-date",
            conference="Red",
            participants=("Alpha", "Beta"),
            known_at="2025-08-25",
            evidence_ids=("fixture-source",),
        )
        overlapping_exact = DatedConfirmation(
            confirmation_id="red-exact-overlap",
            conference="Red",
            participants=("Beta", "Alpha"),
            known_at=datetime(2025, 8, 26, 6, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        overlapping = replace(
            supplement, confirmations=(date_confirmation, overlapping_exact)
        )
        reference = derive_conference_reference(
            snapshot,
            validate_conference_supplement(overlapping, snapshot),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 1, tzinfo=UTC),
            dataset_id="confirmation-overlap",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "unresolved")
        self.assertIsNone(projection.confirmation_id)

        later_exact = replace(
            overlapping_exact,
            confirmation_id="red-exact-later",
            known_at=datetime(2025, 8, 27, tzinfo=UTC),
        )
        ordered = replace(supplement, confirmations=(date_confirmation, later_exact))
        ordered_reference = derive_conference_reference(
            snapshot,
            validate_conference_supplement(ordered, snapshot),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 1, tzinfo=UTC),
            dataset_id="confirmation-ordered",
            model_version="v0.4.0",
        )
        ordered_projection = next(
            item for item in ordered_reference.projections if item.conference == "Red"
        )
        self.assertEqual(ordered_projection.status, "confirmed")
        self.assertEqual(ordered_projection.confirmation_id, "red-exact-later")

    def test_later_record_in_a_signature_group_can_supersede_another_signature(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        old_signature = DatedConfirmation(
            confirmation_id="red-old-alpha-beta",
            conference="Red",
            participants=("Alpha", "Beta"),
            known_at=datetime(2025, 8, 25, 10, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        competing_signature = DatedConfirmation(
            confirmation_id="red-middle-beta-alpha",
            conference="Red",
            participants=("Beta", "Alpha"),
            known_at=datetime(2025, 8, 26, 10, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        latest_signature = DatedConfirmation(
            confirmation_id="red-latest-alpha-beta",
            conference="Red",
            participants=("Alpha", "Beta"),
            known_at=datetime(2025, 8, 27, 10, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        candidate = replace(
            supplement,
            confirmations=(old_signature, competing_signature, latest_signature),
        )
        reference = derive_conference_reference(
            snapshot,
            validate_conference_supplement(candidate, snapshot),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 1, tzinfo=UTC),
            dataset_id="confirmation-a-b-a",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "confirmed")
        self.assertEqual(projection.confirmation_id, "red-latest-alpha-beta")

    def test_sealed_checksum_detects_changed_temporal_semantics(self) -> None:
        snapshot, supplement = _earlier_games_fixture()
        dated = replace(
            supplement.evidence[0],
            published_at="2025-08-25",
            known_at="2025-08-25",
            published_precision=None,
            known_precision=None,
        )
        candidate = replace(supplement, evidence=(dated,))
        sealed = replace(candidate, declared_checksum=candidate.checksum)
        tampered = replace(
            sealed,
            evidence=(
                replace(
                    dated,
                    timing_basis="tampered chronology basis",
                ),
            ),
        )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(tampered, snapshot)


if __name__ == "__main__":
    unittest.main()
