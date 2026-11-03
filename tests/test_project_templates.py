"""Acceptance tests for the per-project templates in docs/templates/project/.

The templates are copied into a temporary Git repository, laid out as a project that adopts the
toolkit would hold them, and checked with the validator's own helpers (scripts/validate.py). The
real HOME is never used: git runs with a temporary HOME.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "docs/templates/project"
GUIDE = REPO_ROOT / "docs/customization.md"
EXPECTED_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/rules/area.md",
    ".claude/agents/domain-reviewer.md",
)
RULE = ".claude/rules/area.md"
AGENT = ".claude/agents/domain-reviewer.md"


def load_validator():
    spec = importlib.util.spec_from_file_location("templates_validate", REPO_ROOT / "scripts/validate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectTemplatesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-templates-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.project = self.tmp / "project repo"
        shutil.copytree(TEMPLATES, self.project, symlinks=True)
        env = {"HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"}
        self.env_patch = mock.patch.dict(os.environ, env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        subprocess.run(["git", "-C", str(self.project), "init", "-q"], check=True, capture_output=True)

    def read(self, rel: str) -> str:
        return (self.project / rel).read_text(encoding="utf-8")

    def frontmatter(self, rel: str) -> dict:
        report = self.validator.Report()
        fields = self.validator.parse_frontmatter(rel, self.read(rel), report)
        self.assertEqual(report.errors, [])
        self.assertIsInstance(fields, dict)
        return fields

    def global_agents(self) -> dict[str, dict]:
        agents = {}
        for path in sorted((REPO_ROOT / "claude/agents").glob("*.md")):
            report = self.validator.Report()
            fields = self.validator.parse_frontmatter(path.name, path.read_text(encoding="utf-8"), report)
            self.assertEqual(report.errors, [], path)
            agents[path.stem] = fields
        return agents


class CopiedTemplateTests(ProjectTemplatesTestCase):
    """Criterion 4: the templates pass the validator's checks when copied into a temporary repository."""

    def test_copy_contains_the_minimal_project_files(self) -> None:
        for rel in EXPECTED_FILES:
            path = self.project / rel
            self.assertTrue(path.is_file() and not path.is_symlink(), rel)

    def test_validator_inventories_every_template_as_safe_text(self) -> None:
        report = self.validator.Report()
        texts, _ = self.validator.inventory(self.project, report)
        self.assertEqual(report.errors, [])
        self.assertEqual(sorted(texts), sorted(EXPECTED_FILES))

    def test_templates_pass_the_validator_content_checks(self) -> None:
        # Global references such as ~/.claude/CLAUDE.md point at the installed toolkit, so the
        # toolkit sources are placed next to the copy for the reference check to resolve them.
        shutil.copytree(REPO_ROOT / "claude", self.project / "claude", symlinks=True)
        report = self.validator.Report()
        for rel in EXPECTED_FILES:
            self.validator.check_text(self.project, rel, self.read(rel), report)
        self.assertEqual(report.errors, [])

    def test_validator_reports_a_secret_added_to_a_copied_template(self) -> None:
        # Guards the previous test against passing vacuously; the value is synthetic.
        fake = "gh" + "p_" + "A" * 36
        report = self.validator.Report()
        self.validator.check_text(self.project, "AGENTS.md", self.read("AGENTS.md") + f"\n{fake}\n", report)
        self.assertTrue(any("possible credential" in error for error in report.errors), report.errors)


class InstructionTemplateTests(ProjectTemplatesTestCase):
    """Criterion 2: minimal project CLAUDE.md and AGENTS.md."""

    def test_claude_md_imports_agents_md(self) -> None:
        self.assertRegex(self.read("CLAUDE.md"), r"(?m)^@AGENTS\.md\s*$")

    def test_agents_md_has_stack_validation_and_domain_rules_sections(self) -> None:
        headings = re.findall(r"(?m)^## (.+?)\s*$", self.read("AGENTS.md"))
        for section in ("Stack", "Validation", "Domain rules"):
            self.assertIn(section, headings)

    def test_agents_md_lists_the_template_reviewer(self) -> None:
        self.assertIn("`domain-reviewer`", self.read("AGENTS.md"))


