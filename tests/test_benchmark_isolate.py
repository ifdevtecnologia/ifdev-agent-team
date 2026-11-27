"""Tests for the per-run isolation of the benchmark harness (benchmark/bench/isolate.py)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from benchmark_support import BENCH_DIR, REPO_ROOT, BenchTestCase, head_sha, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench import isolate  # noqa: E402
from bench.gitops import isolated_git_env  # noqa: E402
from bench.taskspec import load_task  # noqa: E402

PROJECT_CLAUDE = REPO_ROOT / "benchmark" / "project-claude.md"


def git_out(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=isolated_git_env(cwd), capture_output=True, text=True, check=True).stdout


class ManagedItemsTests(unittest.TestCase):
    def test_no_managed_item_comes_from_the_harness(self) -> None:
        common = (REPO_ROOT / "scripts" / "lib" / "common.sh").read_text()
        sources = re.findall(r'^\s*"([^"|]+)\|', common, re.MULTILINE)

        self.assertTrue(sources)
        self.assertFalse([s for s in sources if s.startswith("benchmark")])


class RunsDirTests(BenchTestCase):
    def test_refuses_relative_inside_home_and_inside_a_repository(self) -> None:
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        bare = self.tmp / "bare.git"
        (bare / "objects").mkdir(parents=True)
        (bare / "HEAD").write_text("ref: refs/heads/main\n")

        for path in (Path("relative"), self.home / "runs", repo / "runs", bare / "runs"):
            with self.subTest(path=path), self.assertRaises(isolate.IsolationError):
                isolate.check_runs_dir(path)

            self.assertFalse(path.is_absolute() and path.exists(), path)

    def test_accepts_a_directory_outside_home_and_repositories(self) -> None:
        self.assertEqual(isolate.check_runs_dir(self.runs_dir), self.runs_dir)
        self.assertTrue(self.runs_dir.is_dir())


class WorkspaceTests(BenchTestCase):
    def test_run_root_layout_and_workspace_commit(self) -> None:
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        run_root, base = isolate.prepare_run(self.runs_dir, "T01_A_r1", task, "A", PROJECT_CLAUDE, REPO_ROOT, None)

        self.assertEqual(run_root.parent, self.runs_dir)
        self.assertTrue(run_root.name.startswith("T01_A_r1-"))
        self.assertEqual(sorted(p.name for p in run_root.iterdir()), ["home", "out", "toolkit", "work"])
        # Condition A: empty config directory, no toolkit, installer not run.
        self.assertEqual(list((run_root / "home" / ".claude").iterdir()), [])
        self.assertEqual(list((run_root / "toolkit").iterdir()), [])
        work = run_root / "work"
        self.assertEqual((work / "app.txt").read_text(), "broken\n")
        self.assertEqual((work / "CLAUDE.md").read_text(), PROJECT_CLAUDE.read_text())
        self.assertEqual(git_out(["rev-parse", "HEAD"], work).strip(), base)
        self.assertEqual(git_out(["remote"], work), "")
        self.assertEqual(git_out(["log", "--format=%an <%ae>"], work).strip(), "Bench Harness <bench-harness@example.invalid>")
        self.assertEqual(git_out(["status", "--porcelain"], work), "")
        self.assertIn("example.invalid", (run_root / "home" / ".gitconfig").read_text())

    def test_condition_b_without_sha_is_refused(self) -> None:
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        with self.assertRaises(isolate.IsolationError):
            isolate.prepare_run(self.runs_dir, "T01_B_r1", task, "B", PROJECT_CLAUDE, REPO_ROOT, None)


class ToolkitTests(BenchTestCase):
    def make_repo(self) -> tuple[Path, str]:
        repo = self.tmp / "toolkit-src"
        for rel in ("scripts/install.sh", "benchmark/bench/runner.py", "docs/benchmark/plan.md", "docs/guide.md", "core/x.md",
                    "tests/test_benchmark_runner.py", "docs/decisions/0001-benchmark-protocol.md", "docs/decisions/0002-other.md"):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text("x\n")

        (repo / "scripts" / "install.sh").write_text('mkdir -p "$HOME/.claude" && echo "$@" > "$HOME/.claude/installed"\n')
        env = isolated_git_env(repo)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

        return repo, git_out(["rev-parse", "HEAD"], repo).strip()

    def test_resolve_sha_returns_full_sha_and_refuses_unknown(self) -> None:
        repo, sha = self.make_repo()

        self.assertEqual(isolate.resolve_sha(repo, sha[:10]), sha)

        for bad in ("0" * 40, "--all", "", "no-such-ref", "HEAD:README.md", "HEAD --all", "-n1", "@{-1}"):
            with self.subTest(bad=bad), self.assertRaises(isolate.IsolationError):
                isolate.resolve_sha(repo, bad)

    def test_export_refuses_a_revision_that_git_could_read_as_an_option(self) -> None:
        repo, _ = self.make_repo()

        for bad in ("--output=archive.tar", "--remote=x", "HEAD:README.md", ""):
            with self.subTest(bad=bad), self.assertRaises(isolate.IsolationError):
                isolate.export_toolkit(repo, bad, self.tmp / f"export-{len(bad)}")

        self.assertFalse((repo / "archive.tar").exists())

    def test_export_excludes_the_harness_and_installer_runs_with_temporary_home(self) -> None:
        repo, sha = self.make_repo()
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        run_root, _ = isolate.prepare_run(self.runs_dir, "T01_B_r1", task, "B", PROJECT_CLAUDE, repo, sha)

        toolkit = run_root / "toolkit"
        self.assertTrue((toolkit / "scripts" / "install.sh").is_file())
        self.assertTrue((toolkit / "docs" / "guide.md").is_file())
        self.assertFalse((toolkit / "benchmark").exists())
        self.assertFalse((toolkit / "docs" / "benchmark").exists())
        self.assertFalse((toolkit / "tests").exists())
        self.assertFalse((toolkit / "docs" / "decisions" / "0001-benchmark-protocol.md").exists())
        self.assertTrue((toolkit / "docs" / "decisions" / "0002-other.md").is_file())
        self.assertEqual((run_root / "home" / ".claude" / "installed").read_text().strip(), "--target claude")
        self.assertFalse((self.home / ".claude").exists())

    def test_real_toolkit_installs_managed_items_into_the_run_home(self) -> None:
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        run_root, _ = isolate.prepare_run(self.runs_dir, "T01_C_r1", task, "C", PROJECT_CLAUDE, REPO_ROOT, head_sha())

        claude = run_root / "home" / ".claude"
        self.assertTrue((claude / "CLAUDE.md").is_symlink())
        self.assertTrue(os.path.realpath(claude / "rules" / "testing.md").startswith(str(run_root / "toolkit")))
        self.assertFalse((self.home / ".claude").exists())
        links = [p for p in claude.rglob("*") if p.is_symlink()]
        self.assertTrue(links)
        self.assertFalse([p for p in links if "/benchmark/" in os.path.realpath(p)])

    def test_links_escaping_the_export_are_refused(self) -> None:
        repo, _ = self.make_repo()
        os.symlink("../../outside", repo / "core" / "escape")
        os.symlink("../scripts/install.sh", repo / "core" / "inside")
        env = isolated_git_env(repo)
        subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "links"], cwd=repo, env=env, check=True, capture_output=True)
        destination = self.tmp / "export"

        with self.assertRaisesRegex(isolate.IsolationError, "unsafe link"):
            isolate.export_toolkit(repo, "HEAD", destination)

    def test_failing_installer_is_reported(self) -> None:
        repo, _ = self.make_repo()
        (repo / "scripts" / "install.sh").write_text("exit 3\n")
        env = isolated_git_env(repo)
        subprocess.run(["git", "commit", "-qam", "broken"], cwd=repo, env=env, check=True, capture_output=True)
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        with self.assertRaisesRegex(isolate.IsolationError, "exit code 3"):
            isolate.prepare_run(self.runs_dir, "T01_B_r1", task, "B", PROJECT_CLAUDE, repo, git_out(["rev-parse", "HEAD"], repo).strip())


if __name__ == "__main__":
    unittest.main()
