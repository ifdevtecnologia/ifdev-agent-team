"""Tests for the codex install target (decision record 0004, slice C).

Each test runs the real install.sh and uninstall.sh on a copy of the toolkit with a temporary HOME
passed only to the subprocesses (see test_install.py). The developer's HOME and Codex home are never
used; the optional prompt-input check runs Codex with a temporary CODEX_HOME holding only the
installed files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from test_by_name_references_acceptance import references
from test_install import MANAGED, SETTINGS_JSON, SIBLINGS, TARGETS_STATE, InstallerTestCase, snapshot

ROLES = sorted(path.stem for path in (Path(__file__).resolve().parent.parent / "core/agents").glob("*.md"))
SKILLS = (
    "work-session",
    "verify-changes",
    "flutter-dev",
    "web-dev",
    "supabase-dev",
    "safe-refactor",
    "java-spring-dev",
    "e2e-testing",
)
SHARED_ADAPTER = ".codex/notify/agent-notify-codex.sh"
CODEX_MANAGED = {
    ".codex/AGENTS.md": "dist/codex/AGENTS.md",
    **{f".codex/agents/{role}.toml": f"dist/codex/agents/{role}.toml" for role in ROLES},
    **{f".agents/skills/{skill}": f"core/skills/{skill}" for skill in SKILLS},
    ".codex/notify/agent-notify.sh": "scripts/agent-notify.sh",
    SHARED_ADAPTER: "targets/codex/notify/agent-notify-codex.sh",
}
CODEX = ("--target", "codex")
OVERRIDE = ".codex/AGENTS.override.md"


class CodexTestCase(InstallerTestCase):
    def links(self) -> dict[str, str]:
        return {rel: value[1] for rel, value in snapshot(self.home).items() if value[0] == "link"}

    def expected_links(self, *managed: dict[str, str]) -> dict[str, str]:
        return {rel: str(self.repo_real / source) for items in managed for rel, source in items.items()}

    def state(self) -> str:
        return (self.home / TARGETS_STATE).read_text()


class CodexInstallTests(CodexTestCase):
    def test_install_creates_exactly_the_codex_destinations(self) -> None:
        self.assertEqual(len(ROLES), 18)
        result = self.install(*CODEX)

        self.assertEqual(self.links(), self.expected_links(CODEX_MANAGED))
        self.assertIn(f"{len(CODEX_MANAGED)} linked, 0 backed up, 0 unchanged", result.stdout)
        self.assertEqual(self.state(), "codex\n")
        # The claude target's files and settings hooks are not touched.
        self.assert_absent(self.home / ".claude")
        # Every link resolves to real content.
        self.assertTrue((self.home / ".agents/skills/work-session/agents/openai.yaml").is_file())
        self.assertTrue((self.home / ".codex/agents/reviewer.toml").is_file())

    def test_codex_install_leaves_the_base_config_and_siblings_untouched(self) -> None:
        self.write_siblings()
        self.write_home(".codex/agents/other.toml", 'name = "other"\n')
        self.write_home(".agents/skills/other/SKILL.md", "# other\n")

        self.install(*CODEX)
        self.assert_ok(self.run_script("uninstall.sh", *CODEX))

        self.assert_siblings_intact()
        self.assertEqual((self.home / ".codex/agents/other.toml").read_text(), 'name = "other"\n')
        self.assertEqual((self.home / ".agents/skills/other/SKILL.md").read_text(), "# other\n")

    def test_dry_run_changes_nothing_and_rerun_is_idempotent(self) -> None:
        self.write_home(".codex/AGENTS.md", "user instructions\n")
        before = snapshot(self.home)

        dry = self.run_script("install.sh", *CODEX, "--dry-run")

        self.assert_ok(dry)
        self.assertIn("would back up ~/.codex/AGENTS.md", dry.stdout)
        self.assertIn("would link: ~/.agents/skills/web-dev", dry.stdout)
        self.assertIn("would record targets: codex", dry.stdout)
        self.assertNotIn("settings.json", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        self.install(*CODEX)
        installed = snapshot(self.home)
        for args in (CODEX, ()):
            with self.subTest(args=args):
                rerun = self.run_script("install.sh", *args)

                self.assert_ok(rerun)
                self.assertEqual(snapshot(self.home), installed)
                self.assertIn(f"0 linked, 0 backed up, {len(CODEX_MANAGED)} unchanged", rerun.stdout)

    def test_existing_global_agents_md_is_backed_up_and_replaced(self) -> None:
        self.write_home(".codex/AGENTS.md", "user instructions\n")

        result = self.install(*CODEX)

        self.assertIn("1 backed up", result.stdout)
        self.assertEqual(os.readlink(self.home / ".codex/AGENTS.md"), str(self.repo_real / "dist/codex/AGENTS.md"))
        [backup] = self.backup_dirs()
        self.assertEqual((backup / ".codex/AGENTS.md").read_text(), "user instructions\n")
        self.assertEqual((backup / "MANIFEST").read_text(), ".codex/AGENTS.md\n")

    def test_override_file_is_reported_and_left_untouched(self) -> None:
        self.write_home(OVERRIDE, "override\n")

        for args in ((*CODEX, "--dry-run"), CODEX):
            with self.subTest(args=args):
                result = self.run_script("install.sh", *args)

                self.assert_ok(result)
                self.assertIn("warning: ~/.codex/AGENTS.override.md exists", result.stderr)
                self.assertEqual((self.home / OVERRIDE).read_text(), "override\n")
        self.assertTrue((self.home / ".codex/AGENTS.md").is_symlink())

    def test_override_warning_is_only_for_the_codex_target(self) -> None:
        self.write_home(OVERRIDE, "override\n")

        result = self.install()  # claude

        self.assertNotIn("AGENTS.override.md", result.stdout + result.stderr)

    def test_symlinked_parents_are_refused_without_changes(self) -> None:
        for parent in (".agents", ".agents/skills", ".codex", ".codex/agents"):
            with self.subTest(parent=parent):
                shutil.rmtree(self.home)
                self.home.mkdir()
                target = self.outside / parent.replace("/", "-")
                target.mkdir(exist_ok=True)
                link = self.home / parent
                link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, link)
                before = snapshot(self.home)

                for script, args in (("install.sh", CODEX), ("install.sh", (*CODEX, "--dry-run")), ("uninstall.sh", CODEX)):
                    result = self.run_script(script, *args)

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(f"refusing symlinked parent directory: {link}", result.stderr)
                    self.assertEqual(snapshot(self.home), before)
                self.assertEqual(list(target.iterdir()), [])

    def test_codex_only_machine_without_state_is_not_taken_for_claude(self) -> None:
        self.install(*CODEX)
        (self.home / TARGETS_STATE).unlink()
        before = snapshot(self.home)

        result = self.run_script("install.sh")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("choose --target", result.stderr)
        self.assertEqual(snapshot(self.home), before)

    def test_adding_codex_without_state_also_records_a_legacy_claude_install(self) -> None:
        self.install()
        (self.home / TARGETS_STATE).unlink()
        before = snapshot(self.home)

        dry = self.run_script("install.sh", *CODEX, "--dry-run")
        self.assert_ok(dry)
        self.assertIn("would record targets: claude codex", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        self.assert_ok(self.run_script("install.sh", *CODEX))
        self.assertEqual(self.state(), "claude\ncodex\n")

        # A bare install then reinstalls both.
        self.assert_ok(self.run_script("install.sh"))
        self.assertEqual(self.links(), self.expected_links(MANAGED, CODEX_MANAGED))

    def test_all_installs_both_targets(self) -> None:
        result = self.install("--target", "all")

        self.assertEqual(self.links(), self.expected_links(MANAGED, CODEX_MANAGED))
        # The shared adapter is one destination, linked once.
        self.assertIn(f"{len(MANAGED) + len(CODEX_MANAGED) - 1} linked", result.stdout)
        self.assertEqual(self.state(), "claude\ncodex\n")


class CodexUninstallTests(CodexTestCase):
    def test_uninstall_removes_the_codex_links_and_record(self) -> None:
        self.write_siblings()
        self.install(*CODEX)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.links(), {})
        self.assertIn(f"done: {len(CODEX_MANAGED)} removed", result.stdout)
        self.assert_absent(self.home / TARGETS_STATE)
        self.assert_siblings_intact()
        self.assertNotIn("settings.json", result.stdout)

    def test_uninstall_dry_run_changes_nothing(self) -> None:
        self.install(*CODEX)
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh", *CODEX, "--dry-run")

        self.assert_ok(result)
        self.assertIn("would remove: ~/.codex/AGENTS.md", result.stdout)
        self.assertIn("would record targets: none", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_uninstalling_codex_keeps_the_claude_items_hooks_and_shared_adapter(self) -> None:
        self.install("--target", "claude", "--target", "codex")
        settings = (self.home / SETTINGS_JSON).read_text()

        dry = self.run_script("uninstall.sh", *CODEX, "--dry-run")
        self.assert_ok(dry)
        self.assertIn(f"kept (shared with a target that stays installed): ~/{SHARED_ADAPTER}", dry.stdout)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.links(), self.expected_links(MANAGED))
        self.assertIn(f"kept (shared with a target that stays installed): ~/{SHARED_ADAPTER}", result.stdout)
        self.assertIn(f"done: {len(CODEX_MANAGED) - 1} removed", result.stdout)
        self.assertEqual((self.home / SETTINGS_JSON).read_text(), settings)
        self.assertEqual(self.state(), "claude\n")

    def test_uninstalling_claude_keeps_the_codex_items_and_shared_adapter(self) -> None:
        self.install("--target", "all")

        result = self.run_script("uninstall.sh", "--target", "claude")

        self.assert_ok(result)
        self.assertEqual(self.links(), self.expected_links(CODEX_MANAGED))
        self.assertIn(f"done: {len(MANAGED) - 1} removed", result.stdout)
        # Only the claude target owns the settings hooks.
        hooks = json.loads((self.home / SETTINGS_JSON).read_text()).get("hooks", {})
        self.assertNotIn("agent-notify-hook.sh", json.dumps(hooks))
        self.assertEqual(self.state(), "codex\n")

        # The remaining target then removes the shared adapter with its own items.
        rest = self.run_script("uninstall.sh")
        self.assert_ok(rest)
        self.assertEqual(self.links(), {})
        self.assert_absent(self.home / TARGETS_STATE)

    def test_uninstalling_a_target_that_is_not_installed_keeps_the_shared_adapter(self) -> None:
        self.install()  # claude
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.state(), "claude\n")

    def test_uninstalling_codex_without_state_keeps_a_legacy_claude_install(self) -> None:
        # A machine installed before targets were recorded has the claude items, shared adapter
        # included, and no state file.
        self.install()
        (self.home / TARGETS_STATE).unlink()
        settings = (self.home / SETTINGS_JSON).read_text()
        before = snapshot(self.home)

        dry = self.run_script("uninstall.sh", *CODEX, "--dry-run")
        self.assert_ok(dry)
        self.assertIn(f"kept (shared with a target that stays installed): ~/{SHARED_ADAPTER}", dry.stdout)
        self.assertIn("would record targets: claude", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.links(), self.expected_links(MANAGED))
        self.assertEqual((self.home / SETTINGS_JSON).read_text(), settings)
        self.assertEqual(self.state(), "claude\n")

    def test_uninstalling_codex_without_state_removes_only_the_codex_items(self) -> None:
        self.install("--target", "all")
        (self.home / TARGETS_STATE).unlink()

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.links(), self.expected_links(MANAGED))
        self.assertIn(f"kept (shared with a target that stays installed): ~/{SHARED_ADAPTER}", result.stdout)
        self.assertIn(f"done: {len(CODEX_MANAGED) - 1} removed", result.stdout)
        self.assertEqual(self.state(), "claude\n")

    def test_bare_uninstall_without_state_removes_every_targets_links(self) -> None:
        self.install("--target", "all")
        (self.home / TARGETS_STATE).unlink()

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertEqual(self.links(), {})


class CodexByNameReferenceTests(CodexTestCase):
    """Every rule, skill and role named in what Codex reads from the install resolves: a rule is a
    section of ~/.codex/AGENTS.md, a skill is ~/.agents/skills/<name>/SKILL.md and a role is the
    custom agent ~/.codex/agents/<name>.toml."""

    def setUp(self) -> None:
        super().setUp()
        self.install(*CODEX)

    def test_every_named_rule_skill_and_role_resolves(self) -> None:
        agents_md = (self.home / ".codex/AGENTS.md").read_text(encoding="utf-8")
        texts = {".codex/AGENTS.md": agents_md}
        for path in sorted((self.home / ".codex/agents").glob("*.toml")):
            texts[path.relative_to(self.home).as_posix()] = path.read_text(encoding="utf-8")
        for skill in sorted((self.home / ".agents/skills").iterdir()):
            for path in sorted(skill.rglob("*.md")):
                texts[path.relative_to(self.home).as_posix()] = path.read_text(encoding="utf-8")
        problems, kinds = [], set()
        for rel, text in texts.items():
            for kind, name in sorted(references(text)):
                kinds.add(kind)
                rule = self.repo / "core/rules" / f"{name}.md"
                found = {
                    "rule": rule.is_file() and rule.read_text(encoding="utf-8").rstrip("\n") in agents_md,
                    "skill": (self.home / ".agents/skills" / name / "SKILL.md").is_file(),
                    "agent": (self.home / ".codex/agents" / f"{name}.toml").is_file(),
                }[kind]
                if not found:
                    problems.append(f"{rel}: the `{name}` {kind} does not resolve")
        self.assertEqual(problems, [])
        self.assertEqual(kinds, {"rule", "skill", "agent"})

    def test_the_global_instructions_start_with_the_adapter_and_hold_every_rule(self) -> None:
        agents_md = (self.home / ".codex/AGENTS.md").read_text(encoding="utf-8")
        adapter = (self.repo / "targets/codex/adapter.md").read_text(encoding="utf-8")

        self.assertTrue(agents_md.startswith(adapter))
        for adapter_term in ('`spawn_agent`', '`fork_turns: "none"`', "read-only by instruction", "`.codex/`"):
            self.assertIn(adapter_term, adapter)
        for rule in sorted((self.repo / "core/rules").glob("*.md")):
            self.assertIn(rule.read_text(encoding="utf-8"), agents_md, rule.name)


class CodexPromptInputTests(CodexTestCase):
    """Optional: renders the model-visible input with the installed Codex CLI, without login or a
    model call, in a temporary CODEX_HOME that holds only the installed files (no credentials)."""

    def test_codex_renders_the_whole_global_file_and_hides_the_manual_skill(self) -> None:
        codex = shutil.which("codex")
        if codex is None:
            self.skipTest("the codex CLI is not installed")
        self.install(*CODEX)
        project = self.tmp / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("# Synthetic project marker\n")
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex")}
        try:
            result = subprocess.run(
                [codex, "debug", "prompt-input"], env=env, cwd=project, capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            self.skipTest("codex debug prompt-input did not finish in time")
        if result.returncode != 0:
            self.skipTest(f"codex debug prompt-input is unavailable here (exit {result.returncode})")
        rendered = json.dumps(json.loads(result.stdout))
        global_lines = [line for line in (self.home / ".codex/AGENTS.md").read_text().splitlines() if line.strip()]

        for line in (global_lines[0], global_lines[-1], "# Synthetic project marker"):
            self.assertIn(json.dumps(line)[1:-1], rendered)
        skills = rendered.split("### Available skills", 1)[-1]
        self.assertIn("- verify-changes: ", skills)
        self.assertNotIn("- work-session: ", skills)


if __name__ == "__main__":
    unittest.main()
