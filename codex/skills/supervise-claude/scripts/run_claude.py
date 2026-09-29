#!/usr/bin/env python3
"""Launch a supervised Claude implementer with fail-closed permissions."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


BASE_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write")
REQUIRED_HELP_MARKERS = (
    "--print",
    "--permission-mode",
    '"dontAsk"',
    "--permission-prompts",
    '"none"',
    "--restricted",
    "--tools",
    "--allowedTools",
    "--disallowedTools",
    "--strict-mcp-config",
    "--model",
)
# Stable CLI aliases selected by supervision_policy.select_model; full model IDs are never assumed.
DEFAULT_MODEL = "sonnet"
SUPPORTED_MODELS = (DEFAULT_MODEL, "opus")
FORBIDDEN_ALLOW_PREFIXES = (
    "git add",
    "git commit",
    "git push",
    "git reset",
    "git clean",
    "git rebase",
    "git filter-branch",
    "git filter-repo",
    "gh ",
    "sudo ",
    "ssh ",
    "scp ",
    "chmod ",
    "chown ",
)
UNSAFE_INTERPRETER = re.compile(r"^(?:ba|z|fi)?sh\s+(?:-[^ ]*c\b|\*)|^(?:python\d*|node)\s+(?:-c|-e|\*)\b")
SHELL_CONTROL = re.compile(r"(?:&&|\|\||[;|`<>]|\$\()")
# Windows drive letters (e.g. "C:\") are rejected alongside POSIX absolute paths so a
# path-specific opt-in can never be satisfied by an absolute filesystem location.
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
GLOB_METACHARACTERS = frozenset("*?[]{}")


def deny_rule(command: str) -> tuple[str, str]:
    return (f"Bash({command})", f"Bash({command} *)")


DENIED_TOOLS = (
    *deny_rule("git add"),
    *deny_rule("git commit"),
    *deny_rule("git push"),
    *deny_rule("git reset"),
    *deny_rule("git clean"),
    *deny_rule("git rebase"),
    *deny_rule("git filter-branch"),
    *deny_rule("git filter-repo"),
    *deny_rule("gh pr create"),
    *deny_rule("gh pr merge"),
    *deny_rule("gh pr review"),
    *deny_rule("sudo"),
    *deny_rule("ssh"),
    *deny_rule("scp"),
    *deny_rule("chmod"),
    *deny_rule("chown"),
)


class ConfigurationError(ValueError):
    """The installed CLI or requested allowlist cannot be bounded safely."""


def require_capabilities(help_text: str) -> None:
    missing = [marker for marker in REQUIRED_HELP_MARKERS if marker not in help_text]
    if missing:
        raise ConfigurationError("installed Claude CLI does not advertise: " + ", ".join(missing))
    if "bypassPermissions" not in help_text:
        # Its presence in help confirms that restricted mode can explicitly refuse it.
        raise ConfigurationError("installed Claude CLI help does not describe bypassPermissions")


def require_model(help_text: str, model: str) -> str:
    # Refuse rather than substitute: an unsupported or unadvertised model never falls back to another.
    if model not in SUPPORTED_MODELS:
        raise ConfigurationError(f"unsupported model {model!r}; use one of: " + ", ".join(SUPPORTED_MODELS))
    if not re.search(rf"(?<![\w-]){re.escape(model)}(?![\w-])", help_text):
        raise ConfigurationError(f"installed Claude CLI does not advertise the model alias: {model}")
    return model


def normalize_allow_command(command: str) -> str:
    normalized = " ".join(command.split())
    if not normalized or normalized == "*" or normalized.startswith("*"):
        raise ConfigurationError("Bash allow rules must name a specific inspected command family")
    if normalized.startswith("Bash("):
        raise ConfigurationError("pass the inspected command pattern without a Bash(...) wrapper")
    if "\n" in command or "\0" in command or SHELL_CONTROL.search(normalized):
        raise ConfigurationError("Bash allow rules cannot contain shell control or redirection syntax")
    lowered = normalized.lower()
    if lowered == "bash" or lowered == "sh" or UNSAFE_INTERPRETER.search(lowered):
        raise ConfigurationError("unrestricted shell or interpreter access is forbidden")
    if lowered.startswith(FORBIDDEN_ALLOW_PREFIXES) or lowered in {prefix.rstrip() for prefix in FORBIDDEN_ALLOW_PREFIXES}:
        raise ConfigurationError(f"Claude delivery or privileged command cannot be allowed: {normalized}")
    if lowered.startswith("git ") and not lowered.startswith(("git status", "git diff", "git log")):
        raise ConfigurationError(f"only read-only Git command families may be allowed: {normalized}")
    return normalized


def normalize_claude_path(pattern: str, project: Path) -> str:
    """Validate a repository-root-relative opt-in path under the project's ``.claude/``.

    Restricted mode's bare Edit/Write tool grant does not satisfy Claude Code's separate
    protected-path permission decision for settings/git/tool-configuration paths; this
    produces the one narrow, explicit rule that satisfies it without widening access to
    anything else.
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
    if any(segment in ("", "..") for segment in segments):
        raise ConfigurationError(f"--allow-claude-path cannot contain empty or parent-directory segments: {pattern}")
    if segments[0] != ".claude":
        raise ConfigurationError(f"--allow-claude-path must stay under the project's .claude directory: {pattern}")

    concrete_segments = []
    for segment in segments:
        if any(char in GLOB_METACHARACTERS for char in segment):
            break
        concrete_segments.append(segment)
    project_real = project.resolve(strict=True)
    probe = project.joinpath(*concrete_segments) if concrete_segments else project
    # os.path.lexists (unlike Path.exists) reports a dangling symlink as present, so the
    # walk cannot skip past a broken symlink placed to escape the project directory.
    while not os.path.lexists(probe) and probe != probe.parent:
        probe = probe.parent
    resolved = Path(os.path.realpath(probe))
    try:
        resolved.relative_to(project_real)
    except ValueError:
        raise ConfigurationError(f"--allow-claude-path escapes the project directory via a symlink: {pattern}") from None
    return normalized


