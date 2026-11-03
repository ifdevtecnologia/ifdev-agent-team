"""Acceptance tests for the always-loaded context baseline (issue #34).

The baseline in docs/development.md is pinned to a commit, so its numbers are compared exactly against the script
run on that commit's tree (exported to a temporary directory), not against the working tree: edits
to the content of `core/instructions.md` or the rules must not break the suite, but the recorded
baseline must stay true. Against the working tree only the stable invariants are checked: the same
targets and files and internally consistent tables. Adding a rule, giving one `paths` or adding a
target changes the always-loaded files and requires updating that baseline, which has one
table per target.
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
BASELINE_DOC = REPO_ROOT / "docs/development.md"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import context_budget  # noqa: E402

SECTION_HEADING = "### Always-loaded context baseline"
ROW = re.compile(r"^\| `(?P<file>[^`]+)` \| (?P<bytes>\d+) \| (?P<tokens>\d+) \|$")
TOTAL_ROW = re.compile(r"^\| \*\*Total\*\* \| \*\*(?P<bytes>\d+)\*\* \| \*\*(?P<tokens>\d+)\*\* \|$")
COMMIT = re.compile(r"Baseline at commit `(?P<commit>[0-9a-f]{7,40})` \(estimate\):")
TARGET_LINE = re.compile(r"^`(?P<target>[a-z]+)` target:$")
SCRIPT_TARGET = re.compile(r"^Target: (\S+)$", re.MULTILINE)


def baseline_section() -> str:
    doc = BASELINE_DOC.read_text(encoding="utf-8")
    start = doc.index(SECTION_HEADING)
    end = doc.find("\n### ", start + len(SECTION_HEADING))
    return doc[start : end if end != -1 else len(doc)]


def baseline_tables(section: str) -> dict[str, tuple[list[tuple[str, int, int]], tuple[int, int]]]:
    """{target: (rows, total)} for each "`<target>` target:" table of the baseline section."""
    tables: dict[str, tuple[list[tuple[str, int, int]], tuple[int, int] | None]] = {}
    target = None
    for line in section.splitlines():
        if match := TARGET_LINE.match(line):
            target = match["target"]
            tables[target] = ([], None)
        elif target is None:
            continue
        elif match := ROW.match(line):
            tables[target][0].append((match["file"], int(match["bytes"]), int(match["tokens"])))
        elif match := TOTAL_ROW.match(line):
            tables[target] = (tables[target][0], (int(match["bytes"]), int(match["tokens"])))
    if not tables or any(total is None for _, total in tables.values()):
        raise AssertionError("The baseline needs a table with a Total row for each target")
    return tables


def script_report(root: Path | None = None) -> dict[str, list[tuple[str, int, int]]]:
    """Runs the CLI (default root unless given) and parses each target's always-loaded rows, total
    included, as {target: rows}."""
    args = [sys.executable, str(REPO_ROOT / "scripts/context_budget.py")]
    if root is not None:
        args += ["--root", str(root)]
    stdout = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    parts = SCRIPT_TARGET.split(stdout)[1:]
    report = {}
    for target, text in zip(parts[::2], parts[1::2]):
        always = text.split("On demand", 1)[0]
        report[target] = [
            (m[3], int(m[1]), int(m[2])) for m in re.finditer(r"^\s+(\d+)\s+(\d+)\s+(\S+)$", always, re.MULTILINE)
        ]
    return report


class ContextBaselineAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.section = baseline_section()
        self.tables = baseline_tables(self.section)

    def test_ac1_script_on_repository_lists_always_loaded_files_with_bytes_tokens_and_total(self) -> None:
        reports = script_report()

        self.assertEqual(list(reports), ["claude", "codex"])
        for target, entry_point in (("claude", "dist/claude/CLAUDE.md"), ("codex", "dist/codex/AGENTS.md")):
            with self.subTest(target=target):
                report = reports[target]
                files = [name for name, _, _ in report]
                self.assertEqual(files[0], entry_point)
                self.assertEqual(files[-1], "total")
                for name, size, tokens in report:
                    self.assertEqual(tokens, -(-size // 4), name)
                self.assertEqual(report[-1][1], sum(size for _, size, _ in report[:-1]))

    def test_ac1_ac2_doc_documents_estimate_method_and_marks_baseline_as_estimate(self) -> None:
        self.assertIn("python3 scripts/context_budget.py", self.section)
        self.assertIn("`ceil(bytes / 4)`", self.section)
        self.assertIn("not a tokenizer count", self.section)
        self.assertIn("**estimated**", self.section)
        self.assertRegex(self.section, COMMIT)
        doc = BASELINE_DOC.read_text(encoding="utf-8")
        self.assertLess(doc.index("## Context efficiency"), doc.index(SECTION_HEADING))

    def test_ac2_baseline_table_is_internally_consistent(self) -> None:
        for target, (rows, (total_bytes, total_tokens)) in self.tables.items():
            with self.subTest(target=target):
                for name, size, tokens in rows:
                    self.assertEqual(tokens, context_budget.estimate_tokens(size), name)
                self.assertEqual(total_bytes, sum(size for _, size, _ in rows))
                self.assertEqual(total_tokens, context_budget.estimate_tokens(total_bytes))

    def test_ac2_baseline_lists_the_targets_and_files_the_script_reports_as_always_loaded(self) -> None:
        current = {target: [name for name, _, _ in rows[:-1]] for target, rows in script_report().items()}

        self.assertEqual({target: [name for name, _, _ in rows] for target, (rows, _) in self.tables.items()}, current)

    def test_ac2_baseline_numbers_match_the_script_at_the_recorded_commit(self) -> None:
        if not hasattr(tarfile, "data_filter"):
            self.skipTest("tarfile extraction filters need Python 3.12+ or a security backport")
        commit = COMMIT.search(self.section)["commit"]
        archive = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", commit, "core/rules", "dist/claude", "dist/codex"],
            capture_output=True,
        )
        if archive.returncode != 0:
            # Shallow clones (the CI checkout) do not contain older commits, and a commit before
            # the Codex target (decision record 0004) lacks some of these paths.
            self.skipTest(f"baseline commit {commit} is not available in this clone or predates dist/codex/")
        root = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-baseline-"))
        self.addCleanup(shutil.rmtree, root)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(root, filter="data")

        reports = script_report(root)

        self.assertEqual(list(reports), list(self.tables))
        for target, (rows, total) in self.tables.items():
            with self.subTest(target=target):
                self.assertEqual(reports[target][:-1], rows)
                self.assertEqual(reports[target][-1][1:], total)


if __name__ == "__main__":
    unittest.main()
