"""Independent successive-release checks for the SR-26 forecast ledger.

These fixtures are synthetic.  They exercise the observable Release boundary
and reseal the candidate manifest after each deliberate mutation, so the
checks do not rely on an in-memory evaluator result or on CFBD data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from bs4 import BeautifulSoup

from cfb.forecast_publication import load_verified_forecasts
from cfb.forecast_record import (
    EvidenceRef,
    ForecastCandidate,
    ForecastProvenance,
    GameIdentity,
    GameTimingEvidence,
)
from cfb.firebase import FakeFirebasePublicationBackend
from cfb.forecast_release import canonical_json
from cfb.release import build_release, validate_release
from cfb.ranking_engine import HFA, MODEL_VERSION, PreviousFinal, season_rankings, spreads_for_week
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam
from tests.test_forecast_publication import APP_IDENTITY, TARGET, forecast_fixture, publish, recorded


UTC = timezone.utc
DIGEST = "sha256:" + "b" * 64


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 1, hour, minute, tzinfo=UTC)


def _evidence(name: str) -> EvidenceRef:
    return EvidenceRef("synthetic-lifecycle", name, DIGEST)


def _snapshot(
    *, next_completed: bool, next_home: int = 24, next_away: int = 20,
    next_notes: str | None = None,
) -> SeasonSnapshot:
    teams = (SourceTeam("Home", "X"), SourceTeam("Away", "X"))
    games = (
        SourceGame(
            0, "Home", "fbs", 22, "Away", "fbs", 20, False,
            provider_id="g-final", completed=True,
        ),
        SourceGame(
            1, "Home", "fbs", next_home if next_completed else None,
            "Away", "fbs", next_away if next_completed else None, False,
            provider_id="g-next", completed=next_completed, notes=next_notes,
        ),
    )
    state = {
        "schema_version": 2,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2026,
        "teams_fetched_at": None,
        "games_fetched_at": None,
        "complete_through_week": 1 if next_completed else 0,
        "calendar_provenance": None,
        "correction_registry_provenance": None,
        "migration_provenance": None,
    }
    return SeasonSnapshot(
        "cfb", "FBS", 2026, teams, games, MappingProxyType(state),
        _checksum(state, teams, games),
    )


def _renderer_snapshot(*, missing_home: int = 22, missing_away: int = 20) -> SeasonSnapshot:
    """A four-row report: three graded games and one scored omission."""
    schools = ("Home", "Away", "North", "South", "East", "West", "Alpha", "Beta")
    teams = tuple(SourceTeam(school, "X") for school in schools)
    games = (
        SourceGame(
            0, "Home", "fbs", missing_home, "Away", "fbs", missing_away, False,
            provider_id="g-missing", completed=True,
        ),
        SourceGame(
            1, "North", "fbs", 30, "South", "fbs", 20, False,
            provider_id="g-cover", completed=True,
        ),
        SourceGame(
            1, "East", "fbs", 21, "West", "fbs", 20, False,
            provider_id="g-no-cover", completed=True,
        ),
        SourceGame(
            1, "Alpha", "fbs", 17, "Beta", "fbs", 20, False,
            provider_id="g-third", completed=True,
        ),
    )
    state = {
        "schema_version": 2,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2026,
        "teams_fetched_at": None,
        "games_fetched_at": None,
        "complete_through_week": 1,
        "calendar_provenance": None,
        "correction_registry_provenance": None,
        "migration_provenance": None,
    }
    return SeasonSnapshot(
        "cfb", "FBS", 2026, teams, games, MappingProxyType(state),
        _checksum(state, teams, games),
    )


def _renderer_candidates(snapshot: SeasonSnapshot) -> tuple[ForecastCandidate, ...]:
    """Mirror the Release's W1 candidate construction for exact fixture bytes."""
    prior = PreviousFinal({school: float(10 + index) for index, school in enumerate((
        "Home", "Away", "North", "South", "East", "West", "Alpha", "Beta",
    ))}, {})
    ranking_rows = season_rankings(snapshot, 0, prior)[0]
    ranking_by_school = {str(row["school"]): row for row in ranking_rows}
    rating_digest = "sha256:" + hashlib.sha256(canonical_json(ranking_rows)).hexdigest()
    snapshot_digest = "sha256:" + snapshot.checksum.removeprefix("sha256:")
    spread_rows = spreads_for_week(snapshot, 1, ranking_rows, hfa=HFA, legacy_half_point=False)
    games_by_pair = {
        (game.home_team, game.away_team): game
        for game in snapshot.games if int(game.week) == 1
    }
    candidates = []
    for spread in spread_rows:
        game = games_by_pair[(spread["home_team"], spread["away_team"])]
        home = ranking_by_school[game.home_team]
        away = ranking_by_school[game.away_team]
        candidates.append(ForecastCandidate.create(
            game=GameIdentity(
                str(game.provider_id), snapshot.year, int(game.week),
                game.home_team, game.away_team, game.home_classification,
                game.away_classification, bool(game.neutral_site),
            ),
            home_margin=spread["home_margin"], precision=2,
            provenance=ForecastProvenance(
                "W0", "through-week-0", rating_digest, snapshot_digest,
                MODEL_VERSION, "season-snapshot", "working-tree",
                home["cors"], away["cors"],
                0 if game.neutral_site else Decimal(str(HFA)),
                int(home["rank"]), int(away["rank"]),
            ),
        ))
    return tuple(sorted(candidates, key=lambda item: item.game.provider_id))


