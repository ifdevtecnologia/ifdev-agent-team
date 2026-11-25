"""Numbers from a run's `--output-format stream-json` log (ADR 0001, "Metrics").

Only numbers and categories leave this module: free text in the stream (prompts, code, reports)
is never copied into the result. The stream format is an assumption checked in the smoke round:
the final `result` event carries `total_cost_usd`, `usage`, `modelUsage`, `duration_ms` and
`num_turns`, and subagent calls are `tool_use` blocks of the subagent tool with a `subagent_type`.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

# Assumed name of the subagent tool (formerly `Task`); check in the smoke round.
DEFAULT_SUBAGENT_TOOL = "Agent"
SUBAGENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Names kept from the stream; anything else is dropped. Usage keys may be camelCase, result subtypes are snake_case.
USAGE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RESULT_SUBTYPE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
OTHER_SUBAGENT = "other"
# Assumed: a result subtype that mentions the budget means `--max-budget-usd` ended the run.
BUDGET_MARKER = "budget"
BLOCKED_MARKER = "BLOCKED"


def _number(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if isinstance(value, float) and not math.isfinite(value):
        return None

    return value


def _numbers(mapping: object) -> dict[str, float | int]:
    if not isinstance(mapping, dict):
        return {}

    return {str(k): n for k, v in mapping.items() if (n := _number(v)) is not None and USAGE_KEY.match(str(k))}


def _model_usage(mapping: object) -> dict[str, dict[str, float | int]]:
    if not isinstance(mapping, dict):
        return {}

    return {str(model): _numbers(usage) for model, usage in mapping.items() if re.match(r"^[A-Za-z0-9._:\[\]-]{1,128}$", str(model))}


def _subagent_types(event: dict, tool: str) -> list[str]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None

    if event.get("type") != "assistant" or not isinstance(content, list):
        return []

    found = []

    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool:
            tool_input = block.get("input")
            kind = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
            found.append(kind if isinstance(kind, str) and SUBAGENT_TYPE.match(kind) else OTHER_SUBAGENT)

    return found


def _ends_blocked(text: object) -> bool:
    if not isinstance(text, str):
        return False

    lines = [line for line in text.strip().splitlines() if line.strip()]

    return bool(lines) and BLOCKED_MARKER in lines[-1]


def parse_stream(path: Path, subagent_tool: str = DEFAULT_SUBAGENT_TOOL) -> dict[str, object]:
    """Parses the stream line by line; malformed lines are counted and skipped, a missing file or
    a missing `result` event is reported in the returned fields, never raised."""
    summary: dict[str, object] = {
        "has_result": False,
        "result_subtype": None,
        "is_error": None,
        "budget_capped": False,
        "ended_blocked": False,
        "total_cost_usd": None,
        "duration_ms": None,
        "num_turns": None,
        "usage": {},
        "model_usage": {},
        "subagent_calls": {},
        "malformed_lines": 0,
    }
    calls: Counter[str] = Counter()

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []

    for line in lines:
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            summary["malformed_lines"] += 1
            continue

        if not isinstance(event, dict):
            summary["malformed_lines"] += 1
            continue

        calls.update(_subagent_types(event, subagent_tool))

        if event.get("type") == "result":
            subtype = event.get("subtype")
            subtype = subtype if isinstance(subtype, str) and RESULT_SUBTYPE.match(subtype) else OTHER_SUBAGENT
            summary.update(
                has_result=True,
                result_subtype=subtype,
                is_error=event.get("is_error") if isinstance(event.get("is_error"), bool) else None,
                budget_capped=BUDGET_MARKER in subtype,
                ended_blocked=_ends_blocked(event.get("result")),
                total_cost_usd=_number(event.get("total_cost_usd")),
                duration_ms=_number(event.get("duration_ms")),
                num_turns=_number(event.get("num_turns")),
                usage=_numbers(event.get("usage")),
                model_usage=_model_usage(event.get("modelUsage")),
            )

    summary["subagent_calls"] = dict(sorted(calls.items()))

    return summary
