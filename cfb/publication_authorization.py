"""Authenticated GitHub production-approval evidence for publication."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .publication_records import ValidatedPackageRecord


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
    expected_workflow_path: str = ".github/workflows/firebase-hosting-publish.yml",
    expected_branch: str = "main",
    expected_environment: str = "production",
    expected_owner_login: str = "arkar16",
    expected_owner_id: int = 18_407_890,
) -> ProtectedExecutionEvidence:
    """Fail closed unless GitHub proves this exact first-run owner approval."""

    if (
        runtime.repository != expected_repository
        or runtime.run_attempt != "1"
        or not runtime.run_id.isdigit()
        or int(runtime.run_id) < 1
        or runtime.ref != f"refs/heads/{expected_branch}"
        or runtime.sha != package.candidate_commit
        or runtime.event != "workflow_dispatch"
    ):
        raise PublicationAuthorizationError(
            "runtime does not identify the exact protected publication run"
        )

    run = github.run(expected_repository, runtime.run_id)
    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or str(run_id) != runtime.run_id
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or run_attempt != 1
        or run.get("path") != expected_workflow_path
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != expected_branch
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
        if expected_environment in names:
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
        or environments[0].get("name") != expected_environment
        or not isinstance(user, Mapping)
        or user.get("login") != expected_owner_login
        or isinstance(user.get("id"), bool)
        or user.get("id") != expected_owner_id
    ):
        raise PublicationAuthorizationError(
            "GitHub production approval is not the required owner approval"
        )

    workflow_ref = (
        f"{expected_repository}/{expected_workflow_path}@refs/heads/{expected_branch}"
    )
    return ProtectedExecutionEvidence._create(
        {
            "repository": expected_repository,
            "workflow_ref": workflow_ref,
            "workflow_sha": runtime.sha,
            "run_id": runtime.run_id,
            "run_attempt": runtime.run_attempt,
            "environment": expected_environment,
            "event": runtime.event,
            "ref": runtime.ref,
            "head_sha": runtime.sha,
            "approval_state": "approved",
            "approver_login": expected_owner_login,
            "approver_id": str(expected_owner_id),
        },
        package.digest,
        package.candidate_commit,
    )