def build_command(
    claude: str,
    help_text: str,
    allow_commands: list[str],
    model: str = DEFAULT_MODEL,
    *,
    project: Path | None = None,
    allow_claude_paths: tuple[str, ...] = (),
) -> list[str]:
    require_capabilities(help_text)
    model = require_model(help_text, model)
    allowed_bash = [f"Bash({normalize_allow_command(command)})" for command in allow_commands]
    if allow_claude_paths and project is None:
        raise ConfigurationError("--allow-claude-path requires a resolved project directory")
    claude_paths = [normalize_claude_path(pattern, project) for pattern in allow_claude_paths]
    # Claude Code checks file paths against Edit(path) and Read(path) rules only; a
    # path-scoped Write(path) rule is accepted but never consulted, and warns at startup.
    # Edit(path) alone governs both Edit and Write for a protected path, so it is the
    # single rule this opt-in needs to grant per validated pattern.
    allowed_claude_paths = [f"Edit({path})" for path in claude_paths]
    tools = list(BASE_TOOLS)
    if allowed_bash:
        tools.append("Bash")
    # The model is the only per-cycle variation; permissions, tools and rules never depend on it.
    return [
        claude,
        "--print",
        "--output-format",
        "text",
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--restricted",
        "--strict-mcp-config",
        "--tools",
        ",".join(tools),
        "--allowedTools",
        *BASE_TOOLS,
        *allowed_bash,
        *allowed_claude_paths,
        "--disallowedTools",
        *DENIED_TOOLS,
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--claude", default="claude")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model alias chosen by supervision_policy.py select-model (default: %(default)s).",
    )
    parser.add_argument(
        "--allow-command",
        action="append",
        default=[],
        help="Inspected command pattern without the Bash(...) wrapper; repeat as needed.",
    )
    parser.add_argument(
        "--allow-claude-path",
        action="append",
        default=[],
        help=(
            "Repository-root-relative path or glob under the project's .claude/ that this "
            "task is scoped to edit (e.g. '.claude/agents/foo.md'); repeat as needed. Omit "
            "entirely unless project .claude/** is explicitly in task scope."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project = args.project.resolve(strict=True)
    if not project.is_dir() or args.timeout_seconds <= 0:
        raise ConfigurationError("project must be a directory and timeout must be positive")
    prompt = args.prompt.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ConfigurationError("prompt file is empty")
    help_result = subprocess.run(
        [args.claude, "--help"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if help_result.returncode != 0:
        raise ConfigurationError("could not inspect installed Claude CLI capabilities")
    command = build_command(
        args.claude,
        help_result.stdout,
        args.allow_command,
        args.model,
        project=project,
        allow_claude_paths=tuple(args.allow_claude_path),
    )
    args.log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Scoped to this one invocation's environment only: never exported to the Codex
    # session itself, so it cannot leak into an unrelated, unsupervised Claude run.
    supervised_env = dict(os.environ)
    supervised_env["IFDEV_SUPERVISED_CLAUDE"] = "1"
    with args.log.open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                command,
                cwd=project,
                input=prompt,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=args.timeout_seconds,
                check=False,
                env=supervised_env,
            )
        except subprocess.TimeoutExpired:
            log.write(f"\n[supervisor] Claude timed out after {args.timeout_seconds} seconds\n")
            return 124
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
