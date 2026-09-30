"""Contract tests for immutable forecast-to-BEV binding."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cfb.excitement_forecast import (
    BEV_BINDING_KIND,
    MAX_BEV_PUBLICATION_HISTORY,
    BevArtifact,
    ExcitementForecastError,
    ReconstructionInputQualification,
    bev_relative_path,
    bind_bev,
    load_issued_bev,
    load_issued_bev_history,
    prepare_reconstructed_bev,
)
from cfb.forecast_record import (
    EvidenceRef,
    ForecastCandidate,
    ForecastProvenance,
    GameIdentity,
    GameTimingEvidence,
)
from cfb.firebase import FakeFirebasePublicationBackend
from cfb.github_archive import ArchiveSpec, FakeImmutableArchive
from cfb.publication import PreparedPackage, _deterministic_package, bind_merged_candidate
from tests.test_forecast_publication import publish, recorded
from tests.test_publication_execution import (
    APP_IDENTITY, TARGET, approval, fixture, inventory, sha, validation,
)


UTC = timezone.utc
DIGEST = "sha256:" + "a" * 64


def instant(hour: int) -> datetime:
    return datetime(2026, 9, 1, hour, tzinfo=UTC)


def evidence(name: str) -> EvidenceRef:
    return EvidenceRef("synthetic-test", name, DIGEST)


def candidate(
    suffix: str = "A",
    *,
    margin: str = "2",
    home_rank: int | None = 1,
    away_rank: int | None = 2,
    predecessor: str | None = None,
) -> ForecastCandidate:
    game = GameIdentity(
        f"game-{suffix}", 2026, 1, f"Home {suffix}", f"Away {suffix}",
        "fbs", "fbs", False,
    )
    provenance = ForecastProvenance(
        "WEEK_0", "through-week-0", DIGEST, DIGEST, "v0.4.0",
        "season-snapshot", "ea0048d", Decimal("20"), Decimal("20"),
        Decimal("2"), home_rank, away_rank,
    )
    return ForecastCandidate.create(
        game=game, home_margin=margin, precision=2, provenance=provenance,
        predecessor_version_id=predecessor,
        replacement_reason="pregame correction" if predecessor else None,
    )


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def reseal(value: dict) -> bytes:
    content = dict(value)
    content.pop("artifact_id", None)
    value["artifact_id"] = "sha256:" + hashlib.sha256(canonical(content)).hexdigest()
    return canonical(value)


def qualification(
    item: ForecastCandidate,
    *,
    inputs_as_of: datetime,
    source: EvidenceRef | None = None,
    **overrides,
) -> ReconstructionInputQualification:
    provenance = item.provenance.to_dict()
    anchor_digest = bind_bev(item)[1].forecast_anchor_digest
    values = {
        "inputs_as_of": inputs_as_of,
        "source": source or EvidenceRef(
            "synthetic-test", "qualified-checkpoint", item.provenance.source_snapshot_digest
        ),
        "forecast_anchor_digest": anchor_digest,
        "rating_checkpoint": provenance["rating_checkpoint"],
        "rating_cutoff": provenance["rating_cutoff"],
        "rating_artifact_digest": provenance["rating_artifact_digest"],
        "source_snapshot_digest": provenance["source_snapshot_digest"],
        "model_version": provenance["model_version"],
        "source_kind": provenance["source_kind"],
        "code_revision": provenance["code_revision"],
        "home_rating": provenance["home_rating"],
        "away_rating": provenance["away_rating"],
        "home_field_advantage": provenance["home_field_advantage"],
        "home_rank": provenance.get("home_rank"),
        "away_rank": provenance.get("away_rank"),
    }
    values.update(overrides)
    return ReconstructionInputQualification(**values)


def publication_fixture(
    root: Path,
    pairs: list[tuple[ForecastCandidate, BevArtifact]],
    *,
    archive: FakeImmutableArchive | None = None,
):
    fx = fixture(root, archive=archive)
    for item, artifact in pairs:
        forecast_path = fx.prepared.site / f"cfb/years/{item.game.season}/forecasts/{item.version_id[7:]}.json"
        forecast_path.parent.mkdir(parents=True, exist_ok=True)
        forecast_path.write_bytes(canonical(item.to_dict()))
        bev_path = fx.prepared.site / bev_relative_path(item, artifact.artifact_id)
        bev_path.parent.mkdir(parents=True, exist_ok=True)
        bev_path.write_bytes(artifact.to_bytes())
    repository = root / "repository"
    subprocess.run(["git", "-C", str(repository), "add", "website"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repository), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "commit", "-qm", "BEV fixture"],
        check=True, capture_output=True,
    )
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"]).decode().strip()
    files = {
        f"website/{path.relative_to(fx.prepared.site).as_posix()}": path.read_bytes()
        for path in fx.prepared.site.rglob("*") if path.is_file()
    }
    files["firebase.json"] = fx.prepared.firebase_json.read_bytes()
    inv = inventory(files)
    config_sha = sha(files["firebase.json"])
    archive_path = root / "bev-package.tar.gz"
    bundle_sha = _deterministic_package(fx.prepared.site, fx.prepared.firebase_json, archive_path)
    fx.prepared = PreparedPackage._create(
        archive_path, bundle_sha, inv, config_sha, fx.baseline.digest,
        fx.package.expected_predecessor, "6" * 64,
        validation(inv, config_sha, fx.baseline.digest, "6" * 64),
        fx.prepared.site, fx.prepared.firebase_json,
    )
    fx.package = bind_merged_candidate(fx.prepared, candidate_commit=commit, reader=fx.reader)
    fx.approval, fx.runtime = approval(commit)
    for role in fx.evidence:
        path = root / f"{role}.tar.gz"
        fx.evidence[role] = fx.archive.seal_or_reconcile(ArchiveSpec(
            "owner/repository", f"bev-retained-{root.name}-{role}", commit,
            {path.name: path}, role,
        ))[path.name]
    return fx


class TimedBackend(FakeFirebasePublicationBackend):
    def __init__(self, *args, release_time: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.release_time = release_time

    def release_version(self, target, version):
        value = dict(super().release_version(target, version))
        value["releaseTime"] = self.release_time
        return value


class BevBindingTests(unittest.TestCase):
    def test_literal_elite_matchup_and_canonical_public_bytes(self) -> None:
        bound, artifact = bind_bev(candidate())
        self.assertAlmostEqual(artifact.value, 98.6, places=1)
        self.assertEqual(artifact.market_basis, "missing")
        self.assertEqual(artifact.to_dict()["evidence"]["market_reason"], "market-qualification-unavailable")
        self.assertEqual(BevArtifact.from_bytes(artifact.to_bytes()), artifact)
        self.assertEqual(bound.bindings[-1].kind, BEV_BINDING_KIND)
        self.assertEqual(bound.bindings[-1].digest, artifact.byte_digest)

    def test_anchor_omits_only_own_identity_and_preserves_other_bindings_and_predecessor(self) -> None:
        original, original_bev = bind_bev(candidate("first"))
        correction = candidate("first", margin="1.5", predecessor=original.version_id)
        other = ForecastCandidate.create(
            game=correction.game, home_margin=correction.home_margin,
            precision=correction.precision, provenance=correction.provenance,
            predecessor_version_id=correction.predecessor_version_id,
            replacement_reason=correction.replacement_reason,
            bindings=(replace(original.bindings[0], kind="other-kind"),),
            selection=correction.selection,
        )
        corrected, corrected_bev = bind_bev(other)
        anchor = corrected_bev.forecast_anchor
        self.assertNotIn("version_id", anchor)
        self.assertEqual(anchor["bindings"], [other.bindings[0].to_dict()])
        self.assertEqual(anchor["replacement"]["predecessor_version_id"], original.version_id)
        self.assertEqual(bind_bev(original, original_bev)[0].version_id, original.version_id)
        self.assertNotEqual(corrected_bev.artifact_id, original_bev.artifact_id)

    def test_missing_ranks_are_unavailable_and_semantic_tampering_fails_after_reseal(self) -> None:
        bound, unavailable = bind_bev(candidate(home_rank=None))
        self.assertEqual((unavailable.status, unavailable.value), ("unavailable", None))
        self.assertEqual(unavailable.qualification_reasons, ("missing-home-rank",))
        tampered = unavailable.to_dict()
        tampered["forecast_anchor"]["forecast"]["home_margin"] = "3"
        tampered["forecast_anchor"]["forecast"]["home_handicap"] = "-3"
        tampered["forecast_anchor_digest"] = "sha256:" + hashlib.sha256(
            canonical(tampered["forecast_anchor"])
        ).hexdigest()
        forged = BevArtifact.from_bytes(reseal(tampered))
        with self.assertRaisesRegex(ExcitementForecastError, "does not match"):
            bind_bev(bound, forged)

        calculated, score = bind_bev(candidate("score"))
        changed = score.to_dict()
        changed["value"] = 12.0
        with self.assertRaisesRegex(ExcitementForecastError, "score or components"):
            BevArtifact.from_bytes(reseal(changed))
        changed = score.to_dict()
        changed["components"]["market_boost"] = False
        with self.assertRaisesRegex(ExcitementForecastError, "finite floats"):
            BevArtifact.from_bytes(reseal(changed))
        with self.assertRaisesRegex(ExcitementForecastError, "exact BEV artifact"):
            bind_bev(calculated)

    def test_reconstruction_binds_exact_inputs_without_claiming_issuance_time(self) -> None:
        item = candidate("history")
        timing = GameTimingEvidence(item.game, evidence("start"), actual_started_at=instant(12))
        qualified = qualification(item, inputs_as_of=instant(10))
        artifact = prepare_reconstructed_bev(
            item, reconstructed_at=instant(20), qualified_input=qualified, timing=timing,
        )
        self.assertEqual(artifact.forecast_state, "reconstructed")
        self.assertEqual(artifact.reconstruction.reconstructed_at, instant(20))
        self.assertEqual(artifact.reconstruction.qualified_input.inputs_as_of, instant(10))
        forged = artifact.to_dict()
        forged["reconstruction"]["qualified_input"]["forecast_provenance"][
            "rating_checkpoint"
        ] = "WEEK_99"
        with self.assertRaisesRegex(
            ExcitementForecastError, "does not match forecast provenance"
        ):
            BevArtifact.from_bytes(reseal(forged))
        forged = artifact.to_dict()
        wrong_digest = "sha256:" + "b" * 64
        forged["reconstruction"]["qualified_input"]["source"]["digest"] = wrong_digest
        forged["reconstruction"]["qualified_input"]["forecast_provenance"][
            "source_snapshot_digest"
        ] = wrong_digest
        resealed = reseal(forged)
        for parse in (BevArtifact.from_dict, lambda value: BevArtifact.from_bytes(resealed)):
            with self.subTest(parser=parse), self.assertRaisesRegex(
                ExcitementForecastError, "does not match forecast provenance"
            ):
                parse(forged)
        wrong_qualification = replace(
            artifact.reconstruction.qualified_input,
            source=EvidenceRef("synthetic-test", "wrong-source", wrong_digest),
            source_snapshot_digest=wrong_digest,
        )
        with self.assertRaisesRegex(
            ExcitementForecastError, "does not match forecast provenance"
        ):
            replace(
                artifact,
                reconstruction=replace(
                    artifact.reconstruction, qualified_input=wrong_qualification
                ),
                artifact_id="",
            )
        with self.assertRaisesRegex(ExcitementForecastError, "inputs are not proven pregame"):
            prepare_reconstructed_bev(
                item, reconstructed_at=instant(20),
                qualified_input=qualification(item, inputs_as_of=instant(13)), timing=timing,
            )

        for field, changed in (
            ("rating_checkpoint", "WEEK_1"),
            ("rating_cutoff", "through-week-1"),
            ("source_snapshot_digest", "sha256:" + "b" * 64),
        ):
            kwargs = {field: changed}
            if field == "source_snapshot_digest":
                kwargs["source"] = EvidenceRef("synthetic-test", "wrong-source", changed)
            with self.subTest(field=field), self.assertRaisesRegex(
                ExcitementForecastError, "does not match forecast provenance"
            ):
                prepare_reconstructed_bev(
                    item, reconstructed_at=instant(20),
                    qualified_input=qualification(item, inputs_as_of=instant(10), **kwargs),
                    timing=timing,
                )

        later = candidate("history", margin="1.5", predecessor=item.version_id)
        with self.assertRaisesRegex(ExcitementForecastError, "different forecast anchor"):
            prepare_reconstructed_bev(
                later, reconstructed_at=instant(20), qualified_input=qualified, timing=timing,
            )

    def test_authenticated_multi_game_package_proves_exact_issued_bev_bytes(self) -> None:
        first = bind_bev(candidate("A"))
        second = bind_bev(candidate("B", margin="0.02", home_rank=18, away_rank=22))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = publication_fixture(root, [first, second])
            _, _, run = publish(fx)
            timing = GameTimingEvidence(
                first[0].game, evidence("start"),
                actual_started_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
            issued = load_issued_bev(
                recorded(run), archive=fx.archive, repository="owner/repository",
                destination=root / "issued", timing=timing,
            )
            self.assertIsNotNone(issued)
            self.assertEqual(issued.candidate, first[0])
            self.assertEqual(issued.artifact_bytes, first[1].to_bytes())

    def test_authenticated_history_uses_distinct_receipts_and_rejects_overlay_only(self) -> None:
        original = bind_bev(candidate("history-chain"))
        correction = bind_bev(candidate(
            "history-chain", margin="1.5", predecessor=original[0].version_id
        ))
        timing = GameTimingEvidence(
            original[0].game, evidence("history-start"),
            actual_started_at=datetime(2026, 9, 2, tzinfo=UTC),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = FakeImmutableArchive()
            original_fx = publication_fixture(root / "original", [original], archive=archive)
            correction_fx = publication_fixture(
                root / "correction-overlay", [original, correction], archive=archive
            )
            original_backend = TimedBackend(
                TARGET, original_fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY, release_time="2026-09-01T10:00:00Z",
            )
            correction_backend = TimedBackend(
                TARGET, correction_fx.package.expected_predecessor,
                managed_identity=APP_IDENTITY, release_time="2026-09-01T11:00:00Z",
            )
            _, _, original_run = publish(original_fx, original_backend, name="history-original")
            _, _, correction_run = publish(
                correction_fx, correction_backend, name="history-correction"
            )
            correction_attempt = recorded(correction_run)
            self.assertIsNone(load_issued_bev_history(
                (correction_attempt,), archive=archive, repository="owner/repository",
                destination=root / "overlay-only", timing=timing,
            ))
            issued = load_issued_bev_history(
                (correction_attempt, recorded(original_run)),
                archive=archive, repository="owner/repository",
                destination=root / "history", timing=timing,
            )
            self.assertIsNotNone(issued)
            self.assertEqual(issued.candidate.version_id, correction[0].version_id)
            self.assertEqual(issued.receipt.public_by, datetime(2026, 9, 1, 11, tzinfo=UTC))

    def test_publication_history_has_an_explicit_attempt_limit(self) -> None:
        item = candidate("history-limit")
        timing = GameTimingEvidence(item.game, evidence("limit-start"), actual_started_at=instant(12))
        with self.assertRaisesRegex(ExcitementForecastError, "exceeds 256"):
            load_issued_bev_history(
                [None] * (MAX_BEV_PUBLICATION_HISTORY + 1),
                archive=None, repository="owner/repository",
                destination="unused", timing=timing,
            )
        with patch("cfb.excitement_forecast._load_bev_attempt", return_value=()) as loader:
            self.assertIsNone(load_issued_bev_history(
                [None] * MAX_BEV_PUBLICATION_HISTORY,
                archive=None, repository="owner/repository",
                destination="unused", timing=timing,
            ))
            self.assertEqual(loader.call_count, MAX_BEV_PUBLICATION_HISTORY)


if __name__ == "__main__":
    unittest.main()
