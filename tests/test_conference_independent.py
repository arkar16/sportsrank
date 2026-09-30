"""Independent acceptance checks for snapshot-bound conference references.

These fixtures specify the expected records and rating arithmetic directly.
They do not derive expected values from the conference implementation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
import unittest

from cfb.conference_reference import (
    CheckpointRanking,
    ConferenceReferenceError,
    Record,
    derive_conference_reference,
)
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
from cfb.public_safety import assert_public_bytes


UTC = timezone.utc
EARLY_CUTOFF = datetime(2025, 9, 10, tzinfo=UTC)


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
    completed: bool = True,
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


def _seal_snapshot(
    teams: tuple[SourceTeam, ...],
    games: tuple[SourceGame, ...],
    *,
    complete_through_week: int,
) -> SeasonSnapshot:
    metadata = {
        "schema_version": 4,
        "teams_fetched_at": "2026-01-01T00:00:00+00:00",
        "games_fetched_at": "2026-01-01T00:00:00+00:00",
        "complete_through_week": complete_through_week,
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


def _evidence(
    evidence_id: str = "fixture-source",
    *,
    known_at: datetime = datetime(2025, 8, 1, tzinfo=UTC),
) -> SourceEvidence:
    return SourceEvidence(
        evidence_id=evidence_id,
        url=f"https://example.test/{evidence_id}.pdf",
        source_sha256=("b" if evidence_id == "fixture-source" else "c") * 64,
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
        effective_from=date(2025, 1, 1),
        known_at=known_at,
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _rule(
    conference: str,
    teams: tuple[str, ...],
    *,
    evidence_id: str = "fixture-source",
    site_mode: str = "neutral",
) -> ConferenceRule:
    evidence_ids = (evidence_id,)
    return ConferenceRule(
        conference=conference,
        standings_criterion="winning_percentage",
        championship=ChampionshipRule(
            selection="top_two",
            eligibility=tuple(
                EligibilityRule(team, True, evidence_ids) for team in teams
            ),
            divisions=(),
            site=SitePolicy(site_mode, None, evidence_ids),
            evidence_ids=evidence_ids,
        ),
        evidence_ids=evidence_ids,
    )


def _designation(
    provider_id: str,
    home: str,
    away: str,
    *,
    conference_game: bool = False,
    counts_for_standings: bool = False,
    title_game: bool = False,
    conference: str | None = None,
) -> ConferenceGameDesignation:
    return ConferenceGameDesignation(
        provider_id=provider_id,
        home_team=home,
        away_team=away,
        conference_game=conference_game,
        counts_for_standings=counts_for_standings,
        title_game=title_game,
        conference=conference,
        evidence_ids=("fixture-source",),
    )


def _core_fixture() -> tuple[SeasonSnapshot, ConferenceSupplement]:
    teams = (
        SourceTeam("Red One", "Red"),
        SourceTeam("Red Two", "Red"),
        SourceTeam("Blue One", "Blue"),
        SourceTeam("Blue Two", "Blue"),
        SourceTeam("Independent", "FBS Independents"),
    )
    games = (
        _game("red-league", "Red One", "Red Two", 21, 14),
        _game("red-title", "Red One", "Red Two", 28, 24, phase=None),
        _game("red-blue", "Red One", "Blue One", 17, 10),
        _game("red-independent", "Red Two", "Independent", 24, 7),
        _game(
            "red-fcs",
            "Red One",
            "FCS U",
            31,
            7,
            away_classification="FCS",
        ),
        _game("blue-nonleague", "Blue One", "Blue Two", 27, 20),
        _game(
            "future-blue",
            "Blue One",
            "Blue Two",
            None,
            None,
            week=1,
            completed=False,
        ),
    )
    snapshot = _seal_snapshot(teams, games, complete_through_week=0)
    supplement = ConferenceSupplement(
        season=2025,
        snapshot_checksum=snapshot.checksum,
        content_identity="independent-conference-fixture-v1",
        evidence=(_evidence(),),
        members=(
            ConferenceMember("Red One", "Red", False, ("fixture-source",)),
            ConferenceMember("Red Two", "Red", False, ("fixture-source",)),
            ConferenceMember("Blue One", "Blue", False, ("fixture-source",)),
            ConferenceMember("Blue Two", "Blue", False, ("fixture-source",)),
            ConferenceMember("Independent", None, True, ("fixture-source",)),
        ),
        games=(
            _designation(
                "red-league", "Red One", "Red Two", conference_game=True,
                counts_for_standings=True, conference="Red"
            ),
            _designation(
                "red-title", "Red One", "Red Two", conference_game=True,
                title_game=True, conference="Red"
            ),
            _designation("red-blue", "Red One", "Blue One"),
            _designation("red-independent", "Red Two", "Independent"),
            _designation("red-fcs", "Red One", "FCS U"),
            _designation("blue-nonleague", "Blue One", "Blue Two"),
            _designation("future-blue", "Blue One", "Blue Two"),
        ),
        rules=(
            _rule("Red", ("Red One", "Red Two"), site_mode="seed_hosted"),
            _rule("Blue", ("Blue One", "Blue Two")),
        ),
    )
    return snapshot, supplement


def _rankings(*, missing_red_one: bool = False) -> tuple[CheckpointRanking, ...]:
    return (
        CheckpointRanking(
            "Red One", None if missing_red_one else 80.0,
            None if missing_red_one else 1,
        ),
        CheckpointRanking("Red Two", 70.0, 2),
        CheckpointRanking("Blue One", 60.0, 3),
        CheckpointRanking("Blue Two", 50.0, 4),
        CheckpointRanking("Independent", 40.0, 5),
    )


def _validated(
    snapshot: SeasonSnapshot,
    supplement: ConferenceSupplement,
):
    return validate_conference_supplement(supplement, snapshot)


def _derive(
    snapshot: SeasonSnapshot,
    supplement: ConferenceSupplement,
    rankings: tuple[CheckpointRanking, ...] | None = None,
    *,
    cutoff: datetime | None = EARLY_CUTOFF,
    phase: str = "week",
    target_week: int | None = 0,
):
    return derive_conference_reference(
        snapshot,
        _validated(snapshot, supplement),
        rankings or _rankings(),
        phase=phase,
        target_week=target_week,
        cutoff=cutoff,
        dataset_id="independent-conference-fixture",
        model_version="v0.4.0",
    )


class IndependentConferenceTests(unittest.TestCase):
    def test_source_contract_requires_complete_roster_games_and_exact_snapshot(self):
        snapshot, supplement = _core_fixture()
        validated = _validated(snapshot, supplement)
        self.assertEqual(validated.snapshot_checksum, snapshot.checksum)
        self.assertEqual(validated.supplement_checksum, supplement.checksum)

        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, members=supplement.members[:-1]), snapshot
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, games=supplement.games[:-1]), snapshot
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                supplement, replace(snapshot, checksum="0" * 64)
            )
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(
                replace(supplement, snapshot_checksum="1" * 64), snapshot
            )

        mutated_games = tuple(
            replace(game, home_points=22)
            if game.provider_id == "red-league" else game
            for game in snapshot.games
        )
        mutated_snapshot = replace(snapshot, games=mutated_games)
        with self.assertRaises(ConferenceSourceError):
            validate_conference_supplement(supplement, mutated_snapshot)
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                mutated_snapshot,
                validated,
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=EARLY_CUTOFF,
                dataset_id="independent-conference-fixture",
                model_version="v0.4.0",
            )

    def test_records_separate_fcs_independent_nonleague_and_future_games(self):
        snapshot, supplement = _core_fixture()
        reference = _derive(snapshot, supplement)
        red = next(item for item in reference.standings if item.conference == "Red")
        blue = next(item for item in reference.standings if item.conference == "Blue")
        red_one = next(row for row in red.rows if row.school == "Red One")
        red_two = next(row for row in red.rows if row.school == "Red Two")
        blue_one = next(row for row in blue.rows if row.school == "Blue One")

        self.assertEqual(red_one.conference_record, Record(1, 0, 0))
        self.assertEqual(red_one.overall_record, Record(4, 0, 0))
        self.assertEqual(red_two.conference_record, Record(0, 1, 0))
        self.assertEqual(red_two.overall_record, Record(1, 2, 0))
        self.assertEqual(blue_one.conference_record, Record())
        self.assertEqual(blue_one.overall_record, Record(1, 1, 0))

        red_blue = next(
            item for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(red_blue.regular, Record(1, 0, 0))
        self.assertEqual(red_blue.postseason, Record())
        self.assertEqual(red_blue.combined, Record(1, 0, 0))
        red_fcs = next(
            item for item in reference.interconference
            if item.conference == "Red" and item.opponent == "FCS"
        )
        self.assertEqual(red_fcs.opponent_kind, "fcs")
        self.assertEqual(red_fcs.combined, Record(1, 0, 0))
        red_independent = next(
            item for item in reference.interconference
            if item.conference == "Red" and item.opponent == "FBS Independents"
        )
        self.assertEqual(red_independent.opponent_kind, "independent")
        self.assertEqual(red_independent.combined, Record(1, 0, 0))
        self.assertEqual([row.school for row in reference.independent_rows], ["Independent"])
        self.assertFalse(any(row.school == "FCS U" for row in reference.independent_rows))

    def test_derived_public_json_keeps_independents_separate_from_comparisons(self):
        snapshot, supplement = _core_fixture()
        reference = _derive(snapshot, supplement)

        self.assertEqual(
            [row.school for row in reference.independent_rows], ["Independent"]
        )
        self.assertNotIn(
            "FBS Independents",
            [item.conference for item in reference.comparisons],
        )

        payload = json.dumps(
            reference.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert_public_bytes(
            "cfb/years/2025/conferences/2025_FBS_conferences.json", payload
        )

    def test_literal_rating_distribution_and_missing_rating_do_not_shrink_population(self):
        teams = tuple(SourceTeam(f"Depth {index}", "Depth") for index in range(8))
        games = (
            _game("depth-future", "Depth 0", "Depth 1", None, None, completed=False),
        )
        snapshot = _seal_snapshot(teams, games, complete_through_week=-1)
        members = tuple(
            ConferenceMember(team.school, "Depth", False, ("fixture-source",))
            for team in teams
        )
        supplement = ConferenceSupplement(
            season=2025,
            snapshot_checksum=snapshot.checksum,
            content_identity="depth-fixture-v1",
            evidence=(_evidence(),),
            members=members,
            games=(
                _designation(
                    "depth-future", "Depth 0", "Depth 1",
                    conference_game=True, counts_for_standings=True,
                    conference="Depth"
                ),
            ),
            rules=(_rule("Depth", tuple(team.school for team in teams)),),
        )
        rankings = tuple(
            CheckpointRanking(team.school, float((index + 1) * 10), index + 1)
            for index, team in enumerate(teams)
        )
        reference = _derive(
            snapshot, supplement, rankings, phase="preseason", target_week=None,
            cutoff=datetime(2025, 8, 1, tzinfo=UTC),
        )
        comparison = reference.comparisons[0]
        self.assertTrue(comparison.available)
        self.assertEqual(comparison.member_count, 8)
        self.assertEqual(comparison.mean, 45.0)
        self.assertEqual(comparison.median, 45.0)
        self.assertEqual(comparison.top_count, 2)
        self.assertEqual(comparison.top_mean, 75.0)
        self.assertEqual(comparison.remainder_count, 6)
        self.assertEqual(comparison.remainder_mean, 35.0)
        self.assertEqual(comparison.top_gap, 40.0)

        snapshot, supplement = _core_fixture()
        reference = _derive(snapshot, supplement, _rankings(missing_red_one=True))
        red_comparison = next(
            item for item in reference.comparisons if item.conference == "Red"
        )
        self.assertFalse(red_comparison.available)
        self.assertEqual(red_comparison.reason, "missing_rating")
        self.assertEqual(red_comparison.member_count, 2)

    def test_two_way_tie_fills_berths_but_three_way_tie_stays_unresolved(self):
        snapshot, supplement = _core_fixture()
        tied_games = tuple(
            replace(game, home_points=14, away_points=14)
            if game.provider_id == "red-league" else game
            for game in snapshot.games
        )
        tied_snapshot = _seal_snapshot(snapshot.teams, tied_games, complete_through_week=0)
        tied_supplement = replace(supplement, snapshot_checksum=tied_snapshot.checksum)
        two_way = _derive(tied_snapshot, tied_supplement)
        two_way_projection = next(
            item for item in two_way.projections if item.conference == "Red"
        )
        self.assertEqual(two_way_projection.status, "projected")
        self.assertEqual(two_way_projection.participants, ("Red One", "Red Two"))
        self.assertEqual(two_way_projection.site_state, "unresolved")

        teams = (
            SourceTeam("Red One", "Red"),
            SourceTeam("Red Two", "Red"),
            SourceTeam("Red Three", "Red"),
        )
        games = (
            _game("tie-12", "Red One", "Red Two", 14, 14),
            _game("tie-13", "Red One", "Red Three", 14, 14),
            _game("tie-23", "Red Two", "Red Three", 14, 14),
        )
        three_snapshot = _seal_snapshot(teams, games, complete_through_week=0)
        three_supplement = ConferenceSupplement(
            season=2025,
            snapshot_checksum=three_snapshot.checksum,
            content_identity="three-way-tie-v1",
            evidence=(_evidence(),),
            members=tuple(
                ConferenceMember(team.school, "Red", False, ("fixture-source",))
                for team in teams
            ),
            games=tuple(
                _designation(
                    game.provider_id, game.home_team, game.away_team,
                    conference_game=True, counts_for_standings=True,
                    conference="Red"
                )
                for game in games
            ),
            rules=(_rule("Red", tuple(team.school for team in teams), site_mode="neutral"),),
        )
        three_rankings = tuple(
            CheckpointRanking(name, cors, rank)
            for name, cors, rank in (
                ("Red One", 80.0, 1),
                ("Red Two", 70.0, 2),
                ("Red Three", 60.0, 3),
            )
        )
        three_way = _derive(three_snapshot, three_supplement, three_rankings)
        projection = three_way.projections[0]
        self.assertEqual(projection.status, "unresolved")
        self.assertEqual(projection.participants, ())
        self.assertEqual(
            projection.contenders, ("Red One", "Red Three", "Red Two")
        )

    def test_confirmation_and_rule_evidence_are_limited_by_cutoff_and_future_games_reject(self):
        snapshot, supplement = _core_fixture()
        confirmation = DatedConfirmation(
            confirmation_id="red-final",
            conference="Red",
            participants=("Red One", "Red Two"),
            known_at=datetime(2025, 10, 1, tzinfo=UTC),
            evidence_ids=("fixture-source",),
        )
        confirmed_supplement = replace(supplement, confirmations=(confirmation,))
        early = _derive(snapshot, confirmed_supplement, cutoff=EARLY_CUTOFF)
        late = _derive(
            snapshot,
            confirmed_supplement,
            cutoff=datetime(2025, 10, 1, tzinfo=UTC),
        )
        early_projection = next(item for item in early.projections if item.conference == "Red")
        late_projection = next(item for item in late.projections if item.conference == "Red")
        self.assertEqual(early_projection.status, "projected")
        self.assertIsNone(early_projection.confirmation_id)
        self.assertEqual(late_projection.status, "confirmed")
        self.assertEqual(late_projection.confirmation_id, "red-final")

        future_evidence = _evidence(
            "future-rule", known_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        future_rule = _rule(
            "Red", ("Red One", "Red Two"),
            evidence_id="future-rule", site_mode="seed_hosted",
        )
        future_supplement = replace(
            supplement,
            evidence=(supplement.evidence[0], future_evidence),
            rules=(future_rule, supplement.rules[1]),
        )
        unavailable = _derive(snapshot, future_supplement, cutoff=EARLY_CUTOFF)
        unavailable_projection = next(
            item for item in unavailable.projections if item.conference == "Red"
        )
        self.assertEqual(unavailable_projection.status, "unavailable")
        self.assertEqual(unavailable_projection.participants, ())

        with self.assertRaises(ConferenceReferenceError):
            _derive(snapshot, supplement, target_week=1)

    def test_projection_cutoff_requires_membership_and_selected_game_evidence(self):
        snapshot, supplement = _core_fixture()
        cutoff = EARLY_CUTOFF
        baseline = _derive(snapshot, supplement, cutoff=cutoff)
        future_evidence = _evidence(
            "future-cutoff", known_at=datetime(2025, 10, 1, tzinfo=UTC)
        )
        with_future = replace(
            supplement, evidence=supplement.evidence + (future_evidence,)
        )

        future_membership = replace(
            with_future,
            members=tuple(
                replace(member, evidence_ids=("future-cutoff",))
                if member.team == "Red One" else member
                for member in with_future.members
            ),
        )
        membership_projection = next(
            item for item in _derive(snapshot, future_membership, cutoff=cutoff).projections
            if item.conference == "Red"
        )
        self.assertEqual(membership_projection.status, "unavailable")
        self.assertEqual(membership_projection.reason, "membership_evidence_unavailable")
        self.assertEqual(membership_projection.participants, ())
        self.assertEqual(
            next(item for item in baseline.projections if item.conference == "Blue"),
            next(item for item in _derive(snapshot, future_membership, cutoff=cutoff).projections
                 if item.conference == "Blue"),
        )

        # All selected games that touch the conference contribute identity
        # evidence, including a non-league game and a title-game exclusion.
        for provider_id in ("red-league", "red-blue", "red-title"):
            with self.subTest(provider_id=provider_id):
                future_designation = replace(
                    with_future,
                    games=tuple(
                        replace(game, evidence_ids=("future-cutoff",))
                        if game.provider_id == provider_id else game
                        for game in with_future.games
                    ),
                )
                projection = next(
                    item for item in _derive(
                        snapshot, future_designation, cutoff=cutoff
                    ).projections
                    if item.conference == "Red"
                )
                self.assertEqual(projection.status, "unavailable")
                self.assertEqual(
                    projection.reason, "game_designation_evidence_unavailable"
                )
                self.assertEqual(projection.participants, ())

    def test_unselected_future_evidence_is_ignored_and_exact_cutoff_is_visible(self):
        snapshot, supplement = _core_fixture()
        baseline = _derive(snapshot, supplement, cutoff=EARLY_CUTOFF)
        future_evidence = _evidence(
            "future-unselected", known_at=datetime(2025, 10, 1, tzinfo=UTC)
        )
        future_only = replace(
            supplement,
            evidence=supplement.evidence + (future_evidence,),
            games=tuple(
                replace(game, evidence_ids=("future-unselected",))
                if game.provider_id == "future-blue" else game
                for game in supplement.games
            ),
        )
        observed = _derive(snapshot, future_only, cutoff=EARLY_CUTOFF)
        self.assertEqual(observed.standings, baseline.standings)
        self.assertEqual(observed.projections, baseline.projections)

        boundary_source = replace(
            supplement.evidence[0],
            known_at=EARLY_CUTOFF,
            effective_from=EARLY_CUTOFF.date(),
        )
        boundary = replace(supplement, evidence=(boundary_source,))
        red_projection = next(
            item for item in _derive(snapshot, boundary, cutoff=EARLY_CUTOFF).projections
            if item.conference == "Red"
        )
        self.assertEqual(red_projection.status, "projected")
        self.assertIsNone(red_projection.reason)
        self.assertEqual(red_projection.participants, ("Red One", "Red Two"))

    def test_forged_validated_wrapper_cannot_bypass_underlying_coverage_or_digest(self):
        snapshot, supplement = _core_fixture()
        validated = _validated(snapshot, supplement)

        forged_digest = replace(validated, supplement_checksum="0" * 64)
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                forged_digest,
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=EARLY_CUTOFF,
                dataset_id="independent-conference-fixture",
                model_version="v0.4.0",
            )

        # Remove a future-game designation so a checkpoint-local calculation
        # cannot fail merely because it tries to use that game. The wrapper's
        # digest is forged to match the altered supplement; derive must still
        # revalidate the complete underlying source contract.
        incomplete = replace(
            validated.supplement,
            games=tuple(
                item for item in validated.supplement.games
                if item.provider_id != "future-blue"
            ),
        )
        forged_coverage = replace(
            validated,
            supplement=incomplete,
            supplement_checksum=incomplete.checksum,
        )
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                forged_coverage,
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=EARLY_CUTOFF,
                dataset_id="independent-conference-fixture",
                model_version="v0.4.0",
            )

    def test_no_title_rule_with_future_evidence_is_unavailable_at_earlier_cutoff(self):
        snapshot, supplement = _core_fixture()
        future_evidence = _evidence(
            "future-no-title", known_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        no_title_rule = ConferenceRule(
            conference="Blue",
            standings_criterion="winning_percentage",
            championship=ChampionshipRule(
                selection="none",
                eligibility=(),
                divisions=(),
                site=SitePolicy("none", None, ("future-no-title",)),
                evidence_ids=("future-no-title",),
            ),
            evidence_ids=("future-no-title",),
        )
        future_supplement = replace(
            supplement,
            evidence=(supplement.evidence[0], future_evidence),
            rules=(supplement.rules[0], no_title_rule),
        )

        reference = _derive(
            snapshot, future_supplement, cutoff=EARLY_CUTOFF
        )
        blue_projection = next(
            item for item in reference.projections if item.conference == "Blue"
        )
        self.assertEqual(blue_projection.status, "unavailable")
        self.assertEqual(blue_projection.site_state, "unavailable")
        self.assertEqual(blue_projection.reason, "rule_evidence_unavailable")

        no_cutoff = _derive(snapshot, supplement, cutoff=None)
        self.assertEqual(
            {item.reason for item in no_cutoff.projections}, {"missing_cutoff"}
        )

    def test_game_start_at_cutoff_cannot_support_that_checkpoint(self):
        snapshot, supplement = _core_fixture()
        boundary_games = tuple(
            replace(
                game,
                date="2025-09-10T00:00:00+00:00",
            )
            if game.provider_id == "red-league" else game
            for game in snapshot.games
        )
        boundary_snapshot = _seal_snapshot(
            snapshot.teams, boundary_games, complete_through_week=0
        )
        boundary_supplement = replace(
            supplement, snapshot_checksum=boundary_snapshot.checksum
        )

        with self.assertRaises(ConferenceReferenceError):
            _derive(
                boundary_snapshot,
                boundary_supplement,
                cutoff=EARLY_CUTOFF,
            )


if __name__ == "__main__":
    unittest.main()
