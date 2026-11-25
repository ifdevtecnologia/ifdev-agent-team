"""Acceptance tests of the benchmark harness (issue #73, AC 1, 4-11, 16 and the runner part of 27) through its
command-line entry points, started as subprocesses from benchmark/ like the documented commands.

No test calls the model or Docker: `claude` and `docker` are the fakes of benchmark_support, and the
installer only ever runs against a temporary HOME.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from benchmark_support import (
    AUTH_ENV,
    AUTH_SENTINEL,
    BENCH_DIR,
    REPO_ROOT,
    SOLVE_SCRIPT,
    TASK_YAML,
    BenchTestCase,
    head_sha,
    make_task,
)

MODEL = "claude-test-model-1-0"
PLAN_LINE = re.compile(r"^\s*(\d+)\s+(\S+)\s+task=(\S+) condition=(\S+) repetition=(\d+)$")
RESULT_FILES = ("meta.json", "stream.jsonl", "final.patch", "eval.json")


def bench_cli(module: str, *args: str) -> subprocess.CompletedProcess:
    """`cd benchmark && python3 -m bench.<module> ...` with the test's (patched) environment."""
    return subprocess.run(
        [sys.executable, "-m", f"bench.{module}", *args],
        cwd=BENCH_DIR,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=900,
    )


def plan_of(stdout: str) -> list[tuple[int, str, str, str, int]]:
    rows = []

    for line in stdout.splitlines():
        match = PLAN_LINE.match(line)

        if match:
            order, run_id, task, condition, repetition = match.groups()
            rows.append((int(order), run_id, task, condition, int(repetition)))

    return rows


