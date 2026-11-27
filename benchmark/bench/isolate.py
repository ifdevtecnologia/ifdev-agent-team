"""Per-run isolation (ADR 0001, "Per-run isolation", steps 1 to 3).

Each run gets RUN_ROOT under BENCH_RUNS_DIR with home/ (the temporary HOME), work/ (the
workspace), toolkit/ (the pinned toolkit export, conditions B and C) and out/ (the outputs).
Conditions B and C run without the process-metrics instrument (ADR 0001, "Amendment: toolkit
treatment for conditions B and C"): the toolkit is installed unchanged and the run's HOME is
treated afterwards.
"""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from bench.gitops import SYNTHETIC_EMAIL, SYNTHETIC_NAME, copy_tree, git, isolated_git_env
from bench.taskspec import Task

RUN_SUBDIRS = ("home", "work", "toolkit", "out")
# Never exported into toolkit/: conditions B and C must not see the harness (code, tests and protocol)
# that A does not see.
EXCLUDED_FROM_TOOLKIT = ("benchmark", "docs/benchmark", "docs/decisions/0001-benchmark-protocol.md", "tests")
CONDITIONS_WITH_TOOLKIT = ("B", "C")
# A revision passed to Git (a SHA, branch or HEAD~1): never an option, a path or a range.
GIT_REVISION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._/^~-]{0,254}")
FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# The process-metrics instrument, removed from the run HOME of B and C. Paths are relative to the run HOME.
INSTRUMENT_SKILL = Path(".claude/skills/process-metrics")
INSTRUMENT_TOKEN = re.compile(r"process[- ]metrics", re.IGNORECASE)
# (file, rule name, fragment), applied in order: the skill-list fragment of CLAUDE.md goes after its paragraph.
INSTRUMENT_EDITS = (
    (".claude/CLAUDE.md", "paragraph", re.compile(r"^\*\*Process metrics\.\*\*[^\n]*\n\n", re.M)),
    (".claude/CLAUDE.md", "skill_list_item", re.compile(r", `process-metrics` \([^)\n]*\)")),
    (".claude/rules/git-workflow.md", "bullet", re.compile(r"^- [^\n]*`process-metrics`[^\n]*\n", re.M)),
)
# Session data the agent writes under the config directory, not loaded toolkit content.
UNSCANNED_CONFIG_DIRS = ("projects",)


class IsolationError(RuntimeError):
    """The run directory or the toolkit could not be prepared safely."""


class PreparedRun(NamedTuple):
    run_root: Path
    base_commit: str
    # What remove_instrument did; None for condition A, which installs no toolkit.
    toolkit_treatment: dict[str, object] | None


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False

    return True


def is_repository(path: Path) -> bool:
    """A Git work tree root (a `.git` file, or a `.git` directory with HEAD) or a bare repository."""
    marker = path / ".git"

    return marker.is_file() or (marker / "HEAD").is_file() or ((path / "HEAD").is_file() and (path / "objects").is_dir())


def check_runs_dir(runs_dir: Path) -> Path:
    """Returns the resolved BENCH_RUNS_DIR, refusing one inside the real HOME or a Git repository."""
    if not runs_dir.is_absolute():
        raise IsolationError(f"BENCH_RUNS_DIR must be an absolute path: {runs_dir}")

    resolved = runs_dir.resolve()
    home = Path.home().resolve()

    if _inside(resolved, home):
        raise IsolationError(f"BENCH_RUNS_DIR must be outside the real home directory: {resolved}")

    for candidate in (resolved, *resolved.parents):
        if is_repository(candidate):
            raise IsolationError(f"BENCH_RUNS_DIR must be outside any Git repository: {resolved} is inside {candidate}")

    resolved.mkdir(parents=True, exist_ok=True)

    return resolved


def create_run_root(runs_dir: Path, run_id: str) -> Path:
    run_root = Path(tempfile.mkdtemp(prefix=f"{run_id}-", dir=runs_dir))

    for name in RUN_SUBDIRS:
        (run_root / name).mkdir()

    (run_root / "home" / ".claude").mkdir()

    return run_root


def populate_workspace(task: Task, project_claude: Path, destination: Path) -> None:
    """The run's starting tree: the frozen snapshot plus the project CLAUDE.md shared by A, B and C."""
    copy_tree(task.snapshot, destination)
    shutil.copyfile(project_claude, destination / "CLAUDE.md")


