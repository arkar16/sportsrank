"""Immutable issued-forecast records and independent grading arithmetic.

Candidates describe forecast content.  Publication receipts describe whether
those exact bytes reached the public site.  Game timing evidence is separate:
neither a scheduled start nor a build timestamp establishes pregame issuance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence


CANDIDATE_SCHEMA = "forecast-candidate/v1"
RECEIPT_SCHEMA = "forecast-publication-receipt/v1"
TIMING_SCHEMA = "game-timing-evidence/v1"
SCORE_SCHEMA = "forecast-final-score/v1"
OWNER_ATTESTATION_SCHEMA = "forecast-owner-attestation/v1"


class ForecastContractError(ValueError):
    """Raised when forecast evidence cannot satisfy the durable contract."""


class ForecastSelection(str, Enum):
    HOME = "home"
    AWAY = "away"
    PICKEM = "pickem"
    LEGACY_UNKNOWN = "legacy_unknown"


class StraightUpResult(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    TIE = "tie"
    UNGRADED = "ungraded"


class CoverageResult(str, Enum):
    COVER = "cover"
    NO_COVER = "no_cover"
    PUSH = "push"
    UNGRADED = "ungraded"


class ForecastDisposition(str, Enum):
    """Mutually exclusive Release-level reasons a scheduled Game is not graded."""

    EVALUATED = "evaluated"
    PENDING = "pending"
    CANCELED = "canceled"
    INELIGIBLE_CLASSIFICATION = "ineligible_classification"
    INVALID_RATING = "invalid_rating"
    MISSING_FORECAST = "missing_forecast"
    UNVERIFIED_PUBLICATION = "unverified_publication"
    UNRESOLVED_TEMPORAL_ORDER = "unresolved_temporal_order"
    MISSING_FINAL_SCORE = "missing_final_score"


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastContractError(f"{field} must be non-empty text")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ForecastContractError(f"{field} must be a lowercase sha256 digest")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ForecastContractError(f"{field} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ForecastContractError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ForecastContractError(f"{field} must be a finite decimal")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f")


def _instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ForecastContractError(f"{field} must be a UTC RFC3339 instant")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ForecastContractError(f"{field} must be a UTC RFC3339 instant") from exc
    if parsed.tzinfo != timezone.utc:
        raise ForecastContractError(f"{field} must be UTC")
    return parsed


def _instant_text(value: datetime) -> str:
    if value.tzinfo != timezone.utc:
        raise ForecastContractError("timestamps must use UTC")
    return value.isoformat().replace("+00:00", "Z")


def _fields(data: Mapping[str, Any], expected: set[str], required: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ForecastContractError(f"{label} must be an object")
    extras = set(data) - expected
    missing = required - set(data)
    if extras:
        raise ForecastContractError(f"{label} has unknown fields: {sorted(extras)!r}")
    if missing:
        raise ForecastContractError(f"{label} is missing fields: {sorted(missing)!r}")


@dataclass(frozen=True)
class EvidenceRef:
    """A pinned, independently retrievable evidence source."""

    kind: str
    reference: str
    digest: str

    def __post_init__(self) -> None:
        _required_text(self.kind, "evidence.kind")
        _required_text(self.reference, "evidence.reference")
        _sha256(self.digest, "evidence.digest")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reference": self.reference, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        _fields(data, {"kind", "reference", "digest"}, {"kind", "reference", "digest"}, "evidence")
        return cls(data["kind"], data["reference"], data["digest"])


@dataclass(frozen=True)
class VersionBinding:
    """Optional version pin available to consumers such as ADR-0021."""

    kind: str
    version_id: str
    digest: str

    def __post_init__(self) -> None:
        _required_text(self.kind, "binding.kind")
        _required_text(self.version_id, "binding.version_id")
        _sha256(self.digest, "binding.digest")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "version_id": self.version_id, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VersionBinding":
        _fields(data, {"kind", "version_id", "digest"}, {"kind", "version_id", "digest"}, "version binding")
        return cls(
            _required_text(data["kind"], "binding.kind"),
            _required_text(data["version_id"], "binding.version_id"),
            _required_text(data["digest"], "binding.digest"),
        )


@dataclass(frozen=True)
class GameIdentity:
    provider_id: str
    season: int
    week: int
    home_team: str
    away_team: str
    home_classification: str
    away_classification: str
    neutral_site: bool

    def __post_init__(self) -> None:
        _required_text(self.provider_id, "game.provider_id")
        if not isinstance(self.season, int) or isinstance(self.season, bool) or self.season < 1:
            raise ForecastContractError("game.season must be positive")
        if not isinstance(self.week, int) or isinstance(self.week, bool) or self.week < 0:
            raise ForecastContractError("game.week must be nonnegative")
        for name in ("home_team", "away_team", "home_classification", "away_classification"):
            _required_text(getattr(self, name), f"game.{name}")
        if self.home_team == self.away_team:
            raise ForecastContractError("game teams must differ")
        if not isinstance(self.neutral_site, bool):
            raise ForecastContractError("game.neutral_site must be boolean")

    @property
    def key(self) -> str:
        return f"{self.season}:{self.provider_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id, "season": self.season, "week": self.week,
            "home": {"name": self.home_team, "classification": self.home_classification},
            "away": {"name": self.away_team, "classification": self.away_classification},
            "neutral_site": self.neutral_site,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GameIdentity":
        names = {"provider_id", "season", "week", "home", "away", "neutral_site"}
        _fields(data, names, names, "game")
        for side in ("home", "away"):
            _fields(data[side], {"name", "classification"}, {"name", "classification"}, f"game.{side}")
        return cls(data["provider_id"], data["season"], data["week"],
                   data["home"]["name"], data["away"]["name"],
                   data["home"]["classification"], data["away"]["classification"], data["neutral_site"])


@dataclass(frozen=True)
class ForecastProvenance:
    rating_checkpoint: str
    rating_cutoff: str
    rating_artifact_digest: str
    source_snapshot_digest: str
    model_version: str
    source_kind: str
    code_revision: str
    home_rating: Decimal
    away_rating: Decimal
    home_field_advantage: Decimal
    home_rank: int | None = None
    away_rank: int | None = None

    def __post_init__(self) -> None:
        for name in ("rating_checkpoint", "rating_cutoff", "rating_artifact_digest", "source_snapshot_digest", "model_version", "source_kind", "code_revision"):
            _required_text(getattr(self, name), f"provenance.{name}")
        for name in ("rating_artifact_digest", "source_snapshot_digest"):
            _sha256(getattr(self, name), f"provenance.{name}")
        for name in ("home_rating", "away_rating", "home_field_advantage"):
            object.__setattr__(self, name, _decimal(getattr(self, name), f"provenance.{name}"))
        for name in ("home_rank", "away_rank"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ForecastContractError(f"provenance.{name} must be a positive integer")

    def to_dict(self) -> dict[str, str]:
        result = {
            "rating_checkpoint": self.rating_checkpoint, "rating_cutoff": self.rating_cutoff,
            "rating_artifact_digest": self.rating_artifact_digest,
            "source_snapshot_digest": self.source_snapshot_digest, "model_version": self.model_version,
            "source_kind": self.source_kind, "code_revision": self.code_revision,
            "home_rating": _decimal_text(self.home_rating), "away_rating": _decimal_text(self.away_rating),
            "home_field_advantage": _decimal_text(self.home_field_advantage),
        }
        if self.home_rank is not None:
            result["home_rank"] = self.home_rank
        if self.away_rank is not None:
            result["away_rank"] = self.away_rank
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ForecastProvenance":
        names = {"rating_checkpoint", "rating_cutoff", "rating_artifact_digest", "source_snapshot_digest", "model_version", "source_kind", "code_revision", "home_rating", "away_rating", "home_field_advantage", "home_rank", "away_rank"}
        _fields(data, names, names - {"home_rank", "away_rank"}, "provenance")
        return cls(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ForecastCandidate:
    game: GameIdentity
    home_margin: Decimal
    home_handicap: Decimal
    precision: int
    selection: ForecastSelection
    provenance: ForecastProvenance
    predecessor_version_id: str | None = None
    replacement_reason: str | None = None
    bindings: tuple[VersionBinding, ...] = ()
    version_id: str = ""
    schema_version: str = CANDIDATE_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        game: GameIdentity,
        home_margin: Any,
        precision: int,
        provenance: ForecastProvenance,
        predecessor_version_id: str | None = None,
        replacement_reason: str | None = None,
        bindings: Sequence[VersionBinding] = (),
        selection: ForecastSelection | None = None,
    ) -> "ForecastCandidate":
        margin = _decimal(home_margin, "forecast.home_margin")
        inferred = ForecastSelection.PICKEM if margin == 0 else (ForecastSelection.HOME if margin > 0 else ForecastSelection.AWAY)
        return cls(
            game=game, home_margin=margin, home_handicap=-margin,
            precision=precision, selection=selection or inferred, provenance=provenance,
            predecessor_version_id=predecessor_version_id,
            replacement_reason=replacement_reason, bindings=tuple(bindings),
        )

    def __post_init__(self) -> None:
        if self.schema_version != CANDIDATE_SCHEMA:
            raise ForecastContractError("unsupported forecast candidate schema")
        object.__setattr__(self, "bindings", tuple(self.bindings))
        margin = _decimal(self.home_margin, "forecast.home_margin")
        handicap = _decimal(self.home_handicap, "forecast.home_handicap")
        object.__setattr__(self, "home_margin", margin)
        object.__setattr__(self, "home_handicap", handicap)
        object.__setattr__(self, "bindings", tuple(self.bindings))
        if not isinstance(self.precision, int) or isinstance(self.precision, bool) or not 0 <= self.precision <= 9:
            raise ForecastContractError("forecast.precision must be between 0 and 9")
        quantum = Decimal(1).scaleb(-self.precision)
        if margin.quantize(quantum) != margin or handicap.quantize(quantum) != handicap:
            raise ForecastContractError("forecast values exceed declared precision")
        if handicap != -margin:
            raise ForecastContractError("home handicap must be the opposite sign of home margin")
        expected = ForecastSelection.PICKEM if margin == 0 else (ForecastSelection.HOME if margin > 0 else ForecastSelection.AWAY)
        if self.selection not in (expected, ForecastSelection.LEGACY_UNKNOWN):
            raise ForecastContractError("forecast selection contradicts home margin")
        if self.selection == ForecastSelection.LEGACY_UNKNOWN and margin != 0:
            raise ForecastContractError("legacy unknown selection is valid only for a zero line")
        if (self.predecessor_version_id is None) != (self.replacement_reason is None):
            raise ForecastContractError("replacement predecessor and reason must appear together")
        content_id = _digest(self._content_dict())
        if self.version_id and self.version_id != content_id:
            raise ForecastContractError("forecast version_id does not match candidate content")
        object.__setattr__(self, "version_id", content_id)

    def _content_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version, "game": self.game.to_dict(),
            "forecast": {"home_margin": _decimal_text(self.home_margin), "home_handicap": _decimal_text(self.home_handicap), "precision": self.precision, "selection": self.selection.value},
            "provenance": self.provenance.to_dict(),
            "bindings": [binding.to_dict() for binding in self.bindings],
        }
        if self.predecessor_version_id is not None:
            result["replacement"] = {"predecessor_version_id": self.predecessor_version_id, "reason": self.replacement_reason}
        return result

    @property
    def artifact_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "version_id": self.version_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ForecastCandidate":
        expected = {"schema_version", "version_id", "game", "forecast", "provenance", "bindings", "replacement"}
        _fields(data, expected, {"schema_version", "version_id", "game", "forecast", "provenance"}, "forecast candidate")
        forecast = data["forecast"]
        if not isinstance(forecast, Mapping):
            raise ForecastContractError("forecast must be an object")
        names = {"home_margin", "home_handicap", "precision", "selection"}
        _fields(forecast, names, names, "forecast")
        replacement = data.get("replacement")
        predecessor = reason = None
        if replacement is not None:
            if not isinstance(replacement, Mapping):
                raise ForecastContractError("replacement must be an object")
            _fields(replacement, {"predecessor_version_id", "reason"}, {"predecessor_version_id", "reason"}, "replacement")
            predecessor, reason = str(replacement["predecessor_version_id"]), str(replacement["reason"])
        bindings = data.get("bindings", [])
        if not isinstance(bindings, list):
            raise ForecastContractError("bindings must be a list")
        return cls(
            game=GameIdentity.from_dict(data["game"]), home_margin=forecast["home_margin"],
            home_handicap=forecast["home_handicap"], precision=forecast["precision"],
            selection=ForecastSelection(forecast["selection"]), provenance=ForecastProvenance.from_dict(data["provenance"]),
            predecessor_version_id=predecessor, replacement_reason=reason,
            bindings=tuple(VersionBinding.from_dict(item) for item in bindings),
            version_id=str(data["version_id"]), schema_version=str(data["schema_version"]),
        )


@dataclass(frozen=True)
class PublicationReceipt:
    receipt_id: str
    candidate_version_id: str
    candidate_artifact_digest: str
    publication_artifact_digest: str
    attempt_id: str
    verification_id: str
    source: EvidenceRef
    provider_published_at: datetime | None = None
    verified_public_by: datetime | None = None
    schema_version: str = RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        for name in ("receipt_id", "candidate_version_id", "candidate_artifact_digest", "publication_artifact_digest", "attempt_id", "verification_id"):
            _required_text(getattr(self, name), f"receipt.{name}")
        for name in ("candidate_version_id", "candidate_artifact_digest", "publication_artifact_digest"):
            _sha256(getattr(self, name), f"receipt.{name}")
        if self.schema_version != RECEIPT_SCHEMA:
            raise ForecastContractError("unsupported publication receipt schema")
        if (self.provider_published_at is None) == (self.verified_public_by is None):
            raise ForecastContractError("receipt requires exactly one publication-time fact")
        for value in (self.provider_published_at, self.verified_public_by):
            if value is not None and value.tzinfo != timezone.utc:
                raise ForecastContractError("receipt timestamps must use UTC")

    @property
    def public_by(self) -> datetime:
        return self.provider_published_at or self.verified_public_by  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version, "receipt_id": self.receipt_id,
            "candidate_version_id": self.candidate_version_id,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "publication_artifact_digest": self.publication_artifact_digest,
            "attempt_id": self.attempt_id, "verification_id": self.verification_id,
            "source": self.source.to_dict(),
        }
        if self.provider_published_at is not None:
            result["provider_published_at"] = _instant_text(self.provider_published_at)
        else:
            result["verified_public_by"] = _instant_text(self.verified_public_by)  # type: ignore[arg-type]
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PublicationReceipt":
        expected = {"schema_version", "receipt_id", "candidate_version_id", "candidate_artifact_digest", "publication_artifact_digest", "attempt_id", "verification_id", "source", "provider_published_at", "verified_public_by"}
        required = expected - {"provider_published_at", "verified_public_by"}
        _fields(data, expected, required, "publication receipt")
        return cls(
            receipt_id=str(data["receipt_id"]), candidate_version_id=str(data["candidate_version_id"]),
            candidate_artifact_digest=str(data["candidate_artifact_digest"]), publication_artifact_digest=str(data["publication_artifact_digest"]),
            attempt_id=str(data["attempt_id"]), verification_id=str(data["verification_id"]),
            source=EvidenceRef.from_dict(data["source"]),
            provider_published_at=_instant(data["provider_published_at"], "provider_published_at") if "provider_published_at" in data else None,
            verified_public_by=_instant(data["verified_public_by"], "verified_public_by") if "verified_public_by" in data else None,
            schema_version=str(data["schema_version"]),
        )


@dataclass(frozen=True)
class GameTimingEvidence:
    game: GameIdentity
    source: EvidenceRef
    actual_started_at: datetime | None = None
    observed_not_started_at: datetime | None = None
    schema_version: str = TIMING_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != TIMING_SCHEMA:
            raise ForecastContractError("unsupported game timing schema")
        if (self.actual_started_at is None) == (self.observed_not_started_at is None):
            raise ForecastContractError("timing evidence requires exactly one supported temporal fact")
        for value in (self.actual_started_at, self.observed_not_started_at):
            if value is not None and value.tzinfo != timezone.utc:
                raise ForecastContractError("game timing timestamps must use UTC")

    def to_dict(self) -> dict[str, Any]:
        result = {"schema_version": self.schema_version, "game": self.game.to_dict(), "source": self.source.to_dict()}
        if self.actual_started_at is not None:
            result["actual_started_at"] = _instant_text(self.actual_started_at)
        else:
            result["observed_not_started_at"] = _instant_text(self.observed_not_started_at)  # type: ignore[arg-type]
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GameTimingEvidence":
        expected = {"schema_version", "game", "source", "actual_started_at", "observed_not_started_at"}
        _fields(data, expected, {"schema_version", "game", "source"}, "game timing evidence")
        return cls(
            game=GameIdentity.from_dict(data["game"]), source=EvidenceRef.from_dict(data["source"]),
            actual_started_at=_instant(data["actual_started_at"], "actual_started_at") if "actual_started_at" in data else None,
            observed_not_started_at=_instant(data["observed_not_started_at"], "observed_not_started_at") if "observed_not_started_at" in data else None,
            schema_version=str(data["schema_version"]),
        )


@dataclass(frozen=True)
class OwnerAttestation:
    """Owner-confirmed evidence for a retained pre-contract forecast.

    This record deliberately carries no provider publication event or invented
    timestamp.  The retained page and the owner's bounded confirmation are
    separate, pinned evidence sources; the attestation only establishes that
    the candidate was issued before kickoff.
    """

    candidate_version_id: str
    candidate_artifact_digest: str
    source: EvidenceRef
    attestation: EvidenceRef
    attestation_id: str = ""
    schema_version: str = OWNER_ATTESTATION_SCHEMA

    def __post_init__(self) -> None:
        _sha256(self.candidate_version_id, "owner_attestation.candidate_version_id")
        _sha256(self.candidate_artifact_digest, "owner_attestation.candidate_artifact_digest")
        if self.schema_version != OWNER_ATTESTATION_SCHEMA:
            raise ForecastContractError("unsupported owner attestation schema")
        _required_text(self.source.kind, "owner_attestation.source.kind")
        _required_text(self.attestation.kind, "owner_attestation.attestation.kind")
        content = {
            "schema_version": self.schema_version,
            "candidate_version_id": self.candidate_version_id,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "source": self.source.to_dict(),
            "attestation": self.attestation.to_dict(),
        }
        expected = _digest(content)
        if self.attestation_id and self.attestation_id != expected:
            raise ForecastContractError("owner attestation_id does not match content")
        object.__setattr__(self, "attestation_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "attestation_id": self.attestation_id,
            "candidate_version_id": self.candidate_version_id,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "source": self.source.to_dict(),
            "attestation": self.attestation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OwnerAttestation":
        names = {
            "schema_version", "attestation_id", "candidate_version_id",
            "candidate_artifact_digest", "source", "attestation",
        }
        _fields(data, names, names, "owner attestation")
        return cls(
            candidate_version_id=str(data["candidate_version_id"]),
            candidate_artifact_digest=str(data["candidate_artifact_digest"]),
            source=EvidenceRef.from_dict(data["source"]),
            attestation=EvidenceRef.from_dict(data["attestation"]),
            attestation_id=str(data["attestation_id"]),
            schema_version=str(data["schema_version"]),
        )


def receipt_qualifies(candidate: ForecastCandidate, receipt: PublicationReceipt, timing: GameTimingEvidence) -> bool:
    """Return whether pinned facts establish exact candidate publication pregame."""

    if timing.game != candidate.game:
        return False
    if receipt.candidate_version_id != candidate.version_id or receipt.candidate_artifact_digest != candidate.artifact_digest:
        return False
    if timing.actual_started_at is not None:
        return receipt.public_by < timing.actual_started_at
    return receipt.public_by <= timing.observed_not_started_at  # type: ignore[operator]


def select_graded_forecast(
    candidates: Sequence[ForecastCandidate],
    receipts: Sequence[PublicationReceipt],
    timing: GameTimingEvidence | None = None,
    owner_attestations: Sequence[OwnerAttestation] = (),
) -> ForecastCandidate | None:
    """Select the last qualifying candidate along one explicit replacement chain.

    Owner attestations qualify retained candidates without fabricating a
    publication timestamp.  A sentinel ordering value is used only to choose
    a later automated replacement; it is never serialized as evidence.
    """

    by_id = {candidate.version_id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ForecastContractError("duplicate candidate versions")
    if timing is not None and any(candidate.game != timing.game for candidate in candidates):
        raise ForecastContractError("candidate Game identity does not match timing evidence")
    qualifying: dict[str, datetime] = {}
    owner_order = datetime.min.replace(tzinfo=timezone.utc)
    for attestation in owner_attestations:
        candidate = by_id.get(attestation.candidate_version_id)
        if candidate is None:
            continue
        if attestation.candidate_artifact_digest != candidate.artifact_digest:
            raise ForecastContractError("owner attestation does not bind the exact candidate bytes")
        qualifying[candidate.version_id] = owner_order
    for receipt in receipts:
        candidate = by_id.get(receipt.candidate_version_id)
        if candidate is not None and timing is not None and receipt_qualifies(candidate, receipt, timing):
            # Re-serving unchanged bytes cannot move an ancestor's issuance
            # after its explicit correction.
            qualifying[candidate.version_id] = min(qualifying.get(candidate.version_id, receipt.public_by), receipt.public_by)
    roots = [candidate for candidate in candidates if candidate.predecessor_version_id is None and candidate.version_id in qualifying]
    if not roots:
        return None
    if len(roots) != 1:
        raise ForecastContractError("multiple unrelated qualifying original forecasts")
    selected = roots[0]
    seen = {selected.version_id}
    while True:
        children = [candidate for candidate in candidates if candidate.predecessor_version_id == selected.version_id and candidate.version_id in qualifying]
        if not children:
            return selected
        if len(children) != 1:
            raise ForecastContractError("multiple qualifying sibling replacements")
        child = children[0]
        if child.version_id in seen or qualifying[child.version_id] <= qualifying[selected.version_id]:
            raise ForecastContractError("replacement chain is cyclic or not published after its predecessor")
        selected, seen = child, seen | {child.version_id}


@dataclass(frozen=True)
class ScoreRevision:
    home_points: int
    away_points: int
    observed_at: datetime
    source: EvidenceRef
    predecessor_revision_id: str | None = None
    revision_id: str = ""

    def __post_init__(self) -> None:
        if type(self.home_points) is not int or type(self.away_points) is not int or self.home_points < 0 or self.away_points < 0:
            raise ForecastContractError("final scores must be nonnegative integers")
        if self.observed_at.tzinfo != timezone.utc:
            raise ForecastContractError("score revision timestamp must use UTC")
        content = {"home_points": self.home_points, "away_points": self.away_points, "observed_at": _instant_text(self.observed_at), "source": self.source.to_dict(), "predecessor_revision_id": self.predecessor_revision_id}
        expected = _digest(content)
        if self.revision_id and self.revision_id != expected:
            raise ForecastContractError("score revision_id does not match content")
        object.__setattr__(self, "revision_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return {"home_points": self.home_points, "away_points": self.away_points, "observed_at": _instant_text(self.observed_at), "source": self.source.to_dict(), "predecessor_revision_id": self.predecessor_revision_id, "revision_id": self.revision_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScoreRevision":
        names = {"home_points", "away_points", "observed_at", "source", "predecessor_revision_id", "revision_id"}
        _fields(data, names, names - {"predecessor_revision_id"}, "score revision")
        return cls(data["home_points"], data["away_points"], _instant(data["observed_at"], "observed_at"), EvidenceRef.from_dict(data["source"]), data.get("predecessor_revision_id"), str(data["revision_id"]))


@dataclass(frozen=True)
class FinalScore:
    game: GameIdentity
    revisions: tuple[ScoreRevision, ...]
    schema_version: str = SCORE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != SCORE_SCHEMA or not self.revisions:
            raise ForecastContractError("final score requires a supported schema and revision")
        object.__setattr__(self, "revisions", tuple(self.revisions))
        previous = None
        observed_at = None
        for revision in self.revisions:
            if revision.predecessor_revision_id != previous:
                raise ForecastContractError("score correction history is not a contiguous chain")
            if observed_at is not None and revision.observed_at <= observed_at:
                raise ForecastContractError("score correction observations must increase")
            previous = revision.revision_id
            observed_at = revision.observed_at

    @property
    def current(self) -> ScoreRevision:
        return self.revisions[-1]

    def corrected(self, home_points: int, away_points: int, observed_at: datetime, source: EvidenceRef) -> "FinalScore":
        revision = ScoreRevision(home_points, away_points, observed_at, source, self.current.revision_id)
        if revision.observed_at <= self.current.observed_at:
            raise ForecastContractError("score correction must be observed after its predecessor")
        return replace(self, revisions=self.revisions + (revision,))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "game": self.game.to_dict(), "revisions": [item.to_dict() for item in self.revisions]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FinalScore":
        _fields(data, {"schema_version", "game", "revisions"}, {"schema_version", "game", "revisions"}, "final score")
        if not isinstance(data["revisions"], list):
            raise ForecastContractError("final score revisions must be a list")
        return cls(GameIdentity.from_dict(data["game"]), tuple(ScoreRevision.from_dict(item) for item in data["revisions"]), str(data["schema_version"]))


@dataclass(frozen=True)
class ForecastGrade:
    game: GameIdentity
    forecast_version_id: str
    predicted_home_margin: Decimal
    actual_home_margin: Decimal
    selection: ForecastSelection
    straight_up: StraightUpResult
    coverage: CoverageResult
    absolute_error: Decimal
    squared_error: Decimal
    score_revision_id: str
    score_corrected_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": self.game.to_dict(), "forecast_version_id": self.forecast_version_id,
            "predicted_home_margin": _decimal_text(self.predicted_home_margin),
            "actual_home_margin": _decimal_text(self.actual_home_margin),
            "selection": self.selection.value, "straight_up": self.straight_up.value,
            "coverage": self.coverage.value, "absolute_error": _decimal_text(self.absolute_error),
            "squared_error": _decimal_text(self.squared_error), "score_revision_id": self.score_revision_id,
            "score_corrected_at": _instant_text(self.score_corrected_at) if self.score_corrected_at else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ForecastGrade":
        names = {
            "game", "forecast_version_id", "predicted_home_margin",
            "actual_home_margin", "selection", "straight_up", "coverage",
            "absolute_error", "squared_error", "score_revision_id",
            "score_corrected_at",
        }
        _fields(data, names, names, "forecast grade")
        return cls(
            game=GameIdentity.from_dict(data["game"]),
            forecast_version_id=_required_text(data["forecast_version_id"], "grade.forecast_version_id"),
            predicted_home_margin=_decimal(data["predicted_home_margin"], "grade.predicted_home_margin"),
            actual_home_margin=_decimal(data["actual_home_margin"], "grade.actual_home_margin"),
            selection=ForecastSelection(data["selection"]),
            straight_up=StraightUpResult(data["straight_up"]),
            coverage=CoverageResult(data["coverage"]),
            absolute_error=_decimal(data["absolute_error"], "grade.absolute_error"),
            squared_error=_decimal(data["squared_error"], "grade.squared_error"),
            score_revision_id=_required_text(data["score_revision_id"], "grade.score_revision_id"),
            score_corrected_at=_instant(data["score_corrected_at"], "score_corrected_at") if data["score_corrected_at"] is not None else None,
        )


def grade_forecast(candidate: ForecastCandidate, final_score: FinalScore) -> ForecastGrade:
    if candidate.game != final_score.game:
        raise ForecastContractError("forecast and final score Game identities differ")
    revision = final_score.current
    actual = Decimal(revision.home_points - revision.away_points)
    error = abs(actual - candidate.home_margin)
    if candidate.selection in (ForecastSelection.PICKEM, ForecastSelection.LEGACY_UNKNOWN):
        straight_up, coverage = StraightUpResult.UNGRADED, CoverageResult.UNGRADED
    elif actual == 0:
        straight_up, coverage = StraightUpResult.TIE, CoverageResult.PUSH if abs(candidate.home_margin) == 0 else CoverageResult.NO_COVER
    else:
        selected_home = candidate.selection == ForecastSelection.HOME
        straight_up = StraightUpResult.CORRECT if (actual > 0) == selected_home else StraightUpResult.INCORRECT
        oriented_actual = actual if selected_home else -actual
        line = abs(candidate.home_margin)
        coverage = CoverageResult.COVER if oriented_actual > line else (CoverageResult.PUSH if oriented_actual == line else CoverageResult.NO_COVER)
    return ForecastGrade(
        candidate.game, candidate.version_id, candidate.home_margin, actual, candidate.selection,
        straight_up, coverage, error, error * error, revision.revision_id,
        revision.observed_at if len(final_score.revisions) > 1 else None,
    )


@dataclass(frozen=True)
class ForecastAggregate:
    game_count: int
    margin_count: int
    mae: Decimal | None
    rmse: Decimal | None
    straight_up_wins: int
    straight_up_losses: int
    straight_up_ties: int
    straight_up_count: int
    covers: int
    no_covers: int
    pushes: int
    coverage_count: int
    coverage_percentage: Decimal | None
    pickems: int
    unknown_selections: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_count": self.game_count, "margin_count": self.margin_count,
            "mae": _decimal_text(self.mae) if self.mae is not None else None,
            "rmse": _decimal_text(self.rmse) if self.rmse is not None else None,
            "straight_up_wins": self.straight_up_wins, "straight_up_losses": self.straight_up_losses,
            "straight_up_ties": self.straight_up_ties, "straight_up_count": self.straight_up_count,
            "covers": self.covers, "no_covers": self.no_covers, "pushes": self.pushes,
            "coverage_count": self.coverage_count,
            "coverage_percentage": _decimal_text(self.coverage_percentage) if self.coverage_percentage is not None else None,
            "pickems": self.pickems, "unknown_selections": self.unknown_selections,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ForecastAggregate":
        names = {
            "game_count", "margin_count", "mae", "rmse",
            "straight_up_wins", "straight_up_losses", "straight_up_ties",
            "straight_up_count", "covers", "no_covers", "pushes",
            "coverage_count", "coverage_percentage", "pickems",
            "unknown_selections",
        }
        _fields(data, names, names, "forecast aggregate")
        integer_names = names - {"mae", "rmse", "coverage_percentage"}
        values: dict[str, Any] = {}
        for name in integer_names:
            value = data[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ForecastContractError(f"aggregate.{name} must be a nonnegative integer")
            values[name] = value
        for name in ("mae", "rmse", "coverage_percentage"):
            values[name] = None if data[name] is None else _decimal(data[name], f"aggregate.{name}")
        return cls(**values)


def aggregate_grades(grades: Iterable[ForecastGrade]) -> ForecastAggregate:
    rows = list(grades)
    if len({row.game.key for row in rows}) != len(rows):
        raise ForecastContractError("a Game may contribute at most once to aggregates")
    margin_count = len(rows)
    mae = sum((row.absolute_error for row in rows), Decimal(0)) / margin_count if rows else None
    rmse = (sum((row.squared_error for row in rows), Decimal(0)) / margin_count).sqrt() if rows else None
    wins = sum(row.straight_up == StraightUpResult.CORRECT for row in rows)
    losses = sum(row.straight_up == StraightUpResult.INCORRECT for row in rows)
    ties = sum(row.straight_up == StraightUpResult.TIE for row in rows)
    covers = sum(row.coverage == CoverageResult.COVER for row in rows)
    no_covers = sum(row.coverage == CoverageResult.NO_COVER for row in rows)
    pushes = sum(row.coverage == CoverageResult.PUSH for row in rows)
    denominator = covers + no_covers
    return ForecastAggregate(
        len(rows), margin_count, mae, rmse, wins, losses, ties, wins + losses,
        covers, no_covers, pushes, denominator,
        Decimal(covers) / denominator if denominator else None,
        sum(row.selection == ForecastSelection.PICKEM for row in rows),
        sum(row.selection == ForecastSelection.LEGACY_UNKNOWN for row in rows),
    )


__all__ = [
    "CoverageResult", "EvidenceRef", "FinalScore", "ForecastAggregate",
    "ForecastCandidate", "ForecastContractError", "ForecastGrade",
    "ForecastDisposition", "ForecastProvenance", "ForecastSelection", "GameIdentity",
    "GameTimingEvidence", "OwnerAttestation", "PublicationReceipt", "ScoreRevision",
    "StraightUpResult", "VersionBinding", "aggregate_grades",
    "grade_forecast", "receipt_qualifies", "select_graded_forecast",
]
