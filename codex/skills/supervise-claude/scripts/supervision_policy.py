#!/usr/bin/env python3
"""Deterministic supervision policy: Claude cycle budget, Mechanical remediation and delivery ownership.

Encodes only the objective boundaries of the supervise-claude skill. Codex review classifies
findings and changes and passes those categories in; this module never judges code. Unknown
categories are treated as non-mechanical, so doubt fails closed (delegate or BLOCKED).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath


MAX_CLAUDE_CYCLES = 3

# Operations Codex may run itself, without invoking Claude, as Mechanical remediation.
MECHANICAL_OPERATIONS = frozenset({"formatter", "whitespace-normalization", "line-ending-normalization"})
# Failure/change categories such an operation may resolve or produce.
MECHANICAL_CATEGORIES = frozenset({"formatting", "whitespace", "line-endings"})
# Named here so that reasons are explicit; any other non-mechanical category is also refused.
FORBIDDEN_CHANGES = frozenset(
    {
        "semantic",
        "correctness",
        "test",
        "assertion",
        "refactor",
        "sql",
        "rls",
        "auth",
        "security",
        "migration",
        "dependency",
        "architecture",
        "behavior",
        "suppression",
    }
)

# Delivery actions and their only permitted actor. Claude owns none; unlisted actions (such
# as enabling automatic merge or pushing to the default branch) are permitted to nobody.
DELIVERY_OWNERS = {
    "stage": "codex",
    "commit": "codex",
    "push-task-branch": "codex",
    "create-pr": "codex",
    "approve-pr": "human",
    "merge-pr": "human",
}


class PolicyViolation(ValueError):
    """A requested transition is outside the supervision policy."""


@dataclass(frozen=True)
class RunState:
    cycles_started: int = 0
    task_paths: frozenset = frozenset()
    # Categories of every unresolved validation failure and review finding.
    unresolved: frozenset = frozenset()
    # True once Codex has independently reviewed/validated the current tree.
    validated: bool = False


@dataclass(frozen=True)
class Remediation:
    operation: str
    paths: tuple = ()
    command_inspected: bool = False
    deterministic: bool = False
    # Categories of change the operation produces, as classified by Codex diff review.
    changes: frozenset = frozenset()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple = ()


def _unsafe_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return not path or pure.is_absolute() or ".." in pure.parts


def evaluate_remediation(remediation: Remediation, state: RunState) -> Decision:
    reasons = []
    if not 0 <= state.cycles_started <= MAX_CLAUDE_CYCLES:
        reasons.append(f"invalid cycle count: {state.cycles_started}")
    if remediation.operation not in MECHANICAL_OPERATIONS:
        reasons.append(f"not a mechanical operation: {remediation.operation}")
    if not remediation.command_inspected:
        reasons.append("command was not inspected before execution")
    if not remediation.deterministic:
        reasons.append("operation is not deterministic")
    if not remediation.paths:
        reasons.append("no explicit target paths")
    for path in remediation.paths:
        if _unsafe_path(path) or path not in state.task_paths:
            reasons.append(f"path outside task scope: {path}")
    for change in sorted(set(remediation.changes) - MECHANICAL_CATEGORIES):
        label = "forbidden change" if change in FORBIDDEN_CHANGES else "unclassified change"
        reasons.append(f"{label}: {change}")
    if not remediation.changes:
        reasons.append("no mechanical change declared")
    if state.cycles_started >= MAX_CLAUDE_CYCLES and not (
        state.unresolved and state.unresolved <= MECHANICAL_CATEGORIES
    ):
        reasons.append("after the final Claude cycle, only a sole remaining mechanical failure is eligible")
    return Decision(not reasons, tuple(reasons))


def start_claude_cycle(state: RunState) -> RunState:
    """Persisted BEFORE invoking Claude; the only transition that consumes the budget."""
    if state.cycles_started >= MAX_CLAUDE_CYCLES:
        raise PolicyViolation("Claude cycle budget exhausted; never run cycle four")
    return replace(state, cycles_started=state.cycles_started + 1, validated=False)


def record_review(state: RunState, unresolved) -> RunState:
    return replace(state, unresolved=frozenset(unresolved), validated=True)


def apply_remediation(state: RunState, remediation: Remediation) -> RunState:
    """Mechanical remediation is not a Claude invocation: the cycle counter is unchanged."""
    decision = evaluate_remediation(remediation, state)
    if not decision.allowed:
        raise PolicyViolation("; ".join(decision.reasons))
    return replace(state, validated=False)


def next_step(state: RunState) -> str:
    """Returns DELEGATE, REVIEW, REMEDIATE, COMPLETE or BLOCKED."""
    if state.cycles_started == 0:
        return "DELEGATE"
    if not state.validated:
        return "REVIEW"
    if not state.unresolved:
        return "COMPLETE"
    if state.unresolved <= MECHANICAL_CATEGORIES:
        return "REMEDIATE"
    return "DELEGATE" if state.cycles_started < MAX_CLAUDE_CYCLES else "BLOCKED"


def may_perform(actor: str, action: str) -> bool:
    return DELIVERY_OWNERS.get(action) == actor


def delivery_allowed(state: RunState) -> bool:
    return next_step(state) == "COMPLETE"


def _normalized_lines(text: str) -> list:
    lines = [line.rstrip(" \t") for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def is_normalization_only(before: str, after: str) -> bool:
    """True when the texts differ at most in trailing whitespace, line endings or final newlines."""
    return _normalized_lines(before) == _normalized_lines(after)


def _request_from_json(data: dict) -> tuple:
    state = data.get("state", {})
    remediation = data.get("remediation", {})
    return (
        Remediation(
            operation=str(remediation.get("operation", "")),
            paths=tuple(remediation.get("paths", ())),
            command_inspected=remediation.get("command_inspected") is True,
            deterministic=remediation.get("deterministic") is True,
            changes=frozenset(remediation.get("changes", ())),
        ),
        RunState(
            cycles_started=int(state.get("cycles_started", 0)),
            task_paths=frozenset(state.get("task_paths", ())),
            unresolved=frozenset(state.get("unresolved", ())),
            validated=state.get("validated") is True,
        ),
    )


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-remediation", help="evaluate a JSON request with 'remediation' and 'state'")
    check.add_argument("--request", required=True, type=Path)
    normalization = commands.add_parser("check-normalization", help="compare a file before and after normalization")
    normalization.add_argument("before", type=Path)
    normalization.add_argument("after", type=Path)
    args = parser.parse_args(argv)

    if args.command == "check-remediation":
        remediation, state = _request_from_json(json.loads(args.request.read_text(encoding="utf-8")))
        decision = evaluate_remediation(remediation, state)
        print(json.dumps({"allowed": decision.allowed, "reasons": list(decision.reasons)}))
        return 0 if decision.allowed else 1
    with args.before.open(encoding="utf-8", newline="") as before, args.after.open(encoding="utf-8", newline="") as after:
        same = is_normalization_only(before.read(), after.read())
    print("normalization-only" if same else "content changed")
    return 0 if same else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
