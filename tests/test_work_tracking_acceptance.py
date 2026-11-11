"""Acceptance tests for the project template's Work tracking section and the guide that describes it.

The git-workflow rule leaves three things to the project's instructions: the board (or another
tracker, or an opt-out), the repository for tasks without a pull request and the product's
knowledge-base page. The project AGENTS.md template collects them in one **Work tracking** section,
and step 1 of docs/customization.md tells the reader which template sections to fill in. These
tests keep the guide and the template in step; they read the repository files only.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "docs/templates/project/AGENTS.md"
GUIDE = REPO_ROOT / "docs/customization.md"


def sections(markdown: str) -> dict[str, str]:
    """Map each level-2 heading to its body."""
    parts = re.split(r"(?m)^## (.+?)\s*$", markdown)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


class WorkTrackingTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = sections(TEMPLATE.read_text(encoding="utf-8"))
        self.guide = GUIDE.read_text(encoding="utf-8")

    def test_every_template_section_the_guide_asks_to_fill_in_exists(self) -> None:
        step = re.search(r"(?m)^1\. Copy \[`AGENTS\.md`\]\(templates/project/AGENTS\.md\)(.+)$", self.guide)
        self.assertIsNotNone(step, "step 1 of the guide no longer copies the AGENTS.md template")
        named = re.findall(r"\*\*(.+?)\*\*", step.group(1))
        self.assertIn("Work tracking", named)
        for name in named:
            self.assertIn(name, self.template, f"guide step 1 names a missing template section: {name}")

    def test_work_tracking_section_has_the_links_the_rule_leaves_to_the_project(self) -> None:
        body = self.template.get("Work tracking", "")
        bullets = re.findall(r"(?m)^- (.+)$", body)
        # Board, repository for tasks without a pull request, product page, opt-out or other tracker.
        self.assertGreaterEqual(len(bullets), 4, body)
        self.assertRegex(body, r"(?i)without a pull request")
        self.assertRegex(body, r"(?i)knowledge-base page")
        self.assertRegex(body, r"(?i)does not use a GitHub Project board")

    def test_tracker_opt_out_is_stated_once_in_the_template(self) -> None:
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(text.count("does not use a GitHub Project board"), 1)
        self.assertNotIn("does not use a GitHub Project board", self.template.get("Overrides of global defaults", ""))


if __name__ == "__main__":
    unittest.main()
