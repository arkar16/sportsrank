"""Pure qualification of retained CFBD score evidence for ADR-0021.

This module does not authenticate a private store or fetch provider data.  The
caller must supply receipts obtained from the reviewed supplement boundary and
an independently reviewed target/capture qualification.  A response digest
identifies bytes; it is not, by itself, evidence that the response is complete
or that the game used the ordinary sixty-minute format.

The only wire reader that returns scoring inputs re-runs qualification against
the retained bytes.  A self-sealed public summary therefore cannot be promoted
to a trusted ``QualifiedFinal`` or timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Literal, Mapping, Sequence

from cfb.excitement import (
    NormalizedTimeline,
    QualifiedFinal,
    TimelinePoint,
    TimelineQualificationError,
    normalize_timeline,
)
from cfb.excitement_source import PilotRequest, SupplementReceipt
from cfb.public_safety import assert_public_bytes


SCHEMA_VERSION = 1
SCORE_TRAJECTORY_POLICY_ID = "cfbd-score-trajectory-v1"
SCORE_TRAJECTORY_POLICY_VERSION = "1"
SCORE_TRAJECTORY_POLICY_SHA256 = (
    "5166109215b2a919160e2a2a8d79e4a595e01a854d7f15f8a6f42e29944963db"
)
_DIGEST_CHARS = frozenset("0123456789abcdef")
_ARTIFACT_TOKEN = object()


class QualificationReason(StrEnum):
    """Stable, public-safe reasons why stronger evidence was unavailable."""

    COMPLETION_UNKNOWN = "completion-unknown"
    GAME_INCOMPLETE = "game-incomplete"
    FORMAT_UNKNOWN = "format-unknown"
    FORMAT_UNSUPPORTED = "format-unsupported"
    CLASSIFICATION_UNKNOWN = "classification-unknown"
    QUARTERS_UNAVAILABLE = "quarters-unavailable"
    PLAYS_EMPTY = "plays-empty"
    PLAY_FIELDS_MISSING = "play-fields-missing"
    ORDER_EVIDENCE_MISSING = "order-evidence-missing"
    CLOCK_EVIDENCE_MISSING = "clock-evidence-missing"
    AFTER_PLAY_UNKNOWN = "after-play-semantics-unknown"
    CAPTURE_PARTIAL = "capture-partial"
    CAPTURE_COMPLETENESS_UNKNOWN = "capture-completeness-unknown"
    REGULATION_DURATION_UNKNOWN = "regulation-duration-unknown"
    REGULATION_BOUNDARY_MISSING = "regulation-boundary-missing"
    PERIOD_COVERAGE_INCOMPLETE = "period-coverage-incomplete"
    OVERTIME_UNKNOWN = "overtime-unknown"


class QualificationInference(StrEnum):
    """Reviewed score-trajectory inferences used by a per-game result."""

    QUARTER_END_CARRY = "quarter-end-constant-score-carry"
    QUARTER_START_CARRY = "quarter-start-constant-score-carry"


class QualificationError(ValueError):
    """Base class for invalid retained-evidence input."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class InvalidEvidenceError(QualificationError):
    """Evidence is malformed, contradictory, or bound to a different source."""


class InsufficientEvidenceError(QualificationError):
    """A requested trusted artifact cannot be reconstructed from bound inputs."""


def _invalid(reason: str, message: str) -> InvalidEvidenceError:
    return InvalidEvidenceError(reason, message)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("invalid-field", f"{field_name} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise _invalid("invalid-field", f"{field_name} contains a control character")
    return value


def _digest(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _DIGEST_CHARS for character in value)
    ):
        raise _invalid("invalid-source-binding", f"{field_name} is not a lowercase SHA-256 digest")
    return value


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid("invalid-field", f"{field_name} must be an integer >= {minimum}")
    return value


def _decimal_game_id(value: object, field_name: str = "game_id") -> str:
    if isinstance(value, bool):
        raise _invalid("invalid-game-id", f"{field_name} must be a positive decimal identifier")
    if isinstance(value, int):
        if value < 1:
            raise _invalid("invalid-game-id", f"{field_name} must be a positive decimal identifier")
        return str(value)
    if not isinstance(value, str) or not value.isdecimal() or value.startswith("0"):
        raise _invalid("invalid-game-id", f"{field_name} must be a positive decimal identifier")
    return value


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


