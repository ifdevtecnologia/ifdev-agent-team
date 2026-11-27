"""Tests for the task.yaml schema and loader of the benchmark harness (benchmark/bench/taskspec.py)."""

from __future__ import annotations

import sys
import unittest

from benchmark_support import BENCH_DIR, TASK_YAML, BenchTestCase, make_task

sys.path.insert(0, str(BENCH_DIR))

from bench.taskspec import TaskError, load_task, parse_subset  # noqa: E402

try:
    import yaml
except ImportError:  # PyYAML is a development dependency only
    yaml = None


def replace_line(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


class LoadTaskTests(BenchTestCase):
    def load(self, yaml_text: str):
        self.count = getattr(self, "count", 0) + 1
        task = make_task(self.tasks_dir, f"T{self.count:02d}", yaml_text)

        return load_task(task)

    def assert_rejected(self, yaml_text: str, field: str) -> None:
        with self.assertRaises(TaskError) as caught:
            self.load(yaml_text)

        self.assertIn(field, str(caught.exception))

    def test_valid_task_loads_every_field(self) -> None:
        task = self.load(TASK_YAML.format(task_id="T01"))

        self.assertEqual((task.id, task.type, task.stack, task.image), ("T01", "bugfix", "shell", "bench-shell:test"))
        self.assertEqual(task.test_command, "sh tests/check.sh")
        self.assertIsNone(task.build_command)
        self.assertEqual(task.hidden_command_for("bench_hidden/test_fixed.sh"), "sh bench_hidden/test_fixed.sh")
        self.assertEqual(task.expected_files, ("app.txt",))
        self.assertEqual(task.protected_paths, ("tests",))
        self.assertEqual(task.criteria, ("T01-C1", "T01-C2"))
        self.assertEqual(task.hidden_tests, {"test_fixed.sh": "T01-C1", "test_note.sh": "T01-C2"})

    def test_each_missing_required_field_is_named(self) -> None:
        base = TASK_YAML.format(task_id="T01")

        for line, field in (("stack: shell\n", "stack"), ("image: bench-shell:test\n", "image"), ("id: T01\n", "id")):
            with self.subTest(field=field):
                self.assert_rejected(replace_line(base, line, ""), f"'{field}'")

        self.assert_rejected(replace_line(base, "  test: sh tests/check.sh\n", ""), "commands.test")

    def test_wrong_type_unknown_key_and_bad_values_are_named(self) -> None:
        base = TASK_YAML.format(task_id="T01")
        cases = {
            "criteria": replace_line(base, "criteria:\n  - T01-C1\n  - T01-C2\n", "criteria: T01-C1\n"),
            "unknown key 'owner'": base + "owner: someone\n",
            "commands.deploy": replace_line(base, "  hidden: sh {test}\n", "  hidden: sh {test}\n  deploy: x\n"),
            "'type'": replace_line(base, "type: bugfix", "type: chore"),
            "commands.hidden": replace_line(base, "hidden: sh {test}", "hidden: sh run-all.sh"),
            "unknown criterion": replace_line(base, "test_note.sh: T01-C2", "test_note.sh: T01-C9"),
        }

        for field, text in cases.items():
            with self.subTest(field=field):
                self.assert_rejected(text, field)

    def test_absolute_and_parent_paths_are_rejected(self) -> None:
        base = TASK_YAML.format(task_id="T01")

        for value in ("/etc/passwd", "../outside", "tests/../../x"):
            with self.subTest(value=value):
                self.assert_rejected(replace_line(base, "  - tests\n", f"  - '{value}'\n"), "protected_paths")
                self.assert_rejected(replace_line(base, "  - app.txt\n", f"  - '{value}'\n"), "expected_files")

    def test_missing_task_file_is_reported(self) -> None:
        (self.tasks_dir / "T02").mkdir()

        with self.assertRaises(TaskError):
            load_task(self.tasks_dir / "T02")


class ParseSubsetTests(unittest.TestCase):
    def test_unsupported_yaml_is_rejected_with_the_line(self) -> None:
        cases = {
            "tabs": "id: T01\ncommands:\n\t test: x\n",
            "flow": "criteria: [a, b]\n",
            "duplicate": "id: a\nid: b\n",
            "nested": "commands:\n  test:\n    deep: x\n",
            "orphan indentation": "  - item\n",
            "mixed": "x:\n  - a\n  k: v\n",
            "escape": 'id: "a\\nb"\n',
        }

        for name, text in cases.items():
            with self.subTest(name=name), self.assertRaises(TaskError) as caught:
                parse_subset(text)

            self.assertRegex(str(caught.exception), r"task\.yaml:\d+")

    def test_values_yaml_would_not_read_as_plain_strings_are_rejected(self) -> None:
        for value in ("echo a: b", "1", "2.5", "yes", "No", "null", "~", "on", "0x1f", "0b101", "2026-11-25", "'a' 'b'", "[a]",
                      "&anchor", "|"):
            with self.subTest(value=value), self.assertRaises(TaskError):
                parse_subset(f"a: {value}\n")

        with self.assertRaises(TaskError):
            parse_subset("a:\n  - # only a comment\n")

    def test_quotes_and_comments(self) -> None:
        data = parse_subset("a: 'it''s'  \nb: \"x # y\"\nc: plain # comment\nd:\n  - '#keep'\n")

        self.assertEqual(data, {"a": "it's", "b": "x # y", "c": "plain", "d": ["#keep"]})

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_subset_matches_pyyaml(self) -> None:
        text = TASK_YAML.format(task_id="T01") + (
            "extra: 'quoted: value'\nempty:\nnum: '1'\nword: 'yes'\ncomment: 'x' # c\n"
            "dash: -x\nbraces: sh {test} --x\nnested:\n  'k: 1': \"v # w\"\n"
        )

        self.assertEqual(parse_subset(text), yaml.safe_load(text))


if __name__ == "__main__":
    unittest.main()
