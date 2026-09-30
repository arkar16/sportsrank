from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
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
from tests.test_conference_sources import _snapshot, _supplement


UTC = timezone.utc


def _rankings(*, missing_red_rating: bool = False) -> tuple[CheckpointRanking, ...]:
    return (
        CheckpointRanking("Alpha", None if missing_red_rating else 80.0, None if missing_red_rating else 1),
        CheckpointRanking("Beta", 50.0, 2),
        CheckpointRanking("Gamma", 40.0, 3),
        CheckpointRanking("Delta", 30.0, 4),
        CheckpointRanking("Independent", 20.0, 5),
    )


def _validated(snapshot: SeasonSnapshot, supplement: ConferenceSupplement):
    return validate_conference_supplement(supplement, snapshot)


def _complete_snapshot(snapshot: SeasonSnapshot) -> SeasonSnapshot:
    games = tuple(
        replace(
            game,
            home_points=7 if game.provider_id == "g-future" else game.home_points,
            away_points=3 if game.provider_id == "g-future" else game.away_points,
            completed=True if game.provider_id == "g-future" else game.completed,
            disposition="completed" if game.provider_id == "g-future" else game.disposition,
        )
        for game in snapshot.games
    )
    metadata = dict(snapshot.metadata)
    metadata["complete_through_week"] = 1
    provisional = replace(snapshot, games=games, metadata=metadata, checksum="0" * 64)
    return replace(provisional, checksum=snapshot_content_checksum(provisional))