def _legacy_snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Home", "X"), SourceTeam("Away", "X"))
    games = (
        SourceGame(
            0, "Home", "fbs", 22, "Away", "fbs", 20, False,
            completed=True,
        ),
    )
    state = {
        "schema_version": 2,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2025,
        "teams_fetched_at": None,
        "games_fetched_at": None,
        "complete_through_week": 0,
        "calendar_provenance": None,
        "correction_registry_provenance": None,
        "migration_provenance": None,
    }
    return SeasonSnapshot(
        "cfb", "FBS", 2025, teams, games, MappingProxyType(state),
        _checksum(state, teams, games),
    )


def _issued_candidate() -> ForecastCandidate:
    source_snapshot = _snapshot(next_completed=False)
    rows = season_rankings(
        source_snapshot, 0, PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
    )[0]
    by_school = {str(row["school"]): row for row in rows}
    rating_digest = "sha256:" + hashlib.sha256(canonical_json(rows)).hexdigest()
    return ForecastCandidate.create(
        game=GameIdentity(
            "g-next", 2026, 1, "Home", "Away", "fbs", "fbs", False,
        ),
        home_margin=Decimal("2.24"),
        precision=2,
        provenance=ForecastProvenance(
            "W0", "through-week-0", rating_digest,
            "sha256:" + source_snapshot.checksum, MODEL_VERSION,
            "season-snapshot", "working-tree", Decimal(str(by_school["Home"]["cors"])),
            Decimal(str(by_school["Away"]["cors"])), Decimal("2"),
            int(by_school["Home"]["rank"]), int(by_school["Away"]["rank"]),
        ),
    )


def _authenticated_capability(
    root: Path,
    candidate: ForecastCandidate | tuple[ForecastCandidate, ...],
    *,
    name: str = "issued",
    provider_published_at: datetime | None = None,
):
    """Reconstruct a capability through the fake archive/Firebase boundary."""
    candidates = (candidate,) if isinstance(candidate, ForecastCandidate) else tuple(candidate)
    fx = forecast_fixture(root, candidates)
    backend = None
    if provider_published_at is not None:
        class TimedFakeBackend(FakeFirebasePublicationBackend):
            def release_version(self, target, version):
                result = dict(super().release_version(target, version))
                result["releaseTime"] = provider_published_at.isoformat().replace("+00:00", "Z")
                return result

        backend = TimedFakeBackend(
            TARGET, fx.package.expected_predecessor, managed_identity=APP_IDENTITY,
        )
    _, _, run = publish(
        fx,
        backend=backend,
        name=name,
        clock=lambda: (
            provider_published_at + timedelta(hours=1)
            if provider_published_at is not None
            else datetime(2026, 9, 14, 1, tzinfo=UTC)
        ),
    )
    capability = load_verified_forecasts(
        recorded(run), archive=fx.archive, repository="owner/repository",
        destination=fx.root / "import",
    )
    assert {item.artifact_digest for item in capability.candidates} == {
        item.artifact_digest for item in candidates
    }
    expected_time = provider_published_at or datetime(2026, 9, 14, tzinfo=UTC)
    assert capability.receipts[0].provider_published_at == expected_time
    return capability


def _kickoff() -> datetime:
    return datetime(2026, 9, 15, 12, tzinfo=UTC)


def _prepare_base(path: Path) -> None:
    path.mkdir()
    (path / "index.html").write_text("preserved", encoding="utf-8")
    rankings = path / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
    rankings.parent.mkdir(parents=True)
    rankings.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Home</td><td>10</td><td>0</td></tr>"
        "<tr><td>Away</td><td>20</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    prior_rankings = path / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    prior_rankings.parent.mkdir(parents=True)
    prior_rankings.write_bytes(rankings.read_bytes())


