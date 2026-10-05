"""Allowlisted publication time facts with immutable source references.

Logical publication records remain schema 1.  New source records use schema 2;
legacy source bytes are never upgraded or rewritten during reconstruction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from .publication_records import (
    ArchiveReference, AttemptIntentRecord, ProviderResultRecord,
    VerificationRecord, canonical_json,
)


def publication_instant(value: Any) -> datetime:
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    ) is None:
        raise ValueError("publication time must be a timezone-aware RFC3339 instant")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("publication time must be a timezone-aware RFC3339 instant") from exc
    if result.tzinfo is None:
        raise ValueError("publication time requires an explicit timezone")
    return result.astimezone(timezone.utc)


def deployment_time(source: Mapping[str, Any], result: ProviderResultRecord,
                    intent: AttemptIntentRecord) -> datetime | None:
    """Validate a retrieved deployment source before trusting its time field."""
    legacy = {"schema_version", "record_type", "outcome", "release", "version",
              "file_count", "provider_inventory_sha256", "serving_configuration_sha256",
              "version_bytes"}
    version = source.get("schema_version")
    expected = legacy if version == 1 else legacy | {"provider_published_at", "artifact_sha256", "attempt_id"}
    if version not in {1, 2} or set(source) != expected:
        raise ValueError("deployment observation has unsupported fields")
    if (source["record_type"] != "firebase_deployment_observation"
            or source["outcome"] != "accepted" or result.outcome != "accepted"
            or source["release"] != result.observed_release
            or source["version"] != result.observed_version
            or result.intent_sha256 != intent.digest):
        raise ValueError("deployment observation identity does not match")
    if version == 1:
        return None
    if (source["artifact_sha256"] != intent.artifact_reference.sha256
            or source["attempt_id"] != intent.attempt_id):
        raise ValueError("publication time does not bind the exact attempt artifact")
    return publication_instant(source["provider_published_at"])


def verification_source_matches(source: Mapping[str, Any], verification: VerificationRecord) -> None:
    expected = {
        "schema_version": 1, "record_type": "firebase_verification_observation",
        "outcome": verification.outcome, "release": verification.observed_release,
        "version": verification.observed_version,
        "inventory_sha256": verification.inventory_sha256,
        "configuration_sha256": verification.configuration_sha256,
        "managed_resource_findings": dict(verification.managed_resource_findings),
        "public_page_findings": dict(verification.public_page_findings),
        "findings": list(verification.findings),
    }
    if dict(source) != expected:
        raise ValueError("verification source does not match its immutable record")


def original_publication_facts(source: Mapping[str, Any], result: ProviderResultRecord,
                               intent: AttemptIntentRecord, *, archive: Any,
                               repository: str, destination: Path, depth: int = 0
                               ) -> tuple[datetime | None, datetime | None]:
    """Resolve original time facts, re-reading every preserved source binding."""
    if depth > 32:
        raise ValueError("publication evidence chain exceeds supported depth")
    if source.get("record_type") == "firebase_deployment_observation":
        return deployment_time(source, result, intent), None
    legacy = {
        "schema_version": 1, "record_type": "firebase_reconciled_provider_result",
        "outcome": result.outcome, "release": result.observed_release,
        "version": result.observed_version, "artifact_sha256": intent.artifact_reference.sha256,
        "content_correspondence": "verified",
        "prior_result_evidence": source.get("prior_result_evidence"),
        "prior_verification_evidence": source.get("prior_verification_evidence"),
    }
    expected = dict(legacy)
    if source.get("schema_version") == 2:
        expected.update(schema_version=2, original_evidence=source.get("original_evidence"))
    if dict(source) != expected or any(source.get(key) not in {
        "archive_consistent_untrusted", "missing_or_invalid"
    } for key in ("prior_result_evidence", "prior_verification_evidence")):
        raise ValueError("reconciled publication source is invalid")
    proof = source.get("original_evidence")
    if proof is None:
        return None, None
    if not isinstance(proof, Mapping) or set(proof) != {
        "provider_result", "provider_source", "verification", "verification_source"
    }:
        raise ValueError("original publication references are invalid")

    names = {"provider_result": "provider-result.json", "provider_source": "provider-result-source.json",
             "verification": "verification.json", "verification_source": "verification-source.json"}
    references = {}
    for role, value in proof.items():
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise ValueError("original publication reference must be an object")
        reference = ArchiveReference.from_dict(value)
        if (reference.repository != repository or reference.asset_name != names[role]
                or reference.target_commit != intent.artifact_reference.target_commit):
            raise ValueError("original publication reference role/commit differs")
        references[role] = reference
    if not {"provider_result", "provider_source"} <= references.keys():
        raise ValueError("original provider references are required")
    if len({(ref.release_id, ref.asset_id) for ref in references.values()}) != len(references):
        raise ValueError("original publication roles require distinct assets")
    for record_role, source_role in (("provider_result", "provider_source"), ("verification", "verification_source")):
        if record_role in references and source_role in references:
            left, right = references[record_role], references[source_role]
            if (left.release_id, left.tag) != (right.release_id, right.tag):
                raise ValueError("original record and source must be sealed together")

    def read(name: str) -> Mapping[str, Any]:
        import json
        ref = references[name]
        path = archive.retrieve_and_verify(ref, destination / f"{depth}-{name}.json")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref.sha256:
            raise ValueError("original publication source digest mismatch")
        value = json.loads(raw)
        if not isinstance(value, Mapping) or canonical_json(value) != raw:
            raise ValueError("original publication evidence must be a canonical JSON object")
        return value

    prior = ProviderResultRecord.from_dict(read("provider_result"), intent=intent)
    prior_source = read("provider_source")
    if (prior.outcome != "accepted" or prior.observed_release != result.observed_release
            or prior.observed_version != result.observed_version
            or prior.digest != proof["provider_result"]["sha256"]
            or prior.source_sha256 != proof["provider_source"]["sha256"]
            or hashlib.sha256(canonical_json(prior_source)).hexdigest() != prior.source_sha256):
        raise ValueError("original publication evidence differs from the current identity")
    published, public_by = original_publication_facts(
        prior_source, prior, intent, archive=archive, repository=repository,
        destination=destination, depth=depth + 1,
    )
    if (proof["verification"] is None) != (proof["verification_source"] is None):
        raise ValueError("original verification references must occur together")
    if proof["verification"] is not None:
        verified = VerificationRecord.from_dict(read("verification"), intent=intent, provider_result=prior)
        verified_source = read("verification_source")
        if (verified.digest != proof["verification"]["sha256"]
                or verified.source_sha256 != proof["verification_source"]["sha256"]
                or hashlib.sha256(canonical_json(verified_source)).hexdigest() != verified.source_sha256):
            raise ValueError("original verification digest differs")
        verification_source_matches(verified_source, verified)
        if verified.outcome == "verified":
            observed = publication_instant(verified.observed_at)
            public_by = min(public_by, observed) if public_by else observed
    return published, public_by