def _large_comparison_fixture() -> tuple[SeasonSnapshot, ConferenceSupplement, tuple[CheckpointRanking, ...]]:
    teams = tuple(SourceTeam(f"Team{i}", "Depth") for i in range(8))
    game = SourceGame(
        week=0,
        home_team="Team0",
        home_classification="FBS",
        home_points=None,
        away_team="Team1",
        away_classification="FBS",
        away_points=None,
        neutral_site=False,
        provider_id="depth-future",
        date="2025-09-01",
        completed=False,
        disposition="scheduled",
        phase="regular",
        phase_source="provider",
    )
    metadata = {
        "schema_version": 4,
        "teams_fetched_at": "2026-01-01T00:00:00+00:00",
        "games_fetched_at": "2026-01-01T00:00:00+00:00",
        "complete_through_week": -1,
    }
    provisional = SeasonSnapshot(
        sport="cfb",
        classification="FBS",
        year=2025,
        teams=teams,
        games=(game,),
        metadata=metadata,
        checksum="0" * 64,
    )
    snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
    evidence = SourceEvidence(
        evidence_id="depth-source",
        url="https://example.test/depth.pdf",
        source_sha256="c" * 64,
        published_at=datetime(2025, 1, 1, tzinfo=UTC),
        effective_from=date(2025, 1, 1),
        known_at=datetime(2026, 1, 1, tzinfo=UTC),
        retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    members = tuple(
        ConferenceMember(team.school, "Depth", False, ("depth-source",)) for team in teams
    )
    eligibility = tuple(
        EligibilityRule(team.school, True, ("depth-source",)) for team in teams
    )
    supplement = ConferenceSupplement(
        season=2025,
        snapshot_checksum=snapshot.checksum,
        content_identity="depth-fixture-v1",
        evidence=(evidence,),
        members=members,
        games=(
            ConferenceGameDesignation(
                provider_id="depth-future",
                home_team="Team0",
                away_team="Team1",
                conference_game=True,
                counts_for_standings=True,
                title_game=False,
                conference="Depth",
                evidence_ids=("depth-source",),
            ),
        ),
        rules=(
            ConferenceRule(
                conference="Depth",
                standings_criterion="winning_percentage",
                championship=ChampionshipRule(
                    selection="top_two",
                    eligibility=eligibility,
                    divisions=(),
                    site=SitePolicy("neutral", None, ("depth-source",)),
                    evidence_ids=("depth-source",),
                ),
                evidence_ids=("depth-source",),
            ),
        ),
    )
    rankings = tuple(
        CheckpointRanking(team.school, float((index + 1) * 10), index + 1)
        for index, team in enumerate(teams)
    )
    return snapshot, supplement, rankings


class ConferenceReferenceTests(unittest.TestCase):
    def test_pairing_evidence_cutoff_does_not_erase_retrospective_records(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        cutoff = datetime(2025, 9, 10, tzinfo=UTC)
        later = replace(
            supplement.evidence[0], evidence_id="later-source",
            url="https://example.test/later.pdf", source_sha256="d" * 64,
            known_at=datetime(2025, 10, 1, tzinfo=UTC),
        )

        def derive(source, viewed_cutoff=cutoff):
            return derive_conference_reference(
                snapshot, source, _rankings(), phase="week", target_week=0,
                cutoff=viewed_cutoff, dataset_id="fixture-w0", model_version="v0.4.0",
            )

        baseline = derive(supplement)
        for kind in ("membership", "game_designation"):
            with self.subTest(kind=kind):
                candidate = replace(supplement, evidence=supplement.evidence + (later,))
                if kind == "membership":
                    candidate = replace(candidate, members=tuple(
                        replace(member, evidence_ids=("later-source",))
                        if member.team == "Alpha" else member
                        for member in candidate.members
                    ))
                else:
                    candidate = replace(candidate, games=tuple(
                        replace(game, evidence_ids=("later-source",))
                        if game.provider_id == "g-red" else game
                        for game in candidate.games
                    ))
                earlier = derive(candidate)
                projection = next(p for p in earlier.projections if p.conference == "Red")
                self.assertEqual(projection.status, "unavailable")
                self.assertEqual(projection.reason, f"{kind}_evidence_unavailable")
                self.assertEqual(projection.participants, ())
                self.assertIsNone(projection.host_team)
                self.assertEqual(earlier.standings, baseline.standings)
                self.assertEqual(earlier.comparisons, baseline.comparisons)
                visible = derive(candidate, later.known_at)
                self.assertEqual(
                    next(p for p in visible.projections if p.conference == "Red").status,
                    "projected",
                )

        # Unselected future game evidence cannot contaminate the viewed checkpoint.
        future_only = replace(
            supplement, evidence=supplement.evidence + (later,),
            games=tuple(
                replace(game, evidence_ids=("later-source",))
                if game.provider_id == "g-post" else game for game in supplement.games
            ),
        )
        self.assertEqual(derive(future_only).projections, baseline.projections)
        no_cutoff = derive(supplement, None)
        self.assertTrue(all(p.reason == "missing_cutoff" for p in no_cutoff.projections))

    def test_w0_records_exclude_title_game_but_keep_overall_and_interconference_context(self) -> None:
        snapshot = _snapshot()
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, _supplement(snapshot)),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 10, tzinfo=UTC),
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.standings if item.conference == "Red")
        alpha = next(row for row in red.rows if row.school == "Alpha")
        beta = next(row for row in red.rows if row.school == "Beta")
        self.assertEqual(alpha.conference_record, Record(1, 0, 0))
        self.assertEqual(alpha.conference_record.wins, 1)
        self.assertEqual(alpha.conference_record.losses, 0)
        self.assertEqual(alpha.overall_record.wins, 4)
        self.assertEqual(beta.conference_record, Record(0, 1, 0))
        self.assertEqual(beta.overall_record.wins, 1)
        self.assertEqual(beta.overall_record.losses, 2)
        red_blue = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(red_blue.regular.wins, 1)
        self.assertEqual(red_blue.regular.losses, 0)
        self.assertEqual(red_blue.postseason.games, 0)
        red_fcs = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "FCS"
        )
        self.assertEqual(red_fcs.combined.games, 1)
        red_ind = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "FBS Independents"
        )
        self.assertEqual(red_ind.combined.wins, 1)

    def test_final_adds_postseason_interconference_game_and_binds_identities(self) -> None:
        snapshot = _complete_snapshot(_snapshot())
        supplement = replace(_supplement(_snapshot()), snapshot_checksum=snapshot.checksum)
        validated = _validated(snapshot, supplement)
        reference = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="final",
            target_week=1,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            dataset_id="fixture-rankings-final",
            model_version="v0.4.0",
        )
        self.assertEqual(reference.snapshot_checksum, snapshot.checksum)
        self.assertEqual(reference.supplement_checksum, validated.supplement_checksum)
        self.assertEqual(reference.checkpoint, "FINAL")
        red_blue = next(
            item
            for item in reference.interconference
            if item.conference == "Red" and item.opponent == "Blue"
        )
        self.assertEqual(red_blue.regular.games, 1)
        self.assertEqual(red_blue.postseason.games, 1)
        self.assertEqual(red_blue.combined.wins, 2)

    def test_comparison_uses_all_members_and_ceiling_quarter(self) -> None:
        snapshot, supplement, rankings = _large_comparison_fixture()
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, supplement),
            rankings,
            phase="preseason",
            target_week=None,
            cutoff=datetime(2025, 8, 1, tzinfo=UTC),
            dataset_id="depth-rankings-preseason",
            model_version="v0.4.0",
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

    def test_missing_rating_makes_entire_comparison_unavailable(self) -> None:
        snapshot = _snapshot()
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, _supplement(snapshot)),
            _rankings(missing_red_rating=True),
            phase="preseason",
            target_week=None,
            cutoff=datetime(2025, 8, 1, tzinfo=UTC),
            dataset_id="fixture-rankings-preseason",
            model_version="v0.4.0",
        )
        red = next(item for item in reference.comparisons if item.conference == "Red")
        self.assertFalse(red.available)
        self.assertEqual(red.reason, "missing_rating")
        self.assertEqual(red.member_count, 2)

    def test_tied_berth_is_unresolved_even_when_cors_differs(self) -> None:
        original = _snapshot()
        tied_games = tuple(
            replace(game, home_points=14, away_points=14)
            if game.provider_id == "g-red"
            else game
            for game in original.games
        )
        provisional = replace(original, games=tied_games, checksum="0" * 64)
        tied_snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
        tied_supplement = replace(_supplement(original), snapshot_checksum=tied_snapshot.checksum)
        reference = derive_conference_reference(
            tied_snapshot,
            _validated(tied_snapshot, tied_supplement),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 10, tzinfo=UTC),
            dataset_id="fixture-rankings-w0-tie",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "projected")
        self.assertEqual(projection.participants, ("Alpha", "Beta"))
        self.assertEqual(projection.contenders, ())
        self.assertEqual(projection.site_state, "unresolved")

    def test_confirmation_is_visible_only_after_its_known_at_cutoff(self) -> None:
        snapshot = _snapshot()
        supplement = replace(
            _supplement(snapshot),
            confirmations=(
                DatedConfirmation(
                    confirmation_id="red-final",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=datetime(2026, 1, 1, tzinfo=UTC),
                    evidence_ids=("fixture-source",),
                ),
            ),
        )
        validated = _validated(snapshot, supplement)
        early = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 10, tzinfo=UTC),
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        late = derive_conference_reference(
            snapshot,
            validated,
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        early_projection = next(item for item in early.projections if item.conference == "Red")
        late_projection = next(item for item in late.projections if item.conference == "Red")
        self.assertEqual(early_projection.status, "projected")
        self.assertEqual(early_projection.confirmation_id, None)
        self.assertEqual(late_projection.status, "confirmed")
        self.assertEqual(late_projection.confirmation_id, "red-final")

    def test_future_rule_evidence_makes_projection_unavailable_at_earlier_cutoff(self) -> None:
        snapshot = _snapshot()
        source = _supplement(snapshot)
        future_evidence = replace(
            source.evidence[0],
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        supplement = replace(source, evidence=(future_evidence,))
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, supplement),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 10, tzinfo=UTC),
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "unavailable")
        self.assertEqual(projection.site_state, "unavailable")

    def test_conflicting_same_cutoff_confirmation_site_treatment_stays_unresolved(self) -> None:
        snapshot = _snapshot()
        source = _supplement(snapshot)
        known = datetime(2026, 1, 1, tzinfo=UTC)
        supplement = replace(
            source,
            confirmations=(
                DatedConfirmation(
                    confirmation_id="red-neutral",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=known,
                    evidence_ids=("fixture-source",),
                    site_mode="neutral",
                ),
                DatedConfirmation(
                    confirmation_id="red-hosted",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=known,
                    evidence_ids=("fixture-source",),
                    site_mode="fixed_hosted",
                    host_team="Alpha",
                ),
            ),
        )
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, supplement),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=known,
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "unresolved")
        self.assertEqual(projection.participants, ())
        self.assertEqual(projection.site_state, "unresolved")

    def test_conflicting_confirmation_site_treatment_across_dates_stays_unresolved(self) -> None:
        snapshot = _snapshot()
        source = _supplement(snapshot)
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        later = datetime(2026, 1, 2, tzinfo=UTC)
        supplement = replace(
            source,
            confirmations=(
                DatedConfirmation(
                    confirmation_id="red-neutral-earlier",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=earlier,
                    evidence_ids=("fixture-source",),
                    site_mode="neutral",
                ),
                DatedConfirmation(
                    confirmation_id="red-hosted-later",
                    conference="Red",
                    participants=("Alpha", "Beta"),
                    known_at=later,
                    evidence_ids=("fixture-source",),
                    site_mode="fixed_hosted",
                    host_team="Alpha",
                ),
            ),
        )
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, supplement),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=later,
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "unresolved")
        self.assertEqual(projection.participants, ())
        self.assertEqual(projection.site_state, "unresolved")

    def test_public_wrapper_is_revalidated_before_derivation(self) -> None:
        snapshot = _snapshot()
        source = _supplement(snapshot)
        validated = _validated(snapshot, source)
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                replace(validated, supplement_checksum="0" * 64),
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=datetime(2025, 9, 10, tzinfo=UTC),
                dataset_id="fixture-rankings-w0",
                model_version="v0.4.0",
            )
        incomplete = replace(source, games=source.games[:-1])
        forged = replace(
            validated,
            supplement=incomplete,
            supplement_checksum=incomplete.checksum,
        )
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                forged,
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=datetime(2025, 9, 10, tzinfo=UTC),
                dataset_id="fixture-rankings-w0",
                model_version="v0.4.0",
            )

    def test_future_no_title_evidence_is_unavailable_at_earlier_cutoff(self) -> None:
        snapshot = _snapshot()
        source = _supplement(snapshot)
        future_evidence = replace(
            source.evidence[0],
            effective_from=date(2026, 1, 1),
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        no_title = ConferenceRule(
            conference="Red",
            standings_criterion="winning_percentage",
            championship=ChampionshipRule(
                selection="none",
                eligibility=(),
                divisions=(),
                site=SitePolicy("none", None, ("fixture-source",)),
                evidence_ids=("fixture-source",),
            ),
            evidence_ids=("fixture-source",),
        )
        supplement = replace(
            source,
            evidence=(future_evidence,),
            rules=(no_title, source.rules[1]),
        )
        reference = derive_conference_reference(
            snapshot,
            _validated(snapshot, supplement),
            _rankings(),
            phase="week",
            target_week=0,
            cutoff=datetime(2025, 9, 10, tzinfo=UTC),
            dataset_id="fixture-rankings-w0",
            model_version="v0.4.0",
        )
        projection = next(item for item in reference.projections if item.conference == "Red")
        self.assertEqual(projection.status, "unavailable")
        self.assertEqual(projection.site_state, "unavailable")

    def test_selected_game_start_at_cutoff_is_rejected(self) -> None:
        original = _snapshot()
        games = tuple(
            replace(game, date="2025-09-10T00:00:00+00:00") if game.provider_id == "g-red" else game
            for game in original.games
        )
        provisional = replace(original, games=games, checksum="0" * 64)
        snapshot = replace(provisional, checksum=snapshot_content_checksum(provisional))
        supplement = replace(_supplement(original), snapshot_checksum=snapshot.checksum)
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                _validated(snapshot, supplement),
                _rankings(),
                phase="week",
                target_week=0,
                cutoff=datetime(2025, 9, 10, tzinfo=UTC),
                dataset_id="fixture-rankings-w0",
                model_version="v0.4.0",
            )

    def test_future_or_final_truncation_is_rejected(self) -> None:
        snapshot = _snapshot()
        supplement = _supplement(snapshot)
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                _validated(snapshot, supplement),
                _rankings(),
                phase="week",
                target_week=1,
                cutoff=datetime(2025, 9, 10, tzinfo=UTC),
                dataset_id="fixture-rankings-w1",
                model_version="v0.4.0",
            )
        with self.assertRaises(ConferenceReferenceError):
            derive_conference_reference(
                snapshot,
                _validated(snapshot, supplement),
                _rankings(),
                phase="final",
                target_week=0,
                cutoff=datetime(2025, 9, 10, tzinfo=UTC),
                dataset_id="fixture-rankings-final",
                model_version="v0.4.0",
            )


if __name__ == "__main__":
    unittest.main()
