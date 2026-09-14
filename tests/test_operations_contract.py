"""Static checks for the credential-safe hosting and recovery operations path."""

import json
from pathlib import Path
import tomllib
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


class OperationsContractTests(unittest.TestCase):
    def test_only_manual_firebase_workflow_remains(self):
        workflows = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
        self.assertEqual([path.name for path in workflows], ["firebase-hosting-publish.yml"])

        source = workflows[0].read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertNotRegex(source, r"(?m)^\s*(push|pull_request|schedule):")
        self.assertIn("actions/checkout@v6", source)
        self.assertEqual(source.count("actions/checkout@v6"), 1)
        self.assertIn("actions/setup-python@v6", source)
        self.assertIn("astral-sh/setup-uv@v9", source)
        self.assertIn("validate website", source)
        self.assertIn("--published-site", source)
        self.assertIn("--json", source)
        self.assertIn("needs: validate", source)
        self.assertIn("name: production", source)
        self.assertRegex(source, r"(?ms)^\s+candidate_sha:\n\s+description:.*\n\s+required: true\n\s+type: string")
        self.assertRegex(source, r"(?ms)^\s+base_sha:\n\s+description:.*\n\s+required: true\n\s+type: string")
        self.assertNotIn("inputs.ref", source)
        self.assertNotIn("candidate:", source)

    def test_workflow_rejects_mutable_or_unmerged_commit_inputs(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")

        self.assertIn("^[0-9a-f]{40}$", source)
        self.assertIn("git cat-file -t", source)
        self.assertIn('git rev-parse --verify "$sha^{commit}"', source)
        self.assertIn('git merge-base --is-ancestor "$BASE_SHA" "$CANDIDATE_SHA"', source)
        self.assertIn('refs/remotes/origin/$DEFAULT_BRANCH', source)
        self.assertIn('git merge-base --is-ancestor "$CANDIDATE_SHA" "$default_tip"', source)
        self.assertIn("candidate_sha is not merged into the default branch", source)
        self.assertIn("fetch-depth: 0", source)
        self.assertIn("fetch-tags: false", source)

    def test_publish_job_consumes_one_attested_artifact_without_checkout(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        validation, publish = source.split("\n  publish:", 1)

        self.assertEqual(source.count("actions/upload-artifact@v4"), 1)
        self.assertEqual(source.count("actions/download-artifact@v4"), 1)
        self.assertEqual(source.count("actions/attest-build-provenance@v2"), 1)
        self.assertIn("Package the validated site once", validation)
        self.assertIn("git archive", validation)
        self.assertIn('git archive --format=tar --output="$base_archive" "$BASE_SHA" website', validation)
        self.assertIn('git cat-file -t "$BASE_SHA"', validation)
        self.assertIn('git rev-parse --verify "$BASE_SHA^{commit}"', validation)
        self.assertIn("$RUNNER_TEMP", validation)
        self.assertIn('test -d "$base_site"', validation)
        self.assertIn('test -f "$base_site/index.html"', validation)
        self.assertIn('validate website --published-site "$base_site" --json', validation)
        self.assertNotIn('$GITHUB_WORKSPACE/website', validation)
        self.assertIn("website firebase.json", validation)
        self.assertIn("content_addressed_archive", validation)
        self.assertIn('"artifact_sha256"', validation)
        self.assertIn('"candidate_sha"', validation)
        self.assertIn('"base_sha"', validation)
        self.assertIn("sha256sum -c", publish)
        self.assertIn("jq -e", publish)
        self.assertIn("entryPoint:", publish)
        self.assertNotIn("actions/checkout", publish)
        self.assertNotIn("npm ci", publish)
        self.assertNotIn("uv run", publish)

    def test_protected_production_environment_is_the_only_publish_gate(self):
        source = (WORKFLOW_ROOT / "firebase-hosting-publish.yml").read_text(encoding="utf-8")
        _, publish = source.split("\n  publish:", 1)

        self.assertRegex(publish, r"(?ms)^\s+environment:\n\s+name: production\n")
        self.assertIn("needs: validate", publish)
        self.assertIn("channelId: live", publish)
        self.assertEqual(publish.count("FirebaseExtended/action-hosting-deploy@v0"), 1)

    def test_firebase_and_node_boundaries_are_hosting_only(self):
        firebase = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))
        self.assertEqual(firebase["hosting"]["public"], "website")
        self.assertNotIn("functions", firebase)

        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["firebase-tools"], "15.29.0")
        self.assertEqual(package["scripts"]["serve"], "firebase emulators:start --only hosting")
        self.assertNotIn("deploy", package["scripts"])
        self.assertNotIn("hosting:deploy", package["scripts"])
        self.assertNotRegex(" ".join(package["scripts"].values()), r"firebase\s+deploy")
        self.assertNotIn("functions", " ".join(package["scripts"].values()))

        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"][""]["devDependencies"]["firebase-tools"], "15.29.0")

    def test_python_lock_declares_only_direct_runtime_dependencies(self):
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("cfbd>=5.26,<6", project["project"]["dependencies"])
        self.assertEqual(
            {
                dependency.split(">", 1)[0].split("<", 1)[0].split("=", 1)[0]
                for dependency in project["project"]["dependencies"]
            },
            {"beautifulsoup4", "cfbd", "html5lib", "lxml", "numpy", "pandas"},
        )

    def test_unsafe_morning_handoff_is_blocked_by_authoritative_plan(self):
        """Reviewed-unsafe recovery commands must not remain operator guidance."""
        handoff = (REPO_ROOT / "docs/operations/2026-season-recovery-morning.md").read_text(
            encoding="utf-8"
        )
        plan = (REPO_ROOT / "docs/plans/2026-p0-recovery-and-backfill.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Do not run", handoff)
        self.assertIn("2026-p0-recovery-and-backfill.md", handoff)
        self.assertNotIn("--clone-published", handoff)
        self.assertNotIn("npm run deploy", handoff)

        for marker in (
            "CFBD_API_KEY",
            "exactly six calls",
            "2024 FINAL",
            "2025 FINAL",
            "2026 PRESEASON",
            "Gate 1",
            "Gate 2",
            "systemd",
            "Pi/T7",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, plan)

    def test_human_guidance_points_to_the_gated_immutable_workflow(self):
        workflow_name = "Publish validated static site to Firebase Hosting"
        for relative_path in (
            "README.md",
            "cfb/README.md",
            "docs/operations/2026-season-recovery-morning.md",
        ):
            with self.subTest(relative_path=relative_path):
                guidance = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(workflow_name, guidance)
                self.assertIn("candidate_sha", guidance)
                self.assertIn("base_sha", guidance)
                self.assertIn("production", guidance)


if __name__ == "__main__":
    unittest.main()
