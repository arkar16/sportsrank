from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from cfb.conference_reference import _game_phase, derive_conference_reference
from cfb.conference_sources import (
    ConferenceGameDesignation,
    ConferenceSourceError,
    snapshot_content_checksum,
    validate_conference_supplement,
    _supplement_payload,
)
from tests.test_conference_reference import _rankings
from tests.test_conference_sources import _snapshot, _supplement


UTC = timezone.utc


def _phase_snapshot_and_supplement(
    *,
    phase: str | None = None,
    phase_evidence_ids: tuple[str, ...] = (),
    declared_checksum: str | None = None,
):
    original = _snapshot()
    games = tuple(
        replace(game, phase=None, phase_source=None)
        if game.provider_id == "g-cross"
        else game
        for game in original.games
    )
    provisional = replace(original, games=games, checksum="0" * 64)
    snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
    supplement = replace(
        _supplement(original),
        snapshot_checksum=snapshot.checksum,
        declared_checksum=declared_checksum,
    )
    designation = next(
        item for item in supplement.games if item.provider_id == "g-cross"
    )
    designation = replace(
        designation,
        phase=phase,
        phase_evidence_ids=phase_evidence_ids,
    )
    supplement = replace(
        supplement,
        games=tuple(
            designation if item.provider_id == "g-cross" else item
            for item in supplement.games
        ),
    )
    return snapshot, supplement


class ConferencePhaseEvidenceTests(unittest.TestCase):
    def test_unknown_phase_is_not_inferred_from_title_false_and_has_own_bucket(self) -> None:
        snapshot, supplement = _phase_snapshot_and_supplement()
        validated = validate_conference_supplement(supplement, snapshot)
        designation = next(
            item for item in validated.supplement.games if item.provider_id == "g-cross"
        )
        self.assertEqual(designation.phase, "unknown")
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 2, tzinfo=UTC),
            dataset_id="phase-unknown",
            model_version="v0.4.0",
        )
        red_blue = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(red_blue.regular.games, 0)
        self.assertEqual(red_blue.postseason.games, 0)
        self.assertEqual(red_blue.unknown.games, 1)
        self.assertEqual(red_blue.combined.games, 1)

    def test_designated_phase_separates_retrospective_bucket_without_changing_flags(self) -> None:
        snapshot, supplement = _phase_snapshot_and_supplement(
            phase="postseason", phase_evidence_ids=("fixture-source",)
        )
        validated = validate_conference_supplement(supplement, snapshot)
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 2, tzinfo=UTC),
            dataset_id="phase-postseason",
            model_version="v0.4.0",
        )
        red_blue = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(red_blue.regular.games, 0)
        self.assertEqual(red_blue.postseason.games, 1)
        self.assertEqual(red_blue.unknown.games, 0)
        self.assertEqual(red_blue.combined.games, 1)

    def test_known_snapshot_phase_conflict_is_rejected(self) -> None:
        original = _snapshot()
        supplement = _supplement(original)
        designation = next(
            item for item in supplement.games if item.provider_id == "g-cross"
        )
        conflicting = replace(
            designation,
            phase="postseason",
            phase_evidence_ids=("fixture-source",),
        )
        candidate = replace(
            supplement,
            games=tuple(
                conflicting if item.provider_id == "g-cross" else item
                for item in supplement.games
            ),
        )
        with self.assertRaisesRegex(ConferenceSourceError, "phase disagrees"):
            validate_conference_supplement(candidate, original)

    def test_regular_snapshot_phase_and_title_exclusion_remain_distinct(self) -> None:
        original = _snapshot()
        games = tuple(
            replace(game, phase="regular", phase_source="provider")
            if game.provider_id == "g-title"
            else game
            for game in original.games
        )
        provisional = replace(original, games=games, checksum="0" * 64)
        snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
        supplement = replace(_supplement(original), snapshot_checksum=snapshot.checksum)
        validated = validate_conference_supplement(supplement, snapshot)
        title = next(
            item for item in validated.supplement.games if item.provider_id == "g-title"
        )
        self.assertEqual(title.phase, "unknown")
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 2, tzinfo=UTC),
            dataset_id="title-regular-snapshot",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.standings if item.conference == "Red")
        alpha = next(row for row in red.rows if row.school == "Alpha")
        self.assertEqual(alpha.conference_record.games, 1)
        self.assertEqual(alpha.overall_record.games, 4)

    def test_unknown_snapshot_title_does_not_invent_postseason_phase(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        validated = validate_conference_supplement(supplement, snapshot)
        source_game = next(game for game in snapshot.games if game.provider_id == "g-title")
        designation = next(
            item for item in validated.supplement.games if item.provider_id == "g-title"
        )
        self.assertTrue(designation.title_game)
        self.assertEqual(designation.phase, "unknown")
        self.assertEqual(_game_phase(source_game, designation), "unknown")
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 2, tzinfo=UTC),
            dataset_id="title-unknown-phase",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.standings if item.conference == "Red")
        alpha = next(row for row in red.rows if row.school == "Alpha")
        self.assertEqual(alpha.conference_record.games, 1)
        self.assertEqual(alpha.overall_record.games, 4)

    def test_phase_evidence_must_bind_to_registry_and_known_phase(self) -> None:
        with self.assertRaisesRegex(ConferenceSourceError, "known game phase requires"):
            ConferenceGameDesignation(
                provider_id="phase-test",
                home_team="Alpha",
                away_team="Beta",
                conference_game=False,
                counts_for_standings=False,
                title_game=False,
                conference=None,
                evidence_ids=("fixture-source",),
                phase="postseason",
            )
        snapshot, supplement = _phase_snapshot_and_supplement(
            phase="postseason", phase_evidence_ids=("missing-phase",)
        )
        with self.assertRaisesRegex(ConferenceSourceError, "unknown evidence"):
            validate_conference_supplement(supplement, snapshot)

    def test_phase_fields_are_checksum_bound_and_wire_visible(self) -> None:
        snapshot, supplement = _phase_snapshot_and_supplement(
            phase="postseason", phase_evidence_ids=("fixture-source",)
        )
        sealed = replace(supplement, declared_checksum=supplement.checksum)
        validate_conference_supplement(sealed, snapshot)
        game_wire = next(
            item
            for item in _supplement_payload(sealed)["games"]
            if item["provider_id"] == "g-cross"
        )
        self.assertEqual(game_wire["phase"], "postseason")
        self.assertEqual(game_wire["phase_evidence_ids"], ["fixture-source"])

        original = next(item for item in sealed.games if item.provider_id == "g-cross")
        tampered_game = replace(original, phase="regular")
        tampered = replace(
            sealed,
            games=tuple(
                tampered_game if item.provider_id == "g-cross" else item
                for item in sealed.games
            ),
        )
        self.assertNotEqual(tampered.checksum, sealed.checksum)
        with self.assertRaisesRegex(ConferenceSourceError, "declared_checksum"):
            validate_conference_supplement(tampered, snapshot)


if __name__ == "__main__":
    unittest.main()
