"""Evaluation after the agent exits: the final patch and the hidden, pre-existing and build checks.

Everything happens in a harness-owned scratch directory outside RUN_ROOT, which the agent's
container mounted. The agent's tree is copied once (without special files), its protected paths
are restored from the snapshot, and every check then runs in a fresh evaluator container without
network, on its own fresh copy of that restored tree: build and the pre-existing suite without the
hidden tests, and each hidden test with a fresh copy of the task's hidden/ added. So no check can
change what a later check sees, and hidden tests never enter the agent's container.

Host-side Git never runs on the agent's `.git` (see bench.gitops): the patch is computed in a
separate, harness-owned Git directory and the agent's HEAD is read from the files.
"""

from __future__ import annotations

import filecmp
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from bench.containers import ContainerSpec, run_container
from bench.gitops import copy_tree, git, isolated_git_env, read_head, remove_path
from bench.isolate import populate_workspace
from bench.taskspec import Task

HIDDEN_DIR = "bench_hidden"
EVALUATOR_NETWORK = "none"
DEFAULT_COMMAND_TIMEOUT_S = 900
# Layout of each check's fresh copy: the workspace tree and the evaluator's HOME, side by side.
CHECK_TREE = "tree"
CHECK_HOME = "home"
COPY_ERRORS = (OSError, shutil.Error)
# Caches that running tools creates; left out of both sides of the patch so a run that only ran the
# tests still has an empty diff. Everything else is added with --force on both sides, so neither the
# snapshot's nor the agent's .gitignore can hide a file from the patch.
PATCH_EXCLUDES = tuple(
    f":(exclude,glob){pattern}"
    for pattern in ("**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**")
)


def final_patch(base_tree: Path, work: Path, scratch: Path) -> bytes:
    """Diff of the agent's working tree (committed or not, untracked files included) against the
    base tree, computed in a Git directory the agent never had access to. The agent's own `.git` is
    never read: an explicit --git-dir disables repository discovery."""
    env = isolated_git_env(scratch)
    git_dir = scratch / "patch.git"
    git(["init", "-q", "--bare", str(git_dir)], env)
    add = ["-c", f"core.excludesFile={os.devnull}", "add", "-A", "--force", "--", ".", *PATCH_EXCLUDES]
    base = ["--git-dir", str(git_dir), "--work-tree", str(base_tree)]
    git([*base, *add], env, cwd=base_tree)
    git([*base, "commit", "-q", "--allow-empty", "-m", "base"], env, cwd=base_tree)
    after = ["--git-dir", str(git_dir), "--work-tree", str(work)]
    git([*after, *add], env, cwd=work)

    return git([*after, "diff", "--cached", "--binary", "HEAD"], env, cwd=work, binary=True).stdout


def agent_committed(work: Path, base_commit: str) -> bool:
    """Whether the agent's HEAD moved away from the base commit (new commits, or a reset)."""
    head = read_head(work)

    return head is not None and head != base_commit


def _same_tree(left: Path, right: Path) -> bool:
    """Exact comparison, without filecmp's default ignore list (.git, CVS, ...) except __pycache__,
    which running the tests creates. The restore is unconditional, so ignored caches are still replaced."""
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)

    if left.is_file() and right.is_file():
        return filecmp.cmp(left, right, shallow=False)

    if left.is_dir() and right.is_dir():
        comparison = filecmp.dircmp(left, right, ignore=["__pycache__"])

        if comparison.left_only or comparison.right_only or comparison.funny_files:
            return False

        return all(_same_tree(left / name, right / name) for name in comparison.common_files + comparison.common_dirs)

    return not left.exists() and not right.exists()


def _unlink_symlinked_parents(workspace: Path, rel: str) -> bool:
    """Removes parent components of <rel> inside <workspace> that are symlinks or files, so that
    restoring a protected path never writes or deletes through a link the agent created."""
    found = False
    current = workspace

    for part in Path(rel).parts[:-1]:
        current = current / part

        if current.is_symlink() or (current.exists() and not current.is_dir()):
            current.unlink()
            found = True

    return found


def restore_protected(task: Task, workspace: Path) -> list[str]:
    """Always restores every protected path from the snapshot; returns the ones that had changed."""
    changed = []

    for rel in task.protected_paths:
        original, current = task.snapshot / rel, workspace / rel
        relinked = _unlink_symlinked_parents(workspace, rel)

        if relinked or not _same_tree(original, current):
            changed.append(rel)

        remove_path(current)

        if original.is_dir() and not original.is_symlink():
            copy_tree(original, current)
        elif original.exists() or original.is_symlink():
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, current, follow_symlinks=False)

    return changed


