"""Deterministic tests for supervised Claude permissions and the supervision policy."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
