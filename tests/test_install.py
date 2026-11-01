"""Tests for scripts/install.sh and scripts/uninstall.sh.

Each test copies the toolkit into a temporary directory whose path contains spaces and runs
the real scripts in subprocesses with a temporary HOME. The developer's HOME is never used.
"""

from __future__ import annotations

import json
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
    ".claude/agents/po.md": "claude/agents/po.md",
    ".claude/agents/analyst.md": "claude/agents/analyst.md",
    ".claude/agents/architect.md": "claude/agents/architect.md",
    ".claude/agents/ux.md": "claude/agents/ux.md",
    ".claude/agents/devops.md": "claude/agents/devops.md",
    ".claude/agents/tech-writer.md": "claude/agents/tech-writer.md",
    ".claude/agents/debugger.md": "claude/agents/debugger.md",
    ".claude/agents/metrics-analyst.md": "claude/agents/metrics-analyst.md",
    ".claude/agents/api-reviewer.md": "claude/agents/api-reviewer.md",
    ".claude/agents/reviewer.md": "claude/agents/reviewer.md",
    ".claude/agents/ux-reviewer.md": "claude/agents/ux-reviewer.md",
    ".claude/agents/security-reviewer.md": "claude/agents/security-reviewer.md",
    ".claude/agents/privacy-reviewer.md": "claude/agents/privacy-reviewer.md",
    ".claude/agents/db-reviewer.md": "claude/agents/db-reviewer.md",
    ".claude/agents/performance-reviewer.md": "claude/agents/performance-reviewer.md",
    ".claude/agents/dev.md": "claude/agents/dev.md",
    ".claude/agents/qa.md": "claude/agents/qa.md",
    ".claude/skills/work-session": "claude/skills/work-session",
    ".claude/skills/verify-changes": "claude/skills/verify-changes",
    ".claude/skills/flutter-dev": "claude/skills/flutter-dev",
    ".claude/skills/web-dev": "claude/skills/web-dev",
    ".claude/skills/supabase-dev": "claude/skills/supabase-dev",
    ".claude/skills/safe-refactor": "claude/skills/safe-refactor",
    ".claude/skills/java-spring-dev": "claude/skills/java-spring-dev",
    ".claude/skills/e2e-testing": "claude/skills/e2e-testing",
    ".claude/hooks/agent-notify.sh": "scripts/agent-notify.sh",
    ".claude/hooks/agent-notify-hook.sh": "claude/hooks/agent-notify-hook.sh",
    ".codex/notify/agent-notify-codex.sh": "codex/notify/agent-notify-codex.sh",
}
MANAGED_COUNT = len(MANAGED)
# Formerly managed destination and its old source; the source no longer exists in the checkout.
RETIRED = ".claude/agents/senior-reviewer.md"
RETIRED_SOURCE = "claude/agents/senior-reviewer.md"
# Retired Codex supervision items: a skill directory link and a profile file link.
RETIRED_SUPERVISION = {
    ".codex/skills/supervise-claude": "codex/skills/supervise-claude",
    ".codex/ifdev-autonomous.config.toml": "codex/profiles/ifdev-autonomous.config.toml",
}

# Not a managed symlink: install.sh/uninstall.sh merge the toolkit's notification
# hooks into this file in place instead (see scripts/lib/claude_hooks.py), so it is
# asserted separately from the untouched SIBLINGS below.
SETTINGS_JSON = ".claude/settings.json"
SETTINGS_JSON_CONTENT = '{"synthetic": true}\n'

