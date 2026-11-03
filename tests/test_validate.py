"""Tests for scripts/validate.sh.

Each test copies the toolkit into a temporary Git repository and mutates that copy. Invalid
content, sensitive files and credential-like values are synthetic and generated at runtime,
so this file itself never contains them literally.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
# The subprocess runs with a temporary HOME, which hides a user-site PyYAML installation.
YAML_SITE = str(Path(yaml.__file__).resolve().parent.parent)
FIXTURE_ITEMS = (
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "requirements-dev.txt",
    ".github",
    "core",
    "dist",
    "targets",
    "claude",
    "codex",
    "scripts",
    "tests",
)
MARKER = "SYNTHETIC-CONTENT-MARKER-2f9c"


class ValidatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = self.tmp / "fixture repo"
        self.root.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for name in FIXTURE_ITEMS:
            source = REPO_ROOT / name
            if source.is_dir():
                shutil.copytree(source, self.root / name, symlinks=True, ignore=ignore)
            else:
                shutil.copy2(source, self.root / name)
        (self.tmp / "home").mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.tmp / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": YAML_SITE,
        }
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], env=self.env, check=True, capture_output=True)

    def validate(self, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["bash", str(self.root / "scripts/validate.sh"), *args],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        return result.returncode, result.stdout.decode("utf-8", "replace")

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def append(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")

    def replace(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        content = path.read_text(encoding="utf-8")
        self.assertIn(old, content)
        path.write_text(content.replace(old, new, 1), encoding="utf-8")

    def make_unreadable(self, path: Path) -> None:
        path.chmod(0)
        # Registered after rmtree, so it runs first and cleanup can delete the file.
        self.addCleanup(path.chmod, stat.S_IRUSR | stat.S_IWUSR)

    def assert_passes(self) -> None:
        code, output = self.validate()
        self.assertEqual(code, 0, output)
        self.assertIn("validate: OK", output)

    def assert_fails_with(self, *messages: str) -> str:
        code, output = self.validate()
        self.assertEqual(code, 1, output)
        for message in messages:
            self.assertIn(message, output)
        return output

    def assert_reported(self, output: str, rel: str, message: str) -> None:
        """Asserts a problem for rel regardless of the reported line number."""
        self.assertRegex(output, rf"(?m)^ERROR {re.escape(rel)}(?::\d+)?: {re.escape(message)}")

    def build(self) -> None:
        """Regenerates the fixture's dist/ after a change to its core/ or targets/ sources."""
        subprocess.run(
            ["python3", str(self.root / "scripts/build_targets.py")], env=self.env, check=True, capture_output=True
        )

    def load_validator(self):
        spec = importlib.util.spec_from_file_location("fixture_validate", self.root / "scripts/validate.py")
        module = importlib.util.module_from_spec(spec)
        # As when run as a script, the validator imports its sibling generator from the fixture.
        with mock.patch.dict(sys.modules), mock.patch.object(sys, "path", [str(self.root / "scripts"), *sys.path]):
            sys.modules.pop("build_targets", None)
            spec.loader.exec_module(module)
        return module

    def validate_in_process(self, validator):
        # validate() runs git; isolate it from the developer's configuration.
        with mock.patch.dict(os.environ, {"HOME": self.env["HOME"], "GIT_CONFIG_NOSYSTEM": "1"}):
            return validator.validate(self.root)


class StructureTests(ValidatorTestCase):
    def test_untracked_working_tree_passes(self) -> None:
        self.assert_passes()

    def test_staged_working_tree_passes(self) -> None:
        self.git("add", "-A")
        self.assert_passes()

    def test_missing_required_file_fails(self) -> None:
        (self.root / "CONTRIBUTING.md").unlink()
        self.assert_fails_with("CONTRIBUTING.md: required file is missing")

    def test_not_a_git_repository_is_rejected(self) -> None:
        not_git = self.tmp / "plain"
        not_git.mkdir()
        code, output = self.validate("--root", str(not_git))
        self.assertEqual(code, 2, output)
        self.assertIn("not a git working tree", output)

    def test_bash_syntax_error_fails(self) -> None:
        self.write("scripts/broken.sh", "#!/usr/bin/env bash\nif then\n")
        self.assert_fails_with("scripts/broken.sh: bash syntax error")

    def test_non_executable_entry_point_fails(self) -> None:
        (self.root / "scripts/install.sh").chmod(0o644)
        self.assert_fails_with("scripts/install.sh: entry-point script must be executable")

    def test_entry_point_tracked_without_executable_bit_fails(self) -> None:
        self.git("add", "-A")
        self.git("update-index", "--chmod=-x", "scripts/uninstall.sh")
        self.assert_fails_with("scripts/uninstall.sh: entry-point script is tracked without the executable bit")


def definition(name: str) -> str:
    return f"---\nname: {name}\ndescription: Synthetic definition for tests.\n---\n\n# {name}\n"


