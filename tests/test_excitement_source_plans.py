"""Independent tests for the registered season-metadata source plan."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest

from cfb.excitement_source import (
    DEFAULT_CONFIG,
    MAX_ATTEMPTS,
    PILOT_ID,
    PILOT_MANIFEST_SHA256,
    SEASON_METADATA_CONFIG,
    SEASON_METADATA_MANIFEST_SHA256,
    SEASON_METADATA_MAX_ATTEMPTS,
    SEASON_METADATA_PILOT_ID,
    SOURCE_ARCHIVE_SHA256,
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
    _ClaimLedger,
)
from cfb.private_inputs import InputReference, LocalInputStore
from cfb.request_meter import RequestBudgets, RequestMeter


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]


class _BindingStore(LocalInputStore):
    """Retain real response objects while stubbing trusted source pins."""

    def verify(self, reference: InputReference) -> InputReference:
        return reference


class _NoopBinding(SourceBinding):
    def verify(self, store: LocalInputStore, manifest: PilotManifest) -> None:
        return None


class SeasonMetadataPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest = PilotManifest.load(REPO_ROOT / SEASON_METADATA_CONFIG)
        self.old_manifest = PilotManifest.load(REPO_ROOT / DEFAULT_CONFIG)

    def tearDown(self):
        self.temporary.cleanup()

    def _store(self, suffix: str = "store") -> _BindingStore:
        root = self.root / suffix
        root.mkdir(mode=0o700, exist_ok=True)
        return _BindingStore(root)

    def _meter(
        self,
        suffix: str = "meter",
        *,
        historical: int = SEASON_METADATA_MAX_ATTEMPTS,
        absolute: int = SEASON_METADATA_MAX_ATTEMPTS,
    ) -> RequestMeter:
        return RequestMeter(
            self.root / f"{suffix}.sqlite3",
            RequestBudgets(scheduled=0, historical=historical, absolute=absolute),
            clock=lambda: NOW,
        )

    def _binding(self, manifest: PilotManifest | None = None) -> SourceBinding:
        manifest = manifest or self.manifest
        snapshots = {}
        for request in manifest.requests:
            snapshots[request.parent_snapshot_path] = InputReference(
                "parent-snapshot", request.parent_snapshot_sha256, 0
            )
        return SourceBinding(
            InputReference("source-archive", SOURCE_ARCHIVE_SHA256, 0), snapshots
        )

    def _replay_binding(self, manifest: PilotManifest | None = None) -> _NoopBinding:
        binding = self._binding(manifest)
        return _NoopBinding(binding.source_archive, binding.snapshots)

    def _allowance(
        self,
        suffix: str,
        *,
        manifest_sha256: str = SEASON_METADATA_MANIFEST_SHA256,
        max_attempts: int = SEASON_METADATA_MAX_ATTEMPTS,
        allowance_id: str | None = None,
    ) -> PilotAllowance:
        path = self.root / f"allowance-{suffix}.json"
        path.write_text(
            json.dumps(
                {
                    "allowance_id": allowance_id or f"season-owner-{suffix}",
                    "pilot_manifest_sha256": manifest_sha256,
                    "max_attempts": max_attempts,
                    "purpose": "historical",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return PilotAllowance.load(path)

    def _adapter(
        self,
        transport,
        *,
        manifest: PilotManifest | None = None,
        meter: RequestMeter | None = None,
        store: LocalInputStore | None = None,
    ) -> PilotAdapter:
        return PilotAdapter(
            meter or self._meter(),
            store or self._store(),
            manifest=manifest or self.manifest,
            transport=transport,
            clock=lambda: NOW,
        )

    def _run_complete(
        self,
        suffix: str,
        *,
        meter: RequestMeter | None = None,
        store: _BindingStore | None = None,
        allowance: PilotAllowance | None = None,
        response=None,
    ):
        store = store or self._store(f"{suffix}-store")
        meter = meter or self._meter(
            f"{suffix}-meter",
            historical=SEASON_METADATA_MAX_ATTEMPTS * 2,
            absolute=SEASON_METADATA_MAX_ATTEMPTS * 2,
        )
        allowance = allowance or self._allowance(suffix)
        calls = []
        transport = response or (
            lambda request: calls.append(request.request_id)
            or f"season metadata {request.request_id}".encode()
        )
        result = self._adapter(transport, meter=meter, store=store).acquire(
            allowance, self._binding()
        )
        self.assertEqual(len(result.receipts), SEASON_METADATA_MAX_ATTEMPTS)
        return result, calls, store, meter, allowance

    def test_config_hash_and_exact_five_request_plan_are_frozen(self):
        config_path = REPO_ROOT / SEASON_METADATA_CONFIG
        raw_bytes = config_path.read_bytes()
        raw = json.loads(raw_bytes)
        self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), SEASON_METADATA_MANIFEST_SHA256)
        self.assertEqual(self.manifest.manifest_sha256, SEASON_METADATA_MANIFEST_SHA256)
        self.assertEqual(self.manifest.pilot_id, SEASON_METADATA_PILOT_ID)
        self.assertEqual(self.manifest.max_attempts, 5)
        self.assertEqual(self.manifest.attempts_per_request, 1)
        self.assertFalse(self.manifest.redirects)
        self.assertEqual(self.manifest.retries, 0)
        self.assertEqual(self.manifest.source_archive_sha256, SOURCE_ARCHIVE_SHA256)
        expected = [
            ("2024-games-regular", 2024, "regular", "35b3783a78ca6fa64905a802be51e82b0b5006fae17ee475bd3cd3c45a3905a1", "81202378ba0a862a92a8e168e00f5df0f3c1827b3f5ac2876c7e8efd4d7a5633"),
            ("2024-games-postseason", 2024, "postseason", "35b3783a78ca6fa64905a802be51e82b0b5006fae17ee475bd3cd3c45a3905a1", "81202378ba0a862a92a8e168e00f5df0f3c1827b3f5ac2876c7e8efd4d7a5633"),
            ("2025-games-regular", 2025, "regular", "7663694144e27034bab79a4db4a7a37ce27d0f0dede34704654cd74d02171853", "8f9e919d81fbaf71a23c67cd2da73d59d9225513c115f08d5060bb1caf300f47"),
            ("2025-games-postseason", 2025, "postseason", "7663694144e27034bab79a4db4a7a37ce27d0f0dede34704654cd74d02171853", "8f9e919d81fbaf71a23c67cd2da73d59d9225513c115f08d5060bb1caf300f47"),
            ("2026-games-regular", 2026, "regular", "9bf66d0ccab3878c7926f17b44664644774eed8ea95a7ffa73b3a206eb45296a", "8a99677a4c98e3bddfd5ee3d2f80b0eab4813ef8772cc6d9c47f74303f547d8a"),
        ]
        self.assertEqual(len(raw["requests"]), len(expected))
        self.assertEqual(len(self.manifest.requests), len(expected))
        for request, entry, raw_entry in zip(self.manifest.requests, expected, raw["requests"]):
            request_id, year, season_type, checksum, snapshot_sha256 = entry
            self.assertEqual(request.request_id, request_id)
            self.assertEqual(request.endpoint, "/games")
            self.assertIsNone(request.expected_game_id)
            self.assertEqual(
                request.parameter_map,
                {"classification": "fbs", "seasonType": season_type, "year": year},
            )
            self.assertEqual(request.parent_snapshot_checksum, checksum)
            self.assertEqual(request.parent_snapshot_sha256, snapshot_sha256)
            self.assertEqual(raw_entry["expected_game_id"], None)
            self.assertEqual(raw_entry["params"], request.parameter_map)
            self.assertEqual(raw_entry["parent_snapshot_sha256"], snapshot_sha256)

    def test_old_pilot_identity_and_allowlist_remain_canonical(self):
        self.assertEqual(self.old_manifest.manifest_sha256, PILOT_MANIFEST_SHA256)
        self.assertEqual(self.old_manifest.pilot_id, PILOT_ID)
        self.assertEqual(self.old_manifest.max_attempts, MAX_ATTEMPTS)
        self.assertEqual(len(self.old_manifest.requests), MAX_ATTEMPTS)
        self.assertEqual(
            [request.request_id for request in self.old_manifest.requests],
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

    def test_wrong_extra_reordered_and_altered_plan_files_fail_closed(self):
        raw = json.loads((REPO_ROOT / SEASON_METADATA_CONFIG).read_text(encoding="utf-8"))
        mutations = []
        reordered = dict(raw)
        reordered["requests"] = list(reversed(raw["requests"]))
        mutations.append(reordered)
        extra = dict(raw)
        extra["requests"] = list(raw["requests"]) + [raw["requests"][0]]
        mutations.append(extra)
        altered = json.loads(json.dumps(raw))
        altered["requests"][0]["params"]["seasonType"] = "postseason"
        mutations.append(altered)
        wrong_plan = json.loads(json.dumps(raw))
        wrong_plan["pilot_id"] = PILOT_ID
        mutations.append(wrong_plan)
        for index, value in enumerate(mutations):
            path = self.root / f"mutated-plan-{index}.json"
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            with self.assertRaises(PilotManifestError):
                PilotManifest.load(path)

    def test_forged_manifest_and_unknown_grouped_plays_plan_fail_before_transport(self):
        forged = replace(self.manifest, requests=self.manifest.requests[:1])
        calls = []
        with self.assertRaises(PilotManifestError):
            PilotAdapter(
                self._meter(),
                self._store(),
                manifest=forged,
                transport=lambda request: calls.append(request) or b"unexpected",
            ).acquire(self._allowance("forged"), self._binding())
        self.assertEqual(calls, [])

        future = json.loads((REPO_ROOT / SEASON_METADATA_CONFIG).read_text(encoding="utf-8"))
        future["requests"][0]["endpoint"] = "/plays"
        future["requests"][0]["params"] = {
            "classification": "fbs",
            "seasonType": "regular",
            "team": "Ohio State",
            "week": 1,
            "year": 2026,
        }
        path = self.root / "future-grouped-plays.json"
        path.write_text(json.dumps(future, sort_keys=True), encoding="utf-8")
        with self.assertRaises(PilotManifestError):
            PilotManifest.load(path)

    def test_allowances_are_plan_bound_and_schema_exact(self):
        old_path = self.root / "old-allowance.json"
        old_path.write_text(
            json.dumps(
                {
                    "allowance_id": "old-plan",
                    "pilot_manifest_sha256": PILOT_MANIFEST_SHA256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        old_allowance = PilotAllowance.load(old_path)
        with self.assertRaises(PilotAllowanceError):
            old_allowance.validate(self.manifest)

        wrong = self.root / "wrong-allowance.json"
        wrong.write_text(
            json.dumps(
                {
                    "allowance_id": "wrong-plan",
                    "pilot_manifest_sha256": SEASON_METADATA_MANIFEST_SHA256,
                    "max_attempts": 9,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PilotAllowanceError):
            PilotAllowance.load(wrong)

        extra = self.root / "extra-allowance.json"
        extra.write_text(
            json.dumps(
                {
                    "allowance_id": "extra-plan",
                    "pilot_manifest_sha256": SEASON_METADATA_MANIFEST_SHA256,
                    "max_attempts": SEASON_METADATA_MAX_ATTEMPTS,
                    "purpose": "historical",
                    "unexpected": True,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(PilotAllowanceError):
            PilotAllowance.load(extra)

    def test_allowance_identity_cannot_be_reused_with_changed_document(self):
        store = self._store("allowance-reuse-store")
        meter = self._meter("allowance-reuse-meter", historical=10, absolute=10)
        first = self._allowance("reuse-first", allowance_id="same-season-allowance")
        calls = []
        self._adapter(
            lambda request: calls.append(request.request_id) or b"response",
            meter=meter,
            store=store,
        ).acquire(first, self._binding())

        changed_path = self.root / "allowance-reuse-changed.json"
        changed_path.write_text(
            json.dumps(
                {
                    "purpose": "historical",
                    "max_attempts": SEASON_METADATA_MAX_ATTEMPTS,
                    "pilot_manifest_sha256": SEASON_METADATA_MANIFEST_SHA256,
                    "allowance_id": "same-season-allowance",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        changed = PilotAllowance.load(changed_path)
        self.assertNotEqual(first.document_sha256, changed.document_sha256)
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request.request_id) or b"unexpected",
                meter=meter,
                store=store,
            ).acquire(changed, self._replay_binding())
        self.assertEqual(len(calls), SEASON_METADATA_MAX_ATTEMPTS)

    def test_dry_run_is_nonmutating_and_does_not_read_credentials_or_network(self):
        class Meter:
            def execute(self, **kwargs):
                raise AssertionError("dry-run must not execute a request")

        store_root = self.root / "dry-run-store"
        adapter = PilotAdapter(
            Meter(),
            manifest=self.manifest,
            transport=lambda request: (_ for _ in ()).throw(AssertionError("transport called")),
        )
        with patch(
            "cfb.excitement_source.configured_api_key",
            side_effect=AssertionError("credential read"),
        ):
            plan = adapter.dry_run()
        self.assertEqual(plan["mode"], "dry-run")
        self.assertEqual(plan["network"], "disabled")
        self.assertEqual(plan["credentials"], "not read")
        self.assertEqual(plan["private_store"], "not opened or created")
        self.assertEqual(plan["remaining_attempts"], SEASON_METADATA_MAX_ATTEMPTS)
        self.assertFalse(store_root.exists())

    def test_source_binding_requires_exact_season_snapshot_pins(self):
        binding = self._binding()
        calls = []
        wrong = dict(binding.snapshots)
        path = next(iter(wrong))
        wrong[path] = InputReference("parent-snapshot", "0" * 64, 0)
        with self.assertRaises(PilotSourceBindingError):
            self._adapter(
                lambda request: calls.append(request) or b"unexpected",
                meter=self._meter(),
                store=self._store(),
            ).acquire(
                self._allowance("binding-wrong"),
                SourceBinding(binding.source_archive, wrong),
            )
        extra = dict(binding.snapshots)
        extra["snapshots/unreviewed.json"] = InputReference("parent-snapshot", "0" * 64, 0)
        with self.assertRaises(PilotSourceBindingError):
            self._adapter(
                lambda request: calls.append(request) or b"unexpected",
                meter=self._meter("binding-extra"),
                store=self._store("binding-extra"),
            ).acquire(
                self._allowance("binding-extra"),
                SourceBinding(binding.source_archive, extra),
            )
        self.assertEqual(calls, [])

    def test_claims_happen_before_meter_and_transport_for_all_five_exact_keys(self):
        store = self._store("claim-order-store")
        meter = self._meter(
            "claim-order-meter",
            historical=SEASON_METADATA_MAX_ATTEMPTS,
            absolute=SEASON_METADATA_MAX_ATTEMPTS,
        )
        allowance = self._allowance("claim-order")
        calls = []

        def transport(request):
            calls.append(request.request_id)
            with sqlite3.connect(store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3") as connection:
                state = connection.execute(
                    "SELECT state FROM claims WHERE request_id = ?", (request.request_id,)
                ).fetchone()
            latest = meter.audit_records()[-1]
            self.assertEqual(state, ("claimed",))
            self.assertEqual(latest["outcome"], "started")
            return request.request_id.encode()

        result = self._adapter(transport, meter=meter, store=store).acquire(
            allowance, self._binding()
        )
        self.assertEqual(calls, [request.request_id for request in self.manifest.requests])
        self.assertEqual(len(result.receipts), 5)
        self.assertEqual(sum(row["budget_impact"] for row in meter.audit_records()), 5)

    def test_failed_and_interrupted_attempts_consume_each_key_without_retry(self):
        store = self._store("failure-store")
        meter = self._meter("failure-meter", historical=10, absolute=10)
        allowance = self._allowance("failure")
        calls = []

        def fail(request):
            calls.append(request.request_id)
            raise RuntimeError("synthetic failure")

        with self.assertRaises(PilotTransportError):
            self._adapter(fail, meter=meter, store=store).acquire(allowance, self._binding())
        self.assertEqual(calls, [self.manifest.requests[0].request_id])
        self.assertEqual(meter.audit_records()[0]["outcome"], "failed")
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request.request_id) or b"retry",
                meter=meter,
                store=store,
            ).acquire(self._allowance("failure-restart"), self._replay_binding())
        self.assertEqual(len(calls), 1)

        interrupted_store = self._store("interrupted-store")
        interrupted_meter = self._meter("interrupted-meter", historical=10, absolute=10)
        interrupted_calls = []

        def interrupt(request):
            interrupted_calls.append(request.request_id)
            raise KeyboardInterrupt("synthetic interruption")

        with self.assertRaises(BaseException):
            self._adapter(interrupt, meter=interrupted_meter, store=interrupted_store).acquire(
                self._allowance("interrupted"), self._binding()
            )
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: interrupted_calls.append(request.request_id) or b"retry",
                meter=interrupted_meter,
                store=interrupted_store,
            ).acquire(self._allowance("interrupted-restart"), self._replay_binding())
        self.assertEqual(interrupted_calls, [self.manifest.requests[0].request_id])

    def test_missing_empty_and_replaced_ledger_fail_closed_before_transport(self):
        _, calls, store, meter, allowance = self._run_complete("missing-ledger")
        ledger = store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3"
        ledger.unlink()
        replay_calls = []
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"duplicate",
                meter=meter,
                store=_BindingStore(store.root),
            ).acquire(allowance, self._replay_binding())
        self.assertEqual(calls, [request.request_id for request in self.manifest.requests])
        self.assertEqual(replay_calls, [])

        _, _, empty_store, empty_meter, empty_allowance = self._run_complete("empty-ledger")
        (empty_store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3").write_bytes(b"")
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"duplicate",
                meter=empty_meter,
                store=_BindingStore(empty_store.root),
            ).acquire(empty_allowance, self._replay_binding())

        _, _, first_store, first_meter, first_allowance = self._run_complete("replaced-first")
        _, _, second_store, _, _ = self._run_complete("replaced-second")
        shutil.copy2(
            second_store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3",
            first_store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3",
        )
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"duplicate",
                meter=first_meter,
                store=_BindingStore(first_store.root),
            ).acquire(first_allowance, self._replay_binding())
        self.assertEqual(replay_calls, [])

    def test_initialization_interruption_leaves_restart_fail_closed(self):
        store = self._store("init-crash-store")
        meter = self._meter("init-crash-meter", historical=10, absolute=10)
        allowance = self._allowance("init-crash")
        calls = []

        def crash(_ledger, **_kwargs):
            raise KeyboardInterrupt("synthetic initialization interruption")

        with patch.object(_ClaimLedger, "_initialize", crash):
            with self.assertRaises(KeyboardInterrupt):
                self._adapter(
                    lambda request: calls.append(request) or b"unexpected",
                    meter=meter,
                    store=store,
                ).acquire(allowance, self._binding())
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request) or b"retry",
                meter=meter,
                store=store,
            ).acquire(allowance, self._binding())
        self.assertEqual(calls, [])

    def test_restart_revalidates_retained_response_bytes(self):
        result, _, store, meter, allowance = self._run_complete("retention")
        reference = result.receipts[0].response
        object_path = store.root / "objects" / reference.sha256[:2] / reference.sha256
        object_path.write_bytes(b"tampered season metadata")
        calls = []
        with self.assertRaises(PilotRetentionError):
            self._adapter(
                lambda request: calls.append(request) or b"retry",
                meter=meter,
                store=LocalInputStore(store.root),
            ).acquire(allowance, self._replay_binding())
        self.assertEqual(calls, [])

    def test_plan_and_ledger_identity_are_distinct_from_exhausted_original_pilot(self):
        store = self._store("shared-store")
        meter = RequestMeter(
            self.root / "shared-meter.sqlite3",
            RequestBudgets(scheduled=0, historical=MAX_ATTEMPTS + 5, absolute=MAX_ATTEMPTS + 5),
            clock=lambda: NOW,
        )
        old_allowance_path = self.root / "old-shared-allowance.json"
        old_allowance_path.write_text(
            json.dumps(
                {
                    "allowance_id": "old-shared",
                    "pilot_manifest_sha256": PILOT_MANIFEST_SHA256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        old_allowance = PilotAllowance.load(old_allowance_path)
        old_calls = []
        old_result = PilotAdapter(
            meter,
            store,
            manifest=self.old_manifest,
            transport=lambda request: old_calls.append(request.request_id) or b"old-bytes",
            clock=lambda: NOW,
        ).acquire(old_allowance, self._replay_binding(self.old_manifest))
        season_calls = []
        season_result = self._adapter(
            lambda request: season_calls.append(request.request_id) or b"season-bytes",
            meter=meter,
            store=store,
        ).acquire(self._allowance("shared-season"), self._replay_binding())
        self.assertEqual(old_result.remaining_attempts, 0)
        self.assertEqual(season_result.remaining_attempts, 0)
        self.assertEqual(len(old_calls), MAX_ATTEMPTS)
        self.assertEqual(len(season_calls), SEASON_METADATA_MAX_ATTEMPTS)
        self.assertEqual(sum(row["budget_impact"] for row in meter.audit_records()), 14)
        old_restart_calls = []
        old_restart_allowance_path = self.root / "old-shared-restart-allowance.json"
        old_restart_allowance_path.write_text(
            json.dumps(
                {
                    "allowance_id": "old-shared-restart",
                    "pilot_manifest_sha256": PILOT_MANIFEST_SHA256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        old_restart = PilotAdapter(
            meter,
            store,
            manifest=self.old_manifest,
            transport=lambda request: old_restart_calls.append(request) or b"reset",
            clock=lambda: NOW,
        ).acquire(
            PilotAllowance.load(old_restart_allowance_path),
            self._replay_binding(self.old_manifest),
        )
        self.assertEqual(old_restart.remaining_attempts, 0)
        self.assertEqual(old_restart_calls, [])
        self.assertTrue(
            (store.root / f".{PILOT_ID}.claims.sqlite3").exists()
        )
        self.assertTrue(
            (store.root / f".{SEASON_METADATA_PILOT_ID}.claims.sqlite3").exists()
        )
        self.assertNotEqual(old_result.pilot_id, season_result.pilot_id)
        self.assertNotEqual(old_result.manifest_sha256, season_result.manifest_sha256)

    def test_season_receipts_keep_raw_bytes_and_local_paths_private(self):
        result, _, store, _, _ = self._run_complete(
            "privacy", response=lambda request: b"synthetic-season-raw"
        )
        public = json.dumps(result.safe_dict(), sort_keys=True)
        self.assertNotIn("synthetic-season-raw", public)
        self.assertNotIn(str(store.root), public)
        self.assertNotIn(str(store.root), json.dumps(result.receipts[0].public_receipt()))

    def test_concurrent_season_adapters_do_not_duplicate_or_overspend(self):
        store = self._store("concurrent-store")
        meter = self._meter("concurrent-meter", historical=SEASON_METADATA_MAX_ATTEMPTS, absolute=SEASON_METADATA_MAX_ATTEMPTS)
        allowance = self._allowance("concurrent")
        calls = []

        def transport(request):
            calls.append(request.request_id)
            return request.request_id.encode()

        def run():
            return self._adapter(transport, meter=meter, store=store).acquire(
                allowance, self._binding()
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run), pool.submit(run)]
            results = []
            errors = []
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as error:
                    errors.append(error)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PilotClaimError)
        self.assertEqual(len(calls), SEASON_METADATA_MAX_ATTEMPTS)
        self.assertEqual(len(set(calls)), SEASON_METADATA_MAX_ATTEMPTS)
        self.assertLessEqual(
            sum(row["budget_impact"] for row in meter.audit_records()),
            SEASON_METADATA_MAX_ATTEMPTS,
        )


if __name__ == "__main__":
    unittest.main()
