"""Tests for scripts/context_budget.py with synthetic files in a temporary directory."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import context_budget  # noqa: E402


class ContextBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-budget-"))
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "claude/rules").mkdir(parents=True)

    def write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8")

    def test_estimate_rounds_up_bytes_per_token(self) -> None:
        self.assertEqual(context_budget.estimate_tokens(0), 0)
        self.assertEqual(context_budget.estimate_tokens(1), 1)
        self.assertEqual(context_budget.estimate_tokens(4), 1)
        self.assertEqual(context_budget.estimate_tokens(5), 2)

    def test_counts_entry_point_and_rules_without_paths(self) -> None:
        self.write("claude/CLAUDE.md", "a" * 10)
        self.write("claude/rules/plain.md", "# Plain\n")
        self.write("claude/rules/other-key.md", "---\ndescription: x\n---\nbody\n")
        self.write("claude/rules/scoped.md", '---\npaths:\n  - "src/**/*.ts"\n---\nbody\n')

        always, on_demand = context_budget.classify(self.root)

        self.assertEqual(
            always,
            [("claude/CLAUDE.md", 10), ("claude/rules/other-key.md", 28), ("claude/rules/plain.md", 8)],
        )
        self.assertEqual(on_demand, [("claude/rules/scoped.md", 38)])

    def test_size_is_measured_in_bytes_not_characters(self) -> None:
        self.write("claude/CLAUDE.md", "é")  # two bytes in UTF-8

        always, _ = context_budget.classify(self.root)

        self.assertEqual(always, [("claude/CLAUDE.md", 2)])

    def test_paths_detection(self) -> None:
        self.assertTrue(context_budget.has_paths("---\npaths:\n  - '*.py'\n---\nbody\n"))
        self.assertFalse(context_budget.has_paths("# No frontmatter\npaths: in body\n"))
        self.assertFalse(context_budget.has_paths("---\nname: x\n---\npaths: in body\n"))
        # Unparsable frontmatter is counted as always loaded (conservative).
        self.assertFalse(context_budget.has_paths("---\npaths: [unclosed\n---\n"))
        self.assertFalse(context_budget.has_paths("---\npaths: '*.py'\n"))

    def test_render_reports_total_and_method(self) -> None:
        output = context_budget.render([("a.md", 10), ("b.md", 7)], [])

        self.assertIn("ceil(bytes / 4)", output)
        self.assertIn("not a tokenizer count", output)
        self.assertRegex(output, r"\n\s+17\s+5\s+total\n")
        self.assertIn("(none)", output)

    def test_cli_prints_report_for_root(self) -> None:
        self.write("claude/CLAUDE.md", "x" * 8)
        self.write("claude/rules/scoped.md", "---\npaths: ['*.md']\n---\n")

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/context_budget.py"), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertRegex(result.stdout, r"\n\s+8\s+2\s+claude/CLAUDE.md\n\s+8\s+2\s+total\n")
        self.assertRegex(result.stdout, r"\n\s+\d+\s+\d+\s+claude/rules/scoped.md$")


if __name__ == "__main__":
    unittest.main()
