"""Focused offline regressions for the schema-4 postseason repair seam."""

import unittest

from cfb.postseason_registry import (
    PINNED_REGISTRY,
    PostseasonCorrectionRegistry,
    validate_recovery_classification,
    trusted_registry,
)
from cfb.season_source import SourcePlayoff, normalize_game
from cfb.week_calendar import (
    canonical_postseason_week,
    postseason_calendar_provenance,
)


def _provider_game(**changes):
    value = {
        "id": "401677077",
        "week": 1,
        "seasonType": "postseason",
        "startDate": "2024-12-15T02:00:00+00:00",
        "homeTeam": "Western Michigan",
        "homeClassification": "fbs",
        "homePoints": 28,
        "awayTeam": "South Alabama",
        "awayClassification": "fbs",
        "awayPoints": 20,
        "neutralSite": True,
        "completed": True,
        "notes": "IS4S Salute to Veterans Bowl",
        "playoff": {
            "competition": "CFP",
            "roundName": "Bowl",
            "homeSeed": 1,
        },
    }
    return {**value, **changes}


class PostseasonCalendarRepairTests(unittest.TestCase):
    def test_fixed_windows_derive_lattice_and_fail_closed(self):
        self.assertEqual(
            canonical_postseason_week(2024, "2024-12-15T02:00:00Z"), 16
        )
        self.assertEqual(
            canonical_postseason_week(2024, "2025-01-21T00:30:00Z"), 22
        )
        self.assertEqual(
            canonical_postseason_week(2025, "2025-12-14T01:00:00Z"), 16
        )
        with self.assertRaises(ValueError):
            canonical_postseason_week(2024, "2024-12-13T23:59:59-05:00")
        self.assertEqual(
            canonical_postseason_week(2026, "2026-12-12T12:00:00-05:00"), 15
        )
        self.assertEqual(
            canonical_postseason_week(2026, "2027-01-25T12:00:00-05:00"), 22
        )
        with self.assertRaises(ValueError):
            canonical_postseason_week(2026, "2026-12-11T23:59:59-05:00")
        with self.assertRaises(ValueError):
            canonical_postseason_week(2026, "2027-01-26T00:00:00-05:00")
        provenance = postseason_calendar_provenance(2024)
        self.assertEqual(provenance["start_date"], "2024-12-14")
        self.assertEqual(provenance["end_date"], "2025-01-20")
        provenance = postseason_calendar_provenance(2026)
        self.assertEqual(provenance["start_date"], "2026-12-12")
        self.assertEqual(provenance["end_date"], "2027-01-25")

    def test_provider_phase_and_playoff_metadata_are_preserved(self):
        game = normalize_game(_provider_game(), season=2024, from_provider=True)
        self.assertEqual(game.week, 16)
        self.assertEqual(game.provider_week, 1)
        self.assertEqual(game.provider_season_type, "postseason")
        self.assertEqual(game.phase, "postseason")
        self.assertEqual(game.phase_source, "provider")
        validate_recovery_classification(2026, (game,), None)
        self.assertEqual(game.provider_playoff, SourcePlayoff(competition="CFP", round_name="Bowl", home_seed=1))

    def test_fresh_2026_provider_postseason_does_not_require_legacy_registry(self):
        game = normalize_game(
            _provider_game(
                id="401900001",
                startDate="2026-12-15T17:30:00-05:00",
                notes="IS4S Salute to Veterans Bowl",
            ),
            season=2026,
            from_provider=True,
        )
        self.assertEqual(game.week, 16)
        self.assertEqual(game.provider_id, "401900001")
        self.assertEqual(game.provider_season_type, "postseason")
        self.assertEqual(game.phase, "postseason")
        self.assertEqual(game.phase_source, "provider")

    def test_provider_phase_is_required_and_unknown_phase_fails_closed(self):
        for value in (None, "", "spring_postseason", "allstar"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "seasonType"):
                normalize_game(_provider_game(seasonType=value), season=2024, from_provider=True)

    def test_pinned_registry_rejects_stale_target_or_identity_changes(self):
        self.assertEqual(len(PINNED_REGISTRY.entries), 92)
        first = PINNED_REGISTRY.entries[0]
        self.assertEqual(first.original_canonical_week, 1)
        self.assertEqual(first.canonical_week, 16)
        payload = PINNED_REGISTRY.payload()
        payload["entries"][0]["canonical_week"] = 1
        with self.assertRaisesRegex(ValueError, "calendar-derived"):
            PostseasonCorrectionRegistry.from_payload({**payload, "checksum": None})
        payload = PINNED_REGISTRY.payload()
        payload["entries"][0]["notes"] = "tampered"
        changed = PostseasonCorrectionRegistry.from_payload({**payload, "checksum": None})
        with self.assertRaisesRegex(ValueError, "not code-pinned"):
            trusted_registry(changed)

    def test_registry_checksum_round_trip_is_deterministic(self):
        restored = PostseasonCorrectionRegistry.from_payload(PINNED_REGISTRY.payload())
        self.assertEqual(restored.computed_checksum, PINNED_REGISTRY.computed_checksum)
        self.assertEqual(restored.payload(), PINNED_REGISTRY.payload())


if __name__ == "__main__":
    unittest.main()
