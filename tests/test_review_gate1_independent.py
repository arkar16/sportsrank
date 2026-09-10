"""Independent adversarial checks for the refreshed Gate 1 repair scope.

These tests use small portable fixtures and public release/snapshot seams.  The
manifest reseal helpers model an attacker who can update checksums, so a green
result cannot come only from checksum verification.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from cfb.release import (
    ReleaseValidationError,
    build_release,
    grade_ats,
    validate_release,
)
from cfb.ranking_engine import PreviousFinal
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService
from cfb.season_source import SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache
from tools.scripts.postdeploy_smoke import REQUIRED_PATHS, SmokeError, run_smoke


STAMP = "2026-09-09T12:00:00+00:00"
TEAMS = (
    SourceTeam("Alpha State", "Test"),
    SourceTeam("Beta Tech", "Test"),
)


def _independent_snapshot_checksum(state: dict[str, object], teams, games) -> str:
    """Calculate the documented v3 snapshot digest without production helpers."""

    payload = {
        "schema_version": state["schema_version"],
        "sport": state["sport"],
        "classification": state["classification"],
        "year": state["year"],
        "teams_fetched_at": state["teams_fetched_at"],
        "games_fetched_at": state["games_fetched_at"],
        "complete_through_week": state["complete_through_week"],
        "teams": [asdict(team) for team in teams],
        "games": [
            {
                key: value
                for key, value in asdict(game).items()
                if key
                not in {
                    "provider_season_type",
                    "provider_playoff",
                    "phase",
                    "phase_source",
                }
                and (key != "provider_week" or value is not None)
            }
            for game in games
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _snapshot(year: int = 2024) -> SeasonSnapshot:
    """Portable W0 plus incomplete future-week input for release fixtures."""

    games = (
        SourceGame(
            0,
            "Alpha State",
            "fbs",
            21,
            "Beta Tech",
            "fbs",
            14,
            False,
            completed=True,
            disposition="completed",
            disposition_source="independent-fixture",
        ),
        SourceGame(
            1,
            "Beta Tech",
            "fbs",
            None,
            "Alpha State",
            "fbs",
            None,
            False,
            completed=False,
            disposition="scheduled",
            disposition_source="independent-fixture",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": year,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": 0,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        year,
        TEAMS,
        games,
        metadata,
        _independent_snapshot_checksum(dict(metadata), TEAMS, games),
    )


def _published_base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text(
        f"<!doctype html><html><body>Published Site<br>Last updated: {STAMP}</body></html>\n",
        encoding="utf-8",
    )
    home = base / "cfb" / "cfb.html"
    home.parent.mkdir(parents=True)
    home.write_text(
        "<!doctype html><html><head><title>CFB</title></head><body>"
        f"<p>Last updated: {STAMP}</p></body></html>\n",
        encoding="utf-8",
    )
    prior = base / "cfb" / "years" / "2023" / "rankings" / "2023_FINAL_FBS_cors.html"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        "<!doctype html><html><body><table><thead><tr>"
        "<th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha State</td><td>10.0</td><td>0.0</td></tr>"
        "<tr><td>Beta Tech</td><td>8.0</td><td>0.0</td></tr></tbody>"
        "</table></body></html>\n",
        encoding="utf-8",
    )
    return base


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _reseal(candidate) -> None:
    """Recompute candidate checksums after a temporary attack mutation."""

    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in list(manifest.get("artifact_checksums", {})):
        path = candidate.site / relative
        if path.is_file():
            manifest["artifact_checksums"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("manifest_checksum", None)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    manifest["manifest_checksum"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    _write_json(candidate.manifest_path, manifest)


def _remove_owned_artifact(candidate, relative: str) -> None:
    path = candidate.site / relative
    path.unlink()
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for field in ("owned_artifacts", "required_artifacts"):
        manifest[field] = [value for value in manifest[field] if value != relative]
    manifest["artifact_checksums"].pop(relative, None)
    _write_json(candidate.manifest_path, manifest)

    release = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
    for field in ("owned_artifacts", "required_artifacts"):
        release[field] = [value for value in release[field] if value != relative]
    _write_json(candidate.metadata_path, release)
    _reseal(candidate)


def _table_rows(path: Path) -> list[dict[str, str]]:
    document = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    headers = [cell.get_text(strip=True) for cell in document.select("thead th")]
    return [
        dict(zip(headers, (cell.get_text(strip=True) for cell in row.find_all("td"))))
        for row in document.select("tbody tr")
    ]


class PickemLineIndependentTests(unittest.TestCase):
    def test_zero_line_keeps_observed_side_and_is_always_ungraded(self):
        expected = (
            (21, 14, "home"),
            (14, 21, "away"),
            (14, 14, "push"),
        )
        for home_points, away_points, result in expected:
            with self.subTest(result=result):
                self.assertEqual(
                    grade_ats(
                        home_team="Home",
                        away_team="Away",
                        home_points=home_points,
                        away_points=away_points,
                        home_margin_line=0,
                    ),
                    {
                        "favorite": None,
                        "underdog": None,
                        "ats_result": result,
                        "ats_correct": None,
                    },
                )

    def test_zero_line_rendered_result_keeps_home_side_without_ats_grade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _published_base(root)
            prior = base / "cfb" / "years" / "2023" / "rankings" / "2023_FINAL_FBS_cors.html"
            prior.write_text(
                "<!doctype html><html><body><table><thead><tr>"
                "<th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
                "<tbody><tr><td>Alpha State</td><td>8.0</td><td>0.0</td></tr>"
                "<tr><td>Beta Tech</td><td>10.0</td><td>0.0</td></tr></tbody>"
                "</table></body></html>\n",
                encoding="utf-8",
            )
            candidate = build_release(
                _snapshot(),
                root / "pickem-release",
                release_id="pickem-release",
                phase="week",
                target_week=0,
                previous_final=PreviousFinal(
                    {"Alpha State": 8.0, "Beta Tech": 10.0},
                    {"Alpha State": 0.0, "Beta Tech": 0.0},
                    year=2023,
                    classification="FBS",
                ),
                published_site=base,
                timestamp=STAMP,
            )
            report = validate_release(candidate, published_site=base)
            self.assertTrue(report.valid, [str(failure) for failure in report.failures])
            rows = _table_rows(
                candidate.site / "cfb/years/2024/spread/2024_W0_FBS_spread_results.html"
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ats_result"], "home")
        self.assertEqual(rows[0]["favorite"], "")
        self.assertEqual(rows[0]["underdog"], "")
        self.assertEqual(rows[0]["ats_correct"], "")


class ReleaseGraphIndependentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = _published_base(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def build_week_zero(self, name: str = "week-zero"):
        return build_release(
            _snapshot(),
            self.root / name,
            release_id=name,
            phase="week",
            target_week=0,
            previous_final=PreviousFinal(
                {"Alpha State": 10.0, "Beta Tech": 8.0},
                {"Alpha State": 0.0, "Beta Tech": 0.0},
                year=2023,
                classification="FBS",
            ),
            published_site=self.base,
            timestamp=STAMP,
        )

    def test_numbered_w0_owns_and_exactly_validates_preseason(self):
        candidate = self.build_week_zero()
        report = validate_release(candidate, published_site=self.base)
        self.assertTrue(report.valid, [str(failure) for failure in report.failures])

        preseason = "cfb/years/2024/rankings/2024_PRESEASON_FBS_cors.html"
        manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertIn(preseason, manifest["owned_artifacts"])
        self.assertIn(preseason, manifest["required_artifacts"])
        self.assertIn(preseason, manifest["artifact_checksums"])

        page = candidate.site / preseason
        text = page.read_text(encoding="utf-8").replace("10", "9999", 1)
        page.write_text(text, encoding="utf-8")
        _reseal(candidate)
        rejected = validate_release(candidate, published_site=self.base)
        self.assertFalse(rejected.valid)
        self.assertIn("ranking.value", {failure.code for failure in rejected.failures})

    def test_resealed_numbered_w0_cannot_hide_deleted_preseason(self):
        candidate = self.build_week_zero()
        preseason = "cfb/years/2024/rankings/2024_PRESEASON_FBS_cors.html"
        _remove_owned_artifact(candidate, preseason)

        rejected = validate_release(candidate, published_site=self.base)
        self.assertFalse(rejected.valid)
        self.assertIn("artifact.required", {failure.code for failure in rejected.failures})

    def test_malformed_top_and_run_numeric_values_return_structured_failures(self):
        for field in ("season", "target_week", "scheduled_end_week", "complete_through_week"):
            with self.subTest(scope="top", field=field):
                candidate = self.build_week_zero(f"top-{field}")
                manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                manifest[field] = "malformed-number"
                _write_json(candidate.manifest_path, manifest)
                _reseal(candidate)

                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid)
                self.assertTrue(report.failures)
                self.assertTrue(all(hasattr(failure, "code") for failure in report.failures))

        for field in ("season", "target_week"):
            with self.subTest(scope="run", field=field):
                candidate = self.build_week_zero(f"run-{field}")
                manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                manifest["runs"][0][field] = "malformed-number"
                _write_json(candidate.manifest_path, manifest)
                _reseal(candidate)

                report = validate_release(candidate, published_site=self.base)
                self.assertFalse(report.valid)
                self.assertTrue(report.failures)
                self.assertIn("runs.invalid", {failure.code for failure in report.failures})

    def test_inherited_navigation_read_failure_cannot_produce_a_candidate(self):
        output = self.root / "navigation-failure"
        inherited_home = output / "site" / "cfb" / "cfb.html"
        original_read_text = Path.read_text

        def fail_inherited_home(path: Path, *args, **kwargs):
            if path.resolve() == inherited_home.resolve():
                raise OSError("independent navigation read failure")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=fail_inherited_home):
            with self.assertRaises((OSError, ReleaseValidationError, ValueError)):
                self.build_week_zero("navigation-failure")


class CompletionBoundaryIndependentTests(unittest.TestCase):
    def test_rechecksummed_incomplete_disposition_cannot_forge_completion(self):
        class Source:
            def fetch_teams(self, year, classification, *, cache_decision):
                return TEAMS

            def fetch_games(self, year, classification, *, cache_decision):
                return (
                    SourceGame(
                        0,
                        "Alpha State",
                        "fbs",
                        7,
                        "Beta Tech",
                        "fbs",
                        3,
                        False,
                        completed=False,
                        disposition="scheduled",
                        disposition_source="independent-fixture",
                    ),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = SnapshotCache(root / "cache")
            source = Source()
            service = SeasonSnapshotService(source, cache, clock=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
            service.get(2024, "FBS")

            path = cache.path_for(2024, "FBS")
            state = json.loads(path.read_text(encoding="utf-8"))
            state["complete_through_week"] = 99
            # Recompute the stored checksum over the forged metadata, as an
            # attacker would, before asking the public read-only seam to use it.
            checksum_payload = {
                "schema_version": state["schema_version"],
                "sport": state["sport"],
                "classification": state["classification"],
                "year": state["year"],
                "teams_fetched_at": state.get("teams_fetched_at"),
                "games_fetched_at": state.get("games_fetched_at"),
                "complete_through_week": state["complete_through_week"],
                "teams": state["teams"],
                "games": state["games"],
            }
            state["checksum"] = hashlib.sha256(
                json.dumps(checksum_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            _write_json(path, state)

            with self.assertRaisesRegex(ValueError, "completion metadata verification"):
                service.load_cached(2024, "FBS")


class PostdeploySmokeIndependentTests(unittest.TestCase):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler interface
            route = unquote(urlsplit(self.path).path).lstrip("/")
            seen = self.server.seen  # type: ignore[attr-defined]
            seen.append(route)
            status, body = self.server.responses.get(route, (404, b"not found"))  # type: ignore[attr-defined]
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    @contextlib.contextmanager
    def serve(self, responses: dict[str, tuple[int, bytes]]):
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.Handler)
        server.responses = responses  # type: ignore[attr-defined]
        server.seen = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", server.seen  # type: ignore[attr-defined]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def artifact(self, root: Path) -> dict[str, bytes]:
        responses: dict[str, bytes] = {}
        for index, relative in enumerate(REQUIRED_PATHS):
            body = f"validated artifact page {index}: {relative}\n".encode("utf-8")
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            responses[relative] = body
        return responses

    def test_smoke_checks_all_six_pages_in_exact_artifact_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bodies = self.artifact(root)
            responses = {path: (200, body) for path, body in bodies.items()}
            # The public homepage may be requested at either its concrete
            # artifact path or the hosting root; both must compare to the
            # same validated index artifact.
            responses[""] = (200, bodies["index.html"])
            with self.serve(responses) as (url, seen):
                run_smoke(url, root, timeout=2)
        self.assertEqual(tuple(seen[1:]), REQUIRED_PATHS[1:])
        self.assertIn(seen[0], {"", "index.html"})

    def test_smoke_rejects_wrong_200_and_network_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bodies = self.artifact(root)
            fallback = b"<!doctype html><title>generic fallback</title>\n"
            fallback_responses = {path: (200, fallback) for path in REQUIRED_PATHS}
            fallback_responses[""] = (200, fallback)
            with self.serve(fallback_responses) as (url, _seen):
                with self.assertRaisesRegex(SmokeError, "does not match"):
                    run_smoke(url, root, timeout=2)

            responses = {path: (200, body) for path, body in bodies.items()}
            responses[""] = (200, bodies["index.html"])
            with self.serve(responses) as (url, _seen):
                pass
            with self.assertRaisesRegex(SmokeError, "deployed request failed"):
                run_smoke(url, root, timeout=2)

    def test_workflow_uses_existing_uv_pin_and_stable_production_concurrency(self):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "firebase-hosting-publish.yml"
        source = workflow.read_text(encoding="utf-8")
        self.assertIn("uses: astral-sh/setup-uv@v9.0.0", source)
        self.assertIn(
            "concurrency:\n  group: sportsrank-production-publication\n  cancel-in-progress: false",
            source,
        )

    def test_workflow_uses_exact_url_and_restarts_all_pages_after_transient_failure(self):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "firebase-hosting-publish.yml"
        source = workflow.read_text(encoding="utf-8")
        self.assertIn("PUBLISHED_URL: https://www.sportsrank.top", source)
        self.assertNotIn("job.environment.url", source)
        for marker in ("--timeout 10", "--attempts 3", "--retry-delay 2", "--max-duration 180"):
            self.assertIn(marker, source)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bodies = self.artifact(root)
            calls: list[tuple[str, str]] = []
            sleeps: list[float] = []
            now = [0.0]
            failed_once = False

            def fetch_page(base_url: str, relative_path: str, _timeout: float) -> bytes:
                nonlocal failed_once
                calls.append((base_url, relative_path))
                if relative_path == REQUIRED_PATHS[2] and not failed_once:
                    failed_once = True
                    raise SmokeError("transient HTTP 503")
                return bodies[relative_path]

            def clock() -> float:
                return now[0]

            def sleep(seconds: float) -> None:
                sleeps.append(seconds)
                now[0] += seconds

            run_smoke(
                "https://www.sportsrank.top",
                root,
                timeout=10,
                attempts=3,
                retry_delay=2,
                max_duration=180,
                fetch_page=fetch_page,
                sleep=sleep,
                clock=clock,
            )

        self.assertEqual({base for base, _ in calls}, {"https://www.sportsrank.top"})
        self.assertEqual(
            [path for _, path in calls],
            list(REQUIRED_PATHS[:3]) + list(REQUIRED_PATHS),
        )
        self.assertEqual(sleeps, [2])


if __name__ == "__main__":
    unittest.main()
