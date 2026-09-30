"""Offline publication evidence tests; all provider and archive operations are fakes."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cfb.github_archive import ArchiveSpec
from cfb.firebase import FakeFirebasePublicationBackend
from cfb.forecast_publication import VerifiedForecastPublication, load_verified_forecasts
from cfb.publication import (
    PreparedPackage, RecordedPublicationAttempt, ReconciliationTags,
    PublicationExecutionError, _deterministic_package, bind_merged_candidate,
)
from cfb.publication_records import canonical_json, ProviderResultRecord
from cfb.publication_timing import publication_instant
from tests.test_forecast_record import candidate, game
from tests.test_publication_execution import (
    APP_IDENTITY, STAMP, TARGET, approval, coordinator, fixture, inventory, sha, tags, validation,
)


def forecast_fixture(root, candidates=()):
    fx = fixture(root)
    for item in candidates:
        path = fx.prepared.site / f"cfb/years/{item.game.season}/forecasts/{item.version_id[7:]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json.dumps(item.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    if not candidates:
        return fx
    repository = root / "repository"
    subprocess.run(["git", "-C", str(repository), "add", "website"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "forecast fixture"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"]).decode().strip()
    files = {f"website/{p.relative_to(fx.prepared.site).as_posix()}": p.read_bytes() for p in fx.prepared.site.rglob("*") if p.is_file()}
    files["firebase.json"] = fx.prepared.firebase_json.read_bytes()
    inv = inventory(files)
    config_sha = sha(files["firebase.json"])
    archive_path = root / "forecast-package.tar.gz"
    bundle_sha = _deterministic_package(fx.prepared.site, fx.prepared.firebase_json, archive_path)
    prepared = PreparedPackage._create(
        archive_path, bundle_sha, inv, config_sha, fx.baseline.digest,
        fx.package.expected_predecessor, "6" * 64,
        validation(inv, config_sha, fx.baseline.digest, "6" * 64),
        fx.prepared.site, fx.prepared.firebase_json,
    )
    fx.prepared = prepared
    fx.package = bind_merged_candidate(prepared, candidate_commit=commit, reader=fx.reader)
    fx.approval, fx.runtime = approval(commit)
    for role in fx.evidence:
        path = root / f"{role}.tar.gz"
        fx.evidence[role] = fx.archive.seal_or_reconcile(ArchiveSpec(
            "owner/repository", f"forecast-retained-{role}", commit,
            {path.name: path}, role,
        ))[path.name]
    return fx


def publish(fx, backend=None, name="issued", clock=None):
    backend = backend or FakeFirebasePublicationBackend(TARGET, fx.package.expected_predecessor, managed_identity=APP_IDENTITY)
    co = coordinator(fx, backend)
    if clock:
        co.clock = clock
    run = co._publish_normal_legacy(
        fx.package, prepared=fx.prepared, commit_reader=fx.reader,
        runtime=fx.runtime, baseline=fx.baseline, evidence_references=fx.evidence,
        tags=tags(name), attempt_id=name, retrieval_directory=fx.root / name,
    )
    return co, backend, run


def recorded(run):
    return RecordedPublicationAttempt(run.attempt, run.provider_result, run.provider_evidence,
                                      run.verification, run.verification_evidence)


class ForecastPublicationTests(unittest.TestCase):
    def test_authentic_provider_time_and_exact_candidate_survive_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            fx = forecast_fixture(Path(directory), [candidate(game(), "2.24")])
            later = datetime(2026, 9, 15, tzinfo=timezone.utc)
            co, backend, run = publish(fx, clock=lambda: later)
            self.assertEqual(run.state, "verified")
            old_result = canonical_json(run.provider_result.to_dict())
            old_source = run.provider_evidence.retrieved_source.read_bytes()
            source = json.loads(old_source)
            self.assertEqual(source["provider_published_at"], "2026-09-14T00:00:00Z")
            self.assertEqual(source["attempt_id"], "issued")
            self.assertEqual(source["artifact_sha256"], fx.package.bundle_sha256)
            issued = load_verified_forecasts(recorded(run), archive=fx.archive, repository="owner/repository", destination=fx.root / "import")
            self.assertEqual(len(issued.candidates), 1)
            self.assertEqual(issued.receipts[0].provider_published_at, STAMP)
            self.assertEqual(issued.receipts[0].candidate_artifact_digest, issued.candidates[0].artifact_digest)
            co.clock = lambda: datetime(2026, 9, 20, tzinfo=timezone.utc)
            rec = co.reconcile(recorded(run), baseline=fx.baseline,
                               tags=ReconciliationTags("rec-observation", "rec-result", "rec-verify"), retrieval_directory=fx.root / "reconcile")
            self.assertEqual(rec.state, "reconciled_verified")
            rebuilt = RecordedPublicationAttempt(run.attempt, rec.provider_result, rec.provider_evidence, rec.verification, rec.verification_evidence)
            imported = load_verified_forecasts(rebuilt, archive=fx.archive, repository="owner/repository", destination=fx.root / "import-reconciled")
            self.assertEqual(imported.receipts[0].provider_published_at, STAMP)
            self.assertEqual(canonical_json(ProviderResultRecord.from_dict(json.loads(old_result)).to_dict()), old_result)
            self.assertEqual(run.provider_evidence.retrieved_source.read_bytes(), old_source)
            self.assertEqual(rec.as_prior().provider_result, rec.provider_result)

    def test_unverified_or_raw_receipts_cannot_create_capability(self):
        with self.assertRaises(PublicationExecutionError):
            VerifiedForecastPublication((), (), {})
        with tempfile.TemporaryDirectory() as directory:
            fx = forecast_fixture(Path(directory), [candidate(game(), "2.24")])
            _, _, run = publish(fx)
            missing = RecordedPublicationAttempt(run.attempt, run.provider_result, run.provider_evidence)
            with self.assertRaises(PublicationExecutionError):
                load_verified_forecasts(missing, archive=fx.archive, repository="owner/repository", destination=fx.root / "missing")
            wrong = replace(run.provider_result, observed_at="2000-01-01T00:00:00Z")
            forged = RecordedPublicationAttempt(run.attempt, wrong, run.provider_evidence)
            with self.assertRaises(PublicationExecutionError):
                load_verified_forecasts(forged, archive=fx.archive, repository="owner/repository", destination=fx.root / "forged")

    def test_invalid_provider_time_fails_without_inventing_time(self):
        class BadTime(FakeFirebasePublicationBackend):
            def release_version(self, target, version):
                value = dict(super().release_version(target, version))
                value["releaseTime"] = ""
                return value
        with tempfile.TemporaryDirectory() as directory:
            fx = forecast_fixture(Path(directory))
            backend = BadTime(TARGET, fx.package.expected_predecessor, managed_identity=APP_IDENTITY)
            _, _, run = publish(fx, backend)
            self.assertNotEqual(run.state, "verified")
            self.assertNotEqual(run.provider_result.outcome, "accepted")
            self.assertNotIn("provider_published_at", json.loads(run.provider_evidence.retrieved_source.read_bytes()))

    def test_time_grammar_rejects_naive_and_non_rfc3339(self):
        for value in ("", "2026-09-01", "2026-09-01T12:00:00", "20260901T120000Z", "2026-09-01T12:00:00+0000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                publication_instant(value)
        self.assertEqual(publication_instant("2026-09-14T01:00:00+01:00"), STAMP)
