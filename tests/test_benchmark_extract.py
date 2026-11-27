"""Tests for the stream-json extraction of the benchmark harness (benchmark/bench/extract.py)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from benchmark_support import BENCH_DIR

sys.path.insert(0, str(BENCH_DIR))

from bench.extract import parse_stream  # noqa: E402

FREE_TEXT = "FREE-TEXT-MUST-NOT-LEAK"


def tool_use(name: str, subagent_type: object) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "text", "text": FREE_TEXT},
        {"type": "tool_use", "name": name, "input": {"subagent_type": subagent_type, "prompt": FREE_TEXT}},
    ]}}


RESULT = {
    "type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 1.25, "duration_ms": 900,
    "num_turns": 12, "result": f"{FREE_TEXT}\nStatus: COMPLETE",
    "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000, "service_tier": FREE_TEXT},
    "modelUsage": {"claude-test-1": {"inputTokens": 100, "costUSD": 1.25, "label": FREE_TEXT}},
}


class ParseStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))

    def write(self, *lines: object) -> Path:
        path = self.dir / "stream.jsonl"
        path.write_text("\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n")

        return path

    def test_result_numbers_and_subagent_counts(self) -> None:
        path = self.write(tool_use("Agent", "reviewer"), tool_use("Agent", "qa"), tool_use("Agent", "reviewer"),
                          tool_use("Bash", None), RESULT)

        summary = parse_stream(path)

        self.assertTrue(summary["has_result"])
        self.assertEqual(summary["result_subtype"], "success")
        self.assertEqual((summary["total_cost_usd"], summary["duration_ms"], summary["num_turns"]), (1.25, 900, 12))
        self.assertEqual(summary["usage"], {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000})
        self.assertEqual(summary["model_usage"], {"claude-test-1": {"inputTokens": 100, "costUSD": 1.25}})
        self.assertEqual(summary["subagent_calls"], {"qa": 1, "reviewer": 2})
        self.assertFalse(summary["budget_capped"])
        self.assertFalse(summary["ended_blocked"])

    def test_subagent_tool_name_is_configurable(self) -> None:
        path = self.write(tool_use("Task", "reviewer"), tool_use("Agent", "qa"), RESULT)

        self.assertEqual(parse_stream(path, "Task")["subagent_calls"], {"reviewer": 1})
        self.assertEqual(parse_stream(path)["subagent_calls"], {"qa": 1})

    def test_free_text_never_reaches_the_summary(self) -> None:
        path = self.write(tool_use("Agent", FREE_TEXT), {**RESULT, "subtype": FREE_TEXT})

        summary = parse_stream(path)

        self.assertNotIn(FREE_TEXT, json.dumps(summary))
        self.assertEqual(summary["subagent_calls"], {"other": 1})
        self.assertEqual(summary["result_subtype"], "other")

    def test_malformed_truncated_and_missing_streams_do_not_raise(self) -> None:
        truncated = self.write(tool_use("Agent", "dev"), "not json", "[1, 2]", json.dumps(RESULT)[:40])

        summary = parse_stream(truncated)

        self.assertFalse(summary["has_result"])
        self.assertEqual(summary["malformed_lines"], 3)
        self.assertEqual(summary["subagent_calls"], {"dev": 1})
        self.assertFalse(parse_stream(self.dir / "missing.jsonl")["has_result"])

    def test_budget_cap_blocked_end_and_non_numeric_values(self) -> None:
        capped = {**RESULT, "subtype": "error_max_budget_usd", "total_cost_usd": "1.0", "num_turns": True,
                  "result": "Report\nStatus: BLOCKED (needs an answer)"}

        summary = parse_stream(self.write(capped))

        self.assertTrue(summary["budget_capped"])
        self.assertTrue(summary["ended_blocked"])
        self.assertIsNone(summary["total_cost_usd"])
        self.assertIsNone(summary["num_turns"])


if __name__ == "__main__":
    unittest.main()
