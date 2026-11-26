"""Schema and loader of a benchmark task's `task.yaml`.

The standard library has no YAML parser, so `task.yaml` is written in a small documented subset of
YAML (see benchmark/README.md): top-level `key: value` scalars, block lists of scalars (`- item`)
and one-level mappings of scalars. Comments and blank lines are allowed; anchors, flow collections,
multi-line strings and nesting deeper than one level are rejected. Every valid file in this subset
is also valid YAML with the same meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

TASK_FILE = "task.yaml"

# Task categories from #28; to be checked against the measurement plan (plan §4.2) when it is committed.
TASK_TYPES = ("bugfix", "feature", "refactor", "security")
TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
CRITERION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]{0,254}$")
HIDDEN_PLACEHOLDER = "{test}"

REQUIRED_KEYS = ("id", "type", "stack", "image", "commands", "expected_files", "protected_paths", "criteria", "hidden_tests")
COMMAND_KEYS = {"test": True, "hidden": True, "build": False}

# The value group keeps its leading blanks (stripped later), so no two quantifiers compete for them.
_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:[ \t](.*))?$")
_NESTED_KEY_LINE = re.compile(r"^  ([^\s:#'\"][^:#]*|'[^']*'|\"[^\"]*\"):(?:[ \t](.*))?$")
_LIST_LINE = re.compile(r"^  - (.*)$")


class TaskError(ValueError):
    """An invalid task directory or task.yaml; the message names the file and the field."""


@dataclass(frozen=True)
class Task:
    """A validated task. Paths are POSIX paths relative to the task's snapshot (or hidden/ for hidden_tests)."""

    id: str
    type: str
    stack: str
    image: str
    test_command: str
    hidden_command: str
    build_command: str | None
    expected_files: tuple[str, ...]
    protected_paths: tuple[str, ...]
    criteria: tuple[str, ...]
    hidden_tests: dict[str, str]
    directory: Path

    @property
    def snapshot(self) -> Path:
        return self.directory / "snapshot"

    @property
    def hidden(self) -> Path:
        return self.directory / "hidden"

    @property
    def reference(self) -> Path:
        return self.directory / "reference"

    @property
    def prompt(self) -> Path:
        return self.directory / "prompt.md"

    def hidden_command_for(self, test_path: str) -> str:
        return self.hidden_command.replace(HIDDEN_PLACEHOLDER, test_path)


# Plain scalars that YAML would not read as the same string: null, booleans (YAML 1.1 included) and numbers.
_NON_STRING_PLAIN = re.compile(
    r"^(?:~|null|true|false|yes|no|on|off|y|n|[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?|0x[0-9a-f]+|0o[0-7]+|0b[01]+|\d{4}-\d{1,2}-\d{1,2}(?:[ tT].*)?"
    r"|[-+]?\.inf|\.nan|\d+(?::\d+)+)$",
    re.IGNORECASE,
)
_QUOTED = {"'": re.compile(r"^'((?:[^']|'')*)'(?:\s+#.*)?$"), '"': re.compile(r'^"([^"\\]*)"(?:\s+#.*)?$')}


def _scalar(raw: str, where: str) -> str:
    value = raw.strip()

    if value[:1] in _QUOTED:
        match = _QUOTED[value[0]].match(value)

        if match is None:
            raise TaskError(f"{where}: unsupported quoted string (one quoted value, no escapes, optional comment)")

        return match.group(1).replace("''", "'") if value[0] == "'" else match.group(1)

    value = re.sub(r"\s#.*", "", value).rstrip()

    if not value or value[0] in "[]{}&*!|>%@`,#-?:" and not re.match(r"^-[^\s]", value):
        raise TaskError(f"{where}: unsupported or empty value (quote it)")

    if ": " in value or value.endswith(":") or _NON_STRING_PLAIN.match(value):
        raise TaskError(f"{where}: value {value!r} would not be a plain string in YAML (quote it)")

    return value


def _strip_comment_line(line: str) -> bool:
    stripped = line.strip()

    return not stripped or stripped.startswith("#")


def parse_subset(text: str, source: str = TASK_FILE) -> dict[str, object]:
    """Parses the documented YAML subset into str, list[str] and dict[str, str] values."""
    data: dict[str, object] = {}
    current: str | None = None

    for number, line in enumerate(text.splitlines(), start=1):
        where = f"{source}:{number}"

        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise TaskError(f"{where}: tabs are not allowed in indentation")

        if _strip_comment_line(line):
            continue

        if not line.startswith(" "):
            match = _KEY_LINE.match(line.rstrip())

            if match is None:
                raise TaskError(f"{where}: expected 'key: value'")

            key, raw = match.group(1), match.group(2)

            if key in data:
                raise TaskError(f"{where}: duplicate key '{key}'")

            if raw is None or not raw.strip() or raw.strip().startswith("#"):
                data[key] = None
                current = key
            else:
                data[key] = _scalar(raw, where)
                current = None

            continue

        if current is None:
            raise TaskError(f"{where}: unexpected indentation")

        list_match = _LIST_LINE.match(line.rstrip())

        if list_match is not None:
            if isinstance(data[current], dict):
                raise TaskError(f"{where}: '{current}' mixes list items and keys")

            data[current] = [*(data[current] or []), _scalar(list_match.group(1), where)]
            continue

        nested = _NESTED_KEY_LINE.match(line.rstrip())

        if nested is None or nested.group(2) is None:
            raise TaskError(f"{where}: expected '  - item' or '  key: value' (one level of nesting)")

        if isinstance(data[current], list):
            raise TaskError(f"{where}: '{current}' mixes list items and keys")

        mapping = data[current] or {}
        key = _scalar(nested.group(1), where)

        if key in mapping:
            raise TaskError(f"{where}: duplicate key '{current}.{key}'")

        mapping[key] = _scalar(nested.group(2), where)
        data[current] = mapping

    return data


