"""Independent offline tests for the ADR-0021 source-qualification pilot.

The transport is always a fake in this file.  These checks exercise the
observable request plan, durable claim boundary, meter gate, and private raw
retention without interpreting provider score or clock semantics.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from cfb.excitement_source import (
    API_BASE_URL,
    DEFAULT_CONFIG,
    MAX_ATTEMPTS,
    PILOT_ID,
    PILOT_MANIFEST_SHA256,
    SOURCE_ARCHIVE_SHA256,
    HttpSupplementTransport,
    PilotAdapter,
    PilotAllowance,
    PilotAllowanceError,
    PilotClaimError,
    PilotManifest,
    PilotManifestError,
    PilotRetentionError,
    PilotSourceBindingError,
    PilotTransportError,
    SourceBinding,
)
from cfb.private_inputs import InputReference, LocalInputStore
from cfb.request_meter import RequestBudgets, RequestMeter


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


class _BindingStore(LocalInputStore):
    """Use real retention/restore while stubbing pre-existing source pins."""

    def verify(self, reference: InputReference) -> InputReference:
        return reference


class _NoopBinding(SourceBinding):
    """Skip unavailable pre-existing pins while testing response replay."""

    def verify(self, store: LocalInputStore, manifest: PilotManifest) -> None:
        return None


class _Response:
    def __init__(self, payload: bytes = b"response"):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return 200

    def read(self):
        return self.payload


class _Opener:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.calls = 0
        self.request = None

    def open(self, request, *, timeout):
        self.calls += 1
        self.request = request
        if self.error is not None:
            raise self.error
        return self.response


class SourcePilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest = PilotManifest.load(DEFAULT_CONFIG)
        self.assertEqual(self.manifest.manifest_sha256, PILOT_MANIFEST_SHA256)

    def tearDown(self):
        self.temporary.cleanup()

    def _allowance(self, *, suffix: str = "one", manifest_sha256: str | None = None) -> PilotAllowance:
        path = self.root / f"allowance-{suffix}.json"
        path.write_text(
            json.dumps(
                {
                    "allowance_id": f"owner-allowance-{suffix}",
                    "pilot_manifest_sha256": manifest_sha256 or self.manifest.manifest_sha256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return PilotAllowance.load(path)

    def _store(self) -> _BindingStore:
        store_root = self.root / "private-store"
        store_root.mkdir(mode=0o700, exist_ok=True)
        return _BindingStore(store_root)

    def _binding(self) -> SourceBinding:
        archive = InputReference("source-archive", SOURCE_ARCHIVE_SHA256, 0)
        snapshots = {
            path: InputReference(
                "parent-snapshot",
                request.parent_snapshot_sha256,
                0,
            )
            for path in {request.parent_snapshot_path for request in self.manifest.requests}
            for request in self.manifest.requests
            if request.parent_snapshot_path == path
        }
        return SourceBinding(archive, snapshots)

    def _meter(self, *, historical: int = MAX_ATTEMPTS, absolute: int = MAX_ATTEMPTS) -> RequestMeter:
        return RequestMeter(
            self.root / "meter.sqlite3",
            RequestBudgets(scheduled=0, historical=historical, absolute=absolute),
            clock=lambda: NOW,
        )

    def _adapter(self, transport, *, meter=None, store=None) -> PilotAdapter:
        return PilotAdapter(
            meter or self._meter(),
            store or self._store(),
            manifest=self.manifest,
            transport=transport,
            clock=lambda: NOW,
        )

    def test_manifest_matches_frozen_nine_request_allowlist(self):
        self.assertEqual(self.manifest.pilot_id, PILOT_ID)
        self.assertEqual(self.manifest.max_attempts, 9)
        self.assertEqual(self.manifest.attempts_per_request, 1)
        self.assertFalse(self.manifest.redirects)
        self.assertEqual(self.manifest.retries, 0)
        self.assertEqual(self.manifest.source_archive_sha256, SOURCE_ARCHIVE_SHA256)
        self.assertEqual(len(self.manifest.requests), 9)
        self.assertEqual(len({request.request_key for request in self.manifest.requests}), 9)
        self.assertEqual(
            [request.request_id for request in self.manifest.requests],
            [
                "2024-plays-401628439",
                "2024-games-401628439",
                "2024-lines-401628439",
                "2025-plays-401752677",
                "2025-games-401752677",
                "2025-lines-401752677",
                "2026-plays-401858432",
                "2026-games-401858432",
                "2026-lines-401858432",
            ],
        )
        by_year = {2024: "Georgia", 2025: "Ohio State", 2026: "Ohio State"}
        for request in self.manifest.requests:
            params = request.parameter_map
            self.assertIn(request.endpoint, {"/plays", "/games", "/lines"})
            self.assertEqual(params["seasonType"], "regular")
            self.assertEqual(params["year"], request.year)
            self.assertEqual(params.get("classification"), "fbs" if request.endpoint != "/lines" else None)
            if request.endpoint == "/plays":
                self.assertEqual(set(params), {"classification", "seasonType", "team", "week", "year"})
                self.assertEqual(params["team"], by_year[request.year])
                self.assertEqual(params["week"], 14 if request.year == 2024 else 1)
            elif request.endpoint == "/games":
                self.assertEqual(set(params), {"classification", "id", "seasonType", "year"})
                self.assertEqual(params["id"], int(request.expected_game_id))
            else:
                self.assertEqual(set(params), {"gameId", "seasonType", "year"})
                self.assertEqual(params["gameId"], int(request.expected_game_id))

    def test_manifest_digest_and_allowlist_mutations_fail_closed(self):
        raw = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        raw["requests"][0]["params"]["week"] = 13
        mutated = self.root / "mutated-manifest.json"
        mutated.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        with self.assertRaises(PilotManifestError):
            PilotManifest.load(mutated)

        raw = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        raw["requests"] = raw["requests"][:-1]
        mutated.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        with self.assertRaises(PilotManifestError):
            PilotManifest.load(mutated)

    def test_dry_run_is_credential_free_offline_and_nonmutating(self):
        class Meter:
            def execute(self, **kwargs):
                raise AssertionError("dry-run must not execute a request")

        transport = lambda request: (_ for _ in ()).throw(AssertionError("transport called"))
        adapter = PilotAdapter(Meter(), manifest=self.manifest, transport=transport)
        with patch("cfb.excitement_source.configured_api_key", side_effect=AssertionError("credential read")):
            plan = adapter.dry_run()
        self.assertEqual(plan["mode"], "dry-run")
        self.assertEqual(plan["network"], "disabled")
        self.assertEqual(plan["credentials"], "not read")
        self.assertEqual(plan["private_store"], "not opened or created")
        self.assertEqual(plan["remaining_attempts"], 9)

    def test_success_claims_each_key_before_http_and_retains_private_bytes(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        calls = []

        def transport(request):
            calls.append(request)
            with sqlite3.connect(store.root / f".{PILOT_ID}.claims.sqlite3") as connection:
                state = connection.execute(
                    "SELECT state FROM claims WHERE request_id = ?", (request.request_id,)
                ).fetchone()
            self.assertEqual(state[0], "claimed")
            return f"raw response {request.request_id}".encode()

        result = self._adapter(transport, meter=meter, store=store).acquire(allowance, binding)
        self.assertEqual(len(calls), 9)
        self.assertEqual(len(result.receipts), 9)
        self.assertEqual(result.remaining_attempts, 0)
        self.assertEqual([record["budget_impact"] for record in meter.audit_records()], [1] * 9)
        self.assertEqual({record["outcome"] for record in meter.audit_records()}, {"succeeded"})
        for receipt, request in zip(result.receipts, self.manifest.requests):
            self.assertEqual(receipt.request_id, request.request_id)
            self.assertEqual(receipt.endpoint, request.endpoint)
            self.assertEqual(receipt.params, request.params)
            self.assertEqual(receipt.pilot_id, PILOT_ID)
            self.assertEqual(receipt.manifest_sha256, self.manifest.manifest_sha256)
            self.assertEqual(receipt.source_archive_sha256, SOURCE_ARCHIVE_SHA256)
            self.assertNotIn(b"raw response", json.dumps(receipt.public_receipt()).encode())
            self.assertEqual(store.verify(receipt.response), receipt.response)
            destination_parent = self.root / "restored"
            destination_parent.mkdir(exist_ok=True)
            destination_parent.chmod(0o700)
            restored = store.restore(receipt.response, destination_parent / receipt.request_id)
            self.assertEqual(restored.read_bytes(), f"raw response {receipt.request_id}".encode())
            self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o600)

    def test_restart_does_not_repeat_successful_requests(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        first_calls = []
        first = self._adapter(lambda request: first_calls.append(request) or b"bytes", meter=meter, store=store)
        first.acquire(allowance, binding)
        audit_count = len(meter.audit_records())
        second_calls = []
        second = self._adapter(lambda request: second_calls.append(request) or b"unexpected", meter=meter, store=store)
        result = second.acquire(allowance, binding)
        self.assertEqual(len(first_calls), 9)
        self.assertEqual(second_calls, [])
        self.assertEqual(len(meter.audit_records()), audit_count)
        self.assertEqual(len(result.receipts), 9)

    def test_restart_verifies_retained_response_objects_before_reporting_success(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        first = self._adapter(lambda request: request.request_id.encode(), meter=meter, store=store)
        result = first.acquire(allowance, binding)
        reference = result.receipts[0].response
        object_path = store.root / "objects" / reference.sha256[:2] / reference.sha256
        object_path.unlink()
        replay_store = LocalInputStore(store.root)
        with self.assertRaises(PilotRetentionError):
            PilotAdapter(
                meter,
                replay_store,
                manifest=self.manifest,
                transport=lambda request: b"unexpected retry",
            ).acquire(allowance, _NoopBinding(binding.source_archive, binding.snapshots))

    def test_restart_rejects_in_place_retained_response_corruption(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        result = self._adapter(
            lambda request: request.request_id.encode(), meter=meter, store=store
        ).acquire(allowance, binding)
        reference = result.receipts[0].response
        object_path = store.root / "objects" / reference.sha256[:2] / reference.sha256
        object_path.write_bytes(b"tampered raw response")
        calls = []
        with self.assertRaises(PilotRetentionError):
            PilotAdapter(
                meter,
                LocalInputStore(store.root),
                manifest=self.manifest,
                transport=lambda request: calls.append(request) or b"unexpected retry",
            ).acquire(allowance, _NoopBinding(binding.source_archive, binding.snapshots))
        self.assertEqual(calls, [])

    def test_retention_failure_does_not_expose_response_bytes(self):
        class _FailingStore(_BindingStore):
            def retain(self, *args, **kwargs):
                raise RuntimeError("raw response bytes: synthetic-secret")

        store = _FailingStore(self.root / "private-store")
        meter = self._meter()
        with self.assertRaises(PilotRetentionError) as error:
            self._adapter(
                lambda request: b"raw response bytes: synthetic-secret",
                meter=meter,
                store=store,
            ).acquire(self._allowance(), self._binding())
        self.assertNotIn("synthetic-secret", str(error.exception))
        self.assertNotIn("synthetic-secret", json.dumps(meter.audit_records()))

    def test_forged_manifest_views_are_revalidated_before_acquisition(self):
        forged = replace(self.manifest, requests=self.manifest.requests[:1])
        snapshots = {
            self.manifest.requests[0].parent_snapshot_path: InputReference(
                "parent-snapshot", self.manifest.requests[0].parent_snapshot_sha256, 0
            )
        }
        binding = SourceBinding(InputReference("source-archive", SOURCE_ARCHIVE_SHA256, 0), snapshots)
        calls = []
        with self.assertRaises(PilotManifestError):
            PilotAdapter(
                self._meter(),
                self._store(),
                manifest=forged,
                transport=lambda request: calls.append(request) or b"unexpected",
            ).acquire(self._allowance(), binding)
        self.assertEqual(calls, [])

    def test_failed_request_is_consumed_and_restart_cannot_retry_it(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        calls = []

        def fail(request):
            calls.append(request)
            raise RuntimeError("Authorization: secret-token raw payload")

        with self.assertRaises(PilotTransportError) as first_error:
            self._adapter(fail, meter=meter, store=store).acquire(allowance, binding)
        self.assertNotIn("secret-token", str(first_error.exception))
        self.assertEqual(len(calls), 1)
        self.assertEqual(meter.audit_records()[0]["outcome"], "failed")

        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request) or b"retry",
                meter=meter,
                store=store,
            ).acquire(self._allowance(suffix="two"), binding)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret-token", json.dumps(meter.audit_records()))

    def test_interrupted_request_is_consumed_and_restart_cannot_retry_it(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        calls = []

        def interrupt(request):
            calls.append(request.request_id)
            raise KeyboardInterrupt("synthetic process interruption")

        with self.assertRaises(BaseException):
            self._adapter(interrupt, meter=meter, store=store).acquire(allowance, binding)
        self.assertEqual(calls, [self.manifest.requests[0].request_id])
        self.assertEqual(meter.audit_records()[0]["budget_impact"], 1)

        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request.request_id) or b"retry",
                meter=meter,
                store=store,
            ).acquire(self._allowance(suffix="after-interruption"), binding)
        self.assertEqual(calls, [self.manifest.requests[0].request_id])

    def test_meter_exhaustion_is_claimed_before_transport_and_cannot_restart(self):
        store = self._store()
        meter = self._meter(historical=0, absolute=0)
        allowance = self._allowance()
        binding = self._binding()
        calls = []
        with self.assertRaises(PilotClaimError):
            self._adapter(lambda request: calls.append(request) or b"blocked", meter=meter, store=store).acquire(allowance, binding)
        self.assertEqual(calls, [])
        self.assertEqual(meter.audit_records()[0]["outcome"], "blocked")
        with self.assertRaises(PilotClaimError):
            self._adapter(lambda request: calls.append(request) or b"retry", meter=meter, store=store).acquire(allowance, binding)
        self.assertEqual(calls, [])

    def test_claim_ledger_symlink_is_rejected_before_transport(self):
        store = self._store()
        ledger = store.root / f".{PILOT_ID}.claims.sqlite3"
        target = self.root / "ledger-target"
        target.write_bytes(b"not a ledger")
        ledger.symlink_to(target)
        calls = []
        with self.assertRaisesRegex(PilotClaimError, "pilot claim ledger"):
            self._adapter(
                lambda request: calls.append(request) or b"unexpected",
                meter=self._meter(),
                store=store,
            ).acquire(self._allowance(), self._binding())
        self.assertEqual(calls, [])

    def test_claim_ledger_permissive_file_is_rejected_before_transport(self):
        store = self._store()
        ledger = store.root / f".{PILOT_ID}.claims.sqlite3"
        ledger.write_bytes(b"not a ledger")
        ledger.chmod(0o644)
        calls = []
        with self.assertRaisesRegex(PilotClaimError, "pilot claim ledger"):
            self._adapter(
                lambda request: calls.append(request) or b"unexpected",
                meter=self._meter(),
                store=store,
            ).acquire(self._allowance(), self._binding())
        self.assertEqual(calls, [])

    def test_concurrent_adapters_cannot_overspend_or_duplicate_transport(self):
        store = self._store()
        meter = self._meter()
        allowance = self._allowance()
        binding = self._binding()
        calls = []

        def transport(request):
            calls.append(request.request_id)
            time.sleep(0.003)
            return request.request_id.encode()

        def run():
            return self._adapter(transport, meter=meter, store=store).acquire(allowance, binding)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [pool.submit(run), pool.submit(run)]
            results = []
            errors = []
            for future in outcomes:
                try:
                    results.append(future.result())
                except Exception as error:  # assertions below classify the failure
                    errors.append(error)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PilotClaimError)
        self.assertEqual(len(calls), 9)
        self.assertEqual(len(set(calls)), 9)
        self.assertLessEqual(sum(record["budget_impact"] for record in meter.audit_records()), 9)

    def test_allowance_and_source_binding_identity_or_schema_mismatches_fail_closed(self):
        with self.assertRaises(PilotAllowanceError):
            self._allowance(suffix="wrong", manifest_sha256="0" * 64)

        path = self.root / "bad-allowance.json"
        path.write_text(
            json.dumps(
                {
                    "allowance_id": "owner-allowance-extra",
                    "pilot_manifest_sha256": self.manifest.manifest_sha256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                    "unexpected": "field",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PilotAllowanceError):
            PilotAllowance.load(path)

        binding = self._binding()
        wrong_snapshots = dict(binding.snapshots)
        first = next(iter(wrong_snapshots))
        wrong_snapshots[first] = InputReference("parent-snapshot", "0" * 64, 0)
        with self.assertRaises(PilotSourceBindingError):
            SourceBinding(binding.source_archive, wrong_snapshots).verify(self._store(), self.manifest)

        wrong_archive = InputReference("source-archive", "0" * 64, 0)
        with self.assertRaises(PilotSourceBindingError):
            SourceBinding(wrong_archive, binding.snapshots).verify(self._store(), self.manifest)

        missing_snapshots = dict(binding.snapshots)
        missing_snapshots.pop(next(iter(missing_snapshots)))
        with self.assertRaises(PilotSourceBindingError):
            SourceBinding(binding.source_archive, missing_snapshots).verify(
                self._store(), self.manifest
            )

    def test_missing_allowance_document_fails_closed(self):
        with self.assertRaises(PilotAllowanceError):
            PilotAllowance.load(self.root / "does-not-exist.json")
        with self.assertRaises(PilotSourceBindingError):
            SourceBinding.load(self.root / "does-not-exist-binding.json")

    def test_source_binding_receipt_is_path_free_and_parent_bytes_are_unchanged(self):
        parent = self.root / "snapshots" / "parent.json"
        parent.parent.mkdir(mode=0o700)
        parent.write_bytes(b"original retained parent bytes")
        before = parent.read_bytes()
        receipt = self._binding().safe_dict()
        self.assertNotIn(str(self.root), json.dumps(receipt))
        self.assertNotIn("original retained parent bytes", json.dumps(receipt))
        self.assertEqual(parent.read_bytes(), before)

    def test_http_transport_is_single_attempt_and_rejects_redirects(self):
        request = self.manifest.requests[0]
        opener = _Opener(_Response(b"ok"))
        with patch("cfb.excitement_source.configured_api_key", return_value="synthetic-token"), patch(
            "cfb.excitement_source.build_opener", return_value=opener
        ) as build:
            payload = HttpSupplementTransport(base_url=API_BASE_URL)(request)
        self.assertEqual(payload, b"ok")
        self.assertEqual(opener.calls, 1)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(build.call_args.args[0].__class__.__name__, "_NoRedirectHandler")

        redirect = _Opener(error=HTTPError("https://example.invalid", 302, "redirect", {}, None))
        with patch("cfb.excitement_source.configured_api_key", return_value="synthetic-token"), patch(
            "cfb.excitement_source.build_opener", return_value=redirect
        ):
            with self.assertRaises(PilotTransportError):
                HttpSupplementTransport(base_url=API_BASE_URL)(request)
        self.assertEqual(redirect.calls, 1)

    def test_http_transport_builds_only_the_allowlisted_request(self):
        request = self.manifest.requests[0]
        opener = _Opener(_Response(b"ok"))
        with patch(
            "cfb.excitement_source.configured_api_key", return_value="synthetic-token"
        ), patch("cfb.excitement_source.build_opener", return_value=opener):
            self.assertEqual(HttpSupplementTransport()(request), b"ok")
        outbound = opener.request
        self.assertEqual(
            outbound.full_url,
            (
                f"{API_BASE_URL}/plays?classification=fbs&seasonType=regular&"
                "team=Georgia&week=14&year=2024"
            ),
        )
        self.assertEqual(outbound.get_method(), "GET")
        self.assertEqual(outbound.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(outbound.get_header("Accept"), "application/json")

    def test_http_transport_does_not_retry_network_failures(self):
        request = self.manifest.requests[0]
        opener = _Opener(error=URLError("synthetic network failure"))
        with patch("cfb.excitement_source.configured_api_key", return_value="synthetic-token"), patch(
            "cfb.excitement_source.build_opener", return_value=opener
        ):
            with self.assertRaises(PilotTransportError) as error:
                HttpSupplementTransport(base_url=API_BASE_URL)(request)
        self.assertEqual(opener.calls, 1)
        self.assertNotIn("synthetic network failure", str(error.exception))

    def test_alternate_host_is_rejected_before_credential_lookup(self):
        request = self.manifest.requests[0]
        with patch("cfb.excitement_source.configured_api_key", side_effect=AssertionError("credential read")):
            with self.assertRaises(PilotTransportError):
                HttpSupplementTransport(base_url="https://alternate.example")(request)


if __name__ == "__main__":
    unittest.main()
