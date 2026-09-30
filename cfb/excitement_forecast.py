"""Bind ADR-0021 BEV artifacts to immutable forecast publications.

The BEV artifact points at an acyclic forecast anchor: the complete candidate
content with only its content-derived ``version_id`` and its own BEV binding
removed.  Other bindings and replacement history remain part of the anchor.
An issued BEV is exposed only after the existing immutable publication loader
has authenticated the candidate and the exact BEV bytes in the same package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence

from .excitement import SCORING_VERSION, QualifiedPregame, calculate_bev
from .forecast_publication import VerifiedForecastPublication, load_verified_forecasts
from .forecast_record import (
    EvidenceRef,
    ForecastCandidate,
    ForecastContractError,
    ForecastProvenance,
    GameTimingEvidence,
    PublicationReceipt,
    VersionBinding,
    receipt_qualifies,
    select_graded_forecast,
)
from .public_safety import assert_public_bytes
from .publication import RecordedPublicationAttempt, _retrieve_recorded_attempt


BEV_SCHEMA = "forecast-bev/v1"
BEV_BINDING_KIND = "excitement-bev"
BEV_PATH_PREFIX = "excitement/bev"
MAX_BEV_PUBLICATION_HISTORY = 256
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ExcitementForecastError(ForecastContractError):
    """Raised when a forecast-to-BEV association cannot be proven."""


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExcitementForecastError(f"{field} must be non-empty text")
    return value


def _fields(data: Mapping[str, Any], expected: set[str], required: set[str], label: str) -> None:
    if not isinstance(data, Mapping):
        raise ExcitementForecastError(f"{label} must be an object")
    extras = set(data) - expected
    missing = required - set(data)
    if extras:
        raise ExcitementForecastError(f"{label} has unknown fields: {sorted(extras)!r}")
    if missing:
        raise ExcitementForecastError(f"{label} is missing fields: {sorted(missing)!r}")


def _instant_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo != timezone.utc:
        raise ExcitementForecastError("reconstructed_at must use UTC")
    return value.isoformat().replace("+00:00", "Z")


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ExcitementForecastError("reconstructed_at must be a UTC RFC3339 instant")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ExcitementForecastError("reconstructed_at must be a UTC RFC3339 instant") from exc
    if parsed.tzinfo != timezone.utc:
        raise ExcitementForecastError("reconstructed_at must use UTC")
    return parsed


def _candidate(candidate: ForecastCandidate) -> ForecastCandidate:
    """Reparse the public form so dataclass construction is never authority."""

    try:
        return ForecastCandidate.from_dict(candidate.to_dict())
    except (ForecastContractError, TypeError, ValueError) as exc:
        raise ExcitementForecastError("forecast candidate is not semantically valid") from exc


def _own_bindings(candidate: ForecastCandidate) -> tuple[VersionBinding, ...]:
    return tuple(binding for binding in candidate.bindings if binding.kind == BEV_BINDING_KIND)


def _anchor(candidate: ForecastCandidate) -> dict[str, Any]:
    value = candidate.to_dict()
    value.pop("version_id")
    value["bindings"] = [
        binding for binding in value.get("bindings", [])
        if binding.get("kind") != BEV_BINDING_KIND
    ]
    return value


def _anchor_digest(anchor: Mapping[str, Any]) -> str:
    return _digest(_canonical_json(dict(anchor)))


def _candidate_from_anchor(anchor: Mapping[str, Any]) -> ForecastCandidate:
    """Validate every anchored field through the shared forecast contract."""

    try:
        candidate = ForecastCandidate.from_dict({
            **dict(anchor), "version_id": _anchor_digest(anchor),
        })
    except (ForecastContractError, TypeError, ValueError) as exc:
        raise ExcitementForecastError("BEV forecast anchor is not a valid candidate") from exc
    if _anchor(candidate) != dict(anchor):
        raise ExcitementForecastError("BEV forecast anchor is not canonical")
    if _own_bindings(candidate):
        raise ExcitementForecastError("BEV forecast anchor must omit its own binding")
    return candidate


def bev_relative_path(candidate: ForecastCandidate, artifact_id: str) -> str:
    if not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None:
        raise ExcitementForecastError("BEV artifact_id must be a lowercase sha256 digest")
    return f"cfb/years/{candidate.game.season}/{BEV_PATH_PREFIX}/{artifact_id[7:]}.json"


@dataclass(frozen=True)
class ReconstructionInputQualification:
    """Externally qualified pregame inputs for one exact forecast anchor.

    This value records a caller's authoritative qualification.  It does not
    retrieve or authenticate the referenced source bytes; integration must
    construct it only after the reviewed checkpoint/source boundary succeeds.
    """

    inputs_as_of: datetime
    source: EvidenceRef
    forecast_anchor_digest: str
    rating_checkpoint: str
    rating_cutoff: str
    rating_artifact_digest: str
    source_snapshot_digest: str
    model_version: str
    source_kind: str
    code_revision: str
    home_rating: str
    away_rating: str
    home_field_advantage: str
    home_rank: int | None = None
    away_rank: int | None = None

    def __post_init__(self) -> None:
        _instant_text(self.inputs_as_of)
        if (not isinstance(self.forecast_anchor_digest, str)
                or _SHA256.fullmatch(self.forecast_anchor_digest) is None):
            raise ExcitementForecastError("reconstruction forecast anchor digest is invalid")
        # Reuse the shared forecast contract for every rating/checkpoint field.
        ForecastProvenance.from_dict(self._provenance_dict())
        if self.source.digest != self.source_snapshot_digest:
            raise ExcitementForecastError(
                "reconstruction source evidence does not identify the source snapshot"
            )

    def _provenance_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "rating_checkpoint": self.rating_checkpoint,
            "rating_cutoff": self.rating_cutoff,
            "rating_artifact_digest": self.rating_artifact_digest,
            "source_snapshot_digest": self.source_snapshot_digest,
            "model_version": self.model_version,
            "source_kind": self.source_kind,
            "code_revision": self.code_revision,
            "home_rating": self.home_rating,
            "away_rating": self.away_rating,
            "home_field_advantage": self.home_field_advantage,
        }
        if self.home_rank is not None:
            value["home_rank"] = self.home_rank
        if self.away_rank is not None:
            value["away_rank"] = self.away_rank
        return value

    @property
    def provenance(self) -> ForecastProvenance:
        return ForecastProvenance.from_dict(self._provenance_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs_as_of": _instant_text(self.inputs_as_of),
            "source": self.source.to_dict(),
            "forecast_anchor_digest": self.forecast_anchor_digest,
            "forecast_provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReconstructionInputQualification":
        names = {"inputs_as_of", "source", "forecast_anchor_digest", "forecast_provenance"}
        _fields(data, names, names, "reconstruction")
        provenance = ForecastProvenance.from_dict(data["forecast_provenance"])
        values = provenance.to_dict()
        return cls(
            _instant(data["inputs_as_of"]),
            EvidenceRef.from_dict(data["source"]),
            data["forecast_anchor_digest"],
            values["rating_checkpoint"], values["rating_cutoff"],
            values["rating_artifact_digest"], values["source_snapshot_digest"],
            values["model_version"], values["source_kind"], values["code_revision"],
            values["home_rating"], values["away_rating"], values["home_field_advantage"],
            values.get("home_rank"), values.get("away_rank"),
        )


@dataclass(frozen=True)
class ReconstructionProvenance:
    reconstructed_at: datetime
    qualified_input: ReconstructionInputQualification

    def __post_init__(self) -> None:
        _instant_text(self.reconstructed_at)
        if self.reconstructed_at < self.qualified_input.inputs_as_of:
            raise ExcitementForecastError("reconstruction cannot precede its qualified inputs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconstructed_at": _instant_text(self.reconstructed_at),
            "qualified_input": self.qualified_input.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReconstructionProvenance":
        names = {"reconstructed_at", "qualified_input"}
        _fields(data, names, names, "reconstruction")
        return cls(
            _instant(data["reconstructed_at"]),
            ReconstructionInputQualification.from_dict(data["qualified_input"]),
        )


def _validate_reconstruction_qualification(
    candidate: ForecastCandidate,
    qualified_input: ReconstructionInputQualification,
) -> None:
    expected_anchor = _anchor_digest(_anchor(candidate))
    if qualified_input.forecast_anchor_digest != expected_anchor:
        raise ExcitementForecastError(
            "reconstruction qualification belongs to a different forecast anchor"
        )
    if qualified_input.provenance.to_dict() != candidate.provenance.to_dict():
        raise ExcitementForecastError(
            "reconstruction qualification does not match forecast provenance"
        )


@dataclass(frozen=True)
class BevArtifact:
    """Canonical, public-safe result bound to one immutable forecast anchor."""

    forecast_anchor: Mapping[str, Any]
    forecast_anchor_digest: str
    status: Literal["calculated", "unavailable"]
    value: float | None
    components: Mapping[str, float] | None
    market_basis: Literal["missing"]
    qualification_reasons: tuple[str, ...]
    forecast_state: Literal["candidate", "reconstructed"] = "candidate"
    reconstruction: ReconstructionProvenance | None = None
    scoring_version: str = SCORING_VERSION
    artifact_id: str = ""
    schema_version: str = BEV_SCHEMA

    def __post_init__(self) -> None:
        anchor = json.loads(_canonical_json(dict(self.forecast_anchor)))
        object.__setattr__(self, "forecast_anchor", anchor)
        object.__setattr__(self, "qualification_reasons", tuple(self.qualification_reasons))
        if self.schema_version != BEV_SCHEMA or self.scoring_version != SCORING_VERSION:
            raise ExcitementForecastError("unsupported BEV schema or scoring version")
        if (not isinstance(self.forecast_anchor_digest, str)
                or _SHA256.fullmatch(self.forecast_anchor_digest) is None):
            raise ExcitementForecastError("forecast_anchor_digest must be a lowercase sha256 digest")
        if self.forecast_anchor_digest != _anchor_digest(anchor):
            raise ExcitementForecastError("forecast anchor digest does not match its content")
        if self.market_basis != "missing":
            raise ExcitementForecastError("production BEV binding has no qualified market contract")
        if self.forecast_state == "candidate" and self.reconstruction is not None:
            raise ExcitementForecastError("candidate BEV cannot carry reconstruction provenance")
        if self.forecast_state == "reconstructed" and self.reconstruction is None:
            raise ExcitementForecastError("reconstructed BEV requires explicit provenance")
        if self.forecast_state not in ("candidate", "reconstructed"):
            raise ExcitementForecastError("unsupported BEV forecast state")
        if (self.reconstruction is not None
                and self.reconstruction.qualified_input.forecast_anchor_digest
                != self.forecast_anchor_digest):
            raise ExcitementForecastError("reconstruction evidence does not bind this forecast anchor")
        if self.status == "calculated":
            if self.value is None or self.components is None or self.qualification_reasons:
                raise ExcitementForecastError("calculated BEV has inconsistent result fields")
            if not isinstance(self.components, Mapping):
                raise ExcitementForecastError("BEV components must be an object")
            if type(self.value) is not float or not math.isfinite(self.value):
                raise ExcitementForecastError("calculated BEV value must be a finite float")
            if any(type(value) is not float or not math.isfinite(value)
                   for value in self.components.values()):
                raise ExcitementForecastError("BEV components must be finite floats")
        elif self.status == "unavailable":
            if self.value is not None or self.components is not None or not self.qualification_reasons:
                raise ExcitementForecastError("unavailable BEV requires reasons and no score")
        else:
            raise ExcitementForecastError("unsupported BEV status")
        content_id = _digest(_canonical_json(self._content_dict()))
        if self.artifact_id and self.artifact_id != content_id:
            raise ExcitementForecastError("BEV artifact_id does not match content")
        object.__setattr__(self, "artifact_id", content_id)
        anchored_candidate = _candidate_from_anchor(anchor)
        if self.reconstruction is not None:
            _validate_reconstruction_qualification(
                anchored_candidate, self.reconstruction.qualified_input
            )
        self._validate_score()

    def _content_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "scoring_version": self.scoring_version,
            "forecast_state": self.forecast_state,
            "forecast_anchor": json.loads(_canonical_json(dict(self.forecast_anchor))),
            "forecast_anchor_digest": self.forecast_anchor_digest,
            "status": self.status,
            "value": self.value,
            "components": dict(self.components) if self.components is not None else None,
            "evidence": {
                "market_basis": self.market_basis,
                "market_reason": "market-qualification-unavailable",
                "qualification_reasons": list(self.qualification_reasons),
            },
        }
        if self.reconstruction is not None:
            result["reconstruction"] = self.reconstruction.to_dict()
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "artifact_id": self.artifact_id}

    def to_bytes(self) -> bytes:
        # Frozen dataclasses can still be handed caller-owned mutable mappings;
        # revalidate current state before any bytes cross a trust boundary.
        if self.artifact_id != _digest(_canonical_json(self._content_dict())):
            raise ExcitementForecastError("BEV artifact changed after validation")
        anchored_candidate = _candidate_from_anchor(self.forecast_anchor)
        if self.reconstruction is not None:
            _validate_reconstruction_qualification(
                anchored_candidate, self.reconstruction.qualified_input
            )
        self._validate_score()
        raw = _canonical_json(self.to_dict())
        # The unchanged project scanner remains the final public-byte boundary.
        anchor = self.forecast_anchor
        season = anchor.get("game", {}).get("season") if isinstance(anchor.get("game"), Mapping) else None
        relative = f"cfb/years/{season}/{BEV_PATH_PREFIX}/{self.artifact_id[7:]}.json"
        assert_public_bytes(relative, raw)
        return raw

    @property
    def byte_digest(self) -> str:
        return _digest(self.to_bytes())

    def _validate_score(self) -> None:
        provenance = self.forecast_anchor.get("provenance")
        forecast = self.forecast_anchor.get("forecast")
        if not isinstance(provenance, Mapping) or not isinstance(forecast, Mapping):
            raise ExcitementForecastError("BEV forecast anchor is incomplete")
        home_rank, away_rank = provenance.get("home_rank"), provenance.get("away_rank")
        missing = tuple(
            name for name, value in (("missing-home-rank", home_rank), ("missing-away-rank", away_rank))
            if value is None
        )
        if missing:
            if self.status != "unavailable" or self.qualification_reasons != missing:
                raise ExcitementForecastError("missing ranks must remain an explicit unavailable BEV")
            return
        try:
            score = calculate_bev(QualifiedPregame(
                home_rank=home_rank,
                away_rank=away_rank,
                cors_home_margin=float(forecast["home_margin"]),
                forecast_id=self.forecast_anchor_digest,
                source_snapshot_id=_text(provenance["source_snapshot_digest"], "source snapshot"),
                market=None,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ExcitementForecastError("BEV forecast anchor cannot be scored") from exc
        expected_components = {
            "quality": score.components.quality,
            "competitiveness": score.components.competitiveness,
            "market_boost": score.components.market_boost,
        }
        if (self.status != "calculated" or self.value != score.value
                or dict(self.components or {}) != expected_components):
            raise ExcitementForecastError("BEV score or components do not match bound inputs")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BevArtifact":
        expected = {
            "schema_version", "artifact_id", "scoring_version", "forecast_state",
            "forecast_anchor", "forecast_anchor_digest", "status", "value",
            "components", "evidence", "reconstruction",
        }
        _fields(data, expected, expected - {"reconstruction"}, "BEV artifact")
        evidence = data["evidence"]
        evidence_names = {"market_basis", "market_reason", "qualification_reasons"}
        _fields(evidence, evidence_names, evidence_names, "BEV evidence")
        if evidence["market_reason"] != "market-qualification-unavailable":
            raise ExcitementForecastError("BEV market reason is unsupported")
        if not isinstance(data["forecast_anchor"], Mapping):
            raise ExcitementForecastError("forecast_anchor must be an object")
        components = data["components"]
        if components is not None:
            _fields(components, {"quality", "competitiveness", "market_boost"}, {"quality", "competitiveness", "market_boost"}, "BEV components")
        reasons = evidence["qualification_reasons"]
        if not isinstance(reasons, list) or any(not isinstance(item, str) or not item for item in reasons):
            raise ExcitementForecastError("qualification_reasons must be a list of text")
        reconstruction = data.get("reconstruction")
        return cls(
            forecast_anchor=data["forecast_anchor"],
            forecast_anchor_digest=data["forecast_anchor_digest"],
            status=data["status"],
            value=data["value"],
            components=components,
            market_basis=evidence["market_basis"],
            qualification_reasons=tuple(reasons),
            forecast_state=data["forecast_state"],
            reconstruction=ReconstructionProvenance.from_dict(reconstruction) if reconstruction is not None else None,
            scoring_version=data["scoring_version"],
            artifact_id=data["artifact_id"],
            schema_version=data["schema_version"],
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "BevArtifact":
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExcitementForecastError("BEV artifact is not valid JSON") from exc
        if not isinstance(data, Mapping):
            raise ExcitementForecastError("BEV artifact must be an object")
        artifact = cls.from_dict(data)
        if artifact.to_bytes() != raw:
            raise ExcitementForecastError("BEV artifact bytes are not canonical")
        return artifact


def _new_artifact(
    candidate: ForecastCandidate,
    *,
    forecast_state: Literal["candidate", "reconstructed"] = "candidate",
    reconstruction: ReconstructionProvenance | None = None,
) -> BevArtifact:
    anchor = _anchor(candidate)
    anchor_id = _anchor_digest(anchor)
    provenance = candidate.provenance
    reasons = tuple(
        reason for reason, value in (
            ("missing-home-rank", provenance.home_rank),
            ("missing-away-rank", provenance.away_rank),
        ) if value is None
    )
    if reasons:
        return BevArtifact(anchor, anchor_id, "unavailable", None, None, "missing", reasons,
                           forecast_state, reconstruction)
    score = calculate_bev(QualifiedPregame(
        provenance.home_rank,  # type: ignore[arg-type]
        provenance.away_rank,  # type: ignore[arg-type]
        float(candidate.home_margin), anchor_id, provenance.source_snapshot_digest,
    ))
    components = {
        "quality": score.components.quality,
        "competitiveness": score.components.competitiveness,
        "market_boost": score.components.market_boost,
    }
    return BevArtifact(anchor, anchor_id, "calculated", score.value, components, "missing", (),
                       forecast_state, reconstruction)


def _validate_association(candidate: ForecastCandidate, artifact: BevArtifact) -> VersionBinding:
    if artifact.forecast_anchor != _anchor(candidate):
        raise ExcitementForecastError("BEV artifact does not match the forecast candidate")
    if artifact.forecast_state != "candidate":
        raise ExcitementForecastError("reconstructed BEV cannot bind an issued candidate")
    return VersionBinding(BEV_BINDING_KIND, artifact.artifact_id, artifact.byte_digest)


def bind_bev(
    candidate: ForecastCandidate,
    artifact: BevArtifact | bytes | None = None,
) -> tuple[ForecastCandidate, BevArtifact]:
    """Create or verify one acyclic candidate-to-BEV association.

    An already-bound candidate is never rewritten.  It requires the exact
    artifact bytes (or parsed equivalent) so its association is independently
    revalidated rather than trusted from the candidate binding.
    """

    checked = _candidate(candidate)
    own = _own_bindings(checked)
    if len(own) > 1:
        raise ExcitementForecastError("forecast candidate has duplicate BEV bindings")
    if isinstance(artifact, bytes):
        artifact = BevArtifact.from_bytes(artifact)
    elif artifact is not None:
        artifact = BevArtifact.from_bytes(artifact.to_bytes())
    if own:
        if artifact is None:
            raise ExcitementForecastError("already-bound forecast requires exact BEV artifact bytes")
        expected = _validate_association(checked, artifact)
        if own[0] != expected:
            raise ExcitementForecastError("forecast BEV binding conflicts with exact artifact")
        return checked, artifact
    if artifact is not None:
        raise ExcitementForecastError("new BEV association is calculated from the forecast candidate")
    artifact = _new_artifact(checked)
    binding = _validate_association(checked, artifact)
    bound = ForecastCandidate.create(
        game=checked.game,
        home_margin=checked.home_margin,
        precision=checked.precision,
        provenance=checked.provenance,
        predecessor_version_id=checked.predecessor_version_id,
        replacement_reason=checked.replacement_reason,
        bindings=checked.bindings + (binding,),
        selection=checked.selection,
    )
    return bound, artifact


def prepare_reconstructed_bev(
    candidate: ForecastCandidate,
    *,
    reconstructed_at: datetime,
    qualified_input: ReconstructionInputQualification,
    timing: GameTimingEvidence,
) -> BevArtifact:
    """Prepare an explicitly non-issued historical reconstruction."""

    checked = _candidate(candidate)
    if _own_bindings(checked):
        raise ExcitementForecastError("reconstruction requires an unbound forecast candidate")
    if timing.game != checked.game:
        raise ExcitementForecastError("reconstruction timing Game identity differs")
    _instant_text(reconstructed_at)
    _validate_reconstruction_qualification(checked, qualified_input)
    inputs_as_of = qualified_input.inputs_as_of
    if timing.actual_started_at is not None:
        qualified = inputs_as_of < timing.actual_started_at
    else:
        qualified = inputs_as_of <= timing.observed_not_started_at  # type: ignore[operator]
    if not qualified:
        raise ExcitementForecastError("historical reconstruction inputs are not proven pregame")
    return _new_artifact(
        checked,
        forecast_state="reconstructed",
        reconstruction=ReconstructionProvenance(reconstructed_at, qualified_input),
    )


_ISSUED_TOKEN = object()


@dataclass(frozen=True, init=False)
class IssuedBev:
    candidate: ForecastCandidate
    artifact: BevArtifact
    artifact_bytes: bytes
    receipt: PublicationReceipt
    publication: VerifiedForecastPublication

    def __init__(
        self,
        candidate: ForecastCandidate,
        artifact: BevArtifact,
        artifact_bytes: bytes,
        receipt: PublicationReceipt,
        publication: VerifiedForecastPublication,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _ISSUED_TOKEN:
            raise ExcitementForecastError(
                "issued BEV requires immutable publication reconstruction"
            )
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(self, "artifact_bytes", artifact_bytes)
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(self, "publication", publication)


@dataclass(frozen=True)
class _AuthenticatedBevAttempt:
    candidate: ForecastCandidate
    artifact: BevArtifact
    artifact_bytes: bytes
    receipt: PublicationReceipt
    publication: VerifiedForecastPublication
    overlay: bool


def _validate_history_graph(candidates: Mapping[str, ForecastCandidate]) -> None:
    roots: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for candidate in candidates.values():
        if candidate.predecessor_version_id is None:
            roots.setdefault(candidate.game.key, []).append(candidate.version_id)
        else:
            children.setdefault(candidate.predecessor_version_id, []).append(candidate.version_id)
    if any(len(values) > 1 for values in roots.values()):
        raise ExcitementForecastError("publication history has conflicting original forecasts")
    if any(len(values) > 1 for values in children.values()):
        raise ExcitementForecastError("publication history has conflicting sibling corrections")
    for candidate in candidates.values():
        seen = {candidate.version_id}
        predecessor = candidate.predecessor_version_id
        while predecessor in candidates:
            if predecessor in seen:
                raise ExcitementForecastError("publication history correction chain is cyclic")
            seen.add(predecessor)
            predecessor = candidates[predecessor].predecessor_version_id


def _load_bev_attempt(
    recorded: RecordedPublicationAttempt,
    *,
    archive: Any,
    repository: str,
    destination: Path,
    timing: GameTimingEvidence,
) -> tuple[_AuthenticatedBevAttempt, ...]:
    """Authenticate exact BEV-bearing candidates from one publication attempt."""

    publication = load_verified_forecasts(
        recorded, archive=archive, repository=repository,
        destination=destination / "forecast-publication",
    )
    candidates = tuple(item for item in publication.candidates if item.game == timing.game)
    candidate_ids = {item.version_id for item in candidates}
    if len(candidate_ids) != len(candidates):
        raise ExcitementForecastError("publication attempt contains duplicate forecast versions")
    # A predecessor copied into the same correction package does not establish
    # that it was public before that correction.  Only a separate authenticated
    # attempt can supply the predecessor's earlier issuance fact.
    overlays = {
        item.predecessor_version_id for item in candidates
        if item.predecessor_version_id in candidate_ids
    }
    receipts: dict[str, PublicationReceipt] = {}
    for receipt in publication.receipts:
        if receipt.candidate_version_id not in candidate_ids:
            continue
        if receipt.candidate_version_id in receipts:
            raise ExcitementForecastError("publication attempt has duplicate candidate receipts")
        receipts[receipt.candidate_version_id] = receipt
    package = _retrieve_recorded_attempt(
        recorded, archive=archive, repository=repository,
        destination=destination / "bev-package",
    )
    result: list[_AuthenticatedBevAttempt] = []
    for candidate in candidates:
        own = _own_bindings(candidate)
        if len(own) > 1:
            raise ExcitementForecastError("issued forecast has duplicate BEV bindings")
        if not own:
            continue
        path = bev_relative_path(candidate, own[0].version_id)
        raw = package.files.get(path)
        # A candidate link without its exact artifact does not establish BEV
        # issuance.  It is deliberately unavailable rather than borrowed from
        # another, possibly later, package.
        if raw is None:
            continue
        if _digest(raw) != own[0].digest:
            raise ExcitementForecastError("authenticated package has conflicting bound BEV bytes")
        artifact = BevArtifact.from_bytes(raw)
        checked, _ = bind_bev(candidate, artifact)
        receipt = receipts.get(checked.version_id)
        if receipt is None:
            raise ExcitementForecastError("authenticated BEV candidate lacks its publication receipt")
        result.append(_AuthenticatedBevAttempt(
            checked, artifact, raw, receipt, publication,
            checked.version_id in overlays,
        ))
    return tuple(result)


def load_issued_bev_history(
    recorded_attempts: Sequence[RecordedPublicationAttempt],
    *,
    archive: Any,
    repository: str,
    destination: str | Path,
    timing: GameTimingEvidence,
) -> IssuedBev | None:
    """Select one issued BEV from independently authenticated publications.

    Every attempt is revalidated.  Candidate timestamps are retained per
    package, and only an attempt containing the exact bound BEV bytes can
    establish that BEV's issuance.  Rebuilds retain the earliest authentic
    publication fact; copied predecessors in a correction package cannot
    manufacture an earlier point in the correction chain.
    """

    target = Path(destination)
    if not isinstance(recorded_attempts, Sequence) or isinstance(recorded_attempts, (str, bytes)):
        raise ExcitementForecastError("publication history must be a sequence of attempts")
    if len(recorded_attempts) > MAX_BEV_PUBLICATION_HISTORY:
        raise ExcitementForecastError(
            f"publication history exceeds {MAX_BEV_PUBLICATION_HISTORY} attempts"
        )
    attempts: list[_AuthenticatedBevAttempt] = []
    for index, recorded in enumerate(recorded_attempts):
        attempts.extend(_load_bev_attempt(
            recorded, archive=archive, repository=repository,
            destination=target / f"attempt-{index:04d}", timing=timing,
        ))
    candidates: dict[str, ForecastCandidate] = {}
    artifact_bytes: dict[str, bytes] = {}
    for attempt in attempts:
        previous = candidates.setdefault(attempt.candidate.version_id, attempt.candidate)
        if previous != attempt.candidate:
            raise ExcitementForecastError("publication history conflicts on forecast content")
        previous_bytes = artifact_bytes.setdefault(attempt.candidate.version_id, attempt.artifact_bytes)
        if previous_bytes != attempt.artifact_bytes:
            raise ExcitementForecastError("publication history conflicts on BEV artifact bytes")
    _validate_history_graph(candidates)
    receipts = tuple(attempt.receipt for attempt in attempts if not attempt.overlay)
    candidate = select_graded_forecast(tuple(candidates.values()), receipts, timing)
    if candidate is None:
        return None
    selected_attempts = [
        attempt for attempt in attempts
        if not attempt.overlay
        and attempt.candidate.version_id == candidate.version_id
        and receipt_qualifies(candidate, attempt.receipt, timing)
    ]
    if not selected_attempts:
        raise ExcitementForecastError(
            "selected BEV lacks corresponding qualifying package evidence"
        )
    selected = min(
        selected_attempts,
        key=lambda item: (
            item.receipt.public_by,
            item.receipt.receipt_id,
        ),
    )
    return IssuedBev(
        selected.candidate, selected.artifact, selected.artifact_bytes,
        selected.receipt, selected.publication, _token=_ISSUED_TOKEN,
    )


def load_issued_bev(
    recorded: RecordedPublicationAttempt,
    *,
    archive: Any,
    repository: str,
    destination: str | Path,
    timing: GameTimingEvidence,
) -> IssuedBev | None:
    """Authenticate one attempt through the bounded history selector."""

    return load_issued_bev_history(
        (recorded,), archive=archive, repository=repository,
        destination=destination, timing=timing,
    )


__all__ = [
    "BEV_BINDING_KIND", "BEV_SCHEMA", "MAX_BEV_PUBLICATION_HISTORY",
    "BevArtifact", "ExcitementForecastError",
    "IssuedBev", "ReconstructionInputQualification", "ReconstructionProvenance",
    "bev_relative_path", "bind_bev",
    "load_issued_bev", "load_issued_bev_history", "prepare_reconstructed_bev",
]
