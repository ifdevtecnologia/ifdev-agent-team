"""QA acceptance tests for issue #77, the process-metrics instrument.

Each test is named after the acceptance criterion it proves. The installer tests run the real
install.sh, uninstall.sh and status.sh on a copy of the toolkit with a temporary HOME passed only
to the subprocesses (see test_install.py); the developer's HOME is never used.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from test_install import REPO_ROOT, InstallerTestCase, snapshot

CLAUDE_SKILL = ".claude/skills/process-metrics"
CODEX_SKILL = ".agents/skills/process-metrics"
SOURCE = "core/skills/process-metrics"
SKILL_FILES = ("SKILL.md", "references/records-v1.md")
FRONTMATTER = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
MODES = ("process-metrics: on", "process-metrics: local", "process-metrics: off")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def unreleased() -> str:
    return read("CHANGELOG.md").split("## Unreleased", 1)[1].split("\n## ", 1)[0]


class ProcessMetricsInstallAcceptanceTest(InstallerTestCase):
    """Criterion 2: the skill is a new managed item for claude and codex, installed and removed safely."""

    def assert_skill_linked(self, rel: str) -> None:
        path = self.home / rel
        self.assertTrue(path.is_symlink(), rel)
        self.assertEqual(os.readlink(path), str(self.repo_real / SOURCE), rel)
        for name in SKILL_FILES:
            with self.subTest(destination=rel, file=name):
                self.assertEqual((path / name).read_bytes(), (self.repo / SOURCE / name).read_bytes())

    def test_criterion_2_claude_install_links_only_the_claude_skill(self) -> None:
        result = self.install("--target", "claude")

        self.assert_skill_linked(CLAUDE_SKILL)
        self.assertFalse(os.path.lexists(self.home / CODEX_SKILL))
        self.assertIn(f"~/{CLAUDE_SKILL}", result.stdout)

    def test_criterion_2_codex_install_links_only_the_codex_skill(self) -> None:
        self.install("--target", "codex")

        self.assert_skill_linked(CODEX_SKILL)
        self.assertFalse(os.path.lexists(self.home / CLAUDE_SKILL))

    def test_criterion_2_all_install_links_both_and_a_rerun_changes_nothing(self) -> None:
        self.install("--target", "all")
        self.assert_skill_linked(CLAUDE_SKILL)
        self.assert_skill_linked(CODEX_SKILL)
        before = snapshot(self.home)

        result = self.install("--target", "all")

        self.assertIn("0 linked, 0 copied, 0 backed up", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_criterion_2_status_reports_both_skills_ok_after_install(self) -> None:
        shutil.copy2(REPO_ROOT / "CHANGELOG.md", self.repo / "CHANGELOG.md")
        self.install("--target", "all")

        result = self.run_script("status.sh")

        self.assert_ok(result)
        for rel in (CLAUDE_SKILL, CODEX_SKILL):
            self.assertRegex(result.stdout, rf"(?m)^ok\s+~/{re.escape(rel)}$")

    def test_criterion_2_uninstall_removes_both_skills_and_keeps_other_skills(self) -> None:
        self.write_siblings()
        self.install("--target", "all")

        self.assert_ok(self.run_script("uninstall.sh"))

        self.assertFalse(os.path.lexists(self.home / CLAUDE_SKILL))
        self.assertFalse(os.path.lexists(self.home / CODEX_SKILL))
        self.assert_siblings_intact()
        # The skill sources in the checkout are never removed through the links.
        for name in SKILL_FILES:
            self.assertTrue((self.repo / SOURCE / name).is_file(), name)

    def test_criterion_2_uninstalling_codex_keeps_the_claude_skill(self) -> None:
        self.install("--target", "all")

        self.assert_ok(self.run_script("uninstall.sh", "--target", "codex"))

        self.assertFalse(os.path.lexists(self.home / CODEX_SKILL))
        self.assert_skill_linked(CLAUDE_SKILL)

    def test_criterion_2_an_existing_user_skill_of_the_same_name_is_backed_up_not_lost(self) -> None:
        own = "---\nname: process-metrics\n---\nmine\n"
        self.write_home(f"{CLAUDE_SKILL}/SKILL.md", own)

        result = self.install("--target", "claude")

        self.assert_skill_linked(CLAUDE_SKILL)
        self.assertIn("1 backed up", result.stdout)
        backups = list((self.home / ".ifdev-ai-tools/backups").rglob("SKILL.md"))
        self.assertEqual([path.read_text() for path in backups], [own])


class ProcessMetricsSkillAcceptanceTest(unittest.TestCase):
    """Criteria 1, 3, 4 (as amended by D1) and 9: one tool-neutral definition, modes and the local file."""

    def setUp(self) -> None:
        self.skill = read(f"{SOURCE}/SKILL.md")

    def test_criterion_1_frontmatter_names_the_skill_with_a_loadable_description(self) -> None:
        match = FRONTMATTER.match(self.skill)
        self.assertIsNotNone(match)
        fields = dict(line.split(": ", 1) for line in match.group("body").splitlines())
        self.assertEqual(fields["name"], "process-metrics")
        description = fields["description"].strip('"')
        # Both Claude Code and Codex truncate or reject skill descriptions above 1024 characters.
        self.assertLessEqual(len(description), 1024)
        self.assertIn("issue <repo>#<n> phase=", description)

    def test_criterion_1_definition_exists_once_and_is_tool_neutral(self) -> None:
        # Only files Git would version count: worktrees, clones and caches inside the checkout are not copies.
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        copies = [path for path in listed if path.endswith("/records-v1.md")]
        self.assertEqual(copies, [f"{SOURCE}/references/records-v1.md"])
        for name in SKILL_FILES:
            text = read(f"{SOURCE}/{name}")
            for tool_word in ("~/.claude", "~/.codex", "CLAUDE.md", ".claude/", "spawn_agent"):
                with self.subTest(file=name, word=tool_word):
                    self.assertNotIn(tool_word, text)

    def test_criterion_3_always_loaded_text_is_one_paragraph_and_one_bullet(self) -> None:
        instructions = read("core/instructions.md")
        workflow = read("core/rules/git-workflow.md")
        self.assertEqual(instructions.count("**Process metrics.**"), 1)
        mentions = [line for line in instructions.splitlines() if "process-metrics" in line]
        # The paragraph and the global skills list entry, nothing else.
        self.assertEqual(len(mentions), 2)
        self.assertEqual(len([line for line in workflow.splitlines() if "process-metrics" in line]), 1)
        paragraph = next(line for line in mentions if line.startswith("**Process metrics.**"))
        self.assertLess(len(paragraph.encode()), 500)

    def test_criterion_4_d1_off_wins_and_no_line_means_ask_first_and_publish_nothing(self) -> None:
        for mode in MODES:
            self.assertIn(f"`{mode}`", self.skill)
        self.assertIn("`off` wins over any other line and `local` wins over `on`", self.skill)
        self.assertIn("no record block, no first line, no comment and no file", self.skill)
        self.assertIn("ask the user once which mode to use", self.skill)
        self.assertIn("Until the user answers, or when nobody can answer, publish nothing", self.skill)

    def test_criterion_9_local_file_is_outside_the_repository_and_never_committed(self) -> None:
        reference = read(f"{SOURCE}/references/records-v1.md")
        self.assertIn("$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl", self.skill)
        self.assertIn("$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl", reference)
        self.assertIn("lie outside the repository", reference)
        self.assertIn("Never write records inside the repository or commit them", self.skill)

    def test_criterion_d3_no_wall_clock_start_or_end_fields(self) -> None:
        for name in SKILL_FILES:
            text = read(f"{SOURCE}/{name}")
            for field in ("started_at", "ended_at"):
                with self.subTest(file=name, field=field):
                    self.assertNotIn(field, text)


class ProcessMetricsDocsAcceptanceTest(unittest.TestCase):
    """Criteria 10 and 11: documentation and changelog."""

    def test_criterion_10_docs_explain_switch_fields_never_collected_and_own_project_aggregates(self) -> None:
        doc = read("docs/process-metrics.md")
        for heading in ("## Choosing the mode", "## What is recorded", "## Never collected", "## Aggregates"):
            self.assertIn(heading, doc)
        aggregates = doc.split("## Aggregates", 1)[1].split("\n## ", 1)[0]
        self.assertIn("only from your own projects", aggregates)
        self.assertIn("process-metrics.md", read("docs/team.md"))

    def test_criterion_10_doc_links_resolve(self) -> None:
        doc_dir = REPO_ROOT / "docs"
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", read("docs/process-metrics.md")):
            if "://" in target:
                continue

            with self.subTest(link=target):
                self.assertTrue((doc_dir / target).exists(), target)

    def test_criterion_11_changelog_unreleased_has_added_changed_and_upgrade_with_rerun_and_off(self) -> None:
        section = unreleased()
        for heading in ("### Added", "### Changed", "### Upgrade"):
            self.assertIn(heading, section)
        added = section.split("### Added", 1)[1].split("\n### ", 1)[0]
        changed = section.split("### Changed", 1)[1].split("\n### ", 1)[0]
        upgrade = section.split("### Upgrade", 1)[1]
        self.assertIn("process-metrics", added)
        self.assertIn("process-metrics", changed)
        self.assertIn("install.sh", upgrade)
        self.assertIn("`process-metrics: off`", upgrade)


if __name__ == "__main__":
    unittest.main()
