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
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".editorconfig",
    ".gitignore",
    "requirements-dev.txt",
    ".github",
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

    def load_validator(self):
        spec = importlib.util.spec_from_file_location("fixture_validate", self.root / "scripts/validate.py")
        module = importlib.util.module_from_spec(spec)
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
    def test_name_mismatch_missing_description_and_duplicate_key_fail(self) -> None:
        self.replace("claude/skills/work-session/SKILL.md", "name: work-session", "name: other-session")
        self.replace("claude/skills/verify-changes/SKILL.md", "description:", "summary:")
        self.replace("claude/agents/senior-reviewer.md", "tools: Read", "name: senior-reviewer\ntools: Read")
        output = self.assert_fails_with()
        self.assert_reported(output, "claude/skills/work-session/SKILL.md", "name 'other-session' must match 'work-session'")
        self.assert_reported(
            output, "claude/skills/verify-changes/SKILL.md", "frontmatter 'description' must be a non-empty string"
        )
        self.assert_reported(output, "claude/agents/senior-reviewer.md", "duplicate frontmatter key 'name'")

    def test_invalid_yaml_frontmatter_fails(self) -> None:
        self.write("claude/agents/bad-quote.md", '---\nname: bad-quote\ndescription: "unclosed\n---\n')
        self.write("claude/agents/bad-colon.md", "---\nname: bad-colon\ndescription: invalid: mapping\n---\n")
        # SafeLoader must refuse to construct arbitrary Python objects.
        self.write(
            "claude/agents/unsafe-tag.md",
            '---\nname: unsafe-tag\ndescription: !!python/object/apply:os.system ["true"]\n---\n',
        )
        output = self.assert_fails_with()
        for rel in ("claude/agents/bad-quote.md", "claude/agents/bad-colon.md", "claude/agents/unsafe-tag.md"):
            self.assert_reported(output, rel, "invalid YAML frontmatter")

    def test_wrong_frontmatter_types_fail(self) -> None:
        self.write("claude/agents/list-name.md", "---\nname: [list-name]\ndescription: Synthetic.\n---\n")
        self.write("claude/agents/number-description.md", "---\nname: number-description\ndescription: 42\n---\n")
        self.write("claude/agents/empty-description.md", "---\nname: empty-description\ndescription:\n---\n")
        self.write("claude/agents/sequence.md", "---\n- sequence\n---\n")
        output = self.assert_fails_with()
        self.assert_reported(output, "claude/agents/list-name.md", "frontmatter 'name' must be a non-empty string")
        for rel in ("claude/agents/number-description.md", "claude/agents/empty-description.md"):
            self.assert_reported(output, rel, "frontmatter 'description' must be a non-empty string")
        self.assert_reported(output, "claude/agents/sequence.md", "frontmatter must be a YAML mapping")

    def test_duplicate_definition_names_fail(self) -> None:
        self.write("claude/agents/copy-reviewer.md", definition("senior-reviewer"))
        self.write("codex/skills/copy-supervisor/SKILL.md", definition("supervise-claude"))
        output = self.assert_fails_with(
            "duplicate claude agent name 'senior-reviewer'",
            "duplicate codex skill name 'supervise-claude'",
        )
        self.assert_reported(output, "claude/agents/copy-reviewer.md", "name 'senior-reviewer' must match 'copy-reviewer'")

    def test_multiline_and_typed_yaml_frontmatter_passes(self) -> None:
        self.write(
            "claude/agents/extra-agent.md",
            "---\nname: extra-agent\ndescription: >\n  Folded synthetic text\n  across lines.\n"
            'tools: Read, Grep\ndisable-model-invocation: true\nargument-hint: "<objective>"\n---\n',
        )
        self.assert_passes()

    def test_skill_directory_without_skill_file_fails(self) -> None:
        self.write("codex/skills/empty-skill/notes.md", "notes\n")
        self.assert_fails_with("codex/skills/empty-skill: skill directory has no readable SKILL.md")

    def test_missing_or_unterminated_frontmatter_fails(self) -> None:
        self.write("claude/agents/plain-agent.md", "# no frontmatter\n")
        self.write("claude/agents/open-agent.md", "---\nname: open-agent\n")
        self.assert_fails_with(
            "claude/agents/plain-agent.md: missing frontmatter",
            "claude/agents/open-agent.md: unterminated frontmatter",
        )

    def test_unreadable_or_symlinked_definitions_fail(self) -> None:
        self.make_unreadable(self.write("claude/agents/locked-agent.md", definition("locked-agent")))
        (self.root / "claude/skills/linked-skill").mkdir()
        os.symlink("../work-session/SKILL.md", self.root / "claude/skills/linked-skill/SKILL.md")
        self.assert_fails_with(
            "claude/agents/locked-agent.md: agent definition is missing, unreadable or unsafe",
            "claude/skills/linked-skill: skill directory has no readable SKILL.md",
        )

    def test_tracked_definitions_deleted_from_worktree_fail(self) -> None:
        agent = self.write("claude/agents/gone-agent.md", definition("gone-agent"))
        skill = self.write("codex/skills/gone-skill/SKILL.md", definition("gone-skill"))
        self.git("add", "claude/agents/gone-agent.md", "codex/skills/gone-skill/SKILL.md")
        agent.unlink()
        skill.unlink()
        self.assert_fails_with(
            "claude/agents/gone-agent.md: agent definition is missing, unreadable or unsafe",
            "codex/skills/gone-skill: skill directory has no readable SKILL.md",
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
        self.append("claude/CLAUDE.md", "\nAlso apply `~/.claude/rules/missing-rule.md`.\n")
        self.assert_fails_with("reference ~/.claude/rules/missing-rule.md has no source claude/rules/missing-rule.md")

    def test_unmanaged_global_paths_are_not_required(self) -> None:
        self.append("README.md", "\nSettings in `~/.claude/settings.json` stay untouched.\n")
        self.assert_passes()


class SkillReferenceTests(ValidatorTestCase):
    SKILL = "codex/skills/supervise-claude/SKILL.md"
    REFERENCE = "codex/skills/supervise-claude/references/review.md"

    def test_missing_mandatory_reference_fails(self) -> None:
        (self.root / self.REFERENCE).unlink()
        output = self.assert_fails_with(f"{self.REFERENCE}: required file is missing")
        self.assert_reported(output, self.SKILL, "broken local link 'references/review.md'")

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
        self.write("codex/skills/supervise-claude/references/extra.md", "# Extra\n\nPlain reference prose.\n")
        self.write("claude/skills/verify-changes/references/extra.md", "Plain reference prose.\n")
        self.assert_passes()

    def test_references_are_checked_for_unsafe_and_obsolete_instructions(self) -> None:
        self.append(self.REFERENCE, "\nThen run `sudo make install`.\n")
        self.append("claude/skills/verify-changes/references/commit-messages.md", "\nNever push automatically.\n")
        output = self.assert_fails_with()
        self.assert_reported(output, self.REFERENCE, "unsafe prescription without a prohibition (sudo)")
        self.assert_reported(
            output,
            "claude/skills/verify-changes/references/commit-messages.md",
            "obsolete delivery instruction (unconditional push prohibition)",
        )


class CodexProfileTests(ValidatorTestCase):
    PROFILE = "codex/profiles/ifdev-autonomous.config.toml"

    def test_repository_profile_matches_the_safe_settings(self) -> None:
        validator = self.load_validator()
        report = validator.Report()
        settings = validator.parse_profile(self.PROFILE, (self.root / self.PROFILE).read_text(), report)
        self.assertEqual(report.errors, [])
        self.assertEqual(settings, validator.CODEX_PROFILES[self.PROFILE])
        self.assertEqual(settings["approval_policy"], "never")
        self.assertEqual(settings["sandbox_mode"], "workspace-write")
        self.assertIs(settings["sandbox_workspace_write.network_access"], True)
        self.assertNotIn("approvals_reviewer", settings)
        self.assertNotIn("danger-full-access", settings.values())

    def test_autonomous_supervision_keeps_dangerous_operations_prohibited(self) -> None:
        policy = (self.root / "codex/skills/supervise-claude/SKILL.md").read_text().lower()
        for prohibition in (
            "force push",
            "rewrite remote history",
            "git reset --hard",
            "destructive `git clean`",
            "sudo",
            "ssh/scp",
            "deploy",
            "production",
            "extract real project secrets",
            "approve/merge a pr",
            "auto-merge",
        ):
            with self.subTest(prohibition=prohibition):
                self.assertIn(prohibition, policy)

    @unittest.skipUnless(importlib.util.find_spec("tomllib"), "tomllib requires Python 3.11+")
    def test_profile_subset_parser_agrees_with_real_toml(self) -> None:
        import tomllib

        data = tomllib.loads((self.root / self.PROFILE).read_text())
        flat = {k: v for k, v in data.items() if not isinstance(v, dict)}
        flat.update({f"{t}.{k}": v for t, table in data.items() if isinstance(table, dict) for k, v in table.items()})
        self.assertEqual(flat, self.load_validator().CODEX_PROFILES[self.PROFILE])

    def test_dangerous_profile_values_fail(self) -> None:
        self.replace(self.PROFILE, 'sandbox_mode = "workspace-write"', 'sandbox_mode = "danger-full-access"')
        output = self.assert_fails_with()
        self.assert_reported(output, self.PROFILE, "forbidden profile setting 'sandbox_mode = danger-full-access'")

    def test_changed_missing_extra_or_duplicate_settings_fail(self) -> None:
        self.replace(self.PROFILE, 'approval_policy = "never"\n', "")
        self.replace(self.PROFILE, "network_access = true", "network_access = false")
        self.append(self.PROFILE, 'writable_roots = "/"\nnetwork_access = true\n')
        output = self.assert_fails_with()
        self.assert_reported(output, self.PROFILE, "duplicate profile setting 'sandbox_workspace_write.network_access'")
        self.replace(self.PROFILE, "network_access = true\n", "")
        output = self.assert_fails_with()
        self.assert_reported(output, self.PROFILE, "missing profile setting 'approval_policy'")
        self.assert_reported(output, self.PROFILE, "profile setting 'sandbox_workspace_write.network_access' must be True")
        self.assert_reported(output, self.PROFILE, "unexpected profile setting 'sandbox_workspace_write.writable_roots'")

    def test_unsupported_profile_syntax_fails(self) -> None:
        self.append(self.PROFILE, "shell_environment_policy.inherit = [\"all\"]\n")
        self.assert_fails_with(f"{self.PROFILE}", "unsupported profile syntax")

    def test_unknown_profile_file_fails(self) -> None:
        self.write("codex/profiles/other.config.toml", 'sandbox_mode = "read-only"\n')
        self.assert_fails_with("codex/profiles/other.config.toml: unmanaged Codex profile")


class ManagedItemsTests(ValidatorTestCase):
    COMMON = "scripts/lib/common.sh"
    PROFILE_ITEM = '"codex/profiles/ifdev-autonomous.config.toml|.codex/ifdev-autonomous.config.toml"'

    def test_unmanaged_profile_fails(self) -> None:
        self.replace(self.COMMON, self.PROFILE_ITEM, "")
        self.assert_fails_with("must be managed as '.codex/ifdev-autonomous.config.toml'")

    def test_base_codex_config_can_never_be_managed(self) -> None:
        self.replace(
            self.COMMON, self.PROFILE_ITEM, '"codex/profiles/ifdev-autonomous.config.toml|.codex/config.toml"'
        )
        self.assert_fails_with(
            "managed items must never replace the base Codex config",
            "must be managed as '.codex/ifdev-autonomous.config.toml'",
        )

    def test_missing_source_duplicate_destination_and_malformed_entry_fail(self) -> None:
        self.replace(
            self.COMMON,
            self.PROFILE_ITEM,
            self.PROFILE_ITEM + '\n  "claude/missing.md|.claude/CLAUDE.md"\n  unquoted|entry',
        )
        output = self.assert_fails_with()
        self.assert_reported(output, self.COMMON, "managed source 'claude/missing.md' is missing or unsafe")
        self.assert_reported(output, self.COMMON, "duplicate managed destination '.claude/CLAUDE.md'")
        self.assert_reported(output, self.COMMON, "malformed MANAGED_ITEMS entry")

    def test_missing_managed_items_array_fails(self) -> None:
        self.replace(self.COMMON, "MANAGED_ITEMS=(", "OTHER_ITEMS=(")
        self.assert_fails_with("MANAGED_ITEMS array not found")


class ContentTests(ValidatorTestCase):
    def test_hardcoded_home_path_fails(self) -> None:
        self.write("docs/notes.md", "Clone into " + "/" + "home/" + "alice/projects.\n")
        self.assert_fails_with("docs/notes.md:1: hardcoded machine-specific home path")

    def test_product_term_fails(self) -> None:
        self.append("claude/rules/testing.md", "\nCover " + "house" + "hold membership rules.\n")
        self.assert_fails_with("claude/rules/testing.md", "product/domain-specific term")

    def test_validator_source_is_not_exempt_from_product_terms(self) -> None:
        self.append("scripts/validate.py", "\n# " + "la" + "relo\n")
        self.assert_fails_with("scripts/validate.py", "product/domain-specific term")

    def test_obsolete_delivery_instructions_fail(self) -> None:
        self.append(
            "claude/CLAUDE.md",
            "\nEvery commit requires explicit user approval.\n"
            "\nAsk for approval before each commit.\n"
            "\nCommit somente com aprovação explícita.\n"
            "\nNever push automatically.\n"
            "\nNunca faça push automaticamente.\n"
            # The implementer-only allowance is limited to the supervisor skill.
            "\nClaude must never push.\n",
        )
        self.append("codex/skills/supervise-claude/SKILL.md", "\nCodex must never push the task branch.\n")
        output = self.assert_fails_with()

        def count(rel: str, label: str) -> int:
            return len(re.findall(rf"(?m)^ERROR {re.escape(rel)}:\d+: obsolete delivery instruction \({label}\)", output))

        self.assertEqual(count("claude/CLAUDE.md", "per-commit approval"), 3, output)
        self.assertEqual(count("claude/CLAUDE.md", "unconditional push prohibition"), 3, output)
        self.assertEqual(count("codex/skills/supervise-claude/SKILL.md", "unconditional push prohibition"), 1, output)

    def test_narrower_delivery_restrictions_pass(self) -> None:
        self.append(
            "claude/rules/git-workflow.md",
            "\nWhen the user explicitly restricts delivery, do not push and ask for approval before each commit.\n"
            "\nUnless the task authorizes delivery, ask for approval before each commit.\n"
            "\nDo not push without authorization.\n"
            "\nNever force push.\n",
        )
        self.append("codex/skills/supervise-claude/SKILL.md", "\nClaude must never push or commit; Codex delivers.\n")
        self.assert_passes()

    def test_unsafe_prescriptions_fail(self) -> None:
        self.append(
            "claude/skills/work-session/SKILL.md",
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
            "claude/skills/work-session/SKILL.md",
            "\nNever run `claude --dangerously-skip-permissions`, `git push --force` or `sudo`.\n"
            "\n- Do not commit with\n  `--no-verify`, e.g. to skip slow hooks.\n"
            "\nAgents must not enable auto-merge; deny `git reset --hard`.\n"
            "\nNever use `danger-full-access` or `approval_policy = never`.\n",
        )
        self.assert_passes()

    def test_prohibition_does_not_cover_a_later_sentence(self) -> None:
        self.append(
            "claude/skills/work-session/SKILL.md",
            "\nNever skip reviews. Then run `sudo make install`.\n",
        )
        self.assert_fails_with("unsafe prescription without a prohibition (sudo)")

    def test_commit_emoji_mismatch_fails(self) -> None:
        self.append("claude/CLAUDE.md", "\nExample: `\U0001f41b feat: add export`.\n")
        self.assert_fails_with("claude/CLAUDE.md", "commit emoji does not match type 'feat'")

    def test_missing_variation_selector_fails(self) -> None:
        recycle_without_selector = chr(0x267B)
        self.append("claude/CLAUDE.md", f"\nExample: `{recycle_without_selector} refactor: extract parser`.\n")
        self.assert_fails_with("commit emoji does not match type 'refactor'")

    def test_incomplete_commit_table_fails(self) -> None:
        path = self.root / "claude/rules/git-workflow.md"
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
            "claude/agents/dup-key.md",
            f"---\nname: dup-key\ndescription: Synthetic.\n{github_value}: a\n{github_value}: b\n---\n",
        )
        self.write("claude/agents/bad-tag.md", f"---\nname: bad-tag\ndescription: !{github_value} value\n---\n")
        self.append("README.md", f"\nSee [missing](docs/{github_value}.md).\n")
        # A function body must be a compound command, so bash reports the word as the unexpected token.
        self.write("scripts/broken-token.sh", f"#!/usr/bin/env bash\nf() {github_value}\n")

        output = self.assert_fails_with()
        self.assert_reported(output, "claude/agents/dup-key.md", "duplicate frontmatter key")
        self.assert_reported(output, "claude/agents/bad-tag.md", "invalid YAML frontmatter")
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
            "claude/skills/verify-changes/SKILL.md",
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


if __name__ == "__main__":
    unittest.main()