@dataclass(frozen=True, slots=True)
class TargetGame:
    """Externally qualified roster identity and ordinary-game disposition.

    ``qualification_id`` identifies the reviewed roster decision.  This type
    deliberately cannot establish that decision from a caller-supplied hash.
    Qualification below only checks its exact correspondence to retained bytes.
    """

    game_id: str
    season: int
    season_type: Literal["regular", "postseason"]
    provider_week: int
    home_team: str
    away_team: str
    completion: Literal["completed", "incomplete", "unknown"]
    game_format: Literal["normal", "unsupported", "unknown"]
    qualification_id: str
    games_manifest_sha256: str
    source_archive_sha256: str
    parent_snapshot_path: str
    parent_snapshot_sha256: str
    parent_snapshot_checksum: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "game_id", _decimal_game_id(self.game_id))
        _integer(self.season, "season", minimum=1869)
        if self.season_type not in {"regular", "postseason"}:
            raise _invalid("invalid-season-type", "season_type is unsupported")
        _integer(self.provider_week, "provider_week", minimum=0)
        _text(self.home_team, "home_team")
        _text(self.away_team, "away_team")
        if self.home_team == self.away_team:
            raise _invalid("invalid-orientation", "home and away teams must differ")
        if self.completion not in {"completed", "incomplete", "unknown"}:
            raise _invalid("invalid-completion", "completion is unsupported")
        if self.game_format not in {"normal", "unsupported", "unknown"}:
            raise _invalid("invalid-game-format", "game_format is unsupported")
        _text(self.qualification_id, "qualification_id")
        _digest(self.games_manifest_sha256, "games_manifest_sha256")
        _digest(self.source_archive_sha256, "source_archive_sha256")
        if not isinstance(self.parent_snapshot_path, str) or not self.parent_snapshot_path.startswith("snapshots/"):
            raise _invalid("invalid-source-binding", "parent_snapshot_path is unsafe")
        if ".." in self.parent_snapshot_path.split("/") or "\\" in self.parent_snapshot_path:
            raise _invalid("invalid-source-binding", "parent_snapshot_path is unsafe")
        _digest(self.parent_snapshot_sha256, "parent_snapshot_sha256")
        _digest(self.parent_snapshot_checksum, "parent_snapshot_checksum")


@dataclass(frozen=True, slots=True)
class CaptureQualification:
    """Reviewed interpretation of one exact retained plays response.

    The qualification is a caller trust boundary, bound to response and source
    identity.  Values of ``unknown`` remain unavailable; structural validation
    never upgrades them.  There is intentionally no fixture or allow-unverified
    mode.
    """

    game_id: str
    plays_response_sha256: str
    qualification_id: str
    policy_id: str
    policy_version: str
    policy_sha256: str
    evidence_reference: str
    plays_manifest_sha256: str
    source_archive_sha256: str
    parent_snapshot_sha256: str
    score_semantics: Literal["after_play", "unknown"]
    capture_completeness: Literal["complete", "partial", "unknown"]
    regulation_minutes: int | None
    overtime: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "game_id", _decimal_game_id(self.game_id))
        _digest(self.plays_response_sha256, "plays_response_sha256")
        _text(self.qualification_id, "qualification_id")
        _text(self.policy_id, "policy_id")
        _text(self.policy_version, "policy_version")
        _digest(self.policy_sha256, "policy_sha256")
        if (
            self.policy_id,
            self.policy_version,
            self.policy_sha256,
        ) != (
            SCORE_TRAJECTORY_POLICY_ID,
            SCORE_TRAJECTORY_POLICY_VERSION,
            SCORE_TRAJECTORY_POLICY_SHA256,
        ):
            raise _invalid(
                "unsupported-qualification-policy",
                "capture qualification does not name the frozen score-trajectory policy",
            )
        _text(self.evidence_reference, "evidence_reference")
        _digest(self.plays_manifest_sha256, "plays_manifest_sha256")
        _digest(self.source_archive_sha256, "source_archive_sha256")
        _digest(self.parent_snapshot_sha256, "parent_snapshot_sha256")
        if self.score_semantics not in {"after_play", "unknown"}:
            raise _invalid("invalid-score-semantics", "score_semantics is unsupported")
        if self.capture_completeness not in {"complete", "partial", "unknown"}:
            raise _invalid("invalid-capture-completeness", "capture_completeness is unsupported")
        if self.regulation_minutes is not None:
            _integer(self.regulation_minutes, "regulation_minutes", minimum=1)
        if self.overtime is not None and not isinstance(self.overtime, bool):
            raise _invalid("invalid-overtime", "overtime must be true, false, or unknown")


