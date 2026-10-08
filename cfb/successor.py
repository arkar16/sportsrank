"""Evidence-backed predecessor identity, separate from a provider capture.

This boundary only retrieves existing immutable evidence. It never observes or
writes Firebase, and cannot establish that an archived identity is still live.
"""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .publication_records import (
    ArchiveReference, BaselineRecord, ProviderIdentity, ProviderResultRecord,
    ReconciliationRecord, RecordValidationError, SealedAttemptReference,
    ValidatedPackageRecord, VerificationRecord, record_digest,
)


EVIDENCE_ROLES = (
    "provider_result", "provider_result_source", "verification",
    "verification_source", "reconciliation", "reconciliation_source",
)


@dataclass(frozen=True)
class SuccessorBaselineRecord:
    """Truthful package/audit provenance; no invented capture observations."""

    historical: BaselineRecord
    prior_reference: SealedAttemptReference
    prior_package: ValidatedPackageRecord
    observed: ProviderIdentity
    evidence: Mapping[str, ArchiveReference]

    def __post_init__(self):
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        if set(self.evidence) != set(EVIDENCE_ROLES):
            raise RecordValidationError("successor requires complete immutable verification evidence")
        if self.observed.target != self.historical.target:
            raise RecordValidationError("successor and historical targets differ")
        references = (self.prior_reference.intent_reference, *self.evidence.values())
        if any(
            ref.repository != self.prior_reference.intent_reference.repository
            or ref.target_commit != self.prior_package.candidate_commit
            for ref in references
        ):
            raise RecordValidationError("successor evidence candidate/repository differs")
        if len({(ref.release_id, ref.asset_id) for ref in references}) != 7:
            raise RecordValidationError("successor evidence roles must be distinct")

    @property
    def target(self):
        return self.observed.target

    @property
    def managed_resources(self):
        # Preserve app identity, not old provider-managed response bytes.
        return self.historical.managed_resources

    @property
    def digest(self):
        return record_digest(self.to_dict())

    def to_dict(self):
        return {
            "schema_version": 1,
            "record_type": "verified_successor_baseline",
            "historical": self.historical.to_dict(),
            "prior_reference": self.prior_reference.to_dict(),
            "prior_package": self.prior_package.to_dict(),
            "observed": self.observed.to_dict(),
            "evidence": {key: value.to_dict() for key, value in self.evidence.items()},
        }

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {
            "schema_version", "record_type", "historical", "prior_reference",
            "prior_package", "observed", "evidence",
        } or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise RecordValidationError("successor baseline fields do not match its schema")
        if raw["record_type"] != "verified_successor_baseline" or not isinstance(raw["evidence"], Mapping):
            raise RecordValidationError("successor baseline record is invalid")
        return cls(
            BaselineRecord.from_dict(raw["historical"]),
            SealedAttemptReference.from_dict(raw["prior_reference"]),
            ValidatedPackageRecord.from_dict(raw["prior_package"]),
            ProviderIdentity.from_value(raw["observed"]),
            {key: ArchiveReference.from_dict(value) for key, value in raw["evidence"].items()},
        )


def parse_baseline(raw):
    if raw.get("record_type") == "verified_successor_baseline":
        return SuccessorBaselineRecord.from_dict(raw)
    return BaselineRecord.from_dict(raw)


def preservation_baseline(record):
    return record.historical if isinstance(record, SuccessorBaselineRecord) else record


def require_full_verification(record, verification):
    from tools.scripts.postdeploy_smoke import REQUIRED_PATHS
    if (
        verification.inventory_sha256 != record.prior_package.inventory_sha256
        or verification.configuration_sha256 != record.prior_package.configuration_sha256
        or dict(verification.public_page_findings) != {f"/{path}": "verified:exact-bytes" for path in REQUIRED_PATHS}
        or any(not value.startswith("verified:sha256:") or len(value) != 80 or any(char not in "0123456789abcdef" for char in value[16:]) for value in verification.managed_resource_findings.values())
        or verification.findings != ("complete inventory, configuration, managed resources, and public pages verified",)
    ):
        raise RecordValidationError("successor requires full package inventory/configuration/public verification")


_TOKEN = object()


@dataclass(frozen=True, init=False)
class VerifiedSuccessorBaseline:
    record: SuccessorBaselineRecord
    artifact: object

    def __init__(self, record, artifact, *, _token=None):
        if _token is not _TOKEN:
            raise RecordValidationError("successor authority requires authenticated immutable evidence")
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "artifact", artifact)


