#!/usr/bin/env python3
"""Bounded static lint for the ifdev-agent-team repository.

Checks the required tree, entry-point executable bits, skill/agent YAML frontmatter (the neutral
schema in core/ and the generated Claude agents), the generated Codex agent TOML files, the pairing
of skills that opt out of implicit use, freshness of the generated dist/ files, the
compatibility links, local Markdown links, references to installed global files, machine-specific
paths, tool-specific paths and wording in core/, product/domain content, sensitive paths, credential patterns, unsafe prescriptions,
obsolete delivery instructions, commit convention consistency, managed, moved and retired items
and Bash syntax.

This is a heuristic gate, not a comprehensive semantic or security review. Sensitive
paths are classified by name before any file is opened and are never read. Every diagnostic
is redacted, so no output line echoes a recognized credential value.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

import build_targets

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: the Codex agents are then checked by their header only.
    tomllib = None

# Every team role: a neutral source in core/agents/ and its generated agents in dist/claude/agents/
# and dist/codex/agents/.
ROLES = (
    "po",
    "analyst",
    "architect",
    "ux",
    "designer",
    "reviewer",
    "ux-reviewer",
    "security-reviewer",
    "privacy-reviewer",
    "db-reviewer",
    "performance-reviewer",
    "api-reviewer",
    "debugger",
    "dev",
    "devops",
    "tech-writer",
    "metrics-analyst",
    "qa",
)
CORE_AGENTS_DIR = "core/agents"
CLAUDE_AGENTS_DIR = "dist/claude/agents"
CODEX_AGENTS_DIR = "dist/codex/agents"

REQUIRED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "requirements-dev.txt",
    "core/instructions.md",
    "core/rules/engineering-principles.md",
    "core/rules/testing.md",
    "core/rules/git-workflow.md",
    *(f"{CORE_AGENTS_DIR}/{role}.md" for role in ROLES),
    "core/skills/work-session/SKILL.md",
    "core/skills/work-session/agents/openai.yaml",
    "core/skills/work-session/references/commit-proposal.md",
    "core/skills/verify-changes/SKILL.md",
    "core/skills/verify-changes/references/commit-messages.md",
    "core/skills/flutter-dev/SKILL.md",
    "core/skills/web-dev/SKILL.md",
    "core/skills/supabase-dev/SKILL.md",
    "core/skills/safe-refactor/SKILL.md",
    "core/skills/java-spring-dev/SKILL.md",
    "core/skills/e2e-testing/SKILL.md",
    "targets/claude/adapter.md",
    "targets/claude/agent-footer.md",
    "targets/claude/hooks/agent-notify-hook.sh",
    "targets/codex/adapter.md",
    "targets/codex/agent-footer.md",
    "targets/codex/notify/agent-notify-codex.sh",
    "dist/claude/CLAUDE.md",
    *(f"{CLAUDE_AGENTS_DIR}/{role}.md" for role in ROLES),
    "dist/codex/AGENTS.md",
    *(f"{CODEX_AGENTS_DIR}/{role}.toml" for role in ROLES),
    "scripts/install.sh",
    "scripts/uninstall.sh",
    "scripts/validate.sh",
    "scripts/validate.py",
    "scripts/build_targets.py",
    "scripts/lib/common.sh",
    "scripts/lib/claude_hooks.py",
    "scripts/agent-notify.sh",
    "tests/test_install.py",
    "tests/test_validate.py",
    "tests/test_build_targets.py",
    "tests/test_agent_notify.py",
    "tests/test_agent_notify_hook.py",
    "tests/test_claude_hooks.py",
    ".github/workflows/validate.yml",
)

# Tracked links that keep the pre-0004 source paths resolving for one release (decision record
# 0004), as {link: exact relative target}. No other symlink is accepted inside the repository.
COMPAT_LINKS = {
    "claude/CLAUDE.md": "../dist/claude/CLAUDE.md",
    "claude/rules": "../core/rules",
    "claude/skills": "../core/skills",
    "claude/agents": "../dist/claude/agents",
    "claude/hooks": "../targets/claude/hooks",
    "codex/notify": "../targets/codex/notify",
}

# Documented as runnable with `./scripts/<name>.sh`, so they must keep the executable bit.
EXECUTABLE_SCRIPTS = ("scripts/install.sh", "scripts/uninstall.sh", "scripts/validate.sh", "scripts/agent-notify.sh")

MAX_SCAN_BYTES = 1_000_000

# Every path component is checked, so a match on a directory covers everything below it.
SENSITIVE_DIR_NAMES = {".aws", ".azure", ".gcloud", ".kube", ".docker", ".oci", ".ssh", ".gnupg"}
SENSITIVE_CONFIG_DIRS = {"gcloud", "gh"}  # below .config/
SENSITIVE_NAMES = {".netrc", "_netrc", ".git-credentials", ".pypirc", ".npmrc"}
# Matched after stripping leading dots, so `.credentials.json` counts as `credentials.json`.
SENSITIVE_PREFIXES = ("credential", "secret", "token", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk", ".jks", ".keystore")

CREDENTIAL_PATTERNS = {
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "API secret key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "private key block": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
}

HOME_PATH = re.compile(r"(?<![\w.~])/(?:home|Users)/[A-Za-z0-9._-]+|\b[A-Za-z]:\\+Users\\+[A-Za-z0-9._-]+")

# core/ is tool-neutral (decision record 0004): it names rules, skills and roles, and each target's
# adapter and agent footer say where they live. Tool homes, Claude-only placeholders and tool names,
# and the old toolkit source paths belong in targets/<tool>/ instead.
CORE_DIR = "core/"
CORE_TOOL_SPECIFIC = {
    "tool home path": re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.(?:claude|codex|cursor)(?![\w-])"),
    "$ARGUMENTS placeholder": re.compile(r"\$ARGUMENTS\b"),
    "tool name": re.compile(r"\b(?:Skill|Agent) tool\b"),
    "toolkit source path": re.compile(r"(?<![\w./~-])(?:claude|codex)/(?:CLAUDE\.md|rules|skills|agents|hooks|notify)\b"),
}

# Accidental product or business-domain content. The terms are assembled from fragments so
# that this file never contains them literally and needs no exemption from its own check.
_PRODUCT_FRAGMENTS = ("house" + "hold", "la" + "relo", "financ" + "(?:e|ial)", "expen" + "ses?", "domic" + "[ií]lio")
PRODUCT_TERMS = re.compile(r"\b(?:" + "|".join(_PRODUCT_FRAGMENTS) + r")s?\b", re.IGNORECASE)

UNSAFE_PRESCRIPTIONS = {
    "permission bypass": re.compile(
        r"--dangerously-skip-permissions|--dangerously-bypass-approvals-and-sandbox|\bbypassPermissions\b|--yolo\b"
    ),
    "full-access sandbox or approval bypass": re.compile(
        r"\bdanger-full-access\b|\bapproval_policy\s*=\s*\"?never\b|--ask-for-approval\s+never\b"
    ),
    "hook bypass": re.compile(r"--no-verify\b"),
    "force push": re.compile(r"\bgit\s+push\b[^\n.]*\s(?:--force(?:-with-lease)?|-f)\b|\bforce[- ]push"),
    "default branch push": re.compile(
        r"\bpush(?:es|ing)?\s+(?:directly\s+)?to\s+(?:`?main`?|`?master`?|the\s+(?:repository\s+)?default\s+branch)",
        re.IGNORECASE,
    ),
    "sudo": re.compile(r"\bsudo\b"),
    "world-writable permissions": re.compile(r"\bchmod\s+(?:-R\s+)?777\b"),
    "destructive reset/clean": re.compile(r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\b"),
    "recursive forced deletion": re.compile(r"\brm\s+-rf\b"),
    "merge protection bypass": re.compile(r"\bauto-merge\b|\bgh\s+pr\s+merge\b[^\n.]*--admin\b"),
}
# Hyphens are excluded from word boundaries so that flags such as `--no-verify` never
# count as their own prohibition.
NEGATION = re.compile(
    r"(?<![\w-])(?:never|not|no|don't|do not|must not|cannot|forbid(?:s|den)?|prohibit(?:s|ed)?|"
    r"refuse[sd]?|reject(?:s|ed)?|den(?:y|ies|ied)|avoid|nunca|não)(?![\w-])",
    re.IGNORECASE,
)

# Obsolete global delivery rules that contradict core/rules/git-workflow.md: approval for
# every single commit, or a push prohibition that also applies when delivery is authorized.
# Bounded sentence heuristics, not semantic analysis.
PER_COMMIT_APPROVAL = re.compile(
    r"\b(?:every|each|all)\s+commits?\b[^.;]*\b(?:approv|authori[sz])"
    r"|\b(?:approv|authori[sz])\w*\b[^.;]*\b(?:before|for|of|on)\s+(?:every|each|any)\s+commit"
    r"|\bcommit(?:s|ting)?\s+only\s+(?:with|after|upon)\b[^.;]*\b(?:approv|authori[sz])"
    r"|\bcommit\s+somente\s+com\s+aprova|\baprova\w*[^.;]*\bcada\s+commit",
    re.IGNORECASE,
)
PUSH_PROHIBITION = re.compile(
    r"(?<![\w-])(?:never|do\s+not|don't|must\s+not|may\s+not|cannot|nunca)\s+(?:[\w`'-]+\s+){0,2}"
    r"push(?:es|ed|ing)?(?![\w-])|(?<![\w-])no\s+push(?:es|ing)?(?![\w-])",
    re.IGNORECASE,
)
# Narrower restrictions stay allowed: conditional wording, explicit user restrictions and
# prohibitions limited to force pushes or protected/default branches.
_NARROWER_RESTRICTION = r"\bunless\b|\bnot\s+authori[sz]ed\b|\brestrict\w*|\bnarrower\b|\b(?:default|protected|main|master)\b"
PER_COMMIT_EXCEPTIONS = re.compile(
    _NARROWER_RESTRICTION + r"|\bwithout\s+(?:that|such|delivery)\s+authori[sz]ation", re.IGNORECASE
)
PUSH_EXCEPTIONS = re.compile(
    _NARROWER_RESTRICTION + r"|\bforce\b|\bwithout\s+(?:[\w-]+\s+){0,3}(?:authori[sz]ation|approval)", re.IGNORECASE
)
# Installed content: neutral sources, hand-written target files and generated output.
DELIVERY_RULE_SCOPE = ("core/", "targets/", "dist/")

BLOCK_START = re.compile(r"^\s*(?:$|#|[-*+]\s|\d+\.\s|\||```|~~~)")
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")

# Emoji for each commit type; three of them require the U+FE0F variation selector.
VARIATION_SELECTOR = chr(0xFE0F)
COMMIT_TYPES = {
    "feat": chr(0x2728),
    "fix": chr(0x1F41B),
    "docs": chr(0x1F4DD),
    "refactor": chr(0x267B) + VARIATION_SELECTOR,
    "test": chr(0x2705),
    "chore": chr(0x1F527),
    "perf": chr(0x26A1) + VARIATION_SELECTOR,
    "ci": chr(0x1F477),
    "build": chr(0x1F4E6) + VARIATION_SELECTOR,
}
_EMOJI_BASES = "|".join(sorted({re.escape(e.rstrip(VARIATION_SELECTOR)) for e in COMMIT_TYPES.values()}))
_TYPES = "|".join(COMMIT_TYPES)
_EMOJI = f"((?:{_EMOJI_BASES}){VARIATION_SELECTOR}?)"
COMMIT_SUBJECT = re.compile(rf"{_EMOJI}[ \t]+({_TYPES})(?=[(!:])")
COMMIT_TABLE_ROW = re.compile(rf"^\|\s*{_EMOJI}\s*\|\s*`({_TYPES})`\s*\|", re.MULTILINE)

MARKDOWN_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
GLOBAL_REFERENCE = re.compile(r"~/\.(claude|codex|agents)/([^\s`'\")\]>]+)")
MANAGED_REFERENCE_PREFIXES = {
    "claude": ("CLAUDE.md", "rules/", "agents/", "skills/"),
    "codex": ("AGENTS.md", "agents/", "skills/"),
    "agents": ("skills/",),
}

MANAGED_ITEMS_FILE = "scripts/lib/common.sh"
# Matches a multi-line "<NAME>=(\n...\n)" array or an empty one-line "<NAME>=()".
ITEMS_BLOCK = r"^{name}=\((?:\)|(.*?)^\))"
# Items by field count: MANAGED_ITEMS adds the owning targets to "<source>|<destination>".
ITEM_ENTRY = {
    2: re.compile(r'^\s*"([^"|]+)\|([^"|]+)"\s*$'),
    3: re.compile(r'^\s*"([^"|]+)\|([^"|]+)\|([^"|]+)"\s*$'),
}
AVAILABLE_TARGETS = re.compile(r'^AVAILABLE_TARGETS="([^"]*)"$', re.MULTILINE)
BASE_CODEX_CONFIG = ".codex/config.toml"

NAME_FORMAT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Read-only roles of the Analyst -> Dev -> Reviewer -> QA team, including the specialized reviewers
# (QA writes acceptance tests, so it is not one of them): their neutral source must set
# `read_only: true`, and every generated Claude agent of a read-only role must block Write and
# Edit and never list them as allowed tools.
READ_ONLY_ROLES = (
    "analyst",
    "architect",
    "reviewer",
    "ux-reviewer",
    "security-reviewer",
    "privacy-reviewer",
    "db-reviewer",
    "performance-reviewer",
    "api-reviewer",
    "debugger",
)
WRITE_TOOLS = ("Write", "Edit")
# Tool permissions are target-specific: a neutral agent declares `read_only` and each target maps it.
TOOL_SPECIFIC_AGENT_KEYS = ("tools", "disallowedTools", "readonly")
SKILL_PATH = re.compile(r"^core/skills/([^/]+)/(.+)$")
CORE_AGENT_PATH = re.compile(rf"^{CORE_AGENTS_DIR}/([^/]+)\.md$")
CLAUDE_AGENT_PATH = re.compile(rf"^{CLAUDE_AGENTS_DIR}/([^/]+)\.md$")
CODEX_AGENT_PATH = re.compile(rf"^{CODEX_AGENTS_DIR}/([^/]+)\.toml$")
CODEX_AGENT_KEYS = {"name", "description", "developer_instructions"}
# Codex skips a skill for implicit use when its agents/openai.yaml sets this to false; Claude Code
# reads `disable-model-invocation: true` from SKILL.md. A skill must declare both or neither.
CODEX_SKILL_POLICY = "agents/openai.yaml"


SAFE_YAML_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# PyYAML quotes the offending input (characters, tags, aliases) in its problem strings.
YAML_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
MAX_YAML_PROBLEM = 120


def redact(text: str) -> str:
    """Replaces recognized credential values so that no diagnostic can echo them."""
    for label, pattern in CREDENTIAL_PATTERNS.items():
        # The lambda runs inside pattern.sub in this same iteration, so it always sees the current label.
        text = pattern.sub(lambda _: f"[redacted {label}]", text)  # noqa: B023
    return text


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, path: str, message: str, line: int | None = None) -> None:
        # Paths, link targets, YAML keys and tool output can all carry input content.
        location = f"{path}:{line}" if line else path
        self.errors.append(redact(f"{location}: {message}"))


class DuplicateKeyError(yaml.constructor.ConstructorError):
    def __init__(self, key: object, mark: yaml.Mark) -> None:
        super().__init__(problem=f"found duplicate key '{key}'", problem_mark=mark)
        self.key = key


class UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader (no arbitrary object construction) that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    keys: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in keys:
                raise DuplicateKeyError(key, key_node.start_mark)
            keys.add(key)
        except TypeError:
            pass  # unhashable key: construct_mapping reports it
    return loader.construct_mapping(node, deep)


UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _is_sensitive_component(part: str) -> bool:
    bare = part.lstrip(".")
    return (
        part in SENSITIVE_DIR_NAMES
        or part in SENSITIVE_NAMES
        or part == ".env"
        or part.startswith(".env.")
        or bare == "auth"
        or bare.startswith("auth.")
        or bare.startswith(SENSITIVE_PREFIXES)
        or part.endswith(SENSITIVE_SUFFIXES)
    )


def is_sensitive_path(rel: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(rel).parts]
    if any(a == ".config" and b in SENSITIVE_CONFIG_DIRS for a, b in zip(parts, parts[1:])):
        return True
    return any(_is_sensitive_component(part) for part in parts)


def has_unsafe_parent(root: Path, rel: str) -> bool:
    """True when a parent directory of rel is a symlink or not a directory.

    lstat on the leaf alone would still follow a symlinked parent out of the repository.
    """
    current = root
    for part in PurePosixPath(rel).parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return False  # a deleted parent leaves nothing to read
        if not stat.S_ISDIR(info.st_mode):
            return True
    return False


def git_paths(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        check=True,
        capture_output=True,
    )
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def read_regular_file(path: Path) -> bytes | None:
    """Reads a regular file without following symlinks or blocking on special files."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SCAN_BYTES:
            return None
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def inventory(root: Path, report: Report) -> tuple[dict[str, str], list[str]]:
    """Returns safe text files by relative path plus every inventoried path.

    Sensitive paths are classified by name only: never opened, hashed or read from the index.
    """
    tracked = set(git_paths(root, "--cached"))
    candidates = sorted(tracked | set(git_paths(root, "--others", "--exclude-standard")))
    real_root = os.path.realpath(root)
    texts: dict[str, str] = {}
    for rel in candidates:
        if is_sensitive_path(rel):
            if rel in tracked:
                report.add(rel, "sensitive path is tracked in the git index (content not inspected)")
            continue
        if has_unsafe_parent(root, rel):
            report.add(rel, "parent path is a symlink or not a directory (not read)")
            continue
        path = root / rel
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            resolved = os.path.realpath(path)
            if os.path.commonpath([real_root, resolved]) != real_root:
                report.add(rel, "symlink points outside the repository")
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        try:
            data = read_regular_file(path)
        except OSError as exc:
            report.add(rel, f"cannot read file safely ({exc.strerror})")
            continue
        if data is None or b"\0" in data:
            continue
        try:
            texts[rel] = data.decode("utf-8")
        except UnicodeDecodeError:
            report.add(rel, "file is not valid UTF-8 text")
    return texts, candidates