class FrontmatterTests(ValidatorTestCase):
    def test_read_only_agents_must_block_write_and_edit(self) -> None:
        self.replace("dist/claude/agents/debugger.md", "disallowedTools: Write, Edit", "disallowedTools: Write")
        self.replace("dist/claude/agents/reviewer.md", "tools: Read, Glob, Grep, Bash", "tools: Read, Glob, Grep, Bash, Edit")
        output = self.assert_fails_with()
        self.assert_reported(output, "dist/claude/agents/debugger.md", "read-only agent must list 'Edit' in disallowedTools")
        self.assert_reported(output, "dist/claude/agents/reviewer.md", "read-only agent must not allow 'Edit'")

    NEW_READ_ONLY_AGENTS = (
        "dist/claude/agents/analyst.md",
        "dist/claude/agents/architect.md",
        "dist/claude/agents/ux-reviewer.md",
        "dist/claude/agents/security-reviewer.md",
        "dist/claude/agents/privacy-reviewer.md",
        "dist/claude/agents/db-reviewer.md",
        "dist/claude/agents/performance-reviewer.md",
        "dist/claude/agents/api-reviewer.md",
        "dist/claude/agents/debugger.md",
    )

    def test_analyst_and_specialized_reviewers_must_stay_read_only(self) -> None:
        for rel in self.NEW_READ_ONLY_AGENTS:
            with self.subTest(agent=rel):
                original = (self.root / rel).read_text(encoding="utf-8")
                self.replace(
                    rel,
                    "tools: Read, Glob, Grep, Bash\ndisallowedTools: Write, Edit",
                    "tools: Read, Glob, Grep, Bash, Write\ndisallowedTools: Edit",
                )
                try:
                    output = self.assert_fails_with()
                    self.assert_reported(output, rel, "read-only agent must not allow 'Write'")
                    self.assert_reported(output, rel, "read-only agent must list 'Write' in disallowedTools")
                finally:
                    (self.root / rel).write_text(original, encoding="utf-8")

    def test_analyst_and_specialized_reviewers_are_required(self) -> None:
        for rel in self.NEW_READ_ONLY_AGENTS + tuple(r.replace("dist/claude/", "core/") for r in self.NEW_READ_ONLY_AGENTS):
            with self.subTest(agent=rel):
                original = (self.root / rel).read_text(encoding="utf-8")
                (self.root / rel).unlink()
                try:
                    output = self.assert_fails_with()
                    self.assert_reported(output, rel, "required file is missing or is not a regular file")
                finally:
                    (self.root / rel).write_text(original, encoding="utf-8")

    WRITING_AGENTS = (
        "dist/claude/agents/ux.md",
        "dist/claude/agents/designer.md",
        "dist/claude/agents/dev.md",
        "dist/claude/agents/devops.md",
        "dist/claude/agents/tech-writer.md",
        "dist/claude/agents/po.md",
        "dist/claude/agents/metrics-analyst.md",
        "dist/claude/agents/qa.md",
    )

    def test_writing_agents_are_required(self) -> None:
        for rel in self.WRITING_AGENTS + tuple(r.replace("dist/claude/", "core/") for r in self.WRITING_AGENTS):
            with self.subTest(agent=rel):
                original = (self.root / rel).read_text(encoding="utf-8")
                (self.root / rel).unlink()
                try:
                    output = self.assert_fails_with()
                    self.assert_reported(output, rel, "required file is missing or is not a regular file")
                finally:
                    (self.root / rel).write_text(original, encoding="utf-8")

    NEW_SKILLS = (
        "core/skills/flutter-dev/SKILL.md",
        "core/skills/web-dev/SKILL.md",
        "core/skills/supabase-dev/SKILL.md",
        "core/skills/safe-refactor/SKILL.md",
        "core/skills/java-spring-dev/SKILL.md",
        "core/skills/e2e-testing/SKILL.md",
    )

    def test_new_skills_are_required(self) -> None:
        for rel in self.NEW_SKILLS:
            with self.subTest(skill=rel):
                original = (self.root / rel).read_text(encoding="utf-8")
                (self.root / rel).unlink()
                try:
                    output = self.assert_fails_with()
                    self.assert_reported(output, rel, "required file is missing or is not a regular file")
                finally:
                    (self.root / rel).write_text(original, encoding="utf-8")

    def test_qa_may_write_acceptance_tests(self) -> None:
        text = (self.root / "dist/claude/agents/qa.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^tools: Read, Glob, Grep, Bash, Write, Edit$")
        self.assertNotRegex(text, r"(?m)^disallowedTools:")
        self.assert_passes()

    def test_read_only_agents_accept_sequence_tool_lists(self) -> None:
        self.replace(
            "dist/claude/agents/debugger.md",
            "tools: Read, Glob, Grep, Bash\ndisallowedTools: Write, Edit",
            "tools: [Read, Glob, Grep, Bash]\ndisallowedTools: [Write, Edit]",
        )
        # The hand edit makes the generated file stale, but the read-only check accepts the sequence form.
        output = self.assert_fails_with("dist/claude/agents/debugger.md: generated file is stale")
        self.assertNotIn("read-only agent", output)
        self.assertEqual(output.count("ERROR "), 1, output)

    def test_read_only_roles_must_stay_read_only_in_core(self) -> None:
        self.replace("core/agents/reviewer.md", "read_only: true", "read_only: false")
        self.build()
        output = self.assert_fails_with()
        self.assert_reported(output, "core/agents/reviewer.md", "read-only role must set 'read_only: true'")
        self.assert_reported(output, "dist/claude/agents/reviewer.md", "read-only agent must not allow 'Write'")

    def test_any_agent_marked_read_only_is_checked_in_dist(self) -> None:
        self.replace("core/agents/dev.md", "read_only: false", "read_only: true")
        self.build()
        self.assert_passes()
        self.replace("dist/claude/agents/dev.md", "disallowedTools: Write, Edit\n", "")
        output = self.assert_fails_with()
        self.assert_reported(output, "dist/claude/agents/dev.md", "read-only agent must list 'Write' in disallowedTools")

    def test_neutral_agents_need_a_boolean_read_only_and_no_tool_keys(self) -> None:
        self.replace("core/agents/dev.md", "read_only: false", 'read_only: "no"')
        self.replace("core/agents/qa.md", "read_only: false", "read_only: false\ntools: Read")
        output = self.assert_fails_with()
        self.assert_reported(output, "core/agents/dev.md", "frontmatter 'read_only' must be true or false")
        self.assert_reported(
            output, "core/agents/qa.md", "tool-specific key 'tools' does not belong in a neutral agent (use read_only)"
        )
        self.assert_reported(output, "dist", "cannot generate the target files")

    def test_name_mismatch_missing_description_and_duplicate_key_fail(self) -> None:
        self.replace("core/skills/work-session/SKILL.md", "name: work-session", "name: other-session")
        self.replace("core/skills/verify-changes/SKILL.md", "description:", "summary:")
        self.replace("core/agents/reviewer.md", "read_only:", "name: reviewer\nread_only:")
        output = self.assert_fails_with()
        self.assert_reported(output, "core/skills/work-session/SKILL.md", "name 'other-session' must match 'work-session'")
        self.assert_reported(
            output, "core/skills/verify-changes/SKILL.md", "frontmatter 'description' must be a non-empty string"
        )
        self.assert_reported(output, "core/agents/reviewer.md", "duplicate frontmatter key 'name'")

    def test_invalid_yaml_frontmatter_fails(self) -> None:
        self.write("core/agents/bad-quote.md", '---\nname: bad-quote\ndescription: "unclosed\n---\n')
        self.write("core/agents/bad-colon.md", "---\nname: bad-colon\ndescription: invalid: mapping\n---\n")
        # SafeLoader must refuse to construct arbitrary Python objects.
        self.write(
            "core/agents/unsafe-tag.md",
            '---\nname: unsafe-tag\ndescription: !!python/object/apply:os.system ["true"]\n---\n',
        )
        output = self.assert_fails_with()
        for rel in ("core/agents/bad-quote.md", "core/agents/bad-colon.md", "core/agents/unsafe-tag.md"):
            self.assert_reported(output, rel, "invalid YAML frontmatter")

    def test_wrong_frontmatter_types_fail(self) -> None:
        self.write("core/agents/list-name.md", "---\nname: [list-name]\ndescription: Synthetic.\n---\n")
        self.write("core/agents/number-description.md", "---\nname: number-description\ndescription: 42\n---\n")
        self.write("core/agents/empty-description.md", "---\nname: empty-description\ndescription:\n---\n")
        self.write("core/agents/sequence.md", "---\n- sequence\n---\n")
        output = self.assert_fails_with()
        self.assert_reported(output, "core/agents/list-name.md", "frontmatter 'name' must be a non-empty string")
        for rel in ("core/agents/number-description.md", "core/agents/empty-description.md"):
            self.assert_reported(output, rel, "frontmatter 'description' must be a non-empty string")
        self.assert_reported(output, "core/agents/sequence.md", "frontmatter must be a YAML mapping")

    def test_duplicate_definition_names_fail(self) -> None:
        self.write("core/agents/copy-reviewer.md", definition("reviewer"))
        self.write("core/skills/alpha/SKILL.md", definition("alpha"))
        self.write("core/skills/copy-alpha/SKILL.md", definition("alpha"))
        output = self.assert_fails_with(
            "duplicate core agent name 'reviewer'",
            "duplicate skill name 'alpha'",
        )
        self.assert_reported(output, "core/agents/copy-reviewer.md", "name 'reviewer' must match 'copy-reviewer'")

    def test_multiline_and_typed_yaml_frontmatter_passes(self) -> None:
        self.write(
            "core/skills/extra-skill/SKILL.md",
            "---\nname: extra-skill\ndescription: >\n  Folded synthetic text\n  across lines.\n"
            'allowed-tools: Read, Grep\ndisable-model-invocation: true\nargument-hint: "<objective>"\n---\n',
        )
        self.write(
            "core/agents/extra-agent.md",
            "---\nname: extra-agent\ndescription: >\n  Folded synthetic text\n  across lines.\n"
            "read_only: false\nmodel: inherit\n---\n",
        )
        self.build()
        self.assert_passes()

    def test_skill_directory_without_skill_file_fails(self) -> None:
        self.write("core/skills/empty-skill/notes.md", "notes\n")
        self.assert_fails_with("core/skills/empty-skill: skill directory has no readable SKILL.md")

    def test_missing_or_unterminated_frontmatter_fails(self) -> None:
        self.write("core/agents/plain-agent.md", "# no frontmatter\n")
        self.write("core/agents/open-agent.md", "---\nname: open-agent\n")
        self.assert_fails_with(
            "core/agents/plain-agent.md: missing frontmatter",
            "core/agents/open-agent.md: unterminated frontmatter",
        )

    def test_unreadable_or_symlinked_definitions_fail(self) -> None:
        self.make_unreadable(self.write("core/agents/locked-agent.md", definition("locked-agent")))
        (self.root / "core/skills/linked-skill").mkdir()
        os.symlink("../work-session/SKILL.md", self.root / "core/skills/linked-skill/SKILL.md")
        self.assert_fails_with(
            "core/agents/locked-agent.md: agent definition is missing, unreadable or unsafe",
            "core/skills/linked-skill: skill directory has no readable SKILL.md",
        )

    def test_tracked_definitions_deleted_from_worktree_fail(self) -> None:
        agent = self.write("core/agents/gone-agent.md", definition("gone-agent"))
        skill = self.write("core/skills/gone-skill/SKILL.md", definition("gone-skill"))
        self.git("add", "core/agents/gone-agent.md", "core/skills/gone-skill/SKILL.md")
        agent.unlink()
        skill.unlink()
        self.assert_fails_with(
            "core/agents/gone-agent.md: agent definition is missing, unreadable or unsafe",
            "core/skills/gone-skill: skill directory has no readable SKILL.md",
        )


