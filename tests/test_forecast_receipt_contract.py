"""Compatibility and non-removable current preparation expectations."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.public_site import (LocalValidationReceipt, PublicSiteError,
                            _public_manifest_bytes, validate_and_export, verify_local_receipt)
from tests.test_public_site import _fixture, _canonical, REPOSITORY
from cfb.publication import PublicationPreparationError, prepare_reviewed_package


class ForecastReceiptContractTests(unittest.TestCase):
    def test_legacy_receipt_bytes_still_parse_but_cannot_prepare_current_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, baseline, sources, firebase, trust = _fixture(root)
            site, receipt_path = root / "public", root / "receipt.json"
            receipt = validate_and_export(candidate, baseline, sources, firebase, site, receipt_path,
                                          source_root=sources.root, trusted_input_manifest=trust, code_root=REPOSITORY)
            self.assertEqual(receipt.schema_version, 2)
            self.assertEqual(receipt.independent_validation["artifact_contract"], 3)
            self.assertEqual(verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust).digest, receipt.digest)
            with patch("cfb.public_site.CURRENT_RELEASE_CONTRACT", 4):
                self.assertEqual(LocalValidationReceipt.from_bytes(receipt.to_bytes()).to_bytes(), receipt.to_bytes())
                with self.assertRaisesRegex(PublicSiteError, "current Release contract"):
                    verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust)
            # Literal legacy shape: parsing/resealing preserves every original
            # byte and digest. A current-code preparation has a separate gate.
            old = receipt.to_dict()
            old["schema_version"] = 1
            del old["independent_validation"]["artifact_contract"]
            old_bytes = _canonical(old)
            restored = LocalValidationReceipt.from_bytes(old_bytes)
            self.assertEqual(restored.to_bytes(), old_bytes)
            self.assertEqual(restored.digest, hashlib.sha256(old_bytes).hexdigest())
            receipt_path.write_bytes(old_bytes)
            with self.assertRaisesRegex(PublicSiteError, "current Release contract"):
                verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust)

    def test_public_manifest_legacy_shape_preserved_current_shape_requires_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, baseline, sources, firebase, trust = _fixture(root)
            site = root / "public"
            validate_and_export(candidate, baseline, sources, firebase, site, root / "receipt.json",
                                source_root=sources.root, trusted_input_manifest=trust, code_root=REPOSITORY)
            current = json.loads((site / "manifest.json").read_bytes())
            self.assertEqual(current["artifact_contract"], 3)
            missing = dict(current)
            del missing["artifact_contract"]
            with self.assertRaises(PublicSiteError):
                _public_manifest_bytes(missing)
            missing["schema_version"] = 1
            original = _canonical(missing)
            self.assertEqual(_public_manifest_bytes(missing), original)

    def test_hosted_packaging_rejects_resealed_manifest_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate, baseline, sources, firebase, trust = _fixture(root)
            site = root / "public"
            receipt = validate_and_export(candidate, baseline, sources, firebase, site, root / "receipt.json",
                                          source_root=sources.root, trusted_input_manifest=trust, code_root=REPOSITORY)
            manifest = json.loads((site / "manifest.json").read_bytes())
            manifest["schema_version"] = 1
            del manifest["artifact_contract"]
            changed = _canonical(manifest)
            (site / "manifest.json").write_bytes(changed)
            release = json.loads((site / "release.json").read_bytes())
            release["manifest_sha256"] = hashlib.sha256(changed).hexdigest()
            inventory = [dict(item) for item in receipt.public_site_inventory if item["path"] != "release.json"]
            for entry in inventory:
                if entry["path"] == "manifest.json":
                    entry.update(bytes=len(changed), sha256=release["manifest_sha256"])
            release["public_site_inventory_sha256"] = hashlib.sha256(_canonical(inventory)).hexdigest()
            (site / "release.json").write_bytes(_canonical(release))
            with self.assertRaisesRegex(PublicationPreparationError, "current public artifact contract"):
                prepare_reviewed_package(site, firebase_json=firebase,
                    inventory_sha256=receipt.inventory_sha256,
                    configuration_sha256=receipt.configuration_sha256,
                    expected_baseline_sha256="a" * 64,
                    expected_predecessor=receipt.expected_predecessor,
                    retained_inputs_sha256="b" * 64,
                    validation_sha256=receipt.validation_sha256,
                    output=root / "must-not-exist.tar.gz")
            self.assertFalse((root / "must-not-exist.tar.gz").exists())
