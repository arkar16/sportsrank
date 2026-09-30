"""Independent checks for the frozen comparable-game AEV* reference seam."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from cfb.excitement import QualifiedFinal, QualifiedPregame, TimelinePoint, TimelineQualificationError
from cfb.excitement_reference import (
    REFERENCE_ALGORITHM_VERSION,
    REFERENCE_FEATURE_VERSION,
    REFERENCE_SCHEMA_VERSION,
    SCORING_VERSION,
    ReferenceArtifactError,
    ReferenceGame,
    calculate_aev,
    dump_reference_artifact,
    estimate_aev,
    fit_reference_artifact,
    load_reference_artifact,
    reference_game_from_timeline,
    validate_reference_artifact,
)


def _pregame(cors_margin: float | None = 0.0) -> QualifiedPregame:
    return QualifiedPregame(1, 2, cors_margin, "forecast-1", "pregame-snapshot-1")


def _final(
    game_id: str,
    home_score: int,
    away_score: int,
    *,
    overtime: bool | None = False,
    quarter_scores: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None = None,
) -> QualifiedFinal:
    return QualifiedFinal(
        game_id,
        2024,
        home_score,
        away_score,
        "final-snapshot-1",
        overtime=overtime,
        quarter_scores=quarter_scores,
    )


def _reference(
    game_id: str,
    *,
    season: int = 2023,
    margin: int = 10,
    total: int = 30,
    drama: float = 30.0,
    overtime: bool = False,
    quarters: tuple[int, int, int] | None = None,
    snapshot: str = "snapshot-1",
) -> ReferenceGame:
    return ReferenceGame(
        game_id,
        season,
        snapshot,
        margin,
        total,
        overtime,
        drama,
        drama + (2 if overtime else 0),
        *(quarters or (None, None, None)),
    )


class ReferenceArtifactTests(unittest.TestCase):
    def test_fitting_is_canonical_and_row_order_independent(self):
        games = (
            _reference("b", season=2023, margin=6, total=28, drama=20),
            _reference("a", season=2022, margin=4, total=24, drama=10),
            _reference("c", season=2024, margin=8, total=32, drama=30, snapshot="snapshot-2"),
        )
        first = fit_reference_artifact(games, artifact_id="artifact-1")
        second = fit_reference_artifact(tuple(reversed(games)), artifact_id="artifact-1")
        self.assertEqual(first, second)
        self.assertEqual(tuple(game.game_id for game in first.games), ("a", "b", "c"))
        self.assertEqual(first.source_snapshot_ids, ("snapshot-1", "snapshot-2"))
        self.assertEqual(first.schema_version, REFERENCE_SCHEMA_VERSION)
        self.assertEqual(first.algorithm_version, REFERENCE_ALGORITHM_VERSION)
        validate_reference_artifact(first)

    def test_dump_load_round_trip_and_tampering_fail_closed(self):
        artifact = fit_reference_artifact((_reference("a"), _reference("b", margin=11, total=31)), artifact_id="artifact-1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            dump_reference_artifact(artifact, path)
            self.assertEqual(load_reference_artifact(path), artifact)
            self.assertEqual(load_reference_artifact(path, expected_checksum=artifact.checksum), artifact)
            with self.assertRaises(ReferenceArtifactError):
                load_reference_artifact(path, expected_checksum="0" * 64)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["games"][0]["drama_target"] = 91
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ReferenceArtifactError):
                load_reference_artifact(path)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ReferenceArtifactError):
                load_reference_artifact(path)

            duplicate = path.read_text(encoding="utf-8").replace('"unexpected": true', '"unexpected": true, "unexpected": false')
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaises(ReferenceArtifactError):
                load_reference_artifact(path)

            path.unlink()
            with self.assertRaises(ReferenceArtifactError):
                load_reference_artifact(path)

    def test_incompatible_schema_and_unsafe_rows_are_rejected(self):
        artifact = fit_reference_artifact((_reference("a"),), artifact_id="artifact-1")
        with self.assertRaises(ReferenceArtifactError):
            validate_reference_artifact(replace(artifact, schema_version=999))
        with self.assertRaises(ReferenceArtifactError):
            validate_reference_artifact(replace(artifact, algorithm_version="future-aev"))
        with self.assertRaises(ReferenceArtifactError):
            validate_reference_artifact(replace(artifact, scoring_version="future-scoring"))
        with self.assertRaises(ReferenceArtifactError):
            validate_reference_artifact(replace(artifact, feature_version="future-features"))
        with self.assertRaises(ReferenceArtifactError):
            _reference("bad\nrow")
        with self.assertRaises(ReferenceArtifactError):
            ReferenceGame("bad-target", 2024, "snapshot-1", 1, 2, False, 93, 93)

    def test_reference_game_derives_targets_from_a_validated_timeline(self):
        final = _final(
            "derived",
            14,
            7,
            quarter_scores=((7, 0), (7, 7), (14, 7)),
        )
        timeline = [
            TimelinePoint(0, 0, 0, 0),
            TimelinePoint(7, 0, 1, 15),
            TimelinePoint(7, 7, 2, 30),
            TimelinePoint(14, 7, 3, 45),
            TimelinePoint(14, 7, 4, 60),
        ]
        game = reference_game_from_timeline(_pregame(), final, timeline)
        self.assertEqual((game.absolute_final_margin, game.total_points), (7, 21))
        self.assertEqual((game.q1_winner_margin, game.q2_winner_margin, game.q3_winner_margin), (7, 0, 7))
        self.assertGreaterEqual(game.drama_target, 0)
        self.assertEqual(game.mixed_target, game.drama_target)

    def test_quarter_checkpoint_disagreement_is_not_accepted_as_reference_evidence(self):
        final = _final(
            "bad-quarter",
            14,
            7,
            quarter_scores=((6, 0), (7, 7), (14, 7)),
        )
        timeline = [
            TimelinePoint(0, 0, 0, 0),
            TimelinePoint(7, 0, 1, 15),
            TimelinePoint(7, 7, 2, 30),
            TimelinePoint(14, 7, 3, 45),
            TimelinePoint(14, 7, 4, 60),
        ]
        with self.assertRaises(TimelineQualificationError):
            reference_game_from_timeline(_pregame(), final, timeline)


class ComparableEstimateTests(unittest.TestCase):
    def test_known_status_uses_same_status_pool_and_excludes_target(self):
        games = (
            _reference("target", margin=10, total=30, drama=90),
            _reference("normal-a", margin=10, total=30, drama=20, season=2022),
            _reference("normal-b", margin=10, total=30, drama=40, season=2021),
            _reference("ot", margin=10, total=30, drama=80, overtime=True, season=2020),
        )
        artifact = fit_reference_artifact(games, artifact_id="artifact-1")
        result = estimate_aev(_pregame(), _final("target", 20, 10, overtime=False), artifact)
        self.assertEqual(result.estimated, True)
        self.assertEqual(result.evidence.reference_pool, "false")
        self.assertEqual(result.evidence.reference_support, 2)
        self.assertAlmostEqual(result.components.drama_points, 30.0)
        self.assertAlmostEqual(result.value, 30.0 + 3 * result.components.quality)

    def test_target_exclusion_preserves_frozen_unequal_feature_scales(self):
        games = tuple(
            _reference(str(value), margin=value, total=value, drama=30.0)
            for value in (10, 20, 30, 40, 100)
        )
        artifact = fit_reference_artifact(games, artifact_id="frozen-unequal")
        # Linear quartiles of [10, 20, 30, 40, 100] are 20 and 40.
        # Removing the target from neighbors must not refit to the IQR of 15.
        result = estimate_aev(_pregame(), _final("100", 100, 0), artifact)
        self.assertEqual(result.evidence.reference_scale, (20.0, 20.0))
        self.assertEqual(result.evidence.reference_support, 4)
        self.assertNotIn("100", result.evidence.reference_neighbor_ids)
        self.assertAlmostEqual(result.evidence.reference_cutoff_distance, (2 * (90 / 20) ** 2) ** 0.5)

    def test_unknown_overtime_uses_mixed_target_without_extra_ot_bonus(self):
        games = (
            _reference("normal", margin=10, total=30, drama=20),
            _reference("overtime", margin=10, total=30, drama=40, overtime=True),
        )
        artifact = fit_reference_artifact(games, artifact_id="artifact-1")
        result = estimate_aev(_pregame(), _final("new", 20, 10, overtime=None), artifact)
        # M averages 20 and 42.  The unknown branch adds only observed Q/U.
        self.assertEqual(result.evidence.reference_pool, "all")
        self.assertIsNone(result.components.overtime)
        self.assertAlmostEqual(result.components.drama_points, 31.0)
        self.assertAlmostEqual(result.value, 31.0 + 3 * result.components.quality)

    def test_absent_status_falls_back_to_all_status_pool(self):
        artifact = fit_reference_artifact(
            (_reference("normal", drama=20), _reference("normal-2", drama=40, season=2022)),
            artifact_id="artifact-1",
        )
        result = estimate_aev(_pregame(), _final("new", 20, 10, overtime=True), artifact)
        self.assertEqual(result.evidence.reference_pool, "all")
        self.assertEqual(result.evidence.reference_support, 2)
        self.assertAlmostEqual(result.components.drama_points, 30.0)
        self.assertAlmostEqual(result.value, 30.0 + 2 + 3 * result.components.quality)

    def test_quarter_tier_requires_all_three_checkpoints_and_ties_use_final_tier(self):
        games = (
            _reference("quarter-a", margin=7, total=21, drama=10, quarters=(7, 0, 7)),
            _reference("quarter-b", margin=7, total=21, drama=30, quarters=(0, 7, 7), season=2022),
            _reference("final-only", margin=7, total=21, drama=50, season=2021),
        )
        artifact = fit_reference_artifact(games, artifact_id="artifact-1")
        quarter_result = estimate_aev(
            _pregame(),
            _final("quarter-target", 14, 7, quarter_scores=((7, 0), (7, 7), (14, 7))),
            artifact,
        )
        self.assertEqual(quarter_result.evidence.evidence_tier, "quarter")
        self.assertEqual(quarter_result.evidence.reference_support, 2)
        tie_result = estimate_aev(
            _pregame(),
            _final("tie-target", 14, 14, quarter_scores=((7, 0), (7, 7), (14, 14))),
            artifact,
        )
        self.assertEqual(tie_result.evidence.evidence_tier, "final")

    def test_cutoff_distance_ties_are_all_included_and_target_is_excluded(self):
        games = [_reference("target", margin=10, total=30, drama=90)]
        games.extend(
            _reference(f"tie-{index:02d}", margin=10, total=30, drama=float(index))
            for index in range(51)
        )
        artifact = fit_reference_artifact(games, artifact_id="artifact-1")
        result = estimate_aev(_pregame(), _final("target", 20, 10), artifact)
        self.assertEqual(result.evidence.reference_support, 51)
        self.assertAlmostEqual(result.components.drama_points, sum(range(51)) / 51)

    def test_fewer_than_fifty_neighbors_and_empty_after_exclusion_are_visible(self):
        artifact = fit_reference_artifact((_reference("only", drama=25),), artifact_id="artifact-1")
        result = estimate_aev(_pregame(), _final("new", 20, 10), artifact)
        self.assertEqual(result.evidence.reference_support, 1)
        with self.assertRaises(ReferenceArtifactError):
            estimate_aev(_pregame(), _final("only", 20, 10), artifact)

    def test_calculate_aev_routes_missing_or_invalid_flow_to_estimate_and_requires_reference(self):
        artifact = fit_reference_artifact((_reference("ref", drama=25),), artifact_id="artifact-1")
        final = _final("new", 20, 10)
        estimated = calculate_aev(_pregame(), final, None, artifact)
        self.assertTrue(estimated.estimated)
        with self.assertRaises(ReferenceArtifactError):
            calculate_aev(_pregame(), final, None, None)
        with self.assertRaises(ReferenceArtifactError):
            calculate_aev(_pregame(), final, [TimelinePoint(0, 0, 0, 0)], None)


if __name__ == "__main__":
    unittest.main()