def check_required(root: Path, texts: dict[str, str], report: Report) -> None:
    for rel in REQUIRED_FILES:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            report.add(rel, "required file is missing or is not a regular file")
        elif rel not in texts:
            report.add(rel, "required file is ignored, sensitive or unreadable")


def yaml_problem(exc: yaml.YAMLError) -> str:
    """Describes a YAML error without the quoted input or snippet that PyYAML includes."""
    problem = str(getattr(exc, "problem", None) or "parse error").split("\n", 1)[0]
    return YAML_QUOTED.sub("'...'", problem)[:MAX_YAML_PROBLEM]


def parse_frontmatter(rel: str, text: str, report: Report) -> dict | None:
    """Parses the YAML mapping between the leading `---` fences with a duplicate-rejecting SafeLoader."""
    lines = text.split("\n")
    if lines[0].strip() != "---":
        report.add(rel, "missing frontmatter")
        return None
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        report.add(rel, "unterminated frontmatter")
        return None
    try:
        fields = yaml.load("\n".join(lines[1:end]), Loader=UniqueKeySafeLoader)
    except DuplicateKeyError as exc:
        # +2: marks are zero-based and the YAML starts after the opening fence.
        key = f" '{exc.key}'" if isinstance(exc.key, str) and SAFE_YAML_KEY.match(exc.key) else ""
        report.add(rel, f"duplicate frontmatter key{key}", exc.problem_mark.line + 2)
        return None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        report.add(rel, f"invalid YAML frontmatter ({yaml_problem(exc)})", mark.line + 2 if mark else None)
        return None
    if not isinstance(fields, dict):
        report.add(rel, "frontmatter must be a YAML mapping")
        return None
    return fields


