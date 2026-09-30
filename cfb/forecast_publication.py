"""Read-only bridge from immutable publication evidence to issued forecasts.

No deserialized receipt is a trust capability.  Every import re-reads the exact
package, intent, provider result, verification and source assets through the
existing immutable archive boundary before exposing forecast candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .forecast_record import EvidenceRef, ForecastCandidate, PublicationReceipt
from .publication import (
    RecordedPublicationAttempt, PublicationExecutionError, _retrieve_recorded_attempt,
    _record_evidence_is_valid, _evidence_json,
)
from .publication_records import ProviderResultRecord, VerificationRecord, canonical_json
from .publication_timing import original_publication_facts, publication_instant, verification_source_matches

_TOKEN = object()
_CANDIDATE_PATH = re.compile(r"cfb/years/(\d{4})/forecasts/([0-9a-f]{64})\.json\Z")


@dataclass(frozen=True, init=False)
class VerifiedForecastPublication:
    candidates: tuple[ForecastCandidate, ...]
    receipts: tuple[PublicationReceipt, ...]
    _provenance_json: bytes

    def __init__(self, candidates: tuple[ForecastCandidate, ...],
                 receipts: tuple[PublicationReceipt, ...], provenance: dict[str, Any],
                 *, _token: object | None = None) -> None:
        if _token is not _TOKEN:
            raise PublicationExecutionError("forecast publication requires immutable evidence reconstruction")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "receipts", receipts)
        object.__setattr__(self, "_provenance_json", canonical_json(provenance))

    def to_provenance(self) -> dict[str, Any]:
        return json.loads(self._provenance_json)


def load_verified_forecasts(recorded: RecordedPublicationAttempt, *, archive: Any,
                            repository: str, destination: str | Path) -> VerifiedForecastPublication:
    """Import exact published candidates using archive-verified immutable refs.

    ``recorded`` supplies locators, not authority: its paths and claims are
    revalidated.  A failed/uncertain publication or failed verification raises;
    callers report missing evidence instead of treating a candidate as issued.
    This function never observes or writes Firebase and never calls CFBD.
    """
    target = Path(destination)
    artifact = _retrieve_recorded_attempt(recorded, archive=archive,
                                         repository=repository, destination=target / "attempt")
    result, verification = recorded.provider_result, recorded.verification
    if result is None or verification is None:
        raise PublicationExecutionError("issued forecasts require accepted and verified publication evidence")
    for record, evidence, label in (
        (result, recorded.provider_evidence, "provider"),
        (verification, recorded.verification_evidence, "verification"),
    ):
        if not _record_evidence_is_valid(record, evidence, archive=archive,
                                         repository=repository, destination=target / label):
            raise PublicationExecutionError(f"forecast {label} immutable evidence is invalid")
    assert recorded.provider_evidence is not None and recorded.verification_evidence is not None
    intent = recorded.attempt.intent
    result = ProviderResultRecord.from_dict(result.to_dict(), intent=intent)
    verification = VerificationRecord.from_dict(verification.to_dict(), intent=intent, provider_result=result)
    if (result.outcome != "accepted" or verification.outcome != "verified"
            or verification.inventory_sha256 != artifact.package.inventory_sha256
            or verification.configuration_sha256 != artifact.package.configuration_sha256
            or result.observed_release != verification.observed_release
            or result.observed_version != verification.observed_version):
        raise PublicationExecutionError("forecast publication is not verified against the exact package")
    provider_source = _evidence_json(
        archive, recorded.provider_evidence.source_reference, repository=repository,
        destination=target / "provider-source.json", name="forecast provider source",
    )
    verification_source = _evidence_json(
        archive, recorded.verification_evidence.source_reference, repository=repository,
        destination=target / "verification-source.json", name="forecast verification source",
    )
    verification_source_matches(verification_source, verification)
    published_at, original_public_by = original_publication_facts(
        provider_source, result, intent, archive=archive, repository=repository,
        destination=target / "original-publication",
    )
    observed = publication_instant(verification.observed_at)
    public_by = min(original_public_by, observed) if original_public_by else observed
    if published_at is not None and published_at > public_by:
        raise PublicationExecutionError("provider publication time is later than verified public observation")
    candidates: list[ForecastCandidate] = []
    receipts: list[PublicationReceipt] = []
    for path, raw in sorted(artifact.files.items()):
        match = _CANDIDATE_PATH.fullmatch(path)
        if match is None:
            continue
        candidate = ForecastCandidate.from_dict(json.loads(raw))
        # The exact candidate JSON is deliberately canonical without a trailing
        # newline, matching its declared artifact hash inside the package.
        if (hashlib.sha256(raw).hexdigest() != candidate.artifact_digest.removeprefix("sha256:")
                or candidate.version_id != "sha256:" + match.group(2)
                or candidate.game.season != int(match.group(1))):
            raise PublicationExecutionError("packaged forecast identity or bytes are invalid")
        candidates.append(candidate)
        receipt_key = hashlib.sha256(canonical_json({
            "candidate": candidate.version_id, "provider_result": result.digest,
            "verification": verification.digest,
        })).hexdigest()
        receipts.append(PublicationReceipt(
            receipt_id="sha256:" + receipt_key,
            candidate_version_id=candidate.version_id,
            candidate_artifact_digest=candidate.artifact_digest,
            publication_artifact_digest="sha256:" + artifact.package.bundle_sha256,
            attempt_id=intent.attempt_id, verification_id="sha256:" + verification.digest,
            source=EvidenceRef("immutable-publication", recorded.verification_evidence.record_reference.digest,
                               "sha256:" + recorded.verification_evidence.record_reference.sha256),
            provider_published_at=published_at,
            verified_public_by=None if published_at else public_by,
        ))
    provenance = {
        "schema_version": "forecast-publication-evidence/v1", "repository": repository,
        "intent": recorded.attempt.intent_reference.to_dict(),
        "package": recorded.attempt.package_reference.to_dict(),
        "provider_result": recorded.provider_evidence.record_reference.to_dict(),
        "provider_source": recorded.provider_evidence.source_reference.to_dict(),
        "verification": recorded.verification_evidence.record_reference.to_dict(),
        "verification_source": recorded.verification_evidence.source_reference.to_dict(),
    }
    return VerifiedForecastPublication(tuple(candidates), tuple(receipts), provenance, _token=_TOKEN)
