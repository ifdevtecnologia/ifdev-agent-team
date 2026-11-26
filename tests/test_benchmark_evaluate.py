"""Tests for the evaluation step of the benchmark harness (benchmark/bench/evaluate.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from benchmark_support import BENCH_DIR, REPO_ROOT, BenchTestCase, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench import evaluate, isolate  # noqa: E402
from bench.gitops import isolated_git_env  # noqa: E402
from bench.taskspec import load_task  # noqa: E402

PROJECT_CLAUDE = REPO_ROOT / "benchmark" / "project-claude.md"


class EvaluateTestCase(BenchTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()
        self.run_root, self.base = isolate.prepare_run(self.runs_dir, "T01_A_r1", self.task, "A", PROJECT_CLAUDE, REPO_ROOT, None)
        self.work = self.run_root / "work"
        self.out = self.tmp / "out"
        self.out.mkdir()

    def agent_git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.work, env=isolated_git_env(self.run_root / "home"), check=True, capture_output=True)

    def evaluate(self) -> dict:
        return evaluate.evaluate_run(self.task, self.run_root, self.out, PROJECT_CLAUDE, self.base, 60, self.runs_dir)


class PatchTests(EvaluateTestCase):
    def test_patch_has_committed_uncommitted_and_untracked_changes(self) -> None:
        (self.work / "app.txt").write_text("fixed\n")
        self.agent_git("commit", "-qam", "fix")
        (self.work / "NOTE.txt").write_text("note\n")
        (self.work / "tests" / "check.sh").write_text("true\n")

        result = self.evaluate()

        patch = (self.out / "final.patch").read_bytes()
        self.assertIn(b"+fixed", patch)
        self.assertIn(b"NOTE.txt", patch)
        self.assertIn(b"tests/check.sh", patch)
        self.assertTrue(result["committed"])
        self.assertFalse(result["empty_diff"])
        self.assertEqual(result["protected_changed"], ["tests"])
        self.assertEqual(result["hidden_passed"], 2)
        self.assertEqual(json.loads((self.out / "eval.json").read_text())["criteria"], {"T01-C1": True, "T01-C2": True})

    def test_files_hidden_by_the_agent_gitignore_still_appear_in_the_patch(self) -> None:
        (self.work / ".gitignore").write_text("conftest.py\n")
        (self.work / "conftest.py").write_text("planted\n")

        self.evaluate()

        patch = (self.out / "final.patch").read_bytes()
        self.assertIn(b"conftest.py", patch)
        self.assertIn(b"+planted", patch)

    def test_ignored_snapshot_files_and_test_caches_keep_an_unchanged_run_empty(self) -> None:
        # The snapshot ships a file its own .gitignore matches; the agent only ran the tests.
        (self.task.snapshot / ".gitignore").write_text("vendor/\n")
        (self.task.snapshot / "vendor").mkdir()
        (self.task.snapshot / "vendor" / "lib.txt").write_text("vendored\n")
        subprocess.run(["rm", "-r", "-f", str(self.run_root)], check=True)
        self.run_root, self.base = isolate.prepare_run(self.runs_dir, "T01_A_r2", self.task, "A", PROJECT_CLAUDE, REPO_ROOT, None)
        self.work = self.run_root / "work"
        (self.work / "__pycache__").mkdir()
        (self.work / "__pycache__" / "app.cpython.pyc").write_bytes(b"cache")
        (self.work / ".pytest_cache").mkdir()
        (self.work / ".pytest_cache" / "v").write_text("x\n")

        result = self.evaluate()

        self.assertTrue(result["empty_diff"], (self.out / "final.patch").read_bytes())

    def test_no_change_gives_empty_patch_and_failing_hidden_tests(self) -> None:
        result = self.evaluate()

        self.assertEqual((self.out / "final.patch").read_bytes(), b"")
        self.assertTrue(result["empty_diff"])
        self.assertFalse(result["committed"])
        self.assertEqual(result["hidden_passed"], 0)
        self.assertTrue(result["suite"]["passed"])

    def test_agent_git_config_is_not_executed_on_the_host(self) -> None:
        marker = self.tmp / "executed"
        self.agent_git("config", "core.fsmonitor", f"touch {marker}; false")
        self.agent_git("config", "filter.evil.clean", f"touch {marker}; cat")
        (self.work / ".gitattributes").write_text("* filter=evil\n")
        (self.work / "app.txt").write_text("fixed\n")

        self.evaluate()

        self.assertFalse(marker.exists())
        self.assertIn(b"+fixed", (self.out / "final.patch").read_bytes())

    def test_agent_repository_is_never_read_by_git_on_the_host(self) -> None:
        # A "promisor" remote whose upload-pack would run on the host during a lazy fetch, and a
        # HEAD pointing at a missing commit to trigger it.
        marker = self.tmp / "escaped"
        self.agent_git("config", "extensions.partialClone", "origin")
        self.agent_git("config", "remote.origin.promisor", "true")
        self.agent_git("config", "remote.origin.url", str(self.tmp / "nowhere"))
        self.agent_git("config", "remote.origin.uploadpack", f"sh -c 'touch {marker}' #")
        (self.work / ".git" / "refs" / "heads" / "main").write_text("1" * 40 + "\n")

        result = self.evaluate()

        self.assertFalse(marker.exists())
        self.assertTrue(result["committed"])

    def test_deleted_agent_repository_still_yields_a_patch(self) -> None:
        subprocess.run(["rm", "-r", "-f", str(self.work / ".git")], check=True)
        (self.work / "app.txt").write_text("fixed\n")

        result = self.evaluate()

        self.assertFalse(result["committed"])
        self.assertIn(b"+fixed", (self.out / "final.patch").read_bytes())


class IsolationOfChecksTests(EvaluateTestCase):
    def test_checks_run_outside_run_root_on_fresh_copies_and_build_never_sees_hidden_tests(self) -> None:
        yaml_path = self.task.directory / "task.yaml"
        yaml_path.write_text(yaml_path.read_text().replace(
            "  test: sh tests/check.sh\n", "  test: test ! -e bench_hidden && echo x > SUITE.txt\n  build: test ! -e bench_hidden\n"))
        self.task = load_task(self.task.directory)
        (self.work / "app.txt").write_text("fixed\n")
        (self.work / "NOTE.txt").write_text("note\n")

        result = self.evaluate()

        self.assertTrue(result["build"]["passed"])
        self.assertTrue(result["suite"]["passed"])
        self.assertEqual(result["hidden_passed"], 2)
        mounts = [argv[argv.index("-w") + 1] for argv in self.docker_calls()]
        self.assertEqual(len(set(mounts)), 4)

        for mount in mounts:
            self.assertFalse(mount.startswith(str(self.run_root)), mount)

        # Each check's HOME is its own directory inside its mount, never a shared one like /tmp.
        homes = set()

        for argv in self.docker_calls():
            source = argv[argv.index("--mount") + 1].split(",")[1].removeprefix("source=")
            home = next(a.removeprefix("HOME=") for a in argv if a.startswith("HOME="))
            self.assertEqual(Path(home).parent, Path(source))
            self.assertEqual(Path(argv[argv.index("-w") + 1]).parent, Path(source))
            homes.add(home)

        self.assertEqual(len(homes), 4)

        self.assertEqual(sorted(p.name for p in self.run_root.iterdir()), ["home", "out", "toolkit", "work"])
        self.assertEqual([p for p in self.runs_dir.iterdir() if p.name.startswith("eval-")], [])

    def test_special_files_are_skipped_and_unreadable_files_fail_the_run(self) -> None:
        os.mkfifo(self.work / "pipe")
        (self.work / "app.txt").write_text("fixed\n")
        (self.work / "NOTE.txt").write_text("note\n")

        self.assertEqual(self.evaluate()["hidden_passed"], 2)

        locked = self.work / "locked.txt"
        locked.write_text("x\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)

        result = self.evaluate()

        self.assertTrue(result["workspace_invalid"])
        self.assertEqual(result["hidden_passed"], 0)


class ProtectedPathTests(EvaluateTestCase):
    def test_tampered_protected_test_is_restored_before_checks(self) -> None:
        # The agent weakens the pre-existing suite; the evaluator runs the original one.
        (self.task.snapshot / "tests" / "check.sh").write_text("grep -q fixed app.txt\n")
        self.task = load_task(self.task.directory)
        (self.work / "tests" / "check.sh").write_text("true\n")

        result = self.evaluate()

        self.assertEqual(result["protected_changed"], ["tests"])
        self.assertFalse(result["suite"]["passed"])
        self.assertEqual((self.work / "tests" / "check.sh").read_text(), "true\n")

    def test_caches_do_not_flag_protected_paths_but_are_restored_and_other_ignored_names_are_flagged(self) -> None:
        cache = self.work / "tests" / "__pycache__"
        cache.mkdir()
        (cache / "check.cpython.pyc").write_bytes(b"planted")
        (self.work / "app.txt").write_text("fixed\n")
        # The suite fails if the planted cache survives the restore.
        yaml_path = self.task.directory / "task.yaml"
        yaml_path.write_text(yaml_path.read_text().replace("  test: sh tests/check.sh\n", "  test: test ! -e tests/__pycache__\n"))
        self.task = load_task(self.task.directory)

        result = self.evaluate()

        self.assertEqual(result["protected_changed"], [])
        self.assertTrue(result["suite"]["passed"])
        (self.work / "tests" / "CVS").mkdir()
        (self.work / "tests" / "CVS" / "x").write_text("x\n")
        self.assertEqual(self.evaluate()["protected_changed"], ["tests"])

    def test_symlinked_protected_parent_never_touches_the_link_target(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep\n")
        subprocess.run(["rm", "-r", "-f", str(self.work / "tests")], check=True)
        os.symlink(outside, self.work / "tests")

        result = self.evaluate()

        self.assertEqual(result["protected_changed"], ["tests"])
        self.assertEqual((outside / "keep.txt").read_text(), "keep\n")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["keep.txt"])

    def test_replaced_workspace_fails_without_reading_through_it(self) -> None:
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        subprocess.run(["rm", "-r", "-f", str(self.work)], check=True)
        os.symlink(outside, self.work)

        result = self.evaluate()

        self.assertTrue(result["workspace_invalid"])
        self.assertEqual(result["hidden_passed"], 0)
        self.assertEqual(self.docker_calls(), [])

    def test_hidden_tests_only_enter_the_evaluator_copy(self) -> None:
        self.evaluate()

        self.assertFalse((self.work / evaluate.HIDDEN_DIR).exists())
        hidden_calls = [argv for argv in self.docker_calls() if any("bench_hidden/" in a for a in argv)]
        self.assertEqual(len(hidden_calls), 2)

        for argv in hidden_calls:
            self.assertEqual(argv[argv.index("--network") + 1], "none")
            self.assertNotIn(str(self.work), " ".join(argv))


if __name__ == "__main__":
    unittest.main()
