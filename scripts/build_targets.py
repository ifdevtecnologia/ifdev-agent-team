#!/usr/bin/env python3
"""Generates the per-tool files in `dist/` from the neutral sources in `core/` and `targets/`.

Inputs are read in sorted order and outputs use LF with no timestamps or absolute paths, so a
rerun on unchanged sources writes nothing. `--check` writes nothing and exits non-zero when a
generated file is missing or stale, or when `dist/` holds a file the generator does not produce.

Claude target:
- `dist/claude/CLAUDE.md` = `targets/claude/adapter.md` + `core/instructions.md`.
- `dist/claude/agents/<role>.md` = `core/agents/<role>.md` with its `read_only` frontmatter line
  replaced, in place, by the Claude tool lists, + `targets/claude/agent-footer.md`.
An empty adapter or footer adds nothing, so the output is then byte-identical to its source.

Standard library only (Python 3.9+): the installer and CI need no extra dependency.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DIST = "dist"
INSTRUCTIONS = "core/instructions.md"
AGENTS_DIR = "core/agents"
CLAUDE_TARGET = "targets/claude"

# Every role reads, searches and runs commands; writers also edit files. Read-only roles block
# the edit tools explicitly, which Claude Code enforces.
CLAUDE_READ_ONLY_TOOLS = ("tools: Read, Glob, Grep, Bash", "disallowedTools: Write, Edit")
CLAUDE_WRITER_TOOLS = ("tools: Read, Glob, Grep, Bash, Write, Edit",)
READ_ONLY_VALUES = {"true": True, "false": False}


class BuildError(Exception):
    """A source cannot be turned into its generated form."""


def join_sections(*sections: str) -> str:
    """Joins the non-empty sections with one blank line; empty sections add nothing."""
    present = [section for section in sections if section.strip()]
    if len(present) <= 1:
        return present[0] if present else ""
    return "\n\n".join(section.rstrip("\n") for section in present) + "\n"


def claude_agent(rel: str, text: str, footer: str) -> str:
    """Replaces the neutral `read_only` frontmatter line with the Claude tool lists."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise BuildError(f"{rel}: missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise BuildError(f"{rel}: unterminated frontmatter") from None
    frontmatter: list[str] = []
    found = False
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        if key != "read_only":
            frontmatter.append(line)
            continue
        if found:
            raise BuildError(f"{rel}: duplicate 'read_only' key")
        if value.strip() not in READ_ONLY_VALUES:
            raise BuildError(f"{rel}: 'read_only' must be true or false")
        found = True
        frontmatter.extend(CLAUDE_READ_ONLY_TOOLS if READ_ONLY_VALUES[value.strip()] else CLAUDE_WRITER_TOOLS)
    if not found:
        raise BuildError(f"{rel}: missing 'read_only' key")
    agent = "\n".join([lines[0], *frontmatter, *lines[end:]])
    return join_sections(agent, footer)


def read_text(root: Path, rel: str) -> str:
    path = root / rel
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"{rel}: source is missing or is not a regular file")
    return path.read_bytes().decode("utf-8")


def expected_files(root: Path) -> dict[str, str]:
    """Returns every generated file as {path relative to the repository: content}."""
    adapter = read_text(root, f"{CLAUDE_TARGET}/adapter.md")
    footer = read_text(root, f"{CLAUDE_TARGET}/agent-footer.md")
    files = {f"{DIST}/claude/CLAUDE.md": join_sections(adapter, read_text(root, INSTRUCTIONS))}
    agents_dir = root / AGENTS_DIR
    if agents_dir.is_symlink() or not agents_dir.is_dir():
        raise BuildError(f"{AGENTS_DIR}: source directory is missing or is not a directory")
    for path in sorted(agents_dir.glob("*.md")):
        rel = f"{AGENTS_DIR}/{path.name}"
        files[f"{DIST}/claude/agents/{path.name}"] = claude_agent(rel, read_text(root, rel), footer)
    return files


def existing_files(root: Path) -> set[str]:
    """Every path below dist/ that is not a directory (symlinks included, never followed)."""
    dist = root / DIST
    if dist.is_symlink():
        raise BuildError(f"{DIST}: must be a directory, not a symlink")
    found = set()
    for dirpath, dirnames, filenames in os.walk(dist):
        for name in filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
            found.add((Path(dirpath) / name).relative_to(root).as_posix())
    return found


def problems(root: Path) -> list[tuple[str, str]]:
    """Lists every missing, stale or extra file in dist/ as (path, problem) without writing anything."""
    expected = expected_files(root)
    existing = existing_files(root)
    found = []
    for rel, content in sorted(expected.items()):
        path = root / rel
        if rel not in existing:
            found.append((rel, "generated file is missing"))
        elif path.is_symlink() or path.read_bytes() != content.encode("utf-8"):
            found.append((rel, "generated file is stale"))
    found += [(rel, "file is not produced by the generator") for rel in sorted(existing - set(expected))]
    return found


def build(root: Path) -> list[str]:
    """Writes stale or missing files and removes extra ones; returns the changed paths."""
    expected = expected_files(root)
    changed = []
    for rel in sorted(existing_files(root) - set(expected)):
        (root / rel).unlink()
        changed.append(rel)
    for rel, content in sorted(expected.items()):
        path = root / rel
        data = content.encode("utf-8")
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        path.write_bytes(data)
        changed.append(rel)
    for dirpath, _, _ in sorted(os.walk(root / DIST), reverse=True):
        if Path(dirpath) != root / DIST and not os.listdir(dirpath):
            os.rmdir(dirpath)
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="report stale or extra files without writing")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            found = problems(root)
            for rel, problem in found:
                print(f"ERROR {rel}: {problem}")
            print("build_targets: STALE (run python3 scripts/build_targets.py)" if found else "build_targets: OK")
            return 1 if found else 0
        for rel in build(root):
            print(f"updated: {rel}")
        print("build_targets: OK")
        return 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
