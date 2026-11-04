"""Acceptance tests for upgrading a real pre-0004 install with `git pull` (decision record 0004, slice A).

A temporary clone of this repository is checked out at the last commit before the neutral layout,
installed with that commit's own install.sh into a temporary HOME, then moved to the current commit
the way `git pull` would. The developer's HOME is never used. The tests exercise the committed
HEAD, so uncommitted changes are not part of the upgrade; they skip when the pre-0004 commit is
not available (shallow clones).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_install import MANAGED, MANAGED_COUNT, MOVED_COUNT, PRE_0004_COMMIT, SETTINGS_JSON, TARGETS_STATE, snapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ".ifdev-ai-tools/backups"
HOOK_COMMAND = "bash ~/.claude/hooks/agent-notify-hook.sh"


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def read_through(path: Path) -> dict:
    """Content seen through an installed link: file bytes, or every file below a directory."""
    if path.is_dir():
        return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}
    return {"": path.read_bytes()}


def read_only_agents(core_agents: Path) -> list[str]:
    return sorted(
        p.name for p in core_agents.glob("*.md") if re.search(r"^read_only:\s*true\s*$", p.read_text(), re.MULTILINE)
    )


class GitPullUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{PRE_0004_COMMIT}^{{commit}}"]).returncode:
            raise unittest.SkipTest(f"commit {PRE_0004_COMMIT} is not available in this clone")
        cls.head = git(REPO_ROOT, "rev-parse", "HEAD")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-upgrade-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "checkout with spaces"
        git(self.tmp, "clone", "--quiet", "--no-checkout", str(REPO_ROOT), str(self.repo))
        git(self.repo, "checkout", "--quiet", "--detach", PRE_0004_COMMIT)
        self.repo_real = self.repo.resolve()
        self.home = self.make_home("home dir")

    def make_home(self, name: str) -> Path:
        home = self.tmp / name
        home.mkdir()
        (home / ".claude").mkdir()
        (home / SETTINGS_JSON).write_text('{"synthetic": true}\n')
        return home

    def run_script(self, name: str, *args: str, home: Path | None = None) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home or self.home)}
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / name), *args],
            env=env,
            cwd=self.tmp,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_ok(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def install_pre_0004_and_pull(self) -> None:
        self.assert_ok(self.run_script("install.sh"))
        git(self.repo, "checkout", "--quiet", "--detach", self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    @staticmethod
    def without_backups(home: Path) -> dict:
        """The HOME snapshot without the backups that the settings.json hook merge always takes."""
        return {rel: value for rel, value in snapshot(home).items() if not rel.startswith(BACKUPS)}

    @staticmethod
    def backups(home: Path) -> dict:
        return {rel: value for rel, value in snapshot(home).items() if rel.startswith(BACKUPS)}

    def test_after_git_pull_every_installed_link_resolves_to_the_current_content(self) -> None:
        self.install_pre_0004_and_pull()

        for destination, source in MANAGED.items():
            with self.subTest(destination=destination):
                installed = self.home / destination
                self.assertTrue(installed.is_symlink())
                self.assertTrue(installed.exists(), f"dangling after pull: {os.readlink(installed)}")
                self.assertEqual(read_through(installed), read_through(self.repo / source))
        # The Claude hook command kept in settings.json still finds its script.
        self.assertIn(HOOK_COMMAND, (self.home / SETTINGS_JSON).read_text())
        self.assertTrue((self.home / ".claude/hooks/agent-notify-hook.sh").is_file())

    def test_after_git_pull_read_only_agents_still_block_write_and_edit(self) -> None:
        self.install_pre_0004_and_pull()

        names = read_only_agents(self.repo / "core/agents")
        self.assertIn("reviewer.md", names)
        for name in names:
            with self.subTest(agent=name):
                text = (self.home / ".claude/agents" / name).read_text()
                frontmatter = text.split("---", 2)[1]
                disallowed = re.search(r"^disallowedTools:\s*(.+)$", frontmatter, re.MULTILINE)
                self.assertIsNotNone(disallowed, frontmatter)
                self.assertTrue({"Write", "Edit"} <= {t.strip() for t in disallowed.group(1).split(",")})

    def test_dry_run_after_git_pull_changes_nothing(self) -> None:
        self.install_pre_0004_and_pull()
        before = snapshot(self.home)

        result = self.run_script("install.sh", "--dry-run")

        self.assert_ok(result)
        self.assertEqual(result.stdout.count("would relink: "), MOVED_COUNT)
        self.assertIn("would record targets: claude", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_bare_install_after_git_pull_relinks_without_backups_like_a_fresh_claude_install(self) -> None:
        self.install_pre_0004_and_pull()
        backups_before = self.backups(self.home)

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertIn(f"{MOVED_COUNT} linked, 0 copied, 0 backed up, {MANAGED_COUNT - MOVED_COUNT} unchanged", result.stdout)
        self.assertEqual(self.backups(self.home), backups_before)
        fresh_home = self.make_home("fresh home")
        self.assert_ok(self.run_script("install.sh", "--target", "claude", home=fresh_home))
        self.assertEqual(self.without_backups(self.home), self.without_backups(fresh_home))
        for destination, source in MANAGED.items():
            self.assertEqual(os.readlink(self.home / destination), str(self.repo_real / source), destination)

        rerun = self.run_script("install.sh")
        self.assert_ok(rerun)
        self.assertIn(f"0 linked, 0 copied, 0 backed up, {MANAGED_COUNT} unchanged", rerun.stdout)

    def test_uninstall_after_upgrade_leaves_nothing_managed(self) -> None:
        self.install_pre_0004_and_pull()
        self.assert_ok(self.run_script("install.sh"))

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertIn(f"done: {MANAGED_COUNT} removed", result.stdout)
        self.assert_nothing_managed()

    def test_uninstall_right_after_git_pull_leaves_nothing_managed(self) -> None:
        self.install_pre_0004_and_pull()

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertIn(f"done: {MANAGED_COUNT} removed", result.stdout)
        self.assert_nothing_managed()

    def assert_nothing_managed(self) -> None:
        for destination in MANAGED:
            path = self.home / destination
            self.assertFalse(path.exists() or path.is_symlink(), destination)
        self.assertFalse((self.home / TARGETS_STATE).exists())
        self.assertNotIn(HOOK_COMMAND, (self.home / SETTINGS_JSON).read_text())
        links_to_checkout = [
            rel for rel, value in snapshot(self.home).items() if value[0] == "link" and str(self.repo_real) in value[1]
        ]
        self.assertEqual(links_to_checkout, [])


if __name__ == "__main__":
    unittest.main()
