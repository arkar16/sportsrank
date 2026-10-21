from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest

from cfb.firebase import (
    FirebaseDeployArtifact,
    FirebasePublicationError,
    FirebasePublicationAdapter,
    FakeFirebasePublicationBackend,
    PublicResource,
    _deterministic_gzip,
    _managed_app_identity,
)
from cfb.publication_records import (
    ManagedResourceEvidence,
    ProviderIdentity,
    ProviderTarget,
)
from tools.scripts.postdeploy_smoke import REQUIRED_PATHS


TARGET = ProviderTarget("fixture-project", "fixture-site", "live")
APP_CONFIG = {
    "apiKey": "<REDACTED>",
    "authDomain": "fixture-project.firebaseapp.com",
    "databaseURL": "<REDACTED>",
    "messagingSenderId": "1234",
    "projectId": "fixture-project",
    "storageBucket": "fixture-project.firebasestorage.app",
}
APP_IDENTITY = {
    "project_id": APP_CONFIG["projectId"],
    "messaging_sender_id": APP_CONFIG["messagingSenderId"],
    "auth_domain": APP_CONFIG["authDomain"],
    "storage_bucket": APP_CONFIG["storageBucket"],
}
NATIVE_INIT_JS = (
    "if (typeof firebase === 'undefined') throw new Error("
    "'hosting/init-error: Firebase SDK not detected. You must include it before /__/firebase/init.js');\n"
    "firebase.initializeApp({\n"
    '  "apiKey": "<REDACTED>",\n'
    '  "authDomain": "fixture-project.firebaseapp.com",\n'
    '  "databaseURL": "<REDACTED>",\n'
    '  "messagingSenderId": "1234",\n'
    '  "projectId": "fixture-project",\n'
    '  "storageBucket": "fixture-project.firebasestorage.app"\n'
    "});\n"
).encode()
PLAIN_INIT_JS = (
    "firebase.initializeApp(" + json.dumps(APP_CONFIG, sort_keys=True) + ");\n"
).encode()
INIT_JSON = json.dumps(APP_CONFIG, sort_keys=True).encode()
SDK_GUARD_ONLY = NATIVE_INIT_JS.split(b"firebase.initializeApp", 1)[0]
REPEATED_INIT_JS = (
    NATIVE_INIT_JS.rstrip() + b"\nfirebase.initializeApp({});\n"
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _artifact() -> FirebaseDeployArtifact:
    files = {
        relative: f"fixture:{relative}".encode()
        for relative in REQUIRED_PATHS
    }
    payloads = {
        relative: _deterministic_gzip(body)
        for relative, body in files.items()
    }
    provider_hashes = {
        f"/{relative}": _digest(payload)
        for relative, payload in payloads.items()
    }
    package = SimpleNamespace(
        inventory_sha256="a" * 64,
        configuration_sha256="b" * 64,
    )
    return FirebaseDeployArtifact(
        package,
        files,
        b'{"hosting":{"public":"website"}}',
        {},
        provider_hashes,
        {_digest(payload): payload for payload in payloads.values()},
    )


class ManagedResourceBackend(FakeFirebasePublicationBackend):
    def __init__(
        self,
        *args,
        javascript: bytes = NATIVE_INIT_JS,
        json_body: bytes = INIT_JSON,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.javascript = javascript
        self.json_body = json_body

    def public_resource(self, target: ProviderTarget, path: str) -> PublicResource:
        resource = super().public_resource(target, path)
        if path == "/__/firebase/init.js":
            return replace(resource, body=self.javascript)
        if path == "/__/firebase/init.json":
            return replace(resource, body=self.json_body)
        return resource


def _verification_fixture(
    *,
    javascript: bytes = NATIVE_INIT_JS,
    json_body: bytes = INIT_JSON,
    verification_failure: str | None = None,
) -> tuple[FirebaseDeployArtifact, ManagedResourceBackend, ProviderIdentity]:
    artifact = _artifact()
    predecessor = ProviderIdentity(
        TARGET,
        "sites/fixture-site/channels/live/releases/fixture-release",
        "sites/fixture-site/versions/fixture-version",
    )
    backend = ManagedResourceBackend(
        TARGET,
        predecessor,
        initial_files=artifact.files,
        managed_identity=APP_IDENTITY,
        verification_failure=verification_failure,
        javascript=javascript,
        json_body=json_body,
    )
    backend._expected.update(artifact.provider_hashes)
    backend._created_status = "FINALIZED"
    backend._released_files = dict(artifact.files)
    return artifact, backend, predecessor


def _managed_expectation() -> tuple[ManagedResourceEvidence, ...]:
    return tuple(
        ManagedResourceEvidence(
            path,
            _digest(body),
            _digest(_deterministic_gzip(body)),
            len(body),
            APP_IDENTITY,
        )
        for path, body in (
            ("/__/firebase/init.js", NATIVE_INIT_JS),
            ("/__/firebase/init.json", INIT_JSON),
        )
    )


class ManagedInitParserTests(unittest.TestCase):
    def test_native_sdk_guard_is_accepted_and_identity_is_extracted(self):
        self.assertEqual(
            dict(_managed_app_identity("/__/firebase/init.js", NATIVE_INIT_JS)),
            APP_IDENTITY,
        )

    def test_plain_call_and_json_remain_supported(self):
        self.assertEqual(
            dict(_managed_app_identity("/__/firebase/init.js", PLAIN_INIT_JS)),
            APP_IDENTITY,
        )
        self.assertEqual(
            dict(_managed_app_identity("/__/firebase/init.json", INIT_JSON)),
            APP_IDENTITY,
        )

    def test_parser_rejects_surrounding_code_wrong_resource_and_invalid_calls(self):
        invalid = (
            ("/__/firebase/init.js", b"// preamble\n" + PLAIN_INIT_JS),
            ("/__/firebase/init.js", NATIVE_INIT_JS + b"alert('trailing');\n"),
            ("/__/firebase/init.js", SDK_GUARD_ONLY),
            ("/__/firebase/init.js", REPEATED_INIT_JS),
            ("/__/firebase/init.js", b"firebase.initializeApp();\n"),
            ("/__/firebase/other.js", PLAIN_INIT_JS),
        )
        for path, body in invalid:
            with self.subTest(path=path, body=body):
                with self.assertRaisesRegex(
                    FirebasePublicationError,
                    "managed Firebase configuration is invalid",
                ):
                    _managed_app_identity(path, body)


class ManagedInitVerificationTests(unittest.TestCase):
    def test_verify_accepts_native_guard_without_provider_writes(self):
        artifact, backend, identity = _verification_fixture()
        observation = FirebasePublicationAdapter(TARGET, backend).verify(
            artifact,
            identity,
            expected_managed=_managed_expectation(),
        )

        self.assertEqual(observation.outcome, "verified")
        self.assertIn(
            "managed resources",
            observation.findings[0],
        )
        self.assertEqual(backend.write_count, 0)

    def test_verify_rejects_mismatched_js_and_json_identity_without_writes(self):
        mismatched = dict(APP_CONFIG)
        mismatched["authDomain"] = "other-project.firebaseapp.com"
        artifact, backend, identity = _verification_fixture(
            json_body=json.dumps(mismatched, sort_keys=True).encode(),
        )

        observation = FirebasePublicationAdapter(TARGET, backend).verify(
            artifact,
            identity,
            expected_managed=_managed_expectation(),
        )

        self.assertEqual(observation.outcome, "failed")
        self.assertTrue(
            any(
                "managed resource app identity differs" in finding
                for finding in observation.findings
            )
        )
        self.assertEqual(backend.write_count, 0)

    def test_verify_rejects_missing_managed_resource_without_writes(self):
        artifact, backend, identity = _verification_fixture(
            verification_failure="managed-missing",
        )

        observation = FirebasePublicationAdapter(TARGET, backend).verify(
            artifact,
            identity,
            expected_managed=_managed_expectation(),
        )

        self.assertEqual(observation.outcome, "failed")
        self.assertIn("fake managed resource is missing", observation.findings)
        self.assertEqual(backend.write_count, 0)


if __name__ == "__main__":
    unittest.main()
