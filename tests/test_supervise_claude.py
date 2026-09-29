"""Deterministic tests for supervised Claude permissions and the supervision policy."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "codex/skills/supervise-claude/scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run_claude = load("run_claude")
policy = load("supervision_policy")
notify_final_status = load("notify_final_status")

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
--model <model> Provide an alias for the latest model (e.g. 'sonnet' or 'opus')
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


def without_model(command: list) -> list:
    index = command.index("--model")
    return command[:index] + command[index + 2 :]


class SupervisedClaudeModelTests(unittest.TestCase):
    ALLOW = ["python3 -m unittest discover -s tests -v", "git diff *"]

    def test_defaults_to_the_sonnet_alias(self) -> None:
        command = run_claude.build_command("claude", SUPPORTED_HELP, self.ALLOW)
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(command.count("--model"), 1)

    def test_passes_the_selected_alias(self) -> None:
        for model in ("sonnet", "opus"):
            with self.subTest(model=model):
                command = run_claude.build_command("claude", SUPPORTED_HELP, self.ALLOW, model)
                self.assertEqual(command[command.index("--model") + 1], model)

    def test_permissions_tools_and_rules_are_identical_for_every_model(self) -> None:
        commands = [run_claude.build_command("claude", SUPPORTED_HELP, self.ALLOW, model) for model in run_claude.SUPPORTED_MODELS]
        baseline = without_model(commands[0])
        for command in commands[1:]:
            self.assertEqual(without_model(command), baseline)
        for flag in ("--permission-mode", "--permission-prompts", "--restricted", "--strict-mcp-config", "--tools",
                     "--allowedTools", "--disallowedTools"):
            self.assertIn(flag, baseline)
        self.assertEqual(baseline[baseline.index("--permission-mode") + 1], "dontAsk")

    def test_unsupported_models_fail_without_substitution(self) -> None:
        for model in ("haiku", "claude-opus-4-1-20250805", "", "Sonnet", "sonnet ", "opus[1m]", "default"):
            with self.subTest(model=model), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command("claude", SUPPORTED_HELP, [], model)

    def test_fails_closed_when_the_alias_is_not_advertised(self) -> None:
        help_text = SUPPORTED_HELP.replace("'opus'", "'other'")
        self.assertIn("--model", run_claude.build_command("claude", help_text, [], "sonnet"))
        with self.assertRaises(run_claude.ConfigurationError):
            run_claude.build_command("claude", help_text, [], "opus")

    def test_supported_models_match_the_policy(self) -> None:
        self.assertEqual(run_claude.SUPPORTED_MODELS, policy.SUPPORTED_MODELS)
        self.assertEqual(run_claude.DEFAULT_MODEL, policy.DEFAULT_MODEL)


class ProjectClaudePathPermissionTests(unittest.TestCase):
    """Regression coverage for the project .claude/** patch-handoff authorization.

    Restricted mode's bare Edit/Write tool grant lets Claude edit an ordinary repository
    file such as src/example.txt directly. A protected path such as
    .claude/agents/example.md is never granted to Claude: --allow-claude-path only
    authorizes the supervisor to apply Claude's authored patch (protected_patch.py).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()

    def allowed(self, command: list) -> list:
        return command[command.index("--allowedTools") + 1 : command.index("--disallowedTools")]

    def test_default_invocation_grants_no_project_claude_path(self) -> None:
        command = run_claude.build_command("claude", SUPPORTED_HELP, [], project=self.project)
        allowed = self.allowed(command)
        self.assertFalse(any(rule.startswith(("Edit(", "Write(")) for rule in allowed))
        self.assertEqual(allowed, list(run_claude.BASE_TOOLS))

    def test_ordinary_direct_edit_stays_covered_by_the_bare_grant(self) -> None:
        # src/example.txt is edited directly through the unqualified Edit/Write grant.
        command = run_claude.build_command("claude", SUPPORTED_HELP, [], project=self.project)
        self.assertIn("Edit", command)
        self.assertIn("Write", command)

    def test_authorization_grants_claude_no_direct_protected_write(self) -> None:
        baseline = run_claude.build_command("claude", SUPPORTED_HELP, [], project=self.project)
        for patterns in (
            (".claude/agents/foo.md",),
            (".claude/skills/foo/SKILL.md",),
            (".claude/agents/**",),
            (".claude/agents/old.md", ".claude/agents/new.md", ".claude/skills/foo/SKILL.md"),
        ):
            with self.subTest(patterns=patterns):
                command = run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=patterns
                )
                self.assertEqual(command, baseline)
                self.assertFalse(any(rule.startswith(("Edit(", "Write(")) for rule in self.allowed(command)))

    def test_protected_rename_needs_no_bash_mv_workaround(self) -> None:
        # A protected rename is a `rename from`/`rename to` section of the authored patch,
        # so no Bash tool or `mv` command family is added for it.
        command = run_claude.build_command(
            "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(".claude/agents/**",)
        )
        self.assertNotIn("Bash", command[command.index("--tools") + 1].split(","))
        self.assertFalse(any(rule.startswith("Bash(") for rule in self.allowed(command)))

    def test_rejects_unsupported_glob_syntax(self) -> None:
        for pattern in (".claude/agents/[ab].md", ".claude/{agents,skills}/**", ".claude", ".claude/./a.md"):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_one_unsafe_authorization_among_safe_ones_fails_closed(self) -> None:
        with self.assertRaises(run_claude.ConfigurationError):
            run_claude.build_command(
                "claude",
                SUPPORTED_HELP,
                [],
                project=self.project,
                allow_claude_paths=(".claude/agents/foo.md", "~/.claude/settings.json"),
            )

    def test_prompt_is_unchanged_without_authorization(self) -> None:
        self.assertEqual(run_claude.build_prompt("synthetic task\n", ()), "synthetic task\n")

    def test_prompt_carries_the_patch_contract_with_authorization(self) -> None:
        prompt = run_claude.build_prompt("synthetic task\n", (".claude/agents/foo.md",))
        self.assertTrue(prompt.startswith("synthetic task\n"))
        self.assertIn("<protected-path-contract>", prompt)
        self.assertIn("- .claude/agents/foo.md", prompt)
        self.assertIn(run_claude.protected_patch.ARTIFACT_NAME, prompt)
        self.assertIn("direct\nprotected writes are unsupported", prompt)

    def test_rejects_global_home_claude_settings_and_instructions(self) -> None:
        for pattern in ("~/.claude/settings.json", "~/.claude/CLAUDE.md", "~/.codex/config.toml", "~/.ssh/id_ed25519"):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_rejects_absolute_paths(self) -> None:
        for pattern in ("/etc/passwd", "/var/lib/example/.claude/settings.json", r"C:\Config\.claude\settings.json"):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_rejects_traversal_and_foreign_repository_paths(self) -> None:
        for pattern in (
            "../other-repo/.claude/agents/foo.md",
            ".claude/../../other-repo/.claude/foo.md",
            ".claude/agents/../../../etc/passwd",
        ):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_rejects_paths_outside_the_project_dot_claude_directory(self) -> None:
        for pattern in ("src/example.txt", ".git/config", "CLAUDE.md", "", "   "):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_rejects_a_symlink_escape_out_of_the_project(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.project / ".claude").symlink_to(Path(outside.name).resolve())
        with self.assertRaises(run_claude.ConfigurationError):
            run_claude.build_command(
                "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(".claude/agents/foo.md",)
            )

    def test_rejects_a_dangling_symlink_escape_out_of_the_project(self) -> None:
        # A broken symlink still lexists(); the walk-up must not skip past it and treat
        # the pattern as if .claude were an ordinary, contained directory.
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.project / ".claude").symlink_to(Path(outside.name).resolve() / "does-not-exist")
        with self.assertRaises(run_claude.ConfigurationError):
            run_claude.build_command(
                "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(".claude/agents/foo.md",)
            )

    def test_claude_paths_require_a_resolved_project_directory(self) -> None:
        with self.assertRaises(run_claude.ConfigurationError):
            run_claude.build_command("claude", SUPPORTED_HELP, [], allow_claude_paths=(".claude/agents/foo.md",))

    def test_rejects_a_project_root_under_home_ssh(self) -> None:
        # The launcher must fail closed before invocation, not rely solely on
        # protected_patch.py's own check at later patch-apply time.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            nested = home / ".ssh" / "nested-project"
            nested.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                with self.assertRaises(run_claude.ConfigurationError) as caught:
                    run_claude.build_command(
                        "claude", SUPPORTED_HELP, [], project=nested, allow_claude_paths=(".claude/agents/foo.md",)
                    )
        self.assertIn("global configuration", str(caught.exception))

    def test_rejects_sensitive_allow_claude_path_targets(self) -> None:
        for pattern in (".claude/credentials.json", ".claude/agents/token.md", ".claude/secrets/api.md"):
            with self.subTest(pattern=pattern), self.assertRaises(run_claude.ConfigurationError):
                run_claude.build_command(
                    "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(pattern,)
                )

    def test_opt_in_never_changes_mode_or_denylist_invariants(self) -> None:
        baseline = run_claude.build_command("claude", SUPPORTED_HELP, [], project=self.project)
        opted_in = run_claude.build_command(
            "claude", SUPPORTED_HELP, [], project=self.project, allow_claude_paths=(".claude/agents/foo.md",)
        )
        for command in (baseline, opted_in):
            self.assertIn("dontAsk", command)
            self.assertIn("none", command)
            self.assertIn("--restricted", command)
            self.assertIn("--strict-mcp-config", command)
            self.assertNotIn("bypassPermissions", command)
            self.assertNotIn("acceptEdits", command)
        baseline_denied = baseline[baseline.index("--disallowedTools") + 1 :]
        opted_in_denied = opted_in[opted_in.index("--disallowedTools") + 1 :]
        self.assertEqual(baseline_denied, opted_in_denied)
        self.assertEqual(opted_in_denied, list(run_claude.DENIED_TOOLS))


FAKE_CLAUDE = """#!{python}
import os
import sys
from pathlib import Path
if sys.argv[1:] == ["--help"]:
    print({help!r})
else:
    Path({record!r}).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
    Path({env_record!r}).write_text(os.environ.get("IFDEV_SUPERVISED_CLAUDE", ""), encoding="utf-8")
    Path({stdin_record!r}).write_text(sys.stdin.read(), encoding="utf-8")
"""


class RunClaudeCliTests(unittest.TestCase):
    def run_helper(self, tmp: Path, *extra: str) -> tuple:
        record = tmp / "argv.txt"
        env_record = tmp / "env-supervised.txt"
        stdin_record = tmp / "stdin.txt"
        fake = tmp / "fake-claude"
        fake.write_text(
            FAKE_CLAUDE.format(
                python=sys.executable,
                help=SUPPORTED_HELP,
                record=str(record),
                env_record=str(env_record),
                stdin_record=str(stdin_record),
            ),
            encoding="utf-8",
        )
        fake.chmod(0o700)
        prompt = tmp / "prompt.md"
        prompt.write_text("synthetic task\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_claude.py"), "--project", str(tmp), "--prompt", str(prompt),
             "--log", str(tmp / "logs/claude.log"), "--claude", str(fake), "--timeout-seconds", "30", *extra],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        argv = record.read_text(encoding="utf-8").split("\n") if record.exists() else None
        supervised_marker = env_record.read_text(encoding="utf-8") if env_record.exists() else None
        self.last_stdin = stdin_record.read_text(encoding="utf-8") if stdin_record.exists() else None
        return result, argv, supervised_marker

    def test_passes_the_default_and_selected_model(self) -> None:
        for extra, expected in (((), "sonnet"), (("--model", "opus"), "opus")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                result, argv, _ = self.run_helper(Path(tmp), *extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(argv[argv.index("--model") + 1], expected)

    def test_unsupported_model_is_refused_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, argv, _ = self.run_helper(Path(tmp), "--model", "haiku")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported model", result.stderr)
        self.assertIsNone(argv)

    def test_marks_the_invocation_as_supervised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, _, env_record = self.run_helper(Path(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env_record, "1")

    def test_supervised_marker_does_not_leak_into_this_process(self) -> None:
        # run_claude.py runs as a separate subprocess of this test, so the marker it
        # passes via env= to its own child can never mutate this process's os.environ;
        # comparing snapshots (not asserting absence) keeps this deterministic even
        # when the ambient environment already carries the variable.
        before = dict(os.environ)
        with tempfile.TemporaryDirectory() as tmp:
            self.run_helper(Path(tmp))
        self.assertEqual(dict(os.environ), before)

    def test_authorization_adds_the_patch_contract_but_no_protected_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline, baseline_argv, _ = self.run_helper(Path(tmp))
            result, argv, _ = self.run_helper(
                Path(tmp), "--allow-claude-path", ".claude/agents/foo.md", "--allow-claude-path", ".claude/skills/foo/SKILL.md"
            )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(argv, baseline_argv)
        self.assertFalse(any(token.startswith(("Edit(", "Write(")) for token in argv))
        self.assertTrue(self.last_stdin.startswith("synthetic task\n"))
        self.assertIn("<protected-path-contract>", self.last_stdin)
        self.assertIn("- .claude/agents/foo.md\n- .claude/skills/foo/SKILL.md", self.last_stdin)

    def test_rejects_an_unsafe_claude_path_before_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, argv, _ = self.run_helper(Path(tmp), "--allow-claude-path", "../other-repo/.claude/agents/foo.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--allow-claude-path", result.stderr)
        self.assertIsNone(argv)

    def test_omitting_the_flag_grants_no_project_claude_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, argv, _ = self.run_helper(Path(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(token.startswith(("Edit(", "Write(")) for token in argv))
        self.assertEqual(self.last_stdin, "synthetic task\n")


TASK_PATHS = frozenset({"src/app.py", "tests/test_app.py"})


def formatter(**overrides):
    fields = dict(
        operation="formatter",
        paths=("src/app.py",),
        command_inspected=True,
        deterministic=True,
        changes=frozenset({"formatting"}),
    )
    fields.update(overrides)
    return policy.Remediation(**fields)


def reviewed(cycles: int, unresolved=()):
    state = policy.RunState(task_paths=TASK_PATHS)
    for _ in range(cycles):
        state = policy.start_claude_cycle(state)
    return policy.record_review(state, unresolved)


class MechanicalRemediationTests(unittest.TestCase):
    def test_eligible_mechanical_operations_are_allowed(self) -> None:
        state = reviewed(1, {"formatting"})
        for remediation in (
            formatter(),
            formatter(operation="whitespace-normalization", changes=frozenset({"whitespace"})),
            formatter(operation="line-ending-normalization", paths=tuple(TASK_PATHS), changes=frozenset({"line-endings"})),
        ):
            with self.subTest(operation=remediation.operation):
                self.assertEqual(policy.evaluate_remediation(remediation, state), policy.Decision(True, ()))

    def test_semantic_and_non_mechanical_changes_are_forbidden(self) -> None:
        state = reviewed(1, {"formatting"})
        for change in sorted(policy.FORBIDDEN_CHANGES):
            with self.subTest(change=change):
                decision = policy.evaluate_remediation(formatter(changes=frozenset({"formatting", change})), state)
                self.assertFalse(decision.allowed)
                self.assertIn(f"forbidden change: {change}", decision.reasons)
        for expected in ("semantic", "test", "assertion", "refactor", "sql", "rls", "auth", "security",
                         "migration", "dependency", "architecture", "behavior", "suppression"):
            self.assertIn(expected, policy.FORBIDDEN_CHANGES)

    def test_doubtful_or_unknown_changes_fail_closed(self) -> None:
        state = reviewed(1, {"formatting"})
        decision = policy.evaluate_remediation(formatter(changes=frozenset({"uncertain"})), state)
        self.assertFalse(decision.allowed)
        self.assertIn("unclassified change: uncertain", decision.reasons)
        self.assertFalse(policy.evaluate_remediation(formatter(changes=frozenset()), state).allowed)

    def test_non_mechanical_operations_are_refused(self) -> None:
        state = reviewed(1, {"formatting"})
        for operation in ("lint-autofix", "codemod", "manual-edit", "dependency-update", "snapshot-update", ""):
            with self.subTest(operation=operation):
                decision = policy.evaluate_remediation(formatter(operation=operation), state)
                self.assertIn(f"not a mechanical operation: {operation}", decision.reasons)

    def test_uninspected_or_nondeterministic_commands_are_refused(self) -> None:
        state = reviewed(1, {"formatting"})
        self.assertIn(
            "command was not inspected before execution",
            policy.evaluate_remediation(formatter(command_inspected=False), state).reasons,
        )
        self.assertIn(
            "operation is not deterministic", policy.evaluate_remediation(formatter(deterministic=False), state).reasons
        )

    def test_only_task_scope_paths_are_allowed(self) -> None:
        state = reviewed(1, {"formatting"})
        for path in ("src/other.py", "../outside.py", "/etc/hosts", "src/../src/app.py", ""):
            with self.subTest(path=path):
                decision = policy.evaluate_remediation(formatter(paths=(path,)), state)
                self.assertIn(f"path outside task scope: {path}", decision.reasons)
        self.assertIn("no explicit target paths", policy.evaluate_remediation(formatter(paths=()), state).reasons)

    def test_remediation_does_not_consume_a_claude_cycle(self) -> None:
        state = reviewed(2, {"formatting"})
        remediated = policy.apply_remediation(state, formatter())
        self.assertEqual(remediated.cycles_started, 2)
        self.assertEqual(policy.next_step(remediated), "REVIEW")  # revalidation before COMPLETE
        self.assertEqual(policy.next_step(policy.record_review(remediated, ())), "COMPLETE")

    def test_cycle_three_followed_by_eligible_remediation_can_complete(self) -> None:
        state = reviewed(3, {"formatting", "line-endings"})
        self.assertEqual(policy.next_step(state), "REMEDIATE")
        remediated = policy.apply_remediation(state, formatter(changes=frozenset({"formatting", "line-endings"})))
        self.assertEqual(remediated.cycles_started, 3)
        self.assertFalse(policy.delivery_allowed(remediated))
        final = policy.record_review(remediated, ())
        self.assertEqual(policy.next_step(final), "COMPLETE")
        self.assertTrue(policy.delivery_allowed(final))

    def test_after_cycle_three_remediation_requires_sole_mechanical_failure(self) -> None:
        for unresolved in ({"formatting", "correctness"}, {"formatting", "security"}, {"semantic"}, set()):
            with self.subTest(unresolved=unresolved):
                state = reviewed(3, unresolved)
                self.assertFalse(policy.evaluate_remediation(formatter(), state).allowed)
                with self.assertRaises(policy.PolicyViolation):
                    policy.apply_remediation(state, formatter())
        self.assertEqual(policy.next_step(reviewed(3, {"formatting", "correctness"})), "BLOCKED")

    def test_before_budget_is_exhausted_non_mechanical_failures_are_delegated(self) -> None:
        self.assertEqual(policy.next_step(policy.RunState()), "DELEGATE")
        self.assertEqual(policy.next_step(reviewed(1, {"formatting", "semantic"})), "DELEGATE")
        self.assertEqual(policy.next_step(reviewed(2, {"uncertain"})), "DELEGATE")
        self.assertEqual(policy.next_step(reviewed(3, {"uncertain"})), "BLOCKED")

    def test_there_is_never_a_fourth_claude_cycle(self) -> None:
        state = reviewed(3, {"semantic"})
        with self.assertRaises(policy.PolicyViolation):
            policy.start_claude_cycle(state)
        self.assertFalse(policy.evaluate_remediation(formatter(), policy.RunState(cycles_started=4, task_paths=TASK_PATHS, unresolved=frozenset({"formatting"}))).allowed)

    def test_normalization_only_diff_check(self) -> None:
        self.assertTrue(policy.is_normalization_only("a = 1  \r\nb = 2\r\n", "a = 1\nb = 2\n"))
        self.assertTrue(policy.is_normalization_only("x\n\n\n", "x\n"))
        self.assertFalse(policy.is_normalization_only("if x:\n    y\n", "if x:\ny\n"))
        self.assertFalse(policy.is_normalization_only("a = 1\n", "a = 2\n"))
        self.assertFalse(policy.is_normalization_only("a\nb\n", "a\n\nb\n"))


JUSTIFICATION = "Recorded reason for this cycle's model."


def model_request(**overrides):
    fields = dict(justification=JUSTIFICATION)
    fields.update(overrides)
    return policy.ModelRequest(**fields)


def final_cycle_state(unresolved=("correctness",)):
    return reviewed(2, unresolved)


class ModelSelectionTests(unittest.TestCase):
    def test_initial_implementation_defaults_to_sonnet(self) -> None:
        for request in (policy.ModelRequest(), model_request()):
            with self.subTest(request=request):
                self.assertEqual(policy.select_model(policy.RunState(), request).model, "sonnet")

    def test_ordinary_corrections_use_sonnet(self) -> None:
        state = reviewed(1, {"correctness", "semantic"})
        decision = policy.select_model(state, model_request(reasoning_difficulty=True))
        self.assertEqual(decision.model, "sonnet")
        self.assertIn("ordinary correction; escalation is reserved for the final allowed cycle", decision.reasons)
        # A documented planning assessment applies to the initial cycle only.
        self.assertEqual(
            policy.select_model(state, model_request(planning_assessment=frozenset({"high-risk"}))).model, "sonnet"
        )

    def test_documented_high_complexity_or_risk_planning_may_start_with_opus(self) -> None:
        for assessment in ({"high-complexity"}, {"high-risk"}, {"high-complexity", "high-risk"}):
            with self.subTest(assessment=assessment):
                decision = policy.select_model(policy.RunState(), model_request(planning_assessment=frozenset(assessment)))
                self.assertEqual(decision, policy.ModelDecision("opus", (f"escalation: {JUSTIFICATION}",)))

    def test_task_size_or_undocumented_assessment_never_escalates(self) -> None:
        for assessment in ({"large-task"}, {"task-size"}, {"many-files"}, set()):
            with self.subTest(assessment=assessment):
                request = model_request(planning_assessment=frozenset(assessment))
                self.assertEqual(policy.select_model(policy.RunState(), request).model, "sonnet")

    def test_escalation_requires_a_recorded_justification(self) -> None:
        for justification in ("", "   "):
            with self.subTest(justification=justification):
                initial = policy.select_model(
                    policy.RunState(),
                    model_request(planning_assessment=frozenset({"high-risk"}), justification=justification),
                )
                final = policy.select_model(
                    final_cycle_state(), model_request(reasoning_difficulty=True, justification=justification)
                )
                for decision in (initial, final):
                    self.assertEqual(decision.model, "sonnet")
                    self.assertIn("no recorded escalation justification", decision.reasons)

    def test_final_cycle_unresolved_reasoning_issue_may_use_opus(self) -> None:
        for unresolved in ({"correctness"}, {"semantic"}, {"semantic", "formatting"}):
            with self.subTest(unresolved=unresolved):
                decision = policy.select_model(final_cycle_state(unresolved), model_request(reasoning_difficulty=True))
                self.assertEqual(decision.model, "opus")
                self.assertIn(JUSTIFICATION, decision.reasons[0])

    def test_final_cycle_without_a_reasoning_semantic_finding_stays_sonnet(self) -> None:
        cases = (
            ({"formatting"}, True, "no unresolved semantic or correctness finding"),
            ({"test", "security"}, True, "no unresolved semantic or correctness finding"),
            ({"uncertain"}, True, "no unresolved semantic or correctness finding"),
            ({"correctness"}, False, "finding is not attributed to implementation reasoning difficulty"),
        )
        for unresolved, difficulty, reason in cases:
            with self.subTest(unresolved=unresolved, difficulty=difficulty):
                decision = policy.select_model(final_cycle_state(unresolved), model_request(reasoning_difficulty=difficulty))
                self.assertEqual(decision.model, "sonnet")
                self.assertIn(reason, decision.reasons)

    def test_environment_failures_never_escalate(self) -> None:
        for failure in sorted(policy.ENVIRONMENT_FAILURES):
            for unresolved in ({failure}, {failure, "correctness"}):
                with self.subTest(unresolved=unresolved):
                    decision = policy.select_model(final_cycle_state(unresolved), model_request(reasoning_difficulty=True))
                    self.assertEqual(decision.model, "sonnet")
                    self.assertIn(f"environment failures are never escalated: {failure}", decision.reasons)
        for expected in ("timeout", "authentication", "quota", "network", "transport", "permission-denied",
                         "missing-dependency", "missing-sdk", "environment"):
            self.assertIn(expected, policy.ENVIRONMENT_FAILURES)

    def test_task_level_overrides_are_respected(self) -> None:
        escalating = model_request(reasoning_difficulty=True)
        for state in (policy.RunState(), reviewed(1, {"correctness"}), final_cycle_state()):
            with self.subTest(cycle=state.cycles_started + 1):
                for override in ("sonnet", "no-escalation"):
                    request = model_request(override=override, reasoning_difficulty=True,
                                            planning_assessment=frozenset({"high-risk"}))
                    self.assertEqual(policy.select_model(state, request).model, "sonnet")
                self.assertEqual(policy.select_model(state, policy.ModelRequest(override="opus")).model, "opus")
        self.assertEqual(policy.select_model(final_cycle_state(), escalating).model, "opus")
        self.assertEqual(
            policy.select_model(final_cycle_state(), model_request(override="no-escalation")).reasons,
            ("task-level no-escalation override",),
        )

    def test_unsupported_override_fails_without_substitution(self) -> None:
        for override in ("haiku", "claude-opus-4-1-20250805", "Opus", "best", "default"):
            with self.subTest(override=override), self.assertRaises(policy.PolicyViolation):
                policy.select_model(policy.RunState(), policy.ModelRequest(override=override))

    def test_model_selection_never_changes_the_cycle_budget(self) -> None:
        state = final_cycle_state()
        policy.select_model(state, model_request(reasoning_difficulty=True))
        self.assertEqual(state.cycles_started, 2)
        self.assertEqual(policy.start_claude_cycle(state).cycles_started, 3)
        exhausted = reviewed(3, {"correctness"})
        for request in (model_request(reasoning_difficulty=True), policy.ModelRequest(override="opus")):
            with self.subTest(request=request), self.assertRaises(policy.PolicyViolation):
                policy.select_model(exhausted, request)
        self.assertEqual(policy.MAX_CLAUDE_CYCLES, 3)


class CycleModelHistoryTests(unittest.TestCase):
    def run_cycle(self, state, request):
        decision = policy.select_model(state, request)
        started = policy.start_claude_cycle(state)
        return policy.record_cycle_model(started, decision), decision

    def test_sonnet_only_run_reports_no_escalation_required(self) -> None:
        state = policy.RunState(task_paths=TASK_PATHS)
        state, first = self.run_cycle(state, model_request())
        state = policy.record_review(state, {"correctness"})
        state, second = self.run_cycle(state, model_request())
        state = policy.record_review(state, {"correctness"})
        state, third = self.run_cycle(state, model_request(reasoning_difficulty=False))
        report = policy.render_cycles_report(state)
        self.assertEqual([first.model, second.model, third.model], ["sonnet", "sonnet", "sonnet"])
        self.assertEqual(len(state.cycle_records), 3)
        self.assertEqual([record.cycle for record in state.cycle_records], [1, 2, 3])
        self.assertTrue(report.endswith("No escalation required."))
        self.assertNotIn("opus", report)

    def test_escalated_final_cycle_reports_its_opus_reason(self) -> None:
        state = policy.RunState(task_paths=TASK_PATHS)
        state, _ = self.run_cycle(state, model_request())
        state = policy.record_review(state, {"correctness"})
        state, _ = self.run_cycle(state, model_request())
        state = policy.record_review(state, {"correctness"})
        state, decision = self.run_cycle(state, model_request(reasoning_difficulty=True))
        self.assertEqual(decision.model, "opus")
        report = policy.render_cycles_report(state)
        self.assertIn(f"- Cycle 3: opus (escalation: {JUSTIFICATION})", report)
        self.assertIn(f"Escalation occurred in cycle 3: escalation: {JUSTIFICATION}", report)
        self.assertNotIn("No escalation required.", report)

    def test_cannot_record_before_a_cycle_is_started(self) -> None:
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(policy.RunState(), policy.ModelDecision("sonnet", ("x",)))

    def test_cannot_record_the_same_cycle_twice(self) -> None:
        state = policy.start_claude_cycle(policy.RunState())
        decision = policy.ModelDecision("sonnet", ("x",))
        recorded = policy.record_cycle_model(state, decision)
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(recorded, decision)

    def test_cannot_record_out_of_order(self) -> None:
        state = policy.start_claude_cycle(policy.RunState())
        state = policy.start_claude_cycle(state)  # cycles_started == 2, nothing recorded yet
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(state, policy.ModelDecision("sonnet", ("x",)))

    def test_rejects_an_unsupported_model_in_the_decision(self) -> None:
        state = policy.start_claude_cycle(policy.RunState())
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(state, policy.ModelDecision("haiku", ("x",)))

    def test_rejects_a_forged_prior_record_even_with_a_matching_length(self) -> None:
        forged = policy.CycleRecord(cycle=99, model="haiku", reason="fabricated")
        state = policy.RunState(cycles_started=2, cycle_records=(forged,))
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(state, policy.ModelDecision("sonnet", ("x",)))

    def test_rejects_a_decision_with_no_recorded_reason(self) -> None:
        state = policy.start_claude_cycle(policy.RunState())
        with self.assertRaises(policy.PolicyViolation):
            policy.record_cycle_model(state, policy.ModelDecision("sonnet", ()))

    def test_render_cycles_rejects_a_forged_history(self) -> None:
        forged = policy.CycleRecord(cycle=99, model="haiku", reason="fabricated")
        state = policy.RunState(cycles_started=1, cycle_records=(forged,))
        with self.assertRaises(policy.PolicyViolation):
            policy.render_cycles_report(state)

    def test_render_cycles_requires_exact_agreement_with_cycles_started(self) -> None:
        state = policy.RunState(task_paths=TASK_PATHS)
        state, _ = self.run_cycle(state, model_request())
        mismatched = policy.replace(state, cycles_started=state.cycles_started + 1)
        with self.assertRaises(policy.PolicyViolation):
            policy.render_cycles_report(mismatched)

    def test_does_not_change_cycle_limits_or_transitions(self) -> None:
        state = policy.RunState(task_paths=TASK_PATHS)
        state, _ = self.run_cycle(state, model_request())
        self.assertEqual(state.cycles_started, 1)
        self.assertEqual(policy.next_step(state), "REVIEW")
        exhausted = reviewed(3, {"correctness"})
        with self.assertRaises(policy.PolicyViolation):
            policy.start_claude_cycle(exhausted)


class SupervisionPolicyRecordCycleCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "supervision_policy.py"), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def write(self, directory: Path, name: str, content: str) -> Path:
        path = directory / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def test_record_cycle_and_render_cycles_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "state": {"cycles_started": 1},
                "decision": {"model": "sonnet", "reasons": ["task-level model-only override: sonnet"]},
            }
            recorded = self.run_cli(
                "record-cycle", "--request", str(self.write(Path(tmp), "req.json", json.dumps(request)))
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            cycle_records = json.loads(recorded.stdout)["cycle_records"]
            self.assertEqual(cycle_records, [{"cycle": 1, "model": "sonnet", "reason": "task-level model-only override: sonnet"}])

            render_request = {"state": {"cycles_started": 1, "cycle_records": cycle_records}}
            rendered = self.run_cli(
                "render-cycles", "--request", str(self.write(Path(tmp), "render.json", json.dumps(render_request)))
            )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertIn("No escalation required.", rendered.stdout)

    def test_record_cycle_out_of_order_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = {"state": {"cycles_started": 2}, "decision": {"model": "opus", "reasons": ["escalation: x"]}}
            result = self.run_cli("record-cycle", "--request", str(self.write(Path(tmp), "req.json", json.dumps(request))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("must follow", result.stderr)

    def test_record_cycle_rejects_a_forged_prior_record(self) -> None:
        # Regression: cycles_started=2 with one prior record used to pass the old
        # length-only check even though that record was forged (wrong cycle number,
        # unsupported model). It must now be rejected before cycle 2 is appended.
        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "state": {
                    "cycles_started": 2,
                    "cycle_records": [{"cycle": 99, "model": "haiku", "reason": "fabricated"}],
                },
                "decision": {"model": "sonnet", "reasons": ["task-level model-only override: sonnet"]},
            }
            result = self.run_cli("record-cycle", "--request", str(self.write(Path(tmp), "req.json", json.dumps(request))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cycle history is not sequential", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_record_cycle_requires_a_recorded_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = {"state": {"cycles_started": 1}, "decision": {"model": "sonnet", "reasons": []}}
            result = self.run_cli("record-cycle", "--request", str(self.write(Path(tmp), "req.json", json.dumps(request))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("no recorded reason", result.stderr)

    def test_render_cycles_rejects_a_forged_history(self) -> None:
        # Regression: render-cycles used to print a forged prior record ({"cycle": 99,
        # "model": "haiku", "reason": "fabricated"}) followed by "No escalation
        # required." without validating it at all.
        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "state": {
                    "cycles_started": 1,
                    "cycle_records": [{"cycle": 99, "model": "haiku", "reason": "fabricated"}],
                }
            }
            result = self.run_cli("render-cycles", "--request", str(self.write(Path(tmp), "req.json", json.dumps(request))))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cycle history is not sequential", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_render_cycles_rejects_malformed_histories(self) -> None:
        good = [{"cycle": 1, "model": "sonnet", "reason": "task-level model-only override: sonnet"}]
        cases = {
            "duplicate cycle": (
                good + [{"cycle": 1, "model": "sonnet", "reason": "x"}],
                1,
                "cycle history is not sequential",
            ),
            "gap": (
                [{"cycle": 1, "model": "sonnet", "reason": "x"}, {"cycle": 3, "model": "sonnet", "reason": "x"}],
                1,
                "cycle history is not sequential",
            ),
            "unsupported model": (
                [{"cycle": 1, "model": "haiku", "reason": "x"}],
                1,
                "unsupported model in cycle 1 history",
            ),
            "missing reason": (
                [{"cycle": 1, "model": "sonnet", "reason": ""}],
                1,
                "cycle 1 is missing its recorded model reason",
            ),
            "too many cycles": (
                [
                    {"cycle": 1, "model": "sonnet", "reason": "x"},
                    {"cycle": 2, "model": "sonnet", "reason": "x"},
                    {"cycle": 3, "model": "sonnet", "reason": "x"},
                    {"cycle": 4, "model": "sonnet", "reason": "x"},
                ],
                4,
                "cycle history exceeds the maximum",
            ),
            "cycles_started mismatch": (good, 2, "does not match the recorded history"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (records, cycles_started, expected) in cases.items():
                with self.subTest(name=name):
                    request = {"state": {"cycles_started": cycles_started, "cycle_records": records}}
                    result = self.run_cli(
                        "render-cycles", "--request", str(self.write(Path(tmp), f"{name}.json", json.dumps(request)))
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stderr)
                    self.assertEqual(result.stdout, "")


class DeliveryOwnershipTests(unittest.TestCase):
    def test_codex_owns_task_branch_delivery(self) -> None:
        for action in ("stage", "commit", "push-task-branch", "create-pr"):
            with self.subTest(action=action):
                self.assertTrue(policy.may_perform("codex", action))
                self.assertFalse(policy.may_perform("claude", action))
                self.assertFalse(policy.may_perform("human", action))

    def test_only_a_human_approves_and_merges(self) -> None:
        for action in ("approve-pr", "merge-pr"):
            with self.subTest(action=action):
                self.assertTrue(policy.may_perform("human", action))
                self.assertFalse(policy.may_perform("codex", action))
                self.assertFalse(policy.may_perform("claude", action))

    def test_unlisted_dangerous_actions_are_permitted_to_nobody(self) -> None:
        for action in ("enable-auto-merge", "push-default-branch", "force-push", "bypass-branch-protection"):
            for actor in ("codex", "claude", "human"):
                self.assertFalse(policy.may_perform(actor, action), (actor, action))
        self.assertNotIn("claude", policy.DELIVERY_OWNERS.values())

    def test_claude_invocation_denies_its_delivery_actions(self) -> None:
        denied = run_claude.DENIED_TOOLS
        for rule in ("Bash(git add *)", "Bash(git commit *)", "Bash(git push *)", "Bash(gh pr create *)", "Bash(gh pr merge *)", "Bash(gh pr review *)"):
            self.assertIn(rule, denied)

    def test_delivery_requires_a_complete_validated_state(self) -> None:
        self.assertFalse(policy.delivery_allowed(policy.RunState()))
        self.assertFalse(policy.delivery_allowed(reviewed(1, {"formatting"})))
        self.assertTrue(policy.delivery_allowed(reviewed(1, ())))


class SupervisionPolicyCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "supervision_policy.py"), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def write(self, directory: Path, name: str, content: str) -> Path:
        path = directory / name
        path.write_bytes(content.encode("utf-8"))
        return path

    def test_check_remediation_reports_decision_and_exit_code(self) -> None:
        request = {
            "remediation": {"operation": "formatter", "paths": ["src/app.py"], "command_inspected": True,
                            "deterministic": True, "changes": ["formatting"]},
            "state": {"cycles_started": 3, "task_paths": ["src/app.py"], "unresolved": ["formatting"], "validated": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            allowed = self.run_cli("check-remediation", "--request", str(self.write(Path(tmp), "ok.json", json.dumps(request))))
            request["remediation"]["changes"] = ["formatting", "assertion"]
            denied = self.run_cli("check-remediation", "--request", str(self.write(Path(tmp), "no.json", json.dumps(request))))
            malformed = self.run_cli("check-remediation", "--request", str(self.write(Path(tmp), "bad.json", "{")))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout), {"allowed": True, "reasons": []})
        self.assertEqual(denied.returncode, 1)
        self.assertIn("forbidden change: assertion", json.loads(denied.stdout)["reasons"])
        self.assertEqual(malformed.returncode, 2)

    def test_select_model_reports_the_model_and_reasons(self) -> None:
        request = {
            "state": {"cycles_started": 2, "unresolved": ["correctness"], "validated": True},
            "model": {"reasoning_difficulty": True, "justification": JUSTIFICATION},
        }
        with tempfile.TemporaryDirectory() as tmp:
            escalated = self.run_cli("select-model", "--request", str(self.write(Path(tmp), "final.json", json.dumps(request))))
            request["state"]["unresolved"] = ["correctness", "timeout"]
            environment = self.run_cli("select-model", "--request", str(self.write(Path(tmp), "env.json", json.dumps(request))))
            request["model"] = {"override": "haiku"}
            unsupported = self.run_cli("select-model", "--request", str(self.write(Path(tmp), "bad.json", json.dumps(request))))
            request["model"] = {}
            request["state"]["cycles_started"] = 3
            exhausted = self.run_cli("select-model", "--request", str(self.write(Path(tmp), "four.json", json.dumps(request))))
        self.assertEqual(escalated.returncode, 0, escalated.stderr)
        self.assertEqual(json.loads(escalated.stdout), {"model": "opus", "reasons": [f"escalation: {JUSTIFICATION}"]})
        self.assertEqual(environment.returncode, 0, environment.stderr)
        self.assertEqual(json.loads(environment.stdout)["model"], "sonnet")
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("unsupported task-level model override: haiku", unsupported.stderr)
        self.assertEqual(unsupported.stdout, "")
        self.assertEqual(exhausted.returncode, 2)

    def test_check_normalization_compares_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            before = self.write(Path(tmp), "before", "a  \r\n")
            same = self.run_cli("check-normalization", str(before), str(self.write(Path(tmp), "same", "a\n")))
            changed = self.run_cli("check-normalization", str(before), str(self.write(Path(tmp), "changed", "b\n")))
        self.assertEqual(same.returncode, 0)
        self.assertEqual(changed.returncode, 1)


class SkillPhaseReferenceTests(unittest.TestCase):
    def test_skill_links_the_mechanical_remediation_phase_and_policy(self) -> None:
        skill = (ROOT / "codex/skills/supervise-claude/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("(references/mechanical-remediation.md)", skill)
        self.assertIn("scripts/supervision_policy.py", skill)

    def test_skill_links_the_model_selection_phase(self) -> None:
        skill = (ROOT / "codex/skills/supervise-claude/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("(references/model-selection.md)", skill)
        reference = (ROOT / "codex/skills/supervise-claude/references/model-selection.md").read_text(encoding="utf-8")
        self.assertIn("select-model", reference)

    def test_skill_requires_the_single_final_status_notification_not_native_turn_complete(self) -> None:
        skill = (ROOT / "codex/skills/supervise-claude/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("scripts/notify_final_status.py", skill)
        self.assertIn("exactly once", skill)
        self.assertIn("agent-turn-complete", skill)


class NotifyFinalStatusTests(unittest.TestCase):
    def fake_notifier(self, tmp: Path, log: Path) -> Path:
        script = tmp / "fake-agent-notify.sh"
        script.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$1" "$2" "$3" >> "' + str(log) + '"\n',
            encoding="utf-8",
        )
        script.chmod(0o700)
        return script

    def test_maps_each_status_to_the_documented_event_and_default_message(self) -> None:
        expected = {
            "COMPLETE": ("codex", "complete", "Task complete — PR ready"),
            "PARTIAL": ("codex", "attention", "Task needs attention"),
            "BLOCKED": ("codex", "blocked", "Task blocked"),
        }
        for status, lines in expected.items():
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                log = Path(tmp) / "log.txt"
                notifier = self.fake_notifier(Path(tmp), log)
                sent = notify_final_status.send_final_status(run_dir, status, notifier=notifier)
                self.assertTrue(sent)
                self.assertEqual(log.read_text(encoding="utf-8").splitlines(), list(lines))

    def test_concurrent_calls_for_the_same_run_directory_produce_exactly_one_notifier_call(self) -> None:
        # The marker claim uses an atomic exclusive-create at the OS level, so this is
        # deterministic regardless of thread scheduling: exactly one of many concurrent
        # callers ever wins, never zero and never more than one.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            log = Path(tmp) / "log.txt"
            notifier = self.fake_notifier(Path(tmp), log)
            results: list[bool] = []
            results_lock = threading.Lock()
            start = threading.Barrier(20)

            def attempt() -> None:
                start.wait()
                sent = notify_final_status.send_final_status(run_dir, "COMPLETE", notifier=notifier)
                with results_lock:
                    results.append(sent)

            threads = [threading.Thread(target=attempt) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 19)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["codex", "complete", "Task complete — PR ready"])

    def test_second_call_for_the_same_run_directory_never_sends_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            log = Path(tmp) / "log.txt"
            notifier = self.fake_notifier(Path(tmp), log)

            first = notify_final_status.send_final_status(run_dir, "COMPLETE", notifier=notifier)
            second = notify_final_status.send_final_status(run_dir, "BLOCKED", notifier=notifier)

            self.assertTrue(first)
            self.assertFalse(second)
            # Only the first call's single line trio is present; the second never ran.
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["codex", "complete", "Task complete — PR ready"])

    def test_a_custom_message_overrides_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            log = Path(tmp) / "log.txt"
            notifier = self.fake_notifier(Path(tmp), log)

            notify_final_status.send_final_status(run_dir, "PARTIAL", message="custom text", notifier=notifier)

            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["codex", "attention", "custom text"])

    def test_missing_notifier_is_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            missing = Path(tmp) / "does-not-exist.sh"

            sent = notify_final_status.send_final_status(run_dir, "BLOCKED", notifier=missing)

            self.assertTrue(sent)  # counted as this run's one attempt, even though nothing ran
            self.assertTrue((run_dir / notify_final_status.MARKER_NAME).exists())

    def test_a_failing_notifier_is_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            script = Path(tmp) / "failing.sh"
            script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            script.chmod(0o700)

            sent = notify_final_status.send_final_status(run_dir, "BLOCKED", notifier=script)

            self.assertTrue(sent)

    def test_unsupported_status_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                notify_final_status.send_final_status(Path(tmp) / "run", "UNKNOWN")

    def test_default_notifier_resolves_to_the_bundled_shared_script(self) -> None:
        self.assertEqual(notify_final_status.default_notifier(), ROOT / "scripts" / "agent-notify.sh")

    def test_cli_is_idempotent_across_repeated_finalization_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            log = Path(tmp) / "log.txt"
            notifier = self.fake_notifier(Path(tmp), log)
            argv = [
                "--run-dir", str(run_dir),
                "--status", "COMPLETE",
                "--notifier", str(notifier),
            ]

            first = notify_final_status.main(argv)
            second = notify_final_status.main(argv)

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["codex", "complete", "Task complete — PR ready"])


if __name__ == "__main__":
    unittest.main()