class ReferenceTests(ValidatorTestCase):
    def test_broken_local_link_fails(self) -> None:
        self.append("README.md", "\nSee [missing](docs/missing.md#section).\n")
        self.assert_fails_with("README.md", "broken local link 'docs/missing.md#section'")

    def test_link_escaping_repository_fails(self) -> None:
        (self.tmp / "outside.md").write_text("outside\n")
        self.append("README.md", "\nSee [outside](../outside.md).\n")
        self.assert_fails_with("broken local link '../outside.md'")

    def test_links_inside_code_fences_are_ignored(self) -> None:
        self.append("README.md", "\n```\n[example](does/not/exist.md)\n```\n")
        self.assert_passes()

    def test_missing_installed_rule_reference_fails(self) -> None:
        self.append("core/instructions.md", "\nAlso apply `~/.claude/rules/missing-rule.md`.\n")
        self.assert_fails_with("core/instructions.md:", "reference ~/.claude/rules/missing-rule.md has no managed source")

    def test_installed_references_resolve_through_the_managed_items(self) -> None:
        self.append(
            "core/rules/testing.md",
            "\nSee `~/.claude/skills/web-dev/SKILL.md`, `~/.claude/agents/` and `~/.claude/CLAUDE.md`.\n",
        )
        self.assert_passes()
        self.append("core/rules/testing.md", "\nAlso `~/.claude/skills/web-dev/missing.md`.\n")
        self.assert_fails_with("reference ~/.claude/skills/web-dev/missing.md has no managed source")

    def test_unmanaged_global_paths_are_not_required(self) -> None:
        self.append("README.md", "\nSettings in `~/.claude/settings.json` stay untouched.\n")
        self.assert_passes()