def init_workspace(run_root: Path, task: Task, project_claude: Path) -> str:
    """Fills work/, commits it once with a synthetic author and no remote, writes a synthetic Git
    identity for the agent and returns the base commit."""
    home, work = run_root / "home", run_root / "work"
    populate_workspace(task, project_claude, work)
    (home / ".gitconfig").write_text(f"[user]\n\tname = Bench Agent\n\temail = {SYNTHETIC_EMAIL}\n", encoding="utf-8")
    env = isolated_git_env(home)
    git(["init", "-q"], env, cwd=work)
    git(["symbolic-ref", "HEAD", "refs/heads/main"], env, cwd=work)
    git(["add", "-A", "."], env, cwd=work)
    git(["commit", "-q", "-m", f"{task.id}: starting snapshot", "--author", f"{SYNTHETIC_NAME} <{SYNTHETIC_EMAIL}>"], env, cwd=work)

    return git(["rev-parse", "HEAD"], env, cwd=work).stdout.strip()


def resolve_sha(repo: Path, sha: str) -> str:
    """Full SHA of an existing commit in <repo>; refuses names that do not resolve to a commit."""
    revision = _checked_revision(sha)

    try:
        result = git(["-C", str(repo), "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"], isolated_git_env(repo))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise IsolationError(f"toolkit SHA {sha!r} is not a commit of {repo}") from error

    full = result.stdout.strip()

    if not FULL_SHA.fullmatch(full):
        raise IsolationError(f"toolkit SHA {sha!r} did not resolve to a commit SHA")

    return full


def _checked_revision(revision: str) -> str:
    """<revision> when it is a plain Git revision, so it can never be read as an option."""
    if not GIT_REVISION.fullmatch(revision or ""):
        raise IsolationError(f"invalid toolkit SHA: {revision!r}")

    return revision


def _excluded(name: str) -> bool:
    path = PurePosixPath(name)

    return any(path == PurePosixPath(p) or PurePosixPath(p) in path.parents for p in EXCLUDED_FROM_TOOLKIT)


def _link_stays_inside(member: tarfile.TarInfo) -> bool:
    """Symlink targets are relative to the link's directory, hard link targets to the archive root."""
    if member.linkname.startswith("/"):
        return False

    base = posixpath.dirname(member.name) if member.issym() else ""
    target = posixpath.normpath(posixpath.join(base, member.linkname))

    return target != ".." and not target.startswith("../")


def export_toolkit(repo: Path, sha: str, destination: Path) -> None:
    """`git archive` of the pinned SHA into <destination>, without the harness paths."""
    revision = _checked_revision(sha)
    archive = git(["-C", str(repo), "archive", "--format=tar", revision], isolated_git_env(repo), binary=True).stdout

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = []

        for member in tar.getmembers():
            path = PurePosixPath(member.name)

            if path.is_absolute() or ".." in path.parts:
                raise IsolationError(f"unsafe path in toolkit archive: {member.name}")

            if (member.issym() or member.islnk()) and not _link_stays_inside(member):
                raise IsolationError(f"unsafe link in toolkit archive: {member.name}")

            if not _excluded(member.name):
                members.append(member)

        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, members=members, filter="tar")
        else:  # Python < 3.12 without the extraction filters backport
            tar.extractall(destination, members=members)  # noqa: S202 - names checked above


