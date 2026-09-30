from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from cfb.conference_sources import (
    ChampionshipRule,
    ConferenceGameDesignation,
    ConferenceMember,
    ConferenceRule,
    ConferenceSourceError,
    ConferenceSupplement,
    DatedConfirmation,
    EligibilityRule,
    SitePolicy,
    SourceEvidence,
    snapshot_content_checksum,
    validate_conference_supplement,
)
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam


UTC = timezone.utc
KNOWN = datetime(2025, 2, 1, tzinfo=UTC)


def _game(
    provider_id: str,
    home: str,
    away: str,
    home_points: int | None,
    away_points: int | None,
    *,
    week: int = 0,
    home_classification: str = "FBS",
    away_classification: str = "FBS",
    phase: str | None = "regular",
    completed: bool | None = True,
) -> SourceGame:
    return SourceGame(
        week=week,
        home_team=home,
        home_classification=home_classification,
        home_points=home_points,
        away_team=away,
        away_classification=away_classification,
        away_points=away_points,
        neutral_site=False,
        provider_id=provider_id,
        date="2025-09-01",
        completed=completed,
        disposition="completed" if completed else "scheduled",
        phase=phase,
        phase_source="provider" if phase else None,
    )


def _snapshot(*, add_unknown_fbs: bool = False) -> SeasonSnapshot:
    teams = (
        SourceTeam("Alpha", "Red"),
        SourceTeam("Beta", "Red"),
        SourceTeam("Gamma", "Blue"),
        SourceTeam("Delta", "Blue"),
        SourceTeam("Independent", "FBS Independents"),
    )
    games = (
        _game("g-red", "Alpha", "Beta", 21, 14),
        _game("g-cross", "Alpha", "Gamma", 17, 10),
        _game("g-ind", "Beta", "Independent", 24, 7),
        _game("g-fcs", "Alpha", "FCS U", 31, 7, away_classification="FCS"),
        _game("g-title", "Alpha", "Beta", 28, 24, phase=None),
        _game("g-future", "Gamma", "Delta", None, None, week=1, completed=False),
        _game("g-post", "Alpha", "Gamma", 14, 10, week=1, phase="postseason"),
    )
    if add_unknown_fbs:
        games = games + (_game("g-unknown", "Alpha", "Unknown FBS", 7, 3),)
    metadata = {
        "schema_version": 4,
        "teams_fetched_at": "2026-01-01T00:00:00+00:00",
        "games_fetched_at": "2026-01-01T00:00:00+00:00",
        "complete_through_week": 0,
    }
    provisional = SeasonSnapshot(
        sport="cfb",
        classification="FBS",
        year=2025,
        teams=teams,
        games=games,
        metadata=metadata,
        checksum="0" * 64,
    )
    return replace(provisional, checksum=snapshot_content_checksum(provisional))