def _prepare_renderer_base(path: Path) -> PreviousFinal:
    """Prepare a carryover tree containing every synthetic renderer team."""
    _prepare_base(path)
    schools = ("Home", "Away", "North", "South", "East", "West", "Alpha", "Beta")
    cors = {school: float(10 + index) for index, school in enumerate(schools)}
    rows = "".join(
        f"<tr><td>{school}</td><td>{value:g}</td><td>0</td></tr>"
        for school, value in cors.items()
    )
    ranking = (
        "<table><thead><tr><th>school</th><th>cors</th>"
        f"<th>wins_vs_expected</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    for year in (2024, 2025):
        (path / f"cfb/years/{year}/rankings/{year}_FINAL_FBS_cors.html").write_text(
            ranking, encoding="utf-8",
        )
    return PreviousFinal(cors, {school: 0.0 for school in schools})


def _html_table_values(page: str, required_header: str) -> dict[str, str]:
    """Read one generated table by its headers, independent of column order."""
    soup = BeautifulSoup(page, "html.parser")
    for table in soup.find_all("table"):
        headers = [cell.get_text(strip=True) for cell in table.find_all("th")]
        if required_header not in headers:
            continue
        values = [cell.get_text(strip=True) for cell in table.find_all("tbody")[0].find_all("td")]
        return dict(zip(headers, values))
    raise AssertionError(f"generated page has no table headed {required_header!r}")


def _rewrite_score_report(
    path: Path,
    *,
    home_score: str,
    away_score: str,
    actual_margin: str,
    straight_up: str,
    coverage: str,
    margin_error: str,
    score_corrected: str,
) -> None:
    """Update only the generated row and summary for an independently forged score."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for table in soup.find_all("table"):
        headers = [cell.get_text(strip=True) for cell in table.find_all("th")]
        rows = table.find_all("tbody")[0].find_all("tr") if table.find_all("tbody") else ()
        if "Actual home margin" in headers:
            target = next(
                row for row in rows
                if [cell.get_text(strip=True) for cell in row.find_all("td")][0:2] == ["1", "Home"]
            )
            cells = target.find_all("td")
            values = {
                "Home score": home_score, "Away score": away_score,
                "Actual home margin": actual_margin, "Straight-up": straight_up,
                "CORS line coverage": coverage, "Margin error": margin_error,
                "Score corrected": score_corrected,
            }
            for header, value in values.items():
                cells[headers.index(header)].string = value
        elif "MAE" in headers:
            target = rows[0]
            cells = target.find_all("td")
            values = {
                "Straight-up record": "1-0", "Straight-up accuracy": "100.00%",
                "CORS coverage": "100.00%", "MAE": "7.760", "RMSE": "7.760",
            }
            for header, value in values.items():
                cells[headers.index(header)].string = value
    path.write_text(str(soup), encoding="utf-8")


def _rewrite_omitted_score_report(
    path: Path,
    *,
    home_score: str,
    away_score: str,
    actual_margin: str,
) -> None:
    """Change only the displayed score for the completed omitted game."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for table in soup.find_all("table"):
        headers = [cell.get_text(strip=True) for cell in table.find_all("th")]
        if "Actual home margin" not in headers:
            continue
        rows = table.find_all("tbody")[0].find_all("tr") if table.find_all("tbody") else ()
        target = next(
            row for row in rows
            if [cell.get_text(strip=True) for cell in row.find_all("td")][0:2] == ["0", "Home"]
        )
        cells = target.find_all("td")
        for header, value in {
            "Home score": home_score,
            "Away score": away_score,
            "Actual home margin": actual_margin,
        }.items():
            cells[headers.index(header)].string = value
    path.write_text(str(soup), encoding="utf-8")


def _reseal_score_revision(revision: dict) -> None:
    content = {key: value for key, value in revision.items() if key != "revision_id"}
    revision["revision_id"] = "sha256:" + hashlib.sha256(canonical_json(content)).hexdigest()


def _ledger(release) -> dict:
    return json.loads(
        (release.site / "cfb/years/2026/forecasts/ledger.json").read_text()
    )


def _evaluation(release) -> dict:
    return json.loads(
        (release.site / "cfb/years/2026/forecasts/evaluation.json").read_text()
    )


def _resign_manifest(release, manifest: dict) -> None:
    checksums = manifest["artifact_checksums"]
    for relative in tuple(checksums):
        path = release.site / relative
        if path.is_file():
            checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    ).hexdigest()
    release.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_lifecycle_preview(destination: str | Path) -> Path:
    """Build a durable synthetic four-release site for human review.

    ``destination`` must be a new or empty directory owned by the caller.
    The returned path is the final candidate site's root; all source inputs
    and generated artifacts stay below that destination.
    """
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    base = root / "published"
    if base.exists() and any(base.iterdir()):
        raise FileExistsError(f"preview destination is not empty: {base}")
    _prepare_base(base)
    candidate = _issued_candidate()
    capability = _authenticated_capability(root / "publication", candidate)
    timing = GameTimingEvidence(
        candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
    )
    first = build_release(
        _snapshot(next_completed=False), root / "first", release_id="first",
        target_week=0, phase="week",
        previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
        timestamp="2026-09-14T02:00:00+00:00", published_site=base,
        forecast_publications=(capability,), timing_evidence=(timing,),
    )
    second = build_release(
        _snapshot(next_completed=False, next_notes="rebuild"), root / "second",
        release_id="second", target_week=0, phase="week",
        previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
        timestamp="2026-09-14T20:00:00+00:00", published_site=first.site,
    )
    third = build_release(
        _snapshot(next_completed=True, next_notes="completed"), root / "third",
        release_id="third", target_week=1, phase="week",
        previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
        timestamp="2026-09-16T20:00:00+00:00", published_site=second.site,
    )
    fourth = build_release(
        _snapshot(next_completed=True, next_home=19, next_away=20), root / "fourth",
        release_id="fourth", target_week=1, phase="week",
        previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
        timestamp="2026-09-17T20:00:00+00:00", published_site=third.site,
    )
    return fourth.site


