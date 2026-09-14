"""Independent P1 regressions for the phase-aware postseason calendar.

These tests exercise the calendar, provider adapter, cache, registry, and
ranking seams directly.  They use small synthetic rows so that passing tests
cannot be explained by a shared renderer or by the frozen release artifacts.
"""

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import patch

from cfb import week_calendar
from cfb.postseason_registry import (
    PINNED_REGISTRY,
    PINNED_REGISTRY_CHECKSUM,
    PostseasonCorrectionRegistry,
)
from cfb.ranking_engine import PreviousFinal, ranking_for_week, records_for_week
from cfb.release import build_release, validate_release
from cfb.season_snapshot import (
    SeasonSnapshot,
    SeasonSnapshotService,
    _checksum,
    migrate_postseason_cache,
)
from cfb.season_source import SourceGame, SourcePlayoff, SourceTeam, normalize_game
from cfb.snapshot_cache import SnapshotCache


STAMP = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
WINDOWS = {
    2024: week_calendar.PostseasonWindow(
        season=2024,
        start_date=date(2024, 12, 14),
        end_date=date(2025, 1, 20),
        source_urls=("https://example.test/2024-postseason",),
    ),
    2025: week_calendar.PostseasonWindow(
        season=2025,
        start_date=date(2025, 12, 13),
        end_date=date(2026, 1, 19),
        source_urls=("https://example.test/2025-postseason",),
    ),
}


def _provider_game(**changes):
    payload = {
        "id": 401677077,
        "week": 1,
        "startDate": "2024-12-15T02:00:00+00:00",
        "seasonType": "postseason",
        "homeTeam": "Western Michigan",
        "awayTeam": "South Alabama",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 28,
        "awayPoints": 20,
        "completed": True,
        "neutralSite": True,
        "notes": "IS4S Salute to Veterans Bowl",
        "playoff": {"round": "Bowl", "bowlName": "IS4S Salute to Veterans Bowl"},
    }
    return {**payload, **changes}


def _game(
    *,
    week: int,
    home: str,
    away: str,
    home_points: int,
    away_points: int,
    neutral_site: bool = False,
    **metadata,
):
    return SourceGame(
        week=week,
        home_team=home,
        home_classification="fbs",
        home_points=home_points,
        away_team=away,
        away_classification="fbs",
        away_points=away_points,
        neutral_site=neutral_site,
        completed=True,
        disposition="completed",
        **metadata,
    )


def _ranking_snapshot(postseason_week: int | None) -> SeasonSnapshot:
    teams = (
        SourceTeam("Alpha", "Test"),
        SourceTeam("Beta", "Test"),
        SourceTeam("Gamma", "Test"),
    )
    games = [
        _game(week=1, home="Alpha", away="Beta", home_points=24, away_points=17),
    ]
    if postseason_week is not None:
        games.append(
            _game(
                week=postseason_week,
                home="Gamma",
                away="Alpha",
                home_points=31,
                away_points=7,
                provider_id="401677077",
                date="2024-12-15T02:00:00+00:00",
                provider_week=1,
                provider_season_type="postseason",
                phase="postseason",
                phase_source="recovery_registry",
            )
        )
    metadata = MappingProxyType({"schema_version": 4, "complete_through_week": max(1, postseason_week or 1)})
    return SeasonSnapshot(
        "cfb", "FBS", 2024, teams, tuple(games), metadata, _checksum(metadata, teams, games)
    )


def _legacy_state(
    *, year: int, game: dict, teams: tuple[SourceTeam, ...], canonical_week: int | None = None
):
    normalized = normalize_game(game)
    stored = normalized if canonical_week is None else replace(normalized, week=canonical_week)
    legacy_checksum_game = replace(
        stored,
        provider_season_type=None,
        provider_playoff=None,
        phase=None,
        phase_source=None,
    )
    state = {
        "schema_version": 3,
        "sport": "cfb",
        "classification": "FBS",
        "year": year,
        "teams": [{"school": team.school, "conference": team.conference} for team in teams],
        "games": [
            {
                "week": normalized.week if canonical_week is None else canonical_week,
                "home_team": normalized.home_team,
                "home_classification": normalized.home_classification,
                "home_points": normalized.home_points,
                "away_team": normalized.away_team,
                "away_classification": normalized.away_classification,
                "away_points": normalized.away_points,
                "neutral_site": normalized.neutral_site,
                "provider_id": normalized.provider_id,
                "date": normalized.date,
                "provider_week": normalized.provider_week,
                "completed": normalized.completed,
                "notes": normalized.notes,
                "disposition": normalized.disposition,
                "disposition_source": normalized.disposition_source,
            }
        ],
        "teams_fetched_at": STAMP.isoformat(),
        "games_fetched_at": STAMP.isoformat(),
        "complete_through_week": normalized.week if canonical_week is None else canonical_week,
    }
    state["checksum"] = _checksum(
        state,
        teams,
        (legacy_checksum_game,),
    )
    return state


