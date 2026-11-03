"""Tests for scripts/build_targets.py.

Generator behavior is tested on copies of the sources in temporary directories; the golden tests
compare the committed Claude output (files, agent frontmatter, adapter, footer and the exact body
wording) with the installed files of the last commit before the neutral layout (decision record
0004), read from Git rather than from hard-coded copies.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_targets  # noqa: E402

PRE_0004_COMMIT = "e79c8ae9e4c284cfa9a2ca6a501591299584fae7"
SCRIPT = REPO_ROOT / "scripts/build_targets.py"


def git_show(commit: str, rel: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{commit}:{rel}"], capture_output=True)
    return result.stdout if result.returncode == 0 else None


def frontmatter(text: str) -> str:
    """The frontmatter block of a Markdown definition, delimiters included."""
    lines = text.split("\n")
    return "\n".join(lines[: lines.index("---", 1) + 1])


def split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter with its delimiters, body); a text without frontmatter is all body."""
    if not text.startswith("---\n"):
        return "", text
    head = frontmatter(text)
    return head, text[len(head) :]


# Slice B of decision record 0004 rewords the neutral text and changes nothing else. Applied in
# order to the bodies of the pre-0004 files, these edits must give the generated bodies exactly:
# any other wording change, deletion or insertion fails the golden test.
NEUTRAL_WORDING = (
    ("Blocked Write/Edit tools do not prevent writing through Bash;", "Even where file-edit tools are blocked, the shell can still write;"),
    (r"\bBash\b", "the shell"),
    ("the project's `CLAUDE.md`, `AGENTS.md`", "the project's instructions"),
    ("read its `~/.claude/skills/<name>/SKILL.md`", "use that skill"),
    (r"`~/\.claude/rules/([\w-]+)\.md`", r"the `\1` rule"),
    (r"`~/\.claude/skills/([\w-]+)/SKILL\.md`", r"the `\1` skill"),
)
FRESH_CONTEXT_FALLBACK = (
    "the tool cannot start a role in a fresh context, run each role in a new session with a written"
    " hand-off (objective, acceptance criteria, scope and the previous role's report); when that is not"
    ' possible either, state "independent review not performed" for that role and report PARTIAL. When '
)
# One-off sentences, applied before NEUTRAL_WORDING; each must occur exactly once in its file.
ONE_OFF_WORDING = {
    "CLAUDE.md": (
        (
            "Project instructions (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/`, `.claude/skills/`, `.claude/agents/`)",
            "The **project's instructions** are its `AGENTS.md` plus the coding tool's own project files (for"
            " example `CLAUDE.md` and `.claude/` with its rules, skills and agents, or `.codex/`); they",
        ),
        ("Project-specific reviewers (`.claude/agents/`),", "Project-specific reviewers defined in the project's instructions,"),
        ("agents read a skill at `~/.claude/skills/<name>/SKILL.md`", "agents use a skill by name"),
        ("never skip a role silently. When ", "never skip a role silently. When " + FRESH_CONTEXT_FALLBACK),
    ),
    "agents/db-reviewer.md": (
        (
            "read `~/.claude/skills/supabase-dev/SKILL.md` or `~/.claude/skills/java-spring-dev/SKILL.md`",
            "use the `supabase-dev` or `java-spring-dev` skill",
        ),
    ),
    "agents/security-reviewer.md": (
        (
            "If the project provides a specialized security or access reviewer in `.claude/agents/`,",
            "If the project's instructions provide a specialized security or access reviewer,",
        ),
    ),
}


