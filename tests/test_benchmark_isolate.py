"""Tests for the per-run isolation of the benchmark harness (benchmark/bench/isolate.py)."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from benchmark_support import BENCH_DIR, REPO_ROOT, BenchTestCase, head_sha, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench import isolate  # noqa: E402
from bench.gitops import isolated_git_env  # noqa: E402
from bench.taskspec import load_task  # noqa: E402

PROJECT_CLAUDE = REPO_ROOT / "benchmark" / "project-claude.md"


# The commit that added the process-metrics instrument (#77) and its parent.
INSTRUMENT_COMMIT = "9ed17c8"
PRE_INSTRUMENT_COMMIT = "ae5165b"
# Treated files, relative to the run HOME, and their sources in the toolkit.
TREATED_SOURCES = {".claude/CLAUDE.md": "dist/claude/CLAUDE.md", ".claude/rules/git-workflow.md": "core/rules/git-workflow.md"}

CLAUDE_BEFORE = """\
# Instructions

Neutral paragraph about the team.

## Reporting

- Global skills: `verify-changes` (local verification) and the stack skills.
"""
CLAUDE_WITH_INSTRUMENT = """\
# Instructions

Neutral paragraph about the team.

**Process metrics.** On a task tracked by an issue, follow the `process-metrics` skill; it may ask first.

## Reporting

