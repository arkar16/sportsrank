"""Independent public-boundary checks for the contract-4 progression feature."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
from types import MappingProxyType
import unittest

from cfb.public_site import (
    LocalValidationReceipt,
    PublicSiteError,
    validate_and_export,
    validate_public_output,
    verify_local_receipt,
)
from cfb.publication import PublicationPreparationError, prepare_reviewed_package
from cfb.recovery_inputs import RecoveryInputBundle
from cfb.ranking_engine import PreviousFinal
from cfb.release import build_release
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceTeam
from tests.test_progression_contract_independent import (
    MODEL,
    STAMP,
    _next_season_snapshot,
    _source_snapshot,
    _write_base,
)
from tests.test_public_site import REPOSITORY, _canonical


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _site_inventory(site: Path) -> list[dict[str, object]]:
    """Compute the public inventory independently of the implementation."""

    entries: list[dict[str, object]] = []
    for path in sorted(path for path in site.rglob("*") if path.is_file()):
        relative = path.relative_to(site).as_posix()
        content = path.read_bytes()
        entries.append(
            {"path": relative, "bytes": len(content), "sha256": _sha256(content)}
        )
    return entries


def _inventory_hash(inventory: list[dict[str, object]]) -> str:
    return _sha256(_canonical(inventory))


def _source_file(snapshot, path: Path) -> tuple[str, int]:
    """Write one trusted source snapshot in the retained-input wire format."""

    games = []
    for game in snapshot.games:
        value = asdict(game)
        if snapshot.metadata["schema_version"] < 4:
            for field in ("provider_season_type", "provider_playoff", "phase", "phase_source"):
                if value.get(field) is None:
                    value.pop(field, None)
        if value.get("provider_week") is None:
            value.pop("provider_week", None)
        games.append(value)
    raw = {
        **dict(snapshot.metadata),
        "sport": snapshot.sport,
        "classification": snapshot.classification,
        "year": snapshot.year,
        "teams": [asdict(team) for team in snapshot.teams],
        "games": games,
        "checksum": snapshot.checksum,
    }
    path.write_bytes(_canonical(raw))
    return _sha256(path.read_bytes()), path.stat().st_size


def _source_bundle(root: Path, snapshots: tuple[SeasonSnapshot, ...]):
    source = root / "source-inputs"
    source_snapshots = source / "snapshots"
    source_snapshots.mkdir(parents=True)
    entries = []
    pins = {}
    for snapshot in snapshots:
        source_path = source_snapshots / f"cfb-fbs-{snapshot.year}.json"
        source_sha, source_bytes = _source_file(snapshot, source_path)
        relative = source_path.relative_to(source).as_posix()
        pins[relative] = source_sha
        entries.append(
            {
                "path": relative,
                "sha256": source_sha,
                "bytes": source_bytes,
                "snapshot_checksum": snapshot.checksum,
                "schema_version": int(snapshot.metadata["schema_version"]),
            }
        )
    trust_path = source / "inputs-manifest.json"
    trust_path.write_bytes(_canonical({"files": entries}))
    archive = root / "source-inputs.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(path for path in source.rglob("*") if path.is_file()):
            bundle.add(path, arcname=path.relative_to(source).as_posix())
    archive_sha256 = _sha256(archive.read_bytes())
    trusted_path = root / "trusted-inputs.json"
    trusted_path.write_bytes(
        _canonical(
            {
                "schema_version": 1,
                "record_type": "sr7_recovery_input_trust",
                "source_inputs": {
                    "manifest_sha256": _sha256(trust_path.read_bytes()),
                    "archive_sha256": archive_sha256,
                    "files": {
                        item["path"]: {
                            key: item[key]
                            for key in (
                                "sha256",
                                "bytes",
                                "snapshot_checksum",
                                "schema_version",
                            )
                            if key in item
                        }
                        for item in entries
                    },
                },
            }
        )
    )
    source_inputs = RecoveryInputBundle.from_directory(
        source,
        trusted_file_sha256=pins,
        archive=archive,
        expected_archive_sha256=archive_sha256,
    )
    return source, trusted_path, source_inputs


def _pre2024_snapshot() -> SeasonSnapshot:
    teams = (SourceTeam("Alpha", "Test"), SourceTeam("Beta", "Test"))
    metadata = MappingProxyType(
        {
            "schema_version": 3,
            "sport": "cfb",
            "classification": "FBS",
            "year": 2024,
            "teams_fetched_at": "2026-08-01T00:00:00+00:00",
            "games_fetched_at": "2026-08-01T00:00:00+00:00",
            "complete_through_week": -1,
            "calendar_provenance": None,
            "correction_registry_provenance": None,
            "migration_provenance": None,
        }
    )
    return SeasonSnapshot(
        "cfb", "FBS", 2024, teams, (), metadata, _checksum(metadata, teams, ())
    )


def _write_pre2024_base(path: Path) -> PreviousFinal:
    path.mkdir()
    (path / "index.html").write_text("retained pre-2024 baseline", encoding="utf-8")
    final = path / "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html"
    final.parent.mkdir(parents=True)
    final.write_text(
        "<html><body><table><thead><tr><th>school</th><th>cors</th>"
        "<th>wins_vs_expected</th></tr></thead><tbody>"
        "<tr><td>Alpha</td><td>10</td><td>0</td></tr>"
        "<tr><td>Beta</td><td>20</td><td>0</td></tr>"
        "</tbody></table></body></html>",
        encoding="utf-8",
    )
    return PreviousFinal({"Alpha": 10.0, "Beta": 20.0}, {})


class ProgressionPublicContractIndependentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_current_2025_export(self):
        base = self.root / "published"
        previous, _previous_bytes = _write_base(base)
        snapshot = _source_snapshot(2)

        source, trust_path, source_inputs = _source_bundle(self.root, (snapshot,))

        # Repeating one immutable source keeps the public fixture small while
        # still producing the complete current 2025 progression boundary.
        published = base
        candidate = None
        for label, phase, target in (
            ("preseason", "preseason", None),
            ("w0", "week", 0),
            ("w1", "week", 1),
            ("final", "final", 2),
        ):
            candidate = build_release(
                snapshot,
                self.root / label,
                release_id=label,
                phase=phase,
                target_week=target,
                previous_final=previous,
                model_version=MODEL,
                code_revision="public-contract4-independent",
                timestamp=STAMP,
                published_site=published,
                source_inputs=source_inputs,
            )
            published = candidate.site
        assert candidate is not None

        firebase = self.root / "firebase.json"
        firebase.write_bytes(_canonical({"hosting": {"public": "website"}}))
        site = self.root / "public"
        receipt_path = self.root / "receipt.json"
        receipt = validate_and_export(
            candidate,
            base,
            source_inputs,
            firebase,
            site,
            receipt_path,
            source_root=source,
            trusted_input_manifest=trust_path,
            code_root=REPOSITORY,
        )
        # The small progression baseline is a path-backed PreviousFinal. For
        # final reviewed-package/tree binding, attach the repository's typed
        # verified baseline identity so the fixture exercises the real package
        # contract rather than a draft path-only baseline.
        committed_receipt = json.loads(
            (REPOSITORY / "config/sr7-local-validation-receipt.json").read_bytes()
        )
        receipt_value = receipt.to_dict()
        receipt_value["baseline"] = committed_receipt["baseline"]
        receipt_path.write_bytes(_canonical(receipt_value))
        receipt = LocalValidationReceipt.from_bytes(receipt_path.read_bytes())
        return site, receipt_path, firebase, source, trust_path, receipt

    def _build_current_2026_export(self):
        base = self.root / "published"
        previous, _previous_bytes = _write_base(base)
        snapshot_2025 = _source_snapshot(2)
        snapshot_2026 = _next_season_snapshot(0)
        source, trust_path, source_inputs = _source_bundle(
            self.root, (snapshot_2025, snapshot_2026)
        )
        published = base
        for label, phase, target in (
            ("preseason", "preseason", None),
            ("w0", "week", 0),
            ("w1", "week", 1),
            ("final", "final", 2),
        ):
            release = build_release(
                snapshot_2025,
                self.root / label,
                release_id=label,
                phase=phase,
                target_week=target,
                previous_final=previous,
                model_version=MODEL,
                code_revision="public-contract4-independent",
                timestamp=STAMP,
                published_site=published,
                source_inputs=source_inputs,
            )
            published = release.site
        candidate = build_release(
            snapshot_2026,
            self.root / "2026-w0",
            release_id="2026-w0",
            phase="week",
            target_week=0,
            model_version=MODEL,
            code_revision="public-contract4-cross-season-independent",
            timestamp="2026-09-15T12:00:00+00:00",
            published_site=published,
            source_inputs=source_inputs,
        )
        firebase = self.root / "firebase.json"
        firebase.write_bytes(_canonical({"hosting": {"public": "website"}}))
        site = self.root / "public"
        receipt_path = self.root / "receipt.json"
        receipt = validate_and_export(
            candidate,
            published,
            source_inputs,
            firebase,
            site,
            receipt_path,
            source_root=source,
            trusted_input_manifest=trust_path,
            code_root=REPOSITORY,
        )
        return site, receipt_path, firebase, source, trust_path, receipt

    def _build_pre2024_future_bundle_export(self):
        base = self.root / "pre2024-published"
        previous = _write_pre2024_base(base)
        snapshot_2024 = _pre2024_snapshot()
        snapshot_2025 = _source_snapshot(2)
        source, trust_path, source_inputs = _source_bundle(
            self.root, (snapshot_2024, snapshot_2025)
        )
        candidate = build_release(
            snapshot_2024,
            self.root / "2024-preseason",
            release_id="2024-preseason",
            phase="preseason",
            target_week=None,
            previous_final=previous,
            model_version=MODEL,
            code_revision="public-contract4-pre2024-independent",
            timestamp="2026-08-15T12:00:00+00:00",
            published_site=base,
            source_inputs=source_inputs,
        )
        firebase = self.root / "pre2024-firebase.json"
        firebase.write_bytes(_canonical({"hosting": {"public": "website"}}))
        site = self.root / "pre2024-public"
        receipt_path = self.root / "pre2024-receipt.json"
        receipt = validate_and_export(
            candidate,
            base,
            source_inputs,
            firebase,
            site,
            receipt_path,
            source_root=source,
            trusted_input_manifest=trust_path,
            code_root=REPOSITORY,
        )
        return site, receipt_path, firebase, source, trust_path, receipt

    def _prepare_with_receipt(
        self,
        site: Path,
        firebase: Path,
        receipt: LocalValidationReceipt,
        output: Path,
    ):
        return prepare_reviewed_package(
            site,
            firebase_json=firebase,
            local_validation_receipt=receipt,
            output=output,
        )

    def _reseal_public_records(
        self,
        site: Path,
        receipt_path: Path,
        receipt: LocalValidationReceipt,
        *,
        strip_snapshot_provenance: bool = False,
        remove_snapshot_seasons: set[int] | None = None,
    ) -> LocalValidationReceipt:
        """Reseal public metadata and receipt identities after a site mutation."""

        manifest = json.loads((site / "manifest.json").read_bytes())
        bindings = list(manifest["snapshot_provenance"])
        if strip_snapshot_provenance:
            doomed = bindings
        elif remove_snapshot_seasons:
            doomed = [
                binding
                for binding in bindings
                if any(
                    binding["path"].startswith(f"cfb/years/{season}/")
                    for season in remove_snapshot_seasons
                )
            ]
        else:
            doomed = []
        if doomed:
            for binding in doomed:
                (site / binding["path"]).unlink()
            manifest["snapshot_provenance"] = [
                binding for binding in bindings if binding not in doomed
            ]
        manifest_inventory = [
            item
            for item in _site_inventory(site)
            if item["path"] not in {"manifest.json", "release.json"}
        ]
        manifest["manifest_inventory"] = manifest_inventory
        manifest["manifest_inventory_sha256"] = _inventory_hash(manifest_inventory)
        manifest_bytes = _canonical(manifest)
        (site / "manifest.json").write_bytes(manifest_bytes)

        release = json.loads((site / "release.json").read_bytes())
        release["manifest_sha256"] = _sha256(manifest_bytes)
        release_inventory = [
            item for item in _site_inventory(site) if item["path"] != "release.json"
        ]
        release["public_site_inventory_sha256"] = _inventory_hash(release_inventory)
        (site / "release.json").write_bytes(_canonical(release))

        mutated_receipt = receipt.to_dict()
        if strip_snapshot_provenance or remove_snapshot_seasons:
            mutated_receipt["transform"]["snapshot_paths"] = [
                binding["path"] for binding in manifest["snapshot_provenance"]
            ]
            mutated_receipt["transform"]["replaced_snapshot_count"] = len(
                manifest["snapshot_provenance"]
            )
        full_inventory = _site_inventory(site)
        mutated_receipt["public_site_inventory"] = full_inventory
        mutated_receipt["public_site_inventory_sha256"] = _inventory_hash(full_inventory)
        receipt_path.write_bytes(_canonical(mutated_receipt))
        return LocalValidationReceipt.from_bytes(receipt_path.read_bytes())

    def test_current4_public_feature_is_required_after_resealing_all_outer_records(self):
        site, receipt_path, firebase, source, trust_path, receipt = self._build_current_2025_export()

        manifest = json.loads((site / "manifest.json").read_bytes())
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["artifact_contract"], 4)
        progression_html = "cfb/years/2025/history/2025_FBS_progression.html"
        progression_json = "cfb/years/2025/history/2025_FBS_progression.json"
        season_page = site / "cfb/years/2025/2025_CFB.html"
        self.assertIn(progression_html, [item["path"] for item in receipt.public_site_inventory])
        self.assertIn(progression_json, [item["path"] for item in receipt.public_site_inventory])
        self.assertIn('history/2025_FBS_progression.html', season_page.read_text(encoding="utf-8"))
        self.assertTrue(validate_public_output(site).ok)
        self.assertEqual(
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path).digest,
            receipt.digest,
        )

        (site / progression_html).unlink()
        (site / progression_json).unlink()
        page = season_page.read_text(encoding="utf-8")
        page = page.replace(
            ' | <a href="history/2025_FBS_progression.html">Ranking progression</a>',
            "",
        )
        season_page.write_text(page, encoding="utf-8")

        # An attacker can reseal the public manifest, release metadata, and
        # local receipt independently. The current feature gate must survive
        # all three updated outer identities.
        resealed_receipt = self._reseal_public_records(site, receipt_path, receipt)

        public_report = validate_public_output(site, expected_receipt=receipt)
        self.assertFalse(public_report.ok, public_report.failures)
        self.assertTrue(
            any("progression" in failure.lower() or "2025" in failure for failure in public_report.failures),
            public_report.failures,
        )
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path)

        with self.assertRaises((PublicSiteError, PublicationPreparationError)):
            self._prepare_with_receipt(
                site, firebase, resealed_receipt, self.root / "forged.tar.gz"
            )
        self.assertFalse((self.root / "forged.tar.gz").exists())

    def test_unmodified_current4_public_export_prepares_with_independent_package_context(self):
        site, _receipt_path, firebase, _source, _trust_path, receipt = self._build_current_2025_export()
        prepared = self._prepare_with_receipt(
            site, firebase, receipt, self.root / "valid-package.tar.gz"
        )
        self.assertTrue(prepared.archive.is_file())
        prepared.assert_current()

    def test_trusted_2025_source_identity_cannot_lose_public_provenance(self):
        site, receipt_path, firebase, source, trust_path, receipt = self._build_current_2025_export()
        manifest = json.loads((site / "manifest.json").read_bytes())
        self.assertTrue(manifest["snapshot_provenance"])

        # Remove only the public origin/provenance files and selectors, then
        # reseal the receipt transform as well. The receipt's private
        # evidence still names the trusted 2025 source identity, while the
        # progression feature remains present to isolate the origin omission.
        resealed_receipt = self._reseal_public_records(
            site,
            receipt_path,
            receipt,
            strip_snapshot_provenance=True,
        )
        self.assertEqual(resealed_receipt.transform["snapshot_paths"], [])
        self.assertTrue(
            any(
                item["season"] == 2025
                for item in resealed_receipt.private_evidence["source_inputs"]
            )
        )

        # Public-only validation has no trusted source identity after the
        # bindings are removed; receipt verification must enforce it.
        self.assertFalse(
            validate_public_output(site, expected_receipt=receipt).ok
        )
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path)

    def test_current4_2026_retained_2025_origin_cannot_be_removed_with_2026_provenance_kept(self):
        site, receipt_path, firebase, source, trust_path, receipt = self._build_current_2026_export()
        manifest = json.loads((site / "manifest.json").read_bytes())
        self.assertEqual(manifest["artifact_contract"], 4)
        provenance_seasons = {
            int(json.loads((site / binding["path"]).read_bytes())["identity"]["season"])
            for binding in manifest["snapshot_provenance"]
        }
        self.assertEqual(provenance_seasons, {2025, 2026})
        self.assertEqual(
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path).digest,
            receipt.digest,
        )

        for relative in (
            "cfb/years/2025/history/2025_FBS_progression.html",
            "cfb/years/2025/history/2025_FBS_progression.json",
            "cfb/years/2025/2025_CFB.html",
        ):
            (site / relative).unlink()
        resealed_receipt = self._reseal_public_records(
            site,
            receipt_path,
            receipt,
            remove_snapshot_seasons={2025},
        )
        manifest = json.loads((site / "manifest.json").read_bytes())
        remaining_seasons = {
            int(json.loads((site / binding["path"]).read_bytes())["identity"]["season"])
            for binding in manifest["snapshot_provenance"]
        }
        self.assertEqual(remaining_seasons, {2026})
        self.assertEqual(
            {
                int(item["season"])
                for item in resealed_receipt.private_evidence["source_inputs"]
            },
            {2025, 2026},
        )
        self.assertFalse(
            validate_public_output(site, expected_receipt=receipt).ok
        )
        # This is intentionally a required rejection. The current guard only
        # checks that some public provenance remains, so it may accept after
        # 2025 provenance is removed while 2026 provenance survives.
        with self.assertRaises(PublicSiteError):
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path)

    def test_pre2025_current4_with_full_future_bundle_remains_ungated(self):
        site, receipt_path, firebase, source, trust_path, receipt = (
            self._build_pre2024_future_bundle_export()
        )
        manifest = json.loads((site / "manifest.json").read_bytes())
        self.assertEqual(manifest["artifact_contract"], 4)
        self.assertFalse((site / "cfb/years/2025/2025_CFB.html").exists())
        self.assertEqual(
            {
                int(item["season"])
                for item in receipt.private_evidence["source_inputs"]
            },
            {2024, 2025},
        )
        self.assertTrue(validate_public_output(site).ok)
        self.assertEqual(
            verify_local_receipt(receipt_path, site, firebase, REPOSITORY, trust_path).digest,
            receipt.digest,
        )

    def test_combined_feature_origin_and_season_removal_requires_all_public_gates(self):
        site, receipt_path, firebase, source, trust_path, receipt = self._build_current_2025_export()
        progression_html = site / "cfb/years/2025/history/2025_FBS_progression.html"
        progression_json = site / "cfb/years/2025/history/2025_FBS_progression.json"
        season_page = site / "cfb/years/2025/2025_CFB.html"
        self.assertTrue(progression_html.is_file())
        self.assertTrue(progression_json.is_file())
        self.assertTrue(season_page.is_file())

        # Remove the complete public 2025 feature and its origin evidence,
        # including the canonical season page, then reseal every outer record
        # while preserving the current artifact contract.
        progression_html.unlink()
        progression_json.unlink()
        season_page.unlink()
        resealed_receipt = self._reseal_public_records(
            site,
            receipt_path,
            receipt,
            strip_snapshot_provenance=True,
        )
        manifest = json.loads((site / "manifest.json").read_bytes())
        self.assertEqual(manifest["artifact_contract"], 4)
        self.assertEqual(manifest["snapshot_provenance"], [])
        self.assertEqual(resealed_receipt.transform["snapshot_paths"], [])

        expected_errors = (PublicSiteError, PublicationPreparationError)
        gates = {
            "public validation": lambda: validate_public_output(
                site, expected_receipt=receipt
            ).raise_for_failure(),
            "receipt verification": lambda: verify_local_receipt(
                receipt_path, site, firebase, REPOSITORY, trust_path
            ),
            "reviewed packaging": lambda: prepare_reviewed_package(
                site,
                firebase_json=firebase,
                local_validation_receipt=resealed_receipt,
                output=self.root / "combined-forged.tar.gz",
            ),
        }
        for label, gate in gates.items():
            with self.subTest(gate=label):
                try:
                    gate()
                except expected_errors:
                    continue
                except Exception as exc:  # pragma: no cover - diagnostic branch
                    self.fail(f"{label} raised unexpected {type(exc).__name__}: {exc}")
                else:
                    self.fail(f"{label} accepted resealed contract-4 feature/origin removal")
                finally:
                    forged = self.root / "combined-forged.tar.gz"
                    if forged.exists():
                        forged.unlink()