@dataclass(frozen=True, slots=True)
class RetainedCapture:
    """Exact response bytes paired with their reviewed plan and safe receipt."""

    request: PilotRequest
    receipt: SupplementReceipt
    raw_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.request, PilotRequest):
            raise _invalid("invalid-request", "request must be a PilotRequest")
        if not isinstance(self.receipt, SupplementReceipt):
            raise _invalid("invalid-receipt", "receipt must be a SupplementReceipt")
        if not isinstance(self.raw_bytes, bytes):
            raise _invalid("invalid-response", "raw_bytes must be bytes")
        if self.request.request_id != self.receipt.request_id:
            raise _invalid("request-receipt-mismatch", "receipt request_id differs from the plan")
        if self.request.endpoint != self.receipt.endpoint:
            raise _invalid("request-receipt-mismatch", "receipt endpoint differs from the plan")
        if tuple(sorted(self.request.params)) != tuple(sorted(self.receipt.params)):
            raise _invalid("request-receipt-mismatch", "receipt parameters differ from the plan")
        if self.request.parent_snapshot_path != self.receipt.parent_snapshot_path:
            raise _invalid("request-receipt-mismatch", "receipt parent path differs from the plan")
        if self.request.parent_snapshot_sha256 != self.receipt.parent_snapshot_sha256:
            raise _invalid("request-receipt-mismatch", "receipt parent digest differs from the plan")
        if self.request.parent_snapshot_checksum != self.receipt.parent_snapshot_checksum:
            raise _invalid("request-receipt-mismatch", "receipt parent checksum differs from the plan")
        actual = hashlib.sha256(self.raw_bytes).hexdigest()
        if actual != self.receipt.response.sha256:
            raise _invalid("response-digest-mismatch", "retained response bytes differ from the receipt")
        if len(self.raw_bytes) != self.receipt.response.size:
            raise _invalid("response-size-mismatch", "retained response size differs from the receipt")

    def decode_array(self) -> tuple[Mapping[str, object], ...]:
        try:
            parsed = json.loads(self.raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid("malformed-response", "retained response is not valid JSON") from exc
        if not isinstance(parsed, list) or any(not isinstance(row, Mapping) for row in parsed):
            raise _invalid("malformed-response", "retained response must be an array of objects")
        return tuple(parsed)


@dataclass(frozen=True, slots=True)
class QualificationArtifact:
    """A source-bound result; construction is restricted to this module."""

    artifact_id: str
    status: Literal["full", "reduced", "insufficient"]
    reasons: tuple[QualificationReason, ...]
    inferences: tuple[QualificationInference, ...]
    final: QualifiedFinal | None
    timeline: tuple[TimelinePoint, ...]
    normalized: NormalizedTimeline | None
    source_id: str
    game_id: str
    season: int
    games_request_id: str
    games_response_sha256: str
    games_manifest_sha256: str
    plays_request_id: str
    plays_response_sha256: str
    plays_manifest_sha256: str
    target_qualification_id: str
    capture_qualification_id: str
    capture_policy_id: str
    capture_policy_version: str
    capture_policy_sha256: str
    capture_evidence_reference: str
    capture_score_semantics: Literal["after_play", "unknown"]
    capture_completeness: Literal["complete", "partial", "unknown"]
    capture_regulation_minutes: int | None
    capture_overtime: bool | None
    target_completion: Literal["completed", "incomplete", "unknown"]
    target_game_format: Literal["normal", "unsupported", "unknown"]
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _ARTIFACT_TOKEN:
            raise InvalidEvidenceError(
                "unverified-artifact", "qualification artifacts must be produced from bound retained captures"
            )
        _digest(self.artifact_id, "artifact_id")
        _digest(self.source_id, "source_id")
        _decimal_game_id(self.game_id)
        if self.status not in {"full", "reduced", "insufficient"}:
            raise _invalid("invalid-artifact", "artifact status is unsupported")
        if self.status == "full" and (self.final is None or self.normalized is None):
            raise _invalid("invalid-artifact", "full artifact lacks scoring inputs")
        if self.status == "insufficient" and self.final is not None:
            raise _invalid("invalid-artifact", "insufficient artifact unexpectedly contains a final")

    def _content(self) -> dict[str, object]:
        final: dict[str, object] | None = None
        if self.final is not None:
            final = {
                "game_id": self.final.game_id,
                "season": self.final.season,
                "home_score": self.final.home_score,
                "away_score": self.final.away_score,
                "source_snapshot_id": self.final.source_snapshot_id,
                "verified_completed": self.final.verified_completed,
                "normal_format": self.final.normal_format,
                "overtime": self.final.overtime,
                "quarter_scores": (
                    [list(scores) for scores in self.final.quarter_scores]
                    if self.final.quarter_scores is not None
                    else None
                ),
            }
        timeline = [
            {
                "home_score": point.home_score,
                "away_score": point.away_score,
                "sequence": point.sequence,
                "elapsed_minute": point.elapsed_minute,
                "overtime": point.overtime,
            }
            for point in self.timeline
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reasons": [reason.value for reason in self.reasons],
            "inferences": [inference.value for inference in self.inferences],
            "game": {"id": self.game_id, "season": self.season},
            "source": {
                "id": self.source_id,
                "games": {
                    "request_id": self.games_request_id,
                    "response_sha256": self.games_response_sha256,
                    "manifest_sha256": self.games_manifest_sha256,
                },
                "plays": {
                    "request_id": self.plays_request_id,
                    "response_sha256": self.plays_response_sha256,
                    "manifest_sha256": self.plays_manifest_sha256,
                },
                "target_qualification_id": self.target_qualification_id,
                "capture_qualification_id": self.capture_qualification_id,
                "capture_policy_id": self.capture_policy_id,
                "capture_policy_version": self.capture_policy_version,
                "capture_policy_sha256": self.capture_policy_sha256,
                "capture_evidence_reference": self.capture_evidence_reference,
                "capture_policy": {
                    "id": self.capture_policy_id,
                    "version": self.capture_policy_version,
                    "sha256": self.capture_policy_sha256,
                    "evidence_reference": self.capture_evidence_reference,
                },
                "capture_assertions": {
                    "score_semantics": self.capture_score_semantics,
                    "completeness": self.capture_completeness,
                    "regulation_minutes": self.capture_regulation_minutes,
                    "overtime": self.capture_overtime,
                },
                "target_assertions": {
                    "completion": self.target_completion,
                    "game_format": self.target_game_format,
                },
            },
            "final": final,
            "timeline": timeline,
        }

    def to_dict(self) -> dict[str, object]:
        content = self._content()
        return {**content, "artifact_id": self.artifact_id}

    def to_bytes(self) -> bytes:
        raw = _canonical(self.to_dict())
        assert_public_bytes("excitement-qualification.json", raw)
        return raw

    def to_public_bytes(self) -> bytes:
        return self.to_bytes()

    @classmethod
    def from_bytes(
        cls,
        raw: bytes,
        *,
        target: TargetGame,
        qualification: CaptureQualification,
        games: RetainedCapture,
        plays: RetainedCapture,
    ) -> "QualificationArtifact":
        if not isinstance(raw, bytes):
            raise _invalid("malformed-artifact", "artifact bytes must be bytes")
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid("malformed-artifact", "artifact is not valid JSON") from exc
        if not isinstance(parsed, Mapping):
            raise _invalid("malformed-artifact", "artifact must be an object")
        if _canonical(parsed) != raw:
            raise _invalid("noncanonical-artifact", "artifact bytes are not canonical")
        rebuilt = qualify_excitement(
            target=target, qualification=qualification, games=games, plays=plays
        )
        if rebuilt.to_bytes() != raw:
            raise _invalid("artifact-source-mismatch", "artifact does not match the bound retained captures")
        return rebuilt


@dataclass(frozen=True, slots=True)
class _GameEvidence:
    home_score: int
    away_score: int
    quarter_scores: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None
    regulation_score: tuple[int, int] | None
    overtime_from_lines: bool | None
    classifications_known: bool
    provider_completed: bool


@dataclass(frozen=True, slots=True)
class _PlayEvidence:
    timeline: tuple[TimelinePoint, ...]
    reasons: tuple[QualificationReason, ...]
    inferences: tuple[QualificationInference, ...]
    regulation_period_end: tuple[tuple[int, int], ...]


def _verify_source(target: TargetGame, capture: RetainedCapture, endpoint: str) -> None:
    request = capture.request
    receipt = capture.receipt
    if request.endpoint != endpoint:
        raise _invalid("endpoint-mismatch", f"expected {endpoint} retained response")
    params = request.parameter_map
    if params.get("year") != target.season:
        raise _invalid("season-filter-mismatch", "request year differs from target season")
    if params.get("seasonType") != target.season_type:
        raise _invalid("phase-filter-mismatch", "request seasonType differs from target phase")
    if params.get("classification") not in {None, "fbs"}:
        raise _invalid("classification-filter-mismatch", "request classification is not fbs")
    if endpoint == "/plays":
        if params.get("week") != target.provider_week:
            raise _invalid("week-filter-mismatch", "plays request week differs from target provider week")
        team = params.get("team")
        if team is not None and team not in {target.home_team, target.away_team}:
            raise _invalid("team-filter-mismatch", "plays request team is not a target participant")
    elif request.expected_game_id is not None and request.expected_game_id != target.game_id:
        raise _invalid("game-filter-mismatch", "games request is bound to a different game")
    if request.parent_snapshot_path != target.parent_snapshot_path:
        raise _invalid("source-binding-mismatch", "request parent path differs from target qualification")
    if request.parent_snapshot_sha256 != target.parent_snapshot_sha256:
        raise _invalid("source-binding-mismatch", "request parent digest differs from target qualification")
    if request.parent_snapshot_checksum != target.parent_snapshot_checksum:
        raise _invalid("source-binding-mismatch", "request parent checksum differs from target qualification")
    if receipt.source_archive_sha256 != target.source_archive_sha256:
        raise _invalid("source-binding-mismatch", "receipt archive differs from target qualification")


def _parse_line_scores(value: object, field_name: str) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise _invalid("invalid-line-scores", f"{field_name} must be a non-empty array")
    scores: list[int] = []
    for score in value:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or score < 0 or int(score) != score:
            raise _invalid("invalid-line-scores", f"{field_name} contains a non-integer score")
        scores.append(int(score))
    return tuple(scores)


def _game_evidence(target: TargetGame, capture: RetainedCapture) -> _GameEvidence:
    rows = capture.decode_array()
    matches = [row for row in rows if _decimal_game_id(row.get("id"), "games.id") == target.game_id]
    if len(matches) != 1:
        raise _invalid("game-row-cardinality", "games response must contain exactly one target row")
    row = matches[0]
    if row.get("season") != target.season or row.get("seasonType") != target.season_type:
        raise _invalid("game-metadata-mismatch", "games row season or phase differs from target")
    if row.get("week") != target.provider_week:
        raise _invalid("game-metadata-mismatch", "games row week differs from target")
    if row.get("homeTeam") != target.home_team or row.get("awayTeam") != target.away_team:
        raise _invalid("orientation-mismatch", "games row home/away orientation differs from target")
    completed = row.get("completed")
    if not isinstance(completed, bool):
        raise _invalid("invalid-completion", "games row completed must be boolean")
    home_score = _integer(row.get("homePoints"), "homePoints")
    away_score = _integer(row.get("awayPoints"), "awayPoints")
    classifications_known = row.get("homeClassification") == "fbs" and row.get("awayClassification") == "fbs"
    home_lines = _parse_line_scores(row.get("homeLineScores"), "homeLineScores")
    away_lines = _parse_line_scores(row.get("awayLineScores"), "awayLineScores")
    if (home_lines is None) != (away_lines is None):
        raise _invalid("invalid-line-scores", "home and away line scores must be present together")
    quarters = None
    regulation_score = None
    overtime = None
    if home_lines is not None and away_lines is not None:
        if len(home_lines) != len(away_lines):
            raise _invalid("invalid-line-scores", "home and away line-score lengths differ")
        if len(home_lines) < 4:
            raise _invalid("shortened-line-scores", "line scores do not contain four regulation periods")
        if sum(home_lines) != home_score or sum(away_lines) != away_score:
            raise _invalid("final-line-score-mismatch", "line scores do not sum to the final")
        cumulative = []
        for boundary in range(1, 4):
            cumulative.append((sum(home_lines[:boundary]), sum(away_lines[:boundary])))
        quarters = tuple(cumulative)  # type: ignore[assignment]
        regulation_score = (sum(home_lines[:4]), sum(away_lines[:4]))
        overtime = len(home_lines) > 4
    return _GameEvidence(
        home_score,
        away_score,
        quarters,
        regulation_score,
        overtime,
        classifications_known,
        completed,
    )


def _play_score(row: Mapping[str, object], target: TargetGame) -> tuple[int, int]:
    if row.get("home") != target.home_team or row.get("away") != target.away_team:
        raise _invalid("orientation-mismatch", "play row home/away orientation differs from target")
    offense = row.get("offense")
    defense = row.get("defense")
    offense_score = _integer(row.get("offenseScore"), "offenseScore")
    defense_score = _integer(row.get("defenseScore"), "defenseScore")
    if offense == target.home_team and defense == target.away_team:
        return offense_score, defense_score
    if offense == target.away_team and defense == target.home_team:
        return defense_score, offense_score
    raise _invalid("orientation-mismatch", "play offense/defense do not match target participants")


def _play_evidence(
    target: TargetGame,
    qualification: CaptureQualification,
    capture: RetainedCapture,
    game: _GameEvidence,
) -> _PlayEvidence:
    rows = capture.decode_array()
    selected: list[Mapping[str, object]] = []
    for row in rows:
        raw_game_id = row.get("gameId")
        if raw_game_id is None:
            raise _invalid("missing-game-id", "play row lacks gameId")
        if _decimal_game_id(raw_game_id, "plays.gameId") == target.game_id:
            selected.append(row)
    if not selected:
        return _PlayEvidence((), (QualificationReason.PLAYS_EMPTY,), (), ())

    identities: set[str] = set()
    order_keys: set[tuple[int, int]] = set()
    keyed: list[tuple[tuple[int, int], Mapping[str, object]]] = []
    for row in selected:
        play_id = _text(row.get("id"), "plays.id")
        if play_id in identities:
            raise _invalid("duplicate-play-id", "target plays contain a duplicate provider play id")
        identities.add(play_id)
        drive = row.get("driveNumber")
        play = row.get("playNumber")
        if drive is None or play is None:
            return _PlayEvidence((), (QualificationReason.ORDER_EVIDENCE_MISSING,), (), ())
        key = (_integer(drive, "driveNumber", minimum=1), _integer(play, "playNumber", minimum=1))
        if key in order_keys:
            raise _invalid("ambiguous-play-order", "two target plays share one drive/play order key")
        order_keys.add(key)
        keyed.append((key, row))
    keyed.sort(key=lambda pair: pair[0])

    # Orientation and score field shape are observable even when the caller has
    # not qualified the provider's score timing semantics.  Do not apply
    # after-play trajectory rules in that state: doing so could erase an
    # independently verified final merely because the unknown interpretation
    # differs from the after-play policy.
    if qualification.score_semantics != "after_play":
        for _key, row in keyed:
            _play_score(row, target)
        return _PlayEvidence((), (QualificationReason.AFTER_PLAY_UNKNOWN,), (), ())

    points: list[TimelinePoint] = []
    point_periods: list[int] = []
    prior_period = 0
    prior_remaining: int | None = None
    prior_scores = (0, 0)
    period_ends: dict[int, tuple[int, int]] = {}
    period_ranges: dict[int, list[int]] = {}
    reasons: set[QualificationReason] = set()
    inferences: set[QualificationInference] = set()
    for sequence, (_key, row) in enumerate(keyed):
        period = _integer(row.get("period"), "period", minimum=1)
        if period < prior_period:
            raise _invalid("period-inversion", "candidate play order decreases period")
        clock = row.get("clock")
        if not isinstance(clock, Mapping):
            return _PlayEvidence((), (QualificationReason.CLOCK_EVIDENCE_MISSING,), (), ())
        minutes = clock.get("minutes")
        seconds = clock.get("seconds")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or isinstance(seconds, bool)
            or not isinstance(seconds, int)
            or not 0 <= minutes <= 15
            or not 0 <= seconds < 60
        ):
            raise _invalid("invalid-clock", "play clock is outside a regulation quarter")
        remaining = minutes * 60 + seconds
        if remaining > 900:
            raise _invalid("invalid-clock", "play clock exceeds fifteen minutes")
        if period <= 4:
            if period == prior_period and prior_remaining is not None and remaining > prior_remaining:
                raise _invalid("clock-inversion", "candidate play order increases the clock within a period")
            elapsed = (period - 1) * 15 + (900 - remaining) / 60
            overtime = False
            period_ranges.setdefault(period, []).append(remaining)
        else:
            elapsed = None
            overtime = True
        scores = _play_score(row, target)
        if scores[0] < prior_scores[0] or scores[1] < prior_scores[1]:
            raise _invalid("score-decrease", "candidate play order decreases a score")
        if scores[0] > prior_scores[0] and scores[1] > prior_scores[1]:
            raise _invalid(
                "aggregate-score-transition",
                "one event cannot increase both team scores under the after-play policy",
            )
        scoring = row.get("scoring")
        if not isinstance(scoring, bool):
            return _PlayEvidence((), (QualificationReason.PLAY_FIELDS_MISSING,), (), ())
        if scores != prior_scores and not scoring:
            raise _invalid("after-play-contradiction", "score changes on a row not marked scoring")
        points.append(TimelinePoint(scores[0], scores[1], sequence, elapsed, overtime))
        point_periods.append(period)
        if period <= 4:
            period_ends[period] = scores
        prior_period = period
        prior_remaining = remaining
        prior_scores = scores

    capture_complete = qualification.capture_completeness == "complete"
    if prior_scores != (game.home_score, game.away_score) and capture_complete:
        raise _invalid("terminal-score-mismatch", "complete ordered plays do not reach the games final")
    regulation_periods = set(period_ranges)
    if regulation_periods != {1, 2, 3, 4}:
        reasons.add(QualificationReason.PERIOD_COVERAGE_INCOMPLETE)
    else:
        if max(period_ranges[1]) != 900:
            reasons.add(QualificationReason.REGULATION_BOUNDARY_MISSING)
        if any(min(period_ranges[p]) != 0 for p in (1, 2, 3, 4)):
            inferences.add(QualificationInference.QUARTER_END_CARRY)
        if any(max(period_ranges[p]) != 900 for p in (2, 3, 4)):
            inferences.add(QualificationInference.QUARTER_START_CARRY)
    if capture_complete and points and (points[0].home_score, points[0].away_score) != (0, 0):
        raise _invalid("initial-score-mismatch", "ordered plays do not begin 0-0")
    if capture_complete and points and (points[0].elapsed_minute != 0):
        reasons.add(QualificationReason.REGULATION_BOUNDARY_MISSING)
    if game.quarter_scores is not None and len(period_ends) >= 3:
        for period, expected in enumerate(game.quarter_scores, start=1):
            endpoint_observed = period in period_ranges and min(period_ranges[period]) == 0
            if (capture_complete or endpoint_observed) and period_ends.get(period) != expected:
                raise _invalid("quarter-score-mismatch", "ordered plays disagree with games line scores")
    regulation_endpoint_observed = 4 in period_ranges and min(period_ranges[4]) == 0
    if (
        game.regulation_score is not None
        and (capture_complete or regulation_endpoint_observed)
        and period_ends.get(4) != game.regulation_score
    ):
        raise _invalid("regulation-score-mismatch", "ordered plays disagree at regulation end")
    # The accepted policy permits a qualified constant-score carry to a
    # quarter endpoint without a literal provider row at 0:00.  Materialize
    # that derived boundary for the scorer while recording the inference and
    # only after matching the independently captured quarter checkpoint.
    endpoints: dict[int, tuple[int, int]] = {}
    if capture_complete and game.quarter_scores is not None:
        endpoints.update({index: scores for index, scores in enumerate(game.quarter_scores, start=1)})
    if capture_complete and game.regulation_score is not None:
        endpoints[4] = game.regulation_score
    expanded: list[TimelinePoint] = []
    for index, point in enumerate(points):
        expanded.append(point)
        period = point_periods[index]
        next_period = point_periods[index + 1] if index + 1 < len(point_periods) else None
        if period <= 4 and next_period != period and period in endpoints:
            boundary = float(period * 15)
            if point.elapsed_minute != boundary:
                expected = endpoints[period]
                if (point.home_score, point.away_score) != expected:
                    raise _invalid("quarter-score-mismatch", "constant-score carry lacks quarter support")
                expanded.append(TimelinePoint(expected[0], expected[1], 0, boundary, False))
                inferences.add(QualificationInference.QUARTER_END_CARRY)
    points = [
        TimelinePoint(point.home_score, point.away_score, sequence, point.elapsed_minute, point.overtime)
        for sequence, point in enumerate(expanded)
    ]
    return _PlayEvidence(
        tuple(points), tuple(sorted(reasons, key=lambda reason: reason.value)),
        tuple(sorted(inferences, key=lambda inference: inference.value)),
        tuple(period_ends[period] for period in sorted(period_ends)),
    )