class SkillReferenceTests(ValidatorTestCase):
    SKILL = "core/skills/work-session/SKILL.md"
    REFERENCE = "core/skills/work-session/references/commit-proposal.md"

    def test_missing_mandatory_reference_fails(self) -> None:
        (self.root / self.REFERENCE).unlink()
        output = self.assert_fails_with(f"{self.REFERENCE}: required file is missing")
        self.assert_reported(output, self.SKILL, "broken local link 'references/commit-proposal.md'")

    def test_broken_link_to_reference_fails(self) -> None:
        self.append(self.SKILL, "\nSee [extra steps](references/missing-steps.md).\n")
        self.assert_fails_with(f"{self.SKILL}", "broken local link 'references/missing-steps.md'")

    def test_symlinked_reference_is_not_accepted(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("outside\n")
        (self.root / self.REFERENCE).unlink()
        os.symlink(outside, self.root / self.REFERENCE)
        output = self.assert_fails_with(f"{self.REFERENCE}: required file is missing or is not a regular file")
        self.assert_reported(output, self.REFERENCE, "symlink points outside the repository")

    def test_reference_prose_needs_no_frontmatter(self) -> None:
        self.write("core/skills/work-session/references/extra.md", "# Extra\n\nPlain reference prose.\n")
        self.write("core/skills/verify-changes/references/extra.md", "Plain reference prose.\n")
        self.assert_passes()

    def test_references_are_checked_for_unsafe_and_obsolete_instructions(self) -> None:
        self.append(self.REFERENCE, "\nThen run `sudo make install`.\n")
        self.append("core/skills/verify-changes/references/commit-messages.md", "\nNever push automatically.\n")
        output = self.assert_fails_with()
        self.assert_reported(output, self.REFERENCE, "unsafe prescription without a prohibition (sudo)")
        self.assert_reported(
            output,
            "core/skills/verify-changes/references/commit-messages.md",
            "obsolete delivery instruction (unconditional push prohibition)",
        )


class ManagedItemsTests(ValidatorTestCase):
    COMMON = "scripts/lib/common.sh"
    NOTIFY_ITEM = '"targets/codex/notify/agent-notify-codex.sh|.codex/notify/agent-notify-codex.sh|claude"'

    def test_required_agents_must_be_managed(self) -> None:
        for agent in ("analyst", "architect", "ux", "designer", "security-reviewer", "privacy-reviewer", "db-reviewer", "performance-reviewer", "dev", "devops", "tech-writer", "po", "debugger", "api-reviewer", "metrics-analyst"):
            with self.subTest(agent=agent):
                item = f'  "dist/claude/agents/{agent}.md|.claude/agents/{agent}.md|claude"\n'
                original = (self.root / self.COMMON).read_text(encoding="utf-8")
                self.replace(self.COMMON, item, "")
                try:
                    output = self.assert_fails_with()
                    self.assert_reported(
                        output,
                        self.COMMON,
                        f"agent 'dist/claude/agents/{agent}.md' must be managed as '.claude/agents/{agent}.md'",
                    )
                finally:
                    (self.root / self.COMMON).write_text(original, encoding="utf-8")

    def test_agent_managed_at_wrong_destination_fails(self) -> None:
        self.replace(self.COMMON, "|.claude/agents/ux-reviewer.md|", "|.claude/agents/other.md|")
        self.assert_fails_with("agent 'dist/claude/agents/ux-reviewer.md' must be managed as '.claude/agents/ux-reviewer.md'")

    def test_base_codex_config_can_never_be_managed(self) -> None:
        self.replace(self.COMMON, self.NOTIFY_ITEM, '"targets/codex/notify/agent-notify-codex.sh|.codex/config.toml|claude"')
        self.assert_fails_with("managed items must never replace the base Codex config")

    def test_missing_source_duplicate_destination_and_malformed_entry_fail(self) -> None:
        self.replace(
            self.COMMON,
            self.NOTIFY_ITEM,
            self.NOTIFY_ITEM + '\n  "claude/missing.md|.claude/CLAUDE.md|claude"\n  unquoted|entry\n  "core/rules/testing.md|.claude/rules/two-fields.md"',
        )
        output = self.assert_fails_with()
        self.assert_reported(output, self.COMMON, "managed source 'claude/missing.md' is missing or unsafe")
        self.assert_reported(output, self.COMMON, "duplicate managed destination '.claude/CLAUDE.md'")
        self.assert_reported(output, self.COMMON, "malformed MANAGED_ITEMS entry")
        self.assertEqual(output.count("malformed MANAGED_ITEMS entry"), 2, output)

    def test_missing_managed_items_array_fails(self) -> None:
        self.replace(self.COMMON, "MANAGED_ITEMS=(", "OTHER_ITEMS=(")
        self.assert_fails_with("MANAGED_ITEMS array not found")

    RETIRED_ITEM = '"claude/agents/senior-reviewer.md|.claude/agents/senior-reviewer.md"'

    def test_retired_source_that_still_exists_fails(self) -> None:
        self.write("claude/agents/senior-reviewer.md", "---\nname: senior-reviewer\ndescription: Synthetic.\n---\n")
        output = self.assert_fails_with()
        self.assert_reported(
            output, self.COMMON, "retired source 'claude/agents/senior-reviewer.md' still exists in the checkout"
        )

    def test_retired_supervision_sources_with_content_fail(self) -> None:
        self.write("codex/skills/supervise-claude/SKILL.md", definition("supervise-claude"))
        self.write("codex/profiles/ifdev-autonomous.config.toml", 'sandbox_mode = "read-only"\n')
        output = self.assert_fails_with()
        for source in ("codex/skills/supervise-claude", "codex/profiles/ifdev-autonomous.config.toml"):
            self.assert_reported(output, self.COMMON, f"retired source '{source}' still exists in the checkout")

    def test_retired_directory_with_only_ignored_leftovers_passes(self) -> None:
        # Git keeps ignored caches after the tracked files of a retired directory are removed.
        cache = self.root / "codex/skills/supervise-claude/scripts/__pycache__/run_claude.cpython-312.pyc"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"synthetic cache")
        self.assert_passes()

    def test_retired_destination_colliding_with_managed_destination_fails(self) -> None:
        self.replace(self.COMMON, self.RETIRED_ITEM, '"claude/agents/old-reviewer.md|.claude/agents/reviewer.md"')
        output = self.assert_fails_with()
        self.assert_reported(
            output, self.COMMON, "retired destination '.claude/agents/reviewer.md' collides with a managed destination"
        )

    def test_malformed_retired_entry_and_missing_array_fail(self) -> None:
        self.replace(self.COMMON, self.RETIRED_ITEM, self.RETIRED_ITEM + "\n  unquoted|entry")
        self.assert_fails_with("malformed RETIRED_ITEMS entry")
        self.replace(self.COMMON, "RETIRED_ITEMS=(", "OTHER_RETIRED=(")
        self.assert_fails_with("RETIRED_ITEMS array not found")

    def test_retired_items_with_gone_source_and_free_destination_pass(self) -> None:
        self.replace(
            self.COMMON,
            self.RETIRED_ITEM,
            self.RETIRED_ITEM + '\n  # A second synthetic retired item.\n  "claude/rules/old-rule.md|.claude/rules/old-rule.md"',
        )
        self.assert_passes()

    def test_empty_retired_items_array_passes(self) -> None:
        content = (self.root / self.COMMON).read_text(encoding="utf-8")
        start = content.index("RETIRED_ITEMS=(")
        end = content.index("\n)\n", start) + 3
        (self.root / self.COMMON).write_text(content[:start] + "RETIRED_ITEMS=()\n" + content[end:], encoding="utf-8")
        self.assert_passes()


