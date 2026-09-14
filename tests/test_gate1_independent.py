"""Independent Gate 1 checks for cumulative release graph integrity."""

from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from types import MappingProxyType

from cfb.ranking_engine import PreviousFinal
from cfb.release import build_release, validate_release_chain
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-08T12:00:00+00:00"


def _snapshot(year: int) -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    games = (SourceGame(1, "Alpha", "FBS", 21, "Beta", "FBS", 14, False),)
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": year,
            "teams_fetched_at": STAMP,
            "games_fetched_at": STAMP,
            "complete_through_week": 1,
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", year, teams, games, metadata, _checksum(metadata, teams, games)
    )


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("published bytes", encoding="utf-8")
    prior = base / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
    prior.parent.mkdir(parents=True)
    prior.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>9</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return base


def _reseal(candidate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    for relative in manifest["artifact_checksums"]:
        manifest["artifact_checksums"][relative] = hashlib.sha256(
            (candidate.site / relative).read_bytes()
        ).hexdigest()
    manifest.pop("manifest_checksum", None)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    manifest["manifest_checksum"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    candidate.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class Gate1IndependentTests(unittest.TestCase):
    def test_resealed_cumulative_final_rejects_deleted_prior_preseason_and_w0(self):
        for victim in (
            "cfb/years/2024/rankings/2024_PRESEASON_FBS_cors.html",
            "cfb/years/2024/spread/2024_W0_FBS_spread.html",
        ):
            with self.subTest(victim=victim), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = _base(root)
                first = build_release(
                    _snapshot(2024),
                    root / "2024-final",
                    release_id="2024-final",
                    phase="final",
                    timestamp=STAMP,
                    published_site=base,
                    previous_final=PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}),
                )
                second = build_release(
                    _snapshot(2025),
                    root / "2025-final",
                    release_id="2025-final",
                    phase="final",
                    timestamp=STAMP,
                    published_site=first.site,
                )
                victim_path = second.site / victim
                self.assertTrue(victim_path.is_file())
                victim_path.unlink()

                manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
                for field in ("owned_artifacts", "required_artifacts"):
                    manifest[field].remove(victim)
                manifest["artifact_checksums"].pop(victim)
                metadata = json.loads(second.metadata_path.read_text(encoding="utf-8"))
                for field in ("owned_artifacts", "required_artifacts"):
                    metadata[field].remove(victim)
                second.metadata_path.write_text(
                    json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                second.manifest_path.write_text(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                _reseal(second)

                report = validate_release_chain(second, base)

                self.assertFalse(report.valid)
                self.assertIn("artifact.required", {failure.code for failure in report.failures})


if __name__ == "__main__":
    unittest.main()