def check_definition(
    rel: str, expected_name: str, texts: dict[str, str], seen: dict, kind: str, report: Report
) -> dict | None:
    fields = parse_frontmatter(rel, texts[rel], report)
    if fields is None:
        return None
    description = fields.get("description")
    if not isinstance(description, str) or not description.strip():
        report.add(rel, "frontmatter 'description' must be a non-empty string")
    name = fields.get("name")
    if not isinstance(name, str) or not name.strip():
        report.add(rel, "frontmatter 'name' must be a non-empty string")
        return fields
    if not NAME_FORMAT.match(name):
        report.add(rel, "frontmatter requires a lowercase kebab-case name")
    elif name != expected_name:
        report.add(rel, f"name '{name}' must match '{expected_name}'")
    if name in seen.setdefault(kind, {}):
        report.add(rel, f"duplicate {kind} name '{name}' (also in {seen[kind][name]})")
    seen[kind][name] = rel
    return fields


def check_neutral_agent(rel: str, fields: dict, report: Report) -> None:
    """A core agent declares `read_only` and leaves tool permissions to each target."""
    if not isinstance(fields.get("read_only"), bool):
        report.add(rel, "frontmatter 'read_only' must be true or false")
    for key in TOOL_SPECIFIC_AGENT_KEYS:
        if key in fields:
            report.add(rel, f"tool-specific key '{key}' does not belong in a neutral agent (use read_only)")


