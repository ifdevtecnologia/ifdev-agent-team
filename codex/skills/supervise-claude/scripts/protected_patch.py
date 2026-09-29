#!/usr/bin/env python3
"""Fail-closed protected-path patch handoff for a project's own ``.claude/**``.

Claude Code treats a repository's ``.claude/**`` as a protected path that a supervised,
non-interactive invocation cannot write directly. Instead, Claude authors every intended
protected change as one git-style unified diff at ``ARTIFACT_NAME`` (outside ``.claude/``);
the supervisor validates it here and applies it mechanically with ``git apply``. Claude
remains the semantic author: this module never edits, repairs or reinterprets an artifact.
It either applies it exactly as written or rejects it unchanged, with errors meant for the
next Claude correction cycle.

Validation is deliberately independent of Git: every hunk must match the current file at
its exact stated position (no offset or fuzz), so stale context is rejected before
``git apply --check`` runs. The expected post-state computed here is then compared with the
actual ``.claude/**`` tree after application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ARTIFACT_NAME = "claude-protected.patch"
PROTECTED_ROOT = ".claude"
MAX_ARTIFACT_BYTES = 1024 * 1024
REGULAR_MODE = "100644"
EXECUTABLE_MODE = "100755"
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
# Worktree only (no --index); explicit whitespace handling so no config can rewrite content.
GIT_APPLY = ("apply", "--whitespace=nowarn", "-p1")
# Windows drive letters (e.g. "C:\") are rejected alongside POSIX absolute paths so an
# authorization can never be satisfied by an absolute filesystem location.
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# Only "*", "**" and "?" are supported; other glob syntax is refused rather than guessed.
GLOB_METACHARACTERS = frozenset("*?")
UNSUPPORTED_GLOB = frozenset("[]{}")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")
# Whole directory- or file-name components (after stripping one leading dot, and for the
# final segment after stripping any extension) that mark credentials, secrets, auth or
# token material. This mechanism never authors or applies changes to such paths, even
# inside an otherwise-legitimate project .claude/** authorization.
SENSITIVE_NAMES = frozenset({
    "ssh", "credential", "credentials", "secret", "secrets", "auth", "token", "tokens",
    "password", "passwd", "passphrase", "netrc", "npmrc", "pgpass", "htpasswd",
    "authorized_keys", "known_hosts", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".ppk", ".gpg", ".asc", ".kdbx", ".ovpn", ".jks"})

DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
INDEX_HEADER = re.compile(r"^index [0-9a-f]{7,64}\.\.[0-9a-f]{7,64}(?: (\d{6}))?$")
EXTENDED_HEADERS = {
    "new file mode": re.compile(r"^new file mode (\d{6})$"),
    "deleted file mode": re.compile(r"^deleted file mode (\d{6})$"),
    "rename from": re.compile(r"^rename from (\S+)$"),
    "rename to": re.compile(r"^rename to (\S+)$"),
    "similarity index": re.compile(r"^similarity index (\d{1,3})%$"),
}
NO_NEWLINE = "\\ No newline at end of file"

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_CONFIGURATION = 2
EXIT_APPLICATION_FAILED = 3


class ConfigurationError(ValueError):
    """The supervisor's own authorization or invocation is unsafe or malformed."""


class PatchRejected(ValueError):
    """The artifact is unsafe, unauthorized, stale or malformed; it is left untouched."""


class ApplicationFailed(RuntimeError):
    """Mechanical application or its exact verification failed after validation: BLOCKED."""


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    old_lines: list = field(default_factory=list)
    new_lines: list = field(default_factory=list)


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list = field(default_factory=list)
    new_mode: str | None = None
    deleted_mode: str | None = None
    index_mode: str | None = None

    @property
    def kind(self) -> str:
        if self.old_path is None:
            return "add"
        if self.new_path is None:
            return "delete"
        return "rename" if self.old_path != self.new_path else "modify"

    def describe(self) -> dict:
        return {"kind": self.kind, "old_path": self.old_path, "new_path": self.new_path}


@dataclass
class Plan:
    changes: list
    # Protected-tree path -> expected content (None when removed) for every touched path.
    expected: dict
    before: dict
    artifact_sha256: str
    # The exact validated artifact text handed to git apply.
    text: str


# --- authorization ------------------------------------------------------------------


