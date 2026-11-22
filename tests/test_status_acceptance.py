"""Acceptance tests for scripts/status.sh (issue #42), written from the acceptance criteria.

They complement tests/test_status.py with cross-script scenarios: the shared notify adapter,
uninstalling one target, a legacy machine without a targets record, a dangling link to this
checkout's own source, a held installer lock and symlinked parents. Like the installer tests,
they run the real scripts in subprocesses with a temporary HOME.
"""

from __future__ import annotations

import os
import shutil
import unittest

from test_install import MANAGED, TARGETS_STATE
from test_status import ALL, ALL_ITEMS, CLAUDE_ITEMS, ITEM_LINE, StatusTestCase

SHARED_ADAPTER = ".codex/notify/agent-notify-codex.sh"


class StatusAcceptanceTests(StatusTestCase):
    def item_lines(self, stdout: str) -> list[tuple[str, str]]:
        return [(m.group(1), m.group(2)) for m in map(ITEM_LINE.match, stdout.splitlines()) if m]

    def test_shared_notify_adapter_is_listed_once_when_both_targets_are_installed(self) -> None:
        self.install(*ALL)

        result = self.status_unchanged()

        self.assert_ok(result)
        lines = self.item_lines(result.stdout)
        self.assertEqual(len(lines), ALL_ITEMS, result.stdout)
        self.assertEqual([rel for _, rel in lines].count(SHARED_ADAPTER), 1, result.stdout)

    def test_uninstalling_one_target_leaves_the_other_reported_ok(self) -> None:
        self.install(*ALL)
        self.assert_ok(self.run_script("uninstall.sh", "--target", "codex"))

        result = self.status_unchanged()

        self.assert_ok(result)
        self.assertIn("targets: claude", result.stdout)
        items = self.items(result)
        self.assertEqual(len(items), CLAUDE_ITEMS)
        self.assertEqual(items[SHARED_ADAPTER], "ok")
        self.assertNotIn(".codex/AGENTS.md", items)

        codex = self.status_unchanged("--target", "codex")
        self.assertEqual(codex.returncode, 1, codex.stdout + codex.stderr)
        codex_items = self.items(codex)
        self.assertEqual(codex_items[SHARED_ADAPTER], "ok")
        self.assertEqual(codex_items[".codex/AGENTS.md"], "missing")
        self.assertEqual(codex_items[".codex/agents/dev.toml"], "missing")

    def test_uninstalling_every_target_reports_nothing_installed(self) -> None:
        self.install(*ALL)
        self.assert_ok(self.run_script("uninstall.sh", *ALL))

        result = self.status_unchanged()

        self.assert_ok(result)
        self.assertIn("targets: none installed from this checkout", result.stdout)
        self.assertEqual(self.items(result), {})

    def test_legacy_machine_without_targets_record_is_reported_as_claude(self) -> None:
        self.install()
        (self.home / TARGETS_STATE).unlink()

        result = self.status_unchanged()

        self.assert_ok(result)
        self.assertRegex(result.stdout, r"(?m)^targets: claude$")
        self.assertEqual(len(self.items(result)), CLAUDE_ITEMS)
        self.assertFalse((self.home / TARGETS_STATE).exists())

    def test_dangling_link_to_this_checkouts_own_source_is_broken(self) -> None:
        self.install()
        source = self.repo / MANAGED[".claude/agents/qa.md"]
        source.unlink()

        self.assert_problem(self.status_unchanged(), ".claude/agents/qa.md", "broken")

    def test_status_does_not_wait_for_or_take_the_installer_lock(self) -> None:
        self.install()
        lock = self.home / ".ifdev-ai-tools/lock"
        lock.mkdir()

        result = self.status_unchanged()

        self.assert_ok(result)
        self.assertTrue(lock.is_dir())

    def test_symlinked_claude_directory_is_only_reported(self) -> None:
        self.install()
        claude = self.home / ".claude"
        moved = self.outside / "claude"
        shutil.move(str(claude), str(moved))
        os.symlink(moved, claude)

        result = self.status_unchanged()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        items = self.items(result)
        for rel in MANAGED:
            if rel.startswith(".claude/"):
                self.assertEqual(items[rel], "unsafe-parent", rel)
        self.assertIn("  unsafe: ", result.stdout)

    def test_symlinked_settings_file_reports_unsafe_hooks_without_changes(self) -> None:
        self.install()
        settings = self.home / ".claude/settings.json"
        target = self.outside / "settings.json"
        shutil.move(str(settings), str(target))
        os.symlink(target, settings)

        result = self.status_unchanged()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("  unsafe: ", result.stdout)
        self.assertEqual(set(self.items(result).values()), {"ok"})

    def test_requesting_a_target_that_is_not_installed_reports_its_items_missing(self) -> None:
        self.install()

        result = self.status_unchanged("--target", "codex")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        items = self.items(result)
        self.assertIn("targets: codex (recorded: claude)", result.stdout)
        self.assertEqual(items[SHARED_ADAPTER], "ok")
        self.assertEqual(
            {rel: state for rel, state in items.items() if rel != SHARED_ADAPTER and state != "missing"}, {}
        )


if __name__ == "__main__":
    unittest.main()
