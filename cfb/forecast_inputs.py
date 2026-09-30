"""Explicit operator inputs for issued forecast history and reviewed timing.

History files are locators, never trust assertions. Every use authenticates the
immutable archive references again. Timing is a separately reviewed, hash-pinned
supplement with retained source bytes; scheduled kickoff is not accepted here.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from .forecast_record import ForecastContractError, GameTimingEvidence
from .forecast_publication import VerifiedForecastPublication, load_verified_forecasts
from .github_archive import GitHubReleaseArchive
from .publication import RecordedPublicationAttempt, SealedAttempt, SealedRecordEvidence, retrieve_sealed_attempt
from .publication_authorization import GitHubPreparationProvenanceReader
from .publication_records import (
    ArchiveReference, AttemptIntentRecord, ProviderResultRecord,
    ValidatedPackageRecord, VerificationRecord, SealedAttemptReference, canonical_json,
)

REPOSITORY = "arkar16/sportsrank"
HISTORY_SCHEMA = "forecast-history-locators/v1"
TIMING_SCHEMA = "reviewed-forecast-timing/v1"


@dataclass(frozen=True)
class ForecastInputs:
    publications: tuple[VerifiedForecastPublication, ...] = ()
    timing: tuple[GameTimingEvidence, ...] = ()


def _exact(value: Any, names: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != names:
        raise ForecastContractError(f"{label} has invalid fields")
    return value


def load_history(path: str | Path, *, archive: Any = None,
                 repository: str = REPOSITORY, provenance_reader: Any = None) -> tuple[VerifiedForecastPublication, ...]:
    """Revalidate locators from a retained operator history file, without writes."""
    raw = json.loads(Path(path).read_bytes())
    if isinstance(raw, Mapping) and raw.get("schema_version") == "forecast-ledger/v1":
        from .forecast_release import validate_public_forecast_json
        if not validate_public_forecast_json(raw):
            raise ForecastContractError("forecast history ledger has invalid fields")
        entries = raw["publication_provenance"]
    else:
        _exact(raw, {"schema_version", "publications"}, "forecast history")
        if raw["schema_version"] != HISTORY_SCHEMA or not isinstance(raw["publications"], list):
            raise ForecastContractError("unsupported forecast history")
        entries = raw["publications"]
    archive = archive if archive is not None else GitHubReleaseArchive()
    publications = []
    fields = {"intent", "package", "package_record", "validation", "provider_result",
              "provider_source", "verification", "verification_source"}
    with tempfile.TemporaryDirectory(prefix="sportsrank-forecast-history-") as directory:
        for index, item in enumerate(entries):
            root = Path(directory) / str(index)
            root.mkdir()
            if isinstance(item, Mapping) and item.get("schema_version") == "forecast-publication-evidence/v1":
                _exact(item, (fields - {"package_record", "validation"}) | {"schema_version", "repository"}, "forecast publication provenance")
                if item["repository"] != repository:
                    raise ForecastContractError("forecast history belongs to another repository")
                intent_ref = ArchiveReference.from_dict(item["intent"])
                if intent_ref.repository != repository:
                    raise ForecastContractError("forecast intent belongs to another repository")
                intent_path = archive.retrieve_and_verify(intent_ref, root / "locator-intent.json")
                locator_intent = AttemptIntentRecord.from_dict(json.loads(intent_path.read_bytes()))
                if locator_intent.package_record_reference is None or locator_intent.validation_reference is None:
                    raise ForecastContractError("legacy history requires explicit package_record and validation locators")
                item = {key: value for key, value in item.items() if key not in {"schema_version", "repository"}}
                item.update(package_record=locator_intent.package_record_reference.to_dict(),
                            validation=locator_intent.validation_reference.to_dict())
            _exact(item, fields, "forecast publication locators")
            refs = {key: ArchiveReference.from_dict(value) for key, value in item.items()}
            if any(ref.repository != repository for ref in refs.values()):
                raise ForecastContractError("forecast history belongs to another repository")
            paths = {key: archive.retrieve_and_verify(ref, root / f"{key}.json") for key, ref in refs.items()}
            package = ValidatedPackageRecord.from_dict(json.loads(paths["package_record"].read_bytes()))
            intent = AttemptIntentRecord.from_dict(json.loads(paths["intent"].read_bytes()), package=package)
            if (refs["package"] != intent.artifact_reference
                    or refs["intent"].sha256 != intent.digest
                    or refs["package_record"].sha256 != package.digest
                    or any(ref.target_commit != package.candidate_commit for ref in refs.values())):
                raise ForecastContractError("forecast history refs do not bind the exact attempt")
            for role, pinned in (("package_record", intent.package_record_reference),
                                 ("validation", intent.validation_reference)):
                if pinned is not None and refs[role] != pinned:
                    raise ForecastContractError("forecast history contradicts durable attempt references")
            provider = ProviderResultRecord.from_dict(json.loads(paths["provider_result"].read_bytes()), intent=intent)
            verification = VerificationRecord.from_dict(json.loads(paths["verification"].read_bytes()), intent=intent, provider_result=provider)
            for role, record in (("intent", intent), ("package_record", package),
                                 ("provider_result", provider), ("verification", verification)):
                if paths[role].read_bytes() != canonical_json(record.to_dict()):
                    raise ForecastContractError("forecast history record bytes are not canonical")
            attempt = SealedAttempt(package, intent, refs["package"], refs["package_record"],
                                    refs["validation"], refs["intent"], paths["package"],
                                    paths["package_record"], paths["validation"], paths["intent"], {})
            durable = (intent.package_record_reference, intent.validation_reference,
                       intent.preparation_manifest_reference, intent.preparation_origin_reference,
                       intent.candidate_tree_reference)
            if intent.candidate_bundle_reference is not None:
                raise ForecastContractError("legacy Git-bundle history requires a reviewed offline evidence migration; it is not current-tree preparation")
            if any(value is not None for value in durable):
                # Durable attempts must authenticate the preparation attestation
                # and current-tree evidence through the existing reader. Only
                # historical intents lacking all five refs use the legacy path.
                if any(value is None for value in durable):
                    raise ForecastContractError("durable forecast history references are incomplete")
                if provenance_reader is None:
                    provenance_reader = GitHubPreparationProvenanceReader.from_github_token()
                restored = retrieve_sealed_attempt(
                    SealedAttemptReference(refs["intent"]), archive=archive,
                    repository=repository, provenance_reader=provenance_reader,
                    retrieval_directory=root / "durable",
                )
                if (restored.attempt.intent != intent or restored.attempt.package != package
                        or restored.attempt.package_record_reference != refs["package_record"]
                        or restored.attempt.validation_reference != refs["validation"]):
                    raise ForecastContractError("reconstructed durable attempt differs from history")
                attempt = restored.attempt
            recorded = RecordedPublicationAttempt(
                attempt, provider, SealedRecordEvidence(refs["provider_result"], refs["provider_source"], paths["provider_result"], paths["provider_source"]),
                verification, SealedRecordEvidence(refs["verification"], refs["verification_source"], paths["verification"], paths["verification_source"]),
            )
            publications.append(load_verified_forecasts(recorded, archive=archive, repository=repository, destination=root / "verified"))
    return tuple(publications)


def load_reviewed_timing(path: str | Path, expected_sha256: str) -> tuple[GameTimingEvidence, ...]:
    """Check a reviewed supplement and its retained source bytes independently.

    The digest must come from the review record, not the candidate. Source truth
    requires that review; a checksum alone is not evidence of an actual kickoff.
    """
    path = Path(path)
    data = path.read_bytes()
    if (not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or hashlib.sha256(data).hexdigest() != expected_sha256):
        raise ForecastContractError("reviewed timing supplement digest differs")
    raw = _exact(json.loads(data), {"schema_version", "evidence", "sources"}, "reviewed timing")
    if raw["schema_version"] != TIMING_SCHEMA or not isinstance(raw["evidence"], list) or not isinstance(raw["sources"], dict):
        raise ForecastContractError("unsupported reviewed timing supplement")
    evidence = tuple(GameTimingEvidence.from_dict(item) for item in raw["evidence"])
    if len({item.game.key for item in evidence}) != len(evidence):
        raise ForecastContractError("duplicate game timing evidence")
    expected_sources = {item.source.digest for item in evidence}
    if set(raw["sources"]) != expected_sources:
        raise ForecastContractError("timing source inventory differs from evidence")
    for digest, relative in raw["sources"].items():
        if not isinstance(relative, str):
            raise ForecastContractError("timing source path must be relative")
        source = (path.parent / relative).resolve()
        if Path(relative).is_absolute() or not source.is_relative_to(path.parent.resolve()) or source == path.resolve():
            raise ForecastContractError("timing source escapes its retained supplement")
        if "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ForecastContractError("retained game timing source digest differs")
    return evidence


def add_forecast_arguments(parser: argparse.ArgumentParser, *, corrections: bool = False) -> None:
    parser.add_argument("--forecast-history", type=Path, help="immutable publication locators; revalidated through GitHub archive reads")
    parser.add_argument("--forecast-timing", type=Path, help="reviewed actual-start/not-started evidence supplement")
    parser.add_argument("--forecast-timing-sha256", help="independent reviewed supplement hash")
    if corrections:
        parser.add_argument("--forecast-corrections", type=Path, help="explicit predecessor version to correction reason JSON object")


def load_forecast_inputs(args: argparse.Namespace) -> ForecastInputs:
    history = getattr(args, "forecast_history", None)
    timing = getattr(args, "forecast_timing", None)
    digest = getattr(args, "forecast_timing_sha256", None)
    if (timing is None) != (digest is None):
        raise ForecastContractError("timing supplement and reviewed hash must be supplied together")
    return ForecastInputs(load_history(history) if history else (), load_reviewed_timing(timing, digest) if timing else ())


def load_corrections(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_bytes())
    if not isinstance(raw, dict) or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", key)
            or not isinstance(value, str) or not value.strip() for key, value in raw.items()):
        raise ForecastContractError("forecast corrections require predecessor digests and nonempty reasons")
    return raw
