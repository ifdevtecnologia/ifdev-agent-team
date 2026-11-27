"""Content tests for the process-metrics instrument (issue #77, decision record 0006).

The skill's v1 reference is the single schema source: its examples must follow the rules it states,
its record table must match the checker below, and the marker, the mode lines and the always-loaded
sentences must stay identical wherever they appear. Only repository files are read; nothing is installed.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = "core/skills/process-metrics/SKILL.md"
REFERENCE = "core/skills/process-metrics/references/records-v1.md"
DOC = "docs/process-metrics.md"
MARKER = "<!-- process-metrics v1 -->"
MODES = ("process-metrics: on", "process-metrics: local", "process-metrics: off")
LOCAL_NEVER_POSTS = "nothing is posted on the issue"
FIRST_LINE = re.compile(r"^issue [a-z0-9-]{1,32}#[1-9][0-9]* phase=(estimate|execute)$")
INSTRUCTIONS_SENTENCE = (
    "**Process metrics.** On a task tracked by an issue, follow the `process-metrics` skill, which records role runs "
    "and board moves; the project's instructions choose its mode with `process-metrics: on`, `local` or `off`, and "
    "without that line the skill asks the user first. If the skill is not installed, say so in the report and "
    "continue without it."
)
GIT_WORKFLOW_BULLET = (
    "- When the `process-metrics` mode is `on`, the delivery authorization also covers posting its records as new "
    "comments in the skill's exact layout, marked `<!-- process-metrics v1 -->`, on the task's issue only, never "
    "editing or deleting earlier ones; without that authorization, propose them with the delivery."
)

ROLES = {path.stem for path in (REPO_ROOT / "core/agents").glob("*.md")} | {"main", "other"}
STATUSES = {
    "backlog",
    "to_do",
    "doing",
    "review",
    "to_tests",
    "testing",
    "to_homologation",
    "homologation",
    "to_deploy",
    "done",
    "canceled",
}
REVIEWERS = {
    "reviewer",
    "ux-reviewer",
    "security-reviewer",
    "api-reviewer",
    "privacy-reviewer",
    "db-reviewer",
    "performance-reviewer",
}
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FINDING_ID = re.compile(r"^F[1-9][0-9]*$")
# Required and optional keys per record type, besides `rec`; checked against the reference's table.
SCHEMA = {
    "block": ({"kind"}, {"cycle"}),
    "invocation": (
        {"role", "cycle", "verdict", "checks_run", "checks_blocked", "dev_by_main"},
        {"source", "duration_min"},
    ),
    "transition": ({"status", "at"}, set()),
    "estimate": ({"role", "estimate_min", "estimate_usd_eq", "estimate_tokens"}, set()),
    "finding": ({"role", "cycle", "id"}, set()),
    "resolution": ({"id"}, set()),
}
# (integer only, minimum, inclusive maximum); the minimum is exclusive when it is 0 for a decimal field.
NUMBERS = {
    "cycle": (True, 1, 1000),
    "checks_run": (True, 0, 1000),
    "checks_blocked": (True, 0, 1000),
    "estimate_tokens": (True, 1, 100_000_000),
    "estimate_min": (False, 0, 10080),
    "duration_min": (False, 0, 10080),
    "estimate_usd_eq": (False, 0, 10000),
}
FENCE = re.compile(r"^```(?P<info>[^\n`]*)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
TABLE_ROW = re.compile(r"^\| `(?P<rec>[a-z]+)` \| (?P<required>[^|]*) \| (?P<optional>[^|]*) \|", re.MULTILINE)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def fenced(text: str, *infos: str) -> list[str]:
    """Non-empty lines of the fenced blocks whose info string is one of infos."""
    return [
        line
        for block in FENCE.finditer(text)
        if block.group("info").strip() in infos
        for line in block.group("body").splitlines()
        if line.strip()
    ]


class DuplicateKeyError(ValueError):
    pass


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise DuplicateKeyError("duplicate key")

    return dict(pairs)


def reject_constant(name: str) -> None:
    raise ValueError(f"non-standard number {name}")


def parse_line(line: str) -> tuple[dict | None, list[str]]:
    """A record and its violations of the v1 rules, as a careful reader would parse one line."""
    if "\\" in line:
        return None, ["backslash in line"]

    try:
        record = json.loads(line, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_constant)
    except ValueError as error:
        return None, [f"not a JSON object without duplicate keys: {error}"]

    if not isinstance(record, dict):
        return None, ["not a JSON object"]

    return record, record_errors(record)


def verdicts(role: str) -> set[str]:
    if role == "qa":
        return {"ready", "ready_with_risks", "not_ready"}

    if role in {"dev", "devops", "tech-writer"}:
        return {"complete", "partial", "blocked"}

    if role in REVIEWERS:
        return {"findings", "no_findings"}

    return {"none"}


def number_error(key: str, value: object) -> str | None:
    integer, minimum, maximum = NUMBERS[key]
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)):
        return f"{key} must be {'an integer' if integer else 'a number'}"

    too_low = value < minimum if integer or minimum else value <= 0
    if too_low or value > maximum:
        return f"{key} out of bounds"

    return None


def record_errors(record: dict) -> list[str]:
    """Violations of the v1 rules stated in the reference, for one parsed record."""
    rec = record.get("rec")
    if rec not in SCHEMA:
        return [f"unknown rec {rec!r}"]

    required, optional = SCHEMA[rec]
    keys = set(record) - {"rec"}
    errors = [f"missing {key}" for key in sorted(required - keys)]
    errors += [f"unknown key {key}" for key in sorted(keys - required - optional)]
    errors += value_errors(record)
    if rec == "block":
        errors += block_errors(record)
    elif rec == "invocation":
        errors += invocation_errors(record)

    return errors


def value_errors(record: dict) -> list[str]:
    """Rules shared by every record type: flat values, vocabularies, IDs, numbers and timestamps."""
    errors = []
    for key, value in record.items():
        if value is None or isinstance(value, (dict, list)):
            errors.append(f"{key} must be a flat, non-null value")
        elif isinstance(value, str) and (" " in value or "/" in value):
            errors.append(f"{key} holds free text or a path")

    errors += [error for key in NUMBERS if key in record for error in [number_error(key, record[key])] if error]
    timestamps = [key for key in ("at",) if key in record]
    errors += [f"{key} is not a UTC timestamp" for key in timestamps if not TIMESTAMP.match(str(record[key]))]
    checks = (
        ("role", lambda value: value in ROLES),
        ("status", lambda value: value in STATUSES),
        ("kind", lambda value: value in {"planning", "cycle"}),
        ("source", lambda value: value == "declared"),
        ("id", lambda value: bool(FINDING_ID.match(str(value)))),
    )
    errors += [f"invalid {key} {record[key]!r}" for key, valid in checks if key in record and not valid(record[key])]

    return errors


def block_errors(record: dict) -> list[str]:
    if record.get("kind") == "cycle" and "cycle" not in record:
        return ["a cycle block needs its cycle"]

    if record.get("kind") == "planning" and "cycle" in record:
        return ["a planning block has no cycle"]

    return []


def invocation_errors(record: dict) -> list[str]:
    errors = []
    if record.get("verdict") not in verdicts(record.get("role", "")):
        errors.append(f"verdict {record.get('verdict')!r} is not valid for role {record.get('role')!r}")

    if not isinstance(record.get("dev_by_main"), bool):
        errors.append("dev_by_main must be a boolean")

    declared = {"source", "duration_min"} & set(record)
    if record.get("dev_by_main") is not True:
        return errors + (["source and duration_min apply only when dev_by_main is true"] if declared else [])

    if record.get("role") != "dev":
        errors.append("dev_by_main is true only with role dev")

    if declared != {"source", "duration_min"}:
        errors.append("dev_by_main true needs source=declared and duration_min")

    return errors


def block_layout_errors(lines: list[str]) -> list[str]:
    """A published block starts with its header, and every invocation carries the header's cycle."""
    records = [parse_line(line)[0] or {} for line in lines]
    if not records or records[0].get("rec") != "block":
        return ["the first record is not a block header"]

    header = records[0]
    errors = ["a second header inside the block" for record in records[1:] if record.get("rec") == "block"]
    errors += [
        "invocation cycle differs from the header"
        for record in records[1:]
        if record.get("rec") == "invocation" and record.get("cycle") != header.get("cycle")
    ]

    return errors