def _sensitive_reason(path: str) -> str | None:
    """The path component (if any) identifying credentials, secrets, auth or token material."""
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    for segment in segments[:-1]:
        stripped = segment[1:] if segment.startswith(".") else segment
        if stripped.lower() in SENSITIVE_NAMES:
            return segment
    basename = segments[-1]
    stripped = basename[1:] if basename.startswith(".") else basename
    stem = stripped.split(".", 1)[0].lower()
    if stripped.lower() in SENSITIVE_NAMES or stem in SENSITIVE_NAMES:
        return basename
    if Path(basename).suffix.lower() in SENSITIVE_EXTENSIONS:
        return basename
    return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def normalize_authorization(pattern: str, project: Path) -> str:
    """Validate a repository-root-relative path or glob under the project's ``.claude/``.

    It authorizes the supervisor to apply an authored patch to matching paths; it never
    grants Claude a direct write.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise ConfigurationError("--allow-claude-path must not be empty")
    if "\n" in pattern or "\0" in pattern:
        raise ConfigurationError("--allow-claude-path cannot contain control characters")
    normalized = pattern.strip().replace("\\", "/")
    if normalized.startswith("~"):
        raise ConfigurationError(f"--allow-claude-path cannot reference a home directory: {pattern}")
    if normalized.startswith("/") or WINDOWS_DRIVE.match(normalized):
        raise ConfigurationError(f"--allow-claude-path must be repository-root-relative, not absolute: {pattern}")
    segments = normalized.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ConfigurationError(f"--allow-claude-path cannot contain empty, current or parent-directory segments: {pattern}")
    if segments[0] != PROTECTED_ROOT or len(segments) < 2:
        raise ConfigurationError(f"--allow-claude-path must stay under the project's .claude directory: {pattern}")
    sensitive = _sensitive_reason(normalized)
    if sensitive is not None:
        raise ConfigurationError(
            f"--allow-claude-path cannot reference credentials, secrets, auth or token material ({sensitive}): {pattern}"
        )
    if any(char in UNSUPPORTED_GLOB for char in normalized):
        raise ConfigurationError(f"--allow-claude-path supports only '*', '**' and '?' globs: {pattern}")

    concrete_segments = []
    for segment in segments:
        if any(char in GLOB_METACHARACTERS for char in segment):
            break
        concrete_segments.append(segment)
    project_real = project.resolve(strict=True)
    probe = project.joinpath(*concrete_segments)
    # os.path.lexists (unlike Path.exists) reports a dangling symlink as present, so the
    # walk cannot skip past a broken symlink placed to escape the project directory.
    while not os.path.lexists(probe) and probe != probe.parent:
        probe = probe.parent
    if not _is_within(Path(os.path.realpath(probe)), project_real):
        raise ConfigurationError(f"--allow-claude-path escapes the project directory via a symlink: {pattern}")
    return normalized


def _glob_regex(pattern: str) -> re.Pattern:
    parts = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def is_authorized(path: str, patterns: tuple) -> bool:
    return any(_glob_regex(pattern).match(path) for pattern in patterns)


# --- prompt contract ----------------------------------------------------------------


def prompt_contract(patterns: tuple) -> str:
    """Deterministic instructions appended to the Claude prompt when handoff is in scope."""
    authorized = "\n".join(f"- {pattern}" for pattern in patterns)
    return f"""
<protected-path-contract>
Project-local `.claude/**` is a protected path in this supervised run. Do not attempt to
create, edit, delete or rename anything under `.claude/` with Edit, Write or Bash: direct
protected writes are unsupported and fail. Keep editing ordinary repository files directly.

The supervisor may apply your protected changes only to these repository-root-relative
paths (globs: `*` within one segment, `**` across segments):
{authorized}

Author every intended `.claude/**` change, and nothing else, as ONE git-style unified diff
written with the Write tool to `{ARTIFACT_NAME}` at the repository root (never under
`.claude/`). Never put ordinary files in it; edit those directly. The supervisor validates it
and applies it mechanically all-or-nothing; it never repairs it. A rejected artifact is left
unchanged and its errors come back to you in the next correction cycle.

- One `diff --git a/<path> b/<path>` section per file; repository-root-relative paths
  starting with `.claude/`, with `a/`/`b/` prefixes, no quotes, spaces, absolute or `..` paths.
