"""Acceptance tests for the codex target (decision record 0004, slice C), written from its acceptance
criteria: the installed Codex agents, backups kept through uninstall, and upgrades of machines that
installed the claude target before the codex target existed.

Installs run the real scripts with a temporary HOME passed only to the subprocesses. The upgrade
tests clone this repository into a temporary directory, install with an older commit's own
install.sh and then move the clone to the committed HEAD, the way `git pull` would; they skip when
the older commit is not available (shallow clones). The developer's HOME is never used.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

from test_codex_target import CODEX, CODEX_MANAGED, ROLES, SHARED_ADAPTER, CodexTestCase
from test_install import MANAGED, PRE_0004_COMMIT, SETTINGS_JSON, TARGETS_STATE, snapshot

REPO_ROOT = Path(__file__).resolve().parent.parent
# The last commit before the codex target (slices A and B): the claude target with recorded targets.
PRE_CODEX_COMMIT = "2ae9a21"
HOOK_COMMAND = "bash ~/.claude/hooks/agent-notify-hook.sh"
READ_ONLY_NOTICE = "This role is read-only, and Codex does not enforce it"
# Destinations only the codex target owns: a claude-only machine must never get them.
CODEX_ONLY = sorted(rel for rel in CODEX_MANAGED if rel != SHARED_ADAPTER)


def core_agent(role: str) -> tuple[bool, str]:
    """The neutral agent's read_only flag and its body below the frontmatter."""
    text = (REPO_ROOT / "core/agents" / f"{role}.md").read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---\n", 2)
    read_only = re.search(r"^read_only:\s*(true|false)\s*$", frontmatter, re.MULTILINE)
    assert read_only, role
    return read_only.group(1) == "true", body.strip("\n")


class InstalledCodexAgentTests(CodexTestCase):
    """Every installed ~/.codex/agents/<role>.toml is a valid custom agent carrying its role."""

    def setUp(self) -> None:
        super().setUp()
        if tomllib is None:
            self.skipTest("tomllib needs Python 3.11+")
        self.install(*CODEX)

    def test_every_installed_agent_parses_and_carries_its_role_body_and_footer(self) -> None:
        footer = (self.repo / "targets/codex/agent-footer.md").read_text(encoding="utf-8").strip()
        for role in ROLES:
            with self.subTest(role=role):
                agent = tomllib.loads((self.home / ".codex/agents" / f"{role}.toml").read_text(encoding="utf-8"))
                _, body = core_agent(role)

                self.assertEqual(set(agent), {"name", "description", "developer_instructions"})
                self.assertEqual(agent["name"], role)
                self.assertTrue(agent["description"].strip())
                self.assertTrue(agent["developer_instructions"].startswith(body), role)
                self.assertTrue(agent["developer_instructions"].rstrip("\n").endswith(footer), role)

    def test_only_read_only_roles_carry_the_read_only_notice(self) -> None:
        read_only_roles = []
        for role in ROLES:
            with self.subTest(role=role):
                read_only, _ = core_agent(role)
                agent = tomllib.loads((self.home / ".codex/agents" / f"{role}.toml").read_text(encoding="utf-8"))
                instructions = agent["developer_instructions"]

                if read_only:
                    read_only_roles.append(role)
                    self.assertEqual(instructions.count(READ_ONLY_NOTICE), 1, role)
                else:
                    self.assertNotIn(READ_ONLY_NOTICE, instructions, role)
        # The reviewers are read-only and the writers are not: the split is not vacuous.
        self.assertIn("reviewer", read_only_roles)
        self.assertNotIn("dev", read_only_roles)
        self.assertNotIn("qa", read_only_roles)


class CodexBackupTests(CodexTestCase):
    def test_uninstalling_codex_leaves_the_backed_up_global_instructions_intact(self) -> None:
        self.write_home(".codex/AGENTS.md", "user instructions\n")
        self.install(*CODEX)
        [backup] = self.backup_dirs()

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        # The link is removed and the user's file is not restored on its own: it stays in the backup.
        self.assert_absent(self.home / ".codex/AGENTS.md")
        self.assertEqual((backup / ".codex/AGENTS.md").read_text(), "user instructions\n")
        self.assertEqual((backup / "MANIFEST").read_text(), ".codex/AGENTS.md\n")
        self.assertIn("backups kept", result.stdout)
        self.assertIn(str(backup), result.stdout)

    def test_user_agent_and_skill_at_managed_paths_are_backed_up_and_survive_uninstall(self) -> None:
        self.write_home(".codex/agents/reviewer.toml", 'name = "my-reviewer"\n')
        self.write_home(".agents/skills/web-dev/SKILL.md", "# my web-dev\n")

        result = self.install(*CODEX)

        self.assertIn("2 backed up", result.stdout)
        [backup] = self.backup_dirs()
        self.assertEqual((backup / ".codex/agents/reviewer.toml").read_text(), 'name = "my-reviewer"\n')
        self.assertEqual((backup / ".agents/skills/web-dev/SKILL.md").read_text(), "# my web-dev\n")
        self.assertEqual(os.readlink(self.home / ".agents/skills/web-dev"), str(self.repo_real / "core/skills/web-dev"))

        self.assert_ok(self.run_script("uninstall.sh"))

        self.assertEqual(self.links(), {})
        self.assertEqual((backup / ".codex/agents/reviewer.toml").read_text(), 'name = "my-reviewer"\n')
        self.assertEqual((backup / ".agents/skills/web-dev/SKILL.md").read_text(), "# my web-dev\n")

    def test_claude_and_codex_user_files_survive_co_install_and_uninstall_of_both(self) -> None:
        self.write_siblings()
        user_files = {
            ".codex/agents/mine.toml": 'name = "mine"\n',
            ".agents/skills/mine/SKILL.md": "# mine\n",
            ".codex/AGENTS.override.md": "override\n",
        }
        for rel, text in user_files.items():
            self.write_home(rel, text)

        self.install("--target", "all")
        self.assert_ok(self.run_script("uninstall.sh", "--target", "claude"))
        self.assert_ok(self.run_script("uninstall.sh", *CODEX))

        self.assertEqual(self.links(), {})
        self.assert_siblings_intact()
        for rel, text in user_files.items():
            self.assertEqual((self.home / rel).read_text(), text, rel)


