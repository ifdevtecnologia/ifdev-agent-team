"""Tests for scripts/status.sh, the read-only installation status command.

They reuse the installer fixture: the toolkit is copied into a temporary directory whose path
contains spaces and the real scripts run in subprocesses with a temporary HOME.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from test_install import LEGACY_SOURCES, MANAGED, REPO_ROOT, InstallerTestCase, snapshot

ALL = ("--target", "all")
CLAUDE_ITEMS = len(MANAGED)
# The codex target owns thirty items, one of them the notify adapter shared with claude.
ALL_ITEMS = CLAUDE_ITEMS + 29
ITEM_LINE = re.compile(r"^(\S+)\s+~/(\S+)$")


class StatusTestCase(InstallerTestCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copy2(REPO_ROOT / "CHANGELOG.md", self.repo / "CHANGELOG.md")

    def status(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script("status.sh", *args)

    def status_unchanged(self, *args: str) -> subprocess.CompletedProcess:
        """Runs status.sh and asserts that nothing below HOME, or outside it, changed."""
        before_home, before_outside = snapshot(self.home), snapshot(self.outside)
        result = self.status(*args)
        self.assertEqual(snapshot(self.home), before_home, result.stdout + result.stderr)
        self.assertEqual(snapshot(self.outside), before_outside)
        return result

    @staticmethod
    def items(result: subprocess.CompletedProcess) -> dict[str, str]:
        found = {}
        for line in result.stdout.splitlines():
            match = ITEM_LINE.match(line)
            if match:
                found[match.group(2)] = match.group(1)
        return found

    def assert_problem(self, result: subprocess.CompletedProcess, destination: str, state: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.items(result)[destination], state, result.stdout)
        others = {rel: s for rel, s in self.items(result).items() if rel != destination}
        self.assertEqual(set(others.values()), {"ok"}, result.stdout)


class VersionAndTargetTests(StatusTestCase):
    def test_nothing_installed_reports_version_and_exits_zero_without_changes(self) -> None:
        result = self.status_unchanged()

        self.assert_ok(result)
        self.assertRegex(result.stdout, r"^toolkit: \d+\.\d+\.\d+ \(")
        self.assertIn("targets: none installed from this checkout", result.stdout)
        self.assertFalse((self.home / ".ifdev-ai-tools").exists())

    def test_version_comes_from_the_latest_released_section_and_flags_unreleased_entries(self) -> None:
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Unreleased\n\n### Added\n- Something new.\n\n## 0.4.1 - 2027-01-02\n\n## 0.4.0 - 2027-01-01\n"
        )

        result = self.status()

        self.assertIn("toolkit: 0.4.1 (", result.stdout)
        self.assertIn("note: CHANGELOG.md lists unreleased changes newer than 0.4.1", result.stdout)

    def test_empty_unreleased_section_and_missing_changelog(self) -> None:
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 0.2.0 - 2026-11-12\n")
        self.assertNotIn("unreleased changes", self.status().stdout)

        (self.repo / "CHANGELOG.md").unlink()
        self.assertIn("toolkit: unknown version (", self.status().stdout)

    def test_git_checkout_is_described_without_touching_its_index(self) -> None:
        # The developer's own git configuration (signing, hooks, excludes) must not reach the fixture.
        git_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"}

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
                cwd=self.repo,
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        git("add", "-A")
        git("commit", "-q", "-m", "fixture")
        git("tag", "v9.9.9")
        commit = git("rev-parse", "--short", "HEAD")
        index = self.repo / ".git" / "index"
        tracked = self.repo / "CHANGELOG.md"
        # Stale stat data, certain even on coarse timestamps: a refreshing git command would rewrite the index.
        later = tracked.stat().st_mtime + 10
        os.utime(tracked, (later, later))
        before = (index.read_bytes(), index.stat().st_mtime_ns)

        result = self.status()
        self.assertIn(f"(v9.9.9, commit {commit})", result.stdout)
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)
        self.assertFalse((self.repo / ".git" / "index.lock").exists())

        with tracked.open("a") as handle:
            handle.write("\n")
        self.assertIn(f"(v9.9.9-dirty, commit {commit})", self.status().stdout)

    def test_invalid_usage_exits_two_without_changes(self) -> None:
        for args in (("--target", "cursor"), ("--target",), ("--bogus",)):
            with self.subTest(args=args):
                result = self.status_unchanged(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_symlinked_state_directory_is_reported_without_being_followed(self) -> None:
        os.symlink(self.outside, self.home / ".ifdev-ai-tools")

        result = self.status_unchanged()

        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing symlinked parent directory", result.stderr)


class InstalledStatusTests(StatusTestCase):
    def test_full_install_of_both_targets_is_all_ok(self) -> None:
        self.install(*ALL)

        result = self.status_unchanged()

        self.assert_ok(result)
        items = self.items(result)
        self.assertEqual(len(items), ALL_ITEMS)
        self.assertEqual(set(items.values()), {"ok"})
        for event in ("Stop", "StopFailure", "Notification"):
            self.assertIn(f"  present: {event}", result.stdout)
        self.assertIn("summary: everything is ok", result.stdout)

    def test_target_option_limits_the_report(self) -> None:
        self.install(*ALL)

        result = self.status("--target", "codex")

        self.assert_ok(result)
        items = self.items(result)
        self.assertEqual(len(items), ALL_ITEMS - CLAUDE_ITEMS + 1)
        self.assertNotIn(".claude/CLAUDE.md", items)
        self.assertNotIn("hooks (", result.stdout)

    def test_missing_item(self) -> None:
        self.install()
        (self.home / ".claude/agents/qa.md").unlink()

        self.assert_problem(self.status_unchanged(), ".claude/agents/qa.md", "missing")

    def test_foreign_file_and_foreign_link(self) -> None:
        self.install()
        rules = self.home / ".claude/rules/testing.md"
        rules.unlink()
        rules.write_text("# my own rule\n")
        self.assert_problem(self.status_unchanged(), ".claude/rules/testing.md", "foreign")

        rules.unlink()
        other = self.outside / "rule.md"
        other.write_text("# elsewhere\n")
        os.symlink(other, rules)
        self.assert_problem(self.status_unchanged(), ".claude/rules/testing.md", "foreign")

    def test_dangling_link_is_broken(self) -> None:
        self.install()
        skill = self.home / ".claude/skills/web-dev"
        skill.unlink()
        os.symlink(self.outside / "gone", skill)

        self.assert_problem(self.status_unchanged(), ".claude/skills/web-dev", "broken")

    def test_link_to_a_moved_source_is_outdated(self) -> None:
        self.install()
        destination = self.home / ".claude/CLAUDE.md"
        destination.unlink()
        os.symlink(self.repo_real / LEGACY_SOURCES[".claude/CLAUDE.md"], destination)

        self.assert_problem(self.status_unchanged(), ".claude/CLAUDE.md", "outdated")

    def test_codex_agent_copy_states(self) -> None:
        self.install("--target", "codex")
        copy = self.home / ".codex/agents/dev.toml"
        source = self.repo / "dist/codex/agents/dev.toml"

        with copy.open("a") as handle:
            handle.write("# local edit\n")
        self.assert_problem(self.status_unchanged(), ".codex/agents/dev.toml", "modified")

        self.install("--target", "codex")
        with source.open("a") as handle:
            handle.write("# newer toolkit version\n")
        self.assert_problem(self.status_unchanged(), ".codex/agents/dev.toml", "outdated")

        copy.unlink()
        os.symlink(source.resolve(), copy)
        self.assert_problem(self.status_unchanged(), ".codex/agents/dev.toml", "outdated")

    def test_symlinked_parent_is_reported_without_being_followed(self) -> None:
        self.install()
        rules = self.home / ".claude/rules"
        shutil.rmtree(rules)
        (self.outside / "testing.md").write_text("# outside\n")
        os.symlink(self.outside, rules)

        result = self.status_unchanged()

        self.assertEqual(result.returncode, 1)
        items = self.items(result)
        for name in ("engineering-principles", "testing", "git-workflow"):
            self.assertEqual(items[f".claude/rules/{name}.md"], "unsafe-parent")

    def test_missing_and_unsafe_hooks(self) -> None:
        self.install()
        settings = self.home / ".claude/settings.json"
        settings.write_text('{"hooks": {}}\n')

        result = self.status_unchanged()
        self.assertEqual(result.returncode, 1)
        self.assertIn("  missing: Stop", result.stdout)
        self.assertIn("summary: 3 item(s) need attention", result.stdout)

        settings.write_text("{not json")
        result = self.status_unchanged()
        self.assertEqual(result.returncode, 1)
        self.assertIn("  unsafe: ", result.stdout)

    def test_codex_override_file_is_noted(self) -> None:
        self.install("--target", "codex")
        self.write_home(".codex/AGENTS.override.md", "# override\n")

        result = self.status_unchanged()

        self.assertEqual(result.returncode, 1)
        self.assertIn("note: ~/.codex/AGENTS.override.md exists", result.stdout)


if __name__ == "__main__":
    unittest.main()
