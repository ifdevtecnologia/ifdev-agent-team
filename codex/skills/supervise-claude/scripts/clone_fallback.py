#!/usr/bin/env python3
"""Isolated writable clone fallback for a checkout whose Git metadata is not operable.

Fail-closed. The original checkout is only ever inspected through read-only Git commands (with
optional locks disabled) plus a create-and-remove write probe of its metadata directories; it is
never fetched, switched, staged, committed, reset, stashed, restored, cleaned, reconfigured or
permission-changed. Codex attests the task-context facts only it can judge (relevance of local
state, starting from the remote default branch, the expected repository); everything else is
discovered here. Delivery then happens only in the recorded clone, on its task branch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
# The only Git subcommands ever run against the original checkout.
ORIGINAL_READ_ONLY = frozenset({"rev-parse", "status", "for-each-ref", "rev-list"})
PROBE_PREFIX = ".supervisor-write-probe-"
RECORD_NAME = "workspace.json"
REJECTED_PUSH = re.compile(r"\[(?:remote )?rejected\]|non-fast-forward|fetch first", re.IGNORECASE)
SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")

EXIT_BLOCKED = 1
EXIT_ENVIRONMENT = 3


class FallbackError(RuntimeError):
    """Unsafe or ambiguous condition: BLOCKED."""


class EnvironmentFailure(RuntimeError):
    """Non-code environmental failure (network, authentication): PARTIAL delivery."""


def git(args: list, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        # stderr can echo remote URLs; report only the subcommand.
        raise FallbackError(f"git {args[0]} failed with exit code {result.returncode}")
    return result


def inspect_original(args: list, root: Path, check: bool = True) -> str:
    read_only = args[0] in ORIGINAL_READ_ONLY or args == ["remote"] or args[:2] == ["remote", "get-url"]
    if not read_only:
        raise FallbackError(f"refusing non-read-only Git command on the original checkout: git {args[0]}")
    return git(args, root, check).stdout


def resolve_worktree(project: Path) -> Path | None:
    try:
        project = project.resolve(strict=True)
        inside = git(["rev-parse", "--is-inside-work-tree"], project, check=False)
        top = git(["rev-parse", "--show-toplevel"], project, check=False)
    except OSError:
        return None
    if inside.returncode or inside.stdout.strip() != "true" or top.returncode or not top.stdout.strip():
        return None
    return Path(top.stdout.strip()).resolve()


class ProbeResidueError(FallbackError):
    """A probe file could not be removed from the original's metadata: never select a clone."""

    def __init__(self, path: str) -> None:
        super().__init__(f"original preservation: write probe could not be removed from the original Git metadata, "
                         f"clean it up manually before any retry: {path}")
        self.path = path


def _update_parent(base: Path, ref: str) -> Path:
    """Nearest existing directory a loose update of ref writes into; Git itself creates the rest, so
    a missing directory is never created here."""
    directory = base.joinpath(*ref.split("/")[:-1])
    while directory != base and not directory.is_dir():
        directory = directory.parent
    return directory


def probe_directories(root: Path, task_branch: str = "") -> list:
    """Metadata directories a task-branch switch, commit and ref update write into."""
    git_dir = Path(root, inspect_original(["rev-parse", "--git-dir"], root).strip()).resolve()
    common = Path(root, inspect_original(["rev-parse", "--git-common-dir"], root).strip()).resolve()
    if not task_branch or any(part in ("", ".", "..") for part in task_branch.split("/")):
        task_branch = "-"  # missing or malformed: still probe refs/heads itself, never outside it
    refs = ["refs/heads/" + task_branch]
    head = inspect_original(["rev-parse", "--symbolic-full-name", "HEAD"], root, check=False).strip()
    if head.startswith("refs/heads/"):
        refs.append(head)
    directories = [git_dir, common, common / "objects", common / "refs", _update_parent(git_dir, "logs/HEAD")]
    for ref in refs:
        directories += [_update_parent(common, ref), _update_parent(common, "logs/" + ref)]
    if (common / "reftable").is_dir():
        directories.append(common / "reftable")
    return list(dict.fromkeys(directories))