def check_definitions(texts: dict[str, str], candidates: list[str], report: Report) -> None:
    """Evaluates definitions from the full path inventory, so unreadable or unsafe ones still fail."""
    seen: dict = {}
    skill_dirs: dict[str, bool] = {}
    for rel in candidates:
        match = SKILL_PATH.match(rel)
        if match:
            name = match.group(1)
            skill_dirs[name] = skill_dirs.get(name, False) or match.group(2) == "SKILL.md"
    for name, has_skill in sorted(skill_dirs.items()):
        rel = f"core/skills/{name}/SKILL.md"
        if not has_skill or rel not in texts:
            report.add(f"core/skills/{name}", "skill directory has no readable SKILL.md")
            continue
        check_definition(rel, name, texts, seen, "skill", report)
    for pattern, kind in ((CORE_AGENT_PATH, "core agent"), (CLAUDE_AGENT_PATH, "claude agent")):
        for rel in candidates:
            match = pattern.match(rel)
            if not match:
                continue
            if rel not in texts:
                report.add(rel, "agent definition is missing, unreadable or unsafe")
                continue
            fields = check_definition(rel, match.group(1), texts, seen, kind, report)
            if fields is not None and kind == "core agent":
                check_neutral_agent(rel, fields, report)


def tool_names(value: object) -> set[str]:
    """Tool lists may be a comma-separated string or a YAML sequence."""
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        return {str(part).strip() for part in value}
    return set()


