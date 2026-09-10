"""Focused regressions for the second Gate 1 release review."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import patch

from cfb.release import build_release, grade_ats, validate_release
from cfb.ranking_engine import PreviousFinal
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-08T12:00:00+00:00"
TEAMS = (
    SourceTeam("Alpha State", "Test"),
    SourceTeam("Beta Tech", "Test"),
)


def _snapshot(*, complete_through_week: int = 1, incomplete_w0: bool = False) -> SeasonSnapshot:
    games = (
        SourceGame(
            0,
            "Alpha State",
            "fbs",
            None if incomplete_w0 else 21,
            "Beta Tech",
            "fbs",
            None if incomplete_w0 else 14,
            False,
            provider_id="review-w0",
            completed=False if incomplete_w0 else True,
            disposition="in_progress" if incomplete_w0 else "completed",
        ),
        SourceGame(
            1,
            "Beta Tech",
            "fbs",
            17,
            "Alpha State",
            "fbs",
            10,
            False,
            provider_id="review-w1",
            completed=True,
            disposition="completed",
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2025,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": complete_through_week,
        }
    )
    return SeasonSnapshot(
        "cfb",
        "FBS",
        2025,
        TEAMS,
        games,
        metadata,
        _checksum(metadata, TEAMS, games),
    )


def _prior() -> PreviousFinal:
    return PreviousFinal(
        {"Alpha State": 10.0, "Beta Tech": 8.0},
        {"Alpha State": 0.0, "Beta Tech": 0.0},
        year=2024,
        classification="FBS",
    )


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("legacy", encoding="utf-8")
    prior = base / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        "<table><thead><tr><th>school</th><th>cors</th>"
        "<th>wins_vs_expected</th></tr></thead><tbody>"
        "<tr><td>Alpha State</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>8</td><td>0</td></tr>"
        "</tbody></table>",
        encoding="utf-8",
    )
    return base


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _reseal(candidate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_checksums"] = {
        relative: hashlib.sha256((candidate.site / relative).read_bytes()).hexdigest()
        for relative in manifest.get("owned_artifacts", [])
        if (candidate.site / relative).is_file()
    }
    manifest.pop("manifest_checksum", None)
    manifest["manifest_checksum"] = hashlib.sha256(
        _canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")


def _build(root: Path, *, phase: str = "week", target_week: int = 1):
    candidate = build_release(
        _snapshot(),
        root / "candidate",
        release_id="candidate",
        phase=phase,
        target_week=target_week,
        previous_final=_prior(),
        published_site=_base(root),
        timestamp=STAMP,
    )
    return candidate, root / "published"


class ReviewGate1ReleaseRepairTests(unittest.TestCase):
    def test_zero_line_pickem_preserves_actual_side_but_is_ungraded(self):
        self.assertEqual(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=17,
                away_points=14,
                home_margin_line=0,
            ),
            {
                "favorite": None,
                "underdog": None,
                "ats_result": "home",
                "ats_correct": None,
            },
        )
        self.assertIsNone(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=14,
                away_points=17,
                home_margin_line=0,
            )["ats_correct"]
        )
        self.assertEqual(
            grade_ats(
                home_team="Home",
                away_team="Away",
                home_points=17,
                away_points=17,
                home_margin_line=0,
            )["ats_result"],
            "push",
        )

    def test_numbered_checkpoint_owns_and_validates_preseason(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, base = _build(Path(directory))
            relative = "cfb/years/2025/rankings/2025_PRESEASON_FBS_cors.html"
            path = candidate.site / relative
            manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(path.is_file())
            self.assertIn(relative, manifest["owned_artifacts"])
            self.assertTrue(validate_release(candidate, published_site=base).valid)

            path.unlink()
            manifest["owned_artifacts"].remove(relative)
            manifest["required_artifacts"].remove(relative)
            _reseal(candidate)
            report = validate_release(candidate, published_site=base)
            self.assertFalse(report.valid)
            self.assertIn("artifact.required", {failure.code for failure in report.failures})

    def test_incomplete_w0_cannot_claim_a_numbered_completion_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot(complete_through_week=0, incomplete_w0=True)
            self.assertEqual(snapshot.complete_through_week, -1)
            base = _base(root)
            with self.assertRaisesRegex(ValueError, "target_week cannot exceed"):
                build_release(
                    snapshot,
                    root / "candidate",
                    release_id="candidate",
                    phase="week",
                    target_week=0,
                    previous_final=_prior(),
                    published_site=base,
                    timestamp=STAMP,
                )

    def test_malformed_manifest_numbers_are_structured_failures(self):
        mutations = (
            ("season", "bad"),
            ("target_week", []),
            ("target_week", float("nan")),
            ("scheduled_end_week", {}),
            ("complete_through_week", []),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    candidate, base = _build(root)
                    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                    manifest[field] = value
                    candidate.manifest_path.write_text(_canonical_json(manifest), encoding="utf-8")
                    _reseal(candidate)

                    report = validate_release(candidate, published_site=base)

                    self.assertFalse(report.valid)
                    self.assertTrue(report.failures)

    def test_inherited_cfb_home_read_error_aborts_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = _snapshot()
            base = _base(root)
            home = base / "cfb/cfb.html"
            home.parent.mkdir(parents=True, exist_ok=True)
            home.write_text('<a href="legacy.html">Legacy</a>', encoding="utf-8")
            (base / "cfb/legacy.html").write_text("legacy", encoding="utf-8")
            original_read_text = Path.read_text

            def fail_home_read(path, *args, **kwargs):
                if path.as_posix().endswith("cfb/cfb.html"):
                    raise OSError("injected inherited home read failure")
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", new=fail_home_read):
                with self.assertRaisesRegex(OSError, "inherited home read failure"):
                    build_release(
                        snapshot,
                        root / "candidate",
                        release_id="candidate",
                        phase="final",
                        previous_final=_prior(),
                        published_site=base,
                        timestamp=STAMP,
                    )
