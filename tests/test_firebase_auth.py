"""Credential expiry behavior at the concrete Firebase HTTP boundary, offline."""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import threading
import traceback
import unittest
from unittest.mock import patch

from google.auth.credentials import Credentials

from cfb.publication_cli import build_coordinator
from cfb.firebase import FirebasePublicationError


class RenewableCredentials(Credentials):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.token = "test-initial-token"
        self.expiry = clock[0] + timedelta(hours=1)
        self.refreshes = 0

    def refresh(self, request):
        self.refreshes += 1
        self.token = f"test-renewed-token-{self.refreshes}"
        self.expiry = self.clock[0] + timedelta(hours=1)


def channel_response(request, **kwargs):
    response = io.BytesIO(json.dumps({"release": {
        "name": "sites/sportsrank-837af/channels/live/releases/123",
        "version": {"name": "sites/sportsrank-837af/versions/abcdef"},
    }}).encode())
    response.headers = {}
    response.status = 200
    response.geturl = lambda: request.full_url
    return response


class FirebaseCredentialRenewalTests(unittest.TestCase):
    def test_both_backends_renew_after_long_verification_without_new_coordinator(self):
        clock = [datetime(2026, 10, 19, 12, 0)]
        credentials = RenewableCredentials(clock)
        with patch.dict(os.environ, {
            "FIREBASE_CREDENTIALS_FILE": "/gated/test-credentials.json",
            "FIREBASE_ACCESS_TOKEN": "test-initial-token",
        }), patch("google.auth.load_credentials_from_file", return_value=(credentials, None)), patch(
            "google.auth._helpers.utcnow", side_effect=lambda: clock[0]
        ), patch("cfb.firebase.urlopen", side_effect=channel_response) as http:
            coordinator = build_coordinator()
            coordinator.provider.observe()
            coordinator.provider_reader.observe()
            clock[0] += timedelta(minutes=68)
            coordinator.provider_reader.observe()
            clock[0] += timedelta(minutes=68)
            coordinator.provider.observe()
        self.assertEqual(
            [call.args[0].get_header("Authorization") for call in http.call_args_list],
            ["Bearer test-initial-token", "Bearer test-initial-token", "Bearer test-renewed-token-1", "Bearer test-renewed-token-2"],
        )
        self.assertEqual(credentials.refreshes, 2)

    def test_refresh_failure_prevents_provider_write_and_does_not_leak_or_fall_back(self):
        clock = [datetime(2026, 10, 19, 12, 0)]
        credentials = RenewableCredentials(clock)
        clock[0] += timedelta(minutes=68)
        secret_marker = "test-sensitive-credential-detail"
        with patch.dict(os.environ, {
            "FIREBASE_CREDENTIALS_FILE": "/gated/test.json",
            "FIREBASE_ACCESS_TOKEN": "test-stale-fallback",
        }), patch("google.auth.load_credentials_from_file", return_value=(credentials, None)), patch(
            "google.auth._helpers.utcnow", side_effect=lambda: clock[0]
        ), patch.object(credentials, "refresh", side_effect=RuntimeError(secret_marker)), patch(
            "cfb.firebase.urlopen"
        ) as http:
            coordinator = build_coordinator()
            try:
                coordinator.provider.backend.create_version(coordinator.provider.target, {}, {})
            except FirebasePublicationError as error:
                self.assertEqual(str(error), "Firebase credential refresh failed")
                self.assertNotIn(secret_marker, traceback.format_exc())
            else:
                self.fail("Expired credential must not fall back or reach a provider write")
            http.assert_not_called()

    def test_invalid_gated_file_fails_closed_without_loading_another_account(self):
        with patch.dict(os.environ, {
            "FIREBASE_CREDENTIALS_FILE": "/gated/test.json",
            "FIREBASE_ACCESS_TOKEN": "test-stale-fallback",
        }), patch("google.auth.load_credentials_from_file", side_effect=ValueError("test-private-file-details")) as load, patch(
            "cfb.firebase.urlopen"
        ) as http:
            with self.assertRaisesRegex(FirebasePublicationError, "^Gated Firebase credentials could not be loaded$"):
                build_coordinator().provider.observe()
            load.assert_called_once_with("/gated/test.json", scopes=("https://www.googleapis.com/auth/cloud-platform",))
            http.assert_not_called()

    def test_concurrent_provider_calls_share_one_blocking_refresh(self):
        clock = [datetime(2026, 10, 19, 12, 0)]
        credentials = RenewableCredentials(clock)
        clock[0] += timedelta(minutes=68)
        entered, duplicate, release = threading.Event(), threading.Event(), threading.Event()
        starts = threading.Barrier(9)
        count_lock = threading.Lock()
        refresh_calls = []
        original = credentials.refresh

        def refresh(request):
            with count_lock:
                refresh_calls.append(1)
                if len(refresh_calls) > 1:
                    duplicate.set()
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test refresh was not released")
            original(request)

        with patch.dict(os.environ, {"FIREBASE_CREDENTIALS_FILE": "/gated/test.json"}), patch(
            "google.auth.load_credentials_from_file", return_value=(credentials, None)
        ) as load, patch("google.auth._helpers.utcnow", side_effect=lambda: clock[0]), patch.object(
            credentials, "refresh", side_effect=refresh
        ), patch("cfb.firebase.urlopen", side_effect=channel_response) as http:
            coordinator = build_coordinator()

            def observe():
                starts.wait(3)
                return coordinator.provider.observe()

            with ThreadPoolExecutor(max_workers=8) as workers:
                futures = [workers.submit(observe) for _ in range(8)]
                try:
                    starts.wait(3)
                    self.assertTrue(entered.wait(3))
                    self.assertFalse(duplicate.wait(0.1), "Concurrent callers started duplicate refreshes")
                    http.assert_not_called()
                finally:
                    release.set()
                for future in futures:
                    future.result(timeout=3)
            self.assertEqual(len(refresh_calls), 1)
            self.assertEqual(http.call_count, 8)
            self.assertTrue(all(c.args[0].get_header("Authorization") == "Bearer test-renewed-token-1" for c in http.call_args_list))
            load.assert_called_once()

    def test_explicit_token_local_interface_remains_available_without_gated_file(self):
        with patch.dict(os.environ, {"FIREBASE_ACCESS_TOKEN": "test-local-token"}, clear=True), patch(
            "cfb.firebase.urlopen", side_effect=channel_response
        ) as http, patch("google.auth.load_credentials_from_file") as load:
            build_coordinator().provider.observe()
            self.assertEqual(http.call_args.args[0].get_header("Authorization"), "Bearer test-local-token")
            load.assert_not_called()