def quiet_frontmatter(rel: str, texts: dict[str, str]) -> dict | None:
    """Frontmatter whose problems check_definitions already reports."""
    return parse_frontmatter(rel, texts[rel], Report()) if rel in texts else None


def check_read_only_agents(texts: dict[str, str], report: Report) -> None:
    for role in READ_ONLY_ROLES:
        rel = f"{CORE_AGENTS_DIR}/{role}.md"
        fields = quiet_frontmatter(rel, texts)
        if fields is not None and fields.get("read_only") is not True:
            report.add(rel, "read-only role must set 'read_only: true'")
    for rel in sorted(texts):
        match = CLAUDE_AGENT_PATH.match(rel)
        if not match:
            continue
        source = quiet_frontmatter(f"{CORE_AGENTS_DIR}/{match.group(1)}.md", texts) or {}
        if match.group(1) not in READ_ONLY_ROLES and source.get("read_only") is not True:
            continue
        fields = quiet_frontmatter(rel, texts)
        if fields is None:
            continue
        allowed = tool_names(fields.get("tools"))
        blocked = tool_names(fields.get("disallowedTools"))
        for tool in WRITE_TOOLS:
            if tool in allowed:
                report.add(rel, f"read-only agent must not allow '{tool}'")
            if tool not in blocked:
                report.add(rel, f"read-only agent must list '{tool}' in disallowedTools")


def check_codex_agents(texts: dict[str, str], report: Report) -> None:
    """A generated Codex agent starts with the generated-file comment and, when `tomllib` is
    available, holds exactly the string keys Codex reads, its role's name and, for a read-only
    role, the read-only notice (Codex does not enforce read-only custom agents)."""
    for rel in sorted(texts):
        match = CODEX_AGENT_PATH.match(rel)
        if not match:
            continue
        role = match.group(1)
        source = f"{CORE_AGENTS_DIR}/{role}.md"
        if not texts[rel].startswith(build_targets.CODEX_AGENT_HEADER.format(source=source)):
            report.add(rel, "Codex agent must start with the generated-file comment")
        if tomllib is None:
            continue
        try:
            fields = tomllib.loads(texts[rel])
        except tomllib.TOMLDecodeError:
            report.add(rel, "Codex agent is not valid TOML")
            continue
        if set(fields) != CODEX_AGENT_KEYS:
            report.add(rel, f"Codex agent keys must be exactly {', '.join(sorted(CODEX_AGENT_KEYS))}")
        for key in sorted(CODEX_AGENT_KEYS & set(fields)):
            if not isinstance(fields[key], str) or not fields[key].strip():
                report.add(rel, f"Codex agent '{key}' must be a non-empty string")
        if fields.get("name") != role:
            report.add(rel, f"Codex agent name must be '{role}'")
        neutral = quiet_frontmatter(source, texts) or {}
        description = neutral.get("description")
        if isinstance(description, str) and fields.get("description") != description.strip():
            report.add(rel, f"Codex agent description must match {source}")
        read_only = role in READ_ONLY_ROLES or neutral.get("read_only") is True
        instructions = fields.get("developer_instructions")
        if read_only and build_targets.CODEX_READ_ONLY_NOTICE not in (instructions if isinstance(instructions, str) else ""):
            report.add(rel, "read-only Codex agent must carry the read-only notice in developer_instructions")


