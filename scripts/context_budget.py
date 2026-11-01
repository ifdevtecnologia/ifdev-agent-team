#!/usr/bin/env python3
"""Estimates the context that the installed global instructions load in every session.

Always loaded: `claude/CLAUDE.md` and every Markdown file under `claude/rules/` (searched
recursively, as Claude Code does) without a non-empty `paths` key in its YAML frontmatter. Rules
with `paths` patterns load only when Claude reads matching files, so they are listed as on demand
and not counted. A rule whose frontmatter cannot be parsed, or whose `paths` is empty, is counted
as always loaded (the conservative choice).

Estimation method: tokens = ceil(bytes / BYTES_PER_TOKEN), with 4 bytes per token as a rough
average for English Markdown. This is an estimate, not a tokenizer count; the real number depends
on the model's tokenizer and the text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from validate import Report, parse_frontmatter

BYTES_PER_TOKEN = 4
ENTRY_POINT = "claude/CLAUDE.md"
RULES_DIR = "claude/rules"


def estimate_tokens(size: int) -> int:
    return -(-size // BYTES_PER_TOKEN)


def has_paths(text: str) -> bool:
    """True when the rule's frontmatter declares non-empty `paths`, which makes Claude Code load it on demand."""
    fields = parse_frontmatter("", text, Report()) if text.startswith("---") else None
    return fields is not None and bool(fields.get("paths"))


def classify(root: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Returns (always_loaded, on_demand) as lists of (relative path, size in bytes)."""
    always = [(ENTRY_POINT, len((root / ENTRY_POINT).read_bytes()))]
    on_demand = []
    for path in sorted((root / RULES_DIR).rglob("*.md")):
        data = path.read_bytes()
        entry = (path.relative_to(root).as_posix(), len(data))
        (on_demand if has_paths(data.decode("utf-8")) else always).append(entry)
    return always, on_demand


def render(always: list[tuple[str, int]], on_demand: list[tuple[str, int]]) -> str:
    lines = [
        f"Always loaded every session (estimate: tokens = ceil(bytes / {BYTES_PER_TOKEN}), not a tokenizer count)",
        f"{'bytes':>8} {'~tokens':>8}  file",
    ]
    lines += [f"{size:>8} {estimate_tokens(size):>8}  {rel}" for rel, size in always]
    total = sum(size for _, size in always)
    lines.append(f"{total:>8} {estimate_tokens(total):>8}  total")
    lines.append("On demand (rules with `paths` patterns in the frontmatter; not counted)")
    lines += [f"{size:>8} {estimate_tokens(size):>8}  {rel}" for rel, size in on_demand] or ["  (none)"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    print(render(*classify(args.root.resolve())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
