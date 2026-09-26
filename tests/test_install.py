"""Tests for scripts/install.sh and scripts/uninstall.sh.

Each test copies the toolkit into a temporary directory whose path contains spaces and runs
the real scripts in subprocesses with a temporary HOME. The developer's HOME is never used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MANAGED = {
    ".claude/CLAUDE.md": "claude/CLAUDE.md",
    ".claude/rules/engineering-principles.md": "claude/rules/engineering-principles.md",
    ".claude/rules/testing.md": "claude/rules/testing.md",
    ".claude/rules/git-workflow.md": "claude/rules/git-workflow.md",
    ".claude/agents/senior-reviewer.md": "claude/agents/senior-reviewer.md",
    ".claude/skills/work-session": "claude/skills/work-session",
    ".claude/skills/verify-changes": "claude/skills/verify-changes",
    ".codex/skills/supervise-claude": "codex/skills/supervise-claude",
}

SIBLINGS = {
    ".claude/settings.json": '{"synthetic": true}\n',
    ".claude/rules/project-extra.md": "# extra rule\n",
    ".claude/skills/other-skill/SKILL.md": "---\nname: other-skill\n---\n",
    ".claude/agents/other-agent.md": "# other agent\n",
    ".codex/config.toml": "synthetic = true\n",
    ".codex/skills/other/SKILL.md": "# other codex skill\n",
}


def snapshot(root: Path) -> dict:
    """Maps every path under root to its type and content or link target, never following links."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                result[rel] = ("dir",)
            else:
                result[rel] = ("file", path.read_bytes())
    return result


class InstallerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo with spaces"
        ignore = shutil.ignore_patterns("__pycache__")
        for name in ("scripts", "claude", "codex"):
            shutil.copytree(REPO_ROOT / name, self.repo / name, symlinks=True, ignore=ignore)
        self.repo_real = self.repo.resolve()
        self.home = self.tmp / "home dir"
        self.home.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)}
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / name), *args],
            env=env,
            cwd=self.tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )

    def write_home(self, rel: str, text: str) -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_siblings(self) -> None:
        for rel, text in SIBLINGS.items():
            self.write_home(rel, text)

    def assert_siblings_intact(self) -> None:
        for rel, text in SIBLINGS.items():
            path = self.home / rel
            self.assertFalse(path.is_symlink(), rel)
            self.assertEqual(path.read_text(), text, rel)

    def assert_all_links(self) -> None:
        for destination, source in MANAGED.items():
            path = self.home / destination
            self.assertTrue(path.is_symlink(), destination)
            self.assertEqual(os.readlink(path), str(self.repo_real / source), destination)

    def backup_dirs(self) -> list[Path]:
        backups = self.home / ".ifdev-ai-tools" / "backups"
        return sorted(backups.iterdir()) if backups.is_dir() else []

    def assert_ok(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def install(self) -> subprocess.CompletedProcess:
        result = self.run_script("install.sh")
        self.assert_ok(result)
        return result


class InstallTests(InstallerTestCase):
    def test_dry_run_changes_nothing(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        before = snapshot(self.home)

        result = self.run_script("install.sh", "--dry-run")

        self.assert_ok(result)
        self.assertIn("would back up ~/.claude/CLAUDE.md", result.stdout)
        self.assertIn("would link: ~/.codex/skills/supervise-claude", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_fresh_install_links_all_managed_items(self) -> None:
        result = self.install()

        self.assert_all_links()
        self.assertEqual(self.backup_dirs(), [])
        self.assertFalse((self.home / ".ifdev-ai-tools" / "lock").exists())
        self.assertIn("8 linked, 0 backed up, 0 unchanged", result.stdout)
        # The link resolves to real content, not only to the right path string.
        self.assertTrue((self.home / ".claude/skills/verify-changes/SKILL.md").is_file())

    def test_existing_file_directory_and_symlinks_are_backed_up(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        self.write_home(".claude/skills/work-session/SKILL.md", "custom skill\n")
        foreign_target = self.outside / "foreign.md"
        foreign_target.write_text("foreign\n")
        (self.home / ".claude/rules").mkdir(parents=True, exist_ok=True)
        os.symlink(foreign_target, self.home / ".claude/rules/testing.md")
        (self.home / ".claude/agents").mkdir(parents=True, exist_ok=True)
        missing = self.outside / "missing.md"
        os.symlink(missing, self.home / ".claude/agents/senior-reviewer.md")

        result = self.install()

        self.assert_all_links()
        self.assert_siblings_intact()
        self.assertIn("4 backed up", result.stdout)
        backups = self.backup_dirs()
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertRegex(backup.name, r"^\d{8}-\d{6}-")
        self.assertEqual((backup / ".claude/CLAUDE.md").read_text(), "user instructions\n")
        self.assertEqual((backup / ".claude/skills/work-session/SKILL.md").read_text(), "custom skill\n")
        self.assertEqual(os.readlink(backup / ".claude/rules/testing.md"), str(foreign_target))
        self.assertEqual(os.readlink(backup / ".claude/agents/senior-reviewer.md"), str(missing))
        self.assertEqual(foreign_target.read_text(), "foreign\n")
        manifest = (backup / "MANIFEST").read_text().splitlines()
        self.assertEqual(
            sorted(manifest),
            sorted([
                ".claude/CLAUDE.md",
                ".claude/rules/testing.md",
                ".claude/agents/senior-reviewer.md",
                ".claude/skills/work-session",
            ]),
        )

    def test_second_install_is_idempotent(self) -> None:
        self.write_siblings()
        self.install()
        before = snapshot(self.home)

        result = self.install()

        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn("0 linked, 0 backed up, 8 unchanged", result.stdout)

    def test_symlinked_parent_directory_is_rejected_without_changes(self) -> None:
        (self.home / ".claude").mkdir()
        os.symlink(self.outside, self.home / ".claude/rules")
        before = snapshot(self.home)

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked parent directory", result.stderr)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_symlinked_state_directory_is_rejected_without_changes(self) -> None:
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        os.symlink(self.outside, self.home / ".ifdev-ai-tools")
        before = snapshot(self.home)

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_missing_source_aborts_before_any_change(self) -> None:
        (self.repo / "claude/rules/testing.md").unlink()

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("managed source is missing", result.stderr)
        self.assertEqual(snapshot(self.home), {})

    def test_held_lock_blocks_concurrent_run(self) -> None:
        (self.home / ".ifdev-ai-tools/lock").mkdir(parents=True)

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lock", result.stderr)
        self.assertFalse((self.home / ".claude").exists())
        self.assertTrue((self.home / ".ifdev-ai-tools/lock").is_dir())

    def test_unknown_argument_is_rejected(self) -> None:
        for script in ("install.sh", "uninstall.sh"):
            result = self.run_script(script, "--force")
            self.assertEqual(result.returncode, 2, script)
            self.assertIn("unknown argument", result.stderr)
        self.assertEqual(snapshot(self.home), {})


class UninstallTests(InstallerTestCase):
    def test_uninstall_removes_only_own_links(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        self.install()
        foreign_target = self.outside / "foreign.md"
        foreign_target.write_text("foreign\n")
        foreign_link = self.home / ".claude/rules/git-workflow.md"
        foreign_link.unlink()
        os.symlink(foreign_target, foreign_link)
        real_file = self.home / ".claude/rules/testing.md"
        real_file.unlink()
        real_file.write_text("local copy\n")
        backups_before = snapshot(self.home / ".ifdev-ai-tools/backups")

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        for destination in MANAGED:
            if destination not in (".claude/rules/git-workflow.md", ".claude/rules/testing.md"):
                path = self.home / destination
                self.assertFalse(path.exists() or path.is_symlink(), destination)
        self.assertEqual(os.readlink(foreign_link), str(foreign_target))
        self.assertEqual(real_file.read_text(), "local copy\n")
        self.assert_siblings_intact()
        for parent in (".claude/rules", ".claude/agents", ".claude/skills", ".codex/skills"):
            self.assertTrue((self.home / parent).is_dir(), parent)
        self.assertEqual(snapshot(self.home / ".ifdev-ai-tools/backups"), backups_before)
        self.assertIn("backups kept", result.stdout)
        self.assertIn(str(self.backup_dirs()[0]), result.stdout)

    def test_uninstall_dry_run_changes_nothing(self) -> None:
        self.write_siblings()
        self.install()
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh", "--dry-run")

        self.assert_ok(result)
        self.assertIn("would remove: ~/.claude/CLAUDE.md", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_uninstall_ignores_links_to_another_checkout(self) -> None:
        other_checkout = self.outside / "claude/CLAUDE.md"
        other_checkout.parent.mkdir(parents=True)
        other_checkout.write_text("other\n")
        (self.home / ".claude").mkdir()
        os.symlink(other_checkout, self.home / ".claude/CLAUDE.md")
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertEqual(snapshot(self.home), before)

    def test_uninstall_refuses_symlinked_parent(self) -> None:
        # A parent symlink could make a matching link outside HOME look managed.
        (self.outside / "skills").mkdir()
        os.symlink(self.repo_real / "codex/skills/supervise-claude", self.outside / "skills/supervise-claude")
        (self.home / ".codex").mkdir()
        os.symlink(self.outside / "skills", self.home / ".codex/skills")
        before_outside = snapshot(self.outside)

        result = self.run_script("uninstall.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked parent directory", result.stderr)
        self.assertEqual(snapshot(self.outside), before_outside)

    def link_state_directory_outside(self) -> str:
        outside_backup = "20000101-000000-outside"
        (self.outside / "backups" / outside_backup).mkdir(parents=True)
        os.symlink(self.outside, self.home / ".ifdev-ai-tools")
        return outside_backup

    def assert_refused_without_traversal(self, outside_backup: str) -> None:
        before_home = snapshot(self.home)
        before_outside = snapshot(self.outside)

        result = self.run_script("uninstall.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked parent directory", result.stderr)
        self.assertNotIn(outside_backup, result.stdout + result.stderr)
        self.assertEqual(snapshot(self.home), before_home)
        self.assertEqual(snapshot(self.outside), before_outside)

    def test_uninstall_noop_refuses_symlinked_state_directory(self) -> None:
        # Nothing installed: the backup listing alone must not traverse the symlink.
        self.assert_refused_without_traversal(self.link_state_directory_outside())

    def test_uninstall_refuses_symlinked_state_directory_before_removing_links(self) -> None:
        self.install()
        shutil.rmtree(self.home / ".ifdev-ai-tools")
        outside_backup = self.link_state_directory_outside()

        self.assert_refused_without_traversal(outside_backup)
        self.assert_all_links()


if __name__ == "__main__":
    unittest.main()