def check_relative_path(value: str, field: str) -> str:
    """Rejects absolute paths, '..' components and empty or odd paths in task fields."""
    path = PurePosixPath(value)

    if not value or value.startswith("/") or "\\" in value or ".." in path.parts or path.parts[:1] == (".",):
        raise TaskError(f"{field}: '{value}' must be a relative path without '..'")

    return str(path)


def _require_type(data: dict[str, object], key: str, kind: type, label: str) -> object:
    value = data[key]

    if kind is list and value is None:
        return []

    if not isinstance(value, kind):
        raise TaskError(f"{TASK_FILE}: field '{key}' must be {label}")

    return value


def _commands(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TaskError(f"{TASK_FILE}: field 'commands' must be a mapping")

    unknown = sorted(set(value) - set(COMMAND_KEYS))

    if unknown:
        raise TaskError(f"{TASK_FILE}: unknown key 'commands.{unknown[0]}'")

    for key, required in COMMAND_KEYS.items():
        if required and not value.get(key):
            raise TaskError(f"{TASK_FILE}: missing required field 'commands.{key}'")

    if HIDDEN_PLACEHOLDER not in value["hidden"]:
        raise TaskError(f"{TASK_FILE}: field 'commands.hidden' must contain {HIDDEN_PLACEHOLDER}")

    return value


def validate(data: dict[str, object], directory: Path) -> Task:
    unknown = sorted(set(data) - set(REQUIRED_KEYS))

    if unknown:
        raise TaskError(f"{TASK_FILE}: unknown key '{unknown[0]}'")

    for key in REQUIRED_KEYS:
        if key not in data:
            raise TaskError(f"{TASK_FILE}: missing required field '{key}'")

    patterns = {"id": TASK_ID, "stack": SLUG, "image": IMAGE}

    for key, pattern in patterns.items():
        value = _require_type(data, key, str, "a string")

        if not pattern.match(value):
            raise TaskError(f"{TASK_FILE}: field '{key}' has an invalid value '{value}'")

    if _require_type(data, "type", str, "a string") not in TASK_TYPES:
        raise TaskError(f"{TASK_FILE}: field 'type' must be one of {', '.join(TASK_TYPES)}")

    commands = _commands(data["commands"])
    expected = [check_relative_path(p, "expected_files") for p in _require_type(data, "expected_files", list, "a list")]
    protected = [check_relative_path(p, "protected_paths") for p in _require_type(data, "protected_paths", list, "a list")]
    criteria = _require_type(data, "criteria", list, "a list")

    if not criteria:
        raise TaskError(f"{TASK_FILE}: field 'criteria' must list at least one criterion ID")

    for criterion in criteria:
        if not CRITERION_ID.match(criterion):
            raise TaskError(f"{TASK_FILE}: field 'criteria' has an invalid ID '{criterion}'")

    if len(set(criteria)) != len(criteria):
        raise TaskError(f"{TASK_FILE}: field 'criteria' has duplicate IDs")

    hidden_tests = _require_type(data, "hidden_tests", dict, "a mapping of hidden test file to criterion ID")
    hidden = {check_relative_path(path, "hidden_tests"): criterion for path, criterion in hidden_tests.items()}

    for path, criterion in hidden.items():
        if criterion not in criteria:
            raise TaskError(f"{TASK_FILE}: field 'hidden_tests' maps '{path}' to unknown criterion '{criterion}'")

    return Task(
        id=data["id"],
        type=data["type"],
        stack=data["stack"],
        image=data["image"],
        test_command=commands["test"],
        hidden_command=commands["hidden"],
        build_command=commands.get("build"),
        expected_files=tuple(expected),
        protected_paths=tuple(protected),
        criteria=tuple(criteria),
        hidden_tests=hidden,
        directory=directory,
    )


def load_task(directory: Path) -> Task:
    """Loads and validates <directory>/task.yaml; raises TaskError with the field name."""
    path = directory / TASK_FILE

    if not path.is_file():
        raise TaskError(f"{directory.name}: missing {TASK_FILE}")

    return validate(parse_subset(path.read_text(encoding="utf-8")), directory)