def _check(task: Task, workspace: Path, command: str, log, timeout_s: float) -> dict[str, object]:
    """Runs <command> in <workspace>, a fresh copy made by _fresh_copy: the container mounts the copy's
    parent, so HOME is the copy's own empty directory next to the tree, never a shared one."""
    check_dir = workspace.parent
    spec = ContainerSpec(
        image=task.image,
        name=f"bench-eval-{uuid.uuid4()}",
        mount=check_dir,
        workdir=workspace,
        network=EVALUATOR_NETWORK,
        command=("sh", "-c", command),
        env=(("HOME", str(check_dir / CHECK_HOME)),),
    )
    log.write(f"$ {command}\n".encode())
    log.flush()
    code, timed_out = run_container(spec, timeout_s, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)

    return {"exit_code": code, "passed": code == 0 and not timed_out, "timed_out": timed_out}


def _fresh_copy(restored: Path, scratch: Path, name: str, task: Task | None = None) -> Path:
    """<scratch>/<name>/tree, a copy of <restored>, next to an empty <scratch>/<name>/home."""
    (scratch / name / CHECK_HOME).mkdir(parents=True)
    destination = scratch / name / CHECK_TREE
    copy_tree(restored, destination)

    if task is not None:
        hidden = destination / HIDDEN_DIR
        remove_path(hidden)
        copy_tree(task.hidden, hidden)

    return destination


def run_checks(task: Task, restored: Path, log_path: Path, timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> dict:
    """Runs build, the pre-existing suite and each hidden test file, each in its own evaluator
    container on a fresh copy of <restored>, which is never changed. Returns numbers and categories."""
    scratch = Path(tempfile.mkdtemp(prefix="checks-", dir=restored.parent))

    try:
        with open(log_path, "ab") as log:
            build = None

            if task.build_command:
                build = _check(task, _fresh_copy(restored, scratch, "build"), task.build_command, log, timeout_s)

            suite = _check(task, _fresh_copy(restored, scratch, "suite"), task.test_command, log, timeout_s)
            hidden = []

            for index, (test, criterion) in enumerate(sorted(task.hidden_tests.items())):
                workspace = _fresh_copy(restored, scratch, f"hidden-{index}", task)
                command = task.hidden_command_for(shlex.quote(f"{HIDDEN_DIR}/{test}"))
                hidden.append({"test": test, "criterion": criterion, **_check(task, workspace, command, log, timeout_s)})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    criteria = {c: all(h["passed"] for h in hidden if h["criterion"] == c) for c in task.criteria}

    return {
        "build": build,
        "suite": suite,
        "hidden": hidden,
        "criteria": criteria,
        "hidden_passed": sum(1 for h in hidden if h["passed"]),
        "hidden_total": len(hidden),
    }


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _invalid(task: Task, out: Path, committed: bool, patch: bytes) -> dict:
    """Result of a run whose workspace could not be evaluated: the agent's failure, not retried."""
    evaluation = {
        "workspace_invalid": True, "patch_error": not patch, "patch_bytes": len(patch), "empty_diff": not patch.strip(),
        "committed": committed, "protected_changed": [], "build": None, "suite": None, "hidden": [],
        "criteria": {c: False for c in task.criteria}, "hidden_passed": 0, "hidden_total": len(task.hidden_tests),
    }
    _write_json(out / "eval.json", evaluation)

    return evaluation


def evaluate_run(
    task: Task, run_root: Path, out: Path, project_claude: Path, base_commit: str, timeout_s: float, scratch_parent: Path
) -> dict:
    """Saves final.patch, eval.json and eval.log in <out> for a finished agent run and returns the
    evaluation. <out> and <scratch_parent> must be outside RUN_ROOT."""
    work = run_root / "work"
    scratch = Path(tempfile.mkdtemp(prefix="eval-", dir=scratch_parent))

    try:
        if work.is_symlink() or not work.is_dir():
            # The agent replaced its workspace: nothing is read through it and the run fails.
            (out / "final.patch").write_bytes(b"")

            return _invalid(task, out, False, b"")

        committed = agent_committed(work, base_commit)
        # Rebuilt from the task, since the agent could have changed anything inside RUN_ROOT.
        base_tree = scratch / "base"
        populate_workspace(task, project_claude, base_tree)
        evaluation: dict[str, object] = {"workspace_invalid": False, "patch_error": False}

        try:
            patch = final_patch(base_tree, work, scratch)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            patch, evaluation["patch_error"] = b"", True

        (out / "final.patch").write_bytes(patch)

        try:
            restored = scratch / "restored"
            copy_tree(work, restored)
            protected_changed = restore_protected(task, restored)
        except COPY_ERRORS:
            # Unreadable files left by the agent: its failure, recorded as a finished run.
            return _invalid(task, out, committed, patch)

        evaluation.update(
            patch_bytes=len(patch),
            empty_diff=not patch.strip(),
            committed=committed,
            protected_changed=protected_changed,
            **run_checks(task, restored, out / "eval.log", timeout_s),
        )
        _write_json(out / "eval.json", evaluation)

        return evaluation
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
