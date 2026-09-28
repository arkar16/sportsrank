"""Executable regressions for GitHub-run provenance checks in publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/firebase-hosting-publish.yml"
STEP_NAME = "Verify the authenticated preparation origin and exact reference input"
WORKFLOW_PATH = ".github/workflows/firebase-hosting-publish.yml"
RUN_ID = "123456"
HEAD_SHA = "a" * 40


def _step_script(name: str) -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {name}")
    run = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index] == "        run: |"
    )
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line else "")
    return "\n".join(body) + "\n"


def _run_fixture(tmp_path: Path, **run_overrides: object) -> subprocess.CompletedProcess[str]:
    preparation = tmp_path / "preparation"
    preparation.mkdir()
    package_archive = preparation / "package.tar.gz"
    package_record = preparation / "package.json"
    package_archive.write_bytes(b"package archive")
    package_record.write_bytes(b"package record")
    archive_sha = hashlib.sha256(package_archive.read_bytes()).hexdigest()
    record_sha = hashlib.sha256(package_record.read_bytes()).hexdigest()

    (preparation / "publication-context.json").write_text(
        json.dumps({"candidate_commit": HEAD_SHA}), encoding="utf-8"
    )
    (preparation / "preparation-origin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "publication_preparation_origin",
                "repository": "arkar16/sportsrank",
                "workflow_path": WORKFLOW_PATH,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": HEAD_SHA,
                "run_id": RUN_ID,
                "run_attempt": "1",
            }
        ),
        encoding="utf-8",
    )
    (preparation / "publication-preparation-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "publication_preparation_manifest",
                "repository": "arkar16/sportsrank",
                "workflow_path": WORKFLOW_PATH,
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "head_sha": HEAD_SHA,
                "run_id": RUN_ID,
                "run_attempt": "1",
                "package_archive_sha256": archive_sha,
                "package_record_sha256": record_sha,
            }
        ),
        encoding="utf-8",
    )

    run = {
        "id": int(RUN_ID),
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": HEAD_SHA,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
    }
    run.update(run_overrides)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "$1" == "api" ]]; then
              test "$2" = "repos/arkar16/sportsrank/actions/runs/$EXPECTED_RUN_ID"
              printf '%s\\n' "$RUN_FIXTURE"
              exit 0
            fi
            if [[ "$1" == "run" && "$2" == "view" ]]; then
              echo 'Unknown JSON field: "path"' >&2
              exit 1
            fi
            echo "unexpected gh invocation" >&2
            exit 2
            """
        ),
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    github_env = tmp_path / "github-env"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(github_env),
        "OPERATION": "seal-only",
        "PREPARATION_RUN_ID": RUN_ID,
        "PREPARATION_ARTIFACT_NAME": f"sportsrank-preparation-{HEAD_SHA}",
        "SEALED_REFERENCE": "",
        "EXPECTED_RUN_ID": RUN_ID,
        "RUN_FIXTURE": json.dumps(run, separators=(",", ":")),
    }
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", _step_script(STEP_NAME)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class PublicationWorkflowCliTests(unittest.TestCase):
    def test_preparation_provenance_accepts_exact_rest_run_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _run_fixture(Path(directory))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preparation_provenance_rejects_mismatched_rest_run_fixture(self) -> None:
        overrides = (
            {"path": ".github/workflows/other.yml"},
            {"run_attempt": 2},
            {"head_sha": "b" * 40},
        )
        for override in overrides:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                result = _run_fixture(Path(directory), **override)
                self.assertNotEqual(result.returncode, 0)

    def test_every_run_provenance_lookup_uses_rest_api_fields(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("gh run view", source)
        self.assertEqual(
            source.count('gh api "repos/arkar16/sportsrank/actions/runs/$'), 3
        )
        for field in (
            ".id",
            ".path",
            ".event",
            ".head_branch",
            ".head_sha",
            ".run_attempt",
            ".status",
            ".conclusion",
        ):
            self.assertGreaterEqual(source.count(field), 3, field)


if __name__ == "__main__":
    unittest.main()
