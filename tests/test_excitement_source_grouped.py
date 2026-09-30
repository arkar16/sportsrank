"""Independent offline verification for the grouped-plays source plan."""

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
    GROUPED_PLAYS_CONFIG,
    GROUPED_PLAYS_MANIFEST_SHA256,
    GROUPED_PLAYS_MAX_ATTEMPTS,
    GROUPED_PLAYS_PILOT_ID,
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
METADATA_IDS = {
    "2024-games-regular",
    "2024-games-postseason",
    "2025-games-regular",
    "2025-games-postseason",
    "2026-games-regular",
}
EXPECTED_GROUPED_MANIFEST_SHA256 = (
    "f93589b18c17a9a14882505c62f50a0617a2f228e75601c7cc1e9580ad7dca21"
)
EXPECTED_GROUPED_SCOPE_COUNTS = {
    (2024, "regular", 1): 39,
    (2024, "regular", 2): 49,
    (2024, "regular", 3): 52,
    (2024, "regular", 4): 54,
    (2024, "regular", 5): 52,
    (2024, "regular", 6): 49,
    (2024, "regular", 7): 52,
    (2024, "regular", 8): 59,
    (2024, "regular", 9): 56,
    (2024, "regular", 10): 48,
    (2024, "regular", 11): 50,
    (2024, "regular", 12): 53,
    (2024, "regular", 13): 62,
    (2024, "regular", 14): 67,
    (2024, "regular", 15): 9,
    (2024, "regular", 16): 1,
    (2024, "postseason", 1): 46,
    (2025, "regular", 1): 48,
    (2025, "regular", 2): 50,
    (2025, "regular", 3): 47,
    (2025, "regular", 4): 50,
    (2025, "regular", 5): 51,
    (2025, "regular", 6): 50,
    (2025, "regular", 7): 56,
    (2025, "regular", 8): 59,
    (2025, "regular", 9): 53,
    (2025, "regular", 10): 52,
    (2025, "regular", 11): 51,
    (2025, "regular", 12): 58,
    (2025, "regular", 13): 60,
    (2025, "regular", 14): 67,
    (2025, "regular", 15): 9,
    (2025, "regular", 16): 1,
    (2025, "postseason", 1): 46,
    (2026, "regular", 1): 51,
    (2026, "regular", 2): 49,
    (2026, "regular", 3): 57,
    (2026, "regular", 4): 1,
}
EXPECTED_SOURCE_ARCHIVE_RECEIPT = {
    "role": "source-archive",
    "sha256": "86a27f80709549c48bfaca763f4e925b3168dbae232745d534986e6fee43da37",
    "size": 75165,
}
EXPECTED_SOURCE_SNAPSHOT_RECEIPTS = {
    "snapshots/cfb-fbs-2024.json": {
        "role": "source-snapshot",
        "sha256": "81202378ba0a862a92a8e168e00f5df0f3c1827b3f5ac2876c7e8efd4d7a5633",
        "size": 314911,
    },
    "snapshots/cfb-fbs-2025.json": {
        "role": "source-snapshot",
        "sha256": "8f9e919d81fbaf71a23c67cd2da73d59d9225513c115f08d5060bb1caf300f47",
        "size": 319204,
    },
    "snapshots/9bf66d0ccab3878c7926f17b44664644774eed8ea95a7ffa73b3a206eb45296a/cfb-fbs-2026.json": {
        "role": "source-snapshot",
        "sha256": "8a99677a4c98e3bddfd5ee3d2f80b0eab4813ef8772cc6d9c47f74303f547d8a",
        "size": 397634,
    },
}
EXPECTED_METADATA_RECEIPTS = {
    "2024-games-regular": {
        "role": "adr21-season-metadata-v1.2024-games-regular",
        "sha256": "496612994745bb632ca6a6a28dd97fe9e5f6744e05e756766473a249cb52b207",
        "size": 685444,
    },
    "2024-games-postseason": {
        "role": "adr21-season-metadata-v1.2024-games-postseason",
        "sha256": "c2104ada734774a378046c3899c95438410a3c0a0a889ce3935f256170bfb4c5",
        "size": 39620,
    },
    "2025-games-regular": {
        "role": "adr21-season-metadata-v1.2025-games-regular",
        "sha256": "28129e184b3a1d04a7bc0a709cc8ac87973f6d4d49efba3a2ea0a86d74f237b6",
        "size": 696655,
    },
    "2025-games-postseason": {
        "role": "adr21-season-metadata-v1.2025-games-postseason",
        "sha256": "32f068194d374777aa97d9559bc459d849599b6e41f8f501371ff96cee391e30",
        "size": 39216,
    },
    "2026-games-regular": {
        "role": "adr21-season-metadata-v1.2026-games-regular",
        "sha256": "006856376bdbbb220d3162ab812a06afd8582148fe52bb1d456680919b42959d",
        "size": 670161,
    },
}