def install_toolkit(run_root: Path) -> None:
    """Runs the exported installer with only PATH and the temporary HOME (`env -i` equivalent)."""
    installer = run_root / "toolkit" / "scripts" / "install.sh"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(run_root / "home")}
    result = subprocess.run(
        ["bash", str(installer), "--target", "claude"],
        cwd=run_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    if result.returncode != 0:
        raise IsolationError(f"toolkit install failed with exit code {result.returncode}: {result.stderr[-2000:]}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remove_tree(path: Path) -> None:
    """shutil.rmtree, refused on platforms where it can be redirected through a symlink race."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise IsolationError("shutil.rmtree is not safe against symlink attacks on this platform")

    shutil.rmtree(path)


def _read_editable(target: Path, rel: str) -> str:
    """The text of a file the treatment may edit: a regular file with a single link, or "" when missing."""
    try:
        status = os.lstat(target)
    except FileNotFoundError:
        return ""

    if not stat.S_ISREG(status.st_mode) or status.st_nlink > 1:
        raise IsolationError(f"{rel} is not a regular file with a single link")

    return target.read_bytes().decode("utf-8")


def _replace_file(target: Path, text: str) -> None:
    """Writes <text> to a new file next to <target> and renames it over the name, so the original
    inode is never truncated."""
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("utf-8"))

        os.chmod(temporary, stat.S_IMODE(os.lstat(target).st_mode))
        os.replace(temporary, target)
    except BaseException:
        os.unlink(temporary)
        raise


def _remove_instrument_skill(run_root: Path, home: Path) -> bool:
    """Removes the installed skill (a link into toolkit/, or a directory in the run HOME) and, for a
    link, its source in toolkit/, which the agent could otherwise read through the mounted run root.
    Returns whether the skill was installed."""
    skill = home / INSTRUMENT_SKILL

    if not os.path.lexists(skill):
        return False

    if not _inside(skill.parent.resolve(), home):
        raise IsolationError(f"{INSTRUMENT_SKILL} is not inside the run home")

    if skill.is_symlink():
        toolkit = run_root / "toolkit"
        source = skill.resolve()

        if source == toolkit or not _inside(source, toolkit):
            raise IsolationError(f"{INSTRUMENT_SKILL} links outside the toolkit export")

        skill.unlink()

        if source.is_dir():
            _remove_tree(source)
        elif source.exists():
            source.unlink()

        return True

    if skill.is_dir():
        _remove_tree(skill)

        return True

    raise IsolationError(f"{INSTRUMENT_SKILL} is neither a link into the toolkit export nor a directory")


def _remove_instrument_text(run_root: Path, home: Path) -> list[dict[str, object]]:
    """Removes the instrument's fragments from the loaded files, editing the resolved file only
    inside the run root and only when it is a regular file with a single link. Missing files count
    as zero removals."""
    removed = []

    for rel, rule, fragment in INSTRUMENT_EDITS:
        target = (home / rel).resolve()

        if not _inside(target, run_root):
            raise IsolationError(f"{rel} resolves outside the run directory")

        text = _read_editable(target, rel)
        matches = fragment.findall(text)

        if matches:
            _replace_file(target, fragment.sub("", text))

        removed.append({
            "path": rel,
            "rule": rule,
            "count": len(matches),
            "removed_sha256": _sha256("".join(matches).encode("utf-8")) if matches else None,
        })

    return removed


def _loaded_files(run_root: Path, config: Path) -> dict[str, Path]:
    """Every regular file reachable under the config directory (links followed, each directory
    visited once), keyed by its path relative to the config directory. A link that leaves the run
    root is refused, never followed."""
    files: dict[str, Path] = {}
    pending, visited = [config], set()

    while pending:
        directory = pending.pop()
        real = directory.resolve()

        if real in visited:
            continue

        visited.add(real)

        # Lists the resolved directory, so a link swapped after the check is never followed.
        for entry in sorted(real.iterdir()):
            logical = directory / entry.name
            rel = logical.relative_to(config).as_posix()

            if rel in UNSCANNED_CONFIG_DIRS:
                continue

            target = entry.resolve()

            if not _inside(target, run_root):
                raise IsolationError(f"{rel} resolves outside the run directory")

            if target.is_dir():
                pending.append(logical)
            elif target.is_file():
                files[rel] = target

    return dict(sorted(files.items()))


def remove_instrument(run_root: Path) -> dict[str, object]:
    """Leaves the run HOME of B and C without the process-metrics instrument: the skill removed,
    its fragments cut from the loaded instructions and no mention left in any loaded file. Fails
    closed (IsolationError) on any layout it does not recognise. Reads and writes only inside
    <run_root>; returns the treatment record with the digest of every loaded file."""
    run_root = run_root.resolve()
    home = run_root / "home"
    skill_removed = _remove_instrument_skill(run_root, home)
    removed = _remove_instrument_text(run_root, home)
    counts = [entry["count"] for entry in removed]
    expected = 1 if skill_removed else 0

    if any(count != expected for count in counts):
        state = "installed" if skill_removed else "absent"
        raise IsolationError(f"unexpected process-metrics layout: skill {state}, fragment counts {counts}")

    config = home / ".claude"
    loaded = _loaded_files(run_root, config)
    digests = {}

    for rel, path in loaded.items():
        data = path.read_bytes()

        if INSTRUMENT_TOKEN.search(data.decode("utf-8", errors="replace")):
            raise IsolationError(f"process-metrics still mentioned in {rel}")

        digests[rel] = _sha256(data)

    return {"instrument": "removed" if skill_removed else "absent", "removed": removed, "loaded_sha256": digests}


def prepare_run(
    runs_dir: Path, run_id: str, task: Task, condition: str, project_claude: Path, toolkit_repo: Path, toolkit_sha: str | None
) -> PreparedRun:
    """Creates and fills RUN_ROOT for one run; returns it with the workspace's base commit and,
    for B and C, the toolkit treatment."""
    run_root = create_run_root(runs_dir, run_id)
    base_commit = init_workspace(run_root, task, project_claude)

    if condition not in CONDITIONS_WITH_TOOLKIT:
        return PreparedRun(run_root, base_commit, None)

    if not toolkit_sha:
        raise IsolationError(f"condition {condition} needs a pinned toolkit SHA")

    export_toolkit(toolkit_repo, toolkit_sha, run_root / "toolkit")
    install_toolkit(run_root)

    return PreparedRun(run_root, base_commit, remove_instrument(run_root))
