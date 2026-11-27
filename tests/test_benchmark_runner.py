"""Tests for the benchmark runner (benchmark/bench/runner.py) with fake `docker` and `claude`.

No test calls the model: `claude` and `docker` are fakes generated in a temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

from benchmark_support import AUTH_ENV, AUTH_SENTINEL, BENCH_DIR, SOLVE_SCRIPT, BenchTestCase, head_sha, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench import runner  # noqa: E402
from bench.containers import ContainerError, ContainerSpec, host_user, run_container  # noqa: E402

MODEL = "claude-test-model-1-0"


class RunnerTestCase(BenchTestCase):
    def setUp(self) -> None:
        super().setUp()
        make_task(self.tasks_dir, "T01")
        self.prompts = self.tmp / "prompts"
        self.prompts.mkdir()
        (self.prompts / "project-claude.md").write_text("# Project\nShared by A, B and C.\n")
        (self.prompts / "prompt-template.md").write_text("Task:\n{{TASK_PROMPT}}\n")
        (self.prompts / "C-light.txt").write_text("Use light mode.\n")

    def args(self, *extra: str, conditions: str = "A,B", prompts: bool = True) -> list[str]:
        argv = [
            "--round", "harness", "--tasks", "T01", "--tasks-dir", str(self.tasks_dir), "--runs-dir", str(self.runs_dir),
            "--results-dir", str(self.results_dir), "--conditions", conditions, "--seed", "7", "--model", MODEL,
            "--effort", "high", "--run-cap-usd", "15", "--round-budget-usd", "100", "--toolkit-sha", head_sha(),
            "--auth-env", AUTH_ENV,
        ]

        if prompts:
            argv += [
                "--project-claude", str(self.prompts / "project-claude.md"),
                "--prompt-template", str(self.prompts / "prompt-template.md"),
                "--light-line", str(self.prompts / "C-light.txt"),
            ]

        return argv + list(extra)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main(argv)

        return code, out.getvalue(), err.getvalue()

    def execute(self, *extra: str, conditions: str = "A,B") -> tuple[int, str, str]:
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        os.environ.setdefault("FAKE_CLAUDE_SCRIPT", SOLVE_SCRIPT)

        return self.run_main(self.args("--execute", *extra, conditions=conditions))

    def meta(self, run_id: str) -> dict:
        return json.loads((self.results_dir / "harness" / run_id / "meta.json").read_text())

    def agent_calls(self) -> list[list[str]]:
        return [argv for argv in self.docker_calls() if "claude" in argv]


class PlanTests(RunnerTestCase):
    def test_same_seed_gives_same_plan_and_conditions_are_interleaved(self) -> None:
        plan = runner.build_plan(["T01", "T02", "T03"], ["A", "B", "C"], 2, seed=11)

        self.assertEqual(plan, runner.build_plan(["T01", "T02", "T03"], ["A", "B", "C"], 2, seed=11))
        self.assertNotEqual(plan, runner.build_plan(["T01", "T02", "T03"], ["A", "B", "C"], 2, seed=12))
        self.assertEqual(len(plan), 18)
        self.assertEqual(len({r.run_id for r in plan}), 18)
        self.assertEqual([r.order for r in plan], list(range(1, 19)))

        for start in range(0, 18, 3):
            block = plan[start : start + 3]
            self.assertEqual({r.condition for r in block}, {"A", "B", "C"})
            self.assertEqual(len({(r.task_id, r.repetition) for r in block}), 1)

    def test_dry_run_prints_plan_and_commands_without_calling_docker_or_claude(self) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"

        code, out, err = self.run_main(self.args())

        self.assertEqual(code, 0, err)
        self.assertIn("seed 7", out)
        self.assertIn("T01_A_r1", out)
        self.assertIn("T01_B_r1", out)
        self.assertIn("claude -p --model " + MODEL, out)
        self.assertIn("dry run: nothing was started", out)
        self.assertEqual(self.docker_calls(), [])
        self.assertEqual(self.claude_calls(), [])
        self.assertFalse(self.runs_dir.exists())
        self.assertFalse(self.results_dir.exists())

    def test_model_ids_and_aliases(self) -> None:
        for model in ("claude-test-model-1-0", "claude-opus-5-5", "claude-sonnet-4-5-20250929"):
            self.assertTrue(runner.valid_model_id(model), model)

        for model in (None, "", "opus", "sonnet", "claude-opus-latest", "claude-opus", "gpt-5"):
            self.assertFalse(runner.valid_model_id(model), model)


class GuardTests(RunnerTestCase):
    def assert_refused(self, argv: list[str], message: str) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"
        code, out, err = self.run_main(argv)

        self.assertEqual(code, 2, out + err)
        self.assertIn(message, err)
        self.assertEqual(self.docker_calls(), [])
        self.assertEqual(self.claude_calls(), [])
        self.assertFalse(self.results_dir.exists())

    def without(self, argv: list[str], option: str) -> list[str]:
        index = argv.index(option)

        return argv[:index] + argv[index + 2 :]

    def test_budget_guards(self) -> None:
        self.assert_refused(self.args("--round-budget-usd", "29"), "2 runs x US$15 exceed the round budget of US$29")
        self.assert_refused(self.without(self.args(), "--run-cap-usd"), "--run-cap-usd and --round-budget-usd are required")
        self.assert_refused(self.without(self.args(), "--round-budget-usd"), "--run-cap-usd and --round-budget-usd are required")
        self.assert_refused(self.args("--run-cap-usd", "0"), "must be finite and the run cap positive")

        for option in ("--run-cap-usd", "--round-budget-usd"):
            for value in ("nan", "inf"):
                with self.subTest(option=option, value=value):
                    self.assert_refused(self.args(option, value), "must be finite")

        self.assert_refused(self.args("--time-limit-minutes", "nan"), "--time-limit-minutes positive and finite")
        self.assert_refused(self.args("--command-timeout", "inf"), "--command-timeout must be positive and finite")
        self.assert_refused(self.args("--tasks", "T01,T01"), "must not repeat a task ID")
        self.assert_refused(self.args("--network", "container:other"), "must not share another container's network")

    def test_model_effort_and_option_guards(self) -> None:
        self.assert_refused(self.args("--model", "opus"), "full model ID")
        self.assert_refused(self.without(self.args(), "--effort"), "--effort is required")
        self.assert_refused(self.args("--conditions", "A,D"), "--conditions must be distinct")
        self.assert_refused(self.args("--repetitions", "0"), "--repetitions must be at least 1")
        self.assert_refused(self.args("--auth-env", "bad name"), "--auth-env must be")
        self.assert_refused(self.args("--round-id", "../x"), "--round-id may only contain")

    def test_condition_c_needs_the_light_mode_definition(self) -> None:
        self.assert_refused(self.args("--light-line", str(runner.BENCH_DIR / "conditions" / "C-light.txt"), conditions="A,B,C"), "OD-1")

    def test_pilot_and_evaluation_need_plan_content_and_restricted_network(self) -> None:
        for kind in runner.RESTRICTED_ROUNDS:
            with self.subTest(kind=kind):
                self.assert_refused(self.args("--round", kind), "egress allowlist")
                self.assert_refused(self.args("--round", kind, "--network", "bench-egress", prompts=False), "placeholder")

        os.environ.pop("FAKE_DOCKER_FORBID")
        code, _, err = self.run_main(self.args("--round", "pilot", "--network", "bench-egress"))
        self.assertEqual(code, 0, err)

    def test_execute_needs_the_auth_variable_and_a_safe_runs_dir(self) -> None:
        self.assert_refused(self.args("--execute"), f"the API key variable {AUTH_ENV} is not set")
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        self.assert_refused(self.args("--execute", "--runs-dir", str(self.home / "runs")), "outside the real home")

    def test_toolkit_and_task_guards(self) -> None:
        self.assert_refused(self.without(self.args(), "--toolkit-sha"), "--toolkit-sha is required")
        self.assert_refused(self.args("--toolkit-sha", "0" * 40), "is not a commit")
        self.assert_refused(self.args("--tasks", "T09"), "T09: ")
        self.assert_refused(self.args("--tasks", "../T01"), "invalid task ID")
        self.assert_refused(self.args("--tasks-dir", str(self.tmp / "missing")), "BENCH_TASKS_DIR")
        (self.prompts / "prompt-template.md").write_text("no token\n")
        self.assert_refused(self.args(), "must contain {{TASK_PROMPT}}")


class ExecuteTests(RunnerTestCase):
    def test_conditions_a_b_c_run_end_to_end_with_fakes(self) -> None:
        code, out, err = self.execute(conditions="A,B,C")

        self.assertEqual(code, 0, out + err)

        for condition in "ABC":
            run_id = f"T01_{condition}_r1"
            run_dir = self.results_dir / "harness" / run_id
            meta = self.meta(run_id)

            with self.subTest(condition=condition):
                self.assertEqual(meta["status"], "finished")
                self.assertTrue(meta["o1_success"], meta)
                self.assertEqual((meta["hidden_passed"], meta["hidden_total"]), (2, 2))
                self.assertEqual(meta["seed"], 7)
                self.assertEqual(meta["toolkit_sha"], None if condition == "A" else head_sha())
                self.assertEqual(meta["stream"]["total_cost_usd"], 0.42)
                self.assertEqual(meta["stream"]["subagent_calls"], {"reviewer": 1})
                self.assertFalse(meta["committed"])

                for name in ("meta.json", "stream.jsonl", "final.patch", "eval.json"):
                    self.assertTrue((run_dir / name).is_file(), name)

                self.assertIn(b"+fixed", (run_dir / "final.patch").read_bytes())
                self.assertEqual(json.loads((run_dir / "eval.json").read_text())["criteria"], {"T01-C1": True, "T01-C2": True})

        prompts = {call["env"]["HOME"]: call["prompt"] for call in self.claude_calls()}
        self.assertEqual(len(prompts), 3)
        light = [p for p in prompts.values() if p.rstrip().endswith("Use light mode.")]
        self.assertEqual(len(light), 1)
        base = [p for p in prompts.values() if p not in light]
        self.assertEqual(base[0], base[1])
        self.assertEqual(light[0], base[0].rstrip("\n") + "\n\nUse light mode.\n")

    def test_agent_container_mounts_only_the_run_root_and_passes_the_key_by_name(self) -> None:
        code, out, err = self.execute()
        self.assertEqual(code, 0, out + err)

        for argv in self.agent_calls():
            run_root = argv[argv.index("-w") + 1].rsplit("/work", 1)[0]
            self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a in ("--mount", "-v")],
                             [f"type=bind,source={run_root},target={run_root}"])
            self.assertFalse(argv[argv.index("--user") + 1].startswith("0:"))
            env = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
            run_home = Path(run_root, "home")
            self.assertEqual(env, [
                f"HOME={run_home}", f"CLAUDE_CONFIG_DIR={run_home / '.claude'}", "IFDEV_AGENT_NOTIFY=0",
                "DISABLE_AUTOUPDATER=1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1", AUTH_ENV,
            ])
            joined = " ".join(argv)
            for forbidden in ("hidden", "reference", str(self.tasks_dir), "docker.sock", str(self.home)):
                self.assertNotIn(forbidden, joined)

            command = argv[argv.index("timeout"):]
            self.assertEqual(command[:4], ["timeout", "3600", "claude", "-p"])
            for flag, value in (("--model", MODEL), ("--effort", "high"), ("--output-format", "stream-json"),
                                ("--max-budget-usd", "15"), ("--permission-mode", "acceptEdits"),
                                ("--permission-prompts", "none"), ("--allowedTools", "Read Edit Write Glob Grep Bash Agent"),
                                ("--disallowedTools", "WebFetch WebSearch")):
                self.assertEqual(command[command.index(flag) + 1], value, flag)
            self.assertIn("--verbose", command)
            self.assertIn("--strict-mcp-config", command)
            session = command[command.index("--session-id") + 1]
            self.assertIn(session, {self.meta(f"T01_{c}_r1")["run_uuid"] for c in "AB"})

        for call in self.claude_calls():
            self.assertEqual(call["env"]["IFDEV_AGENT_NOTIFY"], "0")
            self.assertTrue(call["cwd"].endswith("/work"))

    def test_auth_value_never_reaches_arguments_metadata_or_logs(self) -> None:
        code, out, err = self.execute()

        self.assertEqual(code, 0, out + err)
        self.assertNotIn(AUTH_SENTINEL, out + err)
        self.assertFalse(self.meta("T01_A_r1")["auth_value_redacted"])

        self.assertNotIn(AUTH_SENTINEL, self.docker_log.read_text())
        written = [p for p in self.results_dir.rglob("*") if p.is_file()]
        self.assertTrue(written)

        for path in written:
            self.assertNotIn(AUTH_SENTINEL.encode(), path.read_bytes(), path)

    def test_resume_skips_finished_runs_and_retries_errors(self) -> None:
        self.execute()
        first = len(self.agent_calls())
        meta = self.meta("T01_A_r1")
        meta["status"] = "error"
        (self.results_dir / "harness" / "T01_A_r1" / "meta.json").write_text(json.dumps(meta))

        code, out, _ = self.execute()

        self.assertEqual(code, 0)
        self.assertIn("skip T01_B_r1 (already finished)", out)
        self.assertEqual(len(self.agent_calls()), first + 1)
        self.assertEqual(self.meta("T01_A_r1")["status"], "finished")

    def test_round_parameters_are_pinned_and_the_seed_is_reused_on_resume(self) -> None:
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        os.environ["FAKE_CLAUDE_SCRIPT"] = SOLVE_SCRIPT
        argv = [a for a in self.args("--execute") if a != "7"]
        argv.remove("--seed")
        code, out, err = self.run_main(argv)
        self.assertEqual(code, 0, out + err)
        recorded = json.loads((self.results_dir / "harness" / "round.json").read_text())

        code, out, _ = self.run_main(argv)

        self.assertEqual(code, 0)
        self.assertIn(f"seed {recorded['seed']}", out)
        self.assertEqual(recorded["model"], MODEL)

        for option, value in (("--model", "claude-other-model-2-0"), ("--seed", "8"), ("--effort", "low")):
            with self.subTest(option=option):
                code, _, err = self.run_main(self.args("--execute", option, value))
                self.assertEqual(code, 2)
                self.assertIn("was started with other parameters", err)

    def test_auth_value_printed_by_the_agent_is_redacted(self) -> None:
        os.environ["FAKE_CLAUDE_MODE"] = "leak"
        os.environ["FAKE_CLAUDE_SCRIPT"] = f'{SOLVE_SCRIPT} && echo "$BENCH_TEST_AUTH" > LEAK.txt'

        code, out, err = self.execute(conditions="A")

        self.assertEqual(code, 0, out + err)
        self.assertNotIn(AUTH_SENTINEL, out + err)
        self.assertTrue(self.meta("T01_A_r1")["auth_value_redacted"])

        for path in (self.results_dir / "harness" / "T01_A_r1").iterdir():
            self.assertNotIn(AUTH_SENTINEL.encode(), path.read_bytes(), path)

        self.assertIn(b"REDACTED", (self.results_dir / "harness" / "T01_A_r1" / "final.patch").read_bytes())

    def test_malformed_stream_is_flagged_and_the_round_continues(self) -> None:
        os.environ["FAKE_CLAUDE_MODE"] = "garbage"

        code, out, err = self.execute()

        self.assertEqual(code, 0, out + err)

        for condition in "AB":
            meta = self.meta(f"T01_{condition}_r1")
            self.assertEqual(meta["status"], "finished")
            self.assertEqual(meta["stream"]["malformed_lines"], 1)
            self.assertTrue(meta["o1_success"])

    def test_container_not_stopped_is_final_not_retried_nor_evaluated(self) -> None:
        def stuck(*args, **kwargs):
            raise runner.ContainerNotStopped("simulated")

        with mock.patch.object(runner, "_agent_run", stuck):
            code, out, err = self.execute(conditions="A")
            self.execute(conditions="A")

        meta = self.meta("T01_A_r1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((meta["status"], meta["evaluation_error"], meta["o1_success"]), ("finished", "container_not_stopped", False))
        self.assertFalse((self.results_dir / "harness" / "T01_A_r1" / "eval.json").exists())

    def test_evaluation_failure_after_the_agent_is_final_not_retried(self) -> None:
        def failing_evaluate(*args, **kwargs):
            raise OSError("simulated")

        with mock.patch.object(runner, "evaluate_run", failing_evaluate):
            code, out, err = self.execute(conditions="A")

        meta = self.meta("T01_A_r1")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((meta["status"], meta["evaluation_error"], meta["o1_success"]), ("finished", "OSError", False))
        self.assertFalse(meta["timed_out"] or meta["budget_capped"] or meta["no_result"])
        calls = len(self.agent_calls())
        self.execute(conditions="A")
        self.assertEqual(len(self.agent_calls()), calls)

    def test_timeout_budget_cap_missing_result_and_blocked_count_as_failures(self) -> None:
        cases = {
            "sleep": ("timed_out", ["--time-limit-minutes", "0.02"]),
            "budget": ("budget_capped", []),
            "noresult": ("no_result", []),
            "blocked": ("ended_blocked", []),
        }

        for mode, (flag, extra) in cases.items():
            with self.subTest(mode=mode):
                os.environ["FAKE_CLAUDE_MODE"] = mode
                round_id = f"r-{mode}"
                code, out, err = self.execute("--round-id", round_id, *extra, conditions="A")
                meta = json.loads((self.results_dir / round_id / "T01_A_r1" / "meta.json").read_text())

                self.assertEqual(code, 0, out + err)
                self.assertEqual(meta["status"], "finished")
                self.assertTrue(meta[flag], meta)
                self.assertFalse(meta["o1_success"])

    def test_empty_diff_counts_as_failure(self) -> None:
        os.environ["FAKE_CLAUDE_SCRIPT"] = "true"

        self.execute(conditions="A")

        meta = self.meta("T01_A_r1")
        self.assertTrue(meta["empty_diff"])
        self.assertFalse(meta["o1_success"])
        self.assertEqual(meta["hidden_passed"], 0)

    def test_harness_error_is_recorded_and_returns_nonzero(self) -> None:
        def failing_prepare(*args, **kwargs):
            raise runner.IsolationError("simulated")

        with mock.patch.object(runner, "prepare_run", failing_prepare):
            code, out, _ = self.execute()

        self.assertEqual(code, 1)
        self.assertEqual(self.meta("T01_A_r1")["status"], "error")
        self.assertEqual(self.meta("T01_A_r1")["error"], "IsolationError")
        self.assertIn("error", out)
        self.assertEqual(self.agent_calls(), [])

    def test_compose_prompt_keeps_template_and_appends_light_line_only_for_c(self) -> None:
        template = "Before\n{{TASK_PROMPT}}\nAfter\n"

        self.assertEqual(runner.compose_prompt(template, "Do X.\n", "A", "Light."), "Before\nDo X.\nAfter\n")
        self.assertEqual(runner.compose_prompt(template, "Do X.\n", "C", "Light.\n"), "Before\nDo X.\nAfter\n\nLight.\n")

    def test_default_prompt_files_are_placeholders(self) -> None:
        for name in ("project-claude.md", "prompt-template.md", "conditions/C-light.txt"):
            self.assertTrue(runner.is_placeholder(runner.BENCH_DIR / name), name)

        self.assertTrue(runner.is_placeholder(Path(self.tmp / "missing.md")))


class ContainerTests(RunnerTestCase):
    def test_container_still_running_after_timeout_is_an_error(self) -> None:
        spec = ContainerSpec(image="img", mount=self.tmp, workdir=self.tmp, network="none", command=("sleep", "5"), name="bench-x")
        os.environ["FAKE_DOCKER_INSPECT_PRESENT"] = "1"

        with mock.patch("bench.containers.time.sleep"), self.assertRaises(ContainerError):
            run_container(spec, 0.3)

        self.assertIn(["kill", "bench-x"], self.docker_calls())
        os.environ.pop("FAKE_DOCKER_INSPECT_PRESENT")
        self.assertEqual(run_container(spec, 0.3), (124, True))

    def test_root_user_is_refused(self) -> None:
        with mock.patch("bench.containers.os.getuid", return_value=0), self.assertRaises(ContainerError):
            host_user()