def check_skill_invocation_policy(texts: dict[str, str], candidates: list[str], report: Report) -> None:
    """Claude Code and Codex each have their own opt-out from implicit skill use; a skill that sets
    one must set the other, so it never runs implicitly in one tool only."""
    skills = sorted({match.group(1) for rel in candidates if (match := SKILL_PATH.match(rel))})
    for name in skills:
        fields = quiet_frontmatter(f"core/skills/{name}/SKILL.md", texts) or {}
        manual = fields.get("disable-model-invocation") is True
        rel = f"core/skills/{name}/{CODEX_SKILL_POLICY}"
        if rel not in texts:
            if manual:
                report.add(rel, "skill sets 'disable-model-invocation: true' but has no Codex policy file")
            continue
        try:
            data = yaml.load(texts[rel], Loader=UniqueKeySafeLoader)
        except yaml.YAMLError as exc:
            report.add(rel, f"invalid YAML ({yaml_problem(exc)})")
            continue
        policy = data.get("policy") if isinstance(data, dict) else None
        implicit = policy.get("allow_implicit_invocation") if isinstance(policy, dict) else None
        if not isinstance(implicit, bool):
            report.add(rel, "'policy.allow_implicit_invocation' must be true or false")
        elif implicit == manual:
            report.add(
                rel,
                "'policy.allow_implicit_invocation: false' must pair with 'disable-model-invocation: true' in SKILL.md",
            )


def markdown_prose_blocks(text: str):
    """Yields (line, text) paragraphs; list items, headings, table rows and fences start new ones."""
    start, buffer = 0, []
    for number, line in enumerate(text.split("\n"), start=1):
        if BLOCK_START.match(line) and buffer:
            yield start, " ".join(buffer)
            buffer = []
        if line.strip():
            if not buffer:
                start = number
            buffer.append(line.strip())
    if buffer:
        yield start, " ".join(buffer)


def prose_sentences(text: str):
    """Yields (line of the enclosing block, sentence) for Markdown prose."""
    for line, block in markdown_prose_blocks(text):
        normalized = re.sub(r"\b(e\.g|i\.e|etc)\.", r"\1", block)
        for sentence in SENTENCE_END.split(normalized):
            yield line, sentence


def check_unsafe_prescriptions(rel: str, text: str, report: Report) -> None:
    for line, sentence in prose_sentences(text):
        if NEGATION.search(sentence):
            continue
        for label, pattern in UNSAFE_PRESCRIPTIONS.items():
            if pattern.search(sentence):
                report.add(rel, f"unsafe prescription without a prohibition ({label})", line)


def check_obsolete_delivery_rules(rel: str, text: str, report: Report) -> None:
    for line, sentence in prose_sentences(text):
        if PER_COMMIT_APPROVAL.search(sentence) and not PER_COMMIT_EXCEPTIONS.search(sentence):
            report.add(rel, "obsolete delivery instruction (per-commit approval); see core/rules/git-workflow.md", line)
        if PUSH_PROHIBITION.search(sentence) and not PUSH_EXCEPTIONS.search(sentence):
            report.add(rel, "obsolete delivery instruction (unconditional push prohibition); see core/rules/git-workflow.md", line)


def check_commit_convention(rel: str, text: str, report: Report) -> None:
    for match in COMMIT_SUBJECT.finditer(text):
        emoji, commit_type = match.groups()
        if COMMIT_TYPES[commit_type] != emoji:
            report.add(rel, f"commit emoji does not match type '{commit_type}'", line_of(text, match.start()))


def check_commit_table(texts: dict[str, str], report: Report) -> None:
    rel = "core/rules/git-workflow.md"
    if rel not in texts:
        return
    rows = {t: e for e, t in COMMIT_TABLE_ROW.findall(texts[rel])}
    if rows != COMMIT_TYPES:
        report.add(rel, "commit type table must list exactly the nine emoji/type pairs")


def has_managed_source(root: Path, destination: str, managed: list[tuple[str, ...]]) -> bool:
    """True when the installed path <destination> (relative to HOME) is, or lies inside, a managed
    item whose source exists, or is a directory that holds managed items."""
    for source, item_destination, *_ in managed:
        if destination == item_destination:
            return (root / source).exists()
        if destination.startswith(item_destination + "/"):
            return (root / source / destination[len(item_destination) + 1 :]).exists()
    return any(item_destination.startswith(destination + "/") for _, item_destination, *_ in managed)