def assert_successor_overlay(successor, site, owned):
    """Preserve all prior URLs and bytes outside independently validated ownership."""
    for relative, prior_bytes in successor.artifact.files.items():
        current = Path(site) / relative
        if not current.is_file():
            raise RecordValidationError(f"successor removes a prior public URL: {relative}")
        if relative not in owned and current.read_bytes() != prior_bytes:
            raise RecordValidationError(f"successor changes unowned prior bytes: {relative}")


def authenticate_successor(record, *, archive, repository, provenance_reader, destination):
    """Reconstruct exact prior package and full coordinator audit, without live reads."""
    from .publication import (
        PriorVerifiedPublication, PublicationExecutionError, _evidence_json,
        _retrieve_recorded_attempt, _verify_coordinator_predecessor, retrieve_sealed_attempt,
    )

    destination = Path(destination)
    attempt = retrieve_sealed_attempt(
        record.prior_reference, archive=archive, repository=repository,
        provenance_reader=provenance_reader, retrieval_directory=destination / "sealed",
    )
    if attempt.attempt.package != record.prior_package:
        raise PublicationExecutionError("successor prior package differs from authenticated sealed reference")
    artifact = _retrieve_recorded_attempt(
        attempt, archive=archive, repository=repository, destination=destination / "artifact",
    )
    # Later generations must retain the same independently pinned historical
    # authority. Read its receipt from the authenticated candidate Git tree.
    if record.prior_package.expected_baseline_sha256 != record.historical.digest:
        from .publication import GitCommitTreeReader
        from .public_site import LocalValidationReceipt
        reader = GitCommitTreeReader(destination / "sealed/candidate.git")
        receipt = LocalValidationReceipt.from_bytes(reader.read_file(
            record.prior_package.candidate_commit, "config/sr7-local-validation-receipt.json",
        ))
        prior_base = receipt.baseline_record
        if prior_base is None or prior_base.digest != record.prior_package.expected_baseline_sha256 or preservation_baseline(prior_base) != record.historical:
            raise PublicationExecutionError("successor historical preservation authority differs")
    records = {key: _evidence_json(
        archive, ref, repository=repository, destination=destination / f"{key}.json", name=key,
    ) for key, ref in record.evidence.items()}
    intent = attempt.attempt.intent
    result = ProviderResultRecord.from_dict(records["provider_result"], intent=intent)
    verification = VerificationRecord.from_dict(records["verification"], intent=intent, provider_result=result)
    reconciliation = ReconciliationRecord.from_dict(records["reconciliation"], intent=intent, provider_result=result, verification=verification)
    prior = PriorVerifiedPublication._create(repository, intent, result, verification, reconciliation,
        *(record.evidence[key] for key in EVIDENCE_ROLES), record.prior_reference.intent_reference)
    _verify_coordinator_predecessor(
        prior, record.observed, archive=archive, repository=repository,
        destination=destination / "coordinator-verification",
    )
    require_full_verification(record, verification)
    return VerifiedSuccessorBaseline(record, artifact, _token=_TOKEN)


def derive_successor(historical, reference, evidence, *, archive, repository, provenance_reader, destination):
    """Take selectors only; derive package and identity from immutable records."""
    from .publication import _evidence_json
    from .publication_records import AttemptIntentRecord

    destination = Path(destination)
    intent = AttemptIntentRecord.from_dict(_evidence_json(
        archive, reference.intent_reference, repository=repository,
        destination=destination / "intent.json", name="prior intent",
    ))
    if intent.package_record_reference is None:
        raise RecordValidationError("successor requires durable sealed package evidence")
    package = ValidatedPackageRecord.from_dict(_evidence_json(
        archive, intent.package_record_reference, repository=repository,
        destination=destination / "package.json", name="prior package",
    ))
    result = ProviderResultRecord.from_dict(_evidence_json(
        archive, evidence["provider_result"], repository=repository,
        destination=destination / "result.json", name="prior result",
    ), intent=intent)
    identity = ProviderIdentity(result.observed_target, result.observed_release, result.observed_version)
    return authenticate_successor(
        SuccessorBaselineRecord(historical, reference, package, identity, evidence),
        archive=archive, repository=repository, provenance_reader=provenance_reader,
        destination=destination / "authenticated",
    )
