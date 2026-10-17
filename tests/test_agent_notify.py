"""Tests for scripts/agent-notify.sh: the standalone cross-agent notifier.

Runs the real script in a subprocess with a synthetic PATH containing fake
notify-send/paplay/pw-play stubs that only log their argv; no real desktop or audio backend
is ever exercised.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTIFY = REPO_ROOT / "scripts" / "agent-notify.sh"
# Resolved once, independent of PATH: tests deliberately shrink PATH to prove backends
# are optional, and argv[0] lookup must not depend on that same PATH.
BASH = shutil.which("bash") or "/bin/bash"

FAKE_STUB = """#!/bin/sh
{
  printf '%s\\n' "$0"
  for arg in "$@"; do
    printf '%s\\n' "$arg"
  done
  printf '===END===\\n'
} >> "$LOG"
"""

# Same logging behavior, but exits nonzero: simulates a present backend that fails
# (e.g. no D-Bus session, no PulseAudio sink) rather than one that is merely absent.
FAKE_FAILING_STUB = FAKE_STUB + "exit 1\n"


class AgentNotifyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-notify-test-"))
        self.addCleanup(self._cleanup)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        # Only `cat` (used by usage()) resolved from the real host, deliberately never
        # notify-send/paplay/pw-play: those must come solely from self.bin (or be absent) so
        # every test is deterministic regardless of what the host has installed.
        self.safe_bin = self.tmp / "safe-bin"
        self.safe_bin.mkdir()
        os.symlink(shutil.which("cat") or "/bin/cat", self.safe_bin / "cat")
        self.log = self.tmp / "log.txt"
        self.log.write_text("", encoding="utf-8")

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_fake(self, name: str, failing: bool = False) -> None:
        path = self.bin / name
        path.write_text(FAKE_FAILING_STUB if failing else FAKE_STUB, encoding="utf-8")
        path.chmod(0o700)

    def run_notify(self, *args: str, with_stubs: bool = True, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        # self.bin (fakes) is always searched first, then self.safe_bin (cat only):
        # notify-send/paplay/pw-play are only ever found here if a test explicitly faked them.
        path = f"{self.bin}:{self.safe_bin}" if with_stubs else str(self.safe_bin)
        env = {"PATH": path, "LOG": str(self.log), "HOME": str(self.tmp)}
        env.update(env_extra or {})
        return subprocess.run(
            [BASH, str(NOTIFY), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )

    def calls(self) -> list:
        text = self.log.read_text(encoding="utf-8")
        chunks = [chunk for chunk in text.split("===END===\n") if chunk.strip()]
        return [chunk.splitlines() for chunk in chunks]

    def script_with_sound_candidates(self, *candidates: str) -> Path:
        """A copy of the real script with SOUND_CANDIDATES replaced, isolating tests
        from whatever freedesktop sound files happen to exist on the test host."""
        text = NOTIFY.read_text(encoding="utf-8")
        original = text.split('SOUND_CANDIDATES="', 1)[1].split('"', 1)[0]
        patched = text.replace(original, "\n" + "\n".join(candidates) + "\n", 1)
        path = self.tmp / "agent-notify.sh"
        path.write_text(patched, encoding="utf-8")
        return path

    def run_script(self, script: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        # Same deterministic PATH policy as run_notify above.
        env = {"PATH": f"{self.bin}:{self.safe_bin}", "LOG": str(self.log), "HOME": str(self.tmp)}
        env.update(env_extra or {})
        return subprocess.run(
            [BASH, str(script), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )


class InvalidUsageTests(AgentNotifyTestCase):
    def test_missing_arguments_are_rejected(self) -> None:
        for args in ((), ("codex",), ("codex", "complete")):
            with self.subTest(args=args):
                result = self.run_notify(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Usage", result.stderr)

    def test_unknown_agent_is_rejected(self) -> None:
        result = self.run_notify("gpt", "complete", "hi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown agent", result.stderr)

    def test_unknown_event_is_rejected(self) -> None:
        result = self.run_notify("codex", "urgent", "hi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown event", result.stderr)

    def test_empty_message_is_rejected(self) -> None:
        result = self.run_notify("codex", "complete", "")
        self.assertEqual(result.returncode, 2)
        self.assertIn("message must not be empty", result.stderr)

    def test_unknown_switch_is_rejected(self) -> None:
        result = self.run_notify("codex", "complete", "hi", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)

    def test_help_exits_zero_without_calling_any_backend(self) -> None:
        result = self.run_notify("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_invalid_usage_never_invokes_a_backend(self) -> None:
        self.install_fake("notify-send")
        self.install_fake("paplay")
        self.run_notify("codex", "urgent", "hi")
        self.assertEqual(self.calls(), [])


class BackendDispatchTests(AgentNotifyTestCase):
    def test_notify_send_receives_title_and_literal_message(self) -> None:
        self.install_fake("notify-send")

        result = self.run_notify("codex", "complete", "hello world")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertIn("Codex - complete", call)
        self.assertIn("hello world", call)

    def test_urgency_is_critical_only_for_blocked(self) -> None:
        self.install_fake("notify-send")
        for event, urgency in (("attention", "normal"), ("complete", "normal"), ("blocked", "critical")):
            with self.subTest(event=event):
                self.log.write_text("", encoding="utf-8")
                self.run_notify("claude", event, "msg")
                (call,) = self.calls()
                self.assertIn(f"--urgency={urgency}", call)

    def test_message_is_never_evaluated_as_a_command(self) -> None:
        self.install_fake("notify-send")
        canary = self.tmp / "canary"
        payload = f"hi $(touch {canary}) \\`touch {canary}\\`; touch {canary}"

        result = self.run_notify("codex", "complete", payload)

        self.assertEqual(result.returncode, 0)
        self.assertFalse(canary.exists())
        (call,) = self.calls()
        self.assertIn(payload, call)

    def test_no_desktop_switch_skips_notify_send(self) -> None:
        self.install_fake("notify-send")
        self.run_notify("codex", "complete", "hi", "--no-desktop")
        self.assertEqual(self.calls(), [])

    def test_missing_notify_send_is_not_an_error(self) -> None:
        result = self.run_notify("codex", "complete", "hi", with_stubs=False)
        self.assertEqual(result.returncode, 0)

    def test_paplay_plays_first_existing_candidate_sound(self) -> None:
        self.install_fake("paplay")
        missing = self.tmp / "missing.oga"
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(missing), str(candidate))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertIn(str(candidate), call)
        self.assertNotIn(str(missing), call)

    def test_pw_play_is_used_when_paplay_is_missing(self) -> None:
        self.install_fake("pw-play")
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        (call,) = self.calls()
        self.assertEqual(Path(call[0]).name, "pw-play")
        self.assertEqual(call[1:], ["--", str(candidate)])
        self.assertNotIn("\a", result.stdout)

    def test_paplay_is_preferred_when_both_players_exist(self) -> None:
        self.install_fake("paplay")
        self.install_fake("pw-play")
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        self.run_script(script, "codex", "complete", "hi")

        (call,) = self.calls()
        self.assertEqual(Path(call[0]).name, "paplay")

    def test_failing_pw_play_falls_back_to_the_bell(self) -> None:
        self.install_fake("pw-play", failing=True)
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertIn("\a", result.stdout)

    def test_no_sound_switch_skips_paplay_even_with_a_candidate_present(self) -> None:
        self.install_fake("paplay")
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        self.run_script(script, "codex", "complete", "hi", "--no-sound")

        self.assertEqual(self.calls(), [])

    def test_missing_sound_candidates_is_not_an_error(self) -> None:
        self.install_fake("paplay")
        script = self.script_with_sound_candidates(str(self.tmp / "missing-a.oga"), str(self.tmp / "missing-b.oga"))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])  # neither candidate exists

    def test_bell_fires_when_no_backend_is_available_and_is_suppressed_by_switch(self) -> None:
        enabled = self.run_notify("codex", "complete", "hi")
        self.assertIn("\a", enabled.stdout)

        disabled = self.run_notify("codex", "complete", "hi", "--no-bell")
        self.assertNotIn("\a", disabled.stdout)

    def test_bell_is_suppressed_when_desktop_notification_succeeds(self) -> None:
        self.install_fake("notify-send")

        result = self.run_notify("codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("\a", result.stdout)

    def test_bell_is_suppressed_when_sound_succeeds(self) -> None:
        self.install_fake("paplay")
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertNotIn("\a", result.stdout)

    def test_bell_fires_when_a_present_notify_send_fails(self) -> None:
        self.install_fake("notify-send", failing=True)

        result = self.run_notify("codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertIn("\a", result.stdout)
        (call,) = self.calls()  # the failing backend was still actually invoked
        self.assertIn("hi", call)

    def test_bell_fires_when_a_present_paplay_fails(self) -> None:
        self.install_fake("paplay", failing=True)
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        result = self.run_script(script, "codex", "complete", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertIn("\a", result.stdout)
        (call,) = self.calls()
        self.assertIn(str(candidate), call)

    def test_bell_still_fires_when_both_present_backends_fail(self) -> None:
        self.install_fake("notify-send", failing=True)
        self.install_fake("paplay", failing=True)
        script = self.script_with_sound_candidates(str(self.tmp / "missing.oga"))

        result = self.run_script(script, "codex", "blocked", "hi")

        self.assertEqual(result.returncode, 0)
        self.assertIn("\a", result.stdout)

    def test_notify_env_var_disables_desktop_like_the_switch(self) -> None:
        self.install_fake("notify-send")

        result = self.run_notify("codex", "complete", "hi", env_extra={"IFDEV_AGENT_NOTIFY": "0"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("\a", result.stdout)  # nothing else succeeded: BEL still fires

    def test_sound_env_var_disables_sound_like_the_switch(self) -> None:
        self.install_fake("paplay")
        candidate = self.tmp / "sound.oga"
        candidate.write_text("", encoding="utf-8")
        script = self.script_with_sound_candidates(str(candidate))

        result = self.run_script(script, "codex", "complete", "hi", env_extra={"IFDEV_AGENT_SOUND": "0"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("\a", result.stdout)

    def test_all_backends_missing_still_exits_zero(self) -> None:
        result = self.run_notify("claude", "blocked", "hi", "--no-bell", with_stubs=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
