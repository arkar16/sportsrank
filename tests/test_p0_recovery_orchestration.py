"""Offline contract tests for the six-call P0 recovery orchestration."""

from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from cfb import recovery
from cfb.release import ReleaseValidationError
from cfb.request_meter import MeteredRequestFailed, RequestMeter
from cfb.season_snapshot import SeasonSnapshotService
from cfb.season_source import SourceGame, SourceTeam
from cfb.snapshot_cache import SnapshotCache


NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


class MeteredSource:
    """Small offline source that exercises the real RequestMeter boundary."""

    def __init__(self, meter: RequestMeter, category: str, *, fail_games_year: int | None = None):
        self.meter = meter
        self.category = category
        self.fail_games_year = fail_games_year
        self.calls: list[tuple[str, int, str]] = []

    @staticmethod
    def _teams() -> tuple[SourceTeam, ...]:
        return (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))

    @staticmethod
    def _games() -> tuple[SourceGame, ...]:
        return (
            SourceGame(0, "Alpha", "FBS", 21, "Beta", "FBS", 14, False),
            SourceGame(1, "Beta", "FBS", 17, "Alpha", "FBS", 14, False),
        )

    def fetch_teams(self, year: int, classification: str, *, cache_decision: str):
        self.calls.append(("teams", int(year), cache_decision))
        return self.meter.execute(
            purpose=self.category,
            endpoint="teams",
            season=year,
            cache_decision=cache_decision,
            transport=self._teams,
        )

    def fetch_games(self, year: int, classification: str, *, cache_decision: str):
        self.calls.append(("games", int(year), cache_decision))

        def transport():
            if self.fail_games_year == int(year):
                raise RuntimeError("offline fixture transport failed")
            return self._games()

        return self.meter.execute(
            purpose=self.category,
            endpoint="games",
            season=year,
            cache_decision=cache_decision,
            transport=transport,
        )


class RecoveryOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cache_root = self.root / "shared-cache"
        self.meter = RequestMeter(self.cache_root / "cfbd_requests.sqlite3", clock=lambda: NOW)
        self.scheduled_source = MeteredSource(self.meter, "scheduled")
        self.historical_source = MeteredSource(self.meter, "historical")
        self.scheduled = SeasonSnapshotService(
            self.scheduled_source,
            SnapshotCache(self.cache_root / "snapshots"),
            clock=lambda: NOW,
        )
        self.historical = SeasonSnapshotService(
            self.historical_source,
            SnapshotCache(self.cache_root / "snapshots"),
            clock=lambda: NOW,
        )
        self.published = self.root / "website"
        self.published.mkdir()
        (self.published / "index.html").write_text("original bytes", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_prime_persists_teams_and_games_reuses_them_without_refetching(self):
        teams = self.scheduled.prime_teams(2026, "FBS")
        self.assertEqual(len(teams), 2)
        self.assertTrue(self.scheduled.cache.path_for(2026, "FBS").exists())
        self.assertEqual(self.scheduled_source.calls, [("teams", 2026, "miss")])

        snapshot = self.scheduled.get(2026, "FBS")
        self.assertEqual(len(snapshot.games), 2)
        self.assertEqual(
            self.scheduled_source.calls,
            [("teams", 2026, "miss"), ("games", 2026, "miss")],
        )
        records = self.meter.audit_records()
        self.assertEqual(
            [(row["endpoint"], row["cache_decision"]) for row in records],
            [("teams", "miss"), ("games", "miss")],
        )

        before = len(records)
        cached = self.scheduled.load_cached(2026, "FBS")
        self.assertEqual(cached.checksum, snapshot.checksum)
        self.assertEqual(len(self.meter.audit_records()), before)

    def _fake_build(self, snapshot, output_root, *, release_id, phase, published_site, **kwargs):
        root = Path(output_root) / release_id
        site = root / "site"
        shutil.copytree(published_site, site)
        relative = f"cfb/{snapshot.year}_{phase}.html"
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{snapshot.year} {phase}", encoding="utf-8")
        manifest_path = site / "manifest.json"
        manifest_path.write_text(
            json.dumps({"owned_artifacts": [relative]}) + "\n", encoding="utf-8"
        )
        return SimpleNamespace(root=root, site=site, manifest_path=manifest_path)

    @staticmethod
    def _valid_report(candidate, *args, **kwargs):
        return SimpleNamespace(raise_for_failure=lambda: None)

    def test_backfill_enforces_exact_six_calls_and_chains_without_touching_website(self):
        original = (self.published / "index.html").read_bytes()
        with patch.object(recovery, "build_release", side_effect=self._fake_build) as build:
            with patch.object(
                recovery, "validate_release", side_effect=self._valid_report
            ) as validate:
                with patch.object(
                    recovery, "validate_release_chain", side_effect=self._valid_report
                ) as validate_chain:
                    result = recovery.run_p0_backfill(
                        scheduled_service=self.scheduled,
                        historical_service=self.historical,
                        published_site=self.published,
                        output_root=self.root / "releases",
                        timestamp="2026-09-04T12:00:00+00:00",
                    )

        self.assertEqual(build.call_count, 3)
        self.assertEqual(validate.call_count, 3)
        validate_chain.assert_called_once_with(result.releases[-1], self.published)
        self.assertEqual(
            [call.kwargs["phase"] for call in build.call_args_list],
            ["final", "final", "preseason"],
        )
        self.assertEqual(
            [call.kwargs["published_site"] for call in build.call_args_list],
            [self.published, result.releases[0].site, result.releases[1].site],
        )
        self.assertEqual((self.published / "index.html").read_bytes(), original)
        self.assertEqual(len(result.audit_records), 6)
        self.assertEqual(
            [
                (row["category"], row["season"], row["endpoint"], row["cache_decision"])
                for row in result.audit_records
            ],
            [
                ("scheduled", 2026, "teams", "miss"),
                ("historical", 2024, "teams", "miss"),
                ("historical", 2024, "games", "miss"),
                ("historical", 2025, "teams", "miss"),
                ("historical", 2025, "games", "miss"),
                ("scheduled", 2026, "games", "miss"),
            ],
        )

    def test_backfill_stops_on_failure_without_retrying_or_building(self):
        failing_source = MeteredSource(self.meter, "historical", fail_games_year=2024)
        failing = SeasonSnapshotService(
            failing_source,
            SnapshotCache(self.cache_root / "snapshots"),
            clock=lambda: NOW,
        )
        with patch.object(recovery, "build_release") as build:
            with self.assertRaises(MeteredRequestFailed):
                recovery.run_p0_backfill(
                    scheduled_service=self.scheduled,
                    historical_service=failing,
                    published_site=self.published,
                    output_root=self.root / "releases",
                )
            build.assert_not_called()

        records = self.meter.audit_records()
        self.assertEqual(len(records), 3)
        self.assertEqual(records[-1]["outcome"], "failed")
        self.assertEqual([record["season"] for record in records], [2026, 2024, 2024])
        self.assertNotIn(2025, [record["season"] for record in records])

    def test_existing_smoke_row_is_reused_and_downstream_work_remains_zero_call(self):
        self.scheduled.prime_teams(2026, "FBS")
        before = len(self.meter.audit_records())
        with patch.object(recovery, "build_release", side_effect=self._fake_build):
            with patch.object(recovery, "validate_release", side_effect=self._valid_report):
                with patch.object(
                    recovery, "validate_release_chain", side_effect=self._valid_report
                ):
                    result = recovery.run_p0_backfill(
                        scheduled_service=self.scheduled,
                        historical_service=self.historical,
                        published_site=self.published,
                        output_root=self.root / "releases",
                    )
        self.assertEqual(before, 1)
        self.assertEqual(len(result.audit_records), 6)
        self.assertEqual(len(self.meter.audit_records()), 6)
        self.assertEqual(result.candidate_site, result.releases[-1].site)

    def test_build_parser_requires_explicit_phase_and_removes_ambiguous_flags(self):
        parser = recovery.build_parser()
        args = parser.parse_args(
            [
                "build",
                "2025",
                "--classification",
                "FBS",
                "--phase",
                "week",
                "--through-week",
                "1",
                "--release-id",
                "candidate",
                "--published-site",
                "website",
            ]
        )
        self.assertEqual((args.phase, args.through_week), ("week", 1))
        invalid_common = [
            "build",
            "2025",
            "--phase",
            "week",
            "--through-week",
            "1",
            "--release-id",
            "candidate",
            "--published-site",
            "website",
        ]
        for invalid_flag in ("--week", "--clone-published"):
            with self.subTest(invalid_flag=invalid_flag):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(invalid_common + [invalid_flag, "1"])

    def test_validate_cli_uses_original_chain_and_reports_public_delta(self):
        candidate = self.root / "candidate"
        candidate.mkdir()
        (candidate / "index.html").write_text("candidate bytes", encoding="utf-8")
        (candidate / "new.html").write_text("new bytes", encoding="utf-8")
        report = SimpleNamespace(
            valid=True,
            failures=[],
            legacy_failures=[],
            checked_artifacts=["new.html"],
        )
        args = SimpleNamespace(
            candidate=candidate,
            published_site=self.published,
            as_json=True,
        )
        output = StringIO()
        with patch.object(recovery, "validate_release_chain", return_value=report) as chain:
            with redirect_stdout(output):
                self.assertEqual(recovery._validate(args), 0)

        chain.assert_called_once_with(candidate, self.published)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["added"], ["new.html"])
        self.assertEqual(payload["changed"], ["index.html"])
        self.assertEqual(payload["deleted"], [])

    def test_earlier_run_tampering_fails_final_chain_against_original_site(self):
        prior_path = self.published / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
        prior_path.parent.mkdir(parents=True)
        prior_path.write_text(
            "<html><body><table><thead><tr><th>school</th><th>cors</th></tr></thead>"
            "<tbody><tr><td>Alpha</td><td>10</td></tr><tr><td>Beta</td><td>9</td></tr>"
            "</tbody></table></body></html>",
            encoding="utf-8",
        )
        real_validate = recovery.validate_release
        calls = 0

        def validate_then_tamper(candidate, *, published_site=None):
            nonlocal calls
            report = real_validate(candidate, published_site=published_site)
            calls += 1
            if calls == 1:
                (candidate.site / "index.html").write_text("tampered bytes", encoding="utf-8")
            return report

        with patch.object(recovery, "validate_release", side_effect=validate_then_tamper):
            with self.assertRaises(ReleaseValidationError) as caught:
                recovery.run_p0_backfill(
                    scheduled_service=self.scheduled,
                    historical_service=self.historical,
                    published_site=self.published,
                    output_root=self.root / "releases",
                    timestamp="2026-09-04T12:00:00+00:00",
                )

        self.assertEqual(calls, 3)
        self.assertIsNotNone(caught.exception.report)
        self.assertIn(
            "overlay.unowned",
            {failure.code for failure in caught.exception.report.failures},
        )
        self.assertEqual(
            len(self.meter.audit_records()),
            len(recovery.P0_EXPECTED_REQUESTS),
        )


if __name__ == "__main__":
    unittest.main()