class ClaudeTargetUnchangedTests(CodexTestCase):
    def test_claude_target_installs_exactly_its_33_destinations_and_no_codex_item(self) -> None:
        self.assertEqual(len(MANAGED), 33)

        result = self.install("--target", "claude")

        self.assertEqual(self.links(), self.expected_links(MANAGED))
        self.assertIn("33 linked", result.stdout)
        for rel in CODEX_ONLY:
            self.assert_absent(self.home / rel)
        self.assert_absent(self.home / ".agents")
        self.assertEqual(self.state(), "claude\n")


class PriorInstallUpgradeTests(unittest.TestCase):
    """A machine that installed the claude target with an older commit, then pulled this one."""

    def setUp(self) -> None:
        self.head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-codex-upgrade-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "checkout with spaces"
        self.git(self.tmp, "clone", "--quiet", "--no-checkout", str(REPO_ROOT), str(self.repo))
        self.repo_real = self.repo.resolve()
        self.home = self.tmp / "home dir"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / SETTINGS_JSON).write_text('{"synthetic": true}\n')

    @staticmethod
    def git(cwd: Path, *args: str) -> None:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)}
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

    def install_at_and_pull(self, commit: str, *args: str) -> None:
        if subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{commit}^{{commit}}"]).returncode:
            self.skipTest(f"commit {commit} is not available in this clone")
        self.git(self.repo, "checkout", "--quiet", "--detach", commit)
        self.assert_ok(self.run_script("install.sh", *args))
        self.git(self.repo, "checkout", "--quiet", "--detach", self.head)

    def links_to_checkout(self) -> dict[str, str]:
        return {
            rel: value[1]
            for rel, value in snapshot(self.home).items()
            if value[0] == "link" and value[1].startswith(str(self.repo_real))
        }

    def state(self) -> str:
        return (self.home / TARGETS_STATE).read_text()

    def assert_claude_installed(self) -> None:
        for destination, source in MANAGED.items():
            self.assertEqual(os.readlink(self.home / destination), str(self.repo_real / source), destination)
        self.assertIn(HOOK_COMMAND, (self.home / SETTINGS_JSON).read_text())

    def assert_no_codex_only_items(self) -> None:
        for rel in CODEX_ONLY:
            path = self.home / rel
            self.assertFalse(path.exists() or path.is_symlink(), rel)

    def test_bare_install_after_pulling_onto_a_pre_0004_install_stays_claude_only(self) -> None:
        self.install_at_and_pull(PRE_0004_COMMIT)

        self.assert_ok(self.run_script("install.sh"))

        self.assert_claude_installed()
        self.assert_no_codex_only_items()
        self.assertEqual(self.state(), "claude\n")

    def test_bare_install_after_pulling_onto_a_recorded_claude_install_changes_nothing(self) -> None:
        self.install_at_and_pull(PRE_CODEX_COMMIT, "--target", "claude")
        before = snapshot(self.home)

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertIn(f"0 linked, 0 backed up, {len(MANAGED)} unchanged", result.stdout)
        self.assertEqual(
            {rel: value for rel, value in snapshot(self.home).items() if not rel.startswith(".ifdev-ai-tools/backups")},
            {rel: value for rel, value in before.items() if not rel.startswith(".ifdev-ai-tools/backups")},
        )
        self.assert_no_codex_only_items()
        self.assertEqual(self.state(), "claude\n")

    def test_adding_codex_to_a_recorded_claude_install_records_both_and_keeps_claude(self) -> None:
        self.install_at_and_pull(PRE_CODEX_COMMIT, "--target", "claude")
        settings = (self.home / SETTINGS_JSON).read_text()

        result = self.run_script("install.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.state(), "claude\ncodex\n")
        self.assert_claude_installed()
        self.assertEqual((self.home / SETTINGS_JSON).read_text(), settings)
        for destination, source in CODEX_MANAGED.items():
            self.assertEqual(os.readlink(self.home / destination), str(self.repo_real / source), destination)

        # A bare rerun keeps both; removing codex later leaves claude working.
        self.assert_ok(self.run_script("install.sh"))
        self.assertEqual(self.state(), "claude\ncodex\n")
        self.assert_ok(self.run_script("uninstall.sh", *CODEX))
        self.assertEqual(self.state(), "claude\n")
        self.assert_claude_installed()
        self.assert_no_codex_only_items()

    def test_adding_codex_to_a_pre_0004_install_records_both_and_keeps_claude(self) -> None:
        """A machine installed before targets were recorded is a claude install: adding codex
        must record claude too, or a later bare uninstall drops the adapter claude still owns."""
        self.install_at_and_pull(PRE_0004_COMMIT)

        self.assert_ok(self.run_script("install.sh", *CODEX))

        self.assertEqual(self.state(), "claude\ncodex\n")
        self.assert_ok(self.run_script("uninstall.sh", *CODEX))
        self.assertTrue((self.home / SHARED_ADAPTER).is_symlink(), "the claude-owned notify adapter was removed")
        self.assertTrue(self.links_to_checkout())


if __name__ == "__main__":
    unittest.main()