def _source_id(
    target: TargetGame,
    qualification: CaptureQualification,
    games: RetainedCapture,
    plays: RetainedCapture,
) -> str:
    value = {
        "game_id": target.game_id,
        "season": target.season,
        "orientation": hashlib.sha256(
            (target.home_team + "\0" + target.away_team).encode()
        ).hexdigest(),
        "target_assertions": {
            "season_type": target.season_type,
            "provider_week": target.provider_week,
            "completion": target.completion,
            "game_format": target.game_format,
            "parent_snapshot_path": target.parent_snapshot_path,
            "parent_snapshot_checksum": target.parent_snapshot_checksum,
        },
        "target_qualification_id": target.qualification_id,
        "games_manifest_sha256": target.games_manifest_sha256,
        "capture_qualification_id": qualification.qualification_id,
        "capture_policy_id": qualification.policy_id,
        "capture_policy_version": qualification.policy_version,
        "capture_policy_sha256": qualification.policy_sha256,
        "capture_evidence_reference": qualification.evidence_reference,
        "capture_policy": {
            "id": qualification.policy_id,
            "version": qualification.policy_version,
            "sha256": qualification.policy_sha256,
            "evidence_reference": qualification.evidence_reference,
        },
        "capture_assertions": {
            "score_semantics": qualification.score_semantics,
            "completeness": qualification.capture_completeness,
            "regulation_minutes": qualification.regulation_minutes,
            "overtime": qualification.overtime,
        },
        "plays_manifest_sha256": qualification.plays_manifest_sha256,
        "archive": target.source_archive_sha256,
        "parent_snapshot": target.parent_snapshot_sha256,
        "games": games.receipt.public_receipt(),
        "plays": plays.receipt.public_receipt(),
    }
    return hashlib.sha256(_canonical(value)).hexdigest()