def probe_metadata(root: Path, task_branch: str = "") -> list:
    """Directories where actually creating a file failed. Never judged from mode bits alone:
    read-only mounts, ACLs and sandboxes only show up when a write is attempted. A probe file that
    cannot be removed raises ProbeResidueError at once instead of counting as unwritable."""
    failures = []
    for directory in probe_directories(root, task_branch):
        try:
            handle, name = tempfile.mkstemp(prefix=PROBE_PREFIX, dir=directory)
        except OSError as exc:
            failures.append(f"{directory}: {exc.strerror or type(exc).__name__}")
            continue
        os.close(handle)
        try:
            os.unlink(name)
        except OSError as exc:
            raise ProbeResidueError(name) from exc
    return failures


def _strip_repository(location: str) -> str:
    location = location.strip().strip("/").lower()
    if location.endswith(".git"):
        location = location[:-4]
    return location.strip("/")


def repository_identity(url: str) -> str:
    """Credential-free host/path identity. Refuses URLs that embed credentials."""
    if not url or url.startswith("-"):
        raise FallbackError("remote URL is not usable")
    if "://" in url:
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
        except ValueError as exc:
            raise FallbackError("remote URL is not parseable") from exc
        if parts.password or (parts.scheme in ("http", "https") and parts.username):
            raise FallbackError("remote URL embeds credentials")
        location = f"{host}/{parts.path.lstrip('/')}"
    elif match := SCP_LIKE.match(url):
        location = f"{match['host']}/{match['path']}"
    else:
        location = url
    return _strip_repository(location)


def identity_matches(identity: str, expected: str) -> bool:
    expected = _strip_repository(expected)
    if "/" not in expected:
        return False
    return identity == expected or identity.endswith("/" + expected)


def resolve_remote(root: Path, name: str, expected: str) -> tuple:
    """Returns (url, identity, reasons); url is kept in memory only, never recorded."""
    if not name or name.startswith("-") or name not in inspect_original(["remote"], root).split():
        return None, None, [f"delivery remote is not configured: {name}"]
    fetch = inspect_original(["remote", "get-url", "--all", name], root).splitlines()
    push = inspect_original(["remote", "get-url", "--push", "--all", name], root).splitlines()
    if len(fetch) != 1 or push != fetch:
        return None, None, [f"ambiguous delivery remote {name}: fetch and push must be one identical URL"]
    try:
        identity = repository_identity(fetch[0])
    except FallbackError as exc:
        return None, None, [f"untrusted delivery remote {name}: {exc}"]
    if not identity_matches(identity, expected):
        return None, None, [f"delivery remote {name} does not match the expected repository {expected}"]
    return fetch[0], identity, []


def local_state(root: Path) -> list:
    """Staged, unstaged, untracked, stashed or unpublished local-only state."""
    state = []
    entries = [entry for entry in inspect_original(["status", "--porcelain=v1", "-z", "-uall"], root).split("\0") if entry]
    if entries:
        state.append("staged, unstaged or untracked changes")
    if inspect_original(["for-each-ref", "--count=1", "refs/stash"], root).strip():
        state.append("stash entries")
    unpublished = inspect_original(["rev-list", "--count", "--branches", "--not", "--remotes"], root)
    if unpublished.strip() != "0":
        state.append("local commits not on any remote-tracking ref")
    return state


def discover_default(url: str, cwd: Path) -> tuple:
    """Fresh remote default branch and head, never the local or cached remote HEAD."""
    result = git(["ls-remote", "--symref", url, "HEAD"], cwd, check=False)
    if result.returncode != 0:
        raise EnvironmentFailure("remote default branch discovery failed (network or authentication)")
    branch = head = None
    for line in result.stdout.splitlines():
        value, _, ref = line.partition("\t")
        if ref != "HEAD":
            continue
        if value.startswith("ref: refs/heads/"):
            branch = value[len("ref: refs/heads/") :]
        elif re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            head = value
    if not branch or not head or branch.startswith("-"):
        raise FallbackError("remote default branch is ambiguous or missing")
    return branch, head


