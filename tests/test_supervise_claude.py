"""Deterministic tests for supervised Claude permission construction."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "codex/skills/supervise-claude/scripts/run_claude.py"
SPEC = importlib.util.spec_from_file_location("run_claude", SCRIPT)
assert SPEC and SPEC.loader
run_claude = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_claude)

SUPPORTED_HELP = """
--print
--output-format <format>
--permission-mode <mode> choices: "acceptEdits", "bypassPermissions", "dontAsk"
--permission-prompts <target> choices: "host", "none"
--restricted refuses bypassPermissions
--strict-mcp-config
--tools <tools...>
--allowedTools <tools...>
--disallowedTools <tools...>
"""


class SupervisedClaudeCommandTests(unittest.TestCase):
    def test_uses_noninteractive_restricted_mode_with_narrow_tools(self) -> None:
        command = run_claude.build_command(
            "claude", SUPPORTED_HELP, ["python3 -m unittest discover -s tests -v", "git diff *"]
        )
        self.assertIn("dontAsk", command)
        self.assertIn("none", command)
        self.assertIn("--restricted", command)
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("bypassPermissions", command)
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("Bash(*)", command)
        self.assertNotIn("Bash", command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")])
        self.assertIn("Bash(python3 -m unittest discover -s tests -v)", command)

    def test_delivery_and_destructive_commands_are_denied(self) -> None:
        command = run_claude.build_command("claude", SUPPORTED_HELP, ["git status *"])
        denied = command[command.index("--disallowedTools") + 1 :]
        for rule in ("Bash(git commit *)", "Bash(git push *)", "Bash(git reset *)", "Bash(git clean *)"):
            self.assertIn(rule, denied)

    def test_rejects_unrestricted_or_forbidden_allow_rules(self) -> None:
        for rule in ("*", "bash -c *", "Bash(*)", "git commit *", "git push *", "sudo *", "ssh *"):
            with self.subTest(rule=rule), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command("claude", SUPPORTED_HELP, [rule])

    def test_rejects_shell_composition_in_allow_rules(self) -> None:
        for rule in ("npm test && git push", "pytest | tee output", "python3 $(pwd)"):
            with self.subTest(rule=rule), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command("claude", SUPPORTED_HELP, [rule])

    def test_fails_closed_when_cli_feature_is_not_advertised(self) -> None:
        for marker in run_claude.REQUIRED_HELP_MARKERS:
            with self.subTest(marker=marker), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command("claude", SUPPORTED_HELP.replace(marker, ""), [])

    def test_does_not_modify_interactive_configuration(self) -> None:
        command = run_claude.build_command("claude", SUPPORTED_HELP, [])
        self.assertNotIn("--settings", command)
        self.assertNotIn("--setting-sources", command)
        self.assertEqual(command[0], "claude")


if __name__ == "__main__":
    unittest.main()