SIBLINGS = {
    ".claude/rules/project-extra.md": "# extra rule\n",
    ".claude/skills/other-skill/SKILL.md": "---\nname: other-skill\n---\n",
    ".claude/agents/other-agent.md": "# other agent\n",
    ".codex/config.toml": (
        'approval_policy = "on-request"\n'
        'sandbox_mode = "read-only"\n'
        "[sandbox_workspace_write]\n"
        "network_access = false\n"
    ),
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

    def link_retired_to_checkout(self) -> Path:
        """Simulates a machine that installed the toolkit before the item was retired."""
        path = self.home / RETIRED
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(self.repo_real / RETIRED_SOURCE, path)
        return path

    def run_with_retired_link_swapped(self, foreign: Path, script: str) -> subprocess.CompletedProcess:
        """Runs <script> with a readlink wrapper that, on the third read of the retired path
        (after the initial plan and the re-plan under the lock), replaces that link with one
        to <foreign>: a concurrent change between planning and removal."""
        real_readlink = shutil.which("readlink")
        self.assertIsNotNone(real_readlink)
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        counter = self.tmp / "retired-readlink.count"
        wrapper = fake_bin / "readlink"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'last="${*: -1}"\n'
            f'if [[ "$last" == */{RETIRED} ]]; then\n'
            "  count=0\n"
            f'  [[ -f "{counter}" ]] && count=$(cat "{counter}")\n'
            "  count=$((count + 1))\n"
            f'  printf \'%s\\n\' "$count" > "{counter}"\n'
            '  if [[ "$count" -eq 3 ]]; then\n'
            '    rm -- "$last"\n'
            f'    ln -s -- "{foreign}" "$last"\n'
            "  fi\n"
            "fi\n"
            f'exec "{real_readlink}" "$@"\n'
        )
        wrapper.chmod(0o700)
        env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.home)}
        result = subprocess.run(
            ["bash", str(self.repo / "scripts" / script)],
            env=env,
            cwd=self.tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(int(counter.read_text().strip()), 3, result.stdout + result.stderr)
        return result

    def assert_absent(self, path: Path) -> None:
        self.assertFalse(path.exists() or path.is_symlink(), path)


class InstallTests(InstallerTestCase):
    def test_dry_run_changes_nothing(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        self.write_home(SETTINGS_JSON, SETTINGS_JSON_CONTENT)
        before = snapshot(self.home)

        result = self.run_script("install.sh", "--dry-run")

        self.assert_ok(result)
        self.assertIn("would back up ~/.claude/CLAUDE.md", result.stdout)
        self.assertIn("would link: ~/.codex/notify/agent-notify-codex.sh", result.stdout)
        self.assertIn("would add: ~/.claude/settings.json Stop hook", result.stdout)
        self.assertIn("would add: ~/.claude/settings.json StopFailure hook", result.stdout)
        self.assertIn("would add: ~/.claude/settings.json Notification hook", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_fresh_install_links_all_managed_items(self) -> None:
        result = self.install()

        self.assert_all_links()
        self.assertEqual(self.backup_dirs(), [])
        self.assertFalse((self.home / ".ifdev-ai-tools" / "lock").exists())
        self.assertIn(f"{MANAGED_COUNT} linked, 0 backed up, 0 unchanged", result.stdout)
        # The link resolves to real content, not only to the right path string.
        self.assertTrue((self.home / ".claude/skills/verify-changes/SKILL.md").is_file())
        # Notification hooks are merged into a freshly created settings.json, not linked.
        self.assertFalse((self.home / SETTINGS_JSON).is_symlink())
        settings = json.loads((self.home / SETTINGS_JSON).read_text())
        for event in ("Stop", "StopFailure", "Notification"):
            self.assertIn(event, settings["hooks"])

    def test_skill_references_are_reachable_through_installed_links(self) -> None:
        self.install()

        for skill_link in (".claude/skills/verify-changes", ".claude/skills/work-session"):
            source = self.repo / MANAGED[skill_link]
            references = sorted(p.name for p in (source / "references").glob("*.md"))
            self.assertTrue(references, skill_link)
            installed = self.home / skill_link / "references"
            self.assertFalse(installed.is_symlink(), skill_link)  # rides on the skill directory link
            for name in references:
                path = installed / name
                self.assertEqual(path.read_bytes(), (source / "references" / name).read_bytes(), name)
            skill_text = (self.home / skill_link / "SKILL.md").read_text()
            for name in references:
                self.assertIn(f"references/{name}", skill_text, f"{skill_link} does not link {name}")

    def test_base_codex_config_and_codex_skills_are_untouched(self) -> None:
        self.write_siblings()
        base_config = self.home / ".codex/config.toml"
        before = base_config.read_bytes()

        self.install()

        self.assertFalse(base_config.is_symlink())
        self.assertEqual(base_config.read_bytes(), before)
        # Only the opt-in notify adapter is managed below ~/.codex; the user's skills stay.
        self.assertEqual(sorted(p.name for p in (self.home / ".codex").iterdir()), ["config.toml", "notify", "skills"])
        self.assertEqual(sorted(p.name for p in (self.home / ".codex/skills").iterdir()), ["other"])

        self.assert_ok(self.run_script("uninstall.sh"))
        self.assertEqual(base_config.read_bytes(), before)
        self.assert_siblings_intact()

    def test_existing_file_directory_and_symlinks_are_backed_up(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        self.write_home(".claude/skills/work-session/SKILL.md", "custom skill\n")
        self.write_home(SETTINGS_JSON, SETTINGS_JSON_CONTENT)
        foreign_target = self.outside / "foreign.md"
        foreign_target.write_text("foreign\n")
        (self.home / ".claude/rules").mkdir(parents=True, exist_ok=True)
        os.symlink(foreign_target, self.home / ".claude/rules/testing.md")
        (self.home / ".claude/agents").mkdir(parents=True, exist_ok=True)
        missing = self.outside / "missing.md"
        os.symlink(missing, self.home / ".claude/agents/reviewer.md")

        result = self.install()

        self.assert_all_links()
        self.assert_siblings_intact()
        self.assertIn("4 backed up", result.stdout)
        # Settings.json is merged in place (not backed up alongside the managed items);
        # scripts/lib/claude_hooks.py backs it up separately into its own "-hooks" dir.
        backups = self.backup_dirs()
        self.assertEqual(len(backups), 2)
        hooks_backup = next(b for b in backups if b.name.endswith("-hooks"))
        settings = json.loads((hooks_backup / ".claude/settings.json").read_text())
        self.assertEqual(settings, {"synthetic": True})
        installed_settings = json.loads((self.home / SETTINGS_JSON).read_text())
        self.assertEqual(installed_settings["synthetic"], True)
        for event in ("Stop", "StopFailure", "Notification"):
            self.assertIn(event, installed_settings["hooks"])
        backup = next(b for b in backups if not b.name.endswith("-hooks"))
        self.assertRegex(backup.name, r"^\d{8}-\d{6}-")
        self.assertEqual((backup / ".claude/CLAUDE.md").read_text(), "user instructions\n")
        self.assertEqual((backup / ".claude/skills/work-session/SKILL.md").read_text(), "custom skill\n")
        self.assertEqual(os.readlink(backup / ".claude/rules/testing.md"), str(foreign_target))
        self.assertEqual(os.readlink(backup / ".claude/agents/reviewer.md"), str(missing))
        self.assertEqual(foreign_target.read_text(), "foreign\n")
        manifest = (backup / "MANIFEST").read_text().splitlines()
        self.assertEqual(
            sorted(manifest),
            sorted([
                ".claude/CLAUDE.md",
                ".claude/rules/testing.md",
                ".claude/agents/reviewer.md",
                ".claude/skills/work-session",
            ]),
        )

    def test_second_install_is_idempotent(self) -> None:
        self.write_siblings()
        self.write_home(SETTINGS_JSON, SETTINGS_JSON_CONTENT)
        self.install()
        before = snapshot(self.home)
        backups_before = self.backup_dirs()

        result = self.install()

        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.backup_dirs(), backups_before)
        self.assertIn(f"0 linked, 0 backed up, {MANAGED_COUNT} unchanged", result.stdout)
        self.assertIn("unchanged: ~/.claude/settings.json notification hooks already configured", result.stdout)

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

    def test_invalid_settings_json_aborts_before_any_symlink_is_created(self) -> None:
        self.write_home(SETTINGS_JSON, "{ not json")

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)
        # The hooks preflight runs before apply_install: no managed item was linked.
        for destination in MANAGED:
            self.assertFalse((self.home / destination).exists(), destination)
        self.assertEqual((self.home / SETTINGS_JSON).read_text(), "{ not json")

    def test_symlinked_settings_json_aborts_before_any_symlink_is_created(self) -> None:
        real = self.outside / "settings.json"
        real.write_text("{}", encoding="utf-8")
        (self.home / ".claude").mkdir()
        os.symlink(real, self.home / SETTINGS_JSON)

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked path", result.stderr)
        for destination in MANAGED:
            self.assertFalse((self.home / destination).exists(), destination)

    def test_malformed_managed_hooks_shape_aborts_before_any_symlink_is_created(self) -> None:
        self.write_home(SETTINGS_JSON, json.dumps({"hooks": {"Stop": "not-a-list"}}))

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"hooks.Stop"', result.stderr)
        for destination in MANAGED:
            self.assertFalse((self.home / destination).exists(), destination)


class RetiredItemInstallTests(InstallerTestCase):
    def test_install_removes_retired_link_to_this_checkout(self) -> None:
        retired = self.link_retired_to_checkout()

        result = self.install()

        self.assert_absent(retired)
        self.assert_all_links()
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn(f"removed retired link: ~/{RETIRED}", result.stdout)
        self.assertIn(f"{MANAGED_COUNT} linked, 0 backed up, 0 unchanged, 1 retired removed", result.stdout)

    def test_install_keeps_retired_path_that_is_not_a_link_to_this_checkout(self) -> None:
        other_checkout = self.outside / RETIRED_SOURCE
        retired = self.home / RETIRED
        # Resolves to this checkout's old source but is not the exact literal target.
        relative_to_checkout = os.path.relpath(self.repo_real / RETIRED_SOURCE, retired.parent)
        cases = {
            "user file": lambda path: path.write_text("my own reviewer\n"),
            "directory": lambda path: (path.mkdir(), (path / "notes.md").write_text("mine\n")),
            "foreign link": lambda path: os.symlink(other_checkout, path),
            "relative link to this checkout": lambda path: os.symlink(relative_to_checkout, path),
        }
        for label, create in cases.items():
            with self.subTest(label):
                if retired.is_symlink() or retired.is_file():
                    retired.unlink()
                elif retired.is_dir():
                    shutil.rmtree(retired)
                retired.parent.mkdir(parents=True, exist_ok=True)
                create(retired)
                before = {rel: value for rel, value in snapshot(self.home).items() if rel.startswith(RETIRED)}

                result = self.install()

                after = {rel: value for rel, value in snapshot(self.home).items() if rel.startswith(RETIRED)}
                self.assertEqual(after, before)
                self.assertIn(f"kept retired path (not a link to this repository): ~/{RETIRED}", result.stdout)
                self.assertIn("0 retired removed", result.stdout)
                self.assertNotIn("removed retired link", result.stdout)
                self.assert_all_links()

    def test_install_dry_run_reports_retired_link_without_changes(self) -> None:
        self.link_retired_to_checkout()
        before = snapshot(self.home)

        result = self.run_script("install.sh", "--dry-run")

        self.assert_ok(result)
        self.assertIn(f"would remove retired link: ~/{RETIRED}", result.stdout)
        # Same order as a real run: the retired removal precedes the managed links.
        self.assertLess(result.stdout.index("would remove retired link"), result.stdout.index("would link:"))
        self.assertEqual(snapshot(self.home), before)

    def test_second_install_after_retired_removal_has_no_actions(self) -> None:
        self.link_retired_to_checkout()
        self.install()
        before = snapshot(self.home)

        result = self.install()

        self.assertEqual(snapshot(self.home), before)
        self.assertIn(f"0 linked, 0 backed up, {MANAGED_COUNT} unchanged, 0 retired removed", result.stdout)
        self.assertNotIn(RETIRED, result.stdout)

    def test_install_stops_when_retired_link_changes_before_removal(self) -> None:
        retired = self.link_retired_to_checkout()
        foreign = self.outside / "foreign.md"

        result = self.run_with_retired_link_swapped(foreign, "install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed while removing retired links", result.stderr)
        self.assertEqual(os.readlink(retired), str(foreign))
        for destination in MANAGED:
            self.assert_absent(self.home / destination)
        self.assertFalse((self.home / ".ifdev-ai-tools/lock").exists())

    def test_install_and_uninstall_accept_an_empty_retired_list(self) -> None:
        common = self.repo / "scripts/lib/common.sh"
        content = common.read_text()
        start = content.index("RETIRED_ITEMS=(")
        end = content.index("\n)\n", start) + 3
        common.write_text(content[:start] + "RETIRED_ITEMS=()\n" + content[end:])

        result = self.install()

        self.assertIn("0 retired removed", result.stdout)
        uninstall = self.run_script("uninstall.sh")
        self.assert_ok(uninstall)
        self.assertIn(f"done: {MANAGED_COUNT} removed, 0 retired removed", uninstall.stdout)

    def test_install_refuses_symlinked_parent_of_retired_link(self) -> None:
        # The retired destination shares ~/.claude/agents; a symlinked parent could make a
        # link outside HOME look like this checkout's.
        (self.outside / "agents").mkdir()
        os.symlink(self.repo_real / RETIRED_SOURCE, self.outside / "agents/senior-reviewer.md")
        (self.home / ".claude").mkdir()
        os.symlink(self.outside / "agents", self.home / ".claude/agents")
        before_outside = snapshot(self.outside)

        result = self.run_script("install.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing symlinked parent directory", result.stderr)
        self.assertEqual(snapshot(self.outside), before_outside)


class RetiredItemUninstallTests(InstallerTestCase):
    def test_uninstall_removes_retired_link_to_this_checkout(self) -> None:
        # Nothing else installed: the retired link alone must be enough to run the removal.
        retired = self.link_retired_to_checkout()

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assert_absent(retired)
        self.assertTrue((self.home / ".claude/agents").is_dir())
        self.assertIn(f"removed retired link: ~/{RETIRED}", result.stdout)
        self.assertIn("done: 0 removed, 1 retired removed", result.stdout)
        self.assertFalse((self.home / ".ifdev-ai-tools/lock").exists())

    def test_uninstall_removes_retired_and_managed_links_together(self) -> None:
        self.install()
        retired = self.link_retired_to_checkout()

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assert_absent(retired)
        for destination in MANAGED:
            self.assert_absent(self.home / destination)
        self.assertIn(f"done: {MANAGED_COUNT} removed, 1 retired removed", result.stdout)

    def test_uninstall_dry_run_reports_retired_link_without_changes(self) -> None:
        self.link_retired_to_checkout()
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh", "--dry-run")

        self.assert_ok(result)
        self.assertIn(f"would remove retired link: ~/{RETIRED}", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_uninstall_keeps_retired_path_that_is_not_a_link_to_this_checkout(self) -> None:
        self.write_home(RETIRED, "my own reviewer\n")
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertEqual(snapshot(self.home), before)
        self.assertIn(f"kept retired path (not a link to this repository): ~/{RETIRED}", result.stdout)
        self.assertIn("0 retired removed", result.stdout)

    def test_uninstall_stops_when_retired_link_changes_before_removal(self) -> None:
        retired = self.link_retired_to_checkout()
        foreign = self.outside / "foreign.md"

        result = self.run_with_retired_link_swapped(foreign, "uninstall.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed while removing retired links", result.stderr)
        self.assertEqual(os.readlink(retired), str(foreign))
        self.assertFalse((self.home / ".ifdev-ai-tools/lock").exists())

    def test_second_uninstall_has_no_retired_actions(self) -> None:
        self.link_retired_to_checkout()
        self.assert_ok(self.run_script("uninstall.sh"))
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertEqual(snapshot(self.home), before)
        self.assertIn("done: 0 removed, 0 retired removed", result.stdout)
        self.assertNotIn(RETIRED, result.stdout)


class RetiredSupervisionTests(InstallerTestCase):
    """Machines that installed the Codex supervision keep a skill directory link and a profile
    file link. After the pull the tracked sources are gone, but Git leaves ignored files such
    as `__pycache__/` behind, so the old skill directory may still exist."""

    LEFTOVER = "codex/skills/supervise-claude/scripts/__pycache__/run_claude.cpython-312.pyc"

    def setUp(self) -> None:
        super().setUp()
        for source in RETIRED_SUPERVISION.values():
            path = self.repo / source
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()

    def leave_ignored_cache(self) -> Path:
        leftover = self.repo / self.LEFTOVER
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(b"synthetic cache")
        return leftover

    def link_supervision_to_checkout(self) -> None:
        self.write_siblings()
        for destination, source in RETIRED_SUPERVISION.items():
            path = self.home / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.repo_real / source, path)

    def assert_supervision_removed(self, result: subprocess.CompletedProcess) -> None:
        for destination in RETIRED_SUPERVISION:
            self.assert_absent(self.home / destination)
            self.assertIn(f"removed retired link: ~/{destination}", result.stdout)
        # Only the links go: the parent directory and the user's other Codex skills stay.
        self.assertTrue((self.home / ".codex/skills").is_dir())
        self.assert_siblings_intact()

    def test_install_removes_dangling_supervision_links(self) -> None:
        self.link_supervision_to_checkout()

        result = self.install()

        self.assert_supervision_removed(result)
        self.assert_all_links()
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn(f"{MANAGED_COUNT} linked, 0 backed up, 0 unchanged, 2 retired removed", result.stdout)

    def test_install_removes_directory_link_without_touching_the_leftover_directory(self) -> None:
        leftover = self.leave_ignored_cache()
        self.link_supervision_to_checkout()
        self.assertTrue((self.home / ".codex/skills/supervise-claude").is_dir())  # still resolves via the leftover

        result = self.install()

        self.assert_supervision_removed(result)
        self.assertEqual(leftover.read_bytes(), b"synthetic cache")
        self.assertEqual(self.backup_dirs(), [])
        self.assertIn("2 retired removed", result.stdout)

    def test_uninstall_removes_supervision_links_with_the_managed_links(self) -> None:
        leftover = self.leave_ignored_cache()
        self.install()
        self.link_supervision_to_checkout()

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assert_supervision_removed(result)
        self.assertEqual(leftover.read_bytes(), b"synthetic cache")
        self.assertIn(f"done: {MANAGED_COUNT} removed, 2 retired removed", result.stdout)

    def test_dry_runs_report_supervision_links_without_changes(self) -> None:
        self.link_supervision_to_checkout()
        before = snapshot(self.home)

        for script in ("install.sh", "uninstall.sh"):
            with self.subTest(script):
                result = self.run_script(script, "--dry-run")

                self.assert_ok(result)
                for destination in RETIRED_SUPERVISION:
                    self.assertIn(f"would remove retired link: ~/{destination}", result.stdout)
                self.assertEqual(snapshot(self.home), before)

    def test_foreign_supervision_paths_are_kept(self) -> None:
        self.write_siblings()
        own_skill = self.write_home(".codex/skills/supervise-claude/SKILL.md", "my own skill\n")
        self.write_home(".codex/ifdev-autonomous.config.toml", 'sandbox_mode = "read-only"\n')
        other_checkout = self.outside / "codex/skills/supervise-claude"
        other_checkout.mkdir(parents=True)
        cases = {
            "user directory and profile file": None,
            "links to another checkout": lambda: (
                shutil.rmtree(own_skill.parent),
                os.symlink(other_checkout, self.home / ".codex/skills/supervise-claude"),
            ),
        }
        for label, change in cases.items():
            with self.subTest(label):
                if change:
                    change()
                before = self.supervision_snapshot()
                self.assertTrue(before)

                for script in ("install.sh", "uninstall.sh"):
                    result = self.run_script(script)

                    self.assert_ok(result)
                    for destination in RETIRED_SUPERVISION:
                        self.assertIn(f"kept retired path (not a link to this repository): ~/{destination}", result.stdout)
                    self.assertIn("0 retired removed", result.stdout)
                self.assertEqual(self.supervision_snapshot(), before)

    def supervision_snapshot(self) -> dict:
        return {
            rel: value
            for rel, value in snapshot(self.home).items()
            if any(rel == d or rel.startswith(d + "/") for d in RETIRED_SUPERVISION)
        }


class UninstallTests(InstallerTestCase):
    def test_uninstall_removes_only_own_links(self) -> None:
        self.write_siblings()
        self.write_home(".claude/CLAUDE.md", "user instructions\n")
        self.write_home(SETTINGS_JSON, SETTINGS_JSON_CONTENT)
        self.install()
        # Simulates a manually added, unrelated hook on the same event this toolkit
        # manages: surgical uninstall must remove only its own entry.
        settings_path = self.home / SETTINGS_JSON
        settings = json.loads(settings_path.read_text())
        settings["hooks"]["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}]
        settings["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo mine"}]})
        settings_path.write_text(json.dumps(settings))
        foreign_target = self.outside / "foreign.md"
        foreign_target.write_text("foreign\n")
        foreign_link = self.home / ".claude/rules/git-workflow.md"
        foreign_link.unlink()
        os.symlink(foreign_target, foreign_link)
        real_file = self.home / ".claude/rules/testing.md"
        real_file.unlink()
        real_file.write_text("local copy\n")
        backups_root = self.home / ".ifdev-ai-tools/backups"
        backups_before = snapshot(backups_root)

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
        # Every backup that existed before uninstall (from the earlier install) survives
        # untouched; uninstall's own hooks backup of the pre-removal settings.json adds
        # exactly one new entry, it never overwrites an existing one.
        backups_after = snapshot(backups_root)
        for rel, value in backups_before.items():
            self.assertEqual(backups_after.get(rel), value, rel)
        new_entries = set(backups_after) - set(backups_before)
        self.assertTrue(any(rel.endswith(".claude/settings.json") for rel in new_entries), new_entries)
        self.assertIn("backups kept", result.stdout)
        self.assertIn(str(self.backup_dirs()[0]), result.stdout)
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["synthetic"], True)
        self.assertEqual(settings["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}])
        stop_commands = [entry["command"] for group in settings["hooks"]["Stop"] for entry in group["hooks"]]
        self.assertEqual(stop_commands, ["echo mine"])
        self.assertNotIn("StopFailure", settings["hooks"])
        self.assertNotIn("Notification", settings["hooks"])

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
        (self.outside / "notify").mkdir()
        os.symlink(self.repo_real / "codex/notify/agent-notify-codex.sh", self.outside / "notify/agent-notify-codex.sh")
        (self.home / ".codex").mkdir()
        os.symlink(self.outside / "notify", self.home / ".codex/notify")
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

    def test_uninstall_refuses_invalid_settings_json_before_removing_any_link(self) -> None:
        self.install()
        (self.home / SETTINGS_JSON).write_text("{ not json", encoding="utf-8")

        result = self.run_script("uninstall.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)
        self.assert_all_links()
        self.assertEqual((self.home / SETTINGS_JSON).read_text(), "{ not json")

    def test_uninstall_refuses_malformed_managed_hooks_shape_before_removing_any_link(self) -> None:
        self.install()
        settings_path = self.home / SETTINGS_JSON
        settings = json.loads(settings_path.read_text())
        settings["hooks"]["Notification"] = [{"hooks": "not-a-list"}]
        settings_path.write_text(json.dumps(settings))

        result = self.run_script("uninstall.sh")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('"hooks.Notification[0].hooks"', result.stderr)
        self.assert_all_links()

    def test_uninstall_reruns_hooks_preflight_under_the_lock_before_removing_any_link(self) -> None:
        # Mirrors install.sh's own re-plan/preflight under its lock: a second, later
        # settings.json change (simulated here by making the second preflight call
        # fail) must still abort uninstall.sh before any managed link is removed.
        self.install()
        real_python3 = shutil.which("python3")
        self.assertIsNotNone(real_python3)
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        counter = self.tmp / "hooks-preflight-calls.count"
        fake_python3 = fake_bin / "python3"
        fake_python3.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'COUNTER="{counter}"\n'
            "count=0\n"
            '[[ -f "$COUNTER" ]] && count=$(cat "$COUNTER")\n'
            "count=$((count + 1))\n"
            'printf \'%s\\n\' "$count" > "$COUNTER"\n'
            'if [[ "$1" == *claude_hooks.py && "${2:-}" == "uninstall" && "$count" -ge 2 ]]; then\n'
            '  echo "fake: simulated hooks preflight failure on second (locked) check" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec "{real_python3}" "$@"\n'
        )
        fake_python3.chmod(0o700)
        env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.home)}

        result = subprocess.run(
            ["bash", str(self.repo / "scripts" / "uninstall.sh")],
            env=env,
            cwd=self.tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("simulated hooks preflight failure", result.stderr)
        self.assertEqual(int(counter.read_text().strip()), 2)
        self.assert_all_links()


class InstalledHookExecutionTests(InstallerTestCase):
    """The installed hook script keeps its regular, non-executable tracked file mode
    (see claude/hooks/agent-notify-hook.sh); Claude Code must still be able to run it
    through the exact managed command string written into settings.json."""

    def test_installed_stop_hook_runs_via_the_managed_command_despite_default_file_mode(self) -> None:
        self.install()
        hook_path = self.home / ".claude/hooks/agent-notify-hook.sh"
        self.assertFalse(os.access(hook_path, os.X_OK), "hook file must not need its own execute bit")
        settings = json.loads((self.home / SETTINGS_JSON).read_text())
        command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith("bash "), command)

        # Only coreutils the hook adapter needs (cat/grep/sed), never notify-send/paplay:
        # this exercises the real installed hook end to end without any real notification.
        safe_bin = self.tmp / "safe-bin"
        safe_bin.mkdir()
        for tool in ("cat", "grep", "sed", "bash", "dirname"):
            os.symlink(shutil.which(tool), safe_bin / tool)
        env = {"PATH": str(safe_bin), "HOME": str(self.home)}
        result = subprocess.run(
            ["bash", "-c", command],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("\a", result.stdout, result.stderr)  # no desktop/sound backend in PATH: BEL fallback


if __name__ == "__main__":
    unittest.main()
