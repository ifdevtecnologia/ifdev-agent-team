"""Deterministic tests for the isolated writable clone fallback.

Every repository is a temporary local Git repository; the "remote" is a local bare repository, so
no test needs network access or touches a real checkout. Unwritable metadata is simulated at the
probe boundary instead of changing permissions.
"""

from __future__ import annotations

import contextlib
import errno
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "codex/skills/supervise-claude"
SCRIPTS = SKILL_DIR / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fallback = load("clone_fallback")
run_claude = load("run_claude")

UNWRITABLE = ["/original/.git: Read-only file system"]
TASK_BRANCH = "feat/sample-task"
SUPPORTED_HELP = """
--print --output-format --permission-mode "dontAsk" bypassPermissions --permission-prompts "none"
--restricted --tools --allowedTools --disallowedTools --strict-mcp-config
"""


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout.strip()


class GitFixture(unittest.TestCase):
    """A bare 'remote' whose default branch moved after the original checkout was cloned."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        home = self.base / "home"
        home.mkdir()
        environment = mock.patch.dict(os.environ, {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
        })
        environment.start()
        self.addCleanup(environment.stop)

        self.remote = self.base / "hosting/acme/app.git"
        self.remote.mkdir(parents=True)
        run_git(self.remote, "init", "--bare", "-b", "main")
        self.seed = self.base / "seed"
        run_git(self.base, "clone", str(self.remote), str(self.seed))
        self.commit(self.seed, "README.md", "v1\n")
        run_git(self.seed, "push", "origin", "HEAD:refs/heads/main")

        self.original = self.base / "original app"
        run_git(self.base, "clone", str(self.remote), str(self.original))
        self.stale_head = run_git(self.original, "rev-parse", "HEAD")
        self.commit(self.seed, "README.md", "v2\n")
        run_git(self.seed, "push", "origin", "HEAD:refs/heads/main")
        self.fresh_head = run_git(self.seed, "rev-parse", "HEAD")

        self.run_dir = self.base / "runs/run-1"
        self.run_dir.mkdir(parents=True)

    def commit(self, repo: Path, name: str, content: str) -> str:
        (repo / name).write_text(content, encoding="utf-8")
        run_git(repo, "add", "--", name)
        run_git(repo, "commit", "-q", "-m", f"update {name}")
        return run_git(repo, "rev-parse", "HEAD")

    def cli(self, *args: str) -> tuple:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = fallback.main(list(args))
        return code, json.loads(output.getvalue())

    def prepare(self, *extra: str, unwritable: bool = True, project: Path | None = None) -> tuple:
        args = ["prepare", "--project", str(project or self.original), "--run-dir", str(self.run_dir),
                "--remote", "origin", "--expected-repository", "acme/app", "--task-branch", TASK_BRANCH, *extra]
        if not unwritable:
            return self.cli(*args)
        with mock.patch.object(fallback, "probe_metadata", return_value=list(UNWRITABLE)):
            return self.cli(*args)

    def record_path(self) -> Path:
        return self.run_dir / fallback.RECORD_NAME

    def record(self) -> dict:
        return json.loads(self.record_path().read_text(encoding="utf-8"))

    def original_state(self) -> tuple:
        return (
            run_git(self.original, "rev-parse", "HEAD"),
            run_git(self.original, "status", "--porcelain=v1", "-uall"),
            run_git(self.original, "for-each-ref", "--format=%(refname) %(objectname)"),
            run_git(self.original, "config", "--local", "--list"),
        )

    def remote_ref(self, branch: str) -> str:
        return run_git(self.remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}")

    def workspaces(self) -> list:
        return sorted(self.run_dir.glob("workspace-*"))


class WorkspaceSelectionTests(GitFixture):
    def test_writable_metadata_keeps_the_normal_path_without_cloning(self) -> None:
        before = self.original_state()
        calls = []
        real_git = fallback.git
        with mock.patch.object(fallback, "git", side_effect=lambda args, cwd, check=True: calls.append(args) or real_git(args, cwd, check)):
            code, result = self.prepare(unwritable=False)
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "NORMAL")
        self.assertEqual(result["workspace"], str(self.original))
        self.assertEqual(self.workspaces(), [])
        self.assertFalse(any(args[0] in ("clone", "ls-remote", "fetch", "push") for args in calls))
        self.assertEqual(self.original_state(), before)

    def test_unwritable_clean_trusted_origin_selects_a_fresh_isolated_clone(self) -> None:
        before = self.original_state()
        code, result = self.prepare("--starts-from-default")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["mode"], "FALLBACK")
        record = self.record()
        clone = Path(record["clone_path"])
        self.assertEqual(record["workspace"], str(clone))
        self.assertTrue((clone / ".git").is_dir())
        self.assertEqual(clone.parent.parent, self.run_dir)
        self.assertNotIn(self.original, clone.parents)
        # The clone starts from the freshly discovered remote default, not the stale local HEAD.
        self.assertEqual(record["original"]["head"], self.stale_head)
        self.assertEqual(record["remote_head"], self.fresh_head)
        self.assertEqual(record["clone_start_head"], self.fresh_head)
        self.assertEqual(record["default_branch"], "main")
        self.assertEqual(run_git(clone, "rev-parse", "HEAD"), self.fresh_head)
        self.assertEqual(run_git(clone, "rev-parse", "--abbrev-ref", "HEAD"), TASK_BRANCH)
        self.assertTrue(record["repository"].endswith("acme/app"))
        self.assertNotIn(str(self.remote), self.record_path().read_text(encoding="utf-8"))  # URL is never recorded
        self.assertEqual(self.original_state(), before)
        self.assertEqual(self.cli("verify-original", "--record", str(self.record_path())), (0, {"unchanged": True, "changed": []}))

    def test_each_run_gets_a_unique_retained_clone_and_never_overwrites_its_record(self) -> None:
        self.prepare("--starts-from-default")
        clone = Path(self.record()["clone_path"])
        with self.assertRaises(FileExistsError):
            self.prepare("--starts-from-default")
        self.assertEqual(len(self.workspaces()), 1)
        self.assertTrue(clone.is_dir())
        other_run = self.base / "runs/run-2"
        other_run.mkdir()
        self.run_dir = other_run
        self.prepare("--starts-from-default")
        self.assertNotEqual(Path(self.record()["clone_path"]), clone)

    def test_local_uncommitted_files_are_never_copied_into_the_clone(self) -> None:
        (self.original / "notes.txt").write_text("local only\n", encoding="utf-8")
        code, result = self.prepare("--starts-from-default", "--local-state-irrelevant")
        self.assertEqual((code, result["mode"]), (0, "FALLBACK"))
        self.assertFalse((Path(result["workspace"]) / "notes.txt").exists())
        self.assertTrue((self.original / "notes.txt").exists())

    def test_relevant_local_state_blocks_the_fallback(self) -> None:
        cases = {
            "untracked": lambda: (self.original / "draft.txt").write_text("x\n", encoding="utf-8"),
            "unstaged": lambda: (self.original / "README.md").write_text("edited\n", encoding="utf-8"),
            "staged": lambda: ((self.original / "new.txt").write_text("x\n", encoding="utf-8"),
                               run_git(self.original, "add", "--", "new.txt")),
            "local commit": lambda: self.commit(self.original, "local.txt", "x\n"),
        }
        for label, make_state in cases.items():
            with self.subTest(state=label):
                self.setUp()
                make_state()
                before = self.original_state()
                code, result = self.prepare("--starts-from-default")
                self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
                self.assertTrue(any("local-only state" in reason for reason in result["reasons"]), result)
                self.assertEqual(self.workspaces(), [])
                self.assertEqual(self.original_state(), before)

    def test_missing_ambiguous_or_untrusted_remote_blocks_the_fallback(self) -> None:
        cases = {
            "missing": (lambda: run_git(self.original, "remote", "rename", "origin", "upstream"), "not configured"),
            "extra push url": (lambda: run_git(self.original, "remote", "set-url", "--add", "--push", "origin",
                                               str(self.base / "elsewhere/acme/app.git")), "ambiguous"),
            "other repository": (lambda: run_git(self.original, "remote", "set-url", "origin",
                                                 "https://example.invalid/evil-acme/app.git"), "does not match"),
            "embedded credentials": (lambda: run_git(self.original, "remote", "set-url", "origin",
                                                     "https://user:synthetic@example.invalid/acme/app.git"), "credentials"),
        }
        for label, (configure, expected) in cases.items():
            with self.subTest(remote=label):
                self.setUp()
                configure()
                code, result = self.prepare("--starts-from-default")
                self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
                self.assertTrue(any(expected in reason for reason in result["reasons"]), result)
                self.assertNotIn("synthetic", self.record_path().read_text(encoding="utf-8"))
                self.assertEqual(self.workspaces(), [])

    def test_unavailable_network_clone_blocks_the_fallback(self) -> None:
        run_git(self.original, "remote", "set-url", "origin", str(self.base / "offline/acme/app.git"))
        code, result = self.prepare("--starts-from-default")
        self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertTrue(any("discovery failed" in reason for reason in result["reasons"]), result)
        self.assertEqual(self.workspaces(), [])

    def test_task_not_confirmed_to_start_from_default_blocks(self) -> None:
        code, result = self.prepare()
        self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertIn("task was not confirmed to start entirely from the remote default branch", result["reasons"])

    def test_task_branch_equal_to_default_blocks(self) -> None:
        args = ["prepare", "--project", str(self.original), "--run-dir", str(self.run_dir), "--remote", "origin",
                "--expected-repository", "acme/app", "--task-branch", "main", "--starts-from-default"]
        with mock.patch.object(fallback, "probe_metadata", return_value=list(UNWRITABLE)):
            code, result = self.cli(*args)
        self.assertEqual(code, fallback.EXIT_BLOCKED)
        self.assertIn("task branch must not be the remote default branch", result["reasons"])

    def test_invalid_worktree_blocks(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        code, result = self.prepare("--starts-from-default", project=plain)
        self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertEqual(result["reasons"], ["original is not a valid Git worktree"])

    def test_remote_moving_during_clone_blocks_and_retains_the_clone(self) -> None:
        with mock.patch.object(fallback, "discover_default", return_value=("main", self.stale_head)):
            code, result = self.prepare("--starts-from-default")
        self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        record = self.record()
        self.assertTrue(Path(record["clone_path"]).is_dir())
        self.assertEqual(record["clone_start_head"], self.fresh_head)

    def test_original_is_only_inspected_and_nothing_mutates_permissions(self) -> None:
        calls = []
        real_git = fallback.git

        def spy(args, cwd, check=True):
            calls.append((list(args), Path(cwd).resolve()))
            return real_git(args, cwd, check)

        forbidden = {name: mock.DEFAULT for name in ("chmod", "chown", "lchown", "fchmod", "fchown") if hasattr(os, name)}
        with mock.patch.object(fallback, "git", side_effect=spy), \
                mock.patch.multiple(os, **forbidden) as patched:
            self.prepare("--starts-from-default")
        for function in patched.values():
            function.assert_not_called()
        self.assertEqual(self.record()["mode"], "FALLBACK")
        on_original = [args for args, cwd in calls if cwd == self.original]
        self.assertTrue(on_original)
        for args in on_original:
            self.assertTrue(args[0] in {"rev-parse", "status", "for-each-ref", "rev-list"} or args[:2] == ["remote", "get-url"]
                            or args == ["remote"], args)
        for args, _ in calls:
            self.assertFalse({"config", "fetch", "--force", "-f", "stash", "reset", "clean"} & set(args), args)

    def test_original_guard_refuses_mutating_commands(self) -> None:
        for args in (["fetch"], ["switch", "main"], ["add", "."], ["commit"], ["stash"], ["config", "x.y", "z"],
                     ["remote", "set-url", "origin", "x"], ["reset", "--hard"], ["clean", "-fd"], ["restore", "."]):
            with self.subTest(args=args), self.assertRaises(fallback.FallbackError):
                fallback.inspect_original(args, self.original)

    def test_verify_original_detects_a_changed_original(self) -> None:
        self.prepare("--starts-from-default")
        (self.original / "README.md").write_text("changed later\n", encoding="utf-8")
        code, result = self.cli("verify-original", "--record", str(self.record_path()))
        self.assertEqual(code, fallback.EXIT_BLOCKED)
        self.assertFalse(result["unchanged"])
        self.assertIn("status", result["changed"])


class MetadataProbeTests(GitFixture):
    def git_dir(self) -> Path:
        return self.original / ".git"

    def test_writable_metadata_passes_and_leaves_no_probe_files(self) -> None:
        self.assertEqual(fallback.probe_metadata(self.original), [])
        self.assertEqual(list(self.git_dir().rglob(fallback.PROBE_PREFIX + "*")), [])

    def test_probe_attempts_real_writes_instead_of_trusting_mode_bits(self) -> None:
        with mock.patch.object(os, "access", return_value=False):
            self.assertEqual(fallback.probe_metadata(self.original), [])
        with mock.patch.object(fallback.tempfile, "mkstemp", side_effect=OSError(errno.EROFS, "Read-only file system")):
            failures = fallback.probe_metadata(self.original)
        self.assertTrue(failures)
        self.assertTrue(all("Read-only file system" in failure for failure in failures))

    def test_probe_covers_shared_metadata_of_a_linked_worktree(self) -> None:
        linked = self.base / "linked"
        run_git(self.original, "worktree", "add", "-q", "-b", "side", str(linked))
        attempted = []
        real_mkstemp = tempfile.mkstemp

        def record(prefix, dir):
            attempted.append(Path(dir))
            return real_mkstemp(prefix=prefix, dir=dir)

        with mock.patch.object(fallback.tempfile, "mkstemp", side_effect=record):
            self.assertEqual(fallback.probe_metadata(linked), [])
        self.assertIn(self.git_dir().resolve(), attempted)
        self.assertIn(self.git_dir().resolve() / "objects", attempted)
        self.assertIn(self.git_dir().resolve() / "refs/heads", attempted)
        self.assertTrue(any("worktrees" in path.parts for path in attempted))

    def deny(self, denied: Path):
        """Simulates an access denial in one directory at the probe boundary (no permission change)."""
        real_mkstemp = tempfile.mkstemp

        def mkstemp(prefix, dir):
            if Path(dir) == denied:
                raise PermissionError(errno.EACCES, "Permission denied")
            return real_mkstemp(prefix=prefix, dir=dir)

        return mock.patch.object(fallback.tempfile, "mkstemp", side_effect=mkstemp)

    def test_denied_branch_ref_directory_is_not_operable_while_writable_stays_normal(self) -> None:
        heads = self.git_dir().resolve() / "refs/heads"
        code, result = self.prepare(unwritable=False)
        self.assertEqual((code, result["mode"]), (0, "NORMAL"))
        self.record_path().unlink()
        # The task branch's missing parent (refs/heads/feat) is never created; its nearest existing
        # parent, refs/heads, is probed instead. It is also the symbolic HEAD target directory.
        with self.deny(heads):
            code, result = self.prepare("--starts-from-default", unwritable=False)
        self.assertEqual((code, result["mode"]), (0, "FALLBACK"), result)
        self.assertEqual(self.record()["metadata_failures"], [f"{heads}: Permission denied"])
        self.assertFalse((heads / "feat").exists())

    def test_existing_loose_task_branch_directory_is_probed_directly(self) -> None:
        run_git(self.original, "branch", "feat/other")
        feat = self.git_dir().resolve() / "refs/heads/feat"
        with self.deny(feat):
            failures = fallback.probe_metadata(self.original, TASK_BRANCH)
        self.assertEqual(failures, [f"{feat}: Permission denied"])
        self.assertEqual(fallback.probe_metadata(self.original, TASK_BRANCH), [])

    def test_linked_worktree_probes_the_shared_branch_ref_directory(self) -> None:
        linked = self.base / "linked"
        run_git(self.original, "worktree", "add", "-q", "-b", "side", str(linked))
        heads = self.git_dir().resolve() / "refs/heads"
        with self.deny(heads):
            self.assertEqual(fallback.probe_metadata(linked, TASK_BRANCH), [f"{heads}: Permission denied"])

    def test_malformed_task_branch_never_probes_outside_refs_heads(self) -> None:
        directories = fallback.probe_directories(self.original, "../../../escape")
        common = self.git_dir().resolve()
        self.assertTrue(all(path == common or common in path.parents for path in directories), directories)
        self.assertIn(common / "refs/heads", directories)

    def test_unremovable_probe_blocks_without_clone_and_reports_the_residue(self) -> None:
        with mock.patch.object(fallback.os, "unlink", side_effect=PermissionError(errno.EACCES, "Permission denied")):
            code, result = self.prepare("--starts-from-default", unwritable=False)
        self.assertEqual((code, result["mode"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertIsNone(result["workspace"])
        self.assertEqual(self.workspaces(), [])
        self.assertTrue(any("original preservation" in reason for reason in result["reasons"]), result)
        residue = Path(self.record()["probe_residue"])
        self.assertIn(residue, list(self.git_dir().rglob(fallback.PROBE_PREFIX + "*")))  # probing stopped at once
        # Remove only the synthetic fixture's own probe file.
        self.assertTrue(residue.name.startswith(fallback.PROBE_PREFIX) and self.git_dir().resolve() in residue.parents)
        residue.unlink()
        self.assertEqual(list(self.git_dir().rglob(fallback.PROBE_PREFIX + "*")), [])


class CloneDeliveryTests(GitFixture):
    def setUp(self) -> None:
        super().setUp()
        code, result = self.prepare("--starts-from-default")
        self.assertEqual(code, 0, result)
        self.clone = Path(self.record()["clone_path"])
        self.before = self.original_state()

    def test_claude_workspace_is_the_clone(self) -> None:
        prompt = self.run_dir / "claude-prompt-1.md"
        prompt.write_text("Implement the task.\n", encoding="utf-8")
        results = [subprocess.CompletedProcess([], 0, SUPPORTED_HELP), subprocess.CompletedProcess([], 0)]
        argv = ["run_claude.py", "--project", self.record()["workspace"], "--prompt", str(prompt),
                "--log", str(self.run_dir / "claude-round-1.log")]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(run_claude.subprocess, "run", side_effect=results) as run:
            self.assertEqual(run_claude.main(), 0)
        self.assertEqual(run.call_args.kwargs["cwd"], self.clone)
        self.assertNotEqual(run.call_args.kwargs["cwd"], self.original)

    def test_delivery_is_allowed_only_in_the_clone_on_its_task_branch(self) -> None:
        self.assertEqual(fallback.check_target(self.record(), self.clone), [])
        self.assertIn("delivery must never run in the original checkout", fallback.check_target(self.record(), self.original))
        run_git(self.clone, "switch", "-q", "main")
        self.assertIn("delivery must never target the default branch", fallback.check_target(self.record(), self.clone))
        normal = dict(self.record(), mode="NORMAL")
        self.assertEqual(fallback.check_target(normal, self.clone), ["no isolated clone was selected for this run"])

    def test_commit_and_push_publish_only_the_task_branch_from_the_clone(self) -> None:
        head = self.commit(self.clone, "feature.txt", "task\n")
        calls = []
        real_git = fallback.git
        with mock.patch.object(fallback, "git", side_effect=lambda args, cwd, check=True: calls.append(list(args)) or real_git(args, cwd, check)):
            code, result = self.cli("push", "--record", str(self.record_path()))
        self.assertEqual(code, 0, result)
        self.assertEqual(result, {"published_branch": TASK_BRANCH, "published_head": head})
        self.assertEqual(self.remote_ref(TASK_BRANCH), head)
        self.assertEqual(self.remote_ref("main"), self.fresh_head)
        push = next(args for args in calls if args[0] == "push")
        self.assertFalse({"--force", "-f", "--force-with-lease"} & set(push))
        self.assertFalse(any(part.startswith("+") for part in push))
        self.assertEqual(self.original_state(), self.before)
        self.assertEqual(self.cli("verify-original", "--record", str(self.record_path()))[0], 0)

    def test_push_refuses_the_default_branch(self) -> None:
        run_git(self.clone, "switch", "-q", "main")
        self.commit(self.clone, "direct.txt", "x\n")
        code, result = self.cli("push", "--record", str(self.record_path()))
        self.assertEqual((code, result["status"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertEqual(self.remote_ref("main"), self.fresh_head)
        self.assertEqual(self.remote_ref(TASK_BRANCH), "")

    def test_rejected_push_is_blocked_and_never_forced(self) -> None:
        run_git(self.seed, "push", "origin", f"HEAD:refs/heads/{TASK_BRANCH}")
        self.commit(self.seed, "other.txt", "other\n")
        run_git(self.seed, "push", "origin", f"HEAD:refs/heads/{TASK_BRANCH}")
        existing = self.remote_ref(TASK_BRANCH)
        self.commit(self.clone, "feature.txt", "task\n")
        run_git(self.clone, "fetch", "-q", "origin")  # clone-only; keeps the divergence visible
        code, result = self.cli("push", "--record", str(self.record_path()))
        self.assertEqual((code, result["status"]), (fallback.EXIT_BLOCKED, "BLOCKED"))
        self.assertEqual(self.remote_ref(TASK_BRANCH), existing)

    def test_environmental_push_failure_is_partial(self) -> None:
        self.commit(self.clone, "feature.txt", "task\n")
        self.remote.rename(self.remote.with_name("app-offline.git"))
        code, result = self.cli("push", "--record", str(self.record_path()))
        self.assertEqual((code, result["status"]), (fallback.EXIT_ENVIRONMENT, "PARTIAL"))

    def test_pr_command_targets_the_discovered_default_from_the_clone(self) -> None:
        body = self.run_dir / "pr-body.md"
        code, result = self.cli("pr-command", "--record", str(self.record_path()), "--title", "Add task",
                                "--body-file", str(body))
        self.assertEqual(code, 0)
        self.assertEqual(result["cwd"], str(self.clone))
        argv = result["argv"]
        self.assertEqual(argv[:3], ["gh", "pr", "create"])
        self.assertEqual(argv[argv.index("--base") + 1], "main")
        self.assertEqual(argv[argv.index("--head") + 1], TASK_BRANCH)
        self.assertFalse({"merge", "--auto", "--admin"} & set(argv))


class PolicyTests(unittest.TestCase):
    def facts(self, **overrides):
        fields = dict(valid_worktree=True, metadata_failures=("ro",), starts_from_default=True,
                      default_branch="main", remote_head="a" * 40, task_branch=TASK_BRANCH)
        fields.update(overrides)
        return fallback.Facts(**fields)

    def test_decision_modes(self) -> None:
        self.assertEqual(fallback.decide(self.facts()).mode, "FALLBACK")
        self.assertEqual(fallback.decide(self.facts(metadata_failures=())).mode, "NORMAL")
        self.assertEqual(fallback.decide(self.facts(valid_worktree=False)).mode, "BLOCKED")
        for overrides in ({"remote_reasons": ("ambiguous",)}, {"local_state": ("changes",)},
                          {"starts_from_default": False}, {"default_branch": None}, {"remote_head": None},
                          {"task_branch": ""}, {"task_branch": "main"}, {"task_branch": "--force"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(fallback.decide(self.facts(**overrides)).mode, "BLOCKED")
        self.assertEqual(fallback.decide(self.facts(local_state=("changes",), local_state_irrelevant=True)).mode, "FALLBACK")

    def test_status_semantics(self) -> None:
        self.assertEqual(fallback.run_status("FALLBACK", "COMPLETE", True, True, True), "COMPLETE")
        self.assertEqual(fallback.run_status("FALLBACK", "COMPLETE", False, False, True), "PARTIAL")
        self.assertEqual(fallback.run_status("FALLBACK", "COMPLETE", True, False, True), "PARTIAL")
        self.assertEqual(fallback.run_status("BLOCKED", "COMPLETE", False, False, True), "BLOCKED")
        self.assertEqual(fallback.run_status("FALLBACK", "COMPLETE", True, True, False), "BLOCKED")
        self.assertEqual(fallback.run_status("NORMAL", "COMPLETE", True, True, True), "COMPLETE")

    def test_repository_identity_is_credential_free_and_exact(self) -> None:
        self.assertEqual(fallback.repository_identity("git@github.com:Acme/App.git"), "github.com/acme/app")
        self.assertEqual(fallback.repository_identity("https://github.com/acme/app"), "github.com/acme/app")
        self.assertEqual(fallback.repository_identity("ssh://git@github.com/acme/app.git/"), "github.com/acme/app")
        self.assertTrue(fallback.identity_matches("github.com/acme/app", "acme/app"))
        self.assertTrue(fallback.identity_matches("github.com/acme/app", "github.com/acme/app.git"))
        self.assertFalse(fallback.identity_matches("github.com/evil-acme/app", "acme/app"))
        self.assertFalse(fallback.identity_matches("github.com/acme/app", "app"))
        for url in ("https://token@github.com/acme/app.git", "https://u:p@github.com/acme/app.git",
                    "ssh://git:p@github.com/acme/app.git", "--upload-pack=x", ""):
            with self.subTest(url=url), self.assertRaises(fallback.FallbackError):
                fallback.repository_identity(url)


class CliAndDocumentationTests(GitFixture):
    def test_cli_selects_normal_path_for_writable_metadata(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "clone_fallback.py"), "prepare", "--project", str(self.original),
             "--run-dir", str(self.run_dir), "--remote", "origin", "--expected-repository", "acme/app",
             "--task-branch", TASK_BRANCH],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "NORMAL")
        missing = subprocess.run([sys.executable, str(SCRIPTS / "clone_fallback.py"), "prepare"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(missing.returncode, 2)

    def test_skill_links_the_fallback_phase_and_helper(self) -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("(references/isolated-clone-fallback.md)", skill)
        reference = (SKILL_DIR / "references/isolated-clone-fallback.md").read_text(encoding="utf-8")
        self.assertIn("scripts/clone_fallback.py", reference)


if __name__ == "__main__":
    unittest.main()
