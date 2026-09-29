#!/usr/bin/env python3
"""Bounded static lint for the ifdev-ai-tools repository.

Checks the required tree, entry-point executable bits, skill/agent YAML frontmatter, local
Markdown links, references to installed global files, machine-specific paths, product/domain
content, sensitive paths, credential patterns, unsafe prescriptions, obsolete delivery
instructions, commit convention consistency, managed items, managed Codex profile settings and
Bash syntax.

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

REQUIRED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".editorconfig",
    ".gitignore",
    "requirements-dev.txt",
    "claude/CLAUDE.md",
    "claude/rules/engineering-principles.md",
    "claude/rules/testing.md",
    "claude/rules/git-workflow.md",
    "claude/agents/senior-reviewer.md",
    "claude/skills/work-session/SKILL.md",
    "claude/skills/work-session/references/commit-proposal.md",
    "claude/skills/verify-changes/SKILL.md",
    "claude/skills/verify-changes/references/commit-messages.md",
    "codex/skills/supervise-claude/SKILL.md",
    "codex/skills/supervise-claude/references/safety-and-preflight.md",
    "codex/skills/supervise-claude/references/delegation.md",
    "codex/skills/supervise-claude/references/review.md",
    "codex/skills/supervise-claude/references/delivery.md",
    "codex/skills/supervise-claude/references/mechanical-remediation.md",
    "codex/skills/supervise-claude/references/isolated-clone-fallback.md",
    "codex/skills/supervise-claude/references/model-selection.md",
    "codex/skills/supervise-claude/references/protected-path-handoff.md",
    "codex/skills/supervise-claude/scripts/run_claude.py",
    "codex/skills/supervise-claude/scripts/protected_patch.py",
    "codex/skills/supervise-claude/scripts/supervision_policy.py",
    "codex/skills/supervise-claude/scripts/clone_fallback.py",
    "codex/skills/supervise-claude/scripts/notify_final_status.py",
    "codex/profiles/ifdev-autonomous.config.toml",
    "codex/notify/agent-notify-codex.sh",
    "claude/hooks/agent-notify-hook.sh",
    "scripts/install.sh",
    "scripts/uninstall.sh",
    "scripts/validate.sh",
    "scripts/validate.py",
    "scripts/lib/common.sh",
    "scripts/lib/claude_hooks.py",
    "scripts/agent-notify.sh",
    "tests/test_install.py",
    "tests/test_validate.py",
    "tests/test_supervise_claude.py",
    "tests/test_agent_notify.py",
    "tests/test_agent_notify_hook.py",
    "tests/test_claude_hooks.py",
    ".github/workflows/validate.yml",
)

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

# Obsolete global delivery rules that contradict claude/rules/git-workflow.md: approval for
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
DELIVERY_RULE_SCOPE = ("claude/", "codex/")
# Only the supervisor skill may forbid Claude, the delegated implementer, from delivering.
IMPLEMENTER_ONLY_SCOPE = "codex/skills/supervise-claude/"
IMPLEMENTER_ACTOR = re.compile(r"\bClaude\b")

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
GLOBAL_REFERENCE = re.compile(r"~/\.(claude|codex)/([^\s`'\")\]>]+)")
MANAGED_REFERENCE_PREFIXES = {
    "claude": ("CLAUDE.md", "rules/", "agents/", "skills/"),
    "codex": ("skills/",),
}

MANAGED_ITEMS_FILE = "scripts/lib/common.sh"
MANAGED_ITEMS_BLOCK = re.compile(r"^MANAGED_ITEMS=\((.*?)^\)", re.MULTILINE | re.DOTALL)
MANAGED_ITEM = re.compile(r'^\s*"([^"|]+)\|([^"|]+)"\s*$')
BASE_CODEX_CONFIG = ".codex/config.toml"

# Managed opt-in Codex profiles and their exact settings. Profiles live beside the base config
# as $CODEX_HOME/<name>.config.toml and are selected with `codex --profile <name>`.
CODEX_PROFILES_DIR = "codex/profiles/"
CODEX_PROFILES = {
    "codex/profiles/ifdev-autonomous.config.toml": {
        "approval_policy": "never",
        "sandbox_mode": "workspace-write",
        "sandbox_workspace_write.network_access": True,
    },
}
FORBIDDEN_PROFILE_VALUES = {("sandbox_mode", "danger-full-access")}
# A deliberately small TOML subset: comments, simple tables, string and boolean values.
TOML_TABLE = re.compile(r"^\[([A-Za-z0-9_-]+)\]\s*(?:#.*)?$")
TOML_ENTRY = re.compile(r'^([A-Za-z0-9_-]+)\s*=\s*("[^"\\]*"|true|false)\s*(?:#.*)?$')

NAME_FORMAT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SKILL_PATH = re.compile(r"^(claude|codex)/skills/([^/]+)/(.+)$")
AGENT_PATH = re.compile(r"^claude/agents/([^/]+)\.md$")


SAFE_YAML_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# PyYAML quotes the offending input (characters, tags, aliases) in its problem strings.
YAML_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
MAX_YAML_PROBLEM = 120


def redact(text: str) -> str:
    """Replaces recognized credential values so that no diagnostic can echo them."""
    for label, pattern in CREDENTIAL_PATTERNS.items():
        text = pattern.sub(lambda _: f"[redacted {label}]", text)
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def check_definition(rel: str, expected_name: str, texts: dict[str, str], seen: dict, kind: str, report: Report) -> None:
    fields = parse_frontmatter(rel, texts[rel], report)
    if fields is None:
        return
    description = fields.get("description")
    if not isinstance(description, str) or not description.strip():
        report.add(rel, "frontmatter 'description' must be a non-empty string")
    name = fields.get("name")
    if not isinstance(name, str) or not name.strip():
        report.add(rel, "frontmatter 'name' must be a non-empty string")
        return
    if not NAME_FORMAT.match(name):
        report.add(rel, "frontmatter requires a lowercase kebab-case name")
    elif name != expected_name:
        report.add(rel, f"name '{name}' must match '{expected_name}'")
    if name in seen.setdefault(kind, {}):
        report.add(rel, f"duplicate {kind} name '{name}' (also in {seen[kind][name]})")
    seen[kind][name] = rel


def check_definitions(texts: dict[str, str], candidates: list[str], report: Report) -> None:
    """Evaluates definitions from the full path inventory, so unreadable or unsafe ones still fail."""
    seen: dict = {}
    skill_dirs: dict[tuple[str, str], bool] = {}
    for rel in candidates:
        match = SKILL_PATH.match(rel)
        if match:
            key = (match.group(1), match.group(2))
            skill_dirs[key] = skill_dirs.get(key, False) or match.group(3) == "SKILL.md"
    for (tool, name), has_skill in sorted(skill_dirs.items()):
        rel = f"{tool}/skills/{name}/SKILL.md"
        if not has_skill or rel not in texts:
            report.add(f"{tool}/skills/{name}", "skill directory has no readable SKILL.md")
            continue
        check_definition(rel, name, texts, seen, f"{tool} skill", report)
    for rel in candidates:
        match = AGENT_PATH.match(rel)
        if not match:
            continue
        if rel not in texts:
            report.add(rel, "agent definition is missing, unreadable or unsafe")
            continue
        check_definition(rel, match.group(1), texts, seen, "claude agent", report)


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
    implementer_scope = rel.startswith(IMPLEMENTER_ONLY_SCOPE)
    for line, sentence in prose_sentences(text):
        if implementer_scope and IMPLEMENTER_ACTOR.search(sentence):
            continue
        if PER_COMMIT_APPROVAL.search(sentence) and not PER_COMMIT_EXCEPTIONS.search(sentence):
            report.add(rel, "obsolete delivery instruction (per-commit approval); see claude/rules/git-workflow.md", line)
        if PUSH_PROHIBITION.search(sentence) and not PUSH_EXCEPTIONS.search(sentence):
            report.add(rel, "obsolete delivery instruction (unconditional push prohibition); see claude/rules/git-workflow.md", line)


def check_commit_convention(rel: str, text: str, report: Report) -> None:
    for match in COMMIT_SUBJECT.finditer(text):
        emoji, commit_type = match.groups()
        if COMMIT_TYPES[commit_type] != emoji:
            report.add(rel, f"commit emoji does not match type '{commit_type}'", line_of(text, match.start()))


def check_commit_table(texts: dict[str, str], report: Report) -> None:
    rel = "claude/rules/git-workflow.md"
    if rel not in texts:
        return
    rows = {t: e for e, t in COMMIT_TABLE_ROW.findall(texts[rel])}
    if rows != COMMIT_TYPES:
        report.add(rel, "commit type table must list exactly the nine emoji/type pairs")


def check_links(root: Path, rel: str, text: str, report: Report) -> None:
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
            if not (root / tool / reference.rstrip("/")).exists():
                report.add(rel, f"reference ~/.{tool}/{reference} has no source {tool}/{reference}", number)


def check_text(root: Path, rel: str, text: str, report: Report) -> None:
    for label, pattern in CREDENTIAL_PATTERNS.items():
        for match in pattern.finditer(text):
            report.add(rel, f"possible credential ({label}); value not shown", line_of(text, match.start()))
    for match in HOME_PATH.finditer(text):
        report.add(rel, "hardcoded machine-specific home path", line_of(text, match.start()))
    for match in PRODUCT_TERMS.finditer(text):
        report.add(rel, f"product/domain-specific term '{match.group(0)}'", line_of(text, match.start()))
    if rel.endswith(".md"):
        check_unsafe_prescriptions(rel, text, report)
        if rel.startswith(DELIVERY_RULE_SCOPE):
            check_obsolete_delivery_rules(rel, text, report)
        check_commit_convention(rel, text, report)
        check_links(root, rel, text, report)


def parse_managed_items(texts: dict[str, str], report: Report) -> list[tuple[str, str]]:
    text = texts.get(MANAGED_ITEMS_FILE)
    match = MANAGED_ITEMS_BLOCK.search(text) if text is not None else None
    if match is None:
        report.add(MANAGED_ITEMS_FILE, "MANAGED_ITEMS array not found")
        return []
    items = []
    for offset, line in enumerate(match.group(1).split("\n")):
        if not line.strip() or line.strip().startswith("#"):
            continue
        entry = MANAGED_ITEM.match(line)
        if entry is None:
            report.add(MANAGED_ITEMS_FILE, "malformed MANAGED_ITEMS entry", line_of(text, match.start(1)) + offset)
            continue
        items.append(entry.groups())
    return items


def check_managed_items(root: Path, texts: dict[str, str], candidates: list[str], report: Report) -> None:
    items = parse_managed_items(texts, report)
    destinations: set[str] = set()
    for source, destination in items:
        path = root / source
        if path.is_symlink() or not (path.is_file() or path.is_dir()) or has_unsafe_parent(root, source):
            report.add(MANAGED_ITEMS_FILE, f"managed source '{source}' is missing or unsafe")
        elif path.is_dir() and not any(c.startswith(source.rstrip("/") + "/") for c in candidates):
            report.add(MANAGED_ITEMS_FILE, f"managed source directory '{source}' has no tracked content")
        if destination in destinations:
            report.add(MANAGED_ITEMS_FILE, f"duplicate managed destination '{destination}'")
        destinations.add(destination)
        if destination == BASE_CODEX_CONFIG:
            report.add(MANAGED_ITEMS_FILE, "managed items must never replace the base Codex config")
    managed = dict(items)
    for rel in candidates:
        if rel.startswith(CODEX_PROFILES_DIR) and rel not in CODEX_PROFILES:
            report.add(rel, "unmanaged Codex profile (add it to CODEX_PROFILES in scripts/validate.py)")
    for rel in CODEX_PROFILES:
        expected = ".codex/" + PurePosixPath(rel).name
        if managed.get(rel) != expected:
            report.add(MANAGED_ITEMS_FILE, f"Codex profile '{rel}' must be managed as '{expected}'")


def parse_profile(rel: str, text: str, report: Report) -> dict | None:
    settings: dict = {}
    table = ""
    ok = True
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = TOML_TABLE.match(line)
        entry = TOML_ENTRY.match(line)
        if header:
            table = header.group(1) + "."
            continue
        if entry is None:
            report.add(rel, "unsupported profile syntax (only comments, simple tables, strings and booleans)", number)
            ok = False
            continue
        key, value = table + entry.group(1), entry.group(2)
        if key in settings:
            report.add(rel, f"duplicate profile setting '{key}'", number)
            ok = False
        settings[key] = value.strip('"') if value.startswith('"') else value == "true"
    return settings if ok else None


def check_codex_profiles(texts: dict[str, str], report: Report) -> None:
    for rel, expected in CODEX_PROFILES.items():
        if rel not in texts:
            continue  # reported by check_required
        settings = parse_profile(rel, texts[rel], report)
        if settings is None:
            continue
        for key, value in sorted(settings.items()):
            if (key, value) in FORBIDDEN_PROFILE_VALUES:
                report.add(rel, f"forbidden profile setting '{key} = {value}'")
            elif key not in expected:
                report.add(rel, f"unexpected profile setting '{key}'")
            elif value != expected[key]:
                report.add(rel, f"profile setting '{key}' must be {expected[key]!r}")
        for key in sorted(set(expected) - set(settings)):
            report.add(rel, f"missing profile setting '{key}'")


def check_bash_syntax(root: Path, texts: dict[str, str], report: Report) -> None:
    for rel in sorted(texts):
        if not rel.endswith(".sh"):
            continue
        result = subprocess.run(["bash", "-n", str(root / rel)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            report.add(rel, f"bash syntax error: {detail[0] if detail else 'unknown'}")


def check_executable_scripts(root: Path, report: Report) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "-z", "--", *EXECUTABLE_SCRIPTS],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    check_commit_table(texts, report)
    check_managed_items(root, texts, candidates, report)
    check_codex_profiles(texts, report)
    for rel, text in sorted(texts.items()):
        check_text(root, rel, text, report)
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