def _pinned_recovery_state(season: int = 2024) -> tuple[dict, tuple[SourceTeam, ...]]:
    entries = tuple(entry for entry in PINNED_REGISTRY.entries if entry.season == season)
    schools = sorted({name for entry in entries for name in (entry.home_team, entry.away_team)})
    teams = tuple(SourceTeam(school, "Test") for school in schools)
    games = tuple(
        _game(
            week=entry.canonical_week,
            home=entry.home_team,
            away=entry.away_team,
            home_points=28,
            away_points=21,
            provider_id=entry.provider_id,
            date=entry.snapshot_date,
            provider_week=entry.provider_week,
            notes=entry.notes,
            phase="postseason",
            phase_source="recovery_registry",
        )
        for entry in entries
    )
    state = {
        "schema_version": 4,
        "sport": "cfb",
        "classification": "FBS",
        "year": season,
        "teams": [asdict(team) for team in teams],
        "games": [asdict(game) for game in games],
        "teams_fetched_at": STAMP.isoformat(),
        "games_fetched_at": STAMP.isoformat(),
        "complete_through_week": max(game.week for game in games),
        "calendar_provenance": {
            "regular": week_calendar.calendar_provenance(season),
            "postseason": week_calendar.postseason_calendar_provenance(season),
        },
        "correction_registry_provenance": PINNED_REGISTRY.provenance,
    }
    normalized_games = tuple(normalize_game(item) for item in state["games"])
    state["checksum"] = _checksum(state, teams, normalized_games)
    return state, teams