def _make_artifact(
    *,
    status: Literal["full", "reduced", "insufficient"],
    reasons: Sequence[QualificationReason],
    inferences: Sequence[QualificationInference],
    final: QualifiedFinal | None,
    timeline: tuple[TimelinePoint, ...],
    normalized: NormalizedTimeline | None,
    source_id: str,
    target: TargetGame,
    games: RetainedCapture,
    plays: RetainedCapture,
    qualification: CaptureQualification,
) -> QualificationArtifact:
    kwargs = {
        "status": status,
        "reasons": tuple(sorted(set(reasons), key=lambda reason: reason.value)),
        "inferences": tuple(sorted(set(inferences), key=lambda inference: inference.value)),
        "final": final,
        "timeline": timeline,
        "normalized": normalized,
        "source_id": source_id,
        "game_id": target.game_id,
        "season": target.season,
        "games_request_id": games.request.request_id,
        "games_response_sha256": games.receipt.response.sha256,
        "games_manifest_sha256": target.games_manifest_sha256,
        "plays_request_id": plays.request.request_id,
        "plays_response_sha256": plays.receipt.response.sha256,
        "plays_manifest_sha256": qualification.plays_manifest_sha256,
        "target_qualification_id": target.qualification_id,
        "capture_qualification_id": qualification.qualification_id,
        "capture_policy_id": qualification.policy_id,
        "capture_policy_version": qualification.policy_version,
        "capture_policy_sha256": qualification.policy_sha256,
        "capture_evidence_reference": qualification.evidence_reference,
        "capture_score_semantics": qualification.score_semantics,
        "capture_completeness": qualification.capture_completeness,
        "capture_regulation_minutes": qualification.regulation_minutes,
        "capture_overtime": qualification.overtime,
        "target_completion": target.completion,
        "target_game_format": target.game_format,
    }
    provisional = QualificationArtifact(artifact_id="0" * 64, _token=_ARTIFACT_TOKEN, **kwargs)
    artifact_id = hashlib.sha256(_canonical(provisional._content())).hexdigest()
    return QualificationArtifact(artifact_id=artifact_id, _token=_ARTIFACT_TOKEN, **kwargs)