class PathScopedRuleTemplateTests(ProjectTemplatesTestCase):
    """Criterion 3: the rule template uses `paths`."""

    def test_rule_frontmatter_has_a_non_empty_paths_list(self) -> None:
        fields = self.frontmatter(RULE)
        paths = fields.get("paths")
        self.assertIsInstance(paths, list)
        self.assertTrue(paths)
        self.assertTrue(all(isinstance(p, str) and p.strip() for p in paths), paths)

    def test_rule_frontmatter_has_only_the_paths_field(self) -> None:
        self.assertEqual(set(self.frontmatter(RULE)), {"paths"})


class ProjectReviewerTemplateTests(ProjectTemplatesTestCase):
    """Criterion 2: a read-only project reviewer declared like the global read-only agents."""

    def test_agent_passes_the_validator_definition_check(self) -> None:
        report = self.validator.Report()
        texts = {AGENT: self.read(AGENT)}
        self.validator.check_definition(AGENT, Path(AGENT).stem, texts, {}, "claude agent", report)
        self.assertEqual(report.errors, [])

    def test_agent_tools_use_the_global_agents_vocabulary(self) -> None:
        vocabulary = set()
        for fields in self.global_agents().values():
            vocabulary |= self.validator.tool_names(fields.get("tools"))
            vocabulary |= self.validator.tool_names(fields.get("disallowedTools"))
        fields = self.frontmatter(AGENT)
        tools = self.validator.tool_names(fields.get("tools"))
        self.assertTrue(tools)
        self.assertLessEqual(tools, vocabulary)
        self.assertLessEqual(self.validator.tool_names(fields.get("disallowedTools")), vocabulary)

    def test_agent_is_read_only_like_the_global_read_only_agents(self) -> None:
        fields = self.frontmatter(AGENT)
        allowed = self.validator.tool_names(fields.get("tools"))
        blocked = self.validator.tool_names(fields.get("disallowedTools"))
        for tool in self.validator.WRITE_TOOLS:
            self.assertNotIn(tool, allowed)
            self.assertIn(tool, blocked)
        reviewer = self.global_agents()["reviewer"]
        self.assertEqual(allowed, self.validator.tool_names(reviewer.get("tools")))
        self.assertEqual(blocked, self.validator.tool_names(reviewer.get("disallowedTools")))

    def test_agent_name_does_not_replace_a_global_agent(self) -> None:
        self.assertNotIn(self.frontmatter(AGENT)["name"], self.global_agents())


class GuideReferenceTests(unittest.TestCase):
    """Criteria 1 and 3: the guide quotes global rules that still exist and links every template."""

    def setUp(self) -> None:
        self.guide = GUIDE.read_text(encoding="utf-8")

    def test_quoted_precedence_rule_matches_the_global_instructions(self) -> None:
        quote = re.search(r"(?m)^> (.+)$", self.guide)
        self.assertIsNotNone(quote)
        self.assertIn(quote.group(1).strip(), (REPO_ROOT / "claude/CLAUDE.md").read_text(encoding="utf-8"))

    def test_quoted_override_conditions_exist_in_the_global_rules(self) -> None:
        sources = {
            "claude/rules/git-workflow.md": (
                "unless the project's instructions name another tracker or opt out",
                "unless the project documents a stricter workflow",
            ),
            "claude/CLAUDE.md": ("When the project keeps a `CHANGELOG.md`",),
        }
        for rel, phrases in sources.items():
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, text, rel)

    def test_guide_links_every_template(self) -> None:
        for rel in EXPECTED_FILES:
            self.assertIn(f"](templates/project/{rel})", self.guide)


if __name__ == "__main__":
    unittest.main()