class CheckTaskCliTests(BenchTestCase):
    """AC 4, 5 and 6 through `python3 -m bench.check_task <task_dir>`."""

    def check(self, *task_dirs: Path) -> subprocess.CompletedProcess:
        return bench_cli("check_task", *(str(path) for path in task_dirs))

    def test_ac5_valid_task_exits_zero(self) -> None:
        result = self.check(make_task(self.tasks_dir, "T01"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok   ", result.stdout)
        self.assertTrue(self.docker_calls())

    def test_ac4_invalid_task_yaml_exits_nonzero_naming_the_field(self) -> None:
        base = TASK_YAML.format(task_id="T01")
        cases = {
            "missing": (base.replace("stack: shell\n", ""), "'stack'"),
            "wrong-type": (base.replace("criteria:\n  - T01-C1\n  - T01-C2\n", "criteria: T01-C1\n"), "'criteria'"),
            "unknown-key": (base + "extra_field: x\n", "'extra_field'"),
            "absolute-protected": (base.replace("protected_paths:\n  - tests\n", "protected_paths:\n  - /etc\n"),
                                   "protected_paths"),
            "parent-expected": (base.replace("expected_files:\n  - app.txt\n", "expected_files:\n  - ../app.txt\n"),
                                "expected_files"),
        }

        for name, (yaml_text, field) in cases.items():
            with self.subTest(case=name):
                tasks_dir = self.tmp / f"tasks-{name}"
                tasks_dir.mkdir()
                task = make_task(tasks_dir, "T01", yaml_text=yaml_text)

                result = self.check(task)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("FAIL", result.stdout)
                self.assertIn(field, result.stdout)

    def test_ac5_hidden_test_passing_on_snapshot_or_failing_on_reference_exits_nonzero(self) -> None:
        snapshot_passes = make_task(self.tasks_dir, "T01")
        (snapshot_passes / "snapshot" / "NOTE.txt").write_text("already there\n")
        reference_fails = make_task(self.tasks_dir, "T02")
        (reference_fails / "reference" / "app.txt").write_text("still broken\n")

        result = self.check(snapshot_passes, reference_fails)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("hidden test 'test_note.sh' passes on the snapshot", result.stdout)
        self.assertIn("hidden test 'test_fixed.sh' fails on the reference solution", result.stdout)

    def test_ac6_unmapped_hidden_file_or_criterion_without_test_exits_nonzero(self) -> None:
        unmapped = make_task(self.tasks_dir, "T01")
        (unmapped / "hidden" / "test_extra.sh").write_text("true\n")
        uncovered = make_task(
            self.tasks_dir, "T02", yaml_text=TASK_YAML.format(task_id="T02").replace("  - T02-C2\n", "  - T02-C2\n  - T02-C3\n")
        )

        result = self.check(unmapped, uncovered)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("hidden test 'test_extra.sh' is not mapped to a criterion", result.stdout)
        self.assertIn("criterion 'T02-C3' has no hidden test", result.stdout)


class RunnerCliTests(BenchTestCase):
    """AC 7-11, 16 and the runner part of 27 through `python3 -m bench.runner`, with a multi-task, multi-repetition round."""

    def setUp(self) -> None:
        super().setUp()
        make_task(self.tasks_dir, "T01")
        make_task(self.tasks_dir, "T02")
        self.prompts = self.tmp / "prompts"
        self.prompts.mkdir()
        (self.prompts / "project-claude.md").write_text("# Project\nSynthetic shared project file.\n")
        (self.prompts / "prompt-template.md").write_text("Task:\n{{TASK_PROMPT}}\n")
        (self.prompts / "C-light.txt").write_text("Use light mode.\n")

    def args(self, *extra: str, runs_dir: Path | None = None) -> list[str]:
        return [
            "--round", "harness", "--round-id", "acceptance", "--tasks", "T01,T02", "--tasks-dir", str(self.tasks_dir),
            "--runs-dir", str(runs_dir or self.runs_dir), "--results-dir", str(self.results_dir),
            "--conditions", "A,B", "--repetitions", "2", "--seed", "1234", "--model", MODEL, "--effort", "high",
            "--run-cap-usd", "15", "--round-budget-usd", "200", "--toolkit-sha", head_sha(), "--auth-env", AUTH_ENV,
            "--project-claude", str(self.prompts / "project-claude.md"),
            "--prompt-template", str(self.prompts / "prompt-template.md"),
            "--light-line", str(self.prompts / "C-light.txt"),
            *extra,
        ]

    def agent_run_ids(self) -> list[str]:
        """Run IDs in the order the fake `claude` was started (RUN_ROOT is `<run_id>-<random>`)."""
        return [Path(call["env"]["HOME"]).parent.name.split("-", 1)[0] for call in self.claude_calls()]

    def test_ac7_ac8_dry_run_plan_is_reproducible_interleaved_and_starts_nothing(self) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"

        first = bench_cli("runner", *self.args())
        second = bench_cli("runner", *self.args())
        other_seed = bench_cli("runner", *self.args("--seed", "99"))

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        plan = plan_of(first.stdout)
        self.assertEqual([row[0] for row in plan], list(range(1, 9)))
        self.assertEqual(len({row[1] for row in plan}), 8)
        self.assertNotEqual([row[1] for row in plan], [row[1] for row in plan_of(other_seed.stdout)])

        for start in range(0, 8, 2):
            block = plan[start : start + 2]
            self.assertEqual({row[3] for row in block}, {"A", "B"})
            self.assertEqual(len({(row[2], row[4]) for row in block}), 1)

        self.assertEqual(first.stdout.count("claude -p --model " + MODEL), 8)
        self.assertIn("seed 1234", first.stdout)
        self.assertEqual(self.docker_calls(), [])
        self.assertEqual(self.claude_calls(), [])
        self.assertFalse(self.runs_dir.exists())
        self.assertFalse(self.results_dir.exists())

    def test_ac9_refused_round_exits_nonzero_and_starts_nothing(self) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"
        os.environ[AUTH_ENV] = AUTH_SENTINEL

        for name, extra, message in (
            ("over-budget", ("--round-budget-usd", "119"), "8 runs x US$15 exceed the round budget of US$119"),
            ("alias", ("--model", "sonnet"), "full model ID"),
            ("no-auth", ("--execute", "--auth-env", "BENCH_TEST_UNSET_VARIABLE"), "BENCH_TEST_UNSET_VARIABLE is not set"),
        ):
            with self.subTest(case=name):
                result = bench_cli("runner", *self.args(*extra))

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.docker_calls(), [])
                self.assertFalse(self.results_dir.exists())

    def test_ac11_runs_dir_inside_a_git_repository_is_refused(self) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        repo = self.tmp / "some-repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        result = bench_cli("runner", *self.args("--execute", runs_dir=repo / "runs"))

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("outside any Git repository", result.stderr)
        self.assertFalse((repo / "runs").exists())
        self.assertEqual(self.docker_calls(), [])

    def test_ac8_ac10_ac16_ac27_executed_round_follows_the_plan_writes_every_run_and_resumes(self) -> None:
        os.environ["FAKE_DOCKER_FORBID"] = "1"
        dry = bench_cli("runner", *self.args())
        planned = [row[1] for row in plan_of(dry.stdout)]
        os.environ.pop("FAKE_DOCKER_FORBID")
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        os.environ["FAKE_CLAUDE_SCRIPT"] = SOLVE_SCRIPT

        result = bench_cli("runner", *self.args("--execute"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.agent_run_ids(), planned)
        self.assertNotIn(AUTH_SENTINEL, result.stdout + result.stderr)
        round_dir = self.results_dir / "acceptance"
        self.assertEqual(json.loads((round_dir / "round.json").read_text())["seed"], 1234)

        for order, run_id in enumerate(planned, start=1):
            with self.subTest(run_id=run_id):
                run_dir = round_dir / run_id

                for name in RESULT_FILES:
                    self.assertTrue((run_dir / name).is_file(), name)

                meta = json.loads((run_dir / "meta.json").read_text())
                self.assertEqual((meta["status"], meta["order"], meta["seed"]), ("finished", order, 1234))
                self.assertTrue(meta["o1_success"], meta)

                for path in run_dir.iterdir():
                    self.assertNotIn(AUTH_SENTINEL.encode(), path.read_bytes(), path)

        self.assertNotIn(AUTH_SENTINEL, self.docker_log.read_text())
        calls = len(self.claude_calls())

        resumed = bench_cli("runner", *self.args("--execute"))

        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(resumed.stdout.count("(already finished)"), 8)
        self.assertEqual(len(self.claude_calls()), calls)


class NotManagedTests(unittest.TestCase):
    """AC 1: installing every target from a checkout that contains benchmark/ never links or copies it."""

    def test_ac1_install_all_targets_never_points_into_benchmark(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="bench-not-managed-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo, home = tmp / "repo", tmp / "home"
        home.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", "results")

        for name in ("scripts", "core", "dist", "targets", "claude", "codex", "benchmark"):
            shutil.copytree(REPO_ROOT / name, repo / name, symlinks=True, ignore=ignore)

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
        result = subprocess.run(["bash", str(repo / "scripts" / "install.sh"), "--target", "all"], env=env, cwd=tmp,
                                capture_output=True, text=True, timeout=120)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = [p for p in home.rglob("*") if p.is_symlink() or p.is_file()]
        links = [p for p in installed if p.is_symlink()]
        self.assertTrue(links)
        benchmark = repo / "benchmark"
        self.assertEqual([p for p in links if Path(os.path.realpath(p)).is_relative_to(benchmark)], [])
        harness_files = {p.read_bytes() for p in benchmark.rglob("*") if p.is_file() and p.stat().st_size}
        copies = [p for p in installed if not p.is_symlink() and p.read_bytes() in harness_files]
        self.assertEqual(copies, [])


if __name__ == "__main__":
    unittest.main()
