"""Independent source-boundary checks for ADR-0021 normalization.

These fixtures are synthetic caller-qualified evidence.  They exercise the
observable correspondence, ordering, reduced-evidence, and serialization
boundaries without claiming CFBD coverage or empirical AEV validity.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from cfb.excitement_source import PilotManifest, SupplementReceipt
from cfb.private_inputs import InputReference
from cfb.excitement_normalization import (
    CaptureQualification,
    InvalidEvidenceError,
    QualificationArtifact,
    QualificationInference,
    QualificationError,
    QualificationReason,
    RetainedCapture,
    TargetGame,
    qualify_excitement,
)


MANIFEST = PilotManifest.load("config/excitement-pilot-v1.json")
PLAYS_REQUEST = next(request for request in MANIFEST.requests if request.endpoint == "/plays" and request.year == 2025)
GAMES_REQUEST = next(request for request in MANIFEST.requests if request.endpoint == "/games" and request.year == 2025)
ARCHIVE_SHA = MANIFEST.source_archive_sha256
TARGET_QUALIFICATION_ID = "synthetic-target-v1"
CAPTURE_QUALIFICATION_ID = "synthetic-capture-v1"
CAPTURE_POLICY_ID = "cfbd-score-trajectory-v1"
CAPTURE_POLICY_VERSION = "1"
CAPTURE_POLICY_SHA256 = "5166109215b2a919160e2a2a8d79e4a595e01a854d7f15f8a6f42e29944963db"
EVIDENCE_REFERENCE = "synthetic-review-2026-09-30"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _game_payload(*, home: str = "Ohio State", away: str = "Rival", game_id: int = 401752677,
                  home_score: int = 21, away_score: int = 3, completed: bool = True,
                  overtime: bool = False) -> bytes:
    home_lines = [7, 0, 7, max(0, home_score - 14)]
    away_lines = [0, 3, 0, max(0, away_score - 3)]
    if overtime:
        home_lines = [7, 0, 7, 7, max(0, home_score - 21)]
        away_lines = [0, 3, 7, 4, max(0, away_score - 14)]
    return _json_bytes([{
        "id": game_id,
        "season": 2025,
        "seasonType": "regular",
        "week": 1,
        "homeTeam": home,
        "awayTeam": away,
        "homePoints": home_score,
        "awayPoints": away_score,
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "homeLineScores": home_lines,
        "awayLineScores": away_lines,
        "overtime": overtime,
        "completed": completed,
        "status": "completed" if completed else "in_progress",
    }])


def _play(play_number: int, period: int, minute: int, second: int,
          home_score: int, away_score: int, *, drive: int | None = None,
          game_id: int = 401752677, offense: str = "Ohio State",
          defense: str = "Rival", scoring: bool = False) -> dict[str, object]:
    return {
        "id": str(900000 + play_number),
        "gameId": game_id,
        "driveNumber": drive or period,
        "playNumber": play_number,
        "period": period,
        "clock": {"minutes": minute, "seconds": second},
        "home": "Ohio State",
        "away": "Rival",
        "offenseScore": home_score if offense == "Ohio State" else away_score,
        "defenseScore": away_score if offense == "Ohio State" else home_score,
        "scoring": scoring,
        "offense": offense,
        "defense": defense,
        "playType": "Rush",
        "text": f"synthetic play {play_number}",
    }


def _plays_payload(*, reordered: bool = False, final_home: int = 21,
                   final_away: int = 3) -> bytes:
    rows = [
        _play(1, 1, 15, 0, 0, 0),
        _play(2, 1, 10, 0, 7, 0, scoring=True),
        _play(3, 1, 0, 0, 7, 0),
        _play(4, 2, 15, 0, 7, 0),
        _play(5, 2, 5, 0, 7, 3, scoring=True),
        _play(6, 2, 0, 0, 7, 3),
        _play(7, 3, 15, 0, 7, 3),
        _play(8, 3, 8, 0, 14, 3, scoring=True),
        _play(9, 3, 0, 0, 14, 3),
        _play(10, 4, 15, 0, 14, 3),
        _play(11, 4, 5, 0, 21, 3, scoring=True),
        _play(12, 4, 0, 0, final_home, final_away),
    ]
    if reordered:
        rows = list(reversed(rows))
    return _json_bytes(rows)


def _receipt(request, payload: bytes) -> SupplementReceipt:
    digest = hashlib.sha256(payload).hexdigest()
    return SupplementReceipt(
        request_id=request.request_id,
        endpoint=request.endpoint,
        params=request.params,
        response=InputReference(f"synthetic-{request.request_id}", digest, len(payload)),
        captured_at="2026-09-30T12:00:00+00:00",
        pilot_id=MANIFEST.pilot_id,
        manifest_sha256=MANIFEST.manifest_sha256,
        allowance_id="synthetic-allowance",
        source_archive_sha256=ARCHIVE_SHA,
        parent_snapshot_path=request.parent_snapshot_path,
        parent_snapshot_sha256=request.parent_snapshot_sha256,
        parent_snapshot_checksum=request.parent_snapshot_checksum,
    )


def _capture(request, payload: bytes) -> RetainedCapture:
    return RetainedCapture(request=request, receipt=_receipt(request, payload), raw_bytes=payload)


def _target(*, home: str = "Ohio State", away: str = "Rival", game_id: str = "401752677",
            completion: str = "completed", game_format: str = "normal") -> TargetGame:
    return TargetGame(
        game_id=game_id,
        season=2025,
        season_type="regular",
        provider_week=1,
        home_team=home,
        away_team=away,
        completion=completion,
        game_format=game_format,
        qualification_id=TARGET_QUALIFICATION_ID,
        games_manifest_sha256=MANIFEST.manifest_sha256,
        source_archive_sha256=ARCHIVE_SHA,
        parent_snapshot_path=GAMES_REQUEST.parent_snapshot_path,
        parent_snapshot_sha256=GAMES_REQUEST.parent_snapshot_sha256,
        parent_snapshot_checksum=GAMES_REQUEST.parent_snapshot_checksum,
    )


def _qualification(*, score_semantics: str = "after_play", completeness: str = "complete",
                   regulation_minutes: int | None = 60, overtime: bool | None = False,
                   qualification_id: str = CAPTURE_QUALIFICATION_ID,
                   plays_sha256: str | None = None) -> CaptureQualification:
    plays = _plays_payload()
    return CaptureQualification(
        game_id="401752677",
        plays_response_sha256=plays_sha256 or hashlib.sha256(plays).hexdigest(),
        qualification_id=qualification_id,
        policy_id=CAPTURE_POLICY_ID,
        policy_version=CAPTURE_POLICY_VERSION,
        policy_sha256=CAPTURE_POLICY_SHA256,
        evidence_reference=EVIDENCE_REFERENCE,
        plays_manifest_sha256=MANIFEST.manifest_sha256,
        source_archive_sha256=ARCHIVE_SHA,
        parent_snapshot_sha256=PLAYS_REQUEST.parent_snapshot_sha256,
        score_semantics=score_semantics,
        capture_completeness=completeness,
        regulation_minutes=regulation_minutes,
        overtime=overtime,
    )


def _qualify(*, games: bytes | None = None, plays: bytes | None = None,
             target: TargetGame | None = None,
             qualification: CaptureQualification | None = None) -> QualificationArtifact:
    games = games or _game_payload()
    plays = plays or _plays_payload()
    return qualify_excitement(
        target=target or _target(),
        qualification=qualification or _qualification(plays_sha256=hashlib.sha256(plays).hexdigest()),
        games=_capture(GAMES_REQUEST, games),
        plays=_capture(PLAYS_REQUEST, plays),
    )


class IndependentNormalizationTests(unittest.TestCase):
    def test_complete_after_play_capture_is_ordered_by_provider_identity(self):
        ordered = _qualify()
        reordered = _qualify(plays=_plays_payload(reordered=True))
        self.assertEqual(ordered.status, "full")
        self.assertEqual(ordered.final.home_score, 21)
        self.assertEqual(ordered.final.away_score, 3)
        self.assertEqual(ordered.final.quarter_scores, ((7, 0), (7, 3), (14, 3)))
        self.assertEqual(ordered.capture_policy_sha256, CAPTURE_POLICY_SHA256)
        self.assertEqual(ordered.timeline, reordered.timeline)
        self.assertEqual(ordered.normalized.observed_states, reordered.normalized.observed_states)

    def test_same_clock_score_transitions_retain_order_and_zero_duration(self):
        rows = json.loads(_plays_payload())
        rows.insert(2, _play(13, 1, 10, 0, 7, 7, drive=1, scoring=True))
        rows.insert(3, _play(14, 1, 10, 0, 14, 7, drive=1, scoring=True))
        for number, row in enumerate(rows, 1):
            row["playNumber"] = number
            row["id"] = str(900000 + number)
        for row in rows[4:]:
            row["offenseScore"] = int(row["offenseScore"]) + 7
            row["defenseScore"] = int(row["defenseScore"]) + 7
        payload = _json_bytes(rows)
        game_rows = json.loads(_game_payload(home_score=28, away_score=10))
        game_rows[0]["homeLineScores"] = [14, 0, 7, 7]
        game_rows[0]["awayLineScores"] = [7, 3, 0, 0]
        artifact = _qualify(games=_json_bytes(game_rows), plays=payload)
        same_clock = [point for point in artifact.timeline if point.elapsed_minute == 5.0]
        self.assertGreaterEqual(len(same_clock), 2)
        states = [(point.home_score, point.away_score) for point in same_clock]
        self.assertEqual(states[-2:], [(7, 7), (14, 7)])

    def test_scoring_event_cannot_increase_both_teams_without_explanation(self):
        rows = json.loads(_plays_payload())
        for index, home_score, away_score in (
            (4, 8, 1), (5, 8, 1), (6, 8, 1), (7, 15, 1),
            (8, 15, 1), (9, 15, 1), (10, 22, 1), (11, 22, 1),
        ):
            rows[index]["offenseScore"] = home_score
            rows[index]["defenseScore"] = away_score
        rows[4]["scoring"] = True
        rows[7]["scoring"] = True
        rows[10]["scoring"] = True
        game_rows = json.loads(_game_payload(home_score=22, away_score=1))
        game_rows[0]["homeLineScores"] = [7, 1, 7, 7]
        game_rows[0]["awayLineScores"] = [0, 1, 0, 0]
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(games=_json_bytes(game_rows), plays=_json_bytes(rows))

    def test_scoring_true_without_a_score_change_cannot_qualify_full(self):
        rows = json.loads(_plays_payload())
        rows[2]["scoring"] = True
        artifact = _qualify(plays=_json_bytes(rows))
        self.assertNotEqual(artifact.status, "full")
        self.assertIn("scoring-transition-missing", {reason.value for reason in artifact.reasons})

    def test_ambiguous_same_clock_order_and_duplicate_conflict_fail_closed(self):
        rows = json.loads(_plays_payload())
        rows[1]["playNumber"] = rows[2]["playNumber"]
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

        rows = json.loads(_plays_payload())
        rows[1]["offenseScore"] = 8
        rows.append(dict(rows[1], id="999999", offenseScore=6))
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

    def test_score_decrease_clock_inversion_and_final_mismatch_fail_closed(self):
        rows = json.loads(_plays_payload())
        rows[4]["offenseScore"] = 6
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

        rows = json.loads(_plays_payload())
        rows[4]["clock"]["minutes"] = 16
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(games=_game_payload(home_score=20))

    def test_cross_drive_period_boundary_14_49_to_15_00_remains_ordered(self):
        rows = json.loads(_plays_payload())
        rows[1]["clock"] = {"minutes": 14, "seconds": 49}
        rows[2]["period"] = 2
        rows[2]["clock"] = {"minutes": 15, "seconds": 0}
        rows[2]["driveNumber"] = 2
        artifact = _qualify(plays=_json_bytes(rows))
        self.assertEqual(artifact.status, "full")
        self.assertEqual(
            [(point.elapsed_minute, point.home_score, point.away_score) for point in artifact.timeline[:4]],
            [(0.0, 0, 0), (0.18333333333333332, 7, 0), (15.0, 7, 0), (15.0, 7, 0)],
        )

    def test_sparse_quarter_start_can_carry_verified_previous_end(self):
        rows = json.loads(_plays_payload())
        rows = [row for row in rows if not (row["period"] in (2, 3, 4) and row["clock"]["minutes"] == 15)]
        artifact = _qualify(plays=_json_bytes(rows))
        self.assertEqual(artifact.status, "full")
        self.assertEqual(artifact.final.home_score, 21)
        self.assertIn(QualificationInference.QUARTER_START_CARRY, artifact.inferences)

    def test_missing_regulation_start_rejects_but_supported_end_carry_is_full(self):
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(json.loads(_plays_payload())[1:]))

        artifact = _qualify(plays=_json_bytes(json.loads(_plays_payload())[:-1]))
        self.assertEqual(artifact.status, "full")
        self.assertIn(QualificationInference.QUARTER_END_CARRY, artifact.inferences)
        self.assertEqual(
            (artifact.timeline[-1].elapsed_minute, artifact.timeline[-1].home_score, artifact.timeline[-1].away_score),
            (60.0, 21, 3),
        )

    def test_missing_clock_order_or_quarters_stay_reduced(self):
        rows = json.loads(_plays_payload())
        rows[4].pop("clock")
        artifact = _qualify(plays=_json_bytes(rows))
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.CLOCK_EVIDENCE_MISSING, artifact.reasons)
        self.assertIsNotNone(artifact.final)

        rows = json.loads(_plays_payload())
        rows[4].pop("driveNumber")
        artifact = _qualify(plays=_json_bytes(rows))
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.ORDER_EVIDENCE_MISSING, artifact.reasons)

        rows = json.loads(_plays_payload())
        artifact = _qualify(plays=_json_bytes([]))
        self.assertEqual(artifact.status, "reduced")
        self.assertIn(QualificationReason.PLAYS_EMPTY, artifact.reasons)
        self.assertIsNotNone(artifact.final)

    def test_missing_or_partial_capture_is_reduced_and_never_upgraded(self):
        partial = _qualification(completeness="partial", regulation_minutes=45, overtime=None)
        artifact = _qualify(plays=_plays_payload(), qualification=partial)
        self.assertNotEqual(artifact.status, "full")
        self.assertEqual(artifact.final.home_score, 21)
        self.assertIsNone(artifact.normalized)

        after_scoring = json.loads(_plays_payload())[1:]
        partial_after_scoring = _qualification(
            completeness="partial", regulation_minutes=None, overtime=None,
            plays_sha256=hashlib.sha256(_json_bytes(after_scoring)).hexdigest(),
        )
        artifact = _qualify(plays=_json_bytes(after_scoring), qualification=partial_after_scoring)
        self.assertEqual(artifact.status, "reduced")
        self.assertEqual((artifact.final.home_score, artifact.final.away_score), (21, 3))
        self.assertIn(QualificationReason.REGULATION_BOUNDARY_MISSING, artifact.reasons)
        self.assertIsNone(artifact.normalized)

        unknown = _qualification(completeness="unknown", regulation_minutes=None, overtime=None)
        artifact = _qualify(qualification=unknown)
        self.assertNotEqual(artifact.status, "full")
        self.assertTrue(artifact.reasons)

    def test_partial_clock_inversion_reduces_but_preserves_verified_metadata(self):
        rows = json.loads(_plays_payload())
        rows[2]["clock"] = {"minutes": 11, "seconds": 0}
        qualification = _qualification(
            completeness="partial", regulation_minutes=None, overtime=None,
            plays_sha256=hashlib.sha256(_json_bytes(rows)).hexdigest(),
        )
        artifact = _qualify(plays=_json_bytes(rows), qualification=qualification)
        self.assertEqual(artifact.status, "reduced")
        self.assertEqual((artifact.final.home_score, artifact.final.away_score), (21, 3))
        self.assertEqual(artifact.final.quarter_scores, ((7, 0), (7, 3), (14, 3)))
        self.assertFalse(artifact.final.overtime)
        self.assertIn("clock-order-inversion", {reason.value for reason in artifact.reasons})

    def test_clock_inversion_cannot_mask_identity_or_final_contradictions(self):
        rows = json.loads(_plays_payload())
        rows[2]["clock"] = {"minutes": 11, "seconds": 0}
        payload = _json_bytes(rows)
        qualification = _qualification(
            completeness="partial", regulation_minutes=None, overtime=None,
            plays_sha256=hashlib.sha256(payload).hexdigest(),
        )

        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(
                plays=payload,
                qualification=qualification,
                target=replace(_target(), qualification_id=CAPTURE_QUALIFICATION_ID),
            )
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(
                games=_game_payload(home_score=20), plays=payload, qualification=qualification,
            )

        forged_receipt = replace(
            _receipt(PLAYS_REQUEST, payload),
            response=InputReference("synthetic-tampered", "0" * 64, len(payload)),
        )
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            RetainedCapture(request=PLAYS_REQUEST, receipt=forged_receipt, raw_bytes=payload)

    def test_incomplete_unsupported_and_quarter_final_disagreement_do_not_become_normal(self):
        for target, reason in (
            (_target(completion="incomplete"), QualificationReason.GAME_INCOMPLETE),
            (_target(completion="unknown"), QualificationReason.COMPLETION_UNKNOWN),
            (_target(game_format="unsupported"), QualificationReason.FORMAT_UNSUPPORTED),
            (_target(game_format="unknown"), QualificationReason.FORMAT_UNKNOWN),
        ):
            artifact = _qualify(target=target)
            self.assertEqual(artifact.status, "insufficient")
            self.assertIsNone(artifact.final)
            self.assertIn(reason, artifact.reasons)

        rows = json.loads(_plays_payload())
        rows[5]["defenseScore"] = 4
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

    def test_collapsed_overtime_is_binary_and_does_not_invent_elapsed_time(self):
        rows = json.loads(_plays_payload())
        rows[4]["defenseScore"] = 3
        rows[7]["offenseScore"] = 14
        rows[10]["offenseScore"] = 14
        rows[10]["defenseScore"] = 14
        rows[11]["offenseScore"] = 14
        rows[11]["defenseScore"] = 14
        rows[11]["scoring"] = False
        rows.extend([
            _play(13, 5, 0, 0, 21, 14, scoring=True),
            _play(14, 5, 0, 0, 28, 14, scoring=True),
        ])
        game_rows = json.loads(_game_payload(home_score=28, away_score=14, overtime=True))
        game_rows[0]["homeLineScores"] = [7, 0, 7, 0, 14]
        game_rows[0]["awayLineScores"] = [0, 3, 0, 11, 0]
        games = _json_bytes(game_rows)
        target = _target()
        qualification = _qualification(overtime=True, plays_sha256=hashlib.sha256(_json_bytes(rows)).hexdigest())
        artifact = _qualify(games=games, plays=_json_bytes(rows), target=target, qualification=qualification)
        self.assertTrue(artifact.final.overtime)
        self.assertTrue(artifact.normalized.overtime)
        self.assertTrue(all(point.elapsed_minute is None for point in artifact.normalized.overtime))
        self.assertEqual(
            [(point.home_score, point.away_score) for point in artifact.normalized.overtime],
            [(21, 14), (28, 14)],
        )
        self.assertEqual(len(artifact.normalized.overtime), 2)

    def test_unknown_overtime_is_not_false_and_does_not_upgrade(self):
        qualification = _qualification(overtime=None, completeness="complete", regulation_minutes=60)
        artifact = _qualify(qualification=qualification)
        self.assertNotEqual(artifact.status, "full")
        self.assertIsNone(artifact.final.overtime)

    def test_exact_game_season_team_orientation_and_source_bindings_are_required(self):
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(target=_target(away="Ohio State"))
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(target=_target(game_id="401752678"))
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(target=replace(_target(), source_archive_sha256="0" * 64))
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(qualification=replace(_qualification(), parent_snapshot_sha256="0" * 64))

        rows = json.loads(_plays_payload())
        rows[1]["home"] = "Rival"
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(plays=_json_bytes(rows))

        payload = _plays_payload()
        forged_receipt = replace(_receipt(PLAYS_REQUEST, payload), manifest_sha256="0" * 64)
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            qualify_excitement(
                target=_target(), qualification=_qualification(),
                games=_capture(GAMES_REQUEST, _game_payload()),
                plays=RetainedCapture(request=PLAYS_REQUEST, receipt=forged_receipt, raw_bytes=payload),
            )

        payload = _plays_payload()
        mismatched_request = replace(PLAYS_REQUEST, params=tuple(sorted({**PLAYS_REQUEST.parameter_map, "team": "Georgia"}.items())))
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            qualify_excitement(
                target=_target(), qualification=_qualification(),
                games=_capture(GAMES_REQUEST, _game_payload()),
                plays=RetainedCapture(
                    request=mismatched_request,
                    receipt=_receipt(PLAYS_REQUEST, payload),
                    raw_bytes=payload,
                ),
            )

    def test_missing_metadata_and_unknown_score_semantics_do_not_upgrade_to_full(self):
        game = json.loads(_game_payload())
        del game[0]["homeTeam"]
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            _qualify(games=_json_bytes(game))

        qualification = _qualification(score_semantics="unknown")
        artifact = _qualify(qualification=qualification)
        self.assertNotEqual(artifact.status, "full")
        self.assertTrue(artifact.reasons)

    def test_raw_digest_and_declared_size_are_verified_before_decoding(self):
        payload = _plays_payload()
        good = _capture(PLAYS_REQUEST, payload)
        bad_receipt = replace(
            good.receipt,
            response=InputReference("synthetic-tampered", "0" * 64, len(payload)),
        )
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            RetainedCapture(request=PLAYS_REQUEST, receipt=bad_receipt, raw_bytes=payload)

    def test_round_trip_and_resealed_semantic_path_tampering_fail_closed(self):
        artifact = _qualify()
        restored = QualificationArtifact.from_bytes(
            artifact.to_bytes(), target=_target(), qualification=_qualification(),
            games=_capture(GAMES_REQUEST, _game_payload()), plays=_capture(PLAYS_REQUEST, _plays_payload()),
        )
        self.assertEqual(restored.timeline, artifact.timeline)
        self.assertEqual(restored.final, artifact.final)
        self.assertEqual(restored.capture_policy_id, CAPTURE_POLICY_ID)
        self.assertEqual(restored.capture_policy_version, CAPTURE_POLICY_VERSION)
        self.assertEqual(restored.capture_policy_sha256, CAPTURE_POLICY_SHA256)
        self.assertEqual(restored.capture_evidence_reference, EVIDENCE_REFERENCE)

        resealed_plays = _plays_payload(reordered=True)
        resealed_qualification = _qualification(
            plays_sha256=hashlib.sha256(resealed_plays).hexdigest()
        )
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            QualificationArtifact.from_bytes(
                artifact.to_bytes(), target=_target(), qualification=resealed_qualification,
                games=_capture(GAMES_REQUEST, _game_payload()),
                plays=_capture(PLAYS_REQUEST, resealed_plays),
            )

        tampered = json.loads(artifact.to_bytes())
        timeline = tampered.get("timeline") or tampered.get("normalized", {}).get("regulation")
        self.assertIsInstance(timeline, list)
        candidate = next(row for row in timeline if row.get("home_score", row.get("homeScore", 0)) > 0)
        key = "home_score" if "home_score" in candidate else "homeScore"
        candidate[key] = int(candidate[key]) + 1
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            QualificationArtifact.from_bytes(
                _json_bytes(tampered), target=_target(), qualification=_qualification(),
                games=_capture(GAMES_REQUEST, _game_payload()), plays=_capture(PLAYS_REQUEST, _plays_payload()),
            )

        partial_rows = json.loads(_plays_payload())[1:]
        partial_payload = _json_bytes(partial_rows)
        partial_qualification = _qualification(
            completeness="partial", regulation_minutes=None, overtime=None,
            plays_sha256=hashlib.sha256(partial_payload).hexdigest(),
        )
        reduced = _qualify(plays=partial_payload, qualification=partial_qualification)
        promoted = json.loads(reduced.to_bytes())
        promoted["status"] = "full"
        with self.assertRaises((QualificationError, InvalidEvidenceError)):
            QualificationArtifact.from_bytes(
                _json_bytes(promoted), target=_target(), qualification=partial_qualification,
                games=_capture(GAMES_REQUEST, _game_payload()), plays=_capture(PLAYS_REQUEST, partial_payload),
            )

    def test_source_identity_binds_target_completion_and_format_dispositions(self):
        base = _qualify()
        for target in (
            _target(completion="unknown"),
            _target(completion="incomplete"),
            _target(game_format="unknown"),
            _target(game_format="unsupported"),
        ):
            with self.subTest(completion=target.completion, game_format=target.game_format):
                changed = _qualify(target=target)
                self.assertNotEqual(changed.source_id, base.source_id)
                self.assertIsNone(changed.final)

    def test_unsafe_public_provenance_is_rejected_or_omitted_without_unbinding(self):
        cases = (
            ("private-inputs/raw-capture-2025.json", _target, _qualification),
            ("target-qualification/private-review", lambda: replace(_target(), qualification_id="target-qualification/private-review"), _qualification),
            ("capture-qualification/private-review", _target, lambda: replace(_qualification(), qualification_id="capture-qualification/private-review")),
        )
        base = _qualify()
        for changed in (
            _qualify(qualification=replace(_qualification(), evidence_reference="synthetic-review-alt")),
            _qualify(target=replace(_target(), qualification_id="synthetic-target-alt")),
            _qualify(qualification=replace(_qualification(), qualification_id="synthetic-capture-alt")),
        ):
            self.assertNotEqual(changed.source_id, base.source_id)
        for unsafe_value, target_factory, qualification_factory in cases:
            with self.subTest(unsafe_value=unsafe_value):
                try:
                    target = target_factory()
                    qualification = qualification_factory()
                    if unsafe_value.startswith("private-inputs/"):
                        qualification = replace(qualification, evidence_reference=unsafe_value)
                    changed = _qualify(target=target, qualification=qualification)
                    public = changed.to_bytes()
                except (QualificationError, ValueError):
                    continue
                self.assertNotIn(unsafe_value.encode(), public)
                self.assertNotEqual(changed.source_id, base.source_id)

    def test_public_bytes_are_derived_and_exclude_raw_provider_text_and_private_paths(self):
        artifact = _qualify()
        public = artifact.to_public_bytes()
        self.assertNotIn(b"synthetic play", public)
        self.assertNotIn(b"config/excitement-pilot-v1.json", public)
        self.assertNotIn(str(Path.cwd()).encode(), public)
        summary = json.loads(public)
        self.assertIn("status", summary)
        self.assertEqual(summary["source"]["capture_policy_id"], CAPTURE_POLICY_ID)
        self.assertEqual(summary["source"]["capture_policy_version"], CAPTURE_POLICY_VERSION)
        self.assertEqual(summary["source"]["capture_policy_sha256"], CAPTURE_POLICY_SHA256)
        self.assertEqual(summary["source"]["capture_evidence_reference"], EVIDENCE_REFERENCE)
        self.assertEqual(
            summary["source"]["capture_assertions"],
            {
                "score_semantics": "after_play",
                "completeness": "complete",
                "regulation_minutes": 60,
                "overtime": False,
            },
        )
        self.assertNotIn("raw_bytes", summary)
        self.assertNotIn("plays", summary)
        self.assertNotIn("games", summary)


if __name__ == "__main__":
    unittest.main()