class ContentTests(ValidatorTestCase):
    def test_hardcoded_home_path_fails(self) -> None:
        self.write("docs/notes.md", "Clone into " + "/" + "home/" + "alice/projects.\n")
        self.assert_fails_with("docs/notes.md:1: hardcoded machine-specific home path")

    def test_product_term_fails(self) -> None:
        self.append("core/rules/testing.md", "\nCover " + "house" + "hold membership rules.\n")
        self.assert_fails_with("core/rules/testing.md", "product/domain-specific term")

    def test_validator_source_is_not_exempt_from_product_terms(self) -> None:
        self.append("scripts/validate.py", "\n# " + "la" + "relo\n")
        self.assert_fails_with("scripts/validate.py", "product/domain-specific term")

    def test_obsolete_delivery_instructions_fail(self) -> None:
        self.append(
            "core/instructions.md",
            "\nEvery commit requires explicit user approval.\n"
            "\nAsk for approval before each commit.\n"
            "\nCommit somente com aprovação explícita.\n"
            "\nNever push automatically.\n"
            "\nNunca faça push automaticamente.\n"
            "\nClaude must never push.\n",
        )
        # Installed skills have no implementer-only allowance either.
        self.write("core/skills/other/SKILL.md", definition("other") + "\nClaude must never push.\n")
        output = self.assert_fails_with()

        def count(rel: str, label: str) -> int:
            return len(re.findall(rf"(?m)^ERROR {re.escape(rel)}:\d+: obsolete delivery instruction \({label}\)", output))

        self.assertEqual(count("core/instructions.md", "per-commit approval"), 3, output)
        self.assertEqual(count("core/instructions.md", "unconditional push prohibition"), 3, output)
        self.assertEqual(count("core/skills/other/SKILL.md", "unconditional push prohibition"), 1, output)
        # The generated copy carries the same content and is checked too.
        self.build()
        output = self.assert_fails_with()
        self.assertEqual(count("dist/claude/CLAUDE.md", "per-commit approval"), 3, output)

    def test_narrower_delivery_restrictions_pass(self) -> None:
        self.append(
            "core/rules/git-workflow.md",
            "\nWhen the user explicitly restricts delivery, do not push and ask for approval before each commit.\n"
            "\nUnless the task authorizes delivery, ask for approval before each commit.\n"
            "\nDo not push without authorization.\n"
            "\nNever force push.\n",
        )
        self.assert_passes()

    def test_unsafe_prescriptions_fail(self) -> None:
        self.append(
            "core/skills/work-session/SKILL.md",
            "\nAlways run `claude --dangerously-skip-permissions` to save time.\n"
            "\nCommit with `git commit --no-verify` when hooks are slow.\n"
            "\n- Publish with `git push --force origin main`.\n"
            "\nRun Codex with `--sandbox danger-full-access` for speed.\n"
            '\nSet `approval_policy = "never"` in the profile.\n',
        )
        self.assert_fails_with(
            "unsafe prescription without a prohibition (full-access sandbox or approval bypass)",
            "unsafe prescription without a prohibition (permission bypass)",
            "unsafe prescription without a prohibition (hook bypass)",
            "unsafe prescription without a prohibition (force push)",
        )

    def test_prohibitions_of_unsafe_actions_pass(self) -> None:
        self.append(
            "core/skills/work-session/SKILL.md",
            "\nNever run `claude --dangerously-skip-permissions`, `git push --force` or `sudo`.\n"
            "\n- Do not commit with\n  `--no-verify`, e.g. to skip slow hooks.\n"
            "\nAgents must not enable auto-merge; deny `git reset --hard`.\n"
            "\nNever use `danger-full-access` or `approval_policy = never`.\n",
        )
        self.assert_passes()

    def test_prohibition_does_not_cover_a_later_sentence(self) -> None:
        self.append(
            "core/skills/work-session/SKILL.md",
            "\nNever skip reviews. Then run `sudo make install`.\n",
        )
        self.assert_fails_with("unsafe prescription without a prohibition (sudo)")

    def test_commit_emoji_mismatch_fails(self) -> None:
        self.append("core/instructions.md", "\nExample: `\U0001f41b feat: add export`.\n")
        self.assert_fails_with("core/instructions.md", "commit emoji does not match type 'feat'")

    def test_missing_variation_selector_fails(self) -> None:
        recycle_without_selector = chr(0x267B)
        self.append("core/instructions.md", f"\nExample: `{recycle_without_selector} refactor: extract parser`.\n")
        self.assert_fails_with("commit emoji does not match type 'refactor'")

    def test_incomplete_commit_table_fails(self) -> None:
        path = self.root / "core/rules/git-workflow.md"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(line for line in lines if "| `perf` |" not in line), encoding="utf-8")
        self.assert_fails_with("commit type table must list exactly the nine emoji/type pairs")


