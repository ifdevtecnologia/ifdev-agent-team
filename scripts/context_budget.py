#!/usr/bin/env python3
"""Estimates the context that the installed global instructions load in every session.

Reported per target whose generated entry point exists:
- claude: the generated `dist/claude/CLAUDE.md` and every Markdown file under `core/rules/`
  (searched recursively, as Claude Code does) without a non-empty `paths` key in its YAML
  frontmatter. Rules with `paths` patterns load only when Claude reads matching files, so they are
  listed as on demand and not counted. A rule whose frontmatter cannot be parsed, or whose `paths`
  is empty, is counted as always loaded (the conservative choice).
- codex: the generated `dist/codex/AGENTS.md`, which already holds the rules (Codex has no rules
  directory or on-demand rules).

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
ENTRY_POINT = "dist/claude/CLAUDE.md"
CODEX_ENTRY_POINT = "dist/codex/AGENTS.md"
RULES_DIR = "core/rules"


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


def classify_codex(root: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Codex loads its global AGENTS.md whole, with the rules inside, and nothing on demand."""
    return [(CODEX_ENTRY_POINT, len((root / CODEX_ENTRY_POINT).read_bytes()))], []


TARGETS = (("claude", ENTRY_POINT, classify), ("codex", CODEX_ENTRY_POINT, classify_codex))


def report(root: Path) -> str:
    """The render of every target whose generated entry point exists, each under a `Target:` line."""
    sections = [
        f"Target: {name}\n{render(*classify_target(root))}"
        for name, entry_point, classify_target in TARGETS
        if (root / entry_point).is_file()
    ]
    return "\n\n".join(sections) or "No generated entry point found; run python3 scripts/build_targets.py"


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
    print(report(args.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