def _status_paths(status: str) -> list:
    paths, fields = [], iter(field for field in status.split("\0") if field)
    for entry in fields:
        paths.append(entry[3:])
        if entry[:1] in ("R", "C"):
            paths.append(next(fields, ""))
    return paths


def _stat(path: Path) -> list | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return [info.st_mode, info.st_size, info.st_mtime_ns]


def snapshot(root: Path) -> dict:
    """Metadata-only fingerprint of the original: names and stat data, never file contents."""
    status = inspect_original(["status", "--porcelain=v1", "-z", "-uall"], root)
    git_dir = Path(root, inspect_original(["rev-parse", "--git-dir"], root).strip()).resolve()
    state = {
        "head": inspect_original(["rev-parse", "--verify", "--quiet", "HEAD"], root, check=False).strip() or None,
        "branch": inspect_original(["rev-parse", "--abbrev-ref", "HEAD"], root, check=False).strip() or None,
        "status": status,
        "refs": inspect_original(["for-each-ref", "--format=%(refname) %(objectname)"], root),
        "paths": {path: _stat(root / path) for path in _status_paths(status)},
        # Not the index: a plain `git status` may refresh its stat cache. Staging shows up in status.
        "metadata": {name: _stat(git_dir / name) for name in ("HEAD", "config", "packed-refs")},
    }
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()
    return {"head": state["head"], "branch": state["branch"], "fingerprint": digest, "state": state}


@dataclass(frozen=True)
class Facts:
    valid_worktree: bool
    metadata_failures: tuple = ()
    remote_reasons: tuple = ()
    local_state: tuple = ()
    local_state_irrelevant: bool = False
    starts_from_default: bool = False
    default_branch: str | None = None
    remote_head: str | None = None
    task_branch: str = ""


@dataclass(frozen=True)
class Decision:
    mode: str  # NORMAL, FALLBACK or BLOCKED
    reasons: tuple = ()


def decide(facts: Facts) -> Decision:
    if not facts.valid_worktree:
        return Decision("BLOCKED", ("original is not a valid Git worktree",))
    if not facts.metadata_failures:
        return Decision("NORMAL")
    reasons = list(facts.remote_reasons)
    if facts.local_state and not facts.local_state_irrelevant:
        reasons += [f"relevant local-only state may be needed: {item}" for item in facts.local_state]
    if not facts.starts_from_default:
        reasons.append("task was not confirmed to start entirely from the remote default branch")
    if not facts.remote_reasons and not (facts.default_branch and facts.remote_head):
        reasons.append("remote default branch could not be discovered for a network clone")
    if not facts.task_branch or facts.task_branch.startswith("-"):
        reasons.append("a dedicated task branch is required")
    elif facts.task_branch == facts.default_branch:
        reasons.append("task branch must not be the remote default branch")
    return Decision("BLOCKED" if reasons else "FALLBACK", tuple(reasons))


def run_status(mode: str, implementation: str, published: bool, pr_created: bool, original_unchanged: bool) -> str:
    """Overall run status for a fallback-aware run."""
    if mode == "BLOCKED" or implementation == "BLOCKED" or (mode == "FALLBACK" and not original_unchanged):
        return "BLOCKED"
    if implementation == "COMPLETE" and published and pr_created:
        return "COMPLETE"
    # Usable implementation with limitations, or environmental publication/PR failure.
    return "PARTIAL"


def _outside(path: Path, root: Path) -> bool:
    return path != root and root not in path.parents


