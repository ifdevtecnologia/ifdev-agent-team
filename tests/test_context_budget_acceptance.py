"""Acceptance tests for the always-loaded context baseline (issue #34).

The README baseline is pinned to a commit, so its numbers are compared exactly against the script
run on that commit's tree (exported to a temporary directory), not against the working tree: edits
to the content of `claude/CLAUDE.md` or the rules must not break the suite, but the recorded
baseline must stay true. Against the working tree only the stable invariants are checked: the same
set of files and an internally consistent table. Adding a rule, or giving one `paths`, changes the
set of always-loaded files and requires updating the README baseline.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import context_budget  # noqa: E402

SECTION_HEADING = "### Always-loaded context baseline"
ROW = re.compile(r"^\| `(?P<file>[^`]+)` \| (?P<bytes>\d+) \| (?P<tokens>\d+) \|$")
TOTAL_ROW = re.compile(r"^\| \*\*Total\*\* \| \*\*(?P<bytes>\d+)\*\* \| \*\*(?P<tokens>\d+)\*\* \|$")
COMMIT = re.compile(r"Baseline at commit `(?P<commit>[0-9a-f]{7,40})` \(estimate\):")


def baseline_section() -> str:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.index(SECTION_HEADING)
    end = readme.find("\n### ", start + len(SECTION_HEADING))
    return readme[start : end if end != -1 else len(readme)]


def baseline_table(section: str) -> tuple[list[tuple[str, int, int]], tuple[int, int]]:
    rows, total = [], None
    for line in section.splitlines():
        if match := ROW.match(line):
            rows.append((match["file"], int(match["bytes"]), int(match["tokens"])))
        elif match := TOTAL_ROW.match(line):
            total = (int(match["bytes"]), int(match["tokens"]))
    if total is None:
        raise AssertionError("README baseline table has no Total row")
    return rows, total


def script_report(root: Path | None = None) -> list[tuple[str, int, int]]:
    """Runs the CLI (default root unless given) and parses its always-loaded rows, total included."""
    args = [sys.executable, str(REPO_ROOT / "scripts/context_budget.py")]
    if root is not None:
        args += ["--root", str(root)]
    stdout = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    always = stdout.split("On demand", 1)[0]
    return [
        (m[3], int(m[1]), int(m[2]))
        for m in re.finditer(r"^\s+(\d+)\s+(\d+)\s+(\S+)$", always, re.MULTILINE)
    ]


class ContextBaselineAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.section = baseline_section()
        self.rows, self.total = baseline_table(self.section)

    def test_ac1_script_on_repository_lists_always_loaded_files_with_bytes_tokens_and_total(self) -> None:
        report = script_report()

        files = [name for name, _, _ in report]
        self.assertEqual(files[0], "claude/CLAUDE.md")
        self.assertEqual(files[-1], "total")
        for name, size, tokens in report:
            self.assertEqual(tokens, -(-size // 4), name)
        self.assertEqual(report[-1][1], sum(size for _, size, _ in report[:-1]))

    def test_ac1_ac2_readme_documents_estimate_method_and_marks_baseline_as_estimate(self) -> None:
        self.assertIn("python3 scripts/context_budget.py", self.section)
        self.assertIn("`ceil(bytes / 4)`", self.section)
        self.assertIn("not a tokenizer count", self.section)
        self.assertIn("**estimated**", self.section)
        self.assertRegex(self.section, COMMIT)
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(readme.index("## Context efficiency"), readme.index(SECTION_HEADING))

    def test_ac2_baseline_table_is_internally_consistent(self) -> None:
        for name, size, tokens in self.rows:
            self.assertEqual(tokens, context_budget.estimate_tokens(size), name)
        total_bytes, total_tokens = self.total
        self.assertEqual(total_bytes, sum(size for _, size, _ in self.rows))
        self.assertEqual(total_tokens, context_budget.estimate_tokens(total_bytes))

    def test_ac2_baseline_lists_the_files_the_script_reports_as_always_loaded(self) -> None:
        current = [name for name, _, _ in script_report()[:-1]]

        self.assertEqual([name for name, _, _ in self.rows], current)

    def test_ac2_baseline_numbers_match_the_script_at_the_recorded_commit(self) -> None:
        if not hasattr(tarfile, "data_filter"):
            self.skipTest("tarfile extraction filters need Python 3.12+ or a security backport")
        commit = COMMIT.search(self.section)["commit"]
        archive = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", commit, "claude"],
            capture_output=True,
        )
        if archive.returncode != 0:
            # Shallow clones (the CI checkout) do not contain older commits.
            self.skipTest(f"baseline commit {commit} is not available in this clone")
        root = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-baseline-"))
        self.addCleanup(shutil.rmtree, root)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(root, filter="data")

        report = script_report(root)

        self.assertEqual(report[:-1], self.rows)
        self.assertEqual(report[-1][1:], self.total)


if __name__ == "__main__":
    unittest.main()