def qualify_excitement(
    *,
    target: TargetGame,
    qualification: CaptureQualification,
    games: RetainedCapture,
    plays: RetainedCapture,
) -> QualificationArtifact:
    """Qualify exact retained responses into scoring-v1 inputs or reasons.

    Receipt and structural validation can falsify a caller qualification.  It
    cannot promote unknown source semantics to complete/normal evidence.
    """

    if qualification.game_id != target.game_id:
        raise _invalid("qualification-game-mismatch", "capture qualification is for another game")
    if qualification.qualification_id == target.qualification_id:
        raise _invalid("qualification-identity-alias", "target and capture qualifications must be distinct")
    if qualification.plays_response_sha256 != plays.receipt.response.sha256:
        raise _invalid("qualification-response-mismatch", "capture qualification is for other plays bytes")
    if qualification.source_archive_sha256 != target.source_archive_sha256:
        raise _invalid("qualification-source-mismatch", "capture archive differs from target archive")
    if qualification.parent_snapshot_sha256 != target.parent_snapshot_sha256:
        raise _invalid("qualification-source-mismatch", "capture parent differs from target parent")
    if games.receipt.manifest_sha256 != target.games_manifest_sha256:
        raise _invalid("manifest-binding-mismatch", "games receipt differs from the reviewed manifest")
    if plays.receipt.manifest_sha256 != qualification.plays_manifest_sha256:
        raise _invalid("manifest-binding-mismatch", "plays receipt differs from the reviewed manifest")
    _verify_source(target, games, "/games")
    _verify_source(target, plays, "/plays")
    game = _game_evidence(target, games)
    source_id = _source_id(target, qualification, games, plays)

    reduced_reasons: list[QualificationReason] = []
    if target.completion == "unknown":
        reduced_reasons.append(QualificationReason.COMPLETION_UNKNOWN)
    elif target.completion == "incomplete" or not game.provider_completed:
        reduced_reasons.append(QualificationReason.GAME_INCOMPLETE)
    elif not game.provider_completed:
        raise _invalid("completion-mismatch", "qualified completed game is incomplete in provider metadata")
    if target.completion == "completed" and not game.provider_completed:
        raise _invalid("completion-mismatch", "qualified completed game is incomplete in provider metadata")
    if target.game_format == "unknown":
        reduced_reasons.append(QualificationReason.FORMAT_UNKNOWN)
    elif target.game_format == "unsupported":
        reduced_reasons.append(QualificationReason.FORMAT_UNSUPPORTED)
    if not game.classifications_known:
        reduced_reasons.append(QualificationReason.CLASSIFICATION_UNKNOWN)

    can_emit_final = not any(
        reason
        in {
            QualificationReason.COMPLETION_UNKNOWN,
            QualificationReason.GAME_INCOMPLETE,
            QualificationReason.FORMAT_UNKNOWN,
            QualificationReason.FORMAT_UNSUPPORTED,
            QualificationReason.CLASSIFICATION_UNKNOWN,
        }
        for reason in reduced_reasons
    )
    if not can_emit_final:
        return _make_artifact(
            status="insufficient", reasons=reduced_reasons, inferences=(), final=None, timeline=(), normalized=None,
            source_id=source_id, target=target, games=games, plays=plays, qualification=qualification,
        )

    if qualification.overtime is not None and game.overtime_from_lines is not None:
        if qualification.overtime != game.overtime_from_lines:
            raise _invalid("overtime-mismatch", "capture qualification disagrees with games line scores")
    if qualification.overtime is True and game.regulation_score is not None:
        if game.regulation_score[0] != game.regulation_score[1]:
            raise _invalid("overtime-regulation-mismatch", "overtime game is not tied after regulation")
    final = QualifiedFinal(
        game_id=target.game_id,
        season=target.season,
        home_score=game.home_score,
        away_score=game.away_score,
        source_snapshot_id=source_id,
        verified_completed=True,
        normal_format=True,
        overtime=qualification.overtime,
        quarter_scores=game.quarter_scores,
    )
    plays_evidence = _play_evidence(target, qualification, plays, game)
    full_reasons = list(plays_evidence.reasons)
    if game.quarter_scores is None:
        full_reasons.append(QualificationReason.QUARTERS_UNAVAILABLE)
    if qualification.score_semantics != "after_play":
        full_reasons.append(QualificationReason.AFTER_PLAY_UNKNOWN)
    if qualification.capture_completeness == "partial":
        full_reasons.append(QualificationReason.CAPTURE_PARTIAL)
    elif qualification.capture_completeness == "unknown":
        full_reasons.append(QualificationReason.CAPTURE_COMPLETENESS_UNKNOWN)
    if qualification.regulation_minutes != 60:
        full_reasons.append(QualificationReason.REGULATION_DURATION_UNKNOWN)
    if qualification.overtime is None:
        full_reasons.append(QualificationReason.OVERTIME_UNKNOWN)

    if not full_reasons:
        try:
            normalized = normalize_timeline(plays_evidence.timeline, final)
        except TimelineQualificationError as exc:
            raise _invalid("timeline-contract-mismatch", str(exc)) from exc
        return _make_artifact(
            status="full", reasons=(), inferences=plays_evidence.inferences,
            final=final, timeline=plays_evidence.timeline,
            normalized=normalized, source_id=source_id, target=target, games=games,
            plays=plays, qualification=qualification,
        )
    return _make_artifact(
        status="reduced", reasons=full_reasons, inferences=plays_evidence.inferences,
        final=final, timeline=(), normalized=None,
        source_id=source_id, target=target, games=games, plays=plays, qualification=qualification,
    )


__all__ = [
    "CaptureQualification",
    "InsufficientEvidenceError",
    "InvalidEvidenceError",
    "QualificationArtifact",
    "QualificationError",
    "QualificationInference",
    "QualificationReason",
    "RetainedCapture",
    "SCORE_TRAJECTORY_POLICY_ID",
    "SCORE_TRAJECTORY_POLICY_SHA256",
    "SCORE_TRAJECTORY_POLICY_VERSION",
    "TargetGame",
    "qualify_excitement",
]
