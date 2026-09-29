"""Tests for scripts/lib/claude_hooks.py: the ~/.claude/settings.json hook merge."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hooks = load("claude_hooks", "scripts/lib/claude_hooks.py")

OUR_EVENTS = ("Stop", "StopFailure", "Notification")


def expected_group(event: str) -> dict:
    """The exact group hooks.run("install", ...) creates for `event`, mirroring
    hooks._group_payload without depending on that private helper directly."""
    payload = {}
    if event in hooks.HOOK_MATCHERS:
        payload["matcher"] = hooks.HOOK_MATCHERS[event]
    payload["hooks"] = [{"type": "command", "command": hooks.HOOK_COMMANDS[event]}]
    return payload


class ClaudeHooksTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-hooks-test-"))
        self.addCleanup(self._cleanup)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.settings = self.home / ".claude" / "settings.json"

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_settings(self, data) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            self.settings.write_text(data, encoding="utf-8")
        else:
            self.settings.write_text(json.dumps(data), encoding="utf-8")

    def read_settings(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))


class InstallTests(ClaudeHooksTestCase):
    def test_creates_settings_file_with_the_three_managed_hooks(self) -> None:
        lines = hooks.run("install", self.home, dry_run=False)

        self.assertTrue(any("Stop" in line for line in lines))
        data = self.read_settings()
        for event in OUR_EVENTS:
            groups = data["hooks"][event]
            self.assertEqual(groups, [expected_group(event)])

    def test_dry_run_changes_nothing(self) -> None:
        lines = hooks.run("install", self.home, dry_run=True)

        self.assertTrue(all(line.startswith("would add") for line in lines))
        self.assertFalse(self.settings.exists())

    def test_second_install_is_idempotent(self) -> None:
        hooks.run("install", self.home, dry_run=False)
        before = self.settings.read_text(encoding="utf-8")

        lines = hooks.run("install", self.home, dry_run=False)

        self.assertEqual(len(lines), 1)
        self.assertIn("unchanged", lines[0])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)
        self.assertFalse((self.home / ".ifdev-ai-tools" / "backups").exists())

    def test_preserves_unrelated_settings_and_other_hook_commands(self) -> None:
        self.write_settings(
            {
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}],
                },
            }
        )

        hooks.run("install", self.home, dry_run=False)

        data = self.read_settings()
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(data["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}])
        stop_commands = [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]]
        self.assertIn("echo mine", stop_commands)
        self.assertIn(hooks.HOOK_COMMANDS["Stop"], stop_commands)
        self.assertEqual(data["hooks"]["Notification"], [expected_group("Notification")])

    def test_backs_up_settings_before_modifying_an_existing_file(self) -> None:
        self.write_settings({"permissions": {"allow": ["Bash(ls)"]}})

        lines = hooks.run("install", self.home, dry_run=False)

        self.assertTrue(any("backed up" in line for line in lines))
        backups = list((self.home / ".ifdev-ai-tools" / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        backup_settings = json.loads((backups[0] / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(backup_settings, {"permissions": {"allow": ["Bash(ls)"]}})
        self.assertEqual((backups[0] / "MANIFEST").read_text(encoding="utf-8").strip(), ".claude/settings.json")

    def test_refuses_invalid_json_without_changing_anything(self) -> None:
        self.write_settings("{ not json")

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{ not json")

    def test_refuses_non_object_json_without_changing_anything(self) -> None:
        self.write_settings([1, 2, 3])

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)

    def test_refuses_symlinked_settings_file(self) -> None:
        real = self.tmp / "outside.json"
        real.write_text("{}", encoding="utf-8")
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, self.settings)

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)
        self.assertTrue(self.settings.is_symlink())

    def test_refuses_symlinked_claude_directory(self) -> None:
        outside = self.tmp / "outside-claude"
        outside.mkdir()
        os.symlink(outside, self.home / ".claude")

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)


class UninstallTests(ClaudeHooksTestCase):
    def test_noop_when_nothing_installed(self) -> None:
        lines = hooks.run("uninstall", self.home, dry_run=False)

        self.assertEqual(len(lines), 1)
        self.assertIn("unchanged", lines[0])
        self.assertFalse(self.settings.exists())

    def test_removes_exactly_the_managed_entries(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        lines = hooks.run("uninstall", self.home, dry_run=False)

        self.assertTrue(any("remove" in line for line in lines))
        data = self.read_settings()
        self.assertNotIn("hooks", data)

    def test_dry_run_uninstall_changes_nothing(self) -> None:
        hooks.run("install", self.home, dry_run=False)
        before = self.settings.read_text(encoding="utf-8")

        lines = hooks.run("uninstall", self.home, dry_run=True)

        self.assertTrue(all(line.startswith("would remove") for line in lines))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)

    def test_preserves_unrelated_settings_and_manual_additions_to_the_same_event(self) -> None:
        hooks.run("install", self.home, dry_run=False)
        data = self.read_settings()
        data["permissions"] = {"allow": ["Bash(ls)"]}
        data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo mine"}]})
        data["hooks"]["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}]
        self.write_settings(data)

        hooks.run("uninstall", self.home, dry_run=False)

        data = self.read_settings()
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(data["hooks"]["PreToolUse"], [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo guard"}]}])
        stop_commands = [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]]
        self.assertEqual(stop_commands, ["echo mine"])
        self.assertNotIn("Notification", data["hooks"])
        self.assertNotIn("StopFailure", data["hooks"])

    def test_backs_up_settings_before_removing(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        lines = hooks.run("uninstall", self.home, dry_run=False)

        self.assertTrue(any("backed up" in line for line in lines))

    def test_never_deletes_the_settings_file_itself(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        hooks.run("uninstall", self.home, dry_run=False)

        self.assertTrue(self.settings.exists())
        self.assertEqual(self.read_settings(), {})

    def test_removes_a_legacy_notification_group_installed_without_a_matcher(self) -> None:
        # A machine that installed before HOOK_MATCHERS existed has no "matcher" key
        # on its Notification group at all: uninstall must still recognize and remove it.
        self.write_settings({"hooks": {"Notification": [{"hooks": [{"type": "command", "command": hooks.HOOK_COMMANDS["Notification"]}]}]}})

        hooks.run("uninstall", self.home, dry_run=False)

        self.assertNotIn("hooks", self.read_settings())


class NotificationMatcherTests(ClaudeHooksTestCase):
    def test_notification_group_carries_a_matcher_for_the_documented_attention_types(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        group = self.read_settings()["hooks"]["Notification"][0]

        self.assertEqual(group["matcher"], hooks.HOOK_MATCHERS["Notification"])
        for value in hooks.ATTENTION_NOTIFICATION_TYPES:
            self.assertIn(value, group["matcher"])

    def test_stop_and_stop_failure_groups_carry_no_matcher(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        data = self.read_settings()

        for event in ("Stop", "StopFailure"):
            self.assertNotIn("matcher", data["hooks"][event][0])

    def test_does_not_remove_a_foreign_group_with_the_same_command_but_a_different_matcher(self) -> None:
        self.write_settings(
            {
                "hooks": {
                    "Notification": [
                        {"matcher": "something_else", "hooks": [{"type": "command", "command": hooks.HOOK_COMMANDS["Notification"]}]},
                    ]
                }
            }
        )

        hooks.run("uninstall", self.home, dry_run=False)

        data = self.read_settings()
        self.assertEqual(data["hooks"]["Notification"], [{"matcher": "something_else", "hooks": []}])


class ManagedHooksShapeValidationTests(ClaudeHooksTestCase):
    def test_refuses_non_object_top_level_hooks(self) -> None:
        self.write_settings({"hooks": ["not", "an", "object"]})

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)
        self.assertEqual(self.read_settings(), {"hooks": ["not", "an", "object"]})

    def test_refuses_non_array_managed_event(self) -> None:
        self.write_settings({"hooks": {"Stop": "not-a-list"}})

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)

    def test_refuses_non_object_group_in_a_managed_event(self) -> None:
        self.write_settings({"hooks": {"Stop": ["not-an-object"]}})

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)

    def test_refuses_group_whose_hooks_member_is_not_an_array(self) -> None:
        self.write_settings({"hooks": {"Notification": [{"hooks": "not-a-list"}]}})

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=False)

    def test_ignores_malformed_shape_in_an_unmanaged_event(self) -> None:
        # Only the three managed events are ever written into; an unrelated event's
        # shape is none of this module's business and must never be refused.
        self.write_settings({"hooks": {"PreToolUse": "anything at all"}})

        lines = hooks.run("install", self.home, dry_run=False)

        self.assertTrue(any("Stop" in line for line in lines))

    def test_dry_run_also_refuses_malformed_shape_before_any_change(self) -> None:
        self.write_settings({"hooks": {"StopFailure": [{"hooks": "not-a-list"}]}})

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("install", self.home, dry_run=True)
        self.assertFalse((self.home / ".ifdev-ai-tools").exists())

    def test_uninstall_also_refuses_malformed_shape_before_removing_anything(self) -> None:
        hooks.run("install", self.home, dry_run=False)
        data = self.read_settings()
        data["hooks"]["Stop"] = "not-a-list"
        self.write_settings(data)
        before = self.settings.read_text(encoding="utf-8")

        with self.assertRaises(hooks.HookInstallError):
            hooks.run("uninstall", self.home, dry_run=False)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)


class AtomicWriteTests(ClaudeHooksTestCase):
    def test_leaves_no_temporary_file_behind_after_a_successful_write(self) -> None:
        hooks.run("install", self.home, dry_run=False)

        leftovers = [p for p in self.settings.parent.iterdir() if p.name != "settings.json"]
        self.assertEqual(leftovers, [])

    def test_preserves_existing_file_permissions(self) -> None:
        self.write_settings({"permissions": {"allow": ["Bash(ls)"]}})
        self.settings.chmod(0o600)

        hooks.run("install", self.home, dry_run=False)

        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)

    def test_cleans_up_the_temporary_file_when_the_replace_step_fails(self) -> None:
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                hooks.run("install", self.home, dry_run=False)

        self.assertEqual(list(self.settings.parent.iterdir()), [])
        self.assertFalse(self.settings.exists())


if __name__ == "__main__":
    unittest.main()