class SensitiveContentTests(ValidatorTestCase):
    def test_tracked_sensitive_paths_are_reported_without_reading(self) -> None:
        env_file = self.write(".env", f"VALUE={MARKER}\n")
        self.write("config/credentials.json", f'{{"value": "{MARKER}"}}\n')
        # Force-add: the index must be checked even for paths .gitignore excludes.
        self.git("add", "-f", ".env", "config/credentials.json")
        self.make_unreadable(env_file)
        if hasattr(os, "mkfifo"):
            # Replace the tracked file with a FIFO; opening it for reading would block.
            (self.root / "config/credentials.json").unlink()
            os.mkfifo(self.root / "config/credentials.json")

        output = self.assert_fails_with(
            ".env: sensitive path is tracked in the git index (content not inspected)",
            "config/credentials.json: sensitive path is tracked in the git index",
        )
        self.assertNotIn(MARKER, output)
        self.assertNotIn("cannot read", output)

    def test_untracked_sensitive_paths_are_left_untouched(self) -> None:
        # Not covered by .gitignore, so these appear in the untracked inventory.
        self.make_unreadable(self.write(".aws/config", f"{MARKER}\n"))
        self.make_unreadable(self.write("keys/deploy.PEM", f"{MARKER}\n"))
        if hasattr(os, "mkfifo"):
            (self.root / ".ssh").mkdir()
            os.mkfifo(self.root / ".ssh/config")

        self.assert_passes()

    def test_credential_patterns_are_reported_without_value(self) -> None:
        aws_value = "AK" + "IA" + "SYNTHETICEXAMPLE"
        github_value = "gh" + "p_" + "A" * 36
        self.write("docs/example.md", f"aws = {aws_value}\n\ngithub = {github_value}\n")

        output = self.assert_fails_with(
            "docs/example.md:1: possible credential (AWS access key id); value not shown",
            "docs/example.md:3: possible credential (GitHub token); value not shown",
        )
        self.assertNotIn(aws_value, output)
        self.assertNotIn(github_value, output)

    def test_other_diagnostics_never_echo_credential_values(self) -> None:
        github_value = "gh" + "p_" + "A" * 36
        self.write(
            "core/agents/dup-key.md",
            f"---\nname: dup-key\ndescription: Synthetic.\n{github_value}: a\n{github_value}: b\n---\n",
        )
        self.write("core/agents/bad-tag.md", f"---\nname: bad-tag\ndescription: !{github_value} value\n---\n")
        self.append("README.md", f"\nSee [missing](docs/{github_value}.md).\n")
        # A function body must be a compound command, so bash reports the word as the unexpected token.
        self.write("scripts/broken-token.sh", f"#!/usr/bin/env bash\nf() {github_value}\n")

        output = self.assert_fails_with()
        self.assert_reported(output, "core/agents/dup-key.md", "duplicate frontmatter key")
        self.assert_reported(output, "core/agents/bad-tag.md", "invalid YAML frontmatter")
        self.assert_reported(output, "README.md", "broken local link")
        self.assert_reported(output, "scripts/broken-token.sh", "bash syntax error")
        self.assertNotIn(github_value, output)

    def test_symlink_escaping_repository_fails(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("outside\n")
        os.symlink(outside, self.root / "docs-link.md")
        self.assert_fails_with("docs-link.md: symlink points outside the repository")

    def test_sensitive_path_classification(self) -> None:
        validator = self.load_validator()
        sensitive = (
            ".env",
            "config/.env.local",
            ".credentials.json",
            "credentials/config.json",
            "secrets/nested.txt",
            "tokens/list.md",
            "auth.json",
            "auth.yaml",
            "config/.auth.yml",
            "auth/notes.md",
            ".aws/config",
            ".kube/config",
            ".docker/config.json",
            ".config/gcloud/adc.json",
            ".config/gh/hosts.yml",
            ".ssh/known_hosts",
            "keys/id_ed25519.pub",
            "keys/deploy.PEM",
            "keys/putty.ppk",
            "keys/store.jks",
            ".netrc",
            ".git-credentials",
        )
        safe = (
            "README.md",
            "docs/authentication.md",
            "docs/author.md",
            "core/skills/verify-changes/SKILL.md",
            "scripts/validate.py",
            ".config/tool/settings.toml",
            ".github/workflows/validate.yml",
        )
        for rel in sensitive:
            self.assertTrue(validator.is_sensitive_path(rel), rel)
        for rel in safe:
            self.assertFalse(validator.is_sensitive_path(rel), rel)

    def test_sensitive_paths_are_never_read(self) -> None:
        sensitive = (
            ".credentials.json",
            "auth.yaml",
            "config/.auth.yml",
            "secrets/nested.txt",
            "credentials/config.json",
            "tokens/list.md",
            ".kube/config",
            ".config/gcloud/adc.json",
            ".docker/config.json",
            "keys/putty.ppk",
        )
        for rel in sensitive:
            self.write(rel, f"{MARKER}\n")
        self.git("add", "-f", "auth.yaml", "secrets/nested.txt")
        validator = self.load_validator()
        real_read = validator.read_regular_file
        opened = []

        def guarded_read(path):
            rel = Path(path).relative_to(self.root).as_posix()
            opened.append(rel)
            if validator.is_sensitive_path(rel):
                raise AssertionError(f"sensitive path read: {rel}")
            return real_read(path)

        with mock.patch.object(validator, "read_regular_file", guarded_read):
            report = self.validate_in_process(validator)

        self.assertIn("scripts/validate.py", opened)  # the guard really intercepted reads
        self.assertFalse(set(opened) & set(sensitive), opened)
        self.assertEqual(
            sorted(error for error in report.errors if "sensitive path" in error),
            [
                "auth.yaml: sensitive path is tracked in the git index (content not inspected)",
                "secrets/nested.txt: sensitive path is tracked in the git index (content not inspected)",
            ],
        )
        self.assertFalse([error for error in report.errors if MARKER in error])

    def test_tracked_sensitive_path_deleted_from_worktree_is_reported_by_name(self) -> None:
        self.write(".credentials.json", f"{MARKER}\n")
        self.git("add", "-f", ".credentials.json")
        (self.root / ".credentials.json").unlink()
        output = self.assert_fails_with(".credentials.json: sensitive path is tracked in the git index")
        self.assertNotIn(MARKER, output)

    def test_tracked_file_below_symlinked_parent_is_not_read(self) -> None:
        self.write("docs/guide.md", "guide\n")
        self.git("add", "docs/guide.md")
        outside = self.tmp / "outside-docs"
        outside.mkdir()
        aws_value = "AK" + "IA" + "SYNTHETICEXAMPLE"
        (outside / "guide.md").write_text(f"{MARKER} {aws_value}\n")
        shutil.rmtree(self.root / "docs")
        os.symlink(outside, self.root / "docs")

        output = self.assert_fails_with("docs/guide.md: parent path is a symlink or not a directory (not read)")
        self.assertNotIn("possible credential", output)
        self.assertNotIn(MARKER, output)


class GeneratedTargetTests(ValidatorTestCase):
    COMMON = "scripts/lib/common.sh"

    def test_stale_missing_and_extra_generated_files_fail(self) -> None:
        self.replace("core/agents/dev.md", "Senior developer", "Senior engineer")
        (self.root / "dist/claude/CLAUDE.md").unlink()
        self.write("dist/claude/agents/hand-written.md", definition("hand-written"))
        output = self.assert_fails_with()
        hint = "; run python3 scripts/build_targets.py"
        self.assert_reported(output, "dist/claude/agents/dev.md", "generated file is stale" + hint)
        self.assert_reported(output, "dist/claude/CLAUDE.md", "generated file is missing" + hint)
        self.assert_reported(output, "dist/claude/agents/hand-written.md", "file is not produced by the generator" + hint)

    def test_regenerated_output_passes(self) -> None:
        self.replace("core/agents/dev.md", "Senior developer", "Senior engineer")
        self.append("core/instructions.md", "\nOne more synthetic paragraph.\n")
        self.build()
        self.assert_passes()
        self.assertIn("Senior engineer", (self.root / "dist/claude/agents/dev.md").read_text(encoding="utf-8"))

    def test_missing_generator_stops_validation(self) -> None:
        (self.root / "scripts/build_targets.py").unlink()
        self.assert_fails_with("No module named 'build_targets'")


class CompatLinkTests(ValidatorTestCase):
    def test_compat_links_are_required_with_their_exact_targets(self) -> None:
        (self.root / "claude/rules").unlink()
        (self.root / "claude/agents").unlink()
        os.symlink("../core/agents", self.root / "claude/agents")
        output = self.assert_fails_with()
        self.assert_reported(output, "claude/rules", "compatibility link must be a symlink to '../core/rules'")
        self.assert_reported(output, "claude/agents", "compatibility link must be a symlink to '../dist/claude/agents'")

    def test_dangling_compat_link_fails(self) -> None:
        shutil.rmtree(self.root / "targets/codex")
        output = self.assert_fails_with("codex/notify: compatibility link is dangling")
        self.assertIn("targets/codex/notify/agent-notify-codex.sh: required file is missing", output)

    def test_unlisted_symlink_inside_the_repository_fails(self) -> None:
        os.symlink("../core/rules/testing.md", self.root / "docs-testing.md")
        self.assert_fails_with("docs-testing.md: symlink is not a listed compatibility link")

    def test_retired_source_recreated_through_a_compat_link_fails(self) -> None:
        # claude/agents is a compat link, so this lands in dist/claude/agents/senior-reviewer.md.
        self.write("claude/agents/senior-reviewer.md", definition("senior-reviewer"))
        output = self.assert_fails_with()
        self.assert_reported(
            output, "scripts/lib/common.sh", "retired source 'claude/agents/senior-reviewer.md' still exists in the checkout"
        )


class MovedAndTargetItemsTests(ValidatorTestCase):
    COMMON = "scripts/lib/common.sh"
    MOVED_ITEM = '"claude/CLAUDE.md|.claude/CLAUDE.md"'

    def test_moved_item_must_map_a_managed_destination_from_a_gone_source(self) -> None:
        self.write("docs/old-instructions.md", "old\n")
        self.replace(
            self.COMMON,
            self.MOVED_ITEM,
            self.MOVED_ITEM
            + '\n  "claude/rules/old.md|.claude/rules/not-managed.md"'
            + '\n  "core/rules/testing.md|.claude/rules/testing.md"'
            + '\n  "docs/old-instructions.md|.claude/rules/git-workflow.md"'
            + '\n  "claude/rules/testing.md|.claude/rules/engineering-principles.md"'
            + "\n  malformed|moved",
        )
        output = self.assert_fails_with()
        self.assert_reported(output, self.COMMON, "moved destination '.claude/rules/not-managed.md' is not a managed destination")
        self.assert_reported(
            output, self.COMMON, "moved source 'core/rules/testing.md' is still the managed source of '.claude/rules/testing.md'"
        )
        self.assert_reported(
            output, self.COMMON, "moved source 'docs/old-instructions.md' still exists outside the compatibility links"
        )
        self.assert_reported(
            output,
            self.COMMON,
            "moved source 'claude/rules/testing.md' does not resolve to the managed source of"
            " '.claude/rules/engineering-principles.md'",
        )
        self.assert_reported(output, self.COMMON, "malformed MOVED_ITEMS entry")
        self.assertNotIn("moved source 'claude/CLAUDE.md'", output)

    def test_missing_moved_items_array_fails_and_an_empty_one_passes(self) -> None:
        content = (self.root / self.COMMON).read_text(encoding="utf-8")
        start = content.index("MOVED_ITEMS=(")
        end = content.index("\n)\n", start) + 3
        (self.root / self.COMMON).write_text(content[:start] + "MOVED_ITEMS=()\n" + content[end:], encoding="utf-8")
        self.assert_passes()
        self.replace(self.COMMON, "MOVED_ITEMS=()", "OTHER_MOVED=()")
        self.assert_fails_with("MOVED_ITEMS array not found")

    def test_managed_item_targets_must_be_available(self) -> None:
        self.replace(self.COMMON, "|.claude/rules/testing.md|claude", "|.claude/rules/testing.md|claude,cursor")
        self.assert_fails_with("managed item '.claude/rules/testing.md' has unsupported targets: cursor")
        self.replace(self.COMMON, 'AVAILABLE_TARGETS="claude"', "OTHER_TARGETS=claude")
        self.assert_fails_with("AVAILABLE_TARGETS not found")


if __name__ == "__main__":
    unittest.main()
