"""Tests for the Claude/Codex hook adapters: claude/hooks/agent-notify-hook.sh and
codex/notify/agent-notify-codex.sh. Both are exercised against a fake agent-notify.sh
placed in PATH so no real desktop/audio backend is ever reached; the fake logs its
argv, which is exactly the surface these adapters must translate correctly.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_HOOK = REPO_ROOT / "claude" / "hooks" / "agent-notify-hook.sh"
CODEX_ADAPTER = REPO_ROOT / "codex" / "notify" / "agent-notify-codex.sh"
BASH = shutil.which("bash") or "/bin/bash"

FAKE_NOTIFY = """#!/bin/sh
{
  for arg in "$@"; do
    printf '%s\\n' "$arg"
  done
  printf '===END===\\n'
} >> "$LOG"
"""


class HookAdapterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-hook-adapter-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.log = self.tmp / "log.txt"
        self.log.write_text("", encoding="utf-8")
        # claude/hooks/agent-notify-hook.sh resolves the notifier next to itself, so the
        # fake must live in the same directory under this test's isolated HOME.
        self.claude_hooks_dir = self.tmp / "home" / ".claude" / "hooks"
        self.claude_hooks_dir.mkdir(parents=True)
        fake = self.claude_hooks_dir / "agent-notify.sh"
        fake.write_text(FAKE_NOTIFY, encoding="utf-8")
        fake.chmod(0o700)
        hook = self.claude_hooks_dir / "agent-notify-hook.sh"
        hook.write_text(CLAUDE_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
        hook.chmod(0o700)
        self.hook_script = hook
        self.home = self.tmp / "home"

    def calls(self) -> list:
        text = self.log.read_text(encoding="utf-8")
        chunks = [chunk for chunk in text.split("===END===\n") if chunk.strip()]
        return [chunk.splitlines() for chunk in chunks]

    def run_hook(self, kind: str, stdin_text: str = "", extra_env=None) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "LOG": str(self.log), "HOME": str(self.home)}
        env.update(extra_env or {})
        return subprocess.run(
            [BASH, str(self.hook_script), kind],
            input=stdin_text,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )


class ClaudeHookAdapterTests(HookAdapterTestCase):
    def test_stop_success_notifies_complete_when_not_supervised(self) -> None:
        result = self.run_hook("stop-success")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "complete", "Claude Code finished responding"])

    def test_stop_success_is_suppressed_when_supervised(self) -> None:
        result = self.run_hook("stop-success", extra_env={"IFDEV_SUPERVISED_CLAUDE": "1"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_stop_failure_always_notifies_blocked_even_when_supervised(self) -> None:
        result = self.run_hook("stop-failure", extra_env={"IFDEV_SUPERVISED_CLAUDE": "1"})

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "blocked", "Claude Code stopped with an error"])

    def test_notification_extracts_the_message_field(self) -> None:
        payload = '{"session_id": "abc", "message": "Claude needs your permission to use Bash", "hook_event_name": "Notification"}'

        result = self.run_hook("notification", stdin_text=payload)

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "attention", "Claude needs your permission to use Bash"])

    def test_notification_falls_back_to_a_default_message_on_malformed_json(self) -> None:
        result = self.run_hook("notification", stdin_text="not json at all")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "attention", "Claude Code needs your attention"])

    def test_known_non_attention_notification_type_is_not_notified(self) -> None:
        payload = '{"session_id": "abc", "notification_type": "auth_success", "message": "Authentication successful"}'

        result = self.run_hook("notification", stdin_text=payload)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_documented_attention_required_notification_types_still_notify(self) -> None:
        for notification_type in (
            "permission_prompt",
            "idle_prompt",
            "elicitation_dialog",
            "elicitation_url_dialog",
            "agent_needs_input",
        ):
            with self.subTest(notification_type=notification_type):
                self.log.write_text("", encoding="utf-8")
                payload = f'{{"session_id": "abc", "notification_type": "{notification_type}", "message": "Claude needs your permission to use Bash"}}'

                result = self.run_hook("notification", stdin_text=payload)

                self.assertEqual(result.returncode, 0)
                (call,) = self.calls()
                self.assertEqual(call, ["claude", "attention", "Claude needs your permission to use Bash"])

    def test_notification_without_a_notification_type_field_still_notifies(self) -> None:
        # Older/unknown payloads carry no discriminating "notification_type" field at
        # all: the documented conservative choice is to still notify rather than drop it.
        payload = '{"session_id": "abc", "message": "Claude is waiting for your input"}'

        result = self.run_hook("notification", stdin_text=payload)

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "attention", "Claude is waiting for your input"])

    def test_message_text_mentioning_a_non_attention_type_word_still_notifies(self) -> None:
        # Filtering only ever reads the structural "notification_type" field, never the
        # free-text message: this message contains the denylisted word but has no
        # "notification_type" field.
        payload = '{"session_id": "abc", "message": "auth_success reported by a plugin"}'

        result = self.run_hook("notification", stdin_text=payload)

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["claude", "attention", "auth_success reported by a plugin"])

    def test_unknown_hook_kind_never_notifies_and_never_fails(self) -> None:
        result = self.run_hook("something-else")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_notifier_never_fails_the_hook(self) -> None:
        (self.claude_hooks_dir / "agent-notify.sh").unlink()

        result = self.run_hook("stop-success")

        self.assertEqual(result.returncode, 0)


class CodexNotifyAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-codex-notify-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.log = self.tmp / "log.txt"
        self.log.write_text("", encoding="utf-8")
        claude_hooks_dir = self.tmp / "home" / ".claude" / "hooks"
        claude_hooks_dir.mkdir(parents=True)
        fake = claude_hooks_dir / "agent-notify.sh"
        fake.write_text(FAKE_NOTIFY, encoding="utf-8")
        fake.chmod(0o700)
        self.home = self.tmp / "home"

    def calls(self) -> list:
        text = self.log.read_text(encoding="utf-8")
        chunks = [chunk for chunk in text.split("===END===\n") if chunk.strip()]
        return [chunk.splitlines() for chunk in chunks]

    def run_adapter(self, payload: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "LOG": str(self.log), "HOME": str(self.home)}
        return subprocess.run(
            [BASH, str(CODEX_ADAPTER), payload],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

    def test_turn_complete_payload_maps_to_codex_complete(self) -> None:
        payload = '{"type":"agent-turn-complete","turn-id":"1","last-assistant-message":"Done implementing the feature."}'

        result = self.run_adapter(payload)

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["codex", "complete", "Done implementing the feature."])

    def test_only_ever_maps_to_complete_never_attention_or_blocked(self) -> None:
        # Honest limitation: Codex's native `notify` fires only on turn completion.
        for payload in ('{"type":"agent-turn-complete"}', "not json", ""):
            with self.subTest(payload=payload):
                self.log.write_text("", encoding="utf-8")
                self.run_adapter(payload)
                for call in self.calls():
                    self.assertEqual(call[1], "complete")

    def test_malformed_payload_falls_back_and_never_fails(self) -> None:
        result = self.run_adapter("not json")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(call, ["codex", "complete", "Codex finished its turn"])

    def test_missing_notifier_never_fails(self) -> None:
        (self.home / ".claude" / "hooks" / "agent-notify.sh").unlink()

        result = self.run_adapter('{"last-assistant-message":"hi"}')

        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