- Edit: `--- a/<path>`, `+++ b/<path>`, then hunks whose `@@ -start,count +start,count @@`
  headers and context lines match the current file exactly at those line numbers.
- New file: `new file mode 100644`, `--- /dev/null`, `+++ b/<path>`, `@@ -0,0 +1,N @@`.
- Deletion: `deleted file mode 100644`, `--- a/<path>`, `+++ /dev/null`, one hunk removing
  every line.
- Rename: `rename from <old path>` and `rename to <new path>` right after the `diff --git
  a/<old path> b/<new path>` line; both paths must be authorized and the new one must not
  exist. For a rename with edits, add `--- a/<old path>`, `+++ b/<new path>` and hunks
  against the old content.
- Use `\\ No newline at end of file` after a line that lacks a final newline. No binary
  patches, mode changes, symlinks or copies. End the artifact with a newline.
- If no protected change is needed, do not create the artifact. Name it in your report.
</protected-path-contract>
"""


# --- parsing ------------------------------------------------------------------------


def _split_lines(text: str) -> list:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _content_lines(text: str) -> list:
    """File content split into lines that keep their terminating newline."""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _strip_prefix(value: str, prefix: str, section: int) -> str:
    if not value.startswith(prefix):
        raise PatchRejected(f"section {section}: malformed path header {value!r}")
    return value[len(prefix):]


def parse_patch(text: str) -> list:
    if not text.strip():
        raise PatchRejected("artifact is empty")
    if not text.endswith("\n"):
        raise PatchRejected("artifact must end with a newline")
    lines = _split_lines(text)
    if not lines[0].startswith("diff --git "):
        raise PatchRejected("artifact must start with a 'diff --git' header; no preamble is allowed")
    patches = []
    index = 0
    while index < len(lines):
        section = len(patches) + 1
        match = DIFF_HEADER.match(lines[index])
        if not match:
            raise PatchRejected(f"section {section}: expected 'diff --git a/<path> b/<path>', got {lines[index]!r}")
        a_path, b_path = match.groups()
        index += 1
        headers: dict = {}
        index_mode = None
        while index < len(lines) and not lines[index].startswith(("--- ", "diff --git ", "@@")):
            line = lines[index]
            if line.startswith("Binary files ") or line == "GIT binary patch":
                raise PatchRejected(f"section {section}: binary patches are not supported")
            index_match = INDEX_HEADER.match(line)
            if index_match:
                if index_mode is not None or "index" in headers:
                    raise PatchRejected(f"section {section}: duplicate index header")
                headers["index"] = True
                index_mode = index_match.group(1)
                index += 1
                continue
            for name, pattern in EXTENDED_HEADERS.items():
                header_match = pattern.match(line)
                if header_match:
                    if name in headers:
                        raise PatchRejected(f"section {section}: duplicate '{name}' header")
                    headers[name] = header_match.group(1)
                    break
            else:
                raise PatchRejected(f"section {section}: unsupported or unsafe header {line!r}")
            index += 1

        old_marker = new_marker = None
        if index < len(lines) and lines[index].startswith("--- "):
            old_marker = lines[index][4:]
            index += 1
            if index >= len(lines) or not lines[index].startswith("+++ "):
                raise PatchRejected(f"section {section}: '---' header without a following '+++' header")
            new_marker = lines[index][4:]
            index += 1

        hunks = []
        while index < len(lines) and lines[index].startswith("@@"):
            hunk, index = _parse_hunk(lines, index, section)
            hunks.append(hunk)
        if index < len(lines) and not lines[index].startswith("diff --git "):
            raise PatchRejected(f"section {section}: unexpected line {lines[index]!r}")
        if hunks and old_marker is None:
            raise PatchRejected(f"section {section}: hunks require '---'/'+++' headers")
        patches.append(_build_file_patch(section, a_path, b_path, headers, index_mode, old_marker, new_marker, hunks))
    return patches


def _parse_hunk(lines: list, index: int, section: int) -> tuple:
    match = HUNK_HEADER.match(lines[index])
    if not match:
        raise PatchRejected(f"section {section}: malformed hunk header {lines[index]!r}")
    old_start, old_count, new_start, new_count = (
        int(match.group(1)),
        int(match.group(2) if match.group(2) is not None else 1),
        int(match.group(3)),
        int(match.group(4) if match.group(4) is not None else 1),
    )
    if old_count == 0 and new_count == 0:
        raise PatchRejected(f"section {section}: empty hunk")
    hunk = Hunk(old_start, old_count, new_start, new_count)
    index += 1
    last_sides: tuple = ()
    while len(hunk.old_lines) < old_count or len(hunk.new_lines) < new_count:
        if index >= len(lines):
            raise PatchRejected(f"section {section}: hunk is truncated; its line counts do not match its header")
        line = lines[index]
        tag, body = line[:1], line[1:] + "\n"
        if tag == " ":
            hunk.old_lines.append(body)
            hunk.new_lines.append(body)
            last_sides = (hunk.old_lines, hunk.new_lines)
        elif tag == "-":
            hunk.old_lines.append(body)
            last_sides = (hunk.old_lines,)
        elif tag == "+":
            hunk.new_lines.append(body)
            last_sides = (hunk.new_lines,)
        else:
            raise PatchRejected(f"section {section}: hunk line counts do not match its header near {line!r}")
        index += 1
        if index < len(lines) and lines[index] == NO_NEWLINE:
            for side in last_sides:
                side[-1] = side[-1][:-1]
            index += 1
        if len(hunk.old_lines) > old_count or len(hunk.new_lines) > new_count:
            raise PatchRejected(f"section {section}: hunk has more lines than its header declares")
    for side in (hunk.old_lines, hunk.new_lines):
        if any(not line.endswith("\n") for line in side[:-1]):
            raise PatchRejected(f"section {section}: '{NO_NEWLINE}' may only follow the last line of a hunk side")
    return hunk, index


def _build_file_patch(section, a_path, b_path, headers, index_mode, old_marker, new_marker, hunks) -> FilePatch:
    new_mode = headers.get("new file mode")
    deleted_mode = headers.get("deleted file mode")
    rename_from = headers.get("rename from")
    rename_to = headers.get("rename to")
    if (rename_from is None) != (rename_to is None):
        raise PatchRejected(f"section {section}: a rename needs both 'rename from' and 'rename to'")
    if "similarity index" in headers and rename_from is None:
        raise PatchRejected(f"section {section}: 'similarity index' without a rename")
    if new_mode is not None and deleted_mode is not None:
        raise PatchRejected(f"section {section}: a file cannot be both added and deleted")

    if new_mode is not None:
        if new_mode != REGULAR_MODE:
            raise PatchRejected(f"section {section}: new files must use mode {REGULAR_MODE}, not {new_mode}")
        if rename_from is not None or a_path != b_path:
            raise PatchRejected(f"section {section}: malformed new-file header")
        if old_marker is not None and (old_marker != "/dev/null" or new_marker != f"b/{b_path}"):
            raise PatchRejected(f"section {section}: a new file needs '--- /dev/null' and '+++ b/{b_path}'")
        return FilePatch(None, b_path, hunks, new_mode=new_mode, index_mode=index_mode)
    if deleted_mode is not None:
        if rename_from is not None or a_path != b_path:
            raise PatchRejected(f"section {section}: malformed deletion header")
        if old_marker is not None and (old_marker != f"a/{a_path}" or new_marker != "/dev/null"):
            raise PatchRejected(f"section {section}: a deletion needs '--- a/{a_path}' and '+++ /dev/null'")
        return FilePatch(a_path, None, hunks, deleted_mode=deleted_mode, index_mode=index_mode)
    if rename_from is not None:
        if rename_from != a_path or rename_to != b_path or a_path == b_path:
            raise PatchRejected(f"section {section}: rename headers do not match the 'diff --git' paths")
    elif a_path != b_path:
        raise PatchRejected(f"section {section}: different paths without rename headers")
    elif not hunks:
        raise PatchRejected(f"section {section}: modification has no hunks")
    if old_marker is not None and (old_marker != f"a/{a_path}" or new_marker != f"b/{b_path}"):
        raise PatchRejected(f"section {section}: '---'/'+++' paths do not match the 'diff --git' paths")
    return FilePatch(a_path, b_path, hunks, index_mode=index_mode)


# --- validation ---------------------------------------------------------------------


def _check_lexical(path: str, patterns: tuple) -> None:
    segments = path.split("/")
    if path.startswith("/") or path.startswith("~") or WINDOWS_DRIVE.match(path) or "\\" in path:
        raise PatchRejected(f"{path}: absolute, home or non-POSIX paths are not allowed")
    if any(segment in ("", ".", "..") for segment in segments):
        raise PatchRejected(f"{path}: empty, current or parent-directory segments are not allowed")
    if not all(SAFE_SEGMENT.match(segment) for segment in segments):
        raise PatchRejected(f"{path}: unsupported characters in path")
    if segments[0] != PROTECTED_ROOT or len(segments) < 2:
        raise PatchRejected(f"{path}: not under .claude/; edit ordinary repository files directly, not through the artifact")
    sensitive = _sensitive_reason(path)
    if sensitive is not None:
        raise PatchRejected(
            f"{path}: refers to credentials, secrets, auth or token material ({sensitive}); never handled by this mechanism"
        )
    if not is_authorized(path, patterns):
        raise PatchRejected(f"{path}: not authorized for protected patch application")


def _check_filesystem(project: Path, path: str) -> None:
    """Every existing component must be a real directory (or the final regular file)."""
    current = project
    segments = path.split("/")
    for position, segment in enumerate(segments):
        current = current / segment
        if not os.path.lexists(current):
            break
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise PatchRejected(f"{path}: symlinks are not allowed in protected paths ({segment})")
        final = position == len(segments) - 1
        if final and not stat.S_ISREG(info.st_mode):
            raise PatchRejected(f"{path}: target is not a regular file")
        if not final and not stat.S_ISDIR(info.st_mode):
            raise PatchRejected(f"{path}: parent component {segment} is not a directory")
    if not _is_within(Path(os.path.realpath(project / path)), project / PROTECTED_ROOT):
        raise PatchRejected(f"{path}: resolves outside the project's .claude directory")


def check_not_global(project: Path) -> None:
    """Fail closed if a resolved project root is the home directory, or lies under its
    global Claude/Codex configuration or ``~/.ssh``. Shared by the applicator (plan_patch)
    and the launcher (run_claude.py), which both take an already-resolved project path."""
    home = Path(os.path.realpath(os.path.expanduser("~")))
    for global_dir in (home / ".claude", home / ".codex", home / ".ssh"):
        if project == home or _is_within(project, global_dir):
            raise PatchRejected(f"project .claude resolves to global configuration under {home}; refusing")


def _git_mode(path: Path) -> str:
    return EXECUTABLE_MODE if os.lstat(path).st_mode & 0o111 else REGULAR_MODE


def _read_text(path: Path, label: str) -> str:
    data = path.read_bytes()
    if b"\0" in data:
        raise PatchRejected(f"{label}: binary content is not supported")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchRejected(f"{label}: content is not UTF-8 text") from None


def apply_hunks(original: str, hunks: list, label: str) -> str:
    """Apply hunks strictly at their stated positions; any mismatch is stale context."""
    source = _content_lines(original)
    result: list = []
    cursor = 0
    for hunk in hunks:
        position = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        if position < cursor or position + hunk.old_count > len(source):
            raise PatchRejected(f"{label}: hunk @@ -{hunk.old_start},{hunk.old_count} @@ is out of order or out of range")
        expected_new = hunk.new_start if hunk.new_count == 0 else hunk.new_start - 1
        if expected_new != len(result) + (position - cursor):
            raise PatchRejected(f"{label}: hunk @@ +{hunk.new_start},{hunk.new_count} @@ has an inconsistent new start")
        if source[position:position + hunk.old_count] != hunk.old_lines:
            raise PatchRejected(f"{label}: stale or mismatched context at line {hunk.old_start}; regenerate against the current file")
        result.extend(source[cursor:position])
        result.extend(hunk.new_lines)
        cursor = position + hunk.old_count
    result.extend(source[cursor:])
    if any(not line.endswith("\n") for line in result[:-1]):
        raise PatchRejected(f"{label}: a line without a final newline is not at the end of the file")
    return "".join(result)


def _read_artifact(project: Path) -> tuple:
    artifact = project / ARTIFACT_NAME
    if not os.path.lexists(artifact):
        raise PatchRejected(f"no protected patch artifact at {ARTIFACT_NAME}")
    info = os.lstat(artifact)
    if not stat.S_ISREG(info.st_mode):
        raise PatchRejected(f"{ARTIFACT_NAME} must be a regular file, not a symlink or special file")
    if info.st_size > MAX_ARTIFACT_BYTES:
        raise PatchRejected(f"{ARTIFACT_NAME} exceeds {MAX_ARTIFACT_BYTES} bytes")
    data = artifact.read_bytes()
    return artifact, data, hashlib.sha256(data).hexdigest()


def _run_git(project: Path, *args: str, git: str = "git", stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [git, *args],
        cwd=project,
        env={**os.environ, **GIT_ENV},
        input=stdin if stdin is not None else "",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _require_git_root(project: Path, git: str) -> None:
    result = _run_git(project, "rev-parse", "--show-toplevel", git=git)
    if result.returncode != 0 or Path(os.path.realpath(result.stdout.strip())) != project:
        raise ConfigurationError(f"project must be the root of a Git working tree: {project}")


def snapshot(project: Path) -> dict:
    """Every non-directory entry under .claude/: (kind, content-or-target, executable, mode)."""
    root = project / PROTECTED_ROOT
    entries: dict = {}
    if not os.path.lexists(root):
        return entries
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*filenames, *(d for d in dirnames if os.path.islink(os.path.join(directory, d)))]:
            full = Path(directory, name)
            rel = full.relative_to(project).as_posix()
            info = os.lstat(full)
            if stat.S_ISLNK(info.st_mode):
                entries[rel] = ("symlink", os.readlink(full), False, stat.S_IMODE(info.st_mode))
            elif stat.S_ISREG(info.st_mode):
                entries[rel] = ("file", full.read_bytes(), bool(info.st_mode & 0o111), stat.S_IMODE(info.st_mode))
            else:
                entries[rel] = ("other", None, False, stat.S_IMODE(info.st_mode))
    return entries


def plan_patch(project: Path, patterns: tuple, *, git: str = "git") -> Plan:
    """Validate the artifact without mutating anything; raise PatchRejected on any doubt."""
    project = Path(os.path.realpath(project))
    if not patterns:
        raise ConfigurationError("at least one --allow-claude-path authorization is required")
    patterns = tuple(normalize_authorization(pattern, project) for pattern in patterns)
    _require_git_root(project, git)
    check_not_global(project)
    tracked = _run_git(project, "ls-files", "--", ARTIFACT_NAME, git=git)
    if tracked.returncode != 0 or tracked.stdout.strip():
        raise PatchRejected(f"{ARTIFACT_NAME} must be an untracked supervisor handoff artifact")
    _, data, digest = _read_artifact(project)
    if b"\0" in data:
        raise PatchRejected(f"{ARTIFACT_NAME}: binary content is not supported")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PatchRejected(f"{ARTIFACT_NAME}: not UTF-8 text") from None
    patches = parse_patch(text)

    touched: set = set()
    for patch in patches:
        for path in {patch.old_path, patch.new_path} - {None}:
            _check_lexical(path, patterns)
            if path in touched:
                raise PatchRejected(f"{path}: touched by more than one section; the patch is ambiguous")
            touched.add(path)
            _check_filesystem(project, path)

    before = snapshot(project)
    expected: dict = {}
    for patch in patches:
        label = patch.new_path or patch.old_path
        if patch.kind == "add":
            if os.path.lexists(project / patch.new_path):
                raise PatchRejected(f"{patch.new_path}: new file already exists; the patch is stale")
            if patch.index_mode not in (None, REGULAR_MODE):
                raise PatchRejected(f"{label}: unsupported mode {patch.index_mode}")
            expected[patch.new_path] = apply_hunks("", patch.hunks, label)
            continue
        source = project / patch.old_path
        if not os.path.lexists(source):
            raise PatchRejected(f"{patch.old_path}: source file does not exist; the patch is stale")
        mode = _git_mode(source)
        for declared in (patch.index_mode, patch.deleted_mode):
            if declared is not None and declared != mode:
                raise PatchRejected(f"{patch.old_path}: declared mode {declared} does not match current mode {mode}")
        content = apply_hunks(_read_text(source, patch.old_path), patch.hunks, label)
        if patch.kind == "delete":
            if content:
                raise PatchRejected(f"{patch.old_path}: deletion does not remove the whole current content; the patch is stale")
            expected[patch.old_path] = None
            continue
        if patch.kind == "rename":
            if os.path.lexists(project / patch.new_path):
                raise PatchRejected(f"{patch.new_path}: rename destination already exists")
            expected[patch.old_path] = None
        expected[patch.new_path] = content

    # The validated text is piped to Git, so it applies exactly what was checked here even if
    # the file on disk were replaced afterwards.
    check = _run_git(project, *GIT_APPLY, "--check", "-", git=git, stdin=text)
    if check.returncode != 0:
        raise PatchRejected("git apply --check refused the artifact: " + (check.stderr.strip() or "no details"))
    return Plan([patch.describe() for patch in patches], expected, before, digest, text)


def _expected_snapshot(plan: Plan) -> dict:
    result = dict(plan.before)
    for path, content in plan.expected.items():
        if content is None:
            result.pop(path, None)
    for path, content in plan.expected.items():
        if content is not None:
            result[path] = ("file", content.encode("utf-8"))
    return result


def verify(project: Path, plan: Plan, executable: dict) -> None:
    after = snapshot(project)
    expected = _expected_snapshot(plan)
    problems = []
    for path in sorted(set(after) | set(expected)):
        if path not in after:
            problems.append(f"{path}: missing after application")
        elif path not in expected:
            problems.append(f"{path}: unexpected after application")
        elif path in plan.expected:
            kind, content = after[path][:2]
            if (kind, content) != expected[path]:
                problems.append(f"{path}: content differs from the authored patch")
            elif after[path][2] != executable.get(path, False):
                problems.append(f"{path}: executable bit differs from the authored patch")
        elif after[path] != expected[path]:
            problems.append(f"{path}: untouched protected file changed")
    if problems:
        raise ApplicationFailed("protected tree does not exactly match the authored patch: " + "; ".join(problems))


def apply_patch(project: Path, patterns: tuple, archive: Path, *, git: str = "git") -> Plan:
    project = Path(os.path.realpath(project))
    archive = Path(os.path.abspath(archive))
    if _is_within(Path(os.path.realpath(archive.parent)), project) or os.path.lexists(archive):
        raise ConfigurationError("--archive must be a new path outside the project")
    if not archive.parent.is_dir():
        raise ConfigurationError("--archive parent directory must exist")
    plan = plan_patch(project, patterns, git=git)
    # The executable bit a touched path must have afterwards: kept from the source file.
    executable: dict = {}
    for change in plan.changes:
        if change["kind"] in ("modify", "rename"):
            executable[change["new_path"]] = plan.before[change["old_path"]][2]
    artifact = project / ARTIFACT_NAME
    result = _run_git(project, *GIT_APPLY, "-", git=git, stdin=plan.text)
    if result.returncode != 0:
        raise ApplicationFailed("git apply failed after a successful check: " + (result.stderr.strip() or "no details"))
    verify(project, plan, executable)
    _, data, digest = _read_artifact(project)
    if digest != plan.artifact_sha256:
        raise ApplicationFailed(f"{ARTIFACT_NAME} changed during application")
    # Move the applied artifact out of the tree so a later cycle never re-applies it.
    with open(archive, "xb") as handle:
        handle.write(data)
    artifact.unlink()
    return plan


# --- CLI ----------------------------------------------------------------------------


def parse_args(argv: list | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=("check", "apply"))
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument(
        "--allow-claude-path",
        action="append",
        default=[],
        help="Repository-root-relative path or glob under .claude/ the supervisor may patch; repeat as needed.",
    )
    parser.add_argument("--archive", type=Path, help="New path outside the project that receives the applied artifact.")
    parser.add_argument("--git", default="git", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list | None = None) -> int:
    args = parse_args(argv)
    report: dict = {"action": args.action, "artifact": ARTIFACT_NAME}
    code = EXIT_OK
    try:
        if args.action == "apply":
            if args.archive is None:
                raise ConfigurationError("apply requires --archive")
            plan = apply_patch(args.project, tuple(args.allow_claude_path), args.archive, git=args.git)
            report["status"] = "applied"
        else:
            plan = plan_patch(args.project, tuple(args.allow_claude_path), git=args.git)
            report["status"] = "valid"
        report["changes"] = plan.changes
        report["artifact_sha256"] = plan.artifact_sha256
    except PatchRejected as exc:
        report.update(status="rejected", error=str(exc))
        code = EXIT_REJECTED
    except (ConfigurationError, OSError) as exc:
        report.update(status="configuration-error", error=str(exc))
        code = EXIT_CONFIGURATION
    except ApplicationFailed as exc:
        report.update(status="failed", error=str(exc))
        code = EXIT_APPLICATION_FAILED
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
