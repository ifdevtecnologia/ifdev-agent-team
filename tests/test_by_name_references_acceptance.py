"""Acceptance tests for by-name references in the installed Claude target (decision record 0004, slice B).

core/ names rules, skills and roles ("the `testing` rule", "the `web-dev` skill", "the `qa` agent")
and the Claude adapter and agent footer say where they live: a rule is ~/.claude/rules/<name>.md, a
skill is ~/.claude/skills/<name>/SKILL.md and a role is the subagent ~/.claude/agents/<name>.md.
These tests install the Claude target into a temporary HOME with the real install.sh and check that
every name used in what Claude Code reads (CLAUDE.md, the agents and the skills) resolves to an
installed file through that convention. The developer's HOME is never used.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from test_install import InstallerTestCase

NAME = r"`[a-z0-9][a-z0-9-]*`"
SEPARATOR = r"(?:,? and |,? or |, )"
# "the `testing` rule", "the `engineering-principles` and `testing` rules", "the `dev`, `devops` or `ux` roles".
NAMED_LIST = re.compile(rf"(?P<names>(?:{NAME}{SEPARATOR})*{NAME}) (?P<kind>rules?|skills?|agents?|subagents?|roles?)\b")
# "use that skill (`flutter-dev`, `web-dev`, ...)", "The named rules (`engineering-principles`, ...)",
# "the specialized reviewers (`ux-reviewer`, ...)".
PARENTHESIZED_LIST = re.compile(rf"\b(?P<kind>rules?|skills?|roles?|reviewers?) \((?P<names>(?:{NAME}{SEPARATOR})*{NAME})\)")
# The specialized reviewer bullets of the team section: "- `ux-reviewer` when the diff ...".
REVIEWER_BULLET = re.compile(rf"^\s*- (?P<names>{NAME}) when\b", re.MULTILINE)
# Absolute references the adapter and the footer use, for example `~/.claude/rules/<name>.md`.
HOME_REFERENCE = re.compile(r"~/\.claude/[A-Za-z0-9._/-]*[A-Za-z0-9_-]")
# Database roles named in the stack skills ("the `anon` and `authenticated` roles") are not agents.
DATABASE_ROLES = {"anon", "authenticated", "service_role"}

KIND = {
    "rule": "rule",
    "rules": "rule",
    "skill": "skill",
    "skills": "skill",
    "agent": "agent",
    "agents": "agent",
    "subagent": "agent",
    "subagents": "agent",
    "role": "agent",
    "roles": "agent",
    "reviewer": "agent",
    "reviewers": "agent",
}


def names_in(group: str) -> list[str]:
    return re.findall(r"`([^`]+)`", group)


def references(text: str) -> set[tuple[str, str]]:
    """The (kind, name) pairs a Markdown text refers to by name."""
    found: set[tuple[str, str]] = set()
    for pattern in (NAMED_LIST, PARENTHESIZED_LIST):
        for match in pattern.finditer(text):
            kind = KIND[match.group("kind")]
            for name in names_in(match.group("names")):
                if not (kind == "agent" and name in DATABASE_ROLES):
                    found.add((kind, name))
    for match in REVIEWER_BULLET.finditer(text):
        found.update(("agent", name) for name in names_in(match.group("names")))
    return found


def global_lists(claude_md: str) -> tuple[set[str], set[str]]:
    """Skill and agent names listed in the "Global skills: ... Global agents: ..." line, without the
    parenthesized descriptions."""
    line = next(line for line in claude_md.splitlines() if "Global skills:" in line)
    skills_part, agents_part = line.split("Global skills:", 1)[1].split("Global agents:", 1)

    def outside_parentheses(text: str) -> set[str]:
        return set(names_in(re.sub(r"\([^)]*\)", "", text)))

    return outside_parentheses(skills_part), outside_parentheses(agents_part)


class ByNameReferenceTestCase(InstallerTestCase):
    def installed_texts(self) -> dict[str, str]:
        """What Claude Code reads from the install: CLAUDE.md, every agent and every skill file."""
        claude = self.home / ".claude"
        files = [claude / "CLAUDE.md", *sorted((claude / "agents").glob("*.md"))]
        for skill in sorted((claude / "skills").iterdir()):
            files.extend(sorted(skill.rglob("*.md")))
        return {path.relative_to(self.home).as_posix(): path.read_text(encoding="utf-8") for path in files}

    def destination(self, kind: str, name: str) -> Path:
        claude = self.home / ".claude"
        return {
            "rule": claude / "rules" / f"{name}.md",
            "skill": claude / "skills" / name / "SKILL.md",
            "agent": claude / "agents" / f"{name}.md",
        }[kind]

    def unresolved(self) -> list[str]:
        problems = []
        for rel, text in self.installed_texts().items():
            for kind, name in sorted(references(text)):
                if not self.destination(kind, name).is_file():
                    problems.append(f"{rel}: the `{name}` {kind} -> {self.destination(kind, name)} is missing")
        return problems


class InstalledClaudeByNameReferencesTests(ByNameReferenceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.install()

    def test_every_named_rule_skill_and_role_resolves_to_an_installed_file(self) -> None:
        self.assertEqual(self.unresolved(), [])

    def test_references_are_found_in_claude_md_agents_and_skills(self) -> None:
        # Guards against a vacuous pass: the patterns must see the references the content makes.
        texts = self.installed_texts()
        claude_md = references(texts[".claude/CLAUDE.md"])
        for expected in [("rule", "testing"), ("rule", "engineering-principles"), ("rule", "git-workflow"), ("agent", "analyst"), ("agent", "ux-reviewer")]:
            self.assertIn(expected, claude_md)
        self.assertIn(("skill", "e2e-testing"), references(texts[".claude/agents/qa.md"]))
        self.assertIn(("rule", "testing"), references(texts[".claude/skills/safe-refactor/SKILL.md"]))
        kinds = {kind for text in texts.values() for kind, _ in references(text)}
        self.assertEqual(kinds, {"rule", "skill", "agent"})

    def test_the_global_skill_and_agent_lists_match_the_installed_ones_exactly(self) -> None:
        claude_md = (self.home / ".claude/CLAUDE.md").read_text(encoding="utf-8")
        skills, agents = global_lists(claude_md)
        installed_skills = {p.name for p in (self.home / ".claude/skills").iterdir() if (p / "SKILL.md").is_file()}
        installed_agents = {p.stem for p in (self.home / ".claude/agents").glob("*.md")}
        self.assertEqual(skills, installed_skills)
        self.assertEqual(agents, installed_agents)

    def test_the_adapter_and_footer_paths_resolve_to_installed_files(self) -> None:
        # The adapter and footer give the conventions with a `<name>` placeholder; every placeholder
        # is filled with each name referenced by that kind, and literal paths must exist as they are.
        texts = self.installed_texts()
        seen = 0
        for rel, text in texts.items():
            for match in HOME_REFERENCE.finditer(text):
                reference = match.group(0)
                if "<name>" in reference or reference.endswith("<name"):
                    continue
                seen += 1
                with self.subTest(file=rel, reference=reference):
                    self.assertTrue((self.home / reference[2:]).exists(), f"{rel}: {reference} is not installed")
        self.assertGreater(seen, 0)
        adapter = texts[".claude/CLAUDE.md"].split("\n\n", 1)[0]
        self.assertIn("~/.claude/rules/<name>.md", adapter)
        self.assertIn("~/.claude/skills/<name>/", adapter)
        footer = texts[".claude/agents/qa.md"].rstrip("\n").rsplit("\n", 1)[-1]
        for convention in ("~/.claude/rules/<name>.md", "~/.claude/CLAUDE.md", "~/.claude/skills/<name>/SKILL.md"):
            self.assertIn(convention, footer)

    def test_every_installed_agent_ends_with_the_footer_that_resolves_names(self) -> None:
        for agent in sorted((self.home / ".claude/agents").glob("*.md")):
            with self.subTest(agent=agent.name):
                last = agent.read_text(encoding="utf-8").rstrip("\n").rsplit("\n", 1)[-1]
                self.assertTrue(last.startswith("In Claude Code, a named rule is `~/.claude/rules/<name>.md`"), last)


class BrokenByNameReferenceTests(ByNameReferenceTestCase):
    """The check reports a name with no installed file, so a renamed or missing item cannot pass."""

    def test_a_reference_to_a_missing_rule_skill_or_role_is_reported(self) -> None:
        agent = self.repo / "dist/claude/agents/dev.md"
        agent.write_text(
            agent.read_text(encoding="utf-8")
            + "\nSee the `missing-rule` rule, the `missing-skill` skill and the `missing-role` agent.\n",
            encoding="utf-8",
        )
        self.install()

        problems = "\n".join(self.unresolved())
        for kind, name in (("rule", "missing-rule"), ("skill", "missing-skill"), ("agent", "missing-role")):
            self.assertIn(f".claude/agents/dev.md: the `{name}` {kind} ->", problems)


if __name__ == "__main__":
    unittest.main()
