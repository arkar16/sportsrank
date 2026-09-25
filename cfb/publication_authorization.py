"""Authenticated GitHub production-approval evidence for publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .publication_records import ValidatedPackageRecord, canonical_json


class PublicationAuthorizationError(RuntimeError):
    """The current runtime cannot prove the required owner approval."""


@dataclass(frozen=True)
class GitHubRuntimeContext:
    repository: str
    run_id: str
    run_attempt: str
    ref: str
    sha: str
    event: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "GitHubRuntimeContext":
        values = os.environ if environment is None else environment
        names = (
            "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
            "GITHUB_REF", "GITHUB_SHA", "GITHUB_EVENT_NAME",
        )
        try:
            resolved = tuple(values[name] for name in names)
        except KeyError as exc:
            raise PublicationAuthorizationError(
                "GitHub protected-runtime context is incomplete"
            ) from exc
        return cls(*resolved)


class ApprovalReader(Protocol):
    def run(self, repository: str, run_id: str) -> Mapping[str, Any]: ...
    def approvals(
        self, repository: str, run_id: str
    ) -> Sequence[Mapping[str, Any]]: ...


class GitHubApprovalReader:
    """Read-only authenticated GitHub Actions run and approval history."""

    def __init__(
        self, token: Callable[[], str], *, timeout: float = 30.0
    ) -> None:
        self._token = token
        self._timeout = timeout

    @classmethod
    def from_github_token(
        cls, environment: Mapping[str, str] | None = None, *, timeout: float = 30.0
    ) -> "GitHubApprovalReader":
        values = os.environ if environment is None else environment
        return cls(lambda: values.get("GITHUB_TOKEN", ""), timeout=timeout)

    def _get(self, resource: str) -> Any:
        token = self._token()
        if not isinstance(token, str) or not token:
            raise PublicationAuthorizationError("GitHub Actions read token is unavailable")
        request = Request(
            "https://api.github.com/" + resource,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
                if response.status != 200 or len(body) > 4 * 1024 * 1024:
                    raise PublicationAuthorizationError(
                        "GitHub authorization evidence response is invalid"
                    )
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PublicationAuthorizationError(
                "GitHub authorization evidence is unavailable"
            ) from exc
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationAuthorizationError(
                "GitHub authorization evidence is not JSON"
            ) from exc

    def run(self, repository: str, run_id: str) -> Mapping[str, Any]:
        value = self._get(f"repos/{repository}/actions/runs/{run_id}")
        if not isinstance(value, Mapping):
            raise PublicationAuthorizationError("GitHub run evidence is invalid")
        return value

    def approvals(
        self, repository: str, run_id: str
    ) -> Sequence[Mapping[str, Any]]:
        value = self._get(f"repos/{repository}/actions/runs/{run_id}/approvals")
        if not isinstance(value, list) or any(
            not isinstance(item, Mapping) for item in value
        ):
            raise PublicationAuthorizationError("GitHub approval evidence is invalid")
        return tuple(value)


class FakeGitHubApprovalReader:
    """Offline approval reader; fixtures remain data, never authorization flags."""

    def __init__(
        self,
        run: Mapping[str, Any],
        approvals: Sequence[Mapping[str, Any]],
    ) -> None:
        self.run_value = dict(run)
        self.approval_values = tuple(dict(value) for value in approvals)
        self.read_count = 0

    def run(self, repository: str, run_id: str) -> Mapping[str, Any]:
        self.read_count += 1
        return dict(self.run_value)

    def approvals(
        self, repository: str, run_id: str
    ) -> Sequence[Mapping[str, Any]]:
        self.read_count += 1
        return tuple(dict(value) for value in self.approval_values)


_EVIDENCE_TOKEN = object()
_WORKFLOW_PATH = ".github/workflows/firebase-hosting-publish.yml"
_BRANCH = "main"
_ENVIRONMENT = "production"
_OWNER_LOGIN = "arkar16"
_OWNER_ID = 18_407_890


@dataclass(frozen=True, init=False)
class ProtectedExecutionEvidence:
    """Coordinator-created result of authenticated run and approval checks."""

    context: Mapping[str, str]
    package_sha256: str
    candidate_commit: str

    def __init__(
        self,
        context: Mapping[str, str],
        package_sha256: str,
        candidate_commit: str,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _EVIDENCE_TOKEN:
            raise PublicationAuthorizationError(
                "protected execution evidence can only come from authenticated checks"
            )
        object.__setattr__(self, "context", MappingProxyType(dict(context)))
        object.__setattr__(self, "package_sha256", package_sha256)
        object.__setattr__(self, "candidate_commit", candidate_commit)

    @classmethod
    def _create(
        cls,
        context: Mapping[str, str],
        package_sha256: str,
        candidate_commit: str,
    ) -> "ProtectedExecutionEvidence":
        return cls(
            context, package_sha256, candidate_commit, _token=_EVIDENCE_TOKEN
        )


def authorize_protected_execution(
    package: ValidatedPackageRecord,
    *,
    runtime: GitHubRuntimeContext,
    github: ApprovalReader,
    expected_repository: str = "arkar16/sportsrank",
) -> ProtectedExecutionEvidence:
    """Fail closed unless GitHub proves this exact first-run owner approval."""

    if (
        runtime.repository != expected_repository
        or runtime.run_attempt != "1"
        or not runtime.run_id.isdigit()
        or int(runtime.run_id) < 1
        or runtime.ref != f"refs/heads/{_BRANCH}"
        or runtime.sha != package.candidate_commit
        or runtime.event != "workflow_dispatch"
    ):
        raise PublicationAuthorizationError(
            "runtime does not identify the exact protected publication run"
        )

    run = github.run(expected_repository, runtime.run_id)
    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    run_path = run.get("path")
    accepted_run_paths = {
        _WORKFLOW_PATH,
        f"{_WORKFLOW_PATH}@{_BRANCH}",
    }
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or str(run_id) != runtime.run_id
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt != 1
        or not isinstance(run_path, str)
        or run_path not in accepted_run_paths
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != _BRANCH
        or run.get("head_sha") != runtime.sha
    ):
        raise PublicationAuthorizationError(
            "GitHub run evidence does not match the protected runtime"
        )

    production_entries: list[Mapping[str, Any]] = []
    for entry in github.approvals(expected_repository, runtime.run_id):
        environments = entry.get("environments")
        if not isinstance(environments, list):
            raise PublicationAuthorizationError(
                "GitHub approval environment evidence is invalid"
            )
        names = [
            item.get("name") for item in environments if isinstance(item, Mapping)
        ]
        if len(names) != len(environments):
            raise PublicationAuthorizationError(
                "GitHub approval environment evidence is invalid"
            )
        if _ENVIRONMENT in names:
            production_entries.append(entry)
    if len(production_entries) != 1:
        raise PublicationAuthorizationError(
            "GitHub production approval history is missing or ambiguous"
        )
    approval = production_entries[0]
    environments = approval["environments"]
    user = approval.get("user")
    if (
        approval.get("state") != "approved"
        or len(environments) != 1
        or environments[0].get("name") != _ENVIRONMENT
        or not isinstance(user, Mapping)
        or user.get("login") != _OWNER_LOGIN
        or isinstance(user.get("id"), bool)
        or not isinstance(user.get("id"), int)
        or user.get("id") != _OWNER_ID
    ):
        raise PublicationAuthorizationError(
            "GitHub production approval is not the required owner approval"
        )

    workflow_ref = (
        f"{expected_repository}/{_WORKFLOW_PATH}@refs/heads/{_BRANCH}"
    )
    return ProtectedExecutionEvidence._create(
        {
            "repository": expected_repository,
            "workflow_ref": workflow_ref,
            "workflow_sha": runtime.sha,
            "run_id": runtime.run_id,
            "run_attempt": runtime.run_attempt,
            "environment": _ENVIRONMENT,
            "event": runtime.event,
            "ref": runtime.ref,
            "head_sha": runtime.sha,
            "approval_state": "approved",
            "approver_login": _OWNER_LOGIN,
            "approver_id": str(_OWNER_ID),
        },
        package.digest,
        package.candidate_commit,
    )


class PreparationProvenanceError(PublicationAuthorizationError):
    """An exact preparation artifact lacks trusted GitHub provenance."""


PREPARATION_WORKFLOW_PATH = _WORKFLOW_PATH
PREPARATION_BRANCH = _BRANCH
PREPARATION_REPOSITORY = "arkar16/sportsrank"
PREPARATION_MANIFEST_NAME = "publication-preparation-manifest.json"
_PREPARATION_MANIFEST_LIMIT = 64 * 1024
_ATTESTATION_OUTPUT_LIMIT = 4 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _preparation_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _preparation_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise PreparationProvenanceError(f"preparation manifest {name} is invalid")
    return value


@dataclass(frozen=True)
class PreparationManifest:
    """Strict data whose exact bytes are authenticated by GitHub provenance."""

    repository: str
    workflow_path: str
    event: str
    ref: str
    head_sha: str
    run_id: str
    run_attempt: str
    package_archive_sha256: str
    package_record_sha256: str

    @classmethod
    def from_bytes(cls, value: bytes) -> "PreparationManifest":
        if len(value) > _PREPARATION_MANIFEST_LIMIT:
            raise PreparationProvenanceError("preparation manifest is too large")
        try:
            raw = json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreparationProvenanceError("preparation manifest is not JSON") from exc
        names = {
            "schema_version", "record_type", "repository", "workflow_path",
            "event", "ref", "head_sha", "run_id", "run_attempt",
            "package_archive_sha256", "package_record_sha256",
        }
        if not isinstance(raw, Mapping) or set(raw) != names:
            raise PreparationProvenanceError("preparation manifest fields are invalid")
        if (
            isinstance(raw["schema_version"], bool)
            or not isinstance(raw["schema_version"], int)
            or raw["schema_version"] != 1
            or raw["record_type"] != "publication_preparation_manifest"
        ):
            raise PreparationProvenanceError("preparation manifest schema is invalid")
        values = tuple(_preparation_text(raw[name], name) for name in (
            "repository", "workflow_path", "event", "ref", "head_sha",
            "run_id", "run_attempt", "package_archive_sha256",
            "package_record_sha256",
        ))
        manifest = cls(*values)
        if not _COMMIT.fullmatch(manifest.head_sha):
            raise PreparationProvenanceError("preparation manifest head_sha is invalid")
        if (
            not manifest.run_id.isdigit()
            or int(manifest.run_id) < 1
            or manifest.run_attempt != "1"
        ):
            raise PreparationProvenanceError("preparation manifest run identity is invalid")
        if (
            not _SHA256.fullmatch(manifest.package_archive_sha256)
            or not _SHA256.fullmatch(manifest.package_record_sha256)
        ):
            raise PreparationProvenanceError("preparation manifest artifact digest is invalid")
        return manifest

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema_version": 1,
            "record_type": "publication_preparation_manifest",
            "repository": self.repository,
            "workflow_path": self.workflow_path,
            "event": self.event,
            "ref": self.ref,
            "head_sha": self.head_sha,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "package_archive_sha256": self.package_archive_sha256,
            "package_record_sha256": self.package_record_sha256,
        })


def preparation_manifest_bytes(
    *,
    repository: str,
    workflow_path: str,
    event: str,
    ref: str,
    head_sha: str,
    run_id: str,
    run_attempt: str,
    package_archive_sha256: str,
    package_record_sha256: str,
) -> bytes:
    """Create the exact manifest grammar that the preparation job attests."""

    manifest = PreparationManifest.from_bytes(json.dumps({
        "schema_version": 1,
        "record_type": "publication_preparation_manifest",
        "repository": repository,
        "workflow_path": workflow_path,
        "event": event,
        "ref": ref,
        "head_sha": head_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "package_archive_sha256": package_archive_sha256,
        "package_record_sha256": package_record_sha256,
    }).encode("utf-8"))
    return manifest.to_bytes()


_PREPARATION_READER_TOKEN = object()


def _run_attestation_command(
    command: Sequence[str],
) -> subprocess.CompletedProcess[bytes]:
    """Invoke the cryptographic verifier; tests replace only this transport."""

    return subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class GitHubPreparationProvenanceReader:
    """Cryptographically verify a preparation manifest and read its GitHub run."""

    def __init__(
        self,
        github: ApprovalReader,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _PREPARATION_READER_TOKEN:
            raise PreparationProvenanceError(
                "production provenance readers require owned GitHub construction"
            )
        self._github = github

    @classmethod
    def from_github_token(
        cls, environment: Mapping[str, str] | None = None
    ) -> "GitHubPreparationProvenanceReader":
        return cls(
            GitHubApprovalReader.from_github_token(environment),
            _token=_PREPARATION_READER_TOKEN,
        )

    def verify_attestation(
        self,
        artifact: Path,
        *,
        repository: str,
        signer_workflow: str,
        source_ref: str,
        source_digest: str,
    ) -> None:
        command = [
            "gh", "attestation", "verify", str(artifact),
            "--repo", repository,
            "--signer-workflow", signer_workflow,
            "--source-ref", source_ref,
            "--source-digest", source_digest,
            "--deny-self-hosted-runners",
            "--format", "json",
            "--limit", "30",
        ]
        try:
            result = _run_attestation_command(command)
        except OSError as exc:
            raise PreparationProvenanceError(
                "GitHub preparation attestation verification is unavailable"
            ) from exc
        stdout = result.stdout
        if (
            isinstance(result.returncode, bool)
            or not isinstance(result.returncode, int)
            or result.returncode != 0
            or not isinstance(stdout, bytes)
            or len(stdout) > _ATTESTATION_OUTPUT_LIMIT
        ):
            raise PreparationProvenanceError(
                "GitHub preparation attestation verification failed"
            )
        try:
            verified = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreparationProvenanceError(
                "GitHub preparation attestation evidence is invalid"
            ) from exc
        try:
            expected = _preparation_digest(artifact.read_bytes())
        except OSError as exc:
            raise PreparationProvenanceError(
                "preparation manifest changed during attestation verification"
            ) from exc
        if not isinstance(verified, list) or not verified:
            raise PreparationProvenanceError(
                "GitHub preparation attestation evidence is missing"
            )
        for entry in verified:
            if not isinstance(entry, Mapping):
                raise PreparationProvenanceError(
                    "GitHub preparation attestation evidence is invalid"
                )
            verification = entry.get("verificationResult")
            statement = (
                verification.get("statement")
                if isinstance(verification, Mapping) else None
            )
            subjects = statement.get("subject") if isinstance(statement, Mapping) else None
            if not isinstance(subjects, list) or not any(
                isinstance(subject, Mapping)
                and isinstance(subject.get("digest"), Mapping)
                and subject["digest"].get("sha256") == expected
                for subject in subjects
            ):
                raise PreparationProvenanceError(
                    "GitHub preparation attestation subject is invalid"
                )

    def run(self, repository: str, run_id: str) -> Mapping[str, Any]:
        return self._github.run(repository, run_id)


_PREPARATION_EVIDENCE_TOKEN = object()


@dataclass(frozen=True, init=False)
class AuthenticatedPreparationEvidence:
    """Module-issued capability binding an attested manifest to its exact run."""

    manifest: PreparationManifest
    manifest_sha256: str

    def __init__(
        self,
        manifest: PreparationManifest,
        manifest_sha256: str,
        *,
        _token: object | None = None,
    ) -> None:
        if _token is not _PREPARATION_EVIDENCE_TOKEN:
            raise PreparationProvenanceError(
                "authenticated preparation evidence requires verified provenance"
            )
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "manifest_sha256", manifest_sha256)


def authenticate_preparation_manifest(
    path: str | Path,
    *,
    reader: GitHubPreparationProvenanceReader,
    expected_candidate_commit: str,
) -> AuthenticatedPreparationEvidence:
    """Verify exact manifest bytes, signer identity, source, and completed run."""

    if type(reader) is not GitHubPreparationProvenanceReader:
        raise PreparationProvenanceError(
            "preparation provenance requires the concrete GitHub verifier"
        )

    manifest_path = Path(path)
    try:
        value = manifest_path.read_bytes()
    except OSError as exc:
        raise PreparationProvenanceError("preparation manifest is unavailable") from exc
    manifest = PreparationManifest.from_bytes(value)
    expected_ref = f"refs/heads/{PREPARATION_BRANCH}"
    if (
        manifest.repository != PREPARATION_REPOSITORY
        or manifest.workflow_path != PREPARATION_WORKFLOW_PATH
        or manifest.event != "workflow_dispatch"
        or manifest.ref != expected_ref
        or manifest.head_sha != expected_candidate_commit
    ):
        raise PreparationProvenanceError(
            "preparation manifest does not identify the trusted preparation run"
        )
    signer_workflow = (
        f"{PREPARATION_REPOSITORY}/{PREPARATION_WORKFLOW_PATH}"
    )
    reader.verify_attestation(
        manifest_path,
        repository=PREPARATION_REPOSITORY,
        signer_workflow=signer_workflow,
        source_ref=expected_ref,
        source_digest=expected_candidate_commit,
    )
    try:
        if manifest_path.read_bytes() != value:
            raise PreparationProvenanceError(
                "preparation manifest changed during attestation verification"
            )
    except OSError as exc:
        raise PreparationProvenanceError(
            "preparation manifest changed during attestation verification"
        ) from exc
    run = reader.run(PREPARATION_REPOSITORY, manifest.run_id)
    if not isinstance(run, Mapping):
        raise PreparationProvenanceError("GitHub preparation run evidence is invalid")
    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    accepted_paths = {
        PREPARATION_WORKFLOW_PATH,
        f"{PREPARATION_WORKFLOW_PATH}@{PREPARATION_BRANCH}",
    }
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or str(run_id) != manifest.run_id
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt != 1
        or run.get("path") not in accepted_paths
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != PREPARATION_BRANCH
        or run.get("head_sha") != expected_candidate_commit
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise PreparationProvenanceError(
            "GitHub preparation run evidence does not match the attested manifest"
        )
    return AuthenticatedPreparationEvidence(
        manifest,
        _preparation_digest(value),
        _token=_PREPARATION_EVIDENCE_TOKEN,
    )
