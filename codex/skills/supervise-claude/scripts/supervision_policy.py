#!/usr/bin/env python3
"""Deterministic supervision policy: Claude cycle budget, model selection, Mechanical remediation and delivery ownership.

Encodes only the objective boundaries of the supervise-claude skill. Codex review classifies
findings and changes and passes those categories in; this module never judges code. Unknown
categories are treated as non-mechanical, so doubt fails closed (delegate or BLOCKED), and
never justify a model escalation.
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


# Stable aliases advertised by the Claude CLI's --model option; full model IDs are never assumed.
DEFAULT_MODEL = "sonnet"
ESCALATION_MODEL = "opus"
SUPPORTED_MODELS = (DEFAULT_MODEL, ESCALATION_MODEL)
NO_ESCALATION = "no-escalation"
# Objectively documented planning assessments that may justify escalating the initial cycle.
# Task size alone is deliberately absent.
ESCALATION_PLANNING_CATEGORIES = frozenset({"high-complexity", "high-risk"})
# Unresolved findings that may reflect implementation reasoning difficulty.
ESCALATION_FINDINGS = frozenset({"semantic", "correctness"})
# Failures a stronger model cannot fix; their presence makes escalation evidence unreliable.
ENVIRONMENT_FAILURES = frozenset(
    {
        "timeout",
        "authentication",
        "quota",
        "network",
        "transport",
        "permission-denied",
        "missing-dependency",
        "missing-sdk",
        "environment",
    }
)


class PolicyViolation(ValueError):
    """A requested transition is outside the supervision policy."""


@dataclass(frozen=True)
class CycleRecord:
    """The model decision for one already-started Claude cycle, for state.md/final-report.md."""

    cycle: int
    model: str
    reason: str


@dataclass(frozen=True)
class RunState:
    cycles_started: int = 0
    task_paths: frozenset = frozenset()
    # Categories of every unresolved validation failure and review finding.
    unresolved: frozenset = frozenset()
    # True once Codex has independently reviewed/validated the current tree.
    validated: bool = False
    # One CycleRecord per started cycle, in order; see record_cycle_model.
    cycle_records: tuple = ()


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


@dataclass(frozen=True)
class ModelRequest:
    # Empty, a supported model alias (model-only override) or NO_ESCALATION, from the task itself.
    override: str = ""
    # Categories objectively documented in the plan; only considered for the initial cycle.
    planning_assessment: frozenset = frozenset()
    # Codex review attributes the unresolved semantic/correctness finding to reasoning difficulty.
    reasoning_difficulty: bool = False
    # Recorded in state.md; an escalation without it is refused.
    justification: str = ""


@dataclass(frozen=True)
class ModelDecision:
    model: str
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


def _escalation_blockers(cycle: int, state: RunState, request: ModelRequest) -> list:
    reasons = []
    if cycle == 1:
        if not request.planning_assessment & ESCALATION_PLANNING_CATEGORIES:
            reasons.append("initial cycle lacks a documented high-complexity or high-risk planning assessment")
    elif cycle < MAX_CLAUDE_CYCLES:
        reasons.append("ordinary correction; escalation is reserved for the final allowed cycle")
    else:
        if not state.unresolved & ESCALATION_FINDINGS:
            reasons.append("no unresolved semantic or correctness finding")
        environment = sorted(state.unresolved & ENVIRONMENT_FAILURES)
        if environment:
            reasons.append("environment failures are never escalated: " + ", ".join(environment))
        if not request.reasoning_difficulty:
            reasons.append("finding is not attributed to implementation reasoning difficulty")
    if not request.justification.strip():
        reasons.append("no recorded escalation justification")
    return reasons


def select_model(state: RunState, request: ModelRequest) -> ModelDecision:
    """Model for the cycle about to start. Never affects permissions, tools or the cycle budget."""
    if state.cycles_started >= MAX_CLAUDE_CYCLES:
        raise PolicyViolation("Claude cycle budget exhausted; never run cycle four")
    override = request.override
    if override and override not in (*SUPPORTED_MODELS, NO_ESCALATION):
        # Fail safely: never substitute another model for an unsupported request.
        raise PolicyViolation(f"unsupported task-level model override: {override}")
    if override in SUPPORTED_MODELS:
        return ModelDecision(override, (f"task-level model-only override: {override}",))
    if override == NO_ESCALATION:
        return ModelDecision(DEFAULT_MODEL, ("task-level no-escalation override",))
    blockers = _escalation_blockers(state.cycles_started + 1, state, request)
    if blockers:
        return ModelDecision(DEFAULT_MODEL, tuple(blockers))
    return ModelDecision(ESCALATION_MODEL, ("escalation: " + request.justification.strip(),))


def _validate_cycle_history(records: tuple) -> None:
    """Reject a forged, incomplete or malformed cycle history before it is trusted.

    Requires sequential cycle numbers starting at 1 with no gap or duplicate, a
    supported model and a non-empty recorded reason for every entry, and at most
    MAX_CLAUDE_CYCLES entries. Used both before appending a new record and before
    rendering, so neither path can trust a history it did not itself validate.
    """
    if len(records) > MAX_CLAUDE_CYCLES:
        raise PolicyViolation(f"cycle history exceeds the maximum of {MAX_CLAUDE_CYCLES} cycles: {len(records)}")
    for index, record in enumerate(records, start=1):
        if record.cycle != index:
            raise PolicyViolation(f"cycle history is not sequential: expected cycle {index}, found {record.cycle}")
        if record.model not in SUPPORTED_MODELS:
            raise PolicyViolation(f"unsupported model in cycle {record.cycle} history: {record.model}")
        if not record.reason.strip():
            raise PolicyViolation(f"cycle {record.cycle} is missing its recorded model reason")


def record_cycle_model(state: RunState, decision: ModelDecision) -> RunState:
    """Record the model decision for the Claude cycle that was just started.

    Call this right after start_claude_cycle, with the resulting (already incremented)
    state. The cycle number is always state.cycles_started, never a caller-supplied
    value, and must directly follow the last recorded cycle: this is what keeps a
    cycle record from silently disagreeing with the actual cycle order or count. The
    prior history is fully validated before being trusted, and the resulting history
    is validated again after the new record is appended.
    """
    cycle = state.cycles_started
    if cycle == 0:
        raise PolicyViolation("no Claude cycle has been started yet")
    _validate_cycle_history(state.cycle_records)
    if len(state.cycle_records) != cycle - 1:
        raise PolicyViolation(
            f"cycle {cycle} model decision must follow {cycle - 1} recorded cycle(s), "
            f"found {len(state.cycle_records)}"
        )
    if decision.model not in SUPPORTED_MODELS:
        raise PolicyViolation(f"unsupported model in decision: {decision.model}")
    reason = "; ".join(decision.reasons)
    if not reason.strip():
        raise PolicyViolation(f"cycle {cycle} model decision has no recorded reason")
    record = CycleRecord(cycle=cycle, model=decision.model, reason=reason)
    updated = replace(state, cycle_records=state.cycle_records + (record,))
    _validate_cycle_history(updated.cycle_records)
    return updated


def render_cycles_report(state: RunState) -> str:
    """Concise 'Claude cycles:' section for final-report.md: one line per cycle, plus
    whether escalation occurred or completion avoided it. Refuses to render a history
    that is forged, malformed or does not exactly match state.cycles_started, so a
    prior record's contents, order and model are trusted only after validation, never
    on the strength of history length alone."""
    _validate_cycle_history(state.cycle_records)
    if state.cycles_started != len(state.cycle_records):
        raise PolicyViolation(
            f"cycles_started ({state.cycles_started}) does not match the recorded history "
            f"({len(state.cycle_records)} cycle(s))"
        )
    lines = ["Claude cycles:"]
    for record in state.cycle_records:
        suffix = f" ({record.reason})" if record.reason else ""
        lines.append(f"- Cycle {record.cycle}: {record.model}{suffix}")
    escalations = [record for record in state.cycle_records if record.model == ESCALATION_MODEL]
    if escalations:
        last = escalations[-1]
        lines.append(f"Escalation occurred in cycle {last.cycle}: {last.reason}")
    else:
        lines.append("No escalation required.")
    return "\n".join(lines)


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


def _cycle_records_from_json(items) -> tuple:
    return tuple(
        CycleRecord(cycle=int(item.get("cycle", 0)), model=str(item.get("model", "")), reason=str(item.get("reason", "")))
        for item in items
    )


def _cycle_record_to_json(record: CycleRecord) -> dict:
    return {"cycle": record.cycle, "model": record.model, "reason": record.reason}


def _state_from_json(data: dict) -> RunState:
    state = data.get("state", {})
    return RunState(
        cycles_started=int(state.get("cycles_started", 0)),
        task_paths=frozenset(state.get("task_paths", ())),
        unresolved=frozenset(state.get("unresolved", ())),
        validated=state.get("validated") is True,
        cycle_records=_cycle_records_from_json(state.get("cycle_records", ())),
    )


def _decision_from_json(data: dict) -> ModelDecision:
    decision = data.get("decision", {})
    return ModelDecision(model=str(decision.get("model", "")), reasons=tuple(decision.get("reasons", ())))


def _request_from_json(data: dict) -> tuple:
    remediation = data.get("remediation", {})
    return (
        Remediation(
            operation=str(remediation.get("operation", "")),
            paths=tuple(remediation.get("paths", ())),
            command_inspected=remediation.get("command_inspected") is True,
            deterministic=remediation.get("deterministic") is True,
            changes=frozenset(remediation.get("changes", ())),
        ),
        _state_from_json(data),
    )


def _model_request_from_json(data: dict) -> tuple:
    request = data.get("model", {})
    return (
        ModelRequest(
            override=str(request.get("override", "")),
            planning_assessment=frozenset(request.get("planning_assessment", ())),
            reasoning_difficulty=request.get("reasoning_difficulty") is True,
            justification=str(request.get("justification", "")),
        ),
        _state_from_json(data),
    )


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-remediation", help="evaluate a JSON request with 'remediation' and 'state'")
    check.add_argument("--request", required=True, type=Path)
    normalization = commands.add_parser("check-normalization", help="compare a file before and after normalization")
    normalization.add_argument("before", type=Path)
    normalization.add_argument("after", type=Path)
    model = commands.add_parser("select-model", help="select the model for the next cycle from a JSON request with 'model' and 'state'")
    model.add_argument("--request", required=True, type=Path)
    record_cycle = commands.add_parser(
        "record-cycle", help="record a started cycle's model decision from a JSON request with 'state' and 'decision'"
    )
    record_cycle.add_argument("--request", required=True, type=Path)
    render_cycles = commands.add_parser(
        "render-cycles", help="render the final 'Claude cycles:' report from a JSON request with 'state'"
    )
    render_cycles.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "check-remediation":
        remediation, state = _request_from_json(json.loads(args.request.read_text(encoding="utf-8")))
        decision = evaluate_remediation(remediation, state)
        print(json.dumps({"allowed": decision.allowed, "reasons": list(decision.reasons)}))
        return 0 if decision.allowed else 1
    if args.command == "select-model":
        request, state = _model_request_from_json(json.loads(args.request.read_text(encoding="utf-8")))
        selection = select_model(state, request)
        print(json.dumps({"model": selection.model, "reasons": list(selection.reasons)}))
        return 0
    if args.command == "record-cycle":
        data = json.loads(args.request.read_text(encoding="utf-8"))
        updated = record_cycle_model(_state_from_json(data), _decision_from_json(data))
        print(json.dumps({"cycle_records": [_cycle_record_to_json(record) for record in updated.cycle_records]}))
        return 0
    if args.command == "render-cycles":
        state = _state_from_json(json.loads(args.request.read_text(encoding="utf-8")))
        print(render_cycles_report(state))
        return 0
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
