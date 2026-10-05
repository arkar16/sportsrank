"""Independent checks for source-bound conference chronology and evidence.

Expected bounds and projection states are literal values.  The tests use the
existing small source fixture only as a complete roster/game setup.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from cfb.conference_reference import CheckpointRanking, derive_conference_reference
from cfb.conference_sources import (
    ChampionshipRule,
    ConferenceSourceError,
    DatedConfirmation,
    EligibilityRule,
    SitePolicy,
    SourceEvidence,
    _evidence_payload,
    snapshot_content_checksum,
    validate_conference_supplement,
)
from tests.test_conference_sources import _snapshot, _supplement


UTC = timezone.utc
DATE_ONLY = date(2025, 8, 25)
DATE_ONLY_BOUND = datetime(2025, 8, 26, 12, tzinfo=UTC)


def _rankings() -> tuple[CheckpointRanking, ...]:
    return (
        CheckpointRanking("Alpha", 80.0, 1),
        CheckpointRanking("Beta", 70.0, 2),
        CheckpointRanking("Gamma", 60.0, 3),
        CheckpointRanking("Delta", 50.0, 4),
        CheckpointRanking("Independent", 40.0, 5),
    )


def _earlier_fixture():
    original = _snapshot()
    games = tuple(
        replace(game, date="2025-01-01T00:00:00+00:00")
        for game in original.games
    )
    provisional = replace(original, games=games, checksum="0" * 64)
    snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
    supplement = replace(_supplement(original), snapshot_checksum=snapshot.checksum)
    return snapshot, supplement


def _derive(snapshot, supplement, *, cutoff: datetime):
    return derive_conference_reference(
        snapshot,
        validate_conference_supplement(supplement, snapshot),
        _rankings(),
        phase="week",
        target_week=0,
        cutoff=cutoff,
        dataset_id="independent-evidence",
        model_version="v0.4.0",
    )


def _four_team_red_fixture():
    """Make a complete four-member league for independent confirmation cases."""

    snapshot, supplement = _earlier_fixture()
    games = tuple(
        replace(
            game,
            week=0,
            home_team="Gamma",
            away_team="Delta",
            home_points=14,
            away_points=10,
            phase="regular",
            phase_source="provider",
            disposition="completed",
        )
        if game.provider_id == "g-post" else game
        for game in snapshot.games
    )
    teams = tuple(
        replace(team, conference="Red")
        if team.school in {"Gamma", "Delta"}
        else team
        for team in snapshot.teams
    )
    provisional = replace(snapshot, teams=teams, games=games, checksum="0" * 64)
    snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
    members = tuple(
        replace(member, conference="Red")
        if member.team in {"Gamma", "Delta"}
        else member
        for member in supplement.members
    )
    games = tuple(
        replace(
            game,
            home_team="Gamma",
            away_team="Delta",
            conference_game=True,
            counts_for_standings=True,
            title_game=False,
            conference="Red",
        )
        if game.provider_id == "g-post" else game
        for game in supplement.games
    )
    red_rule = next(rule for rule in supplement.rules if rule.conference == "Red")
    eligibility = tuple(red_rule.championship.eligibility) + (
        EligibilityRule("Gamma", True, ("fixture-source",)),
        EligibilityRule("Delta", True, ("fixture-source",)),
    )
    rules = (
        replace(
            red_rule,
            championship=replace(red_rule.championship, eligibility=eligibility),
        ),
    )
    supplement = replace(
        supplement,
        snapshot_checksum=snapshot.checksum,
        members=members,
        games=games,
        rules=rules,
    )
    return snapshot, supplement


class IndependentConferenceEvidenceTests(unittest.TestCase):
    def test_date_bound_is_utc_minus_twelve_conservative_and_same_day_is_unavailable(self):
        snapshot, supplement = _earlier_fixture()
        dated = replace(
            supplement.evidence[0],
            published_at=DATE_ONLY,
            known_at=DATE_ONLY,
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            published_at_upper_bound=None,
            known_at_upper_bound=None,
            timing_basis="official publication date; timezone not stated",
        )
        validated = validate_conference_supplement(
            replace(supplement, evidence=(dated,)), snapshot
        )
        evidence = validated.supplement.evidence[0]
        self.assertIsNone(evidence.published_at)
        self.assertIsNone(evidence.known_at)
        self.assertEqual(evidence.published_date, DATE_ONLY)
        self.assertEqual(evidence.known_date, DATE_ONLY)
        self.assertEqual(evidence.known_at_bound, DATE_ONLY_BOUND)
        self.assertEqual(evidence.known_at_upper_bound, DATE_ONLY_BOUND)
        self.assertEqual(evidence.known_precision, "date")

        payload = _evidence_payload(evidence)
        self.assertIsNone(payload["known_at"])
        self.assertEqual(payload["known_date"], "2025-08-25")
        self.assertEqual(payload["known_at_upper_bound"], DATE_ONLY_BOUND.isoformat())
        self.assertEqual(payload["known_precision"], "date")
        self.assertEqual(payload["known_raw"], "2025-08-25")
        self.assertEqual(payload["published_date"], "2025-08-25")

        before_bound = _derive(
            snapshot,
            replace(supplement, evidence=(dated,)),
            cutoff=datetime(2025, 8, 25, 23, 59, tzinfo=UTC),
        )
        at_bound = _derive(
            snapshot,
            replace(supplement, evidence=(dated,)),
            cutoff=DATE_ONLY_BOUND,
        )
        before_projection = next(
            item for item in before_bound.projections if item.conference == "Red"
        )
        at_projection = next(
            item for item in at_bound.projections if item.conference == "Red"
        )
        self.assertEqual(before_projection.status, "unavailable")
        self.assertEqual(before_projection.reason, "rule_evidence_unavailable")
        self.assertEqual(at_projection.status, "projected")
        self.assertEqual(at_projection.participants, ("Alpha", "Beta"))
        self.assertEqual(before_bound.standings, at_bound.standings)

    def test_naive_clock_preserves_raw_clock_and_timezone_uncertainty(self):
        snapshot, supplement = _earlier_fixture()
        naive_clock = replace(
            supplement.evidence[0],
            published_at=datetime(2025, 8, 25, 23, 30),
            known_at=datetime(2025, 8, 25, 23, 30),
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            published_at_upper_bound=None,
            known_at_upper_bound=None,
            timing_basis="official local clock; timezone not stated",
        )
        validated = validate_conference_supplement(
            replace(supplement, evidence=(naive_clock,)), snapshot
        )
        evidence = validated.supplement.evidence[0]
        self.assertIsNone(evidence.published_at)
        self.assertIsNone(evidence.known_at)
        self.assertEqual(
            evidence.known_local_datetime,
            datetime(2025, 8, 25, 23, 30),
        )
        self.assertEqual(evidence.published_precision, "local_datetime")
        self.assertEqual(evidence.known_precision, "local_datetime")
        self.assertEqual(
            evidence.known_at_lower_bound,
            datetime(2025, 8, 25, 9, 30, tzinfo=UTC),
        )
        self.assertEqual(
            evidence.known_at_bound,
            datetime(2025, 8, 26, 11, 30, tzinfo=UTC),
        )
        self.assertEqual(evidence.known_raw, "2025-08-25T23:30:00")
        self.assertEqual(evidence.published_raw, "2025-08-25T23:30:00")
        payload = _evidence_payload(evidence)
        self.assertIsNone(payload["known_date"])
        self.assertEqual(payload["known_local_datetime"], "2025-08-25T23:30:00")
        self.assertEqual(payload["known_raw"], "2025-08-25T23:30:00")
        self.assertIsNone(payload["published_date"])
        self.assertEqual(payload["published_local_datetime"], "2025-08-25T23:30:00")
        self.assertEqual(payload["published_raw"], "2025-08-25T23:30:00")
        copied = replace(evidence)
        self.assertIsNone(copied.known_at)
        self.assertEqual(
            _evidence_payload(copied)["known_local_datetime"],
            "2025-08-25T23:30:00",
        )

        string_clock = replace(
            supplement.evidence[0],
            published_at="2025-08-25T23:30:00",
            known_at="2025-08-25T23:30:00",
            published_precision=None,
            known_precision=None,
            published_raw=None,
            known_raw=None,
            published_at_upper_bound=None,
            known_at_upper_bound=None,
            timing_basis="official local clock; timezone not stated",
        )
        string_validated = validate_conference_supplement(
            replace(supplement, evidence=(string_clock,)), snapshot
        ).supplement.evidence[0]
        self.assertEqual(string_validated.known_precision, "local_datetime")
        self.assertEqual(
            string_validated.known_at_bound,
            datetime(2025, 8, 26, 11, 30, tzinfo=UTC),
        )

        same_day = _derive(
            snapshot,
            replace(supplement, evidence=(naive_clock,)),
            cutoff=datetime(2025, 8, 25, 23, 59, tzinfo=UTC),
        )
        next_day = _derive(
            snapshot,
            replace(supplement, evidence=(naive_clock,)),
            cutoff=datetime(2025, 8, 26, 11, 30, tzinfo=UTC),
        )
        same_day_red = next(
            item for item in same_day.projections if item.conference == "Red"
        )
        next_day_red = next(
            item for item in next_day.projections if item.conference == "Red"
        )
        self.assertEqual(same_day_red.reason, "rule_evidence_unavailable")
        self.assertEqual(next_day_red.status, "projected")

    def test_temporal_precision_contradictions_and_unsupported_values_fail_closed(self):
        common = dict(
            url="https://example.test/precision",
            source_sha256="d" * 64,
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
            effective_from=date(2025, 1, 1),
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                evidence_id="missing-reason",
                known_at=None,
                known_precision="unknown",
                **common,
            )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                evidence_id="bad-bound",
                known_at="2025-08-25",
                known_at_upper_bound=datetime(2025, 8, 26, 11, 59, tzinfo=UTC),
                **common,
            )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                evidence_id="bad-precision",
                known_at="2025-08-25",
                known_precision="instant",
                **common,
            )
        with self.assertRaises(ConferenceSourceError):
            SourceEvidence(
                evidence_id="unsupported-precision",
                known_at="2025-08-25",
                known_precision="minute",
                **common,
            )

    def test_unknown_timing_never_uses_retrieval_or_pdf_metadata(self):
        snapshot, supplement = _earlier_fixture()
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
        reference = _derive(snapshot, candidate, cutoff=datetime(2026, 1, 1, tzinfo=UTC))
        baseline = _derive(
            snapshot,
            supplement,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
        )
        red = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(red.status, "unavailable")
        self.assertEqual(red.reason, "rule_evidence_unavailable")
        self.assertEqual(reference.standings, baseline.standings)

    def test_unknown_selection_or_eligibility_only_disables_affected_projection(self):
        snapshot, supplement = _earlier_fixture()
        baseline = _derive(
            snapshot, supplement, cutoff=datetime(2025, 9, 1, tzinfo=UTC)
        )
        red_rule = next(rule for rule in supplement.rules if rule.conference == "Red")
        unknown_selection = ChampionshipRule(
            selection="unknown",
            eligibility=(),
            divisions=(),
            site=SitePolicy("unresolved", None, red_rule.championship.site.evidence_ids),
            evidence_ids=red_rule.championship.evidence_ids,
            reason="official selection rule was not established",
        )
        selection_candidate = replace(
            supplement,
            rules=tuple(
                replace(red_rule, championship=unknown_selection)
                if rule.conference == "Red" else rule
                for rule in supplement.rules
            ),
        )
        selection_reference = _derive(
            snapshot, selection_candidate, cutoff=datetime(2025, 9, 1, tzinfo=UTC)
        )
        selection_projection = next(
            item for item in selection_reference.projections if item.conference == "Red"
        )
        self.assertEqual(selection_projection.reason, "selection_rule_unavailable")
        self.assertEqual(selection_projection.participants, ())
        self.assertEqual(selection_reference.standings, baseline.standings)

        unknown_eligibility = tuple(
            replace(row, eligible=None, reason="title eligibility source unresolved")
            for row in red_rule.championship.eligibility
        )
        eligibility_candidate = replace(
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
        eligibility_reference = _derive(
            snapshot, eligibility_candidate, cutoff=datetime(2025, 9, 1, tzinfo=UTC)
        )
        eligibility_projection = next(
            item for item in eligibility_reference.projections if item.conference == "Red"
        )
        self.assertEqual(eligibility_projection.reason, "eligibility_evidence_unavailable")
        self.assertEqual(eligibility_reference.standings, baseline.standings)

    def test_confirmation_windows_overlap_unresolved_but_disjoint_instants_order(self):
        snapshot, supplement = _four_team_red_fixture()
        overlap = replace(
            supplement,
            confirmations=(
                DatedConfirmation(
                    confirmation_id="date-early",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at="2025-08-25",
                    evidence_ids=("fixture-source",),
                    site_mode="neutral",
                ),
                DatedConfirmation(
                    confirmation_id="date-late",
                    conference="Red",
                    participants=("Gamma", "Delta"),
                    known_at="2025-08-26",
                    evidence_ids=("fixture-source",),
                    site_mode="fixed_hosted",
                    host_team="Gamma",
                ),
            ),
        )
        overlap_reference = _derive(
            snapshot, overlap, cutoff=datetime(2025, 8, 27, 12, tzinfo=UTC)
        )
        overlap_projection = next(
            item for item in overlap_reference.projections if item.conference == "Red"
        )
        self.assertEqual(overlap_projection.status, "unresolved")
        self.assertEqual(overlap_projection.reason, "unresolved_qualification")
        self.assertIsNone(overlap_projection.confirmation_id)

        disjoint = replace(
            supplement,
            confirmations=(
                DatedConfirmation(
                    confirmation_id="instant-early",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=datetime(2025, 8, 25, 10, tzinfo=UTC),
                    evidence_ids=("fixture-source",),
                    site_mode="neutral",
                ),
                DatedConfirmation(
                    confirmation_id="instant-late",
                    conference="Red",
                    participants=("Gamma", "Delta"),
                    known_at=datetime(2025, 8, 26, 10, tzinfo=UTC),
                    evidence_ids=("fixture-source",),
                    site_mode="fixed_hosted",
                    host_team="Gamma",
                ),
            ),
        )
        disjoint_reference = _derive(
            snapshot, disjoint, cutoff=datetime(2025, 9, 1, tzinfo=UTC)
        )
        disjoint_projection = next(
            item for item in disjoint_reference.projections if item.conference == "Red"
        )
        self.assertEqual(disjoint_projection.status, "confirmed")
        self.assertEqual(disjoint_projection.confirmation_id, "instant-late")
        self.assertEqual(disjoint_projection.site_state, "hosted")

        same_pair_conflict = replace(
            supplement,
            confirmations=(
                DatedConfirmation(
                    confirmation_id="same-pair-early",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=datetime(2025, 8, 25, 10, tzinfo=UTC),
                    evidence_ids=("fixture-source",),
                    site_mode="neutral",
                ),
                DatedConfirmation(
                    confirmation_id="same-pair-late",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=datetime(2025, 8, 26, 10, tzinfo=UTC),
                    evidence_ids=("fixture-source",),
                    site_mode="fixed_hosted",
                    host_team="Alpha",
                ),
            ),
        )
        same_pair_reference = _derive(
            snapshot, same_pair_conflict, cutoff=datetime(2025, 9, 1, tzinfo=UTC)
        )
        same_pair_projection = next(
            item for item in same_pair_reference.projections if item.conference == "Red"
        )
        self.assertEqual(same_pair_projection.status, "unresolved")
        self.assertIsNone(same_pair_projection.confirmation_id)

    def test_declared_checksum_rejects_precision_and_bound_semantic_tampering(self):
        snapshot, supplement = _earlier_fixture()
        sealed = replace(supplement, declared_checksum=supplement.checksum)
        changed = replace(
            sealed.evidence[0],
            known_at="2025-08-25",
            known_precision=None,
            known_raw=None,
            known_at_upper_bound=None,
        )
        tampered = replace(sealed, evidence=(changed,))
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(tampered, snapshot)


if __name__ == "__main__":
    unittest.main()