def _evidence() -> SourceEvidence:
    return SourceEvidence(
        evidence_id="fixture-source",
        url="https://example.test/fixture.pdf",
        source_sha256="b" * 64,
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
        effective_from=date(2025, 1, 1),
        known_at=KNOWN,
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _supplement(snapshot: SeasonSnapshot, *, omit_game: str | None = None) -> ConferenceSupplement:
    evidence = (_evidence(),)
    members = (
        ConferenceMember("Alpha", "Red", False, ("fixture-source",)),
        ConferenceMember("Beta", "Red", False, ("fixture-source",)),
        ConferenceMember("Gamma", "Blue", False, ("fixture-source",)),
        ConferenceMember("Delta", "Blue", False, ("fixture-source",)),
        ConferenceMember("Independent", None, True, ("fixture-source",)),
    )
    games = []
    for source_game in snapshot.games:
        provider_id = source_game.provider_id
        if provider_id == omit_game:
            continue
        same_red = {source_game.home_team, source_game.away_team} == {"Alpha", "Beta"}
        games.append(
            ConferenceGameDesignation(
                provider_id=provider_id,
                home_team=source_game.home_team,
                away_team=source_game.away_team,
                conference_game=same_red and provider_id == "g-red" or provider_id == "g-title",
                counts_for_standings=provider_id == "g-red",
                title_game=provider_id == "g-title",
                conference="Red" if same_red else None,
                evidence_ids=("fixture-source",),
            )
        )
    rules = (
        ConferenceRule(
            conference="Red",
            standings_criterion="winning_percentage",
            championship=ChampionshipRule(
                selection="top_two",
                eligibility=(
                    EligibilityRule("Alpha", True, ("fixture-source",)),
                    EligibilityRule("Beta", True, ("fixture-source",)),
                ),
                divisions=(),
                site=SitePolicy("seed_hosted", None, ("fixture-source",)),
                evidence_ids=("fixture-source",),
            ),
            evidence_ids=("fixture-source",),
        ),
        ConferenceRule(
            conference="Blue",
            standings_criterion="winning_percentage",
            championship=ChampionshipRule(
                selection="top_two",
                eligibility=(
                    EligibilityRule("Gamma", True, ("fixture-source",)),
                    EligibilityRule("Delta", True, ("fixture-source",)),
                ),
                divisions=(),
                site=SitePolicy("neutral", None, ("fixture-source",)),
                evidence_ids=("fixture-source",),
            ),
            evidence_ids=("fixture-source",),
        ),
    )
    return ConferenceSupplement(
        season=2025,
        snapshot_checksum=snapshot.checksum,
        content_identity="fixture-conference-v1",
        evidence=evidence,
        members=members,
        games=tuple(games),
        rules=rules,
    )


class ConferenceSourcesTests(unittest.TestCase):
    def test_valid_supplement_binds_exact_snapshot_content(self) -> None:
        snapshot = _snapshot()
        self.assertEqual(snapshot.checksum, snapshot_content_checksum(snapshot))
        supplement = _supplement(snapshot)
        validated = validate_conference_supplement(supplement, snapshot)
        self.assertEqual(validated.snapshot_checksum, snapshot.checksum)
        self.assertEqual(validated.supplement_checksum, supplement.checksum)
        self.assertEqual(validated.content_identity, "fixture-conference-v1")

    def test_missing_game_designation_is_not_complete_coverage(self) -> None:
        snapshot = _snapshot()
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                _supplement(snapshot, omit_game="g-cross"), snapshot
            )

    def test_fcs_opponent_is_allowed_but_unknown_fbs_is_rejected(self) -> None:
        snapshot = _snapshot()
        validate_conference_supplement(_supplement(snapshot), snapshot)
        unknown_snapshot = _snapshot(add_unknown_fbs=True)
        unknown = _supplement(unknown_snapshot)
        unknown_games = unknown.games + (
            ConferenceGameDesignation(
                provider_id="g-unknown",
                home_team="Alpha",
                away_team="Unknown FBS",
                conference_game=False,
                counts_for_standings=False,
                title_game=False,
                conference=None,
                evidence_ids=("fixture-source",),
            ),
        )
        unknown = replace(unknown, games=unknown_games, snapshot_checksum=unknown_snapshot.checksum)
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(unknown, unknown_snapshot)

    def test_designation_must_match_snapshot_home_and_away_identity(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        tampered = replace(
            supplement,
            games=tuple(
                replace(item, away_team="Delta") if item.provider_id == "g-cross" else item
                for item in supplement.games
            ),
        )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(tampered, snapshot)

    def test_stored_or_supplement_snapshot_checksum_tampering_fails_closed(self) -> None:
        snapshot = _snapshot()
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                _supplement(snapshot), replace(snapshot, checksum="0" * 64)
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(_supplement(snapshot), snapshot_checksum="1" * 64), snapshot
            )

    def test_schema_four_provenance_is_part_of_snapshot_content_identity(self) -> None:
        snapshot = _snapshot()
        with_provenance = replace(
            snapshot,
            metadata={
                **snapshot.metadata,
                "calendar_provenance": {"fixture": "calendar-a"},
                "correction_registry_provenance": {"fixture": "registry-a"},
                "migration_provenance": {"fixture": "migration-a"},
            },
        )
        self.assertNotEqual(
            snapshot_content_checksum(snapshot),
            snapshot_content_checksum(with_provenance),
        )

    def test_confirmation_cannot_claim_knowledge_before_its_evidence(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        confirmation = DatedConfirmation(
            confirmation_id="red-final",
            conference="Red",
            participants=("Alpha", "Beta"),
            known_at=datetime(2025, 1, 1, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, confirmations=(confirmation,)), snapshot
            )


if __name__ == "__main__":
    unittest.main()
