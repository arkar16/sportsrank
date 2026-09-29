from __future__ import annotations

from collections import Counter
import threading
import time
import unittest
from unittest.mock import patch

import cfb.firebase as firebase_module
from cfb.firebase import (
    FirebaseDeployArtifact,
    FirebaseFinalizeReceiptUncertain,
    FirebaseFinalizeWriteUncertain,
    FirebaseFinalizedVersionReadUncertain,
    FirebaseFinalizedVersionReceiptUncertain,
    FirebaseFinalizedVersionStatisticsUncertain,
    FirebaseLiveObservationUncertain,
    FirebaseLiveReceiptUncertain,
    FirebasePublicationError,
    FirebasePublicationAdapter,
    FirebaseReleaseReceiptUncertain,
    FirebaseReleaseWriteUncertain,
    ProviderWriteUncertain,
    _provider_inventory,
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
        self.version_calls = 0
        self.release_calls = 0
        self.events: list[str] = []
        self.identity = ProviderIdentity(
            TARGET,
            "sites/fixture-site/channels/live/releases/parallel-release",
            VERSION,
        )

    def observe(self, target):
        self.events.append("observe")
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
        application = tuple(
            {"path": path, "hash": digest, "status": "ACTIVE"}
            for path, digest in sorted(_artifact(len(self.required)).provider_hashes.items())
        )
        managed = tuple(
            {"path": path, "hash": "f" * 64, "status": "ACTIVE"}
            for path in ("/__/firebase/init.js", "/__/firebase/init.json")
        )
        return application + managed

    def finalize_version(self, version):
        with self.lock:
            if self.active or self.completed != self.required:
                raise AssertionError("finalization ran before every upload settled")
        self.finalize_calls += 1
        self.events.append("finalize")
        return {
            "name": VERSION,
            "status": "FINALIZED",
        }

    def version(self, version):
        self.version_calls += 1
        self.events.append("version")
        return {
            "name": VERSION,
            "status": "FINALIZED",
            "config": {},
            "fileCount": str(len(self.required) + 2),
        }

    def release_version(self, target, version):
        self.release_calls += 1
        self.events.append("release")
        return {
            "name": self.identity.release,
            "type": "DEPLOY",
            "releaseTime": "2026-09-28T00:00:00Z",
            "version": {"name": VERSION, "status": "FINALIZED"},
        }


class FirebaseParallelUploadTests(unittest.TestCase):
    def test_inventory_separates_exact_managed_resources_from_application(self):
        application = {
            "path": "/index.html", "hash": "a" * 64, "status": "ACTIVE"
        }
        managed = [
            {"path": path, "hash": digest * 64, "status": "ACTIVE"}
            for path, digest in (
                ("/__/firebase/init.js", "b"),
                ("/__/firebase/init.json", "c"),
            )
        ]
        self.assertEqual(
            dict(_provider_inventory([application, *managed])),
            {"/index.html": "a" * 64},
        )

        invalid = (
            [application, managed[0]],
            [application, *managed, managed[0]],
            [application, *managed, {
                "path": "/__/firebase/other", "hash": "d" * 64,
                "status": "ACTIVE",
            }],
            [application, *managed, {
                "path": "/other.html", "hash": "not-a-hash", "status": "ACTIVE",
            }],
            [application, {**managed[0], "status": "EXPECTED"}, managed[1]],
            [application, {**managed[0], "hash": "not-a-hash"}, managed[1]],
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(FirebasePublicationError):
                _provider_inventory(values)

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
        self.assertEqual(backend.version_calls, 1)
        self.assertEqual(backend.release_calls, 1)
        self.assertEqual(
            backend.events[-4:], ["finalize", "version", "release", "observe"]
        )

    def test_fresh_finalized_version_read_must_be_available_and_exact(self):
        artifact = _artifact(4)

        class UnavailableVersionBackend(RecordingParallelBackend):
            def version(self, version):
                self.version_calls += 1
                self.events.append("version")
                raise FirebasePublicationError("raw provider detail must stay private")

        unavailable = UnavailableVersionBackend(set(artifact.provider_payloads))
        with self.assertRaises(FirebaseFinalizedVersionReadUncertain) as raised:
            FirebasePublicationAdapter(TARGET, unavailable).deploy(
                artifact, attempt_id="unavailable-finalized-version"
            )
        self.assertNotIn("raw provider detail", str(raised.exception))
        self.assertEqual(unavailable.release_calls, 0)

        invalid_receipts = (
            {"name": VERSION, "status": "FINALIZED", "fileCount": "6"},
            {"name": VERSION + "-wrong", "status": "FINALIZED", "config": {}, "fileCount": "6"},
            {"name": VERSION, "status": "CREATED", "config": {}, "fileCount": "6"},
            {"name": VERSION, "status": "FINALIZED", "config": {"cleanUrls": True}, "fileCount": "6"},
            {"name": VERSION, "status": "FINALIZED", "config": {}, "fileCount": "5"},
        )
        for index, receipt in enumerate(invalid_receipts):
            class InvalidVersionBackend(RecordingParallelBackend):
                def version(self, version):
                    self.version_calls += 1
                    self.events.append("version")
                    return receipt

            backend = InvalidVersionBackend(set(artifact.provider_payloads))
            with self.subTest(index=index), self.assertRaises(
                FirebaseFinalizedVersionReceiptUncertain
            ):
                FirebasePublicationAdapter(TARGET, backend).deploy(
                    artifact, attempt_id=f"invalid-finalized-version-{index}"
                )
            self.assertEqual(backend.release_calls, 0)

    def test_finalized_version_waits_for_delayed_file_count(self):
        artifact = _artifact(4)

        class DelayedFileCountBackend(RecordingParallelBackend):
            def version(self, version):
                receipt = dict(super().version(version))
                if self.version_calls <= 2:
                    receipt.pop("fileCount")
                return receipt

        backend = DelayedFileCountBackend(set(artifact.provider_payloads))
        with patch("cfb.firebase.time.sleep") as sleep:
            FirebasePublicationAdapter(TARGET, backend).deploy(
                artifact, attempt_id="delayed-file-count"
            )

        self.assertEqual(backend.version_calls, 3)
        self.assertEqual(
            sleep.call_args_list[-2:],
            [((2,), {}), ((4,), {})],
        )
        self.assertEqual(backend.release_calls, 1)

    def test_missing_file_count_exhausts_bounded_wait_without_release(self):
        artifact = _artifact(4)

        class MissingFileCountBackend(RecordingParallelBackend):
            def version(self, version):
                receipt = dict(super().version(version))
                receipt.pop("fileCount")
                return receipt

        backend = MissingFileCountBackend(set(artifact.provider_payloads))
        with patch("cfb.firebase.time.sleep") as sleep:
            with self.assertRaises(FirebaseFinalizedVersionStatisticsUncertain) as raised:
                FirebasePublicationAdapter(TARGET, backend).deploy(
                    artifact, attempt_id="missing-file-count"
                )

        self.assertNotIn("raw provider", str(raised.exception))
        self.assertEqual(backend.version_calls, 6)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list[-5:]],
            [2, 4, 8, 16, 30],
        )
        self.assertEqual(backend.release_calls, 0)

    def test_post_upload_failure_stages_are_fixed_and_sanitized(self):
        artifact = _artifact(4)

        cases = (
            ("finalize_write", FirebaseFinalizeWriteUncertain),
            ("finalize_receipt", FirebaseFinalizeReceiptUncertain),
            ("release_write", FirebaseReleaseWriteUncertain),
            ("release_receipt", FirebaseReleaseReceiptUncertain),
            ("live_observation", FirebaseLiveObservationUncertain),
            ("live_receipt", FirebaseLiveReceiptUncertain),
        )
        for stage, expected in cases:
            class StageFailureBackend(RecordingParallelBackend):
                def finalize_version(self, version):
                    result = super().finalize_version(version)
                    if stage == "finalize_write":
                        raise ProviderWriteUncertain("sensitive finalize response")
                    if stage == "finalize_receipt":
                        return {"name": VERSION + "-wrong", "status": "FINALIZED"}
                    return result

                def release_version(self, target, version):
                    result = super().release_version(target, version)
                    if stage == "release_write":
                        raise ProviderWriteUncertain("sensitive release response")
                    if stage == "release_receipt":
                        return {**result, "type": "ROLLBACK"}
                    return result

                def observe(self, target):
                    if stage == "live_observation":
                        raise FirebasePublicationError("sensitive account metadata")
                    if stage == "live_receipt":
                        return ProviderIdentity(
                            TARGET,
                            "sites/fixture-site/channels/live/releases/other-release",
                            VERSION,
                        )
                    return super().observe(target)

            backend = StageFailureBackend(set(artifact.provider_payloads))
            with self.subTest(stage=stage), self.assertRaises(expected) as raised:
                FirebasePublicationAdapter(TARGET, backend).deploy(
                    artifact, attempt_id=f"stage-{stage}"
                )
            self.assertEqual(type(raised.exception).__name__, expected.__name__)
            self.assertNotIn("sensitive", str(raised.exception))

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
