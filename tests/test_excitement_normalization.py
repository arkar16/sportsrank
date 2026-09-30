from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest

from cfb.excitement_normalization import (
    CaptureQualification,
    InvalidEvidenceError,
    QualificationArtifact,
    QualificationInference,
    QualificationReason,
    RetainedCapture,
    SCORE_TRAJECTORY_POLICY_ID,
    SCORE_TRAJECTORY_POLICY_SHA256,
    SCORE_TRAJECTORY_POLICY_VERSION,
    TargetGame,
    qualify_excitement,
)
from cfb.excitement_source import PilotRequest, SupplementReceipt
from cfb.private_inputs import InputReference
from cfb.public_safety import assert_public_bytes


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _request(endpoint: str) -> PilotRequest:
    params = (
        (("classification", "fbs"), ("id", 123), ("seasonType", "regular"), ("year", 2025))
        if endpoint == "/games"
        else (("classification", "fbs"), ("seasonType", "regular"), ("team", "Ohio State"), ("week", 1), ("year", 2025))
    )
    return PilotRequest(
        request_id=endpoint[1:] + "-123",
        endpoint=endpoint,
        params=params,
        expected_game_id="123",
        parent_snapshot_checksum=DIGEST_A,
        parent_snapshot_path="snapshots/cfb-fbs-2025.json",
        parent_snapshot_sha256=DIGEST_B,
    )


def _capture(endpoint: str, value: object, *, manifest: str = DIGEST_C) -> RetainedCapture:
    raw = _bytes(value)
    request = _request(endpoint)
    receipt = SupplementReceipt(
        request_id=request.request_id,
        endpoint=endpoint,
        params=request.params,
        response=InputReference("synthetic." + request.request_id, hashlib.sha256(raw).hexdigest(), len(raw)),
        captured_at="2026-09-30T00:00:00+00:00",
        pilot_id="synthetic-plan",
        manifest_sha256=manifest,
        allowance_id="synthetic-allowance",
        source_archive_sha256=DIGEST_C,
        parent_snapshot_path=request.parent_snapshot_path,
        parent_snapshot_sha256=request.parent_snapshot_sha256,
        parent_snapshot_checksum=request.parent_snapshot_checksum,
    )
    return RetainedCapture(request, receipt, raw)


def _game(**changes):
    row = {
        "id": 123,
        "season": 2025,
        "week": 1,
        "seasonType": "regular",
        "completed": True,
        "homeTeam": "Ohio State",
        "awayTeam": "Away",
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homePoints": 14,
        "awayPoints": 7,
        "homeLineScores": [0, 7, 0, 7],
        "awayLineScores": [0, 0, 0, 7],
    }
    row.update(changes)
    return row