def reader_order(sources: list[tuple[str, str, int, list[str]]]) -> list[list[str]]:
    """(source, created_at, comment id or file position, lines) -> blocks in reader precedence: comments
    before local blocks, comments by creation time then comment ID, local blocks in file order."""
    comments = sorted((entry for entry in sources if entry[0] == "comment"), key=lambda entry: (entry[1], entry[2]))
    local = sorted((entry for entry in sources if entry[0] == "local"), key=lambda entry: entry[2])

    return [entry[3] for entry in comments + local]


def local_file_blocks(text: str) -> list[list[str]]:
    """Blocks of a local file: each starts at a `block` header; blank lines are ignored."""
    blocks: list[list[str]] = []
    for line in filter(str.strip, text.splitlines()):
        if json.loads(line)["rec"] == "block":
            blocks.append([])

        blocks[-1].append(line)

    return blocks


def reconstruct(blocks: list[list[str]]) -> tuple[dict, int, int]:
    """Blocks in reader precedence -> ({(kind, cycle): records}, duplicate blocks, invalid repeated findings)."""
    kept: dict[tuple[str, int | None], list[dict]] = {}
    duplicates = 0
    invalid_findings = 0
    finding_ids: set[str] = set()
    for lines in blocks:
        header, *records = [json.loads(line) for line in lines]
        key = (header["kind"], header.get("cycle"))
        if key in kept:
            duplicates += 1
            continue

        kept[key] = []
        for record in records:
            if record["rec"] == "finding" and record["id"] in finding_ids:
                invalid_findings += 1
                continue

            finding_ids.update([record["id"]] if record["rec"] == "finding" else [])
            kept[key].append(record)

    return kept, duplicates, invalid_findings


class ReferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = read(REFERENCE)
        self.lines = fenced(self.reference, "jsonl")

    def test_every_example_is_a_valid_v1_record(self) -> None:
        self.assertTrue(self.lines)
        for line in self.lines:
            with self.subTest(line=line):
                self.assertEqual(parse_line(line)[1], [])

    def test_examples_cover_every_record_type_and_both_invocation_forms(self) -> None:
        records = [json.loads(line) for line in self.lines]
        self.assertEqual({record["rec"] for record in records}, set(SCHEMA))
        forms = {record["dev_by_main"] for record in records if record["rec"] == "invocation"}
        self.assertEqual(forms, {True, False})
        self.assertEqual({record["kind"] for record in records if record["rec"] == "block"}, {"planning", "cycle"})

    def test_record_table_matches_the_checker(self) -> None:
        names = re.compile(r"`([a-z_]+)`")
        table = {
            row["rec"]: (set(names.findall(row["required"])), set(names.findall(row["optional"])))
            for row in TABLE_ROW.finditer(self.reference)
            if row["rec"] != "rec"
        }
        self.assertEqual(table, SCHEMA)

    def test_reference_lists_every_vocabulary_value_and_bound(self) -> None:
        for value in sorted(ROLES | STATUSES | {"planning", "declared"}):
            self.assertIn(f"`{value}`", self.reference)
        for bound in ("1000", "10080", "10000", "100000000"):
            self.assertIn(bound, self.reference)

    def test_first_line_examples_match_the_grammar(self) -> None:
        examples = [line for line in fenced(self.reference, "text") if line.startswith("issue ")]
        self.assertEqual(len(examples), 2)
        for line in examples:
            self.assertRegex(line, FIRST_LINE)
        self.assertIn(FIRST_LINE.pattern, self.reference)

    def test_never_collected_allows_only_the_declared_duration(self) -> None:
        section = self.reference.split("## Never collected", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`duration_min` with `source` `declared`", section)
        self.assertNotIn("people", section)
        self.assertNotIn("started_at", self.reference)


class LocalFileExampleTest(unittest.TestCase):
    def test_local_file_example_parses_into_valid_blocks(self) -> None:
        reference = read(REFERENCE)
        section = reference.split("A synthetic local file with two blocks:", 1)[1]
        text = FENCE.search(section).group("body")
        self.assertNotIn(MARKER, text)
        self.assertNotIn("```", text)
        for line in filter(str.strip, text.splitlines()):
            self.assertEqual(parse_line(line)[1], [], line)

        blocks = local_file_blocks(text)
        for lines in blocks:
            self.assertEqual(block_layout_errors(lines), [])
        kept, duplicates, invalid = reconstruct(blocks)
        self.assertEqual(list(kept), [("cycle", 1), ("cycle", 2)])
        self.assertEqual((duplicates, invalid), (0, 0))


class SkillExamplesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL)

    def test_role_block_example_is_valid(self) -> None:
        lines = fenced(self.skill, "process-metrics v1")
        self.assertEqual(len(lines), 1)
        self.assertEqual(parse_line(lines[0])[1], [])

    def test_comment_example_has_the_marker_and_a_header_first(self) -> None:
        comment = re.search(r"<!-- process-metrics v1 -->\n\s*```jsonl\n(?P<body>.*?)\n\s*```", self.skill, re.DOTALL)
        self.assertIsNotNone(comment)
        lines = [line.strip() for line in comment.group("body").splitlines()]
        for line in lines:
            self.assertEqual(parse_line(line)[1], [], line)
        self.assertEqual(block_layout_errors(lines), [])


