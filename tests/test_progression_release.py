"""Focused integration coverage for artifact-contract 4 progression output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from cfb.ranking_engine import PreviousFinal
from cfb.release import build_release, validate_release
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


def _snapshot(year: int) -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Independent"))
    games = (
        SourceGame(
            week=0,
            home_team="Alpha",
            home_classification="fbs",
            home_points=24,
            away_team="Beta",
            away_classification="fbs",
            away_points=17,
            neutral_site=False,
            completed=True,
        ),
    )
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": year,
            "complete_through_week": 0,
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", year, teams, games, metadata, _checksum(metadata, teams, games)
    )


def _base(root: Path) -> tuple[Path, PreviousFinal]:
    root.mkdir()
    (root / "index.html").write_text("preserved", encoding="utf-8")
    final = root / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha</td><td>20</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>19</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return root, PreviousFinal({"Alpha": 20.0, "Beta": 19.0}, {})


class ProgressionReleaseTests(unittest.TestCase):
    def test_current_and_retained_2025_sources_emit_bound_progression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, prior = _base(root / "base")
            release_2025 = build_release(
                _snapshot(2025),
                root / "release-2025",
                release_id="release-2025",
                phase="final",
                target_week=0,
                previous_final=prior,
                timestamp="2026-09-29T12:00:00+00:00",
                published_site=base,
            )
            report = validate_release(release_2025, published_site=base)
            self.assertTrue(report.valid, [str(item) for item in report.failures])
            manifest_2025 = json.loads(release_2025.manifest_path.read_bytes())
            feature_2025 = manifest_2025["progression_feature"]
            prior_path = base / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
            self.assertEqual(
                feature_2025["carryover_identity"],
                "prior-final:" + hashlib.sha256(prior_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(feature_2025["source"]["kind"], "current-run")
            self.assertIn(
                "history/2025_FBS_progression.html",
                (release_2025.site / "cfb/years/2025/2025_CFB.html").read_text(),
            )

            release_2026 = build_release(
                _snapshot(2026),
                root / "release-2026",
                release_id="release-2026",
                phase="week",
                target_week=0,
                timestamp="2026-09-30T12:00:00+00:00",
                published_site=release_2025.site,
            )
            report = validate_release(release_2026, published_site=release_2025.site)
            self.assertTrue(report.valid, [str(item) for item in report.failures])
            manifest_2026 = json.loads(release_2026.manifest_path.read_bytes())
            self.assertEqual(manifest_2026["runs"][:-1], manifest_2025["runs"])
            self.assertEqual(
                manifest_2026["progression_feature"]["source"]["kind"],
                "retained-run",
            )
            self.assertEqual(
                manifest_2026["progression_feature"]["source"]["run_sha256"],
                hashlib.sha256(
                    json.dumps(
                        manifest_2025["runs"][0],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            )
            season_2025 = (
                release_2026.site / "cfb/years/2025/2025_CFB.html"
            ).read_text(encoding="utf-8")
            self.assertIn("Last updated: 2026-09-30T12:00:00+00:00", season_2025)


if __name__ == "__main__":
    unittest.main()
