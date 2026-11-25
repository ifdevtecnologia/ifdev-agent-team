"""Host-side Git and file-tree helpers shared by the harness modules.

Host-side Git only ever runs on harness-owned repositories (the task snapshot copies and the patch
repository), never on the agent's `.git`: repository configuration written by the agent could make
Git run programs on the host (filters, fsmonitor, lazy fetches from a "promisor" remote).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

SYNTHETIC_NAME = "Bench Harness"
SYNTHETIC_EMAIL = "bench-harness@example.invalid"


def isolated_git_env(home: Path) -> dict[str, str]:
    """Environment for host-side Git: no system or global configuration from the machine, no lazy
    fetches and a synthetic identity."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_AUTHOR_NAME": SYNTHETIC_NAME,
        "GIT_AUTHOR_EMAIL": SYNTHETIC_EMAIL,
        "GIT_COMMITTER_NAME": SYNTHETIC_NAME,
        "GIT_COMMITTER_EMAIL": SYNTHETIC_EMAIL,
        "GIT_TERMINAL_PROMPT": "0",
    }


def git(args: list[str], env: dict[str, str], cwd: Path | None = None, binary: bool = False):
    """Runs Git with hooks, fsmonitor and every transport disabled."""
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=not binary,
        check=True,
        timeout=300,
    )


def _not_copied(directory: str, names: list[str]) -> list[str]:
    """`.git` and special files (sockets, FIFOs, devices), which are never copied."""
    skipped = []

    for name in names:
        if name == ".git":
            skipped.append(name)
            continue

        mode = os.lstat(os.path.join(directory, name)).st_mode

        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            skipped.append(name)

    return skipped


def copy_tree(source: Path, destination: Path) -> None:
    """Copies a tree without its .git directory and special files, keeping symlinks as links
    (never following them)."""
    shutil.copytree(source, destination, symlinks=True, ignore=_not_copied, dirs_exist_ok=True)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def read_head(work: Path) -> str | None:
    """The commit the agent's HEAD points to, read from the files without running Git and without
    following symlinks; None when it cannot be read safely."""
    git_dir = work / ".git"

    def regular_text(path: Path) -> str | None:
        if path.is_symlink() or not path.is_file():
            return None

        try:
            with open(path, "rb") as handle:
                return handle.read(4096).decode("utf-8", errors="replace").strip()
        except OSError:
            return None

    if git_dir.is_symlink() or not git_dir.is_dir():
        return None

    head = regular_text(git_dir / "HEAD")

    if head is None or not head.startswith("ref: "):
        return head

    ref = head[5:].strip()

    real_git_dir = os.path.realpath(git_dir)

    if not ref.startswith("refs/") or ".." in ref.split("/"):
        return None

    if os.path.commonpath([real_git_dir, os.path.realpath(git_dir / ref)]) != real_git_dir:
        return None

    loose = regular_text(git_dir / ref)

    if loose is not None:
        return loose

    for line in (regular_text(git_dir / "packed-refs") or "").splitlines():
        parts = line.split()

        if len(parts) == 2 and parts[1] == ref:
            return parts[0]

    return None
