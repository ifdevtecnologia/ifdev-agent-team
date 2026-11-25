"""Tests for benchmark/bench/check_task.py with a fake `docker` that runs the checks locally."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest

from benchmark_support import BENCH_DIR, BenchTestCase, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench import check_task  # noqa: E402


class CheckTaskTests(BenchTestCase):
    def test_valid_task_passes_and_runs_checks_in_evaluator_containers(self) -> None:
        task = make_task(self.tasks_dir)

        self.assertEqual(check_task.check_task(task), [])
        calls = self.docker_calls()
        # snapshot and reference: pre-existing suite plus two hidden tests each.
        self.assertEqual(len(calls), 6)

        for argv in calls:
            self.assertEqual(argv[argv.index("--network") + 1], "none")
            self.assertIn("bench-shell:test", argv)

    def test_hidden_test_passing_on_the_snapshot_fails_the_task(self) -> None:
        task = make_task(self.tasks_dir)
        (task / "snapshot" / "NOTE.txt").write_text("already there\n")

        problems = check_task.check_task(task)

        self.assertEqual(problems, ["hidden test 'test_note.sh' passes on the snapshot (it must fail before the fix)"])

    def test_hidden_test_failing_on_the_reference_fails_the_task(self) -> None:
        task = make_task(self.tasks_dir)
        (task / "reference" / "app.txt").write_text("still broken\n")

        self.assertEqual(check_task.check_task(task), ["hidden test 'test_fixed.sh' fails on the reference solution"])

    def test_reference_breaking_the_suite_or_build_fails_the_task(self) -> None:
        task = make_task(self.tasks_dir)
        yaml_path = task / "task.yaml"
        yaml_path.write_text(yaml_path.read_text().replace("  hidden: sh {test}\n", "  hidden: sh {test}\n  build: test ! -f BREAK\n"))
        (task / "reference" / "BREAK").write_text("x\n")

        self.assertEqual(check_task.check_task(task), ["the build fails on the reference solution"])
        (task / "reference" / "BREAK").unlink()
        (task / "snapshot" / "tests" / "check.sh").write_text("grep -q broken app.txt\n")

        self.assertEqual(check_task.check_task(task), ["the pre-existing suite fails on the reference solution"])

    def test_unmapped_hidden_file_and_criterion_without_test_fail_without_running_checks(self) -> None:
        task = make_task(self.tasks_dir)
        (task / "hidden" / "test_extra.sh").write_text("true\n")
        yaml_path = task / "task.yaml"
        yaml_path.write_text(yaml_path.read_text().replace("  test_note.sh: T01-C2\n", ""))
        (task / "hidden" / "test_note.sh").unlink()

        problems = check_task.check_task(task)

        self.assertEqual(
            problems,
            ["hidden test 'test_extra.sh' is not mapped to a criterion in hidden_tests", "criterion 'T01-C2' has no hidden test"],
        )
        self.assertEqual(self.docker_calls(), [])

    def test_mapped_file_missing_and_missing_directories_are_reported(self) -> None:
        task = make_task(self.tasks_dir)
        (task / "hidden" / "test_note.sh").unlink()
        (task / "prompt.md").unlink()

        problems = check_task.check_task(task)

        self.assertIn("missing prompt.md", problems)
        self.assertIn("hidden_tests lists 'test_note.sh', which does not exist in hidden/", problems)

    def test_main_exit_status_and_output(self) -> None:
        good = make_task(self.tasks_dir, "T01")
        bad = make_task(self.tasks_dir, "T02")
        (bad / "task.yaml").write_text("id: T02\n")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            self.assertEqual(check_task.main([str(good)]), 0)
            self.assertEqual(check_task.main([str(good), str(bad)]), 1)

        self.assertIn("ok   ", output.getvalue())
        self.assertIn("FAIL ", output.getvalue())
        self.assertIn("missing required field", output.getvalue())


if __name__ == "__main__":
    unittest.main()
