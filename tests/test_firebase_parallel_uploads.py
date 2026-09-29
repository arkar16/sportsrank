from __future__ import annotations

from collections import Counter
import threading
import time
import unittest
from unittest.mock import patch

import cfb.firebase as firebase_module
from cfb.firebase import (
    FirebaseDeployArtifact,
    FirebasePublicationAdapter,
    ProviderWriteUncertain,
)
from cfb.publication_records import ProviderIdentity, ProviderTarget


TARGET = ProviderTarget("fixture-project", "fixture-site", "live")
VERSION = "sites/fixture-site/versions/parallel-version"
UPLOAD_URL = (
    "https://upload-firebasehosting.googleapis.com/upload/"
    "sites/fixture-site/versions/parallel-version/files"
)


def _digest(index: int) -> str:
    return f"{index:064x}"


def _artifact(count: int) -> FirebaseDeployArtifact:
    payloads = {_digest(index): f"payload-{index}".encode() for index in range(count)}
    hashes = {f"/file-{index}.html": digest for index, digest in enumerate(payloads)}
    files = {path.removeprefix("/"): payloads[digest] for path, digest in hashes.items()}
    return FirebaseDeployArtifact(object(), files, b"{}", {}, hashes, payloads)  # type: ignore[arg-type]


class RecordingParallelBackend:
    def __init__(self, required: set[str], *, failure: BaseException | None = None):
        self.required = required
        self.failure = failure
        self.fail_digest = min(required) if failure is not None else None
        self.lock = threading.Lock()
        self.concurrent = threading.Event()
        self.release_running = threading.Event()
        self.attempts: Counter[str] = Counter()
        self.completed: set[str] = set()
        self.active = 0
        self.max_active = 0
        self.finalize_calls = 0
        self.release_calls = 0
        self.identity = ProviderIdentity(
            TARGET,
            "sites/fixture-site/channels/live/releases/parallel-release",
            VERSION,
        )

    def observe(self, target):
        return self.identity

    def create_version(self, target, config, labels):
        return {"name": VERSION, "status": "CREATED", "config": dict(config)}

    def populate_files(self, version, files):
        return {
            "uploadRequiredHashes": sorted(self.required),
            "uploadUrl": UPLOAD_URL,
        }

    def upload_file(self, upload_url, digest, payload):
        with self.lock:
            self.attempts[digest] += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self.concurrent.set()
        try:
            self.concurrent.wait(timeout=2)
            if digest == self.fail_digest:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with self.lock:
                        if self.active >= 2:
                            break
                    time.sleep(0.001)
                self.release_running.set()
                raise self.failure  # type: ignore[misc]
            if self.failure is not None:
                self.release_running.wait(timeout=2)
            time.sleep(0.005)
            with self.lock:
                self.completed.add(digest)
        finally:
            with self.lock:
                self.active -= 1

    def inventory(self, version):
        return tuple(
            {"path": path, "hash": digest, "status": "ACTIVE"}
            for path, digest in sorted(_artifact(len(self.required)).provider_hashes.items())
        )

    def finalize_version(self, version):
        with self.lock:
            if self.active or self.completed != self.required:
                raise AssertionError("finalization ran before every upload settled")
        self.finalize_calls += 1
        return {
            "name": VERSION,
            "status": "FINALIZED",
            "config": {},
            "fileCount": str(len(self.required)),
        }

    def release_version(self, target, version):
        self.release_calls += 1
        return {
            "name": self.identity.release,
            "type": "DEPLOY",
            "releaseTime": "2026-09-28T00:00:00Z",
            "version": {"name": VERSION, "status": "FINALIZED"},
        }


class FirebaseParallelUploadTests(unittest.TestCase):
    def test_uploads_are_bounded_once_each_and_finish_before_finalization(self):
        self.assertEqual(firebase_module._MAX_PARALLEL_UPLOADS, 64)
        artifact = _artifact(40)
        backend = RecordingParallelBackend(set(artifact.provider_payloads))

        with patch("cfb.firebase._MAX_PARALLEL_UPLOADS", 4):
            FirebasePublicationAdapter(TARGET, backend).deploy(
                artifact, attempt_id="parallel-success"
            )

        self.assertGreater(backend.max_active, 1)
        self.assertLessEqual(backend.max_active, 4)
        self.assertEqual(backend.attempts, Counter({digest: 1 for digest in backend.required}))
        self.assertEqual(backend.completed, backend.required)
        self.assertEqual(backend.finalize_calls, 1)
        self.assertEqual(backend.release_calls, 1)

    def test_failure_cancels_pending_work_settles_running_and_never_finalizes(self):
        artifact = _artifact(40)
        failure = ProviderWriteUncertain("original upload failure")
        backend = RecordingParallelBackend(
            set(artifact.provider_payloads), failure=failure
        )

        with patch("cfb.firebase._MAX_PARALLEL_UPLOADS", 4):
            with self.assertRaises(ProviderWriteUncertain) as raised:
                FirebasePublicationAdapter(TARGET, backend).deploy(
                    artifact, attempt_id="parallel-failure"
                )

        self.assertIs(raised.exception, failure)
        self.assertEqual(backend.active, 0)
        self.assertGreater(len(backend.attempts), 1)
        self.assertLessEqual(len(backend.attempts), 4)
        self.assertTrue(all(count == 1 for count in backend.attempts.values()))
        self.assertEqual(backend.finalize_calls, 0)
        self.assertEqual(backend.release_calls, 0)


if __name__ == "__main__":
    unittest.main()
