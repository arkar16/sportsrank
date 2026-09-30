"""Release-boundary checks for preserved forecast artifacts."""

from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from cfb.forecast_publication import VerifiedForecastPublication
from cfb.forecast_record import (
    EvidenceRef, FinalScore, ForecastCandidate, ForecastContractError,
    ForecastProvenance, GameIdentity,
    GameTimingEvidence, PublicationReceipt, ScoreRevision,
)
from cfb.forecast_release import (
    ForecastSourceCheckpoint, build_forecast_artifacts, canonical_json,
    validate_evaluation_semantics, validate_forecast_capabilities,
    validate_forecast_sources,
    validate_public_forecast_json,
)
from cfb.ranking_engine import PreviousFinal, preseason_ranking
from cfb.release import build_release, validate_release
from cfb.season_snapshot import SeasonSnapshot, _checksum
from cfb.season_source import SourceGame, SourceTeam


UTC = timezone.utc
DIGEST = "sha256:" + "b" * 64


def at(hour: int) -> datetime:
    return datetime(2026, 9, 1, hour, tzinfo=UTC)


def source(label: str) -> EvidenceRef:
    return EvidenceRef("synthetic-test", label, DIGEST)


def identity() -> GameIdentity:
    return GameIdentity("g-final", 2026, 0, "Home", "Away", "fbs", "fbs", False)


def candidate(margin: str = "2.24") -> ForecastCandidate:
    return ForecastCandidate.create(
        game=identity(), home_margin=margin, precision=2,
        provenance=ForecastProvenance(
            "PRESEASON", "before-week-0", DIGEST, DIGEST, "v0.4.0",
            "season-snapshot", "revision", Decimal("20.24"), Decimal("20"),
            Decimal("2"), 1, 2,
        ),
    )


def publication(item: ForecastCandidate) -> VerifiedForecastPublication:
    receipt = PublicationReceipt(
        "receipt", item.version_id, item.artifact_digest, DIGEST, "attempt",
        "verification", source("publication"), provider_published_at=at(10),
    )
    reference = {
        "schema_version": 1, "record_type": "archive_reference",
        "repository": "owner/repo", "release_id": "1", "tag": "evidence",
        "target_commit": "a" * 40, "asset_id": "1", "asset_name": "evidence.json",
        "sha256": "b" * 64, "size": 1, "immutable": True,
    }
    provenance = {
        "schema_version": "forecast-publication-evidence/v1", "repository": "owner/repo",
        "intent": reference, "package": reference, "provider_result": reference,
        "provider_source": reference, "verification": reference,
        "verification_source": reference,
    }
    # Production callers can only receive this value through immutable archive
    # reconstruction. This fixture bypasses construction solely after building
    # independently hand-authored candidate/receipt facts.
    result = object.__new__(VerifiedForecastPublication)
    object.__setattr__(result, "candidates", (item,))
    object.__setattr__(result, "receipts", (receipt,))
    object.__setattr__(result, "_provenance_json", json.dumps(provenance).encode())
    return result


def snapshot(home_points=22, away_points=20) -> SeasonSnapshot:
    teams = (SourceTeam("Home", "X"), SourceTeam("Away", "X"))
    games = (
        SourceGame(0, "Home", "fbs", home_points, "Away", "fbs", away_points, False, provider_id="g-final", completed=True),
        SourceGame(1, "Away", "fbs", None, "Home", "fbs", None, False, provider_id="g-next"),
    )
    state = {
        "schema_version": 2, "sport": "cfb", "classification": "FBS", "year": 2026,
        "teams_fetched_at": None, "games_fetched_at": None,
        "complete_through_week": 0 if home_points is not None and away_points is not None else -1,
        "calendar_provenance": None,
        "correction_registry_provenance": None, "migration_provenance": None,
    }
    return SeasonSnapshot(
        "cfb", "FBS", 2026,
        teams, games, MappingProxyType(state), _checksum(state, teams, games),
    )


