"""Independent checks for phase-qualified conference source records."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import unittest

from cfb.conference_inputs import ConferenceInputError, parse_conference_supplement
from cfb.conference_reference import (
    CheckpointRanking,
    ConferenceReferenceError,
    Record,
    _game_phase,
    derive_conference_reference,
)
from cfb.conference_sources import (
    ChampionshipRule,
    ConferenceGameDesignation,
    ConferenceMember,
    ConferenceRule,
    ConferenceSourceError,
    ConferenceSupplement,
    EligibilityRule,
    SitePolicy,
    SourceEvidence,
    _supplement_payload,
    snapshot_content_checksum,
    validate_conference_supplement,
)
from cfb.season_snapshot import SeasonSnapshot
from cfb.season_source import SourceGame, SourceTeam


UTC = timezone.utc
CUTOFF = datetime(2025, 9, 10, tzinfo=UTC)


def _game(
    provider_id: str,
    home: str,
    away: str,
    home_points: int | None,
    away_points: int | None,
    *,
    phase: str | None,
    date_text: str = "2025-09-01",
    completed: bool = True,
) -> SourceGame:
    qualified_phase = phase if phase in {"regular", "postseason"} else None
    return SourceGame(
        week=0,
        home_team=home,
        home_classification="FBS",
        home_points=home_points,
        away_team=away,
        away_classification="FBS",
        away_points=away_points,
        neutral_site=False,
        provider_id=provider_id,
        date=date_text,
        completed=completed,
        disposition="completed" if completed else "scheduled",
        provider_season_type=qualified_phase,
        phase=phase,
        phase_source="provider" if qualified_phase is not None else None,
    )


def _seal_snapshot(
    teams: tuple[SourceTeam, ...], games: tuple[SourceGame, ...]
) -> SeasonSnapshot:
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


def _designation(
    provider_id: str,
    home: str,
    away: str,
    *,
    conference_game: bool = False,
    counts_for_standings: bool = False,
    title_game: bool = False,
    conference: str | None = None,
    evidence_ids: tuple[str, ...] = ("phase-source",),
    phase: str = "unknown",
    phase_evidence_ids: tuple[str, ...] = (),
) -> ConferenceGameDesignation:
    return ConferenceGameDesignation(
        provider_id=provider_id,
        home_team=home,
        away_team=away,
        conference_game=conference_game,
        counts_for_standings=counts_for_standings,
        title_game=title_game,
        conference=conference,
        evidence_ids=evidence_ids,
        phase=phase,
        phase_evidence_ids=phase_evidence_ids,
    )


def _rule(conference: str, teams: tuple[str, ...]) -> ConferenceRule:
    evidence_ids = ("phase-source",)
    return ConferenceRule(
        conference=conference,
        standings_criterion="winning_percentage",
        championship=ChampionshipRule(
            selection="top_two",
            eligibility=tuple(
                EligibilityRule(team, True, evidence_ids) for team in teams
            ),
            divisions=(),
            site=SitePolicy("neutral", None, evidence_ids),
            evidence_ids=evidence_ids,
        ),
        evidence_ids=evidence_ids,
    )


def _fixture() -> tuple[SeasonSnapshot, ConferenceSupplement]:
    teams = (
        SourceTeam("Red One", "Red"),
        SourceTeam("Red Two", "Red"),
        SourceTeam("Blue One", "Blue"),
        SourceTeam("Blue Two", "Blue"),
        SourceTeam("Independent", "FBS Independents"),
    )
    games = (
        _game("red-regular", "Red One", "Red Two", 21, 14, phase="regular"),
        _game("red-title", "Red One", "Red Two", 28, 24, phase="regular"),
        _game("cross-regular", "Red One", "Blue One", 17, 10, phase="regular"),
        _game("cross-postseason", "Red Two", "Blue Two", 14, 21, phase="postseason"),
        _game("cross-unknown", "Red One", "Blue Two", 7, 3, phase="unknown"),
    )
    snapshot = _seal_snapshot(teams, games)
    supplement = ConferenceSupplement(
        season=2025,
        snapshot_checksum=snapshot.checksum,
        content_identity="phase-independent-v1",
        evidence=(
            SourceEvidence(
                evidence_id="phase-source",
                url="https://example.test/phase-source.pdf",
                source_sha256="a" * 64,
                published_at=datetime(2025, 1, 1, tzinfo=UTC),
                effective_from=date(2025, 1, 1),
                known_at=datetime(2025, 8, 1, tzinfo=UTC),
                retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
            ),
        ),
        members=(
            ConferenceMember("Red One", "Red", False, ("phase-source",)),
            ConferenceMember("Red Two", "Red", False, ("phase-source",)),
            ConferenceMember("Blue One", "Blue", False, ("phase-source",)),
            ConferenceMember("Blue Two", "Blue", False, ("phase-source",)),
            ConferenceMember("Independent", None, True, ("phase-source",)),
        ),
        games=(
            _designation(
                "red-regular", "Red One", "Red Two",
                conference_game=True, counts_for_standings=True, conference="Red",
                phase="regular", phase_evidence_ids=("phase-source",),
            ),
            _designation(
                "red-title", "Red One", "Red Two",
                conference_game=True, title_game=True, conference="Red",
                phase="regular", phase_evidence_ids=("phase-source",),
            ),
            _designation(
                "cross-regular", "Red One", "Blue One",
                phase="regular", phase_evidence_ids=("phase-source",),
            ),
            _designation(
                "cross-postseason", "Red Two", "Blue Two",
                phase="postseason", phase_evidence_ids=("phase-source",),
            ),
            _designation("cross-unknown", "Red One", "Blue Two"),
        ),
        rules=(
            _rule("Red", ("Red One", "Red Two")),
            _rule("Blue", ("Blue One", "Blue Two")),
        ),
    )
    return snapshot, supplement


def _rankings() -> tuple[CheckpointRanking, ...]:
    return (
        CheckpointRanking("Red One", 80.0, 1),
        CheckpointRanking("Red Two", 70.0, 2),
        CheckpointRanking("Blue One", 60.0, 3),
        CheckpointRanking("Blue Two", 50.0, 4),
        CheckpointRanking("Independent", 40.0, 5),
    )


def _derive(
    snapshot: SeasonSnapshot,
    supplement: ConferenceSupplement,
    *,
    cutoff: datetime = CUTOFF,
):
    return derive_conference_reference(
        snapshot,
        validate_conference_supplement(supplement, snapshot),
        _rankings(),
        phase="week",
        target_week=0,
        cutoff=cutoff,
        dataset_id="phase-independent",
        model_version="phase-v1",
    )


def _wire(supplement: ConferenceSupplement) -> dict[str, object]:
    return {"supplement": _supplement_payload(supplement), "checksum": supplement.checksum}


class IndependentConferencePhaseTests(unittest.TestCase):
    def test_unknown_phase_without_evidence_stays_unknown_and_title_does_not_count(self):
        snapshot, supplement = _fixture()
        reference = _derive(snapshot, supplement)
        cross = next(
            item
            for item in reference.interconference
            if item.conference == "Red"
            and item.opponent == "Blue"
            and item.opponent_kind == "conference"
        )
        self.assertEqual(cross.regular, Record(1, 0, 0))
        self.assertEqual(cross.postseason, Record(0, 1, 0))
        self.assertEqual(cross.unknown, Record(1, 0, 0))
        self.assertEqual(cross.combined, Record(2, 1, 0))

        red = next(item for item in reference.standings if item.conference == "Red")
        red_one = next(row for row in red.rows if row.school == "Red One")
        red_two = next(row for row in red.rows if row.school == "Red Two")
        self.assertEqual(red_one.conference_record, Record(1, 0, 0))
        self.assertEqual(red_two.conference_record, Record(0, 1, 0))
        unknown_game = next(game for game in snapshot.games if game.provider_id == "cross-unknown")
        self.assertEqual(unknown_game.phase, "unknown")
        self.assertIsNone(unknown_game.phase_source)

    def test_title_marker_does_not_promote_unknown_snapshot_phase_to_postseason(self):
        snapshot, supplement = _fixture()
        unknown_games = tuple(
            replace(
                game,
                phase=None,
                phase_source=None,
                provider_season_type=None,
            )
            if game.provider_id == "red-title" else game
            for game in snapshot.games
        )
        unknown_snapshot = _seal_snapshot(snapshot.teams, unknown_games)
        unknown_title = replace(
            supplement.games[1], phase="unknown", phase_evidence_ids=()
        )
        unknown_supplement = replace(
            supplement,
            snapshot_checksum=unknown_snapshot.checksum,
            games=(supplement.games[0], unknown_title) + supplement.games[2:],
        )
        validate_conference_supplement(unknown_supplement, unknown_snapshot)
        source_game = next(
            game for game in unknown_snapshot.games if game.provider_id == "red-title"
        )
        self.assertEqual(_game_phase(source_game, unknown_title), "unknown")
        reference = _derive(unknown_snapshot, unknown_supplement)
        red = next(item for item in reference.standings if item.conference == "Red")
        self.assertEqual(
            next(row for row in red.rows if row.school == "Red One").conference_record,
            Record(1, 0, 0),
        )

    def test_provider_home_away_and_evidence_registry_are_exactly_bound(self):
        snapshot, supplement = _fixture()
        original = supplement.games[0]
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, games=(replace(original, provider_id="not-in-snapshot"),) + supplement.games[1:]),
                snapshot,
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, games=(replace(original, home_team="Blue One"),) + supplement.games[1:]),
                snapshot,
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, games=(replace(original, evidence_ids=("missing-evidence",)),) + supplement.games[1:]),
                snapshot,
            )

    def test_resealing_different_snapshot_phase_cannot_reuse_old_evidence(self):
        snapshot, supplement = _fixture()
        parsed = parse_conference_supplement(_wire(supplement), snapshot)
        self.assertEqual(parsed.supplement_checksum, supplement.checksum)

        changed_games = tuple(
            replace(
                game,
                phase="postseason",
                phase_source="provider",
                provider_season_type="postseason",
            )
            if game.provider_id == "cross-regular" else game
            for game in snapshot.games
        )
        changed_snapshot = _seal_snapshot(snapshot.teams, changed_games)
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(supplement, changed_snapshot)

        changed_designation = replace(
            supplement.games[0],
            phase="postseason",
            phase_evidence_ids=("phase-source",),
        )
        forged_wire = _wire(
            replace(
                supplement,
                games=(changed_designation,) + supplement.games[1:],
            )
        )
        with self.assertRaises(ConferenceInputError):
            parse_conference_supplement(forged_wire, snapshot)

    def test_incomplete_scope_fails_closed_instead_of_becoming_unknown(self):
        snapshot, supplement = _fixture()
        incomplete_game = _game(
            "incomplete-unknown",
            "Blue One",
            "Blue Two",
            None,
            None,
            phase=None,
            completed=False,
        )
        incomplete_snapshot = _seal_snapshot(snapshot.teams, snapshot.games + (incomplete_game,))
        incomplete_supplement = replace(
            supplement,
            snapshot_checksum=incomplete_snapshot.checksum,
            games=supplement.games
            + (_designation("incomplete-unknown", "Blue One", "Blue Two"),),
        )
        with self.assertRaises(ConferenceReferenceError):
            _derive(incomplete_snapshot, incomplete_supplement)

    def test_cutoff_timing_only_withholds_projection_and_keeps_retrospective_records(self):
        snapshot, supplement = _fixture()
        changed_games = tuple(
            replace(game, date="2025-09-09")
            if game.provider_id == "red-regular" else game
            for game in snapshot.games
        )
        timed_snapshot = _seal_snapshot(snapshot.teams, changed_games)
        timed_supplement = replace(supplement, snapshot_checksum=timed_snapshot.checksum)
        reference = _derive(timed_snapshot, timed_supplement)
        red_projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(red_projection.status, "unavailable")
        self.assertEqual(red_projection.reason, "ambiguous_game_timing")
        red = next(item for item in reference.standings if item.conference == "Red")
        self.assertEqual(
            next(row for row in red.rows if row.school == "Red One").conference_record,
            Record(1, 0, 0),
        )
        cross = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(cross.regular, Record(1, 0, 0))
        self.assertEqual(cross.postseason, Record(0, 1, 0))


if __name__ == "__main__":
    unittest.main()