class ForecastLifecycleIndependentTests(unittest.TestCase):
    def test_same_checkpoint_refresh_keeps_issued_html_when_engine_inputs_change(self):
        candidate = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            refreshed = _authenticated_capability(root / "refresh-publication", candidate, name="refresh")
            timing = GameTimingEvidence(
                candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
            )
            first = build_release(
                _snapshot(next_completed=False), root / "first", release_id="first",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            completed = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "completed",
                release_id="completed", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=first.site,
            )
            refreshed_release = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "refresh",
                release_id="refresh", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T21:00:00+00:00", published_site=completed.site,
                code_revision="fresh-revision", hfa=0,
                forecast_publications=(refreshed,), timing_evidence=(timing,),
            )
            ledger = _ledger(refreshed_release)
            self.assertEqual(
                [item["forecast"]["home_margin"] for item in ledger["candidates"]],
                ["2.24"],
            )
            html = (
                refreshed_release.site
                / "cfb/years/2026/spread/2026_FBS_forecast_results.html"
            ).read_text(encoding="utf-8")
            self.assertIn("2.24", html)
            report = validate_release(
                refreshed_release, published_site=completed.site,
                forecast_publications=(capability, refreshed), timing_evidence=(timing,),
            )
            self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_build_release_pregame_correction_and_late_child_selection(self):
        original = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            original_capability = _authenticated_capability(root / "publication", original)
            timing = GameTimingEvidence(
                original.game, _evidence("actual-start"), actual_started_at=_kickoff(),
            )
            first = build_release(
                _snapshot(next_completed=False), root / "first", release_id="first",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(original_capability,), timing_evidence=(timing,),
            )
            corrected_release = build_release(
                _snapshot(next_completed=False), root / "corrected", release_id="corrected",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T03:00:00+00:00", published_site=first.site,
                forecast_publications=(original_capability,), timing_evidence=(timing,),
                forecast_replacements={original.version_id: "pregame model correction"},
            )
            corrected_ledger = _ledger(corrected_release)
            child_raw = next(
                item for item in corrected_ledger["candidates"]
                if item.get("replacement", {}).get("predecessor_version_id") == original.version_id
            )
            child = ForecastCandidate.from_dict(child_raw)
            self.assertEqual(child.predecessor_version_id, original.version_id)
            self.assertEqual(child.replacement_reason, "pregame model correction")
            child_capability = _authenticated_capability(
                root / "child-publication", child, name="child",
                provider_published_at=datetime(2026, 9, 14, 2, tzinfo=UTC),
            )
            completed = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "completed",
                release_id="completed", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=corrected_release.site,
                forecast_publications=(child_capability,), timing_evidence=(timing,),
            )
            completed_ledger = _ledger(completed)
            completed_evaluation = _evaluation(completed)
            row = next(item for item in completed_evaluation["games"] if item["game"]["provider_id"] == "g-next")
            self.assertEqual(row["grade"]["forecast_version_id"], child.version_id)
            self.assertEqual(
                {item["version_id"] for item in completed_ledger["candidates"]},
                {original.version_id, child.version_id},
            )

            late = ForecastCandidate.create(
                game=child.game, home_margin=Decimal("8.00"), precision=0,
                provenance=child.provenance,
                predecessor_version_id=child.version_id,
                replacement_reason="postgame model correction",
            )
            late_capability = _authenticated_capability(
                root / "late-publication", late, name="late",
                provider_published_at=_kickoff() + timedelta(hours=1),
            )
            from cfb.forecast_release import build_forecast_artifacts
            late_ledger, late_evaluation = build_forecast_artifacts(
                _snapshot(next_completed=True, next_notes="completed"),
                inherited_ledger=completed.site / "cfb/years/2026/forecasts/ledger.json",
                generated_candidates=(late,), forecast_publications=(late_capability,),
                timing_evidence=(timing,), observed_at="2026-09-02T21:00:00+00:00",
            )
            late_row = next(item for item in late_evaluation["games"] if item["game"]["provider_id"] == "g-next")
            self.assertEqual(late_row["grade"]["forecast_version_id"], child.version_id)
            self.assertEqual(
                {item["version_id"] for item in late_ledger["candidates"]},
                {original.version_id, child.version_id, late.version_id},
            )
    def test_successive_releases_preserve_issued_value_and_apply_score_corrections(self):
        candidate = _issued_candidate()
        timing = GameTimingEvidence(
            candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            first = build_release(
                _snapshot(next_completed=False), root / "first", release_id="first",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            first_ledger = _ledger(first)
            issued = first_ledger["candidates"]
            self.assertEqual(len(issued), 1)
            self.assertEqual(issued[0]["forecast"]["home_margin"], "2.24")
            self.assertEqual(first_ledger["receipts"][0]["provider_published_at"], "2026-09-14T00:00:00Z")
            self.assertEqual(first_ledger["timing_evidence"][0]["actual_started_at"], "2026-09-15T12:00:00Z")

            # The ranking engine would produce a different W1 forecast from
            # this changed prior ranking.  With no new authenticated receipt,
            # the issued candidate must remain byte-identical.
            second = build_release(
                _snapshot(next_completed=False, next_notes="rebuild"), root / "second", release_id="second",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T20:00:00+00:00", published_site=first.site,
            )
            second_ledger = _ledger(second)
            self.assertEqual(second_ledger["candidates"], issued)
            self.assertEqual(second_ledger["receipts"], first_ledger["receipts"])
            self.assertEqual(second_ledger["timing_evidence"], first_ledger["timing_evidence"])

            # Once the game is complete, grading uses the preserved issued
            # version and the separately retained actual-start evidence.
            third = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "third", release_id="third",
                target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=second.site,
            )
            row = next(item for item in _evaluation(third)["games"] if item["game"]["provider_id"] == "g-next")
            self.assertEqual(row["disposition"], "evaluated")
            self.assertEqual(row["grade"]["forecast_version_id"], candidate.version_id)
            self.assertEqual(row["grade"]["absolute_error"], "1.76")
            third_html = (
                third.site / "cfb/years/2026/spread/2026_FBS_forecast_results.html"
            ).read_text(encoding="utf-8")
            self.assertIn(">24<", third_html)
            self.assertIn(">20<", third_html)
            self.assertIn(">4<", third_html)
            self.assertIn(">1.76<", third_html)

            # A later source correction changes the grade and revision chain,
            # while the immutable forecast identity/value stay unchanged.
            fourth = build_release(
                _snapshot(next_completed=True, next_home=19, next_away=20),
                root / "fourth", release_id="fourth", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-17T20:00:00+00:00", published_site=third.site,
            )
            fourth_ledger = _ledger(fourth)
            history = next(item for item in fourth_ledger["score_history"] if item["game"]["provider_id"] == "g-next")
            self.assertEqual(len(history["revisions"]), 2)
            self.assertEqual(history["revisions"][0]["observed_at"], "2026-09-16T20:00:00Z")
            self.assertEqual(history["revisions"][1]["observed_at"], "2026-09-17T20:00:00Z")
            corrected = next(item for item in _evaluation(fourth)["games"] if item["game"]["provider_id"] == "g-next")
            self.assertEqual(corrected["grade"]["forecast_version_id"], candidate.version_id)
            self.assertEqual(corrected["grade"]["actual_home_margin"], "-1")
            self.assertEqual(corrected["grade"]["absolute_error"], "3.24")
            fourth_html = (
                fourth.site / "cfb/years/2026/spread/2026_FBS_forecast_results.html"
            ).read_text(encoding="utf-8")
            self.assertIn(">19<", fourth_html)
            self.assertIn(">20<", fourth_html)
            self.assertIn(">-1<", fourth_html)
            self.assertIn(">3.24<", fourth_html)
            self.assertIn("2026-09-17T20:00:00+00:00", fourth_html)
            self.assertIn("CORS coverage", fourth_html)
            self.assertIn("Coverage denominator", fourth_html)
            self.assertIn("Missing forecast", fourth_html)

            for release in (first, second, third, fourth):
                report = validate_release(
                    release, published_site=release.base_site,
                    forecast_publications=(capability,), timing_evidence=(timing,),
                )
                self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_resealed_score_history_and_source_forgery_is_rejected(self):
        """Score bytes, dependent grades, and source observations form one contract."""
        candidate = _issued_candidate()
        timing = GameTimingEvidence(
            candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            first = build_release(
                _snapshot(next_completed=False), root / "first", release_id="first",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            second = build_release(
                _snapshot(next_completed=False, next_notes="rebuild"), root / "second",
                release_id="second", target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T20:00:00+00:00", published_site=first.site,
            )
            third = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "third",
                release_id="third", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=second.site,
            )
            fourth = build_release(
                _snapshot(next_completed=True, next_home=19, next_away=20), root / "fourth",
                release_id="fourth", target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-17T20:00:00+00:00", published_site=third.site,
            )
            baseline = validate_release(
                fourth, published_site=third.site,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            self.assertTrue(baseline.valid, [str(item) for item in baseline.failures])

            ledger_path = fourth.site / "cfb/years/2026/forecasts/ledger.json"
            evaluation_path = fourth.site / "cfb/years/2026/forecasts/evaluation.json"
            report_paths = (
                fourth.site / "cfb/years/2026/spread/2026_W1_FBS_spread_results.html",
                fourth.site / "cfb/years/2026/spread/2026_FBS_forecast_results.html",
            )
            original_ledger = ledger_path.read_bytes()
            original_evaluation = evaluation_path.read_bytes()
            original_reports = tuple(path.read_bytes() for path in report_paths)
            original_manifest = fourth.manifest_path.read_bytes()

            def score_record(value: dict) -> dict:
                return next(
                    item for item in value["score_history"]
                    if item["game"]["provider_id"] == "g-next"
                )

            def evaluation_row(value: dict) -> dict:
                return next(
                    item for item in value["games"]
                    if item["game"]["provider_id"] == "g-next"
                )

            cases = ("forged score and recomputed grade", "changed source digest", "changed observed_at")
            for case in cases:
                with self.subTest(case=case):
                    ledger = json.loads(original_ledger)
                    evaluation = json.loads(original_evaluation)
                    score = score_record(ledger)
                    current = score["revisions"][-1]
                    if case == "forged score and recomputed grade":
                        current["home_points"] = 30
                        _reseal_score_revision(current)
                        grade = evaluation_row(evaluation)["grade"]
                        grade.update({
                            "actual_home_margin": "10",
                            "straight_up": "correct",
                            "coverage": "cover",
                            "absolute_error": "7.76",
                            "squared_error": "60.2176",
                            "score_revision_id": current["revision_id"],
                        })
                        forged_aggregate = {
                            **evaluation["season_summary"],
                            "mae": "7.76", "rmse": "7.76",
                            "straight_up_wins": 1, "straight_up_losses": 0,
                            "covers": 1, "no_covers": 0, "coverage_count": 1,
                            "coverage_percentage": "1",
                        }
                        evaluation["season_summary"] = forged_aggregate
                        evaluation["weekly"]["1"] = copy.deepcopy(forged_aggregate)
                        for path in report_paths:
                            _rewrite_score_report(
                                path, home_score="30", away_score="20", actual_margin="10",
                                straight_up="Correct", coverage="Cover", margin_error="7.76",
                                score_corrected="2026-09-17T20:00:00Z",
                            )
                    elif case == "changed source digest":
                        current["source"]["digest"] = "sha256:" + "d" * 64
                        _reseal_score_revision(current)
                        evaluation_row(evaluation)["grade"]["score_revision_id"] = current["revision_id"]
                    else:
                        current["observed_at"] = "2026-09-17T21:00:00Z"
                        _reseal_score_revision(current)
                        grade = evaluation_row(evaluation)["grade"]
                        grade["score_revision_id"] = current["revision_id"]
                        grade["score_corrected_at"] = "2026-09-17T21:00:00Z"
                        for path in report_paths:
                            _rewrite_score_report(
                                path, home_score="19", away_score="20", actual_margin="-1",
                                straight_up="Incorrect", coverage="No cover", margin_error="3.24",
                                score_corrected="2026-09-17T21:00:00Z",
                            )
                    ledger_path.write_bytes(canonical_json(ledger))
                    evaluation_path.write_bytes(canonical_json(evaluation))
                    manifest = json.loads(original_manifest)
                    _resign_manifest(fourth, manifest)
                    report = validate_release(
                        fourth, published_site=third.site,
                        forecast_publications=(capability,), timing_evidence=(timing,),
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("forecast.contract", {item.code for item in report.failures})
                    ledger_path.write_bytes(original_ledger)
                    evaluation_path.write_bytes(original_evaluation)
                    for path, original in zip(report_paths, original_reports):
                        path.write_bytes(original)
                    fourth.manifest_path.write_bytes(original_manifest)

    def test_omitted_completed_game_score_and_identity_are_source_bound(self):
        """Omitted rows still bind current scores and FinalScore identity to source."""
        initial_snapshot = _renderer_snapshot()
        corrected_snapshot = _renderer_snapshot(missing_home=25)
        candidates = _renderer_candidates(initial_snapshot)
        timing = tuple(
            GameTimingEvidence(
                candidate.game, _evidence(f"renderer-start-{candidate.game.provider_id}"),
                actual_started_at=_kickoff(),
            )
            for candidate in candidates
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            prior = _prepare_renderer_base(base)
            capability = _authenticated_capability(
                root / "publication", candidates, name="omitted-score",
            )
            first = build_release(
                initial_snapshot, root / "first", release_id="first",
                target_week=0, phase="week", previous_final=prior,
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=timing,
            )
            second = build_release(
                corrected_snapshot, root / "second", release_id="second",
                target_week=0, phase="week", previous_final=prior,
                timestamp="2026-09-16T20:00:00+00:00", published_site=first.site,
            )
            baseline = validate_release(
                second, published_site=first.site,
                forecast_publications=(capability,), timing_evidence=timing,
            )
            self.assertTrue(baseline.valid, [str(item) for item in baseline.failures])

            ledger_path = second.site / "cfb/years/2026/forecasts/ledger.json"
            report_paths = (
                second.site / "cfb/years/2026/spread/2026_FBS_forecast_results.html",
            )
            original_ledger = ledger_path.read_bytes()
            original_reports = tuple(path.read_bytes() for path in report_paths)
            original_manifest = second.manifest_path.read_bytes()
            old_score = copy.deepcopy(next(
                item for item in _ledger(first)["score_history"]
                if item["game"]["provider_id"] == "g-missing"
            ))

            for case in ("stale omitted current score", "mismatched FinalScore.game"):
                with self.subTest(case=case):
                    ledger = json.loads(original_ledger)
                    score = next(
                        item for item in ledger["score_history"]
                        if item["game"]["provider_id"] == "g-missing"
                    )
                    if case == "stale omitted current score":
                        # Restore the prior valid revision and its rendered
                        # score, while the current source snapshot says 25-20.
                        score.clear()
                        score.update(copy.deepcopy(old_score))
                        for path in report_paths:
                            _rewrite_omitted_score_report(
                                path, home_score="22", away_score="20", actual_margin="2",
                            )
                    else:
                        # Keep the corrected points but change only the FinalScore
                        # game metadata; the provider key remains g-missing.
                        score["game"]["home"]["name"] = "Tampered Home"
                    ledger_path.write_bytes(canonical_json(ledger))
                    manifest = json.loads(original_manifest)
                    _resign_manifest(second, manifest)
                    report = validate_release(
                        second, published_site=first.site,
                        forecast_publications=(capability,), timing_evidence=timing,
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("forecast.contract", {item.code for item in report.failures})
                    ledger_path.write_bytes(original_ledger)
                    for path, original in zip(report_paths, original_reports):
                        path.write_bytes(original)
                    second.manifest_path.write_bytes(original_manifest)

    def test_generated_forecast_renderer_shows_nontrivial_coverage_and_mobile_semantics(self):
        """Exercise actual Release HTML, including empty and omitted outcomes."""
        snapshot = _renderer_snapshot()
        candidates = _renderer_candidates(snapshot)
        timing = tuple(
            GameTimingEvidence(
                candidate.game, _evidence(f"renderer-start-{candidate.game.provider_id}"),
                actual_started_at=_kickoff(),
            )
            for candidate in candidates
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            prior = _prepare_renderer_base(base)
            capability = _authenticated_capability(
                root / "publication", candidates, name="renderer",
            )
            release = build_release(
                snapshot, root / "candidate", release_id="candidate",
                target_week=0, phase="week", previous_final=prior,
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=timing,
            )
            report = validate_release(
                release, published_site=base,
                forecast_publications=(capability,), timing_evidence=timing,
            )
            self.assertTrue(report.valid, [str(item) for item in report.failures])
            season_page = (
                release.site / "cfb/years/2026/spread/2026_FBS_forecast_results.html"
            ).read_text(encoding="utf-8")
            week_zero_page = (
                release.site / "cfb/years/2026/spread/2026_W0_FBS_spread_results.html"
            ).read_text(encoding="utf-8")

            # This is a real generated report, not a hand-authored index.  Its
            # summary must preserve the exact 1/3 domain ratio as a readable
            # percentage and denominator.
            season_summary = _html_table_values(season_page, "CORS coverage")
            self.assertEqual(season_summary["Evaluated"], "3")
            self.assertEqual(season_summary["CORS coverage"], "33.33%")
            self.assertEqual(season_summary["Coverage denominator"], "3")

            soup = BeautifulSoup(season_page, "html.parser")
            result_table = next(
                table for table in soup.find_all("table")
                if table.find("th", string="Graded Forecast (home handicap)")
            )
            headers = [cell.get_text(strip=True) for cell in result_table.find_all("th")]
            result_rows = [
                dict(zip(headers, [cell.get_text(strip=True) for cell in row.find_all("td")]))
                for row in result_table.find_all("tbody")[0].find_all("tr")
            ]
            missing = next(row for row in result_rows if row["Home"] == "Home")
            self.assertEqual(missing["Home score"], "22")
            self.assertEqual(missing["Away score"], "20")
            self.assertEqual(missing["Actual home margin"], "2")
            self.assertEqual(missing["Graded Forecast (home handicap)"], "—")
            self.assertEqual(missing["CORS line coverage"], "—")
            self.assertEqual(missing["Disposition"], "Missing forecast")

            no_cover = next(row for row in result_rows if row["Home"] == "East")
            self.assertEqual(no_cover["CORS line coverage"], "No cover")
            self.assertNotIn(">no_cover<", season_page)

            # Week 0 has a known final score but no qualifying forecast.  Its
            # zero-sample aggregate uses explicit unavailable marks and zero
            # denominators, rather than blank cells.
            week_zero_summary = _html_table_values(week_zero_page, "CORS coverage")
            self.assertEqual(week_zero_summary["Evaluated"], "0")
            self.assertEqual(week_zero_summary["CORS coverage"], "—")
            self.assertEqual(week_zero_summary["Coverage denominator"], "0")
            self.assertEqual(week_zero_summary["MAE"], "—")
            self.assertEqual(week_zero_summary["RMSE"], "—")

            # The generated forecast report itself carries the responsive
            # viewport and contained table-scroll feature.  The first three
            # columns remain identifiable as Week/Home/Away on narrow views.
            self.assertIn(
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                season_page,
            )
            self.assertIn('class="forecast-report"', season_page)
            self.assertIn("overflow-x:auto", season_page)
            self.assertIn("position:sticky", season_page)
            self.assertIn("<th>Week</th><th>Home</th><th>Away</th>", season_page)

            # Every generated table gets its own horizontal-scroll region.  The
            # identity/sticky class belongs only to the game table; headings
            # remain outside the regions so they cannot scroll over the table.
            document = BeautifulSoup(season_page, "html.parser")
            report_root = document.select_one("div.forecast-report")
            self.assertIsNotNone(report_root)
            regions = report_root.select("div.forecast-table-scroll")
            self.assertEqual(
                [region.get("aria-label") for region in regions],
                ["Games", "Summary", "Omissions"],
            )
            self.assertEqual(len(regions), 3)
            self.assertTrue(all(len(region.find_all("table", recursive=False)) == 1 for region in regions))
            game_tables = report_root.select("table.forecast-games")
            self.assertEqual(len(game_tables), 1)
            self.assertIs(game_tables[0].parent, regions[0])
            self.assertFalse(any(region.select("table.forecast-games") for region in regions[1:]))
            for table in report_root.find_all("table"):
                self.assertIn("forecast-table-scroll", table.parent.get("class", []))
            for heading in report_root.find_all("h2"):
                self.assertNotIn("forecast-table-scroll", heading.parent.get("class", []))

    def test_resealed_semantic_row_tampering_fails_for_identity_disposition_duplicate_and_omission(self):
        candidate = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            timing = GameTimingEvidence(candidate.game, _evidence("actual-start"), actual_started_at=_kickoff())
            release = build_release(
                _snapshot(next_completed=True, next_notes="completed"), root / "candidate", release_id="candidate",
                target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            evaluation_path = release.site / "cfb/years/2026/forecasts/evaluation.json"
            original_evaluation = evaluation_path.read_bytes()
            original_manifest = release.manifest_path.read_bytes()
            evaluated_index = next(
                index for index, item in enumerate(json.loads(original_evaluation)["games"])
                if item["game"]["provider_id"] == "g-next"
            )
            cases = {
                "identity": lambda value: value["games"][evaluated_index]["game"]["home"].__setitem__("name", "Tampered Home"),
                "disposition": lambda value: value["games"][evaluated_index].__setitem__("disposition", "pending"),
                "duplicate": lambda value: value["games"].append(copy.deepcopy(value["games"][evaluated_index])),
                "omission": lambda value: value["omission_counts"].__setitem__("pending", value["omission_counts"].get("pending", 0) + 1),
            }
            for label, mutate in cases.items():
                with self.subTest(case=label):
                    value = json.loads(original_evaluation)
                    mutate(value)
                    evaluation_path.write_bytes(canonical_json(value))
                    manifest = json.loads(original_manifest)
                    _resign_manifest(release, manifest)
                    report = validate_release(
                        release, published_site=base,
                        forecast_publications=(capability,), timing_evidence=(timing,),
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("forecast.contract", {item.code for item in report.failures})
                    evaluation_path.write_bytes(original_evaluation)
                    release.manifest_path.write_bytes(original_manifest)

    def test_resealed_weekly_and_season_forecast_report_text_tampering_fails(self):
        """Report bytes remain bound even when their manifest is resealed."""
        candidate = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            timing = GameTimingEvidence(
                candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
            )
            release = build_release(
                _snapshot(next_completed=False), root / "candidate",
                release_id="candidate", target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            baseline = validate_release(
                release, published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            self.assertTrue(baseline.valid, [str(item) for item in baseline.failures])
            original_manifest = release.manifest_path.read_bytes()
            for relative in (
                "cfb/years/2026/spread/2026_W0_FBS_spread_results.html",
                "cfb/years/2026/spread/2026_FBS_forecast_results.html",
            ):
                with self.subTest(report=relative):
                    path = release.site / relative
                    original = path.read_bytes()
                    # Keep every table cell unchanged; this deliberately tests
                    # arbitrary unbound page bytes after a valid reseal.
                    path.write_bytes(original + b"\n<!-- arbitrary report tamper -->\n")
                    manifest = json.loads(original_manifest)
                    _resign_manifest(release, manifest)
                    report = validate_release(
                        release, published_site=base,
                        forecast_publications=(capability,), timing_evidence=(timing,),
                    )
                    self.assertFalse(report.valid)
                    path.write_bytes(original)
                    release.manifest_path.write_bytes(original_manifest)

    def test_resealed_forecast_provenance_must_match_authenticated_capability(self):
        candidate = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            timing = GameTimingEvidence(
                candidate.game, _evidence("actual-start"), actual_started_at=_kickoff(),
            )
            release = build_release(
                _snapshot(next_completed=False), root / "candidate",
                release_id="candidate", target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-14T02:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            baseline = validate_release(
                release, published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            self.assertTrue(baseline.valid, [str(item) for item in baseline.failures])
            ledger_path = release.site / "cfb/years/2026/forecasts/ledger.json"
            original_ledger = ledger_path.read_bytes()
            original_manifest = release.manifest_path.read_bytes()
            cases = {
                "missing provenance": lambda value: value.pop("publication_provenance"),
                "changed well-formed provider reference": lambda value: value["publication_provenance"][0]["provider_result"].__setitem__("sha256", "c" * 64),
            }
            for label, mutate in cases.items():
                with self.subTest(case=label):
                    value = json.loads(original_ledger)
                    mutate(value)
                    ledger_path.write_bytes(canonical_json(value))
                    manifest = json.loads(original_manifest)
                    _resign_manifest(release, manifest)
                    report = validate_release(
                        release, published_site=base,
                        forecast_publications=(capability,), timing_evidence=(timing,),
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("forecast.contract", {item.code for item in report.failures})
                    ledger_path.write_bytes(original_ledger)
                    release.manifest_path.write_bytes(original_manifest)

    def test_current_run_cannot_strip_forecast_marker_and_artifacts_then_reseal(self):
        candidate = _issued_candidate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            capability = _authenticated_capability(root / "publication", candidate)
            timing = GameTimingEvidence(candidate.game, _evidence("actual-start"), actual_started_at=_kickoff())
            release = build_release(
                _snapshot(next_completed=True), root / "candidate", release_id="candidate",
                target_week=1, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2026-09-16T20:00:00+00:00", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            manifest = json.loads(release.manifest_path.read_text())
            forecast_paths = [
                path for path in manifest["owned_artifacts"]
                if "/forecasts/" in path or path.endswith("_forecast_results.html")
            ]
            for relative in forecast_paths:
                path = release.site / relative
                if path.exists():
                    path.unlink()
                manifest["artifact_checksums"].pop(relative, None)
                manifest["owned_artifacts"].remove(relative)
            for key in ("artifact_contract", "forecast_contract", "forecast_ledger_path", "forecast_evaluation_path"):
                manifest.pop(key, None)
                manifest["runs"][-1].pop(key, None)
            _resign_manifest(release, manifest)
            report = validate_release(release, published_site=base)
            self.assertFalse(report.valid)
            self.assertIn("forecast.contract", {item.code for item in report.failures})

    def test_literal_legacy_v2_run_remains_readable_only_at_explicit_legacy_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            _prepare_base(base)
            release = build_release(
                _legacy_snapshot(), root / "legacy", release_id="legacy",
                target_week=0, phase="week",
                previous_final=PreviousFinal({"Home": 10.0, "Away": 20.0}, {}),
                timestamp="2025-09-01T20:00:00+00:00", published_site=base,
            )
            manifest = json.loads(release.manifest_path.read_text())
            manifest["manifest_version"] = 2
            _resign_manifest(release, manifest)
            legacy_bytes = release.manifest_path.read_bytes()
            report = validate_release(
                release, published_site=base, expected_manifest_version=2,
            )
            self.assertTrue(report.valid, [str(item) for item in report.failures])
            # The same byte-preserved archive is not a current candidate
            # unless the caller explicitly selects the legacy inspection path.
            current = validate_release(release, published_site=base)
            self.assertFalse(current.valid)
            self.assertEqual(release.manifest_path.read_bytes(), legacy_bytes)


if __name__ == "__main__":
    unittest.main()
