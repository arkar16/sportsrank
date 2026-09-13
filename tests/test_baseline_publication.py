"""Behavioral checks for provider-backed Published Site baselines."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import tarfile
from types import MappingProxyType
import unittest

from cfb.baseline import BaselineValidationError, VerifiedBaseline, import_baseline
from cfb.firebase import FakeFirebaseReadBackend, FirebaseReadAdapter
from cfb.publication_records import (
    AttemptIntentRecord,
    BaselineRecord,
    ManagedResourceEvidence,
    ProviderResultRecord,
    ProviderIdentity,
    ProviderTarget,
    RecordValidationError,
    SourceProvenance,
    ValidatedPackageRecord,
    VerificationRecord,
)
from cfb.ranking_engine import PreviousFinal
from cfb.release import build_release, validate_release
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


STAMP = "2026-09-13T12:00:00+00:00"
TARGET = {"project": "fixture-project", "site": "fixture-site", "channel": "live"}
RELEASE = "sites/fixture-site/channels/live/releases/release-1"
VERSION = "sites/fixture-site/versions/version-1"
MANAGED = ("__/firebase/init.js", "__/firebase/init.json")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _provider_config() -> dict[str, str]:
    return {
        "authDomain": "fixture-project.firebaseapp.com",
        "messagingSenderId": "1234",
        "projectId": "fixture-project",
        "storageBucket": "fixture-project.firebasestorage.app",
    }


def _evidence(root: Path, *, extra_managed: bool = False) -> Path:
    config = _provider_config()
    raw_files = {
        "index.html": b"published fixture",
        "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html": (
            b"<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead>"
            b"<tbody><tr><td>Alpha</td><td>10</td><td>0</td></tr>"
            b"<tr><td>Beta</td><td>9</td><td>0</td></tr></tbody></table>"
        ),
        "__/firebase/init.json": json.dumps(config, sort_keys=True).encode(),
        "__/firebase/init.js": ("firebase.initializeApp(" + json.dumps(config, sort_keys=True) + ");").encode(),
    }
    if extra_managed:
        raw_files["__/firebase/other.json"] = b"{}"
    files = []
    inventory = []
    for relative, raw in sorted(raw_files.items()):
        payload = gzip.compress(raw, compresslevel=9, mtime=0)
        provider_sha = hashlib.sha256(payload).hexdigest()
        raw_sha = hashlib.sha256(raw).hexdigest()
        site_path = root / "site" / relative
        site_path.parent.mkdir(parents=True, exist_ok=True)
        site_path.write_bytes(raw)
        payload_path = root / "provider-payloads" / f"{provider_sha}.gz"
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_bytes(payload)
        inventory.append({"path": f"/{relative}", "relative": relative, "provider_sha256": provider_sha, "status": "ACTIVE"})
        files.append({
            **inventory[-1],
            "sha256": raw_sha,
            "bytes": len(raw),
            "verification": "exact-provider-gzip",
            "provider_payload": f"provider-payloads/{provider_sha}.gz",
            "url": f"https://fixture.invalid/{relative}",
            "etag": None,
            "last_modified": None,
        })
    channel = {
        "name": "sites/fixture-site/channels/live",
        "release": {"name": RELEASE, "version": {"name": VERSION}},
    }
    version = {"name": VERSION, "status": "FINALIZED", "config": {}, "fileCount": str(len(files))}
    _write_json(root / "channel-before.json", channel)
    _write_json(root / "channel-after.json", channel)
    _write_json(root / "version.json", version)
    _write_json(root / "inventory.json", inventory)
    metadata_sha = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("channel-before.json", "channel-after.json", "version.json", "inventory.json")
    }
    capture = {
        "schema_version": 1,
        "site": "fixture-site",
        "started_at": STAMP,
        "completed_at": STAMP,
        "live": {"release": RELEASE, "version": VERSION},
        "source_commit": None,
        "source_commit_status": "unknown",
        "status": "verified",
        "file_count": len(files),
        "total_bytes": sum(entry["bytes"] for entry in files),
        "files": files,
        "config_sha256": hashlib.sha256(b"{}").hexdigest(),
        "metadata_sha256": metadata_sha,
    }
    _write_json(root / "capture.json", capture)
    return root


def _archive(evidence: Path, archive: Path) -> str:
    with tarfile.open(archive, "w:gz") as target:
        for path in sorted(evidence.rglob("*")):
            target.add(path, arcname=path.relative_to(evidence).as_posix(), recursive=False)
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def _snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    games = (SourceGame(1, "Alpha", "FBS", 21, "Beta", "FBS", 14, False),)
    metadata = MappingProxyType({
        "schema_version": 3,
        "sport": "cfb",
        "classification": "FBS",
        "year": 2024,
        "teams_fetched_at": STAMP,
        "games_fetched_at": STAMP,
        "complete_through_week": 1,
    })
    return SeasonSnapshot("cfb", "FBS", 2024, teams, games, metadata, _checksum(metadata, teams, games))


class BaselineImportTests(unittest.TestCase):
    def test_verified_unknown_source_baseline_drives_existing_release_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = _evidence(root / "evidence")
            archive = root / "baseline.tar.gz"
            archive_sha = _archive(evidence, archive)
            baseline = import_baseline(
                archive,
                target=TARGET,
                expected_archive_sha256=archive_sha,
                materialize_to=root / "application-site",
            )

            self.assertIsNone(baseline.record.source.commit)
            self.assertEqual(baseline.record.source.status, "unknown")
            self.assertEqual(tuple(item.path for item in baseline.record.managed_resources), tuple(f"/{p}" for p in MANAGED))
            self.assertNotIn("apiKey", baseline.record.managed_resources[0].app_identity)
            self.assertEqual(BaselineRecord.from_dict(baseline.record.to_dict()), baseline.record)
            self.assertFalse((baseline.application_site / MANAGED[0]).exists())
            self.assertTrue((baseline.application_site / "index.html").is_file())

            candidate = build_release(
                _snapshot(),
                root / "candidate",
                release_id="candidate",
                phase="week",
                timestamp=STAMP,
                published_site=baseline,
                previous_final=PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}),
            )
            report = validate_release(candidate, published_site=baseline)
            self.assertTrue(report.valid, [str(failure) for failure in report.failures])

    def test_import_rejects_corruption_mixed_observations_and_extra_reserved_resources(self):
        attacks = ("missing", "raw", "payload", "mixed", "config", "extra-managed")
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                evidence = _evidence(root / "evidence", extra_managed=attack == "extra-managed")
                if attack == "missing":
                    (evidence / "inventory.json").unlink()
                elif attack == "raw":
                    (evidence / "site/index.html").write_bytes(b"tampered")
                elif attack == "payload":
                    payload = next((evidence / "provider-payloads").iterdir())
                    payload.write_bytes(b"tampered")
                elif attack == "mixed":
                    after = json.loads((evidence / "channel-after.json").read_text())
                    after["release"]["name"] = "sites/fixture-site/channels/live/releases/other"
                    _write_json(evidence / "channel-after.json", after)
                elif attack == "config":
                    version = json.loads((evidence / "version.json").read_text())
                    version["config"] = {"cleanUrls": True}
                    _write_json(evidence / "version.json", version)
                    capture = json.loads((evidence / "capture.json").read_text())
                    capture["metadata_sha256"]["version.json"] = hashlib.sha256(
                        (evidence / "version.json").read_bytes()
                    ).hexdigest()
                    _write_json(evidence / "capture.json", capture)
                archive = root / "baseline.tar.gz"
                archive_sha = _archive(evidence, archive)
                with self.assertRaises(BaselineValidationError):
                    import_baseline(archive, target=TARGET, expected_archive_sha256=archive_sha)

    def test_import_requires_actual_archive_bytes_to_match_the_external_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = _evidence(root / "evidence")
            archive = root / "baseline.tar.gz"
            _archive(evidence, archive)
            with self.assertRaisesRegex(BaselineValidationError, "trusted evidence"):
                import_baseline(
                    archive,
                    target=TARGET,
                    expected_archive_sha256="0" * 64,
                )

    def test_default_materialization_lives_until_verified_baseline_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = _evidence(root / "evidence")
            archive = root / "baseline.tar.gz"
            archive_sha = _archive(evidence, archive)
            baseline = import_baseline(
                archive,
                target=TARGET,
                expected_archive_sha256=archive_sha,
            )
            materialized = baseline.application_site
            self.assertTrue((materialized / "index.html").is_file())
            baseline.assert_current()
            baseline.close()
            self.assertFalse(materialized.exists())

    def test_release_seam_rejects_forged_or_post_import_mutated_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(BaselineValidationError):
                VerifiedBaseline(None, root, root)  # type: ignore[arg-type]
            evidence = _evidence(root / "evidence")
            archive = root / "baseline.tar.gz"
            archive_sha = _archive(evidence, archive)
            baseline = import_baseline(
                archive,
                target=TARGET,
                expected_archive_sha256=archive_sha,
                materialize_to=root / "application-site",
            )
            candidate = build_release(
                _snapshot(),
                root / "candidate",
                release_id="candidate",
                phase="week",
                timestamp=STAMP,
                published_site=baseline,
                previous_final=PreviousFinal({"Alpha": 10.0, "Beta": 9.0}, {}),
            )
            (baseline.application_site / "index.html").write_bytes(b"changed after verification")
            report = validate_release(candidate, published_site=baseline)
            self.assertFalse(report.valid)
            self.assertIn("changed after import", report.failures[0].message)


class FirebaseCaptureTests(unittest.TestCase):
    def test_read_adapter_captures_stable_provider_state_and_rejects_a_mixed_capture(self):
        raw = {relative: (_evidence_bytes(relative)) for relative in (*MANAGED, "index.html")}
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                backend = FakeFirebaseReadBackend(TARGET, raw, serving_config={}, change_after_capture=changed)
                adapter = FirebaseReadAdapter(TARGET, backend)
                if changed:
                    with self.assertRaisesRegex(BaselineValidationError, "changed during capture"):
                        adapter.capture(Path(directory) / "capture")
                else:
                    baseline = adapter.capture(Path(directory) / "capture")
                    self.assertEqual(baseline.record.observed.release, backend.release)
                    self.assertEqual(backend.write_count, 0)

    def test_read_adapter_rejects_incomplete_inventory_and_configuration_races(self):
        raw = {relative: _evidence_bytes(relative) for relative in (*MANAGED, "index.html")}
        attacks = (
            {"reported_file_count": 99},
            {"change_configuration_after_capture": True},
        )
        for index, options in enumerate(attacks):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                backend = FakeFirebaseReadBackend(TARGET, raw, serving_config={}, **options)
                adapter = FirebaseReadAdapter(TARGET, backend)
                with self.assertRaises(BaselineValidationError):
                    adapter.capture(Path(directory) / f"capture-{index}")


def _evidence_bytes(relative: str) -> bytes:
    config = _provider_config()
    if relative.endswith("init.json"):
        return json.dumps(config, sort_keys=True).encode()
    if relative.endswith("init.js"):
        return ("firebase.initializeApp(" + json.dumps(config, sort_keys=True) + ");").encode()
    return b"published fixture"


class SharedRecordTests(unittest.TestCase):
    def test_records_are_strictly_versioned_linked_and_keep_unknown_outcomes_explicit(self):
        package = ValidatedPackageRecord.create(
            candidate_commit="c" * 40,
            bundle_sha256="1" * 64,
            inventory_sha256="2" * 64,
            configuration_sha256="3" * 64,
            expected_baseline_sha256="4" * 64,
            retained_inputs_sha256="5" * 64,
            validation_sha256="6" * 64,
        )
        intent = AttemptIntentRecord.create(
            attempt_id="attempt-1",
            purpose="normal",
            package=package,
            expected_predecessor={"target": TARGET, "release": RELEASE, "version": VERSION},
            artifact_reference="immutable://package-1",
            intent_reference="immutable://intent-1",
            protected_context={"workflow_ref": "refs/heads/main"},
        )
        result = ProviderResultRecord.create_unknown(
            intent=intent,
            observed_target=TARGET,
            observed_release=None,
            observed_version=None,
            observed_at=STAMP,
            source_sha256="7" * 64,
        )
        verification = VerificationRecord.create_unknown(
            intent=intent,
            provider_result=result,
            observed_at=STAMP,
            findings=("provider receipt unavailable",),
            source_sha256="8" * 64,
        )

        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(verification.outcome, "unknown")
        self.assertEqual(result.intent_sha256, intent.digest)
        self.assertEqual(verification.provider_result_sha256, result.digest)
        self.assertEqual(ValidatedPackageRecord.from_dict(package.to_dict()), package)
        self.assertEqual(AttemptIntentRecord.from_dict(intent.to_dict(), package=package), intent)
        self.assertEqual(ProviderResultRecord.from_dict(result.to_dict(), intent=intent), result)
        self.assertEqual(
            VerificationRecord.from_dict(
                verification.to_dict(), intent=intent, provider_result=result
            ),
            verification,
        )
        forged = result.to_dict()
        forged["intent_sha256"] = "0" * 64
        with self.assertRaises(RecordValidationError):
            ProviderResultRecord.from_dict(forged, intent=intent)
        extra = package.to_dict()
        extra["caller_approved"] = True
        with self.assertRaises(RecordValidationError):
            ValidatedPackageRecord.from_dict(extra)

    def test_caller_constructed_values_cannot_bypass_target_source_or_identity_rules(self):
        with self.assertRaises(RecordValidationError):
            ProviderTarget("fixture-project", "fixture-site", "preview")
        with self.assertRaises(RecordValidationError):
            ProviderTarget("fixture-project", "../other-site", "live")
        with self.assertRaises(RecordValidationError):
            SourceProvenance("unknown", "c" * 40)
        target = ProviderTarget.from_value(TARGET)
        with self.assertRaises(RecordValidationError):
            ProviderIdentity(
                target,
                "sites/fixture-site/channels/preview/releases/release-1",
                VERSION,
            )
        with self.assertRaises(RecordValidationError):
            SourceProvenance("known", "c" * 41)

    def test_verification_rejects_a_provider_result_from_another_attempt(self):
        package = ValidatedPackageRecord.create(
            candidate_commit="c" * 40,
            bundle_sha256="1" * 64,
            inventory_sha256="2" * 64,
            configuration_sha256="3" * 64,
            expected_baseline_sha256="4" * 64,
            retained_inputs_sha256="5" * 64,
            validation_sha256="6" * 64,
        )
        values = {
            "purpose": "normal",
            "package": package,
            "expected_predecessor": {"target": TARGET, "release": RELEASE, "version": VERSION},
            "artifact_reference": "immutable://package-1",
            "intent_reference": "immutable://intent-1",
            "protected_context": {"workflow_ref": "refs/heads/main"},
        }
        first = AttemptIntentRecord.create(attempt_id="attempt-1", **values)
        second = AttemptIntentRecord.create(attempt_id="attempt-2", **values)
        second_result = ProviderResultRecord.create_unknown(
            intent=second,
            observed_target=TARGET,
            observed_release=None,
            observed_version=None,
            observed_at=STAMP,
            source_sha256="7" * 64,
        )
        with self.assertRaises(RecordValidationError):
            ProviderResultRecord.create_unknown(
                intent=first,
                observed_target={
                    "project": "other-project",
                    "site": "other-site",
                    "channel": "live",
                },
                observed_release=None,
                observed_version=None,
                observed_at=STAMP,
                source_sha256="7" * 64,
            )
        with self.assertRaises(RecordValidationError):
            VerificationRecord.create_unknown(
                intent=first,
                provider_result=second_result,
                observed_at=STAMP,
                findings=("receipt unavailable",),
                source_sha256="8" * 64,
            )

    def test_direct_record_constructors_copy_mutable_containers(self):
        target = ProviderTarget.from_value(TARGET)
        predecessor = ProviderIdentity(target, RELEASE, VERSION)
        context = {"workflow_ref": "refs/heads/main"}
        intent = AttemptIntentRecord(
            "attempt-1",
            "normal",
            "1" * 64,
            predecessor,
            "immutable://package-1",
            "immutable://intent-1",
            context,
        )
        original_digest = intent.digest
        context["workflow_ref"] = "refs/heads/attacker"
        self.assertEqual(intent.protected_context["workflow_ref"], "refs/heads/main")
        self.assertEqual(intent.digest, original_digest)

        app_identity = {
            "project_id": "fixture-project",
            "messaging_sender_id": "1234",
            "auth_domain": "fixture-project.firebaseapp.com",
            "storage_bucket": "fixture-project.firebasestorage.app",
        }
        managed = [
            ManagedResourceEvidence(f"/{relative}", "2" * 64, "3" * 64, 1, app_identity)
            for relative in MANAGED
        ]
        private_hashes = {"capture.json": "4" * 64}
        baseline_record = BaselineRecord(
            target,
            predecessor,
            predecessor,
            predecessor,
            STAMP,
            "5" * 64,
            "6" * 64,
            "7" * 64,
            "8" * 64,
            2,
            2,
            SourceProvenance("unknown", None),
            managed,  # type: ignore[arg-type]
            private_hashes,
            "allowlisted-v1",
        )
        baseline_digest = baseline_record.digest
        managed.clear()
        private_hashes["capture.json"] = "9" * 64
        app_identity["project_id"] = "attacker"
        self.assertEqual(len(baseline_record.managed_resources), 2)
        self.assertEqual(
            baseline_record.managed_resources[0].app_identity["project_id"],
            "fixture-project",
        )
        self.assertEqual(baseline_record.digest, baseline_digest)

        result = ProviderResultRecord.create_unknown(
            intent=intent,
            observed_target=TARGET,
            observed_release=None,
            observed_version=None,
            observed_at=STAMP,
            source_sha256="a" * 64,
        )
        managed_findings = {"/__/firebase/init.js": "unknown"}
        page_findings = {"/index.html": "unknown"}
        findings = ["receipt unavailable"]
        verification = VerificationRecord(
            intent.attempt_id,
            intent.digest,
            result.digest,
            None,
            None,
            None,
            None,
            managed_findings,
            page_findings,
            "unknown",
            STAMP,
            findings,  # type: ignore[arg-type]
            "b" * 64,
            "allowlisted-v1",
        )
        verification_digest = verification.digest
        managed_findings.clear()
        page_findings.clear()
        findings.clear()
        self.assertEqual(verification.digest, verification_digest)


if __name__ == "__main__":
    unittest.main()