def _play(drive, period, remaining, home_score, away_score, *, scoring=False, play=1):
    offense_home = drive % 2 == 1
    return {
        "id": f"p-{drive}-{play}", "driveId": f"d-{drive}", "gameId": 123,
        "driveNumber": drive, "playNumber": play, "period": period,
        "clock": {"minutes": remaining // 60, "seconds": remaining % 60},
        "home": "Ohio State", "away": "Away",
        "offense": "Ohio State" if offense_home else "Away",
        "defense": "Away" if offense_home else "Ohio State",
        "offenseScore": home_score if offense_home else away_score,
        "defenseScore": away_score if offense_home else home_score,
        "scoring": scoring,
    }


def _plays():
    # Response order is intentionally reversed and later quarter starts are
    # sparse.  The prior period-end state supplies the justified carry.
    ordered = [
        _play(1, 1, 900, 0, 0), _play(2, 1, 0, 0, 0),
        _play(3, 2, 600, 7, 0, scoring=True), _play(4, 2, 0, 7, 0),
        _play(5, 3, 300, 7, 0), _play(6, 3, 0, 7, 0),
        _play(7, 4, 300, 7, 7, scoring=True),
        _play(8, 4, 300, 14, 7, scoring=True), _play(9, 4, 0, 14, 7),
    ]
    return list(reversed(ordered))


def _target(**changes) -> TargetGame:
    values = {
        "game_id": "123", "season": 2025, "season_type": "regular", "provider_week": 1,
        "home_team": "Ohio State", "away_team": "Away", "completion": "completed",
        "game_format": "normal", "qualification_id": "target-review-v1",
        "games_manifest_sha256": DIGEST_C, "source_archive_sha256": DIGEST_C,
        "parent_snapshot_path": "snapshots/cfb-fbs-2025.json",
        "parent_snapshot_sha256": DIGEST_B, "parent_snapshot_checksum": DIGEST_A,
    }
    values.update(changes)
    return TargetGame(**values)


def _qualification(plays: RetainedCapture, **changes) -> CaptureQualification:
    values = {
        "game_id": "123", "plays_response_sha256": plays.receipt.response.sha256,
        "qualification_id": "capture-review-v1", "policy_id": SCORE_TRAJECTORY_POLICY_ID,
        "policy_version": SCORE_TRAJECTORY_POLICY_VERSION,
        "policy_sha256": SCORE_TRAJECTORY_POLICY_SHA256,
        "evidence_reference": "sr35-evidence-123",
        "plays_manifest_sha256": DIGEST_C, "source_archive_sha256": DIGEST_C,
        "parent_snapshot_sha256": DIGEST_B, "score_semantics": "after_play",
        "capture_completeness": "complete", "regulation_minutes": 60, "overtime": False,
    }
    values.update(changes)
    return CaptureQualification(**values)


def _qualify(*, rows=None, game=None, target=None, qualification_changes=None):
    games = _capture("/games", [_game() if game is None else game])
    plays = _capture("/plays", _plays() if rows is None else rows)
    qualification = _qualification(plays, **(qualification_changes or {}))
    artifact = qualify_excitement(
        target=target or _target(), qualification=qualification, games=games, plays=plays
    )
    return artifact, games, plays, qualification


class ExcitementNormalizationTests(unittest.TestCase):
    def test_full_timeline_orders_provider_identity_and_preserves_same_clock_changes(self):
        artifact, *_ = _qualify()
        self.assertEqual(artifact.status, "full")
        self.assertEqual(artifact.final.quarter_scores, ((0, 0), (7, 0), (7, 0)))
        self.assertEqual(
            [point.elapsed_minute for point in artifact.timeline],
            [0, 15, 20, 30, 40, 45, 55, 55, 60],
        )
        same_clock = [point for point in artifact.timeline if point.elapsed_minute == 55]
        self.assertEqual([(p.home_score, p.away_score) for p in same_clock], [(7, 7), (14, 7)])

    def test_unqualified_semantics_remain_reduced(self):
        cases = [
            ({"score_semantics": "unknown"}, QualificationReason.AFTER_PLAY_UNKNOWN),
            ({"capture_completeness": "unknown"}, QualificationReason.CAPTURE_COMPLETENESS_UNKNOWN),
            ({"capture_completeness": "partial"}, QualificationReason.CAPTURE_PARTIAL),
            ({"regulation_minutes": None}, QualificationReason.REGULATION_DURATION_UNKNOWN),
            ({"overtime": None}, QualificationReason.OVERTIME_UNKNOWN),
        ]
        for changes, reason in cases:
            with self.subTest(reason=reason):
                artifact, *_ = _qualify(qualification_changes=changes)
                self.assertEqual(artifact.status, "reduced")
                self.assertIn(reason, artifact.reasons)
                self.assertEqual(artifact.timeline, ())

    def test_partial_capture_preserves_independently_verified_reduced_final(self):
        rows = [row for row in _plays() if row["period"] == 2]
        artifact, *_ = _qualify(
            rows=rows,
            qualification_changes={
                "capture_completeness": "partial",
                "regulation_minutes": None,
                "overtime": None,
            },
        )
        self.assertEqual(artifact.status, "reduced")
        self.assertEqual((artifact.final.home_score, artifact.final.away_score), (14, 7))
        self.assertIn(QualificationReason.CAPTURE_PARTIAL, artifact.reasons)
        self.assertEqual(artifact.timeline, ())

        complete_without_start = [row for row in _plays() if row["period"] >= 2]
        with self.assertRaisesRegex(InvalidEvidenceError, "do not begin 0-0"):
            _qualify(rows=complete_without_start)

    def test_unknown_score_semantics_do_not_apply_after_play_rules(self):
        rows = [dict(row) for row in _plays()]
        rows[1]["scoring"] = False
        artifact, *_ = _qualify(
            rows=rows,
            qualification_changes={"score_semantics": "unknown"},
        )
        self.assertEqual(artifact.status, "reduced")
        self.assertEqual((artifact.final.home_score, artifact.final.away_score), (14, 7))
        self.assertIn(QualificationReason.AFTER_PLAY_UNKNOWN, artifact.reasons)

        rows[3]["home"] = "Different"
        with self.assertRaisesRegex(InvalidEvidenceError, "orientation differs"):
            _qualify(rows=rows, qualification_changes={"score_semantics": "unknown"})

    def test_source_identity_binds_exact_caller_assertions(self):
        base, *_ = _qualify()
        assertions = [
            {"score_semantics": "unknown"},
            {"capture_completeness": "partial"},
            {"regulation_minutes": 59},
            {"overtime": None},
        ]
        for changes in assertions:
            with self.subTest(changes=changes):
                changed, *_ = _qualify(qualification_changes=changes)
                self.assertNotEqual(changed.source_id, base.source_id)

        for target_changes in (
            {"completion": "unknown"},
            {"game_format": "unknown"},
        ):
            with self.subTest(target_changes=target_changes):
                changed, *_ = _qualify(target=_target(**target_changes))
                self.assertNotEqual(changed.source_id, base.source_id)

    def test_only_frozen_policy_identity_is_accepted(self):
        plays = _capture("/plays", _plays())
        for changes in (
            {"policy_id": "other-policy"},
            {"policy_version": "2"},
            {"policy_sha256": "0" * 64},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(InvalidEvidenceError, "frozen score-trajectory policy"):
                    _qualification(plays, **changes)

    def test_unknown_roster_disposition_emits_no_final(self):
        for changes, reason in [
            ({"game_format": "unknown"}, QualificationReason.FORMAT_UNKNOWN),
            ({"game_format": "unsupported"}, QualificationReason.FORMAT_UNSUPPORTED),
            ({"completion": "unknown"}, QualificationReason.COMPLETION_UNKNOWN),
        ]:
            artifact, *_ = _qualify(target=_target(**changes))
            self.assertEqual(artifact.status, "insufficient")
            self.assertIsNone(artifact.final)
            self.assertIn(reason, artifact.reasons)

    def test_bytes_manifest_filters_and_orientation_are_exact(self):
        capture = _capture("/plays", _plays())
        with self.assertRaisesRegex(InvalidEvidenceError, "differ from the receipt"):
            RetainedCapture(capture.request, capture.receipt, capture.raw_bytes + b" ")
        with self.assertRaisesRegex(InvalidEvidenceError, "reviewed manifest"):
            _qualify(qualification_changes={"plays_manifest_sha256": "0" * 64})
        with self.assertRaisesRegex(InvalidEvidenceError, "target participant"):
            _qualify(target=_target(home_team="Different"))

    def test_ambiguous_order_and_score_decrease_fail(self):
        rows = _plays()
        first, second = rows[-1], rows[-2]
        ambiguous = [dict(row) for row in rows]
        ambiguous[-2]["driveNumber"] = first["driveNumber"]
        ambiguous[-2]["playNumber"] = first["playNumber"]
        with self.assertRaisesRegex(InvalidEvidenceError, "share one drive/play"):
            _qualify(rows=ambiguous)

        decrease = [dict(row) for row in rows]
        decrease[0] = _play(9, 4, 0, 13, 7)
        with self.assertRaisesRegex(InvalidEvidenceError, "decreases a score"):
            _qualify(rows=decrease)

    def test_clock_inversion_is_typed_reduced_flow(self):
        cases = []
        cross_drive = [dict(row) for row in _plays()]
        prior = next(row for row in cross_drive if row["driveNumber"] == 3)
        current = next(row for row in cross_drive if row["driveNumber"] == 4)
        prior["clock"] = {"minutes": 14, "seconds": 49}
        current["clock"] = {"minutes": 15, "seconds": 0}
        cases.append(cross_drive)
        cases.append(_plays() + [_play(9, 4, 60, 14, 7, play=2)])

        for rows in cases:
            with self.subTest(rows=len(rows)):
                artifact, *_ = _qualify(rows=rows)
                self.assertEqual(artifact.status, "reduced")
                self.assertIn(QualificationReason.CLOCK_ORDER_INVERSION, artifact.reasons)
                self.assertEqual((artifact.final.home_score, artifact.final.away_score), (14, 7))
                self.assertEqual(artifact.final.quarter_scores, ((0, 0), (7, 0), (7, 0)))
                self.assertFalse(artifact.final.overtime)
                self.assertEqual(artifact.timeline, ())

        contradictory = [dict(row) for row in cross_drive]
        contradictory[-1]["home"] = "Different"
        with self.assertRaisesRegex(InvalidEvidenceError, "orientation differs"):
            _qualify(rows=contradictory)
        with self.assertRaisesRegex(InvalidEvidenceError, "do not reach the games final"):
            _qualify(
                rows=cross_drive,
                game=_game(homePoints=15, homeLineScores=[0, 7, 0, 8]),
            )

    def test_scoring_flag_requires_an_observed_score_transition(self):
        rows = [dict(row) for row in _plays()]
        unchanged = next(
            row
            for row in rows
            if row["period"] == 1 and row["clock"] == {"minutes": 0, "seconds": 0}
        )
        unchanged["scoring"] = True
        artifact, *_ = _qualify(rows=rows)
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.SCORING_TRANSITION_MISSING, artifact.reasons)
        self.assertEqual((artifact.final.home_score, artifact.final.away_score), (14, 7))

        inverse = [dict(row) for row in _plays()]
        changed = next(row for row in inverse if row["scoring"] is True)
        changed["scoring"] = False
        with self.assertRaisesRegex(InvalidEvidenceError, "not marked scoring"):
            _qualify(rows=inverse)

    def test_public_qualification_identifiers_are_opaque(self):
        plays = _capture("/plays", _plays())
        for value in (
            "/Users/example/private-inputs/review.json",
            "../review",
            "review with spaces",
            "file:review",
            "x" * 129,
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(InvalidEvidenceError, "opaque public identifier"):
                    _qualification(plays, evidence_reference=value)
        with self.assertRaisesRegex(InvalidEvidenceError, "opaque public identifier"):
            _target(qualification_id="/tmp/target")
        with self.assertRaisesRegex(InvalidEvidenceError, "opaque public identifier"):
            _qualification(plays, qualification_id="capture/review")

    def test_missing_start_is_reduced_but_supported_period_end_is_derived(self):
        no_start = [row for row in _plays() if not (row["period"] == 1 and row["clock"]["minutes"] == 15)]
        artifact, *_ = _qualify(rows=no_start)
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.REGULATION_BOUNDARY_MISSING, artifact.reasons)
        self.assertEqual(artifact.timeline, ())

        no_end = [row for row in _plays() if not (row["period"] == 2 and row["clock"]["minutes"] == 0)]
        artifact, *_ = _qualify(rows=no_end)
        self.assertEqual(artifact.status, "full")
        self.assertIn(QualificationInference.QUARTER_END_CARRY, artifact.inferences)
        self.assertTrue(any(point.elapsed_minute == 30 for point in artifact.timeline))

    def test_missing_clock_is_typed_reduced_evidence(self):
        rows = [dict(row) for row in _plays()]
        rows[3].pop("clock")
        artifact, *_ = _qualify(rows=rows)
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.CLOCK_EVIDENCE_MISSING, artifact.reasons)
        self.assertEqual(artifact.timeline, ())
        self.assertIsNone(artifact.normalized)

    def test_quarter_and_final_disagreement_fail(self):
        with self.assertRaisesRegex(InvalidEvidenceError, "sum to the final"):
            _qualify(game=_game(homeLineScores=[0, 7, 0, 6]))
        rows = [dict(row) for row in _plays()]
        rows[4] = _play(5, 3, 300, 8, 0, scoring=True)
        with self.assertRaises(InvalidEvidenceError):
            _qualify(rows=rows)

    def test_wire_round_trip_recomputes_sources_and_rejects_reseal(self):
        artifact, games, plays, qualification = _qualify()
        raw = artifact.to_bytes()
        restored = QualificationArtifact.from_bytes(
            raw, target=_target(), qualification=qualification, games=games, plays=plays
        )
        self.assertEqual(restored, artifact)
        self.assertEqual(restored.capture_score_semantics, "after_play")
        self.assertEqual(restored.capture_completeness, "complete")
        self.assertEqual(restored.capture_regulation_minutes, 60)
        self.assertFalse(restored.capture_overtime)
        self.assertEqual(restored.target_completion, "completed")
        self.assertEqual(restored.target_game_format, "normal")
        assert_public_bytes("qualification.json", artifact.to_public_bytes())
        value = json.loads(raw)
        value["timeline"][2]["home_score"] = 6
        value["artifact_id"] = "0" * 64
        tampered = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self.assertRaisesRegex(InvalidEvidenceError, "does not match"):
            QualificationArtifact.from_bytes(
                tampered, target=_target(), qualification=qualification, games=games, plays=plays
            )

    def test_artifact_cannot_be_directly_forged(self):
        artifact, *_ = _qualify()
        with self.assertRaisesRegex(InvalidEvidenceError, "must be produced"):
            replace(artifact, _token=object())


if __name__ == "__main__":
    unittest.main()
