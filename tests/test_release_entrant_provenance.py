"""Focused acceptance tests for Classification Entrant carryover evidence."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from cfb.carryover_registry import CLASSIFICATION_ENTRANTS, reconcile_previous_final
from cfb.ranking_engine import PreviousFinal, RankingContractError, validate_previous_final
from cfb.release import build_release, validate_release
from cfb.season_snapshot import SeasonSnapshot, SeasonSnapshotService, _checksum
from cfb.season_source import FixtureSeasonSource, SourceTeam
from cfb.snapshot_cache import SnapshotCache


FIXTURES = Path(__file__).parent / "fixtures" / "cfbd"
FIXED = "2026-09-04T12:00:00+00:00"


def _entrant_snapshot(root: Path) -> SeasonSnapshot:
    source = FixtureSeasonSource(FIXTURES)
    service = SeasonSnapshotService(
        source,
        SnapshotCache(root / "cache"),
        clock=lambda: datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    original = service.get(2025, "FBS")
    teams = original.teams + (SourceTeam("Delaware", "Conference USA"),)
    state = {
        **dict(original.metadata),
        "sport": original.sport,
        "classification": original.classification,
        "year": original.year,
    }
    return SeasonSnapshot(
        sport=original.sport,
        classification=original.classification,
        year=original.year,
        teams=teams,
        games=original.games,
        metadata=MappingProxyType(dict(original.metadata)),
        checksum=_checksum(state, teams, original.games),
    )


def _base(root: Path) -> Path:
    base = root / "published"
    base.mkdir()
    (base / "index.html").write_text("immutable base", encoding="utf-8")
    final = base / "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
        "<tbody><tr><td>Alpha State</td><td>1</td><td>0</td></tr>"
        "<tr><td>Beta Tech</td><td>2</td><td>0</td></tr></tbody></table>",
        encoding="utf-8",
    )
    return base


def _reseal_all(candidate, mutate) -> None:
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    release_json = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
    season_metadata_path = candidate.site / "cfb/years/2025/metadata.json"
    season_metadata = json.loads(season_metadata_path.read_text(encoding="utf-8"))
    for value in (manifest, release_json, season_metadata):
        mutate(value["runs"][0]["carryover"])
    candidate.metadata_path.write_text(
        json.dumps(release_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    season_metadata_path.write_text(
        json.dumps(season_metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for relative in manifest["artifact_checksums"]:
        manifest["artifact_checksums"][relative] = hashlib.sha256(
            (candidate.site / relative).read_bytes()
        ).hexdigest()
    manifest.pop("manifest_checksum", None)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    manifest["manifest_checksum"] = hashlib.sha256(encoded.encode()).hexdigest()
    candidate.manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


class ReleaseEntrantProvenanceTests(unittest.TestCase):
    def test_registry_is_structured_exact_and_only_applies_present_new_entrants(self):
        identities = [(entry.season, entry.classification, entry.team) for entry in CLASSIFICATION_ENTRANTS]
        self.assertEqual(
            identities,
            [
                (2024, "FBS", "Kennesaw State"),
                (2025, "FBS", "Delaware"),
                (2025, "FBS", "Missouri State"),
                (2026, "FBS", "North Dakota State"),
                (2026, "FBS", "Sacramento State"),
            ],
        )
        result = reconcile_previous_final(
            2025, "fbs", {"Returning": 3.0}, {}, {"Returning", "Delaware"}
        )
        self.assertEqual(result.cors, {"Delaware": -10.0, "Returning": 3.0})
        self.assertEqual(result.evidence()["entrants"], [CLASSIFICATION_ENTRANTS[1].evidence()])
        self.assertEqual(result.evidence()["identity_repairs"], [])

        returning = reconcile_previous_final(
            2025, "FBS", {"Delaware": 4.0}, {}, {"Delaware"}
        )
        self.assertEqual(returning.cors, {"Delaware": 4.0})
        self.assertEqual(returning.entrants, ())

    def test_arbitrary_missing_returning_team_still_fails_and_mojibake_is_separate(self):
        snapshot = SeasonSnapshot(
            "cfb", "FBS", 2025,
            (SourceTeam("Returning", "C"), SourceTeam("Unregistered", "C")),
            (), MappingProxyType({"complete_through_week": -1}), "unused",
        )
        result = reconcile_previous_final(
            2025, "FBS", {"Returning": 3.0}, {}, {"Returning", "Unregistered"}
        )
        with self.assertRaisesRegex(RankingContractError, "team set"):
            validate_previous_final(snapshot, PreviousFinal(result.cors, result.wins_vs_expected))

        repaired = reconcile_previous_final(
            2024, "FBS", {"San Jos\N{REPLACEMENT CHARACTER} State": 1.0}, {},
            {"San Jos\N{LATIN SMALL LETTER E WITH ACUTE} State"},
        )
        self.assertEqual(repaired.entrants, ())
        identity_repair = repaired.evidence()["identity_repairs"][0]
        self.assertEqual(identity_repair["canonical_team"], "San José State")
        self.assertEqual(
            identity_repair["source_urls"],
            ["https://sjsuspartans.com/trademark-and-licensing"],
        )
        self.assertIn("canonical", identity_repair["reason"])

    def test_resealed_entrant_provenance_attacks_are_rejected(self):
        for attack in ("omitted", "extra", "baseline", "source", "returning"):
            with self.subTest(attack=attack):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    base = _base(root)
                    candidate = build_release(
                        _entrant_snapshot(root),
                        root / "release",
                        release_id="release",
                        phase="week",
                        target_week=1,
                        timestamp=FIXED,
                        code_revision="test",
                        published_site=base,
                    )
                    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
                    evidence = manifest["runs"][0]["carryover"]
                    self.assertEqual(evidence["entrants"], [CLASSIFICATION_ENTRANTS[1].evidence()])
                    self.assertTrue(validate_release(candidate, published_site=base).valid)

                    def mutate(carryover):
                        if attack == "omitted":
                            carryover["entrants"] = []
                        elif attack == "extra":
                            carryover["entrants"].append(CLASSIFICATION_ENTRANTS[2].evidence())
                        elif attack == "baseline":
                            carryover["entrants"][0]["baseline"] = -9.0
                        elif attack == "source":
                            carryover["entrants"][0]["source_urls"] = ["https://example.invalid/forged"]
                        else:
                            forged = dict(CLASSIFICATION_ENTRANTS[1].evidence())
                            forged["team"] = "Alpha State"
                            carryover["entrants"] = [forged]

                    _reseal_all(candidate, mutate)
                    report = validate_release(candidate, published_site=base)
                    self.assertFalse(report.valid)
                    self.assertIn(
                        "carryover.provenance",
                        {failure.code for failure in report.failures},
                    )


if __name__ == "__main__":
    unittest.main()
