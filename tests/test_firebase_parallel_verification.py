"""Full application-byte proof with controlled concurrent provider responses."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest

from cfb.firebase import (
    FakeFirebasePublicationBackend, FirebaseDeployArtifact, FirebasePublicationAdapter,
    FirebasePublicationError,
)
from tests.test_publication_execution import fixture, TARGET, APP_IDENTITY
from tools.scripts.postdeploy_smoke import REQUIRED_PATHS


class ControlledReads(FakeFirebasePublicationBackend):
    def __init__(self, predecessor, *, failure=None):
        super().__init__(TARGET, predecessor, managed_identity=APP_IDENTITY)
        self.failure = failure
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.release_reads = threading.Event()
        self.failed = threading.Event()
        self.active = 0
        self.peak = 0
        self.seen = Counter()

    def public_resource(self, target, path):
        with self.lock:
            self.seen[path] += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active >= 16:
                self.ready.set()
        try:
            if not self.release_reads.wait(timeout=5):
                raise FirebasePublicationError("controlled reads were not released")
            resource = super().public_resource(target, path)
            if self.failure in {"bytes", "transport"} and path == "/000-historical.txt":
                self.failed.set()
                if self.failure == "transport":
                    raise FirebasePublicationError("controlled historical read failed")
                return replace(resource, body=b"wrong historical bytes")
            if self.failure == "root" and path == "/":
                return replace(resource, body=b"wrong homepage route")
            return resource
        finally:
            with self.lock:
                self.active -= 1


class FirebaseParallelVerificationTests(unittest.TestCase):
    def exercise(self, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            fx = fixture(Path(directory), extra_files={
                f"{index:03d}-historical.txt": f"historical-{index}".encode()
                for index in range(64)
            })
            artifact = FirebaseDeployArtifact.from_archive(fx.prepared.archive, fx.package)
            backend = ControlledReads(fx.package.expected_predecessor, failure=failure)
            adapter = FirebasePublicationAdapter(TARGET, backend)
            deployed = adapter.deploy(artifact, attempt_id="verification-fixture")
            writes = backend.write_count
            with ThreadPoolExecutor(max_workers=1) as driver:
                task = driver.submit(adapter.verify, artifact, deployed.identity,
                                     expected_managed=fx.baseline.managed_resources)
                try:
                    concurrent = backend.ready.wait(timeout=2)
                finally:
                    backend.release_reads.set()
                result = task.result(timeout=5)
            self.assertTrue(concurrent, "full-file reads were serialized")
            self.assertGreater(backend.peak, 1)
            self.assertLessEqual(backend.peak, 16)
            self.assertEqual(backend.active, 0, "verification returned with reads in flight")
            self.assertEqual(backend.write_count, writes, "verification must remain read-only")
            return result, backend, artifact

    def test_complete_inventory_reads_overlap_and_keep_root_and_managed_checks(self):
        result, backend, artifact = self.exercise()
        self.assertEqual(result.outcome, "verified", result.findings)
        expected = Counter({"/" + path: 1 for path in artifact.files})
        expected.update("/" if path == "index.html" else "/" + path for path in REQUIRED_PATHS)
        expected.update(("/__/firebase/init.js", "/__/firebase/init.json"))
        self.assertEqual(backend.seen, expected)
        self.assertEqual(result.inventory_sha256, artifact.package.inventory_sha256)

    def test_historical_corruption_transport_and_root_fail_without_success_receipt(self):
        for failure in ("bytes", "transport", "root"):
            with self.subTest(failure=failure):
                result, backend, artifact = self.exercise(failure)
                self.assertEqual(result.outcome, "failed", result.findings)
                self.assertEqual(result.public_findings, {})
                if failure != "root":
                    self.assertIsNone(result.inventory_sha256)
                    self.assertLess(len(backend.seen), len(artifact.files))
                    self.assertTrue(backend.failed.is_set())
                    self.assertNotIn("/__/firebase/init.js", backend.seen)
                    self.assertTrue(any("historical" in finding for finding in result.findings))
                else:
                    self.assertIn("/", backend.seen)
