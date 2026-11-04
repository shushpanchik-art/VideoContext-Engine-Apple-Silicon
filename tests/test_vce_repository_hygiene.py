from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_WORKFLOW = ROOT / ".github" / "workflows" / "cleanup-merged-topic-branches.yml"
MANIFEST = ROOT / "PUBLICATION_PACKAGE_MANIFEST.json"
COMMIT_INFO = ROOT / "COMMIT_INFO.txt"
TEST_STATUS = ROOT / "TEST_STATUS.md"


class TestRepositoryHygiene(unittest.TestCase):
    def test_cleanup_preserves_default_protected_and_open_pr_branches(self):
        workflow = CLEANUP_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("defaultBranch", workflow)
        self.assertIn("branch.protected", workflow)
        self.assertIn("openHeads.has(ref)", workflow)
        self.assertIn('ref === defaultBranch', workflow)

    def test_merged_pr_branch_is_deleted_only_if_its_tip_was_not_reused(self):
        workflow = CLEANUP_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("const mergedHeads = new Map()", workflow)
        self.assertIn("shas.add(pr.head.sha)", workflow)
        self.assertIn("mergedShas.has(branch.commit.sha)", workflow)
        self.assertIn("Preserved reused branch with a newer tip", workflow)

    def test_orphan_cleanup_requires_zero_commits_ahead_of_main(self):
        workflow = CLEANUP_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.rest.repos.compareCommits", workflow)
        self.assertIn("comparison.data.ahead_by !== 0", workflow)
        self.assertIn("Preserved branch with unique commits", workflow)

    def test_orphan_cleanup_is_limited_to_topic_prefixes(self):
        workflow = CLEANUP_WORKFLOW.read_text(encoding="utf-8")
        for prefix in (
            "chore/", "fix/", "feature/", "docs/", "test/",
            "perf/", "refactor/", "hotfix/", "benchmark/"
        ):
            self.assertIn(f'"{prefix}"', workflow)

    def test_cleanup_workflow_javascript_is_syntactically_valid(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed in this environment")

        lines = CLEANUP_WORKFLOW.read_text(encoding="utf-8").splitlines()
        marker = next(i for i, line in enumerate(lines) if line.strip() == "script: |")
        marker_indent = len(lines[marker]) - len(lines[marker].lstrip())
        script_lines = []
        for line in lines[marker + 1:]:
            if line.strip():
                indent = len(line) - len(line.lstrip())
                if indent <= marker_indent:
                    break
                script_lines.append(line[marker_indent + 2:])
            else:
                script_lines.append("")

        script = "\n".join(script_lines).strip() + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", encoding="utf-8") as handle:
            handle.write(script)
            handle.flush()
            result = subprocess.run(
                [node, "--check", handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_manifest_is_valid_json_and_contains_stable_distribution_provenance(self):
        import json

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["distribution_branch"], "main")
        self.assertEqual(
            manifest["source_provenance"]["initial_full_runtime_distribution"]["pull_request"],
            15,
        )
        self.assertEqual(
            manifest["source_provenance"]["initial_full_runtime_distribution"]["merge_commit"],
            "549186bbf9c4653955cb8d8f4d1e967055e80b55",
        )
        self.assertFalse(manifest["privacy_defaults"]["github_upload_enabled_by_default"])
        self.assertTrue(manifest["publication_contract"]["local_user_settings_and_model_weights_excluded"])
        # Volatile repository state belongs to GitHub, not to the distribution manifest.
        for key in (
            "repository_state",
            "merge_pull_requests",
            "latest_repository_audit_commit",
            "latest_repository_maintenance_merge_commit",
            "last_verified_main_checkpoint",
            "checks",
        ):
            self.assertNotIn(key, manifest)

    def test_commit_info_contains_immutable_provenance_not_a_live_audit_snapshot(self):
        info = COMMIT_INFO.read_text(encoding="utf-8")
        self.assertIn("Upstream reference commit: 4a42b4a309d9d7d8f7f944c2e210a027940208f3", info)
        self.assertIn("Merge commit: 549186bbf9c4653955cb8d8f4d1e967055e80b55", info)
        self.assertIn("Current branch inventory", info)
        self.assertNotIn("LATEST VERIFIED AUDIT CHECKPOINT", info)
        self.assertNotIn("Closed without merge:", info)
        self.assertNotIn("Open pull requests:", info)

    def test_test_status_uses_live_actions_instead_of_frozen_run_numbers(self):
        status = TEST_STATUS.read_text(encoding="utf-8")
        self.assertIn("actions?query=branch%3Amain", status)
        self.assertIn("physical Apple Silicon Mac", status)
        self.assertNotIn("41868806204", status)
        self.assertNotIn("Текущий подтверждённый checkpoint", status)


if __name__ == "__main__":
    unittest.main()
