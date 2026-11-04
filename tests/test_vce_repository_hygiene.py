from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_WORKFLOW = ROOT / ".github" / "workflows" / "cleanup-merged-topic-branches.yml"
MANIFEST = ROOT / "PUBLICATION_PACKAGE_MANIFEST.json"


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

    def test_manifest_is_valid_json_and_records_audit_boundary(self):
        import json

        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["distribution_branch"], "main")
        self.assertIn("audit_snapshot_boundary", manifest["repository_state"])
        self.assertGreaterEqual(manifest["repository_state"]["closed_pull_requests"], 29)


if __name__ == "__main__":
    unittest.main()