class RecordRulesTest(unittest.TestCase):
    """The checker rejects what the reference forbids, so passing examples mean something."""

    VALID = {
        "rec": "invocation",
        "role": "qa",
        "cycle": 1,
        "verdict": "ready",
        "checks_run": 2,
        "checks_blocked": 0,
        "dev_by_main": False,
    }
    DEV_BY_MAIN = {**VALID, "role": "dev", "verdict": "complete", "dev_by_main": True, "source": "declared"}

    def test_valid_records_pass(self) -> None:
        self.assertEqual(record_errors(self.VALID), [])
        self.assertEqual(record_errors({**self.DEV_BY_MAIN, "duration_min": 12.5}), [])

    def test_rejects_invalid_invocations(self) -> None:
        cases = {
            "unknown type": {"rec": "note"},
            "unknown key": {"issue": "12"},
            "repository key": {"repo": "example-repo"},
            "null value": {"verdict": None},
            "nested value": {"cycle": {"n": 1}},
            "free text": {"verdict": "looks good"},
            "path": {"role": "core/agents"},
            "verdict of another role": {"verdict": "no_findings"},
            "cycle zero": {"cycle": 0},
            "cycle above the bound": {"cycle": 1001},
            "decimal cycle": {"cycle": 1.5},
            "integer written with a fraction": {"checks_run": 2.0},
            "negative count": {"checks_blocked": -1},
            "count above the bound": {"checks_run": 1001},
            "boolean as count": {"checks_run": True},
            "declared without dev_by_main": {"source": "declared"},
            "wall-clock start": {"started_at": "2026-01-05T14:10:00Z"},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(record_errors({**self.VALID, **changes}), [])
        missing = dict(self.VALID)
        del missing["dev_by_main"]
        self.assertNotEqual(record_errors(missing), [])

    def test_rejects_invalid_declared_dev_work(self) -> None:
        cases = {
            "missing duration": {},
            "zero duration": {"duration_min": 0},
            "duration above the bound": {"duration_min": 10081},
            "another role": {"duration_min": 5, "role": "qa", "verdict": "ready"},
            "another source": {"duration_min": 5, "source": "telemetry"},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(record_errors({**self.DEV_BY_MAIN, **changes}), [])

    def test_rejects_invalid_other_records(self) -> None:
        cases = (
            {"rec": "block", "kind": "cycle"},
            {"rec": "block", "kind": "planning", "cycle": 1},
            {"rec": "block", "kind": "review", "cycle": 1},
            {"rec": "estimate", "role": "dev", "estimate_min": 0, "estimate_usd_eq": 1, "estimate_tokens": 10},
            {"rec": "estimate", "role": "dev", "estimate_min": 5, "estimate_usd_eq": 1, "estimate_tokens": 1.5},
            {"rec": "estimate", "role": "dev", "estimate_min": 5, "estimate_usd_eq": 10001, "estimate_tokens": 10},
            {"rec": "estimate", "role": "dev", "estimate_min": 5, "estimate_usd_eq": 1, "estimate_tokens": 100000001},
            {"rec": "transition", "status": "in_progress", "at": "2026-01-05T14:10:00Z"},
            {"rec": "transition", "status": "doing", "at": "2026-01-05 14:10"},
            {"rec": "finding", "role": "reviewer", "cycle": 1, "id": "F0"},
            {"rec": "resolution", "id": "finding-1"},
        )
        for record in cases:
            with self.subTest(record=record):
                self.assertNotEqual(record_errors(record), [])

    def test_rejects_duplicate_keys_and_escapes(self) -> None:
        for line in (
            '{"rec":"transition","status":"doing","status":"done","at":"2026-01-05T14:10:00Z"}',
            '{"rec":"transition","status":"d\\u006fing","at":"2026-01-05T14:10:00Z"}',
            '["rec","transition"]',
            '{"rec":"invocation","role":"qa","cycle":NaN,"verdict":"ready","checks_run":1,"checks_blocked":0,"dev_by_main":false}',
            '{"rec":"estimate","role":"dev","estimate_min":Infinity,"estimate_usd_eq":1,"estimate_tokens":10}',
            "not json",
        ):
            with self.subTest(line=line):
                record, errors = parse_line(line)
                self.assertIsNone(record)
                self.assertNotEqual(errors, [])

    def test_first_line_grammar_rejects_malformed_lines(self) -> None:
        for line in (
            "issue Example_Repo#12 phase=execute",
            "issue example-repo#0 phase=execute",
            "issue example-repo#12 phase=review",
            "issue example-repo#12",
            f"issue {'a' * 33}#1 phase=execute",
        ):
            with self.subTest(line=line):
                self.assertNotRegex(line, FIRST_LINE)


class ReaderSequenceTest(unittest.TestCase):
    """A synthetic issue history: planning, cycles 1 and 2, then a duplicate cycle 1 block."""

    BLOCKS = [
        [
            '{"rec":"block","kind":"planning"}',
            '{"rec":"estimate","role":"dev","estimate_min":30,"estimate_usd_eq":1,"estimate_tokens":50000}',
        ],
        [
            '{"rec":"block","kind":"cycle","cycle":1}',
            '{"rec":"invocation","role":"reviewer","cycle":1,"verdict":"findings","checks_run":2,"checks_blocked":0,"dev_by_main":false}',
            '{"rec":"finding","role":"reviewer","cycle":1,"id":"F1"}',
            '{"rec":"transition","status":"doing","at":"2026-01-05T15:00:00Z"}',
        ],
        [
            '{"rec":"block","kind":"cycle","cycle":2}',
            '{"rec":"invocation","role":"qa","cycle":2,"verdict":"ready","checks_run":5,"checks_blocked":0,"dev_by_main":false}',
            '{"rec":"finding","role":"qa","cycle":2,"id":"F1"}',
            '{"rec":"transition","status":"to_homologation","at":"2026-01-05T17:00:00Z"}',
        ],
        [
            '{"rec":"block","kind":"cycle","cycle":1}',
            '{"rec":"invocation","role":"dev","cycle":1,"verdict":"complete","checks_run":3,"checks_blocked":0,"dev_by_main":false}',
        ],
    ]

    def test_every_block_is_valid(self) -> None:
        for lines in self.BLOCKS:
            self.assertEqual(block_layout_errors(lines), [])
            for line in lines:
                self.assertEqual(parse_line(line)[1], [], line)

    def test_cycles_come_from_headers_and_the_first_duplicate_wins(self) -> None:
        kept, duplicates, invalid_findings = reconstruct(self.BLOCKS)

        self.assertEqual(list(kept), [("planning", None), ("cycle", 1), ("cycle", 2)])
        self.assertEqual(duplicates, 1)
        self.assertEqual([r["role"] for r in kept[("cycle", 1)] if r["rec"] == "invocation"], ["reviewer"])
        self.assertEqual(invalid_findings, 1)
        self.assertEqual([r for r in kept[("cycle", 2)] if r["rec"] == "finding"], [])

    def test_a_comment_block_wins_over_a_local_block_and_ties_break_by_comment_id(self) -> None:
        local_cycle_1 = ['{"rec":"block","kind":"cycle","cycle":1}', self.BLOCKS[3][1]]
        sources = [
            ("local", "", 0, local_cycle_1),
            ("comment", "2026-01-05T15:01:00Z", 20, self.BLOCKS[2]),
            ("comment", "2026-01-05T15:01:00Z", 10, self.BLOCKS[1]),
            ("comment", "2026-01-05T15:01:00Z", 5, self.BLOCKS[3]),
        ]

        kept, duplicates, _ = reconstruct(reader_order(sources))

        self.assertEqual(sorted(kept), [("cycle", 1), ("cycle", 2)])
        self.assertEqual(duplicates, 2)
        self.assertEqual([r["role"] for r in kept[("cycle", 1)] if r["rec"] == "invocation"], ["dev"])

    def test_layout_rejects_a_missing_header_and_a_mismatched_cycle(self) -> None:
        body = self.BLOCKS[1]
        self.assertNotEqual(block_layout_errors(body[1:]), [])
        self.assertNotEqual(block_layout_errors(['{"rec":"block","kind":"cycle","cycle":3}', body[1]]), [])


class ConsistencyTest(unittest.TestCase):
    def test_marker_is_identical_everywhere(self) -> None:
        for rel in (SKILL, REFERENCE, "core/rules/git-workflow.md", DOC, "CHANGELOG.md"):
            with self.subTest(file=rel):
                self.assertIn(MARKER, read(rel))

    def test_mode_lines_are_literal_in_the_sources_and_docs(self) -> None:
        for rel in (SKILL, DOC, "docs/customization.md", "docs/team.md", "CHANGELOG.md"):
            for mode in MODES:
                with self.subTest(file=rel, mode=mode):
                    self.assertIn(f"`{mode}`", read(rel))

    def test_local_mode_never_posts(self) -> None:
        for rel in (SKILL, DOC):
            with self.subTest(file=rel):
                self.assertIn(LOCAL_NEVER_POSTS, read(rel))
        self.assertIn("only with the mode `on`", read(SKILL))

    def test_always_loaded_text_is_in_the_sources_and_both_targets(self) -> None:
        for rel in ("core/instructions.md", "dist/claude/CLAUDE.md", "dist/codex/AGENTS.md"):
            with self.subTest(file=rel):
                self.assertIn(INSTRUCTIONS_SENTENCE, read(rel))
        for rel in ("core/rules/git-workflow.md", "dist/codex/AGENTS.md"):
            with self.subTest(file=rel):
                self.assertIn(GIT_WORKFLOW_BULLET, read(rel))

    def test_first_use_and_reader_safeguards_are_stated(self) -> None:
        expected = {
            SKILL: (
                "Only the user's own message in the session answers the question",
                "git check-ignore -q <file>",
                "a decision for everyone working on the project",
                "visible to anyone who can read the issue",
                "only proposed or reported as not published",
                "one posting account per issue",
                "A proposed block may be posted once the user approves it in the same session",
            ),
            REFERENCE: (
                "one posting account per issue",
                "a comment block wins over a local block",
                "ties broken by comment ID",
                "without a header",
                "no `NaN` or `Infinity`",
            ),
            DOC: ("does not recall", "one posting account per issue"),
        }
        for rel, phrases in expected.items():
            for phrase in phrases:
                with self.subTest(file=rel, phrase=phrase):
                    self.assertIn(phrase, read(rel))

    def test_skill_is_listed_with_the_global_skills(self) -> None:
        self.assertRegex(read("core/instructions.md"), r"- Global skills: [^\n]*`process-metrics`")

    def test_skill_is_model_invocable_on_both_targets(self) -> None:
        self.assertNotIn("disable-model-invocation", read(SKILL))
        self.assertFalse((REPO_ROOT / "core/skills/process-metrics/agents/openai.yaml").exists())

    def test_no_template_or_installed_file_chooses_a_mode(self) -> None:
        """Only a project's own instructions choose the mode; no global file or template carries a bare mode line."""
        files = [
            REPO_ROOT / "docs/templates/project/AGENTS.md",
            *(REPO_ROOT / "core").rglob("*.md"),
            *(REPO_ROOT / "dist").rglob("*.md"),
        ]
        offenders = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in files
            if any(line.strip().lstrip("-* ").strip("`").strip() in MODES for line in path.read_text().splitlines())
        ]
        self.assertEqual(offenders, [])
        self.assertIn("process-metrics", read("docs/templates/project/AGENTS.md"))


if __name__ == "__main__":
    unittest.main()