def create_clone(record: dict, run_dir: Path, url: str) -> None:
    """Unique clone inside the external run directory; retained (never deleted) for the whole run."""
    clone = Path(tempfile.mkdtemp(prefix="workspace-", dir=run_dir)) / "repo"
    record["clone_path"] = str(clone)
    remote, default_branch = record["remote"], record["default_branch"]
    if git(["clone", "--origin", remote, "--branch", default_branch, "--", url, str(clone)], run_dir, check=False).returncode:
        raise EnvironmentFailure("network clone of the delivery remote failed")
    clone = clone.resolve()
    start = git(["rev-parse", "HEAD"], clone).stdout.strip()
    record.update(clone_path=str(clone), clone_start_head=start)
    if start != record["remote_head"]:
        raise FallbackError("clone head differs from the discovered remote default head; rediscover before continuing")
    git(["check-ref-format", "--branch", record["task_branch"]], clone)
    git(["switch", "--create", record["task_branch"]], clone)


def prepare(args: argparse.Namespace) -> tuple:
    run_dir = args.run_dir.resolve(strict=True)
    record_path = run_dir / RECORD_NAME
    if record_path.exists():
        raise FileExistsError(f"workspace record already exists: {record_path}")
    root = resolve_worktree(args.project)
    record = {"mode": "BLOCKED", "project": str(args.project), "original_root": str(root) if root else None,
              "remote": args.remote, "expected_repository": args.expected_repository, "task_branch": args.task_branch,
              "reasons": []}
    exit_code = 0
    try:
        if root is None:
            facts = Facts(valid_worktree=False)
        else:
            if not _outside(run_dir, root):
                raise FallbackError("run directory must be outside the original checkout")
            record["original"] = snapshot(root)
            failures = tuple(probe_metadata(root, args.task_branch))
            record["metadata_failures"] = list(failures)
            facts = Facts(valid_worktree=True, metadata_failures=failures)
            url = None
            if failures:
                url, identity, remote_reasons = resolve_remote(root, args.remote, args.expected_repository)
                record["repository"] = identity
                default_branch = remote_head = None
                if url:
                    try:
                        default_branch, remote_head = discover_default(url, run_dir)
                    except (FallbackError, EnvironmentFailure) as exc:
                        remote_reasons.append(str(exc))
                record.update(default_branch=default_branch, remote_head=remote_head)
                facts = Facts(True, failures, tuple(remote_reasons), tuple(local_state(root)),
                              args.local_state_irrelevant, args.starts_from_default, default_branch, remote_head,
                              args.task_branch)
                record["local_state"] = list(facts.local_state)
        decision = decide(facts)
        record.update(mode=decision.mode, reasons=list(decision.reasons))
        if decision.mode == "FALLBACK":
            record["mode"] = "BLOCKED"  # until the clone is verified
            create_clone(record, run_dir, url)
            record["mode"] = "FALLBACK"
            record["workspace"] = record["clone_path"]
        elif decision.mode == "NORMAL":
            record["workspace"] = str(root)
    except (FallbackError, EnvironmentFailure) as exc:
        record.update(mode="BLOCKED")
        record["reasons"].append(str(exc))
        if isinstance(exc, ProbeResidueError):
            record["probe_residue"] = exc.path
    if record["mode"] == "BLOCKED":
        exit_code = EXIT_BLOCKED
    with record_path.open("x", encoding="utf-8") as handle:  # never overwrite an earlier record
        json.dump(record, handle, indent=2, sort_keys=True)
    return record, exit_code


def load_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_original(record: dict) -> list:
    before = record.get("original", {}).get("state")
    if not before or not record.get("original_root"):
        raise FallbackError("record has no original snapshot")
    after = snapshot(Path(record["original_root"]))["state"]
    return sorted(key for key in before if before[key] != after.get(key))


def check_target(record: dict, repo: Path) -> list:
    """Reasons why staging, committing, pushing or opening a PR must not happen in repo."""
    if record.get("mode") != "FALLBACK":
        return ["no isolated clone was selected for this run"]
    top = resolve_worktree(repo)
    reasons = []
    if top is None:
        return ["delivery target is not a Git worktree"]
    if str(top) == record.get("original_root"):
        reasons.append("delivery must never run in the original checkout")
    if str(top) != record.get("clone_path"):
        reasons.append("delivery target is not the recorded isolated clone")
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], top, check=False).stdout.strip()
    if branch == record.get("default_branch"):
        reasons.append("delivery must never target the default branch")
    if branch != record.get("task_branch"):
        reasons.append(f"current branch {branch or 'unknown'} is not the recorded task branch")
    return reasons