class ForecastArtifactTests(unittest.TestCase):
    def test_verified_issued_forecast_is_graded_and_correction_preserves_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger_path = Path(temporary) / "ledger.json"
            item = candidate()
            cap = publication(item)
            timing = GameTimingEvidence(item.game, source("start"), actual_started_at=at(12))
            ledger, evaluation = build_forecast_artifacts(
                snapshot(), inherited_ledger=ledger_path, generated_candidates=(),
                forecast_publications=(cap,), timing_evidence=(timing,),
                observed_at="2026-09-01T20:00:00+00:00",
            )
            self.assertEqual(evaluation["season_summary"]["mae"], "0.24")
            self.assertEqual(evaluation["games"][0]["grade"]["coverage"], "no_cover")
            ledger_path.write_bytes(canonical_json(ledger))
            corrected_ledger, corrected = build_forecast_artifacts(
                snapshot(17, 20), inherited_ledger=ledger_path,
                generated_candidates=(candidate("9.00"),),
                forecast_publications=(cap,), timing_evidence=(timing,),
                observed_at="2026-09-02T20:00:00+00:00",
            )
            self.assertEqual(len(corrected_ledger["candidates"]), 1)
            self.assertEqual(len(corrected_ledger["score_history"][0]["revisions"]), 2)
            self.assertEqual(corrected["games"][0]["grade"]["forecast_version_id"], item.version_id)
            self.assertEqual(corrected["games"][0]["grade"]["absolute_error"], "5.24")
            validate_evaluation_semantics(corrected_ledger, corrected)

            validate_forecast_capabilities(ledger, (cap,))
            without_provenance = {**ledger, "publication_provenance": []}
            with self.assertRaisesRegex(Exception, "publication provenance"):
                validate_forecast_capabilities(without_provenance, (cap,))

    def test_missing_evidence_is_visible_and_candidate_is_not_issued(self):
        item = candidate()
        ledger, evaluation = build_forecast_artifacts(
            snapshot(), inherited_ledger=Path("/nonexistent/ledger.json"),
            generated_candidates=(item,), forecast_publications=(), timing_evidence=(),
            observed_at="2026-09-01T20:00:00+00:00",
        )
        self.assertEqual(evaluation["omission_counts"]["unverified_publication"], 1)
        self.assertEqual(evaluation["season_summary"]["margin_count"], 0)
        self.assertEqual(ledger["receipts"], [])

        preserved = FinalScore.from_dict(ledger["score_history"][0])
        current = preserved.current
        forged_score = FinalScore(
            preserved.game,
            (ScoreRevision(
                99,
                current.away_points,
                current.observed_at,
                current.source,
            ),),
        )
        forged_ledger = {**ledger, "score_history": [forged_score.to_dict()]}
        with self.assertRaisesRegex(Exception, "current final score"):
            validate_evaluation_semantics(forged_ledger, evaluation, snapshot())

    def test_public_schema_rejects_source_shaped_or_extra_fields(self):
        item = candidate().to_dict()
        self.assertTrue(validate_public_forecast_json(item))
        self.assertFalse(validate_public_forecast_json({**item, "games": [{"raw": True}]}))

    def test_source_checkpoint_rejects_rehashed_rank_tampering(self):
        snap = snapshot(None, None)
        prior = PreviousFinal({"Home": 20.24, "Away": 20.0}, {})
        rows = tuple(preseason_ranking(snap, prior))
        rating_digest = "sha256:" + hashlib.sha256(canonical_json(list(rows))).hexdigest()
        snapshot_digest = "sha256:" + snap.checksum.removeprefix("sha256:")
        original = ForecastCandidate.create(
            game=identity(), home_margin="2.24", precision=2,
            provenance=ForecastProvenance(
                "PRESEASON", "before-week-0", rating_digest, snapshot_digest,
                "v0.4.0", "season-snapshot", "revision",
                Decimal("20.24"), Decimal("20"), Decimal("2"), 1, 2,
            ),
        )
        source_checkpoint = ForecastSourceCheckpoint(
            snapshot_digest, "PRESEASON", "before-week-0", rows,
            (identity(),), "v0.4.0", "revision", Decimal("2"),
        )
        validate_forecast_sources({"candidates": [original.to_dict()]}, (source_checkpoint,))
        validate_forecast_sources(
            {"candidates": [original.to_dict()]},
            (source_checkpoint, source_checkpoint),
        )
        conflicting_checkpoint = replace(
            source_checkpoint,
            rating_rows=(
                {**rows[0], "cors": float(rows[0]["cors"]) + 1},
                *rows[1:],
            ),
        )
        with self.assertRaisesRegex(ForecastContractError, "header is ambiguous"):
            validate_forecast_sources(
                {"candidates": [original.to_dict()]},
                (source_checkpoint, conflicting_checkpoint),
            )
        forged = ForecastCandidate.create(
            game=original.game, home_margin=original.home_margin, precision=original.precision,
            provenance=replace(original.provenance, home_rank=2),
        )
        with self.assertRaisesRegex(Exception, "ratings, ranks"):
            validate_forecast_sources({"candidates": [forged.to_dict()]}, (source_checkpoint,))