- Global skills: `verify-changes` (local verification), `process-metrics` (process records on the task's issue) and the stack skills.
"""
WORKFLOW_BEFORE = "# Git workflow\n\n- First bullet.\n- Last bullet.\n"
WORKFLOW_WITH_INSTRUMENT = (
    "# Git workflow\n\n- First bullet.\n"
    "- When the `process-metrics` mode is `on`, post comments marked `<!-- process-metrics v1 -->`.\n- Last bullet.\n"
)
# Links the toolkit's files into $HOME/.claude the way the real installer does; EXTRA lines follow.
FAKE_INSTALLER = """\
set -e
toolkit="$(cd "$(dirname "$0")/.." && pwd)"
config="$HOME/.claude"
mkdir -p "$config/rules" "$config/skills" "$config/agents"
ln -s "$toolkit/dist/claude/CLAUDE.md" "$config/CLAUDE.md"
ln -s "$toolkit/core/rules/git-workflow.md" "$config/rules/git-workflow.md"
for skill in "$toolkit"/core/skills/*; do ln -s "$skill" "$config/skills/${skill##*/}"; done
for agent in "$toolkit"/agents/*.md; do if [ -e "$agent" ]; then ln -s "$agent" "$config/agents/${agent##*/}"; fi; done
"""


def git_out(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=isolated_git_env(cwd), capture_output=True, text=True, check=True).stdout


def commit_exists(revision: str) -> bool:
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{revision}^{{commit}}"],
                            env=isolated_git_env(REPO_ROOT), capture_output=True, check=False)

    return result.returncode == 0


def commit_files(repo: Path, files: dict[str, str]) -> str:
    """Writes <files> into a new repository at <repo>, commits them and returns the commit SHA."""
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")

    env = isolated_git_env(repo)

    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    return git_out(["rev-parse", "HEAD"], repo).strip()


def mentions(config: Path) -> list[str]:
    """Files under <config> (links followed) that still mention the instrument."""
    found = []

    for directory, _, names in os.walk(config, followlinks=True):
        for name in names:
            path = Path(directory, name)

            if isolate.INSTRUMENT_TOKEN.search(path.read_text(encoding="utf-8", errors="replace")):
                found.append(str(path.relative_to(config)))

    return found


def only_removals(before: str, after: str) -> bool:
    return all(tag in ("equal", "delete") for tag, *_ in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes())


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

        run_root, base, treatment = isolate.prepare_run(self.runs_dir, "T01_A_r1", task, "A", PROJECT_CLAUDE, REPO_ROOT, None)

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
        self.assertIsNone(treatment)

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

        run_root, _, treatment = isolate.prepare_run(self.runs_dir, "T01_B_r1", task, "B", PROJECT_CLAUDE, repo, sha)

        # An install without the instrument's files: nothing to remove.
        self.assertEqual(treatment["instrument"], "absent")
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

        run_root, _, treatment = isolate.prepare_run(self.runs_dir, "T01_C_r1", task, "C", PROJECT_CLAUDE, REPO_ROOT, head_sha())

        claude = run_root / "home" / ".claude"
        self.assertTrue((claude / "CLAUDE.md").is_symlink())
        self.assertTrue(os.path.realpath(claude / "rules" / "testing.md").startswith(str(run_root / "toolkit")))
        self.assertFalse((self.home / ".claude").exists())
        links = [p for p in claude.rglob("*") if p.is_symlink()]
        self.assertTrue(links)
        self.assertFalse([p for p in links if "/benchmark/" in os.path.realpath(p)])
        # The instrument is gone from what the agent loads, and only removals were made.
        self.assertEqual(treatment["instrument"], "removed")
        self.assertEqual([entry["count"] for entry in treatment["removed"]], [1, 1, 1])
        self.assertFalse(os.path.lexists(claude / "skills" / "process-metrics"))
        self.assertFalse((run_root / "toolkit" / "core" / "skills" / "process-metrics").exists())
        self.assertEqual(mentions(claude), [])

        for rel, source in TREATED_SOURCES.items():
            with self.subTest(rel=rel):
                installed = git_out(["show", f"HEAD:{source}"], REPO_ROOT)
                treated = (run_root / "home" / rel).read_text(encoding="utf-8")
                self.assertNotEqual(treated, installed)
                self.assertTrue(only_removals(installed, treated))

    @unittest.skipUnless(commit_exists(INSTRUMENT_COMMIT) and commit_exists(PRE_INSTRUMENT_COMMIT), "needs the full history")
    def test_treated_instrument_commit_equals_the_text_before_the_instrument(self) -> None:
        task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

        run_root, _, treatment = isolate.prepare_run(
            self.runs_dir, "T01_B_r1", task, "B", PROJECT_CLAUDE, REPO_ROOT, INSTRUMENT_COMMIT
        )

        self.assertEqual(treatment["instrument"], "removed")

        for rel, source in TREATED_SOURCES.items():
            with self.subTest(rel=rel):
                before = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{PRE_INSTRUMENT_COMMIT}:{source}"],
                                        env=isolated_git_env(REPO_ROOT), capture_output=True, check=True).stdout
                self.assertEqual((run_root / "home" / rel).read_bytes(), before)

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


class InstrumentTests(BenchTestCase):
    """remove_instrument on synthetic toolkits installed by a fake installer (conditions B and C)."""

    def setUp(self) -> None:
        super().setUp()
        self.task = load_task(make_task(self.tasks_dir))
        self.runs_dir.mkdir()

    def toolkit(self, claude: str = CLAUDE_WITH_INSTRUMENT, workflow: str = WORKFLOW_WITH_INSTRUMENT, skill: bool = True,
                extra_files: dict[str, str] | None = None, extra_install: str = "") -> tuple[Path, str]:
        files = {
            "scripts/install.sh": FAKE_INSTALLER + extra_install,
            "dist/claude/CLAUDE.md": claude,
            "core/rules/git-workflow.md": workflow,
            "core/skills/other/SKILL.md": "Another skill.\n",
            **(extra_files or {}),
        }

        if skill:
            files["core/skills/process-metrics/SKILL.md"] = "Records process metrics.\n"

        repo = Path(tempfile.mkdtemp(prefix="toolkit-", dir=self.tmp))

        return repo, commit_files(repo, files)

    def prepare(self, repo: Path, sha: str, condition: str = "B") -> isolate.PreparedRun:
        return isolate.prepare_run(self.runs_dir, f"T01_{condition}_r1", self.task, condition, PROJECT_CLAUDE, repo, sha)

    def test_installed_instrument_is_removed_leaving_the_text_before_it(self) -> None:
        repo, sha = self.toolkit()

        run_root, _, treatment = self.prepare(repo, sha)

        claude = run_root / "home" / ".claude"
        self.assertFalse(os.path.lexists(claude / "skills" / "process-metrics"))
        self.assertFalse((run_root / "toolkit" / "core" / "skills" / "process-metrics").exists())
        self.assertEqual((claude / "skills" / "other" / "SKILL.md").read_text(), "Another skill.\n")
        self.assertEqual((claude / "CLAUDE.md").read_bytes(), CLAUDE_BEFORE.encode())
        self.assertEqual((claude / "rules" / "git-workflow.md").read_bytes(), WORKFLOW_BEFORE.encode())
        self.assertEqual(treatment["instrument"], "removed")
        self.assertEqual([(e["path"], e["rule"], e["count"]) for e in treatment["removed"]], [
            (".claude/CLAUDE.md", "paragraph", 1),
            (".claude/CLAUDE.md", "skill_list_item", 1),
            (".claude/rules/git-workflow.md", "bullet", 1),
        ])
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", e["removed_sha256"]) for e in treatment["removed"]))
        self.assertEqual(sorted(treatment["loaded_sha256"]), ["CLAUDE.md", "rules/git-workflow.md", "skills/other/SKILL.md"])
        self.assertEqual(treatment["loaded_sha256"]["CLAUDE.md"], hashlib.sha256(CLAUDE_BEFORE.encode()).hexdigest())

    def test_toolkit_without_the_instrument_is_left_unchanged(self) -> None:
        repo, sha = self.toolkit(CLAUDE_BEFORE, WORKFLOW_BEFORE, skill=False)

        run_root, _, treatment = self.prepare(repo, sha)

        claude = run_root / "home" / ".claude"
        self.assertEqual(treatment["instrument"], "absent")
        self.assertEqual([e["count"] for e in treatment["removed"]], [0, 0, 0])
        self.assertEqual([e["removed_sha256"] for e in treatment["removed"]], [None, None, None])
        self.assertEqual((claude / "CLAUDE.md").read_bytes(), CLAUDE_BEFORE.encode())
        self.assertEqual((claude / "rules" / "git-workflow.md").read_bytes(), WORKFLOW_BEFORE.encode())

    def test_conditions_b_and_c_load_the_same_files_and_a_has_no_treatment(self) -> None:
        repo, sha = self.toolkit()

        treatments = {condition: self.prepare(repo, sha, condition).toolkit_treatment for condition in "ABC"}

        self.assertIsNone(treatments["A"])
        self.assertEqual(treatments["B"], treatments["C"])
        self.assertTrue(treatments["B"]["loaded_sha256"])

    def test_unrecognised_layouts_fail_closed(self) -> None:
        cases = {
            "rewritten paragraph with the skill installed": (
                {"claude": CLAUDE_WITH_INSTRUMENT.replace("**Process metrics.**", "**Process metrics:**")},
                "unexpected process-metrics layout",
            ),
            "fragments installed without the skill": ({"skill": False}, "unexpected process-metrics layout"),
            "a duplicated fragment with the skill installed": (
                {"claude": CLAUDE_WITH_INSTRUMENT.replace("## Reporting", "**Process metrics.** Again.\n\n## Reporting")},
                r"unexpected process-metrics layout: skill installed, fragment counts \[2, 1, 1\]",
            ),
            "a file at the skill's path": (
                {"skill": False, "extra_install": 'echo skill > "$config/skills/process-metrics"\n'},
                "neither a link into the toolkit export nor a directory",
            ),
            "a mention left in an agent": (
                {"extra_files": {"agents/x.md": "Use the process metrics skill.\n"}},
                "still mentioned in agents/x.md",
            ),
        }

        for name, (options, message) in cases.items():
            with self.subTest(name=name):
                repo, sha = self.toolkit(**options)

                with self.assertRaisesRegex(isolate.IsolationError, message):
                    self.prepare(repo, sha)

    def test_skill_copied_as_a_directory_into_the_run_home_is_removed(self) -> None:
        repo, sha = self.toolkit(skill=False, extra_install='mkdir "$config/skills/process-metrics"\n'
                                                            'echo "Records process metrics." > "$config/skills/process-metrics/SKILL.md"\n')

        run_root, _, treatment = self.prepare(repo, sha)

        self.assertEqual(treatment["instrument"], "removed")
        self.assertFalse(os.path.lexists(run_root / "home" / ".claude" / "skills" / "process-metrics"))

    def test_skill_linking_outside_the_run_root_is_refused_and_left_untouched(self) -> None:
        outside = self.tmp / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("Records process metrics.\n")
        repo, sha = self.toolkit(skill=False, extra_install=f'ln -s "{outside}" "$config/skills/process-metrics"\n')

        with self.assertRaisesRegex(isolate.IsolationError, "links outside the toolkit export"):
            self.prepare(repo, sha)

        self.assertEqual((outside / "SKILL.md").read_text(), "Records process metrics.\n")

    def test_skill_linking_to_the_toolkit_root_is_refused_and_the_export_kept(self) -> None:
        repo, sha = self.toolkit(skill=False, extra_install='ln -s "$toolkit" "$config/skills/process-metrics"\n')

        with self.assertRaisesRegex(isolate.IsolationError, "links outside the toolkit export"):
            self.prepare(repo, sha)

        run_root = next(self.runs_dir.iterdir())
        self.assertTrue((run_root / "toolkit" / "scripts" / "install.sh").is_file())

    def outside_instructions(self) -> Path:
        outside = self.tmp / "outside" / "CLAUDE.md"
        outside.parent.mkdir()
        outside.write_text(CLAUDE_WITH_INSTRUMENT)

        return outside

    def test_instructions_linked_to_a_file_outside_the_run_root_are_refused_and_left_untouched(self) -> None:
        outside = self.outside_instructions()
        repo, sha = self.toolkit(extra_install=f'rm "$config/CLAUDE.md"\nln -s "{outside}" "$config/CLAUDE.md"\n')

        with self.assertRaisesRegex(isolate.IsolationError, ".claude/CLAUDE.md resolves outside the run directory"):
            self.prepare(repo, sha)

        self.assertEqual(outside.read_bytes(), CLAUDE_WITH_INSTRUMENT.encode())

    def test_instructions_hard_linked_to_a_file_outside_the_run_root_are_refused_and_left_untouched(self) -> None:
        outside = self.outside_instructions()

        try:
            os.link(outside, self.tmp / "hard-link-probe")
        except OSError:
            self.skipTest("hard links are not available in the temporary directory")

        repo, sha = self.toolkit(extra_install=f'rm "$config/CLAUDE.md"\nln "{outside}" "$config/CLAUDE.md"\n')

        with self.assertRaisesRegex(isolate.IsolationError, ".claude/CLAUDE.md is not a regular file with a single link"):
            self.prepare(repo, sha)

        self.assertEqual(outside.read_bytes(), CLAUDE_WITH_INSTRUMENT.encode())

    def test_loaded_link_leaving_the_run_root_is_refused(self) -> None:
        outside = self.tmp / "outside-agents"
        outside.mkdir()
        (outside / "x.md").write_text("Neutral.\n")
        repo, sha = self.toolkit(extra_install=f'ln -s "{outside}" "$config/agents/external"\n')

        with self.assertRaisesRegex(isolate.IsolationError, "agents/external resolves outside the run directory"):
            self.prepare(repo, sha)

    def test_link_cycles_and_session_data_are_not_scanned_twice_or_at_all(self) -> None:
        repo, sha = self.toolkit(extra_install='ln -s "$config" "$config/skills/loop"\nmkdir -p "$config/projects"\n'
                                               'echo "process metrics" > "$config/projects/session.jsonl"\n')

        _, _, treatment = self.prepare(repo, sha)

        self.assertEqual(treatment["instrument"], "removed")
        self.assertFalse([rel for rel in treatment["loaded_sha256"] if rel.startswith(("projects/", "skills/loop/"))])


if __name__ == "__main__":
    unittest.main()