class GoldenTest(unittest.TestCase):
    """The generated Claude files (CLAUDE.md and the agents) are unchanged across decision record
    0004 except for the planned edits (rules and skills are linked from core/ and not compared here):
    slice A moved the files unchanged, and slice B only prepends the adapter, appends the agent
    footer and applies the wording edits listed above, so the files, the agent frontmatter (tool
    lists included), the adapter and footer positions and the exact bodies are compared."""

    def setUp(self) -> None:
        listing = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-tree", "--name-only", PRE_0004_COMMIT, "claude/agents/"],
            capture_output=True,
            text=True,
        )
        if listing.returncode != 0:
            # Shallow clones (the CI checkout) do not contain older commits.
            self.skipTest(f"commit {PRE_0004_COMMIT} is not available in this clone")
        self.old_agents = sorted(Path(rel).name for rel in listing.stdout.split())
        self.adapter = (REPO_ROOT / "targets/claude/adapter.md").read_text(encoding="utf-8")
        self.footer = (REPO_ROOT / "targets/claude/agent-footer.md").read_text(encoding="utf-8")

    def test_the_same_agents_are_generated(self) -> None:
        self.assertEqual(self.old_agents, sorted(p.name for p in (REPO_ROOT / "dist/claude/agents").iterdir()))

    def test_agent_frontmatter_including_tool_lists_is_unchanged(self) -> None:
        for name in self.old_agents:
            with self.subTest(agent=name):
                old = git_show(PRE_0004_COMMIT, f"claude/agents/{name}").decode("utf-8")
                new = (REPO_ROOT / f"dist/claude/agents/{name}").read_text(encoding="utf-8")
                self.assertEqual(frontmatter(new), frontmatter(old))

    def test_the_adapter_comes_first_and_every_agent_ends_with_the_footer(self) -> None:
        self.assertTrue(self.adapter.strip() and self.footer.strip(), "the Claude adapter and footer are empty")
        claude_md = (REPO_ROOT / "dist/claude/CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude_md.startswith(self.adapter.rstrip("\n") + "\n\n"))
        self.assertIn("# Global engineering instructions", claude_md)
        for name in self.old_agents:
            with self.subTest(agent=name):
                agent = (REPO_ROOT / f"dist/claude/agents/{name}").read_text(encoding="utf-8")
                self.assertTrue(agent.endswith("\n\n" + self.footer))
                self.assertEqual(agent.count(self.footer.strip()), 1)

    def generated_body(self, rel: str) -> str:
        """A dist/claude file without the adapter or the agent footer, as the old file ended."""
        text = (REPO_ROOT / "dist/claude" / rel).read_text(encoding="utf-8")
        if rel == "CLAUDE.md":
            prefix = self.adapter.rstrip("\n") + "\n\n"
            self.assertTrue(text.startswith(prefix), "the adapter does not come first")
            return text[len(prefix) :]
        suffix = "\n\n" + self.footer
        self.assertTrue(text.endswith(suffix), f"{rel} does not end with the footer")
        return text[: -len(suffix)] + "\n"

    def test_bodies_differ_from_the_old_files_only_by_the_listed_wording_edits(self) -> None:
        names = ["CLAUDE.md", *(f"agents/{name}" for name in self.old_agents)]
        unused = {pattern for pattern, _ in NEUTRAL_WORDING}
        for rel in names:
            with self.subTest(file=rel):
                head, body = split_frontmatter(git_show(PRE_0004_COMMIT, f"claude/{rel}").decode("utf-8"))
                for old, new in ONE_OFF_WORDING.get(rel, ()):
                    self.assertEqual(body.count(old), 1, f"one-off edit source not found once: {old!r}")
                    body = body.replace(old, new)
                for pattern, replacement in NEUTRAL_WORDING:
                    body, count = re.subn(pattern, replacement, body)
                    if count:
                        unused.discard(pattern)
                self.assertEqual(self.generated_body(rel), head + body)
        self.assertEqual(unused, set(), "wording edits that no file needs")
        self.assertEqual(set(ONE_OFF_WORDING) - set(names), set(), "one-off edits for files that are not generated")

    def test_the_adapter_and_footer_stay_small(self) -> None:
        # Decision record 0004: an adapter under 1 KB and a footer of two or three lines.
        self.assertLess(len(self.adapter.encode("utf-8")), 1024)
        self.assertLessEqual(len(self.footer.strip().split("\n")), 3)


class FreshnessTest(unittest.TestCase):
    def test_committed_output_is_fresh(self) -> None:
        self.assertEqual(build_targets.problems(REPO_ROOT), [])


class GeneratorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="ifdev-ai-tools-build-"))
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("core", "targets", "dist"):
            shutil.copytree(REPO_ROOT / name, self.root / name, symlinks=True)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def tree(self) -> dict[str, bytes]:
        dist = self.root / "dist"
        return {p.relative_to(dist).as_posix(): p.read_bytes() for p in sorted(dist.rglob("*")) if p.is_file()}

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class CliTests(GeneratorTestCase):
    def test_check_passes_on_fresh_output_and_build_is_idempotent(self) -> None:
        before = self.tree()

        check = self.run_cli("--check")
        build = self.run_cli()

        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("build_targets: OK", check.stdout)
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertNotIn("updated:", build.stdout)
        self.assertEqual(self.tree(), before)

    def test_rebuilding_from_scratch_is_deterministic(self) -> None:
        before = self.tree()
        shutil.rmtree(self.root / "dist")

        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.run_cli().stdout, "build_targets: OK\n")

    def test_check_reports_stale_missing_and_extra_files_without_writing(self) -> None:
        self.write("core/instructions.md", "# Changed instructions\n")
        (self.root / "dist/claude/agents/qa.md").unlink()
        self.write("dist/claude/agents/extra.md", "hand-written\n")
        self.write("dist/other/notes.md", "stray\n")
        before = self.tree()

        result = self.run_cli("--check")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR dist/claude/CLAUDE.md: generated file is stale", result.stdout)
        self.assertIn("ERROR dist/claude/agents/qa.md: generated file is missing", result.stdout)
        self.assertIn("ERROR dist/claude/agents/extra.md: file is not produced by the generator", result.stdout)
        self.assertIn("ERROR dist/other/notes.md: file is not produced by the generator", result.stdout)
        self.assertEqual(self.tree(), before)

    def test_build_repairs_stale_missing_and_extra_files(self) -> None:
        expected = self.tree()
        self.write("dist/claude/CLAUDE.md", "edited by hand\n")
        (self.root / "dist/claude/agents/qa.md").unlink()
        self.write("dist/other/notes.md", "stray\n")

        result = self.run_cli()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.tree(), expected)
        self.assertFalse((self.root / "dist/other").exists())
        self.assertEqual(self.run_cli("--check").returncode, 0)

    def test_symlink_in_dist_is_reported_and_replaced(self) -> None:
        target = self.root / "dist/claude/agents/dev.md"
        content = target.read_bytes()
        target.unlink()
        target.symlink_to(self.root / "core/agents/dev.md")

        self.assertIn("ERROR dist/claude/agents/dev.md: generated file is stale", self.run_cli("--check").stdout)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), content)

    def test_invalid_source_fails_with_exit_code_2_and_writes_nothing(self) -> None:
        self.write("core/agents/broken.md", "---\nname: broken\ndescription: Synthetic.\n---\n")
        before = self.tree()

        for args in ((), ("--check",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)

                self.assertEqual(result.returncode, 2)
                self.assertIn("core/agents/broken.md: missing 'read_only' key", result.stderr)
                self.assertEqual(self.tree(), before)


class ClaudeAgentTests(unittest.TestCase):
    def agent(self, frontmatter: str, body: str = "\n# Role\n") -> str:
        return f"---\n{frontmatter}\n---\n{body}"

    def test_read_only_maps_to_todays_claude_tool_lists_in_place(self) -> None:
        source = "name: x\ndescription: Synthetic.\nread_only: {}\nmodel: inherit"
        self.assertEqual(
            build_targets.claude_agent("x.md", self.agent(source.format("true")), ""),
            self.agent(
                "name: x\ndescription: Synthetic.\ntools: Read, Glob, Grep, Bash\n"
                "disallowedTools: Write, Edit\nmodel: inherit"
            ),
        )
        self.assertEqual(
            build_targets.claude_agent("x.md", self.agent(source.format("false")), ""),
            self.agent("name: x\ndescription: Synthetic.\ntools: Read, Glob, Grep, Bash, Write, Edit\nmodel: inherit"),
        )

    def test_footer_is_appended_after_one_blank_line(self) -> None:
        result = build_targets.claude_agent("x.md", self.agent("name: x\nread_only: false", "\nBody.\n\n"), "Footer.\n")
        self.assertTrue(result.endswith("\nBody.\n\nFooter.\n"), result)

    def test_invalid_frontmatter_is_rejected(self) -> None:
        cases = {
            "# no frontmatter\n": "missing frontmatter",
            "---\nname: x\nread_only: true\n": "unterminated frontmatter",
            self.agent("name: x"): "missing 'read_only' key",
            self.agent("name: x\nread_only: yes"): "'read_only' must be true or false",
            self.agent("name: x\nread_only: true\nread_only: true"): "duplicate 'read_only' key",
        }
        for text, message in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(build_targets.BuildError, message):
                    build_targets.claude_agent("x.md", text, "")


class JoinSectionsTests(unittest.TestCase):
    def test_empty_sections_add_nothing(self) -> None:
        self.assertEqual(build_targets.join_sections("", "Text.\n"), "Text.\n")
        self.assertEqual(build_targets.join_sections("Text.\n", " \n"), "Text.\n")
        self.assertEqual(build_targets.join_sections("", ""), "")

    def test_sections_are_separated_by_one_blank_line(self) -> None:
        self.assertEqual(build_targets.join_sections("Adapter.\n\n", "# Body\n"), "Adapter.\n\n# Body\n")


class AdapterTests(GeneratorTestCase):
    def test_adapter_and_footer_are_included_in_the_claude_output(self) -> None:
        self.write("targets/claude/adapter.md", "Adapter note.\n")
        self.write("targets/claude/agent-footer.md", "Footer note.\n")

        self.assertEqual(self.run_cli().returncode, 0)

        instructions = (self.root / "core/instructions.md").read_text(encoding="utf-8")
        self.assertEqual(
            (self.root / "dist/claude/CLAUDE.md").read_text(encoding="utf-8"), "Adapter note.\n\n" + instructions
        )
        self.assertTrue((self.root / "dist/claude/agents/dev.md").read_text(encoding="utf-8").endswith("\n\nFooter note.\n"))

    def test_missing_adapter_fails(self) -> None:
        (self.root / "targets/claude/adapter.md").unlink()
        result = self.run_cli("--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("targets/claude/adapter.md: source is missing", result.stderr)


try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None


def parse_toml(test: unittest.TestCase, text: str) -> dict:
    if tomllib is None:
        test.skipTest("tomllib needs Python 3.11+")
    return tomllib.loads(text)


class TomlStringTests(unittest.TestCase):
    SAMPLES = (
        "",
        "plain text",
        'say "hi"',
        'three """ quotes and a trailing quote"',
        "back\\slash \\n not a newline \\u0041",
        "tab\there",
        "line one\nline two\n",
        "\n\nleading newlines kept",
        "crlf\r\nand lone cr\r",
        "controls \x00 \x07 \x08 \x0b \x0c \x1b \x1f \x7f",
        "unicode — é ✨ 𝄞",
        "ends with backslash \\",
    )

    def test_round_trip_through_a_toml_parser(self) -> None:
        for value in self.SAMPLES:
            for multiline in (False, True):
                with self.subTest(value=value, multiline=multiline):
                    text = f"key = {build_targets.toml_string(value, multiline=multiline)}\n"
                    self.assertEqual(parse_toml(self, text), {"key": value})

    def test_single_line_strings_never_span_lines(self) -> None:
        for value in self.SAMPLES:
            self.assertNotIn("\n", build_targets.toml_string(value))
            self.assertNotIn("\r", build_targets.toml_string(value))

    def test_escapes(self) -> None:
        self.assertEqual(build_targets.toml_string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(build_targets.toml_string("a\nb"), '"a\\nb"')
        self.assertEqual(build_targets.toml_string("\x01\x7f"), '"\\u0001\\u007F"')
        self.assertEqual(build_targets.toml_string("a\nb", multiline=True), '"""\na\nb"""')


class CodexAgentTests(unittest.TestCase):
    def agent(self, read_only: str = "false", body: str = "\n# Role\n\nBody with \"quotes\" and a \\ backslash.\n") -> str:
        return f"---\nname: x\ndescription: Synthetic role — for tests.\nread_only: {read_only}\n---\n{body}"

    def test_writer_agent_keys_body_and_footer(self) -> None:
        text = build_targets.codex_agent("core/agents/x.md", self.agent(), "Footer.\n")

        self.assertTrue(text.startswith("# Generated by scripts/build_targets.py from core/agents/x.md; do not edit.\n"))
        self.assertEqual(
            parse_toml(self, text),
            {
                "name": "x",
                "description": "Synthetic role — for tests.",
                "developer_instructions": '# Role\n\nBody with "quotes" and a \\ backslash.\n\nFooter.\n',
            },
        )
        self.assertNotIn(build_targets.CODEX_READ_ONLY_NOTICE, text)

    def test_read_only_agent_carries_the_notice_before_the_footer(self) -> None:
        text = build_targets.codex_agent("core/agents/x.md", self.agent("true"), "Footer.\n")

        instructions = parse_toml(self, text)["developer_instructions"]
        self.assertTrue(instructions.endswith(f"\n\n{build_targets.CODEX_READ_ONLY_NOTICE}\n\nFooter.\n"), instructions)

    def test_quoted_and_block_scalars_are_read(self) -> None:
        cases = {
            "description: 'It''s quoted'": "It's quoted",
            'description: "double quoted"': "double quoted",
            "description: >\n  Folded synthetic text\n  across lines.": "Folded synthetic text across lines.",
            "description: |-\n  Literal\n  lines.": "Literal\nlines.",
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                text = f"---\nname: x\n{line}\nread_only: false\nmodel: inherit\n---\n\n# Role\n"
                agent = parse_toml(self, build_targets.codex_agent("x.md", text, ""))
                self.assertEqual(agent["description"], expected)
                self.assertEqual(agent["developer_instructions"], "# Role\n")

    def test_invalid_frontmatter_is_rejected(self) -> None:
        cases = {
            "# no frontmatter\n": "missing frontmatter",
            "---\nname: x\n": "unterminated frontmatter",
            "---\nname: x\nread_only: true\n---\n": "missing 'description' key",
            "---\nname: x\ndescription: d\n---\n": "missing 'read_only' key",
            "---\nname: x\ndescription: d\nread_only: maybe\n---\n": "'read_only' must be true or false",
            "---\nname: x\nname: y\ndescription: d\nread_only: true\n---\n": "duplicate 'name' key",
            "---\nname: x\ndescription: [a, b]\nread_only: true\n---\n": "unsupported frontmatter line",
            "---\nname: x\ndescription: &anchor d\nread_only: true\n---\n": "unsupported frontmatter line",
            "---\nname: x\n  indented: d\nread_only: true\n---\n": "unsupported frontmatter line",
            "---\nname: x\ndescription: \"esc\\\\aped\"\nread_only: true\n---\n": "unsupported quoted frontmatter value",
            "---\nname: x\ndescription: 'unclosed\nread_only: true\n---\n": "unsupported quoted frontmatter value",
        }
        for text, message in cases.items():
            with self.subTest(message):
                with self.assertRaisesRegex(build_targets.BuildError, message):
                    build_targets.codex_agent("x.md", text, "")


class CodexOutputTests(GeneratorTestCase):
    def test_global_file_is_adapter_instructions_and_sorted_rules(self) -> None:
        self.write("targets/codex/adapter.md", "Adapter note.\n")
        self.write("core/rules/a-first.md", "# A rule\n")

        self.assertEqual(self.run_cli().returncode, 0)

        rules = [
            (self.root / "core/rules" / name).read_text(encoding="utf-8")
            for name in sorted(p.name for p in (self.root / "core/rules").glob("*.md"))
        ]
        self.assertEqual(rules[0], "# A rule\n")
        instructions = (self.root / "core/instructions.md").read_text(encoding="utf-8")
        self.assertEqual(
            (self.root / "dist/codex/AGENTS.md").read_text(encoding="utf-8"),
            "\n\n".join(["Adapter note.", instructions.rstrip("\n"), *(rule.rstrip("\n") for rule in rules)]) + "\n",
        )

    def test_one_agent_file_per_core_agent(self) -> None:
        self.write("core/agents/extra.md", "---\nname: extra\ndescription: Synthetic.\nread_only: false\n---\n\n# Extra\n")

        self.assertEqual(self.run_cli().returncode, 0)

        core = sorted(p.stem for p in (self.root / "core/agents").glob("*.md"))
        codex = sorted(p.stem for p in (self.root / "dist/codex/agents").glob("*.toml"))
        self.assertEqual(codex, core)
        (self.root / "core/agents/extra.md").unlink()
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertFalse((self.root / "dist/codex/agents/extra.toml").exists())

    def test_check_reports_a_stale_codex_file(self) -> None:
        self.write("targets/codex/agent-footer.md", "Changed footer.\n")

        result = self.run_cli("--check")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR dist/codex/agents/dev.toml: generated file is stale", result.stdout)
        self.assertNotIn("dist/codex/AGENTS.md", result.stdout)

    def test_missing_codex_adapter_fails(self) -> None:
        (self.root / "targets/codex/adapter.md").unlink()
        result = self.run_cli("--check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("targets/codex/adapter.md: source is missing", result.stderr)


class CommittedCodexOutputTests(unittest.TestCase):
    """The committed Codex files parse back to their neutral sources."""

    def test_agents_parse_to_the_core_name_description_and_body(self) -> None:
        footer = (REPO_ROOT / "targets/codex/agent-footer.md").read_text(encoding="utf-8")
        for source in sorted((REPO_ROOT / "core/agents").glob("*.md")):
            with self.subTest(agent=source.name):
                head, body = split_frontmatter(source.read_text(encoding="utf-8"))
                fields = dict(line.split(": ", 1) for line in head.split("\n")[1:-1])
                agent = parse_toml(self, (REPO_ROOT / f"dist/codex/agents/{source.stem}.toml").read_text(encoding="utf-8"))
                self.assertEqual(agent["name"], fields["name"])
                self.assertEqual(agent["description"], fields["description"])
                instructions = agent["developer_instructions"]
                self.assertTrue(instructions.startswith(body.lstrip("\n").rstrip("\n")))
                self.assertTrue(instructions.endswith("\n\n" + footer))
                self.assertEqual(build_targets.CODEX_READ_ONLY_NOTICE in instructions, fields["read_only"] == "true")

    def test_the_codex_adapter_and_footer_stay_small(self) -> None:
        self.assertLess(len((REPO_ROOT / "targets/codex/adapter.md").read_bytes()), 1024)
        self.assertLessEqual(len((REPO_ROOT / "targets/codex/agent-footer.md").read_text(encoding="utf-8").splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