class _BindingStore(LocalInputStore):
    """Use real retained-response integrity while stubbing trusted source pins."""

    def verify(self, reference: InputReference) -> InputReference:
        return reference


class _NoopBinding(SourceBinding):
    def verify(self, store: LocalInputStore, manifest: PilotManifest) -> None:
        return None


class _MetadataFailureStore(_BindingStore):
    def __init__(self, root: Path, failing_role: str):
        self.failing_role = failing_role
        super().__init__(root)

    def verify(self, reference: InputReference) -> InputReference:
        if reference.role == self.failing_role:
            raise ValueError("synthetic missing or mismatched metadata object")
        return reference


class _ReplayStore(LocalInputStore):
    """Bypass synthetic metadata pins while verifying retained response bytes."""

    def verify(self, reference: InputReference) -> InputReference:
        if reference.role.startswith(f"{SEASON_METADATA_PILOT_ID}."):
            return reference
        return super().verify(reference)


class GroupedPlaysPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifest = PilotManifest.load(REPO_ROOT / GROUPED_PLAYS_CONFIG)
        self.metadata_manifest = PilotManifest.load(REPO_ROOT / SEASON_METADATA_CONFIG)
        self.old_manifest = PilotManifest.load(REPO_ROOT / DEFAULT_CONFIG)

    def tearDown(self):
        self.temporary.cleanup()

    def _store(self, suffix: str) -> _BindingStore:
        root = self.root / suffix
        root.mkdir(mode=0o700, exist_ok=True)
        return _BindingStore(root)

    def _meter(
        self,
        suffix: str,
        *,
        historical: int = GROUPED_PLAYS_MAX_ATTEMPTS,
        absolute: int = GROUPED_PLAYS_MAX_ATTEMPTS,
    ) -> RequestMeter:
        return RequestMeter(
            self.root / f"{suffix}.sqlite3",
            RequestBudgets(scheduled=0, historical=historical, absolute=absolute),
            clock=lambda: NOW,
        )

    def _binding(self, manifest: PilotManifest) -> SourceBinding:
        snapshots = {
            path: InputReference.from_public_receipt(receipt)
            for path, receipt in EXPECTED_SOURCE_SNAPSHOT_RECEIPTS.items()
        }
        return SourceBinding(
            InputReference.from_public_receipt(EXPECTED_SOURCE_ARCHIVE_RECEIPT), snapshots
        )

    def _replay_binding(self, manifest: PilotManifest) -> _NoopBinding:
        binding = self._binding(manifest)
        return _NoopBinding(binding.source_archive, binding.snapshots)

    def _allowance(
        self,
        suffix: str,
        *,
        manifest_sha256: str = GROUPED_PLAYS_MANIFEST_SHA256,
        max_attempts: int = GROUPED_PLAYS_MAX_ATTEMPTS,
        allowance_id: str | None = None,
    ) -> PilotAllowance:
        path = self.root / f"allowance-{suffix}.json"
        path.write_text(
            json.dumps(
                {
                    "allowance_id": allowance_id or f"grouped-{suffix}",
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
            meter or self._meter("default-meter"),
            store or self._store("default-store"),
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
        transport=None,
    ):
        store = store or self._store(f"{suffix}-store")
        meter = meter or self._meter(
            f"{suffix}-meter",
            historical=GROUPED_PLAYS_MAX_ATTEMPTS * 2,
            absolute=GROUPED_PLAYS_MAX_ATTEMPTS * 2,
        )
        allowance = allowance or self._allowance(suffix)
        calls = []
        transport = transport or (
            lambda request: calls.append(request.request_id)
            or f"grouped bytes {request.request_id}".encode()
        )
        result = self._adapter(transport, meter=meter, store=store).acquire(
            allowance, self._binding(self.manifest)
        )
        self.assertEqual(len(result.receipts), GROUPED_PLAYS_MAX_ATTEMPTS)
        return result, calls, store, meter, allowance

    def test_grouped_manifest_hash_scopes_filters_and_all_1764_ids_are_frozen(self):
        config_path = REPO_ROOT / GROUPED_PLAYS_CONFIG
        raw_bytes = config_path.read_bytes()
        raw = json.loads(raw_bytes)
        self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), EXPECTED_GROUPED_MANIFEST_SHA256)
        self.assertEqual(GROUPED_PLAYS_MANIFEST_SHA256, EXPECTED_GROUPED_MANIFEST_SHA256)
        self.assertEqual(self.manifest.manifest_sha256, EXPECTED_GROUPED_MANIFEST_SHA256)
        self.assertEqual(self.manifest.pilot_id, GROUPED_PLAYS_PILOT_ID)
        self.assertEqual(self.manifest.max_attempts, len(EXPECTED_GROUPED_SCOPE_COUNTS))
        self.assertEqual(self.manifest.attempts_per_request, 1)
        self.assertFalse(self.manifest.redirects)
        self.assertEqual(self.manifest.retries, 0)
        self.assertEqual(self.manifest.source_archive_sha256, SOURCE_ARCHIVE_SHA256)
        self.assertEqual(self.manifest.metadata_manifest_sha256, SEASON_METADATA_MANIFEST_SHA256)
        self.assertEqual(set(self.manifest.metadata_sources or {}), METADATA_IDS)
        self.assertEqual(len(raw["requests"]), 38)
        self.assertEqual(len(self.manifest.requests), 38)

        expected_scopes = {
            (year, season_type, week)
            for year in (2024, 2025)
            for season_type, weeks in (("regular", range(1, 17)), ("postseason", (1,)))
            for week in weeks
        }
        expected_scopes |= {(2026, "regular", week) for week in range(1, 5)}
        seen_scopes = set()
        scope_counts = {}
        target_ids = []
        for request, raw_request in zip(self.manifest.requests, raw["requests"]):
            params = request.parameter_map
            scope = (request.year, params["seasonType"], params["week"])
            seen_scopes.add(scope)
            scope_counts[scope] = len(request.target_game_ids)
            self.assertEqual(request.endpoint, "/plays")
            self.assertIsNone(request.expected_game_id)
            self.assertEqual(set(params), {"classification", "seasonType", "week", "year"})
            self.assertEqual(params["classification"], "fbs")
            self.assertEqual(request.metadata_request_id, f"{request.year}-games-{params['seasonType']}")
            self.assertEqual(request.request_id, f"{request.year}-plays-{params['seasonType']}-week-{params['week']}")
            self.assertEqual(raw_request["target_game_ids"], list(request.target_game_ids))
            self.assertEqual(raw_request["metadata_request_id"], request.metadata_request_id)
            target_ids.extend(request.target_game_ids)
        self.assertEqual(seen_scopes, expected_scopes)
        self.assertEqual(scope_counts, EXPECTED_GROUPED_SCOPE_COUNTS)
        self.assertEqual(len(target_ids), 1764)
        self.assertEqual(len(set(target_ids)), 1764)
        self.assertTrue(all(game_id.isdigit() for game_id in target_ids))

        raw_sources = raw["metadata_sources"]
        for metadata_id, reference in (self.manifest.metadata_sources or {}).items():
            self.assertEqual(reference.public_receipt(), raw_sources[metadata_id])

    def test_old_and_season_metadata_manifests_remain_canonical(self):
        self.assertEqual(self.old_manifest.manifest_sha256, PILOT_MANIFEST_SHA256)
        self.assertEqual(self.old_manifest.pilot_id, PILOT_ID)
        self.assertEqual(self.old_manifest.max_attempts, MAX_ATTEMPTS)
        self.assertEqual(self.metadata_manifest.manifest_sha256, SEASON_METADATA_MANIFEST_SHA256)
        self.assertEqual(self.metadata_manifest.pilot_id, SEASON_METADATA_PILOT_ID)
        self.assertEqual(self.metadata_manifest.max_attempts, SEASON_METADATA_MAX_ATTEMPTS)

    def test_literal_metadata_and_source_receipts_pin_the_reviewed_inputs(self):
        self.assertEqual(
            {
                key: reference.public_receipt()
                for key, reference in (self.manifest.metadata_sources or {}).items()
            },
            EXPECTED_METADATA_RECEIPTS,
        )
        self.assertEqual(
            self.manifest.source_archive_sha256,
            EXPECTED_SOURCE_ARCHIVE_RECEIPT["sha256"],
        )
        for request in self.manifest.requests:
            self.assertEqual(
                request.parent_snapshot_sha256,
                EXPECTED_SOURCE_SNAPSHOT_RECEIPTS[request.parent_snapshot_path]["sha256"],
            )
        binding = self._binding(self.manifest)
        self.assertEqual(binding.source_archive.public_receipt(), EXPECTED_SOURCE_ARCHIVE_RECEIPT)
        self.assertEqual(
            {
                path: reference.public_receipt()
                for path, reference in binding.snapshots.items()
            },
            EXPECTED_SOURCE_SNAPSHOT_RECEIPTS,
        )

    def test_wrong_extra_reordered_and_altered_grouped_plans_fail_closed(self):
        raw = json.loads((REPO_ROOT / GROUPED_PLAYS_CONFIG).read_text(encoding="utf-8"))
        mutations = []
        reordered = dict(raw)
        reordered["requests"] = list(reversed(raw["requests"]))
        mutations.append(reordered)
        extra = dict(raw)
        extra["requests"] = list(raw["requests"]) + [raw["requests"][0]]
        mutations.append(extra)
        altered = json.loads(json.dumps(raw))
        altered["requests"][0]["params"]["week"] = 2
        mutations.append(altered)
        altered_targets = json.loads(json.dumps(raw))
        altered_targets["requests"][0]["target_game_ids"].reverse()
        mutations.append(altered_targets)
        altered_metadata = json.loads(json.dumps(raw))
        altered_metadata["metadata_manifest_sha256"] = SEASON_METADATA_MANIFEST_SHA256[:-1] + "0"
        mutations.append(altered_metadata)
        for index, value in enumerate(mutations):
            path = self.root / f"grouped-mutated-{index}.json"
            path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            with self.assertRaises(PilotManifestError):
                PilotManifest.load(path)

    def test_canonical_grouped_manifest_object_mutation_fails_before_transport(self):
        forged_request = replace(
            self.manifest.requests[0],
            target_game_ids=tuple(reversed(self.manifest.requests[0].target_game_ids)),
        )
        forged = replace(
            self.manifest,
            requests=(forged_request, *self.manifest.requests[1:]),
        )
        calls = []
        with self.assertRaises(PilotManifestError):
            self._adapter(
                lambda request: calls.append(request) or b"unexpected",
                manifest=forged,
                meter=self._meter("forged-meter"),
                store=self._store("forged-store"),
            ).acquire(self._allowance("forged"), self._binding(self.manifest))
        self.assertEqual(calls, [])

    def test_cross_plan_allowance_and_grouped_identity_cannot_alias(self):
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
        self.assertNotEqual(self.manifest.pilot_id, self.metadata_manifest.pilot_id)
        self.assertNotEqual(self.manifest.manifest_sha256, self.metadata_manifest.manifest_sha256)
        self.assertNotEqual(self.manifest.max_attempts, self.metadata_manifest.max_attempts)

    def test_missing_or_mismatched_metadata_fails_before_claim_and_meter(self):
        metadata_role = (self.manifest.metadata_sources or {})["2024-games-regular"].role
        for suffix in ("missing", "mismatched"):
            store = _MetadataFailureStore(self.root / f"metadata-{suffix}", metadata_role)
            meter = self._meter(f"metadata-{suffix}-meter")
            calls = []
            with self.assertRaises(PilotSourceBindingError):
                self._adapter(
                    lambda request: calls.append(request) or b"unexpected",
                    meter=meter,
                    store=store,
                ).acquire(self._allowance(f"metadata-{suffix}"), self._replay_binding(self.manifest))
            self.assertEqual(calls, [])
            self.assertEqual(meter.audit_records(), [])
            self.assertFalse((store.root / f".{GROUPED_PLAYS_PILOT_ID}.claims.sqlite3").exists())

    def test_claims_precede_meter_and_transport_for_all_grouped_keys(self):
        store = self._store("claim-order-store")
        meter = self._meter("claim-order-meter")
        allowance = self._allowance("claim-order")
        calls = []

        def transport(request):
            calls.append(request.request_id)
            with sqlite3.connect(store.root / f".{GROUPED_PLAYS_PILOT_ID}.claims.sqlite3") as connection:
                state = connection.execute(
                    "SELECT state FROM claims WHERE request_id = ?", (request.request_id,)
                ).fetchone()
            self.assertEqual(state, ("claimed",))
            self.assertEqual(meter.audit_records()[-1]["outcome"], "started")
            return request.request_id.encode()

        result = self._adapter(transport, meter=meter, store=store).acquire(
            allowance, self._binding(self.manifest)
        )
        self.assertEqual(calls, [request.request_id for request in self.manifest.requests])
        self.assertEqual(len(result.receipts), 38)
        self.assertEqual(sum(row["budget_impact"] for row in meter.audit_records()), 38)

    def test_grouped_failure_and_restart_cannot_retry_consumed_key(self):
        store = self._store("failure-store")
        meter = self._meter("failure-meter", historical=76, absolute=76)
        allowance = self._allowance("failure")
        calls = []

        def fail(request):
            calls.append(request.request_id)
            raise RuntimeError("synthetic grouped failure")

        with self.assertRaises(PilotTransportError):
            self._adapter(fail, meter=meter, store=store).acquire(
                allowance, self._binding(self.manifest)
            )
        self.assertEqual(calls, [self.manifest.requests[0].request_id])
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: calls.append(request.request_id) or b"retry",
                meter=meter,
                store=store,
            ).acquire(self._allowance("failure-restart"), self._replay_binding(self.manifest))
        self.assertEqual(len(calls), 1)
        self.assertEqual(meter.audit_records()[0]["outcome"], "failed")

        interrupted_store = self._store("interrupted-store")
        interrupted_meter = self._meter("interrupted-meter", historical=76, absolute=76)
        interrupted_calls = []

        def interrupt(request):
            interrupted_calls.append(request.request_id)
            raise KeyboardInterrupt("synthetic grouped interruption")

        with self.assertRaises(BaseException):
            self._adapter(
                interrupt,
                meter=interrupted_meter,
                store=interrupted_store,
            ).acquire(self._allowance("interrupted"), self._binding(self.manifest))
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: interrupted_calls.append(request.request_id) or b"retry",
                meter=interrupted_meter,
                store=interrupted_store,
            ).acquire(
                self._allowance("interrupted-restart"),
                self._replay_binding(self.manifest),
            )
        self.assertEqual(interrupted_calls, [self.manifest.requests[0].request_id])

    def test_missing_replaced_state_and_retained_bytes_fail_closed_on_restart(self):
        result, calls, store, meter, allowance = self._run_complete("state")
        ledger = store.root / f".{GROUPED_PLAYS_PILOT_ID}.claims.sqlite3"
        ledger.unlink()
        replay_calls = []
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"duplicate",
                meter=meter,
                store=_BindingStore(store.root),
            ).acquire(allowance, self._replay_binding(self.manifest))
        self.assertEqual(replay_calls, [])
        self.assertEqual(len(calls), GROUPED_PLAYS_MAX_ATTEMPTS)

        _, _, first_store, first_meter, first_allowance = self._run_complete("replace-first")
        _, _, second_store, _, _ = self._run_complete("replace-second")
        shutil.copy2(
            second_store.root / f".{GROUPED_PLAYS_PILOT_ID}.claims.sqlite3",
            first_store.root / f".{GROUPED_PLAYS_PILOT_ID}.claims.sqlite3",
        )
        with self.assertRaises(PilotClaimError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"duplicate",
                meter=first_meter,
                store=_BindingStore(first_store.root),
            ).acquire(first_allowance, self._replay_binding(self.manifest))
        self.assertEqual(replay_calls, [])

        retention_result, _, retention_store, retention_meter, retention_allowance = self._run_complete(
            "retention"
        )
        reference = retention_result.receipts[0].response
        (
            retention_store.root / "objects" / reference.sha256[:2] / reference.sha256
        ).write_bytes(
            b"tampered grouped bytes"
        )
        with self.assertRaises(PilotRetentionError):
            self._adapter(
                lambda request: replay_calls.append(request) or b"retry",
                meter=retention_meter,
                store=_ReplayStore(retention_store.root),
            ).acquire(retention_allowance, self._replay_binding(self.manifest))
        self.assertEqual(replay_calls, [])

    def test_old_nine_season_five_and_grouped_38_plans_share_budget_without_aliasing(self):
        store = self._store("shared-store")
        meter = RequestMeter(
            self.root / "shared-meter.sqlite3",
            RequestBudgets(scheduled=0, historical=52, absolute=52),
            clock=lambda: NOW,
        )
        old_path = self.root / "old-shared.json"
        old_path.write_text(
            json.dumps(
                {
                    "allowance_id": "shared-old",
                    "pilot_manifest_sha256": PILOT_MANIFEST_SHA256,
                    "max_attempts": MAX_ATTEMPTS,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        season_path = self.root / "season-shared.json"
        season_path.write_text(
            json.dumps(
                {
                    "allowance_id": "shared-season",
                    "pilot_manifest_sha256": SEASON_METADATA_MANIFEST_SHA256,
                    "max_attempts": SEASON_METADATA_MAX_ATTEMPTS,
                    "purpose": "historical",
                }
            ),
            encoding="utf-8",
        )
        calls = []
        old_allowance = PilotAllowance.load(old_path)
        season_allowance = PilotAllowance.load(season_path)
        grouped_allowance = self._allowance("shared-grouped")
        old_result = PilotAdapter(
            meter,
            store,
            manifest=self.old_manifest,
            transport=lambda request: calls.append(request.request_id) or b"old",
            clock=lambda: NOW,
        ).acquire(
            old_allowance, self._replay_binding(self.old_manifest)
        )
        season_result = PilotAdapter(
            meter,
            store,
            manifest=self.metadata_manifest,
            transport=lambda request: calls.append(request.request_id) or b"season",
            clock=lambda: NOW,
        ).acquire(
            season_allowance, self._replay_binding(self.metadata_manifest)
        )
        grouped_result = self._adapter(
            lambda request: calls.append(request.request_id) or b"grouped",
            meter=meter,
            store=store,
        ).acquire(grouped_allowance, self._replay_binding(self.manifest))
        self.assertEqual(old_result.remaining_attempts, 0)
        self.assertEqual(season_result.remaining_attempts, 0)
        self.assertEqual(grouped_result.remaining_attempts, 0)
        self.assertEqual(len(calls), 52)
        self.assertEqual(sum(row["budget_impact"] for row in meter.audit_records()), 52)
        self.assertEqual(
            {row["outcome"] for row in meter.audit_records()}, {"succeeded"}
        )
        replay_calls = []
        PilotAdapter(
            meter,
            store,
            manifest=self.old_manifest,
            transport=lambda request: replay_calls.append(request) or b"old-replay",
            clock=lambda: NOW,
        ).acquire(old_allowance, self._replay_binding(self.old_manifest))
        PilotAdapter(
            meter,
            store,
            manifest=self.metadata_manifest,
            transport=lambda request: replay_calls.append(request) or b"season-replay",
            clock=lambda: NOW,
        ).acquire(season_allowance, self._replay_binding(self.metadata_manifest))
        self._adapter(
            lambda request: replay_calls.append(request) or b"grouped-replay",
            meter=meter,
            store=store,
        ).acquire(grouped_allowance, self._replay_binding(self.manifest))
        self.assertEqual(replay_calls, [])
        for pilot_id in (PILOT_ID, SEASON_METADATA_PILOT_ID, GROUPED_PLAYS_PILOT_ID):
            self.assertTrue((store.root / f".{pilot_id}.claims.sqlite3").exists())
            self.assertTrue((store.root / f".{pilot_id}.genesis.json").exists())
            self.assertTrue((store.root / f".{pilot_id}.ledger-ready.json").exists())

    def test_grouped_concurrent_adapters_do_not_duplicate_or_overspend(self):
        store = self._store("concurrent-store")
        meter = self._meter("concurrent-meter")
        allowance = self._allowance("concurrent")
        calls = []

        def transport(request):
            calls.append(request.request_id)
            return request.request_id.encode()

        def run():
            return self._adapter(transport, meter=meter, store=store).acquire(
                allowance, self._binding(self.manifest)
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
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(len(results) + len(errors), 2)
        self.assertTrue(all(isinstance(error, PilotClaimError) for error in errors))
        self.assertEqual(len(calls), GROUPED_PLAYS_MAX_ATTEMPTS)
        self.assertEqual(len(set(calls)), GROUPED_PLAYS_MAX_ATTEMPTS)
        self.assertLessEqual(
            sum(row["budget_impact"] for row in meter.audit_records()),
            GROUPED_PLAYS_MAX_ATTEMPTS,
        )


if __name__ == "__main__":
    unittest.main()