def _provider_release_snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    game = _game(
        week=16,
        home="Alpha",
        away="Beta",
        home_points=24,
        away_points=17,
        neutral_site=True,
        provider_id="401677077",
        date="2024-12-15T02:00:00+00:00",
        provider_week=1,
        provider_season_type="postseason",
        notes="IS4S Salute to Veterans Bowl",
        phase="postseason",
        phase_source="provider",
    )
    metadata = MappingProxyType(
        {
            "schema_version": 4,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2024,
            "teams_fetched_at": STAMP.isoformat(),
            "games_fetched_at": STAMP.isoformat(),
            "complete_through_week": 16,
            "calendar_provenance": {
                "regular": week_calendar.calendar_provenance(2024),
                "postseason": week_calendar.postseason_calendar_provenance(2024),
            },
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", 2024, teams, (game,), metadata, _checksum(metadata, teams, (game,))
    )


def _fresh_regular_snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    game = _game(
        week=1,
        home="Alpha",
        away="Beta",
        home_points=24,
        away_points=17,
        provider_id="401900002",
        date="2024-09-01T16:00:00+00:00",
        provider_week=1,
        provider_season_type="regular",
        phase="regular",
        phase_source="provider",
    )
    metadata = MappingProxyType(
        {
            "schema_version": 4,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2024,
            "teams_fetched_at": STAMP.isoformat(),
            "games_fetched_at": STAMP.isoformat(),
            "complete_through_week": 1,
            "calendar_provenance": week_calendar.calendar_provenance(2024),
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", 2024, teams, (game,), metadata, _checksum(metadata, teams, (game,))
    )


def _cache_state(snapshot: SeasonSnapshot) -> dict:
    metadata = dict(snapshot.metadata)
    state = {
        "schema_version": metadata.get("schema_version", 4),
        "sport": snapshot.sport,
        "classification": snapshot.classification,
        "year": snapshot.year,
        "teams": [asdict(team) for team in snapshot.teams],
        "games": [asdict(game) for game in snapshot.games],
        "teams_fetched_at": metadata.get("teams_fetched_at"),
        "games_fetched_at": metadata.get("games_fetched_at"),
        "complete_through_week": metadata.get("complete_through_week", -1),
        "calendar_provenance": metadata.get("calendar_provenance"),
        "correction_registry_provenance": metadata.get(
            "correction_registry_provenance"
        ),
        "migration_provenance": metadata.get("migration_provenance"),
    }
    state["checksum"] = _checksum(state, snapshot.teams, snapshot.games)
    return state


def _release_base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("published", encoding="utf-8")
    final = base / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>9</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return base


class _ProviderSource:
    requires_provider_metadata = True

    def __init__(self, game):
        self.game = game

    def fetch_teams(self, year, classification, *, cache_decision):
        return (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))

    def fetch_games(self, year, classification, *, cache_decision):
        return (self.game,)


class PostseasonIndependentTests(unittest.TestCase):
    def test_postseason_provider_week_one_uses_fixed_lattice_and_retains_phase(self):
        with patch.dict(week_calendar.POSTSEASON_WINDOWS, WINDOWS, clear=True):
            game = normalize_game(_provider_game(), season=2024, from_provider=True)
        expected = week_calendar.canonical_postseason_week(2024, game.date)
        self.assertEqual(game.week, expected)
        self.assertGreater(game.week, 1)
        self.assertEqual(game.provider_week, 1)
        self.assertEqual(game.provider_season_type, "postseason")
        self.assertEqual(game.phase, "postseason")
        self.assertEqual(game.phase_source, "provider")
        self.assertEqual(game.provider_playoff.round, "Bowl")
        self.assertEqual(game.provider_playoff.bowl_name, "IS4S Salute to Veterans Bowl")

    def test_timezone_local_date_dst_and_empty_gap_mapping(self):
        with patch.dict(week_calendar.POSTSEASON_WINDOWS, WINDOWS, clear=True):
            # The 05:00Z instant is midnight America/New_York on Dec 16.
            self.assertEqual(
                week_calendar.canonical_postseason_week(2024, "2024-12-16T04:59:59Z"),
                16,
            )
            self.assertEqual(
                week_calendar.canonical_postseason_week(2024, "2024-12-16T05:00:00Z"),
                17,
            )
            self.assertEqual(
                week_calendar.canonical_postseason_week(2024, date(2024, 12, 23)),
                18,
            )
            self.assertEqual(
                week_calendar.canonical_postseason_week(2024, "2025-01-20T05:00:00Z"),
                22,
            )

    def test_mixed_regular_and_postseason_games_can_share_week_and_later_cfp_is_stable(self):
        with patch.dict(week_calendar.POSTSEASON_WINDOWS, WINDOWS, clear=True):
            regular = normalize_game(
                _provider_game(
                    id=401000001,
                    startDate="2024-12-09T15:00:00Z",
                    seasonType="regular",
                    week=16,
                    homeTeam="Alpha",
                    awayTeam="Beta",
                    notes="regular conference game",
                    playoff=None,
                ),
                season=2024,
                from_provider=True,
            )
            postseason = normalize_game(
                _provider_game(
                    id=401000002,
                    startDate="2024-12-14T15:00:00Z",
                    homeTeam="Gamma",
                    awayTeam="Alpha",
                ),
                season=2024,
                from_provider=True,
            )
            early_window = replace(WINDOWS[2024], end_date=date(2025, 1, 1))
            expanded_window = replace(WINDOWS[2024], end_date=date(2025, 1, 20))
            with patch.dict(week_calendar.POSTSEASON_WINDOWS, {2024: early_window}, clear=True):
                early = week_calendar.canonical_postseason_week(
                    2024, "2024-12-15T02:00:00Z"
                )
            with patch.dict(week_calendar.POSTSEASON_WINDOWS, {2024: expanded_window}, clear=True):
                expanded = week_calendar.canonical_postseason_week(
                    2024, "2024-12-15T02:00:00Z"
                )
        self.assertEqual(regular.week, 16)
        self.assertEqual(postseason.week, 16)
        self.assertEqual(regular.phase, "regular")
        self.assertEqual(postseason.phase, "postseason")
        self.assertEqual(early, expanded)

    def test_provider_missing_or_unsupported_phase_fails_closed(self):
        for value in (None, "championship", "playoffs"):
            raw = _provider_game(seasonType=value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "seasonType"):
                normalize_game(raw, season=2024, from_provider=True)
        with self.assertRaisesRegex(ValueError, "date"):
            normalize_game(
                _provider_game(startDate=None), season=2024, from_provider=True
            )
        with self.assertRaisesRegex(ValueError, "boundary|supported"):
            week_calendar.canonical_postseason_week(2027, "2027-12-20T00:00:00Z")

    def test_provider_capture_persists_calendar_provenance_and_reloads(self):
        with patch.dict(week_calendar.POSTSEASON_WINDOWS, WINDOWS, clear=True):
            game = normalize_game(_provider_game(), season=2024, from_provider=True)
            expected_calendar = {
                "regular": week_calendar.calendar_provenance(2024),
                "postseason": week_calendar.postseason_calendar_provenance(2024),
            }
            with tempfile.TemporaryDirectory() as directory:
                cache = SnapshotCache(Path(directory) / "cache")
                service = SeasonSnapshotService(
                    _ProviderSource(game), cache, clock=lambda: STAMP
                )
                captured = service.get(2024)
                reloaded = service.load_cached(2024)
        self.assertEqual(captured.games[0].phase, "postseason")
        self.assertEqual(reloaded.games[0].provider_season_type, "postseason")
        self.assertEqual(
            reloaded.metadata["calendar_provenance"],
            expected_calendar,
        )

    def test_schema4_fresh_provider_without_phase_origin_fails_snapshot_load(self):
        fresh = _fresh_regular_snapshot()
        stripped_game = replace(
            fresh.games[0],
            provider_season_type=None,
            provider_playoff=None,
            phase=None,
            phase_source=None,
        )
        metadata = dict(fresh.metadata)
        stripped = SeasonSnapshot(
            fresh.sport,
            fresh.classification,
            fresh.year,
            fresh.teams,
            (stripped_game,),
            MappingProxyType(metadata),
            _checksum(metadata, fresh.teams, (stripped_game,)),
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2024, "FBS", _cache_state(stripped))

            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            with self.assertRaisesRegex(ValueError, "origin|phase|provider|migration"):
                SeasonSnapshotService(NoFetchSource(), cache).load_cached(2024, "FBS")

    def test_schema4_fresh_provider_without_phase_origin_fails_release_validation(self):
        fresh = _fresh_regular_snapshot()
        prior = PreviousFinal(
            {"Alpha": 10.0, "Beta": 9.0},
            {"Alpha": 0.0, "Beta": 0.0},
            year=2023,
            classification="FBS",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _release_base(root)
            candidate = build_release(
                fresh,
                root / "candidate",
                release_id="stripped-provider",
                target_week=1,
                phase="week",
                previous_final=prior,
                published_site=base,
                timestamp=STAMP.isoformat(),
            )
            snapshot_path = candidate.site / "cfb/years/2024/data/snapshot.json"
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            for key in ("phase", "phase_source", "provider_season_type", "provider_playoff"):
                payload["games"][0].pop(key, None)
            payload_games = tuple(normalize_game(item) for item in payload["games"])
            payload_teams = tuple(SourceTeam(**item) for item in payload["teams"])
            snapshot_state = {
                "schema_version": payload["metadata"]["schema_version"],
                "sport": payload["sport"],
                "classification": payload["classification"],
                "year": payload["year"],
                "teams_fetched_at": payload["metadata"].get("teams_fetched_at"),
                "games_fetched_at": payload["metadata"].get("games_fetched_at"),
                "complete_through_week": payload["metadata"].get("complete_through_week"),
                "teams": payload["teams"],
                "games": payload["games"],
                "calendar_provenance": payload["metadata"].get("calendar_provenance"),
                "correction_registry_provenance": payload["metadata"].get(
                    "correction_registry_provenance"
                ),
                "migration_provenance": payload["metadata"].get("migration_provenance"),
            }
            payload["checksum"] = _checksum(snapshot_state, payload_teams, payload_games)
            snapshot_path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            relative_snapshot = str(snapshot_path.relative_to(candidate.site))
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            manifest["snapshot_checksum"] = payload["checksum"]
            manifest["source_snapshot"] = payload["checksum"]
            manifest["runs"][0]["source_snapshot"] = payload["checksum"]
            manifest["artifact_checksums"][relative_snapshot] = hashlib.sha256(
                snapshot_path.read_bytes()
            ).hexdigest()
            manifest.pop("manifest_checksum", None)
            manifest["manifest_checksum"] = hashlib.sha256(
                (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            ).hexdigest()
            candidate.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            report = validate_release(candidate, published_site=base)
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                token in failure.message.lower()
                for failure in report.failures
                for token in ("origin", "phase", "provider", "migration")
            )
        )

    def test_recovery_rows_require_migration_origin_in_addition_to_registry(self):
        state, teams = _pinned_recovery_state()
        self.assertNotIn("migration_provenance", state)
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2024, "FBS", state)

            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            with self.assertRaisesRegex(ValueError, "migration|origin|source"):
                SeasonSnapshotService(NoFetchSource(), cache).load_cached(2024, "FBS")

    def test_recovery_rows_require_registry_when_origin_is_present(self):
        state, teams = _pinned_recovery_state()
        state.pop("correction_registry_provenance")
        games = tuple(normalize_game(item) for item in state["games"])
        state["checksum"] = _checksum(state, teams, games)
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2024, "FBS", state)

            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            with self.assertRaisesRegex(ValueError, "registry|recovery|origin"):
                SeasonSnapshotService(NoFetchSource(), cache).load_cached(2024, "FBS")

    def test_2026_unknown_legacy_phase_without_origin_fails_schema4_load(self):
        teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
        legacy = _legacy_state(
            year=2026,
            game=_provider_game(
                id=401900003,
                startDate="2026-08-30T02:00:00+00:00",
                seasonType=None,
                homeTeam="Alpha",
                awayTeam="Beta",
                notes=None,
                playoff=None,
            ),
            teams=teams,
            canonical_week=0,
        )
        legacy["schema_version"] = 4
        legacy["calendar_provenance"] = week_calendar.calendar_provenance(2026)
        legacy.pop("correction_registry_provenance", None)
        legacy.pop("migration_provenance", None)
        games = tuple(normalize_game(item) for item in legacy["games"])
        legacy["checksum"] = _checksum(legacy, teams, games)
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2026, "FBS", legacy)

            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            with self.assertRaisesRegex(ValueError, "origin|phase|provider|migration"):
                SeasonSnapshotService(NoFetchSource(), cache).load_cached(2026, "FBS")

    def test_schema4_canonical_fixture_without_provider_metadata_stays_compatible(self):
        snapshot = _ranking_snapshot(None)
        with tempfile.TemporaryDirectory() as directory:
            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2024, "FBS", _cache_state(snapshot))

            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            loaded = SeasonSnapshotService(NoFetchSource(), cache).load_cached(2024, "FBS")
        self.assertEqual(loaded.games, snapshot.games)

    def test_early_ranking_is_invariant_to_postseason_score_when_week_is_correct(self):
        prior_cors = {"Alpha": 10.0, "Beta": 9.0, "Gamma": 8.0}
        prior_wve = {school: 0.0 for school in prior_cors}
        early_only = _ranking_snapshot(None)
        corrected = _ranking_snapshot(17)
        contaminated = _ranking_snapshot(1)
        early_rows = ranking_for_week(early_only, 1, prior_cors, prior_wve)
        corrected_rows = ranking_for_week(corrected, 1, prior_cors, prior_wve)
        contaminated_rows = ranking_for_week(contaminated, 1, prior_cors, prior_wve)
        self.assertEqual(early_rows, corrected_rows)
        self.assertNotEqual(early_rows, contaminated_rows)
        self.assertEqual(
            records_for_week(corrected, 1), records_for_week(early_only, 1)
        )

    def test_recovery_registry_binds_exact_identity_and_source(self):
        registry = PINNED_REGISTRY
        found = registry.lookup(2024, "401677077")
        self.assertIsNotNone(found)
        self.assertEqual(found.provider_id, "401677077")
        self.assertEqual(found.snapshot_date, "2024-12-15T02:00:00+00:00")
        self.assertEqual(found.home_team, "Western Michigan")
        self.assertEqual(found.away_team, "South Alabama")
        self.assertEqual(found.notes, "IS4S Salute to Veterans Bowl")
        self.assertEqual(found.phase, "postseason")
        self.assertEqual(found.phase_source, "recovery_registry")
        self.assertEqual(found.canonical_week, 16)
        self.assertEqual(registry.computed_checksum, PINNED_REGISTRY_CHECKSUM)
        payload = registry.payload()
        restored = PostseasonCorrectionRegistry.from_payload(payload)
        self.assertEqual(restored.computed_checksum, registry.computed_checksum)
        self.assertEqual(restored.lookup(2024, "401677077"), found)

    def test_registry_rejects_identity_or_phase_tamper_after_reseal(self):
        payload = PINNED_REGISTRY.payload()
        payload["entries"][0]["home_team"] = "Forged Team"
        unsigned = dict(payload)
        unsigned.pop("checksum")
        payload["checksum"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        with self.assertRaises(ValueError):
            PostseasonCorrectionRegistry.from_payload(payload).validate(require_pinned=True)

    def test_cache_migration_rejects_arbitrary_registry_for_late_rows(self):
        teams = (
            SourceTeam("Western Michigan", "Test"),
            SourceTeam("South Alabama", "Test"),
        )
        state = _legacy_state(
            year=2024,
            game=_provider_game(),
            teams=teams,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "snapshots"
            destination = root / "migrated"
            source.mkdir()
            source_path = source / "cfb-fbs-2024.json"
            source_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            before = source_path.read_bytes()
            with self.assertRaises(ValueError):
                migrate_postseason_cache(source, destination, None, seasons=(2024,))
            self.assertEqual(source_path.read_bytes(), before)

    def test_unapproved_2026_migration_input_and_new_provider_input_fail_closed(self):
        teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
        legacy = _legacy_state(
            year=2026,
            game=_provider_game(
                id=401900001,
                startDate="2026-08-30T02:00:00+00:00",
                seasonType=None,
                homeTeam="Alpha",
                awayTeam="Beta",
                notes=None,
                playoff=None,
            ),
            teams=teams,
            canonical_week=0,
        )
        with self.assertRaisesRegex(ValueError, "seasonType"):
            normalize_game(
                _provider_game(
                    id=401900001,
                    startDate="2026-08-30T02:00:00+00:00",
                    seasonType=None,
                    homeTeam="Alpha",
                    awayTeam="Beta",
                    notes=None,
                    playoff=None,
                ),
                season=2026,
                from_provider=True,
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "snapshots"
            destination = root / "migrated"
            source.mkdir()
            source_path = source / "cfb-fbs-2026.json"
            source_path.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
            before = source_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "approved input identity"):
                migrate_postseason_cache(source, destination, None, seasons=(2026,))
            self.assertEqual(source_path.read_bytes(), before)

    def test_snapshot_load_rejects_resealed_phase_date_and_registry_provenance_tamper(self):
        for mutation in ("phase", "date", "registry"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cache = SnapshotCache(root / "snapshots")
                state, teams = _pinned_recovery_state()
                cache.save(2024, "FBS", state)

                class NoFetchSource:
                    def fetch_teams(self, *args, **kwargs):
                        raise AssertionError("snapshot load attempted transport")

                    def fetch_games(self, *args, **kwargs):
                        raise AssertionError("snapshot load attempted transport")

                service = SeasonSnapshotService(NoFetchSource(), cache)
                forged = json.loads(cache.path_for(2024, "FBS").read_text(encoding="utf-8"))
                if mutation == "phase":
                    forged["games"][0]["phase"] = "regular"
                elif mutation == "date":
                    forged["games"][0]["date"] = "2025-02-01T00:00:00+00:00"
                else:
                    forged["correction_registry_provenance"]["checksum"] = "0" * 64
                forged_games = tuple(normalize_game(item) for item in forged["games"])
                forged["checksum"] = _checksum(forged, teams, forged_games)
                cache.save(2024, "FBS", forged)
                with self.assertRaisesRegex(
                    ValueError, "migration|origin|registry|recovery|canonical|provider"
                ):
                    service.load_cached(2024, "FBS")

    def test_resealed_cache_cannot_hide_all_pinned_recovery_rows(self):
        for season in (2024, 2025):
            with self.subTest(season=season), tempfile.TemporaryDirectory() as directory:
                state, teams = _pinned_recovery_state(season)
                for game in state["games"]:
                    game["week"] = 1
                    game.pop("phase", None)
                    game.pop("phase_source", None)
                state["complete_through_week"] = 1
                state["calendar_provenance"] = week_calendar.calendar_provenance(season)
                state["checksum"] = _checksum(
                    state,
                    teams,
                    tuple(normalize_game(item) for item in state["games"]),
                )
                cache = SnapshotCache(Path(directory) / "cache")
                cache.save(season, "FBS", state)

                class NoFetchSource:
                    requires_provider_metadata = False

                    def fetch_teams(self, *args, **kwargs):
                        raise AssertionError("snapshot load attempted transport")

                    def fetch_games(self, *args, **kwargs):
                        raise AssertionError("snapshot load attempted transport")

                service = SeasonSnapshotService(NoFetchSource(), cache)
                with self.assertRaisesRegex(ValueError, "recovery|registry|postseason"):
                    service.load_cached(season, "FBS")

    def test_migration_source_checksum_provenance_cannot_be_resealed(self):
        teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
        game = _game(
            week=0,
            home="Alpha",
            away="Beta",
            home_points=24,
            away_points=17,
            provider_id="401900001",
            date="2026-08-30T02:00:00+00:00",
            provider_week=1,
            provider_season_type="regular",
            phase="regular",
            phase_source="provider",
        )
        state = {
            "schema_version": 4,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2026,
            "teams": [asdict(team) for team in teams],
            "games": [asdict(game)],
            "teams_fetched_at": STAMP.isoformat(),
            "games_fetched_at": STAMP.isoformat(),
            "complete_through_week": 0,
            "calendar_provenance": week_calendar.calendar_provenance(2026),
            "migration_provenance": {
                "kind": "postseason-calendar-repair",
                "source_schema_version": 3,
                "source_snapshot_checksum": PINNED_REGISTRY.snapshot_checksums[2026],
                "target_schema_version": 4,
                "registry_version": PINNED_REGISTRY.version,
                "registry_checksum": PINNED_REGISTRY.computed_checksum,
            },
        }
        state["checksum"] = _checksum(state, teams, (game,))
        with tempfile.TemporaryDirectory() as directory:
            class NoFetchSource:
                requires_provider_metadata = False

                def fetch_teams(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

                def fetch_games(self, *args, **kwargs):
                    raise AssertionError("snapshot load attempted transport")

            cache = SnapshotCache(Path(directory) / "cache")
            cache.save(2026, "FBS", state)
            service = SeasonSnapshotService(NoFetchSource(), cache)
            with self.assertRaisesRegex(ValueError, "migration|source"):
                service.load_cached(2026, "FBS")
            forged = json.loads(cache.path_for(2026, "FBS").read_text(encoding="utf-8"))
            forged["migration_provenance"]["source_snapshot_checksum"] = "0" * 64
            forged_teams = tuple(SourceTeam(**item) for item in forged["teams"])
            forged_games = tuple(normalize_game(item) for item in forged["games"])
            forged["checksum"] = _checksum(forged, forged_teams, forged_games)
            cache.save(2026, "FBS", forged)
            with self.assertRaisesRegex(ValueError, "migration|source"):
                service.load_cached(2026, "FBS")

    def test_release_validation_rejects_resealed_calendar_provenance_tamper(self):
        snapshot = _provider_release_snapshot()
        prior = PreviousFinal(
            {"Alpha": 10.0, "Beta": 9.0},
            {"Alpha": 0.0, "Beta": 0.0},
            year=2023,
            classification="FBS",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _release_base(root)
            candidate = build_release(
                snapshot,
                root / "candidate",
                release_id="candidate",
                phase="preseason",
                previous_final=prior,
                published_site=base,
                timestamp=STAMP.isoformat(),
            )
            baseline = validate_release(candidate, published_site=base)
            self.assertTrue(baseline.valid, [str(failure) for failure in baseline.failures])

            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            manifest["runs"][0]["week_calendar"]["postseason"]["policy_id"] = "forged-policy"
            manifest.pop("manifest_checksum", None)
            manifest["manifest_checksum"] = hashlib.sha256(
                (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            ).hexdigest()
            candidate.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            forged = validate_release(candidate, published_site=base)
            self.assertFalse(forged.valid)
            self.assertIn("runs.invalid", {failure.code for failure in forged.failures})


if __name__ == "__main__":
    unittest.main()
