from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.scripts.postdeploy_smoke import (
    DEFAULT_ATTEMPTS,
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_PAGE_TIMEOUT_SECONDS,
    DEFAULT_RETRY_DELAY_SECONDS,
    REQUIRED_PATHS,
    SmokeError,
    main,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "firebase-hosting-publish.yml"
PUBLISHED_URL = "https://www.sportsrank.top"

EXPECTED_REQUIRED_PATHS = (
    "index.html",
    "cfb/years/2023/rankings/2023_FINAL_FBS_cors.html",
    "cfb/years/2024/rankings/2024_FINAL_FBS_cors.html",
    "cfb/years/2025/rankings/2025_FINAL_FBS_cors.html",
    "cfb/years/2026/rankings/2026_PRESEASON_FBS_cors.html",
    "cfb/years/2026/data/slate/weekly_slate/2026_W0_FBS_slate.html",
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class PostdeploySmokeTests(unittest.TestCase):
    def _artifact(self, root: Path, *, omit: str | None = None) -> dict[str, bytes]:
        responses: dict[str, bytes] = {}
        for index, relative in enumerate(EXPECTED_REQUIRED_PATHS):
            if relative == omit:
                continue
            body = f"validated page {index}: {relative}\n".encode("utf-8")
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            responses[relative] = body
        return responses

    def _run_main(
        self,
        artifact_root: Path,
        fetch_page,
        clock: _FakeClock,
        *,
        base_url: str = PUBLISHED_URL,
        attempts: int = DEFAULT_ATTEMPTS,
        retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
        max_duration: float = DEFAULT_MAX_DURATION_SECONDS,
        timeout: float = DEFAULT_PAGE_TIMEOUT_SECONDS,
    ) -> tuple[int, str]:
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            result = main(
                [
                    "--base-url",
                    base_url,
                    "--artifact-root",
                    str(artifact_root),
                    "--timeout",
                    str(timeout),
                    "--attempts",
                    str(attempts),
                    "--retry-delay",
                    str(retry_delay),
                    "--max-duration",
                    str(max_duration),
                ],
                fetch_page=fetch_page,
                sleep=clock.sleep,
                clock=clock,
            )
        return result, stderr.getvalue()

    def test_required_paths_and_retry_defaults_are_explicit(self):
        self.assertEqual(REQUIRED_PATHS, EXPECTED_REQUIRED_PATHS)
        self.assertEqual(DEFAULT_ATTEMPTS, 3)
        self.assertEqual(DEFAULT_PAGE_TIMEOUT_SECONDS, 10.0)
        self.assertEqual(DEFAULT_RETRY_DELAY_SECONDS, 2.0)
        self.assertEqual(DEFAULT_MAX_DURATION_SECONDS, 180.0)

    def test_matching_pages_pass_and_receive_the_exact_configured_base_url(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[tuple[str, str, float]] = []

            def fetch_page(base_url: str, relative_path: str, timeout: float) -> bytes:
                calls.append((base_url, relative_path, timeout))
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 0, error)
        self.assertEqual([path for _, path, _ in calls], list(EXPECTED_REQUIRED_PATHS))
        self.assertEqual({base for base, _, _ in calls}, {PUBLISHED_URL})
        self.assertEqual({timeout for _, _, timeout in calls}, {DEFAULT_PAGE_TIMEOUT_SECONDS})
        self.assertEqual(clock.sleeps, [])

    def test_transient_http_failure_retries_the_entire_six_page_check(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[str] = []
            failed_once = False

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                nonlocal failed_once
                calls.append(relative_path)
                if relative_path == EXPECTED_REQUIRED_PATHS[2] and not failed_once:
                    failed_once = True
                    raise SmokeError("HTTP 503 transient failure")
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 0, error)
        self.assertEqual(calls, list(EXPECTED_REQUIRED_PATHS[:3]) + list(EXPECTED_REQUIRED_PATHS))
        self.assertEqual(clock.sleeps, [DEFAULT_RETRY_DELAY_SECONDS])

    def test_wrong_content_retries_the_entire_six_page_check(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[str] = []
            returned_wrong_once = False

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                nonlocal returned_wrong_once
                calls.append(relative_path)
                if relative_path == EXPECTED_REQUIRED_PATHS[1] and not returned_wrong_once:
                    returned_wrong_once = True
                    return b"generic 200 fallback"
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 0, error)
        self.assertEqual(calls, list(EXPECTED_REQUIRED_PATHS[:2]) + list(EXPECTED_REQUIRED_PATHS))
        self.assertEqual(clock.sleeps, [DEFAULT_RETRY_DELAY_SECONDS])

    def test_exhaustion_fails_nonzero_and_restarts_all_six_pages_each_attempt(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[str] = []

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                calls.append(relative_path)
                if relative_path == EXPECTED_REQUIRED_PATHS[-1]:
                    raise SmokeError("HTTP 503 persistent failure")
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 1)
        self.assertIn("verification exhausted after 3 attempts", error)
        self.assertEqual(calls, list(EXPECTED_REQUIRED_PATHS) * DEFAULT_ATTEMPTS)
        self.assertEqual(clock.sleeps, [DEFAULT_RETRY_DELAY_SECONDS] * (DEFAULT_ATTEMPTS - 1))

    def test_network_failure_is_retryable_then_succeeds_without_real_network_or_delay(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[str] = []
            failed_once = False

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                nonlocal failed_once
                calls.append(relative_path)
                if relative_path == EXPECTED_REQUIRED_PATHS[-1] and not failed_once:
                    failed_once = True
                    raise SmokeError("network unreachable")
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 0, error)
        self.assertEqual(calls, list(EXPECTED_REQUIRED_PATHS) + list(EXPECTED_REQUIRED_PATHS))
        self.assertEqual(clock.sleeps, [DEFAULT_RETRY_DELAY_SECONDS])

    def test_retry_window_caps_sleep_and_stops_before_attempt_limit(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root)
            calls: list[str] = []

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                calls.append(relative_path)
                if relative_path == EXPECTED_REQUIRED_PATHS[-1]:
                    raise SmokeError("persistent network failure")
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(
                artifact_root,
                fetch_page,
                clock,
                attempts=5,
                retry_delay=4,
                max_duration=5,
            )

        self.assertEqual(result, 1)
        self.assertIn("verification exhausted", error)
        self.assertEqual(clock.sleeps, [4, 1])
        self.assertLessEqual(len(calls), 5 * len(EXPECTED_REQUIRED_PATHS))
        self.assertLessEqual(clock.now, 5)

    def test_missing_required_artifact_fails_before_network_attempts(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            bodies = self._artifact(artifact_root, omit=EXPECTED_REQUIRED_PATHS[-1])
            calls: list[str] = []

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                calls.append(relative_path)
                return bodies[relative_path]

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock)

        self.assertEqual(result, 1)
        self.assertIn("required artifact", error)
        self.assertEqual(calls, [])
        self.assertEqual(clock.sleeps, [])

    def test_empty_or_invalid_base_url_fails_before_network_attempts(self):
        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            self._artifact(artifact_root)
            calls: list[str] = []

            def fetch_page(_base_url: str, relative_path: str, _timeout: float) -> bytes:
                calls.append(relative_path)
                return b"unexpected"

            clock = _FakeClock()
            result, error = self._run_main(artifact_root, fetch_page, clock, base_url="")

        self.assertEqual(result, 1)
        self.assertIn("absolute HTTP(S)", error)
        self.assertEqual(calls, [])
        self.assertEqual(clock.sleeps, [])

    def test_workflow_pins_uv_serializes_production_and_runs_bounded_coordinator(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("uses: astral-sh/setup-uv@v9.0.0", workflow)
        self.assertNotIn("uses: astral-sh/setup-uv@v9\n", workflow)
        self.assertIn(
            "concurrency:\n  group: sportsrank-production-publication\n  cancel-in-progress: false",
            workflow,
        )
        self.assertNotIn("job.environment.url", workflow)
        protected_start = workflow.index("\n  protected:")
        protected = workflow[protected_start:]
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment:\n      name: production", protected)
        self.assertIn("actions: read", protected)
        self.assertIn("google-github-actions/auth@v2", protected)
        self.assertIn("Verify the authenticated preparation origin and exact reference input", protected)
        self.assertIn("gh run view", protected)
        self.assertIn("releases/assets/$asset_id", protected)
        self.assertIn("id: preparation_transport", protected)
        self.assertIn("gh run download", protected)
        self.assertIn('echo "available=false" >> "$GITHUB_OUTPUT"', protected)
        self.assertIn("actions/attest-build-provenance@v2", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("gh attestation verify", protected)
        self.assertIn("--signer-workflow", protected)
        self.assertIn("--source-ref", protected)
        self.assertIn("--source-digest", protected)
        self.assertIn("--deny-self-hosted-runners", protected)
        self.assertIn("gh api", protected)
        self.assertIn("commit.tree.sha", protected)
        self.assertIn("reachable-objects.txt", protected)
        self.assertIn("github-tree.json", protected)
        self.assertIn("config/sr7-recovery-inputs.json", workflow)
        self.assertIn("--trusted-input-manifest", workflow)
        self.assertIn("sha256sum -c", protected)
        self.assertIn("candidate-tree.tar.gz.sha256", protected)
        self.assertIn("candidate-tree.tar.gz", protected)
        self.assertIn("archive --format=tar", protected)
        self.assertIn("cmp \"$RUNNER_TEMP/execution-source.expected.tar.gz\"", protected)
        self.assertIn("prepare-reviewed", workflow)
        self.assertIn("python -m cfb.publication_cli", protected)
        self.assertIn("prior_reference", workflow)
        self.assertIn("publication-state", protected)
        self.assertNotIn("FirebaseExtended/action-hosting-deploy", workflow)
        self.assertNotIn("npm run deploy", workflow)
        self.assertNotIn("PUBLISHED_URL", workflow)
        self.assertNotIn("candidate_sha", workflow)
        self.assertNotIn("base_sha", workflow)
        self.assertNotIn("continue-on-error: true", protected)
        self.assertNotIn("|| true", protected)

        attestation = protected.index(
            "Verify the preparation attestation before runtime materialization"
        )
        materialize = protected.index(
            "Materialize the exact execution code without a source checkout"
        )
        install = protected.index("Install the transported locked runtime")
        firebase_auth = protected.index("Obtain the gated Firebase access token")
        self.assertLess(attestation, materialize)
        self.assertLess(attestation, install)
        self.assertLess(attestation, firebase_auth)

        summary_start = workflow.index(
            "      - name: Expose the exact package, baseline, and approval context"
        )
        summary_end = workflow.index(
            "      - name: Attest the exact preparation manifest and package digests",
            summary_start,
        )
        self.assertNotIn("evidence_references", workflow[summary_start:summary_end])

        prepare_end = workflow.index("\n  protected:")
        self.assertEqual(workflow[:prepare_end].count("actions/checkout@"), 1)
        self.assertNotIn("actions/checkout@", protected)


if __name__ == "__main__":
    unittest.main()
