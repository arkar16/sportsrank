"""Offline operator reachability and distrust of supplied evidence claims."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cfb.forecast_inputs import (
    HISTORY_SCHEMA, TIMING_SCHEMA, ForecastInputs, load_corrections,
    load_history, load_reviewed_timing,
)
from cfb.forecast_record import EvidenceRef, ForecastContractError, GameTimingEvidence
from cfb.recovery import build_parser
from cfb.github_archive import ArchiveSpec, ArchiveError
from cfb.publication import PublicationExecutionError
from tests.test_forecast_publication import forecast_fixture, publish
from tests.test_forecast_record import candidate, game


def locators(run):
    return {
        "intent": run.attempt.intent_reference.to_dict(),
        "package": run.attempt.package_reference.to_dict(),
        "package_record": run.attempt.package_record_reference.to_dict(),
        "validation": run.attempt.validation_reference.to_dict(),
        "provider_result": run.provider_evidence.record_reference.to_dict(),
        "provider_source": run.provider_evidence.source_reference.to_dict(),
        "verification": run.verification_evidence.record_reference.to_dict(),
        "verification_source": run.verification_evidence.source_reference.to_dict(),
    }


class ForecastInputTests(unittest.TestCase):
    def test_history_retrieves_exact_immutable_attempt_and_rejects_changed_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = candidate(game(), "2.24")
            fx = forecast_fixture(root, [original])
            _, _, run = publish(fx)
            history = root / "history.json"
            raw = {"schema_version": HISTORY_SCHEMA, "publications": [locators(run)]}
            history.write_text(json.dumps(raw))
            actual = load_history(history, archive=fx.archive, repository="owner/repository")
            self.assertEqual(actual[0].candidates, (original,))
            self.assertEqual(actual[0].receipts[0].candidate_artifact_digest, original.artifact_digest)
            raw["publications"][0]["provider_source"]["sha256"] = "0" * 64
            history.write_text(json.dumps(raw))
            with self.assertRaises(ArchiveError):
                load_history(history, archive=fx.archive, repository="owner/repository")

    def test_valid_hash_in_another_immutable_release_is_not_the_source_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = forecast_fixture(root, [candidate(game(), "2.24")])
            _, _, run = publish(fx)
            refs = fx.archive.seal_or_reconcile(ArchiveSpec(
                "owner/repository", "unrelated-source-release", fx.package.candidate_commit,
                {"provider-result-source.json": run.provider_evidence.retrieved_source}, "fixture",
            ))
            item = locators(run)
            item["provider_source"] = refs["provider-result-source.json"].to_dict()
            path = root / "mixed.json"
            path.write_text(json.dumps({"schema_version": HISTORY_SCHEMA, "publications": [item]}))
            with self.assertRaisesRegex(PublicationExecutionError, "roles or bindings"):
                load_history(path, archive=fx.archive, repository="owner/repository")

    def test_reviewed_timing_binds_separate_source_bytes_and_external_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "actual-start-evidence.txt"
            source.write_bytes(b"Synthetic official actual opening kick: 2026-09-20T12:00:00Z")
            digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
            timing = GameTimingEvidence(game(), EvidenceRef("synthetic-official-report", "fixture://kick", digest),
                                        actual_started_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
            path = root / "timing.json"
            path.write_text(json.dumps({"schema_version": TIMING_SCHEMA, "evidence": [timing.to_dict()], "sources": {digest: source.name}}))
            reviewed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(load_reviewed_timing(path, reviewed_hash), (timing,))
            source.write_bytes(b"changed source")
            with self.assertRaisesRegex(ForecastContractError, "source digest"):
                load_reviewed_timing(path, reviewed_hash)
            with self.assertRaisesRegex(ForecastContractError, "supplement digest"):
                load_reviewed_timing(path, "0" * 64)

    def test_cli_accepts_separate_history_timing_and_intentional_correction(self):
        parser = build_parser()
        args = parser.parse_args(["build", "2026", "--phase", "week", "--through-week", "0",
                                  "--release-id", "fixture", "--published-site", "/tmp/base",
                                  "--forecast-history", "/tmp/history", "--forecast-timing", "/tmp/timing",
                                  "--forecast-timing-sha256", "a" * 64, "--forecast-corrections", "/tmp/corrections"])
        self.assertEqual(args.forecast_history, Path("/tmp/history"))
        self.assertEqual(args.forecast_corrections, Path("/tmp/corrections"))
        for command in (["validate", "/tmp/candidate"], ["promote", "/tmp/candidate", "/tmp/base"]):
            self.assertEqual(parser.parse_args(command + ["--forecast-history", "/tmp/history"]).forecast_history, Path("/tmp/history"))

    def test_correction_requires_content_addressed_predecessor_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "correction.json"
            path.write_text(json.dumps({"sha256:" + "a" * 64: "Corrected home-site source"}))
            self.assertEqual(len(load_corrections(path)), 1)
            path.write_text(json.dumps({"trusted": ""}))
            with self.assertRaises(ForecastContractError):
                load_corrections(path)