class ForecastReleaseIntegrationTests(unittest.TestCase):
    @staticmethod
    def prepare_base(base: Path) -> None:
        base.mkdir()
        (base / "index.html").write_text("preserved", encoding="utf-8")
        path = base / "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html"
        path.parent.mkdir(parents=True)
        path.write_text("<table><thead><tr><th>school</th><th>cors</th><th>wins_vs_expected</th></tr></thead><tbody><tr><td>Home</td><td>20.24</td><td>0</td></tr><tr><td>Away</td><td>20</td><td>0</td></tr></tbody></table>")

    def test_2026_release_emits_contract_three_natural_candidate_and_honest_omission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            self.prepare_base(base)
            prior = PreviousFinal({"Home": 20.24, "Away": 20.0}, {"Home": 0.0, "Away": 0.0})
            release = build_release(
                snapshot(), root / "candidate", release_id="candidate", target_week=0,
                phase="week", previous_final=prior, timestamp="2026-09-01T20:00:00+00:00",
                code_revision="revision", published_site=base,
            )
            manifest = json.loads(release.manifest_path.read_text())
            self.assertEqual((manifest["manifest_version"], manifest["artifact_contract"]), (3, 3))
            ledger = json.loads((release.site / manifest["forecast_ledger_path"]).read_text())
            self.assertEqual(len(ledger["candidates"]), 1)
            forecast = ledger["candidates"][0]["forecast"]
            self.assertEqual(forecast["precision"], 2)
            self.assertEqual(Decimal(forecast["home_handicap"]), -Decimal(forecast["home_margin"]))
            evaluation = json.loads((release.site / manifest["forecast_evaluation_path"]).read_text())
            self.assertEqual(evaluation["omission_counts"]["missing_forecast"], 1)
            page = (release.site / "cfb/years/2026/spread/2026_W0_FBS_spread_results.html").read_text()
            self.assertIn("Graded Forecast", page)
            self.assertIn("CORS line coverage", page)
            self.assertNotIn("ats_result", page)
            report = validate_release(release, published_site=base)
            self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_same_checkpoint_explicit_corrections_preserve_revision_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            self.prepare_base(base)
            prior = PreviousFinal({"Home": 20.24, "Away": 20.0}, {})
            probe = build_release(
                snapshot(), root / "probe", release_id="probe", target_week=0,
                phase="week", previous_final=prior,
                timestamp="2026-09-01T20:00:00+00:00",
                code_revision="same-revision", published_site=base,
            )
            probe_ledger = json.loads(
                (probe.site / "cfb/years/2026/forecasts/ledger.json").read_text()
            )
            active = next(
                ForecastCandidate.from_dict(value)
                for value in probe_ledger["candidates"]
                if value["game"]["week"] == 1
            )
            capability = publication(active)
            timing = GameTimingEvidence(
                active.game, source("actual-start"), actual_started_at=at(12)
            )
            issued = build_release(
                snapshot(), root / "issued", release_id="issued", target_week=0,
                phase="week", previous_final=prior,
                timestamp="2026-09-01T20:00:00+00:00",
                code_revision="same-revision", published_site=base,
                forecast_publications=(capability,), timing_evidence=(timing,),
            )
            for correction_revision in ("same-revision", "corrected-revision"):
                with self.subTest(code_revision=correction_revision):
                    corrected = build_release(
                        snapshot(), root / f"corrected-{correction_revision}",
                        release_id=f"corrected-{correction_revision}",
                        target_week=0, phase="week", previous_final=prior,
                        timestamp="2026-09-01T21:00:00+00:00",
                        code_revision=correction_revision,
                        published_site=issued.site,
                        forecast_publications=(capability,),
                        timing_evidence=(timing,),
                        forecast_replacements={
                            active.version_id: "explicit correction"
                        },
                    )
                    corrected_manifest = json.loads(
                        corrected.manifest_path.read_text()
                    )
                    corrected_ledger = json.loads(
                        (
                            corrected.site
                            / "cfb/years/2026/forecasts/ledger.json"
                        ).read_text()
                    )
                    corrected_candidates = tuple(
                        ForecastCandidate.from_dict(value)
                        for value in corrected_ledger["candidates"]
                    )
                    child = next(
                        value for value in corrected_candidates
                        if value.predecessor_version_id == active.version_id
                    )
                    self.assertEqual(
                        child.provenance.code_revision, correction_revision
                    )
                    self.assertEqual(
                        corrected_manifest["runs"][-1]["code_revision"],
                        correction_revision,
                    )
                    report = validate_release(
                        corrected,
                        published_site=issued.site,
                        forecast_publications=(capability,),
                        timing_evidence=(timing,),
                    )
                    self.assertTrue(
                        report.valid, [str(item) for item in report.failures]
                    )

    def test_final_run_owns_rewritten_preseason_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"
            self.prepare_base(base)
            prior = PreviousFinal({"Home": 20.24, "Away": 20.0}, {})
            numbered = build_release(
                snapshot(), root / "numbered", release_id="numbered",
                target_week=0, phase="week", previous_final=prior,
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            teams = (SourceTeam("Home", "X"), SourceTeam("Away", "X"))
            games = (
                SourceGame(0, "Home", "fbs", 22, "Away", "fbs", 20, False, provider_id="g-final", completed=True),
                SourceGame(1, "Away", "fbs", 17, "Home", "fbs", 14, False, provider_id="g-next", completed=True),
            )
            state = {
                "schema_version": 2, "sport": "cfb", "classification": "FBS",
                "year": 2026, "teams_fetched_at": None, "games_fetched_at": None,
                "complete_through_week": 1, "calendar_provenance": None,
                "correction_registry_provenance": None, "migration_provenance": None,
            }
            final_snapshot = SeasonSnapshot(
                "cfb", "FBS", 2026, teams, games, MappingProxyType(state),
                _checksum(state, teams, games),
            )
            final = build_release(
                final_snapshot, root / "final", release_id="final",
                target_week=1, phase="final", previous_final=prior,
                timestamp="2026-09-02T20:00:00+00:00",
                published_site=numbered.site,
            )
            preseason = (
                final.site
                / "cfb/years/2026/rankings/2026_PRESEASON_FBS_cors.html"
            ).read_text()
            self.assertIn("Last updated: 2026-09-02T20:00:00+00:00", preseason)
            report = validate_release(final, published_site=base)
            self.assertTrue(report.valid, [str(item) for item in report.failures])

    def test_manifest_downgrade_cannot_remove_forecast_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "published"; self.prepare_base(base)
            release = build_release(
                snapshot(), root / "candidate", release_id="candidate", target_week=0,
                phase="week", previous_final=PreviousFinal({"Home": 20.24, "Away": 20.0}, {}),
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            manifest = json.loads(release.manifest_path.read_text())
            manifest["manifest_version"] = 2
            manifest.pop("artifact_contract")
            manifest.pop("forecast_contract")
            manifest.pop("forecast_ledger_path")
            manifest.pop("forecast_evaluation_path")
            manifest.pop("manifest_checksum")
            manifest["manifest_checksum"] = hashlib.sha256((json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()).hexdigest()
            release.manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
            report = validate_release(release, published_site=base)
            self.assertFalse(report.valid)
            self.assertTrue({"metadata.field", "runs.missing", "artifact.contract", "forecast.contract"} & {item.code for item in report.failures})

    def test_resealed_display_tampering_is_rejected_semantically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); base = root / "published"; self.prepare_base(base)
            release = build_release(
                snapshot(), root / "candidate", release_id="candidate", target_week=0,
                phase="week", previous_final=PreviousFinal({"Home": 20.24, "Away": 20.0}, {}),
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            relative = "cfb/years/2026/spread/2026_W1_FBS_spread.html"
            page = release.site / relative
            original = page.read_text(encoding="utf-8")
            ledger = json.loads((release.site / "cfb/years/2026/forecasts/ledger.json").read_text())
            margin = ledger["candidates"][0]["forecast"]["home_margin"]
            needle = f"<td>{margin}</td>"
            self.assertIn(needle, original)
            page.write_text(original.replace(needle, "<td>9.99</td>", 1), encoding="utf-8")
            manifest = json.loads(release.manifest_path.read_text())
            manifest["artifact_checksums"][relative] = hashlib.sha256(page.read_bytes()).hexdigest()
            manifest.pop("manifest_checksum")
            manifest["manifest_checksum"] = hashlib.sha256(
                (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            ).hexdigest()
            release.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            report = validate_release(release, published_site=base)
            self.assertFalse(report.valid)
            self.assertIn("forecast.display", {failure.code for failure in report.failures})

    def test_resealed_result_summary_tampering_is_rejected_semantically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); base = root / "published"; self.prepare_base(base)
            release = build_release(
                snapshot(), root / "candidate", release_id="candidate", target_week=0,
                phase="week", previous_final=PreviousFinal({"Home": 20.24, "Away": 20.0}, {}),
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            relative = "cfb/years/2026/spread/2026_FBS_forecast_results.html"
            page = release.site / relative
            original = page.read_text(encoding="utf-8")
            self.assertIn("Missing forecast", original)
            page.write_text(original.replace("Missing forecast", "Evaluated", 1), encoding="utf-8")
            manifest = json.loads(release.manifest_path.read_text())
            manifest["artifact_checksums"][relative] = hashlib.sha256(page.read_bytes()).hexdigest()
            manifest.pop("manifest_checksum")
            manifest["manifest_checksum"] = hashlib.sha256(
                (json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            ).hexdigest()
            release.manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            report = validate_release(release, published_site=base)
            self.assertFalse(report.valid)
            self.assertIn("forecast.report", {failure.code for failure in report.failures})

    def test_missing_bound_inherited_ledger_fails_instead_of_resetting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); base = root / "published"; self.prepare_base(base)
            first = build_release(
                snapshot(), root / "first", release_id="first", target_week=0,
                phase="week", previous_final=PreviousFinal({"Home": 20.24, "Away": 20.0}, {}),
                timestamp="2026-09-01T20:00:00+00:00", published_site=base,
            )
            (first.site / "cfb/years/2026/forecasts/ledger.json").unlink()
            with self.assertRaisesRegex(ValueError, "missing its bound ledger"):
                build_release(
                    snapshot(), root / "second", release_id="second", target_week=0,
                    phase="week", previous_final=PreviousFinal({"Home": 20.24, "Away": 20.0}, {}),
                    timestamp="2026-09-01T21:00:00+00:00", published_site=first.site,
                )

    def test_path_validation_requires_reconstructed_capability_for_issued_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); base = root / "published"; self.prepare_base(base)
            prior = PreviousFinal({"Home": 20.24, "Away": 20.0}, {})
            issued_release = build_release(
                snapshot(None, None), root / "issued", release_id="issued",
                phase="preseason", previous_final=prior,
                timestamp="2026-09-01T09:00:00+00:00", published_site=base,
            )
            issued_ledger = json.loads(
                (issued_release.site / "cfb/years/2026/forecasts/ledger.json").read_text()
            )
            item = ForecastCandidate.from_dict(issued_ledger["candidates"][0])
            cap = publication(item)
            timing = GameTimingEvidence(item.game, source("actual-start"), actual_started_at=at(12))
            release = build_release(
                snapshot(), root / "candidate", release_id="candidate", target_week=0,
                phase="week", previous_final=prior,
                timestamp="2026-09-01T20:00:00+00:00", published_site=issued_release.site,
                forecast_publications=(cap,), timing_evidence=(timing,),
            )
            evaluation = json.loads((release.site / "cfb/years/2026/forecasts/evaluation.json").read_text())
            self.assertEqual(evaluation["season_summary"]["margin_count"], 1)
            report = validate_release(release, published_site=issued_release.site)
            self.assertTrue(report.valid, [str(failure) for failure in report.failures])
            missing = validate_release(release.site, published_site=issued_release.site)
            self.assertFalse(missing.valid)
            self.assertIn("forecast.contract", {failure.code for failure in missing.failures})
            explicit = validate_release(
                release.site, published_site=issued_release.site,
                forecast_publications=(cap,), timing_evidence=(timing,),
            )
            self.assertTrue(explicit.valid, [str(failure) for failure in explicit.failures])


if __name__ == "__main__":
    unittest.main()