def push_task_branch(record: dict) -> dict:
    clone = Path(record.get("clone_path") or ".")
    reasons = check_target(record, clone)
    if reasons:
        raise FallbackError("; ".join(reasons))
    branch, remote = record["task_branch"], record["remote"]
    # No leading "+": a non-fast-forward update is rejected, never forced.
    result = git(["push", "--set-upstream", remote, f"refs/heads/{branch}:refs/heads/{branch}"], clone, check=False)
    if result.returncode != 0:
        if REJECTED_PUSH.search(result.stderr):
            raise FallbackError("remote rejected the task branch push; history conflict is not resolved automatically")
        raise EnvironmentFailure("task branch push failed (network or authentication)")
    head = git(["rev-parse", "HEAD"], clone).stdout.strip()
    published = git(["ls-remote", remote, f"refs/heads/{branch}"], clone, check=False)
    if published.returncode != 0:
        raise EnvironmentFailure("could not verify the published task branch")
    if published.stdout.split("\t", 1)[0].strip() != head:
        raise FallbackError("published task branch head does not match the clone head")
    return {"published_branch": branch, "published_head": head}


def pr_command(record: dict, title: str, body_file: Path) -> dict:
    clone = Path(record.get("clone_path") or ".")
    reasons = check_target(record, clone)
    if reasons:
        raise FallbackError("; ".join(reasons))
    argv = ["gh", "pr", "create", "--repo", record["repository"], "--base", record["default_branch"],
            "--head", record["task_branch"], "--title", title, "--body-file", str(body_file)]
    return {"cwd": str(clone), "argv": argv}


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="probe metadata; select NORMAL, FALLBACK (clone) or BLOCKED")
    prep.add_argument("--project", required=True, type=Path)
    prep.add_argument("--run-dir", required=True, type=Path)
    prep.add_argument("--remote", required=True, help="delivery remote name trusted from task context")
    prep.add_argument("--expected-repository", required=True, help="expected OWNER/REPO or HOST/OWNER/REPO")
    prep.add_argument("--task-branch", required=True)
    prep.add_argument("--starts-from-default", action="store_true",
                      help="Codex attests the task can begin entirely from the remote default branch")
    prep.add_argument("--local-state-irrelevant", action="store_true",
                      help="Codex attests existing local-only state is unrelated to the task")
    for name, help_text in (("verify-original", "confirm the original checkout is unchanged"),
                            ("check-target", "confirm a repository is the recorded clone on its task branch"),
                            ("push", "non-force push of the task branch from the clone"),
                            ("pr-command", "print the PR creation argv and working directory")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--record", required=True, type=Path)
        if name == "check-target":
            sub.add_argument("--repo", required=True, type=Path)
        if name == "pr-command":
            sub.add_argument("--title", required=True)
            sub.add_argument("--body-file", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "prepare":
            record, code = prepare(args)
            print(json.dumps({key: record.get(key) for key in (
                "mode", "reasons", "workspace", "original_root", "default_branch", "remote_head",
                "clone_start_head", "task_branch")}, indent=2))
            return code
        record = load_record(args.record)
        if args.command == "verify-original":
            changed = verify_original(record)
            print(json.dumps({"unchanged": not changed, "changed": changed}))
            return EXIT_BLOCKED if changed else 0
        if args.command == "check-target":
            reasons = check_target(record, args.repo)
            print(json.dumps({"allowed": not reasons, "reasons": reasons}))
            return EXIT_BLOCKED if reasons else 0
        if args.command == "push":
            print(json.dumps(push_task_branch(record)))
            return 0
        print(json.dumps(pr_command(record, args.title, args.body_file)))
        return 0
    except FallbackError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return EXIT_BLOCKED
    except EnvironmentFailure as exc:
        print(json.dumps({"status": "PARTIAL", "reason": str(exc)}))
        return EXIT_ENVIRONMENT


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
