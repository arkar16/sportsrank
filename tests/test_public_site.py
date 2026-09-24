"""Contract tests for the private-to-public SportsRank export boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from cfb.public_site import (
    LocalValidationError,
    LocalValidationReceipt,
    PublicSiteError,
    PublicSiteValidationError,
    validate_and_export,
    validate_public_output,
    verify_local_receipt,
)
from cfb.recovery_inputs import RecoveryInputBundle
from cfb.public_safety import assert_public_bytes
from cfb.release import build_release
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceTeam


REPOSITORY = Path(__file__).resolve().parents[1]


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fixture(root: Path) -> tuple[Path, Path, RecoveryInputBundle, Path, Path]:
    """Build one tiny complete private candidate and its trusted input bundle."""

    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    state = {
        "schema_version": 3,
        "sport": "cfb",
        "classification": "FBS",
        "year": 1897,
        "teams_fetched_at": "2026-09-01T00:00:00+00:00",
        "games_fetched_at": "2026-09-01T00:00:00+00:00",
        "complete_through_week": -1,
        "teams": teams,
        "games": (),
    }
    checksum = _checksum(state, teams, ())
    snapshot = SeasonSnapshot(
        "cfb",
        "FBS",
        1897,
        teams,
        (),
        {
            "schema_version": 3,
            "teams_fetched_at": state["teams_fetched_at"],
            "games_fetched_at": state["games_fetched_at"],
            "complete_through_week": -1,
        },
        checksum,
    )

    baseline = root / "baseline"
    baseline.mkdir()
    (baseline / "index.html").write_text("retained baseline", encoding="utf-8")

    source = root / "source-inputs"
    (source / "snapshots").mkdir(parents=True)
    raw = {
        "schema_version": 3,
        "sport": "cfb",
        "classification": "FBS",
        "year": 1897,
        "teams_fetched_at": state["teams_fetched_at"],
        "games_fetched_at": state["games_fetched_at"],
        "complete_through_week": -1,
        "teams": [{"school": team.school, "conference": team.conference} for team in teams],
        "games": [],
        "checksum": checksum,
    }
    source_bytes = _canonical(raw)
    source_file = source / "snapshots/cfb-fbs-1897.json"
    source_file.write_bytes(source_bytes)
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    input_manifest = {
        "purpose": "test source inputs",
        "files": [
            {
                "path": "snapshots/cfb-fbs-1897.json",
                "sha256": source_sha,
                "bytes": len(source_bytes),
            }
        ],
    }
    # The direct inputs manifest is accepted as a local trust document.  A
    # production run uses config/sr7-recovery-inputs.json instead.
    manifest_path = source / "inputs-manifest.json"
    manifest_path.write_bytes(_canonical(input_manifest))
    source_inputs = RecoveryInputBundle.from_directory(
        source,
        trusted_file_sha256={"snapshots/cfb-fbs-1897.json": source_sha},
    )

    candidate = build_release(
        snapshot,
        root / "candidate",
        release_id="candidate",
        phase="preseason",
        published_site=baseline,
        source_inputs=source_inputs,
        timestamp="2026-09-01T00:00:00+00:00",
    )
    firebase = root / "firebase.json"
    firebase.write_bytes(_canonical({"hosting": {"public": "website"}}))
    return candidate.site, baseline, source_inputs, firebase, manifest_path


class PublicSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidate, self.baseline, self.source_inputs, self.firebase, self.trust = _fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def export(self) -> tuple[Path, Path, object]:
        site = self.root / "public-site"
        receipt_path = self.root / "receipt.json"
        receipt = validate_and_export(
            self.candidate,
            self.baseline,
            self.source_inputs,
            self.firebase,
            site,
            receipt_path,
            source_root=self.source_inputs.root,
            trusted_input_manifest=self.trust,
            code_root=REPOSITORY,
        )
        return site, receipt_path, receipt

    def test_export_replaces_source_payload_and_verify_uses_only_safe_receipt(self) -> None:
        site, receipt_path, receipt = self.export()
        self.assertTrue(validate_public_output(site).ok)
        release = json.loads((site / "release.json").read_text(encoding="utf-8"))
        inventory_without_release = [
            item for item in receipt.public_site_inventory if item["path"] != "release.json"
        ]
        self.assertEqual(
            release["public_site_inventory_sha256"],
            hashlib.sha256(_canonical(inventory_without_release)).hexdigest(),
        )
        # The hosted verifier receives the checkout and committed trust file,
        # not the retained source bundle.  Removing the raw input after export
        # must not affect verification.
        trusted_copy = self.root / "trusted-input-manifest.json"
        shutil.copyfile(self.trust, trusted_copy)
        shutil.rmtree(self.source_inputs.root / "snapshots")
        verified = verify_local_receipt(
            receipt_path,
            site,
            self.firebase,
            REPOSITORY,
            trusted_copy,
        )
        self.assertEqual(receipt.digest, verified.digest)
        snapshot = json.loads(
            (site / "cfb/years/1897/data/snapshot.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("teams", snapshot)
        self.assertNotIn("games", snapshot)
        self.assertEqual(receipt.inventory_sha256, verified.inventory_sha256)

    def test_changed_ranking_fails_private_independent_validation(self) -> None:
        ranking = next(self.candidate.glob("cfb/years/1897/rankings/*PRESEASON*.html"))
        ranking.write_text(ranking.read_text(encoding="utf-8").replace("Alpha", "Tampered", 1), encoding="utf-8")
        with self.assertRaises(LocalValidationError):
            self.export()
        self.assertFalse((self.root / "public-site").exists())

    def test_stale_receipt_code_site_config_and_pins_fail(self) -> None:
        site, receipt_path, _receipt = self.export()
        # Site bytes are independently inventoried by the receipt.
        (site / "index.html").write_text("changed", encoding="utf-8")
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, self.firebase, REPOSITORY, self.trust)
        (site / "index.html").write_text("retained baseline", encoding="utf-8")

        self.firebase.write_text('{"hosting":{"public":"other"}}', encoding="utf-8")
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, self.firebase, REPOSITORY, self.trust)
        self.firebase.write_bytes(_canonical({"hosting": {"public": "website"}}))

        trust_value = json.loads(self.trust.read_text(encoding="utf-8"))
        trust_value["purpose"] = "changed"
        self.trust.write_bytes(_canonical(trust_value))
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, self.firebase, REPOSITORY, self.trust)

    def test_renamed_source_json_nested_archive_and_symlink_are_rejected(self) -> None:
        site, _receipt_path, _receipt = self.export()
        private_snapshot = self.candidate / "cfb/years/1897/data/snapshot.json"
        (site / "renamed.txt").write_bytes(private_snapshot.read_bytes())
        self.assertIn("source snapshot payload", " ".join(validate_public_output(site).failures))
        (site / "renamed.txt").unlink()

        archive = site / "nested/payload.txt"
        archive.parent.mkdir()
        with tarfile.open(archive, "w") as target:
            target.add(private_snapshot, arcname="snapshot.json")
        self.assertIn("unapproved archive", " ".join(validate_public_output(site).failures))
        shutil.rmtree(archive.parent)

        outside = self.root / "outside"
        outside.write_text("private", encoding="utf-8")
        (site / "link").symlink_to(outside)
        self.assertFalse(validate_public_output(site).ok)

    def test_changed_validator_or_workflow_invalidates_receipt(self) -> None:
        site, receipt_path, _receipt = self.export()
        checkout = self.root / "checkout"
        checkout.mkdir()
        for name in ("cfb", "tools", ".github", "config"):
            shutil.copytree(REPOSITORY / name, checkout / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("pyproject.toml", "uv.lock", "firebase.json"):
            shutil.copyfile(REPOSITORY / name, checkout / name)
        verify_local_receipt(receipt_path, site, self.firebase, checkout, self.trust)
        for name in ("cfb/release.py", ".github/workflows/firebase-hosting-publish.yml"):
            with self.subTest(path=name):
                path = checkout / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# changed after local validation\n")
                with self.assertRaisesRegex(PublicSiteError, "source fingerprint"):
                    verify_local_receipt(receipt_path, site, self.firebase, checkout, self.trust)
                path.write_bytes(original)

    def test_receipt_rejects_hidden_payload_and_failed_validation(self) -> None:
        _site, receipt_path, _receipt = self.export()
        original = receipt_path.read_bytes()
        for field in ("private_evidence", "baseline", "independent_validation", "transform"):
            with self.subTest(field=field):
                value = json.loads(original)
                value[field]["hidden_payload"] = {"teams": [], "games": []}
                with self.assertRaises(PublicSiteError):
                    LocalValidationReceipt.from_bytes(_canonical(value))
        value = json.loads(original)
        value["independent_validation"]["success"] = False
        with self.assertRaises(PublicSiteError):
            LocalValidationReceipt.from_bytes(_canonical(value))

    def test_embedded_source_payload_in_browser_assets_is_rejected(self) -> None:
        site, _receipt_path, _receipt = self.export()
        for suffix in ("html", "js", "css"):
            with self.subTest(suffix=suffix):
                path = site / f"embedded.{suffix}"
                path.write_text('<script>const raw={"snapshot":{"teams":[],"games":[]}}</script>')
                report = validate_public_output(site)
                self.assertIn("embedded source snapshot payload", " ".join(report.failures))
                path.unlink()

    def test_receipt_private_candidate_claims_match_public_manifest(self) -> None:
        site, receipt_path, _receipt = self.export()
        original = receipt_path.read_bytes()
        for field, forged in (("candidate_tree_sha256", "0" * 64),
                              ("candidate_release_id", "forged-release")):
            with self.subTest(field=field):
                value = json.loads(original)
                value["private_evidence"][field] = forged
                receipt_path.write_bytes(_canonical(value))
                with self.assertRaisesRegex(PublicSiteError, "private candidate binding"):
                    verify_local_receipt(receipt_path, site, self.firebase, REPOSITORY, self.trust)
        receipt_path.write_bytes(original)

    def test_public_history_summary_does_not_exempt_provider_team_records(self) -> None:
        public_row = {"school": "Alpha", "conference": "Test", "cors": 1.0,
                      "record": "1-0-0", "win_pct": 1.0,
                      "kind": "National champion", "year": 1897}
        assert_public_bytes("metadata.json", _canonical({"inherited_history": [public_row]}))
        with self.assertRaisesRegex(ValueError, "source snapshot payload"):
            assert_public_bytes("renamed.bin", _canonical([{"school": "Alpha", "conference": "Test"}]))

    def test_reviewed_environment_template_cannot_carry_a_changed_value(self) -> None:
        template = (REPOSITORY / ".env.example").read_bytes()
        assert_public_bytes(".env.example", template)
        with self.assertRaisesRegex(ValueError, "private path"):
            assert_public_bytes(".env.example", template + b"\nCFBD_API_KEY=synthetic-test-value\n")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