def check_links(root: Path, rel: str, text: str, managed: list[tuple[str, ...]], report: Report) -> None:
    real_root = os.path.realpath(root)
    in_fence = False
    for number, line in enumerate(text.split("\n"), start=1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in MARKDOWN_LINK.finditer(line):
            target = match.group(1).split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                continue
            resolved = os.path.realpath(root / PurePosixPath(rel).parent / target)
            if os.path.commonpath([real_root, resolved]) != real_root or not os.path.exists(resolved):
                report.add(rel, f"broken local link '{match.group(1)}'", number)
        for match in GLOBAL_REFERENCE.finditer(line):
            tool, reference = match.group(1), match.group(2).rstrip(".,;:")
            if "..." in reference or any(c in reference for c in "<*{$"):
                continue
            if not reference.startswith(MANAGED_REFERENCE_PREFIXES[tool]):
                continue
            if not has_managed_source(root, f".{tool}/{reference.rstrip('/')}", managed):
                report.add(rel, f"reference ~/.{tool}/{reference} has no managed source", number)


def check_tool_neutral(rel: str, text: str, report: Report) -> None:
    for label, pattern in CORE_TOOL_SPECIFIC.items():
        for match in pattern.finditer(text):
            report.add(
                rel,
                f"tool-specific {label} '{match.group(0)}' in core/ (refer by name; tool details go in targets/)",
                line_of(text, match.start()),
            )


def check_text(root: Path, rel: str, text: str, managed: list[tuple[str, ...]], report: Report) -> None:
    for label, pattern in CREDENTIAL_PATTERNS.items():
        for match in pattern.finditer(text):
            report.add(rel, f"possible credential ({label}); value not shown", line_of(text, match.start()))
    for match in HOME_PATH.finditer(text):
        report.add(rel, "hardcoded machine-specific home path", line_of(text, match.start()))
    for match in PRODUCT_TERMS.finditer(text):
        report.add(rel, f"product/domain-specific term '{match.group(0)}'", line_of(text, match.start()))
    if rel.startswith(CORE_DIR):
        check_tool_neutral(rel, text, report)
    if rel.endswith(".md"):
        check_unsafe_prescriptions(rel, text, report)
        if rel.startswith(DELIVERY_RULE_SCOPE):
            check_obsolete_delivery_rules(rel, text, report)
        check_commit_convention(rel, text, report)
        check_links(root, rel, text, managed, report)


def parse_item_array(texts: dict[str, str], name: str, report: Report, fields: int = 2) -> list[tuple[str, ...]]:
    """Parses a "<source>|<destination>" Bash array such as RETIRED_ITEMS or MOVED_ITEMS, or,
    with fields=3, MANAGED_ITEMS with its owning targets."""
    text = texts.get(MANAGED_ITEMS_FILE)
    pattern = re.compile(ITEMS_BLOCK.format(name=name), re.MULTILINE | re.DOTALL)
    match = pattern.search(text) if text is not None else None
    if match is None:
        report.add(MANAGED_ITEMS_FILE, f"{name} array not found")
        return []
    if match.group(1) is None:
        return []
    items = []
    for offset, line in enumerate(match.group(1).split("\n")):
        if not line.strip() or line.strip().startswith("#"):
            continue
        entry = ITEM_ENTRY[fields].match(line)
        if entry is None:
            report.add(MANAGED_ITEMS_FILE, f"malformed {name} entry", line_of(text, match.start(1)) + offset)
            continue
        items.append(entry.groups())
    return items


def has_inventoried_content(source: str, candidates: list[str]) -> bool:
    """True when the inventory holds the file itself or anything below the directory."""
    prefix = source.rstrip("/") + "/"
    return any(rel == source or rel.startswith(prefix) for rel in candidates)


def check_retired_items(
    root: Path, candidates: list[str], texts: dict[str, str], managed_destinations: set[str], report: Report
) -> None:
    """A retired source must be gone (otherwise it is still managed) and its destination must
    not collide with a managed one (install would link it and uninstall would fight over it).

    "Gone" means absent from the inventory: Git-ignored leftovers such as `__pycache__/` stay
    on disk after the tracked files are removed and are not toolkit content. Below a
    compatibility link the inventory lists the link's target instead, so the path itself counts."""
    for source, destination in parse_item_array(texts, "RETIRED_ITEMS", report):
        through_link = reached_through_compat_link(source) and os.path.lexists(root / source)
        if through_link or has_inventoried_content(source, candidates):
            report.add(MANAGED_ITEMS_FILE, f"retired source '{source}' still exists in the checkout")
        if destination in managed_destinations:
            report.add(MANAGED_ITEMS_FILE, f"retired destination '{destination}' collides with a managed destination")


def available_targets(texts: dict[str, str], report: Report) -> set[str]:
    match = AVAILABLE_TARGETS.search(texts.get(MANAGED_ITEMS_FILE) or "")
    if match is None:
        report.add(MANAGED_ITEMS_FILE, "AVAILABLE_TARGETS not found")
        return set()
    return set(match.group(1).split())


def reached_through_compat_link(rel: str) -> bool:
    return any(rel == link or rel.startswith(link + "/") for link in COMPAT_LINKS)


def check_moved_items(root: Path, texts: dict[str, str], managed: dict[str, str], report: Report) -> None:
    """A moved item's destination must still be managed, from a different source, and its old
    source may survive only through a compatibility link (otherwise it is still a real source)
    that resolves to the new source, so a machine that only pulls keeps loading the same file."""
    for source, destination in parse_item_array(texts, "MOVED_ITEMS", report):
        if destination not in managed:
            report.add(MANAGED_ITEMS_FILE, f"moved destination '{destination}' is not a managed destination")
        elif managed[destination] == source:
            report.add(MANAGED_ITEMS_FILE, f"moved source '{source}' is still the managed source of '{destination}'")
        if not os.path.lexists(root / source):
            continue
        if not reached_through_compat_link(source):
            report.add(MANAGED_ITEMS_FILE, f"moved source '{source}' still exists outside the compatibility links")
        elif destination in managed and os.path.realpath(root / source) != os.path.realpath(root / managed[destination]):
            report.add(MANAGED_ITEMS_FILE, f"moved source '{source}' does not resolve to the managed source of '{destination}'")


def check_compat_links(root: Path, candidates: list[str], report: Report) -> None:
    for rel, target in sorted(COMPAT_LINKS.items()):
        path = root / rel
        if not path.is_symlink() or os.readlink(path) != target:
            report.add(rel, f"compatibility link must be a symlink to '{target}'")
        elif not path.exists():
            report.add(rel, "compatibility link is dangling")
    for rel in candidates:
        if rel not in COMPAT_LINKS and not has_unsafe_parent(root, rel) and (root / rel).is_symlink():
            report.add(rel, "symlink is not a listed compatibility link")


def check_managed_items(root: Path, texts: dict[str, str], candidates: list[str], items: list, report: Report) -> None:
    targets = available_targets(texts, report)
    destinations: set[str] = set()
    for source, destination, owners in items:
        unknown = sorted(set(owners.split(",")) - targets)
        if unknown:
            report.add(MANAGED_ITEMS_FILE, f"managed item '{destination}' has unsupported targets: {', '.join(unknown)}")
        path = root / source
        if path.is_symlink() or not (path.is_file() or path.is_dir()) or has_unsafe_parent(root, source):
            report.add(MANAGED_ITEMS_FILE, f"managed source '{source}' is missing or unsafe")
        elif path.is_dir() and not has_inventoried_content(source, candidates):
            report.add(MANAGED_ITEMS_FILE, f"managed source directory '{source}' has no tracked content")
        if destination in destinations:
            report.add(MANAGED_ITEMS_FILE, f"duplicate managed destination '{destination}'")
        destinations.add(destination)
        if destination == BASE_CODEX_CONFIG:
            report.add(MANAGED_ITEMS_FILE, "managed items must never replace the base Codex config")
    check_retired_items(root, candidates, texts, destinations, report)
    check_moved_items(root, texts, {destination: source for source, destination, _ in items}, report)
    check_managed_agents(set(items), report)


def check_managed_agents(managed: set[tuple[str, str, str]], report: Report) -> None:
    """A required or read-only role whose generated agent is outside MANAGED_ITEMS, for its target,
    would never be installed."""
    for role in sorted({*ROLES, *READ_ONLY_ROLES}):
        for rel, expected, target in (
            (f"{CLAUDE_AGENTS_DIR}/{role}.md", f".claude/agents/{role}.md", "claude"),
            (f"{CODEX_AGENTS_DIR}/{role}.toml", f".codex/agents/{role}.toml", "codex"),
        ):
            if (rel, expected, target) not in managed:
                report.add(MANAGED_ITEMS_FILE, f"agent '{rel}' must be managed as '{expected}' for the {target} target")


def check_generated(root: Path, report: Report) -> None:
    """dist/ must match what scripts/build_targets.py generates now."""
    try:
        found = build_targets.problems(root)
    except build_targets.BuildError as exc:
        report.add("dist", f"cannot generate the target files: {exc}")
        return
    except (OSError, ValueError) as exc:
        report.add("dist", f"cannot generate the target files ({type(exc).__name__})")
        return
    for rel, message in found:
        report.add(rel, f"{message}; run python3 scripts/build_targets.py")


def check_bash_syntax(root: Path, texts: dict[str, str], report: Report) -> None:
    for rel in sorted(texts):
        if not rel.endswith(".sh"):
            continue
        result = subprocess.run(["bash", "-n", str(root / rel)], capture_output=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            report.add(rel, f"bash syntax error: {detail[0] if detail else 'unknown'}")


def check_executable_scripts(root: Path, report: Report) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "-z", "--", *EXECUTABLE_SCRIPTS],
        check=True,
        capture_output=True,
    )
    # Entries are "<mode> <object> <stage>\t<path>".
    index_modes = {}
    for entry in result.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if "\t" in entry:
            meta, rel = entry.split("\t", 1)
            index_modes[rel] = meta.split(" ", 1)[0]
    for rel in EXECUTABLE_SCRIPTS:
        try:
            info = os.lstat(root / rel)
        except FileNotFoundError:
            continue  # reported by check_required
        if stat.S_ISREG(info.st_mode) and not info.st_mode & stat.S_IXUSR:
            report.add(rel, "entry-point script must be executable (chmod +x)")
        if index_modes.get(rel, "100755") != "100755":
            report.add(rel, "entry-point script is tracked without the executable bit")


def validate(root: Path) -> Report:
    report = Report()
    texts, candidates = inventory(root, report)
    check_required(root, texts, report)
    check_executable_scripts(root, report)
    check_definitions(texts, candidates, report)
    check_read_only_agents(texts, report)
    check_codex_agents(texts, report)
    check_skill_invocation_policy(texts, candidates, report)
    check_commit_table(texts, report)
    check_generated(root, report)
    check_compat_links(root, candidates, report)
    managed = parse_item_array(texts, "MANAGED_ITEMS", report, fields=3)
    check_managed_items(root, texts, candidates, managed, report)
    for rel, text in sorted(texts.items()):
        check_text(root, rel, text, managed, report)
    check_bash_syntax(root, texts, report)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        subprocess.run(["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        print(redact(f"error: not a git working tree: {root}"), file=sys.stderr)
        return 2
    report = validate(root)
    for message in report.errors:
        print(f"ERROR {message}")
    if report.errors:
        print(f"validate: FAILED ({len(report.errors)} problem(s))")
        return 1
    print("validate: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
