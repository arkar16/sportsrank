"""Independent SR-33 checks for immutable BEV forecast bindings.

These tests deliberately calculate the numerical oracle from the accepted
ADR-0021 formula instead of importing the scorer's implementation.  The
fixtures are synthetic and never qualify a production market or publication.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cfb import excitement_forecast as sut
from cfb.github_archive import ArchiveSpec
from cfb.forecast_record import (
    EvidenceRef,
    ForecastCandidate,
    ForecastProvenance,
    GameTimingEvidence,
    GameIdentity,
    VersionBinding,
)
from cfb.publication_records import canonical_json
from cfb.publication import PreparedPackage, _deterministic_package, bind_merged_candidate
from tests.test_forecast_publication import forecast_fixture, publish, recorded
from tests.test_publication_execution import approval, inventory, validation


DIGEST = "sha256:" + ("a" * 64)


def _game(label: str = "A", *, home: str = "Home", away: str = "Away") -> GameIdentity:
    return GameIdentity(
        provider_id=f"fixture-{label}", season=2026, week=1,
        home_team=home, away_team=away,
        home_classification="fbs", away_classification="fbs",
        neutral_site=False,
    )


def _provenance(
    *, home_rank: int | None = 1, away_rank: int | None = 2,
    code_revision: str = "sr33-fixture",
) -> ForecastProvenance:
    return ForecastProvenance(
        rating_checkpoint="PRESEASON", rating_cutoff="before-week-0",
        rating_artifact_digest=DIGEST, source_snapshot_digest=DIGEST,
        model_version="cors-fixture", source_kind="synthetic",
        code_revision=code_revision, home_rating=Decimal("20.24"),
        away_rating=Decimal("20.00"), home_field_advantage=Decimal("2.00"),
        home_rank=home_rank, away_rank=away_rank,
    )


def _candidate(
    label: str = "A", margin: str = "2", *, home_rank: int | None = 1,
    away_rank: int | None = 2, predecessor: str | None = None,
    bindings: tuple[VersionBinding, ...] = (),
    home: str = "Home", away: str = "Away",
) -> ForecastCandidate:
    return ForecastCandidate.create(
        game=_game(label, home=home, away=away), home_margin=margin,
        precision=2 if "." in margin else 0,
        provenance=_provenance(home_rank=home_rank, away_rank=away_rank),
        predecessor_version_id=predecessor,
        replacement_reason="pregame correction" if predecessor else None,
        bindings=bindings,
    )


def _binding_result(value):
    """Normalize the result object without deriving values from the scorer."""
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], value[1]
    candidate = getattr(value, "candidate", None)
    artifact = getattr(value, "artifact", None)
    if candidate is not None and artifact is not None:
        return candidate, artifact
    raise AssertionError("bind_bev must return both the bound candidate and artifact")


def _artifact_score(artifact) -> Decimal:
    """Read the public score field from either its object or safe JSON form."""
    for name in ("score", "bev", "value"):
        value = getattr(artifact, name, None)
        if isinstance(value, (int, float, Decimal, str)):
            try:
                return Decimal(str(value))
            except Exception:
                pass
    data = artifact.to_dict()
    for name in ("score", "bev", "value"):
        if name in data:
            return Decimal(str(data[name]))
    raise AssertionError("BEV artifact has no public score field")


def _artifact_bytes(artifact) -> bytes:
    raw = artifact.to_bytes()
    if not isinstance(raw, bytes):
        raise AssertionError("BEV artifact serialization must return bytes")
    return raw


class IndependentBEVOracleTests(unittest.TestCase):
    def test_number_one_vs_two_by_two_is_98_6_and_zero_is_exact(self):
        bound, artifact = _binding_result(sut.bind_bev(_candidate(margin="2")))
        self.assertAlmostEqual(float(_artifact_score(artifact)), 98.6, places=1)
        self.assertEqual(bound.game, _candidate().game)
        self.assertEqual(artifact.market_basis, "missing")
        self.assertEqual(artifact.to_dict()["evidence"]["market_reason"], "market-qualification-unavailable")

        zero, zero_artifact = _binding_result(sut.bind_bev(_candidate("zero", margin="0")))
        self.assertAlmostEqual(float(_artifact_score(zero_artifact)), 99.4, places=1)
        self.assertEqual(zero.home_margin, Decimal("0"))

    def test_small_natural_margin_is_nonzero_and_missing_rank_is_unavailable(self):
        _, artifact = _binding_result(sut.bind_bev(_candidate("tiny", margin="0.01")))
        self.assertGreater(_artifact_score(artifact), Decimal("0"))

        missing, missing_artifact = _binding_result(
            sut.bind_bev(_candidate("missing-rank", home_rank=None, away_rank=2))
        )
        self.assertEqual(missing_artifact.status, "unavailable")
        self.assertIsNone(missing_artifact.value)
        self.assertEqual(missing_artifact.qualification_reasons, ("missing-home-rank",))

    def test_orientation_and_canonical_bytes_are_stable(self):
        first, first_artifact = _binding_result(sut.bind_bev(_candidate("orient", margin="2")))
        swapped = _candidate("orient-swapped", margin="-2", home_rank=2, away_rank=1,
                             home="Away", away="Home")
        second, second_artifact = _binding_result(sut.bind_bev(swapped))
        self.assertEqual(_artifact_score(first_artifact), _artifact_score(second_artifact))
        self.assertEqual(_artifact_bytes(second_artifact), sut.BevArtifact.from_bytes(_artifact_bytes(second_artifact)).to_bytes())
        self.assertEqual(first.game.provider_id, "fixture-orient")
        self.assertEqual(second.game.provider_id, "fixture-orient-swapped")


class IndependentBEVLifecycleTests(unittest.TestCase):
    def test_existing_binding_revalidates_and_later_information_does_not_rewrite_it(self):
        original, artifact = _binding_result(sut.bind_bev(_candidate("stable", margin="2")))
        original_bytes = _artifact_bytes(artifact)
        rebound, rebound_artifact = _binding_result(sut.bind_bev(original, artifact=artifact))
        self.assertEqual(rebound, original)
        self.assertEqual(_artifact_bytes(rebound_artifact), original_bytes)

    def test_pregame_correction_retains_predecessor_and_both_artifacts(self):
        prior, prior_artifact = _binding_result(sut.bind_bev(_candidate("correction", margin="2")))
        corrected = _candidate("correction", margin="1.5", predecessor=prior.version_id)
        current, current_artifact = _binding_result(sut.bind_bev(corrected))
        self.assertNotEqual(prior.version_id, current.version_id)
        self.assertEqual(current.predecessor_version_id, prior.version_id)
        self.assertNotEqual(_artifact_bytes(prior_artifact), _artifact_bytes(current_artifact))

    def test_anchor_preserves_other_bindings_and_predecessor(self):
        other = VersionBinding("other-artifact", "sha256:" + "b" * 64, DIGEST)
        predecessor = _candidate("anchor-prior", margin="2")
        candidate = _candidate("anchor", margin="2", predecessor=predecessor.version_id,
                              bindings=(other,))
        bound, _ = _binding_result(sut.bind_bev(candidate))
        self.assertEqual(bound.predecessor_version_id, predecessor.version_id)
        self.assertIn(other, bound.bindings)
        self.assertEqual(sum(binding.kind == sut.BEV_BINDING_KIND for binding in bound.bindings), 1)
        self.assertEqual(bound.game, candidate.game)
        self.assertEqual(bound.provenance, candidate.provenance)

    def test_resealed_semantic_tampering_is_rejected(self):
        candidate, artifact = _binding_result(sut.bind_bev(_candidate("tamper", margin="2")))
        data = json.loads(_artifact_bytes(artifact))
        for key in ("score", "bev", "value"):
            if key in data:
                data[key] = "0"
                break
        else:
            self.fail("fixture artifact has no score field to tamper")
        with self.assertRaises(Exception):
            resealed = sut.BevArtifact.from_bytes(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
            sut.bind_bev(candidate, artifact=resealed)

    def test_mutating_components_after_construction_is_rejected_at_binding(self):
        candidate, artifact = _binding_result(sut.bind_bev(_candidate("mutable", margin="2")))
        self.assertIsNotNone(artifact.components)
        artifact.components["quality"] = 0  # type: ignore[index]
        with self.assertRaises(Exception):
            sut.bind_bev(candidate, artifact=artifact)

    def test_unknown_state_and_malformed_missing_rank_anchor_are_rejected(self):
        candidate, artifact = _binding_result(sut.bind_bev(_candidate("shape", margin="2")))
        with self.assertRaises(Exception):
            replace(artifact, forecast_state="forged", artifact_id="")

        malformed = dict(artifact.forecast_anchor)
        malformed.pop("game")
        malformed_provenance = dict(malformed["provenance"])
        malformed_provenance.pop("home_rank")
        malformed["provenance"] = malformed_provenance
        malformed_digest = "sha256:" + __import__("hashlib").sha256(canonical_json(malformed)).hexdigest()
        with self.assertRaises(Exception):
            sut.BevArtifact(
                forecast_anchor=malformed,
                forecast_anchor_digest=malformed_digest,
                status="unavailable", value=None, components=None,
                market_basis="missing", qualification_reasons=("missing-home-rank",),
            )


class IndependentBEVReconstructionTests(unittest.TestCase):
    def _qualified_input(self, candidate, *, inputs_as_of=None, source=None, **overrides):
        _, artifact = _binding_result(sut.bind_bev(candidate))
        provenance = candidate.provenance.to_dict()
        provenance.update(overrides)
        return sut.ReconstructionInputQualification(
            inputs_as_of=inputs_as_of or datetime(2026, 9, 1, 17, tzinfo=timezone.utc),
            source=source or EvidenceRef("synthetic", "checkpoint", DIGEST),
            forecast_anchor_digest=artifact.forecast_anchor_digest,
            rating_checkpoint=provenance["rating_checkpoint"],
            rating_cutoff=provenance["rating_cutoff"],
            rating_artifact_digest=provenance["rating_artifact_digest"],
            source_snapshot_digest=provenance["source_snapshot_digest"],
            model_version=provenance["model_version"],
            source_kind=provenance["source_kind"],
            code_revision=provenance["code_revision"],
            home_rating=provenance["home_rating"],
            away_rating=provenance["away_rating"],
            home_field_advantage=provenance["home_field_advantage"],
            home_rank=provenance.get("home_rank"),
            away_rank=provenance.get("away_rank"),
        )

    def test_missing_publication_or_timing_cannot_upgrade_reconstruction(self):
        candidate = _candidate("reconstructed", margin="2")
        timing = GameTimingEvidence(
            candidate.game,
            EvidenceRef("synthetic", "kickoff", DIGEST),
            actual_started_at=datetime(2026, 9, 1, 18, tzinfo=timezone.utc),
        )
        qualified = self._qualified_input(candidate, inputs_as_of=datetime(2026, 9, 1, 17, tzinfo=timezone.utc))
        artifact = sut.prepare_reconstructed_bev(
            candidate,
            reconstructed_at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
            qualified_input=qualified,
            timing=timing,
        )
        self.assertEqual(artifact.forecast_state, "reconstructed")
        self.assertIsNotNone(artifact.reconstruction)
        self.assertEqual(artifact.reconstruction.reconstructed_at.hour, 20)
        self.assertEqual(artifact.reconstruction.qualified_input, qualified)
        with self.assertRaises(Exception):
            sut.prepare_reconstructed_bev(
                candidate,
                reconstructed_at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
                qualified_input=replace(
                    qualified,
                    inputs_as_of=datetime(2026, 9, 1, 19, tzinfo=timezone.utc),
                ),
                timing=timing,
            )

    def test_reconstruction_rejects_qualification_provenance_mismatches(self):
        candidate = _candidate("qualified-mismatch", margin="2")
        timing = GameTimingEvidence(
            candidate.game, EvidenceRef("synthetic", "kickoff", DIGEST),
            actual_started_at=datetime(2026, 9, 1, 18, tzinfo=timezone.utc),
        )
        qualified = self._qualified_input(candidate)
        variants = (
            ("source_snapshot_digest", {"source_snapshot_digest": "sha256:" + "b" * 64}),
            ("rating_checkpoint", {"rating_checkpoint": "WEEK_1"}),
            ("rating_cutoff", {"rating_cutoff": "after-week-1"}),
        )
        for label, changes in variants:
            with self.subTest(field=label), self.assertRaises(Exception):
                sut.prepare_reconstructed_bev(
                    candidate,
                    reconstructed_at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
                    qualified_input=replace(qualified, **changes),
                    timing=timing,
                )

    def test_qualification_for_older_inputs_cannot_be_reused_for_later_candidate(self):
        older = _candidate("qualification-chain", margin="2")
        qualified = self._qualified_input(older)
        later = _candidate("qualification-chain", margin="1.5")
        timing = GameTimingEvidence(
            later.game, EvidenceRef("synthetic", "kickoff", DIGEST),
            actual_started_at=datetime(2026, 9, 1, 18, tzinfo=timezone.utc),
        )
        with self.assertRaises(Exception):
            sut.prepare_reconstructed_bev(
                later,
                reconstructed_at=datetime(2026, 9, 1, 20, tzinfo=timezone.utc),
                qualified_input=qualified,
                timing=timing,
            )

    def test_unverified_recorded_attempt_cannot_claim_issued_bev(self):
        candidate, artifact = _binding_result(sut.bind_bev(_candidate("unissued", margin="2")))
        self.assertTrue(candidate.version_id)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                sut.load_issued_bev(
                    None, archive=None, repository="owner/repo",
                    destination=Path(directory), timing=None,
                )

    def test_bare_issued_container_cannot_create_an_issuance_capability(self):
        candidate, artifact = _binding_result(sut.bind_bev(_candidate("bare-issued", margin="2")))
        with self.assertRaises(Exception):
            sut.IssuedBev(candidate, artifact, _artifact_bytes(artifact), None, None)


class IndependentBEVPublicationTests(unittest.TestCase):
    def test_authenticated_multigame_package_returns_exact_selected_bev_bytes(self):
        first, first_artifact = _binding_result(sut.bind_bev(_candidate("published-a", margin="2")))
        second, second_artifact = _binding_result(sut.bind_bev(_candidate("published-b", margin="0.01")))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fx = forecast_fixture(root / "publication", [first, second])
            for item, artifact in ((first, first_artifact), (second, second_artifact)):
                path = fx.prepared.site / sut.bev_relative_path(item, artifact.artifact_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_artifact_bytes(artifact))

            repository = root / "publication" / "repository"
            subprocess.run(["git", "-C", str(repository), "add", "website"], check=True,
                           capture_output=True)
            subprocess.run([
                "git", "-C", str(repository), "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid", "commit", "-qm",
                "BEV package fixture",
            ], check=True, capture_output=True)
            commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"]).decode().strip()
            files = {
                f"website/{path.relative_to(fx.prepared.site).as_posix()}": path.read_bytes()
                for path in fx.prepared.site.rglob("*") if path.is_file()
            }
            files["firebase.json"] = fx.prepared.firebase_json.read_bytes()
            inventory_sha = inventory(files)
            configuration_sha = hashlib.sha256(files["firebase.json"]).hexdigest()
            archive_path = root / "publication" / "bev-package.tar.gz"
            bundle_sha = _deterministic_package(fx.prepared.site, fx.prepared.firebase_json, archive_path)
            fx.prepared = PreparedPackage._create(
                archive_path, bundle_sha, inventory_sha, configuration_sha,
                fx.baseline.digest, fx.package.expected_predecessor, "6" * 64,
                validation(inventory_sha, configuration_sha, fx.baseline.digest, "6" * 64),
                fx.prepared.site, fx.prepared.firebase_json,
            )
            fx.package = bind_merged_candidate(fx.prepared, candidate_commit=commit, reader=fx.reader)
            fx.evidence = {}
            for role in ("baseline", "source_inputs", "original_prepared"):
                path = root / "publication" / f"{role}-bev.tar.gz"
                path.write_bytes(role.encode())
                fx.evidence[role] = fx.archive.seal_or_reconcile(ArchiveSpec(
                    "owner/repository", f"retained-bev-{role}", commit,
                    {path.name: path}, role,
                ))[path.name]
            fx.approval, fx.runtime = approval(commit)
            _, _, run = publish(fx)
            timing = GameTimingEvidence(
                first.game, EvidenceRef("synthetic", "kickoff", DIGEST),
                actual_started_at=datetime(2026, 9, 15, 18, tzinfo=timezone.utc),
            )
            issued = sut.load_issued_bev(
                recorded(run), archive=fx.archive, repository="owner/repository",
                destination=root / "publication" / "import", timing=timing,
            )
            self.assertIsNotNone(issued)
            assert issued is not None
            self.assertEqual(issued.candidate.version_id, first.version_id)
            self.assertEqual(issued.artifact_bytes, _artifact_bytes(first_artifact))
            self.assertEqual(issued.artifact, sut.BevArtifact.from_bytes(issued.artifact_bytes))


if __name__ == "__main__":
    unittest.main()
