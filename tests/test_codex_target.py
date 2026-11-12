"""Tests for the codex install target (decision record 0004, slice C).

Each test runs the real install.sh and uninstall.sh on a copy of the toolkit with a temporary HOME
passed only to the subprocesses (see test_install.py). The developer's HOME and Codex home are never
used; the optional prompt-input check runs Codex with a temporary CODEX_HOME holding only the
installed files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from test_by_name_references_acceptance import references
from test_install import MANAGED, SETTINGS_JSON, TARGETS_STATE, InstallerTestCase, snapshot

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
# Installed as regular-file copies: Codex does not load a symlinked custom agent (ELOOP).
CODEX_COPIED = {f".codex/agents/{role}.toml": f"dist/codex/agents/{role}.toml" for role in ROLES}
CODEX_LINKED = {
    ".codex/AGENTS.md": "dist/codex/AGENTS.md",
    **{f".agents/skills/{skill}": f"core/skills/{skill}" for skill in SKILLS},
    ".codex/notify/agent-notify.sh": "scripts/agent-notify.sh",
    SHARED_ADAPTER: "targets/codex/notify/agent-notify-codex.sh",
}
CODEX_MANAGED = {**CODEX_LINKED, **CODEX_COPIED}
CODEX = ("--target", "codex")
OVERRIDE = ".codex/AGENTS.override.md"
COPIES_STATE = ".ifdev-ai-tools/copies"


class CodexTestCase(InstallerTestCase):
    def links(self) -> dict[str, str]:
        return {rel: value[1] for rel, value in snapshot(self.home).items() if value[0] == "link"}

    def expected_links(self, *managed: dict[str, str]) -> dict[str, str]:
        return {rel: str(self.repo_real / source) for items in managed for rel, source in items.items()}

    def state(self) -> str:
        return (self.home / TARGETS_STATE).read_text()

    def assert_copies(self) -> None:
        """Every Codex agent is a regular file, not a symlink, byte-equal to its dist source."""
        for rel, source in CODEX_COPIED.items():
            path = self.home / rel
            self.assertFalse(os.path.islink(path), rel)
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(path.read_bytes(), (self.repo / source).read_bytes(), rel)

    def assert_no_copies(self) -> None:
        for rel in CODEX_COPIED:
            self.assert_absent(self.home / rel)


class CodexInstallTests(CodexTestCase):
    def test_install_creates_exactly_the_codex_destinations(self) -> None:
        self.assertEqual(len(ROLES), 18)
        result = self.install(*CODEX)

        self.assertEqual(self.links(), self.expected_links(CODEX_LINKED))
        self.assert_copies()
        self.assertIn(f"{len(CODEX_LINKED)} linked, {len(CODEX_COPIED)} copied, 0 backed up, 0 unchanged", result.stdout)
        self.assertEqual(self.state(), "codex\n")
        # The claude target's files and settings hooks are not touched.
        self.assert_absent(self.home / ".claude")
        # Every link resolves to real content.
        self.assertTrue((self.home / ".agents/skills/work-session/agents/openai.yaml").is_file())

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
        self.assertIn(f"would copy: ~/.codex/agents/dev.toml from {self.repo_real / 'dist/codex/agents/dev.toml'}", dry.stdout)
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
                self.assertIn(f"0 linked, 0 copied, 0 backed up, {len(CODEX_MANAGED)} unchanged", rerun.stdout)

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
        self.assertEqual(self.links(), self.expected_links(MANAGED, CODEX_LINKED))
        self.assert_copies()

    def test_all_installs_both_targets(self) -> None:
        result = self.install("--target", "all")

        self.assertEqual(self.links(), self.expected_links(MANAGED, CODEX_LINKED))
        self.assert_copies()
        # The shared adapter is one destination, linked once.
        self.assertIn(f"{len(MANAGED) + len(CODEX_LINKED) - 1} linked, {len(CODEX_COPIED)} copied", result.stdout)
        self.assertEqual(self.state(), "claude\ncodex\n")


class CodexUninstallTests(CodexTestCase):
    def test_uninstall_removes_the_codex_links_and_record(self) -> None:
        self.write_siblings()
        self.install(*CODEX)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertEqual(self.links(), {})
        self.assert_no_copies()
        self.assertIn(f"done: {len(CODEX_MANAGED)} removed", result.stdout)
        self.assert_absent(self.home / TARGETS_STATE)
        self.assert_absent(self.home / COPIES_STATE)
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
        self.assertEqual(self.links(), self.expected_links(CODEX_LINKED))
        self.assert_copies()
        self.assertIn(f"done: {len(MANAGED) - 1} removed", result.stdout)
        # Only the claude target owns the settings hooks.
        hooks = json.loads((self.home / SETTINGS_JSON).read_text()).get("hooks", {})
        self.assertNotIn("agent-notify-hook.sh", json.dumps(hooks))
        self.assertEqual(self.state(), "codex\n")

        # The remaining target then removes the shared adapter with its own items.
        rest = self.run_script("uninstall.sh")
        self.assert_ok(rest)
        self.assertEqual(self.links(), {})
        self.assert_no_copies()
        self.assert_absent(self.home / TARGETS_STATE)
        self.assert_absent(self.home / COPIES_STATE)

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
        self.assert_no_copies()


class CodexAgentCopyTests(CodexTestCase):
    """Codex opens custom agent files without following symlinks, so a linked agent is listed but
    fails to start ("Symbolic link loop"). The agents are installed as copies whose checksums are
    recorded in ~/.ifdev-ai-tools/copies; only a copy that still matches its record is the toolkit's."""

    DEV = ".codex/agents/dev.toml"

    def records(self) -> dict[str, str]:
        path = self.home / COPIES_STATE
        if not path.exists():
            return {}
        return {rel: checksum for checksum, rel in (line.split(" ", 1) for line in path.read_text().splitlines())}

    def link_like_the_old_installer(self) -> None:
        """A machine installed before the agents were copied: every codex item links to this checkout."""
        for rel, source in CODEX_MANAGED.items():
            path = self.home / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(self.repo_real / source, path)
        self.write_home(TARGETS_STATE, "codex\n")

    def change_dist(self, rel: str = DEV) -> bytes:
        source = self.repo / CODEX_COPIED[rel]
        source.write_bytes(source.read_bytes() + b"# a newer version of the generated agent\n")
        return source.read_bytes()

    def test_fresh_install_writes_regular_files_and_records_their_checksums(self) -> None:
        self.install(*CODEX)

        self.assert_copies()
        self.assertEqual(sorted(p.name for p in (self.home / ".codex/agents").iterdir()), sorted(f"{r}.toml" for r in ROLES))
        records = self.records()
        self.assertEqual(set(records), set(CODEX_COPIED))
        for rel, source in CODEX_COPIED.items():
            digest = hashlib.sha256((self.repo / source).read_bytes()).hexdigest()
            self.assertEqual(records[rel], f"sha256:{digest}", rel)
        self.assertEqual(stat.S_IMODE((self.home / self.DEV).stat().st_mode), 0o644)
        # Written through temporary files renamed into place: none is left in the state directory.
        self.assertEqual(sorted(p.name for p in (self.home / ".ifdev-ai-tools").iterdir()), ["copies", "targets"])

    def test_legacy_symlinks_are_migrated_to_copies_without_backups(self) -> None:
        self.link_like_the_old_installer()
        before = snapshot(self.home)

        dry = self.run_script("install.sh", "--dry-run")
        self.assert_ok(dry)
        self.assertIn(f"would replace the link ~/{self.DEV} with a copy of {self.repo_real / CODEX_COPIED[self.DEV]}", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assert_copies()
        self.assertEqual(self.links(), self.expected_links(CODEX_LINKED))
        self.assertIn(f"copied: ~/{self.DEV} from {self.repo_real / CODEX_COPIED[self.DEV]} (replaced the link)", result.stdout)
        self.assertIn(f"0 linked, {len(CODEX_COPIED)} copied, 0 backed up, {len(CODEX_LINKED)} unchanged", result.stdout)
        self.assertEqual(self.backup_dirs(), [])
        self.assertEqual(set(self.records()), set(CODEX_COPIED))

    def test_reinstall_after_a_dist_change_updates_the_unmodified_copy(self) -> None:
        self.install(*CODEX)
        new_content = self.change_dist()

        dry = self.run_script("install.sh", "--dry-run")
        self.assert_ok(dry)
        self.assertIn(f"would update the copy: ~/{self.DEV}", dry.stdout)

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertEqual((self.home / self.DEV).read_bytes(), new_content)
        self.assertIn(f"updated: ~/{self.DEV}", result.stdout)
        self.assertIn(f"0 linked, 1 copied, 0 backed up, {len(CODEX_MANAGED) - 1} unchanged", result.stdout)
        self.assertEqual(self.backup_dirs(), [])
        self.assertEqual(self.records()[self.DEV], f"sha256:{hashlib.sha256(new_content).hexdigest()}")

    def test_user_modified_copy_is_backed_up_not_overwritten(self) -> None:
        self.install(*CODEX)
        modified = (self.home / self.DEV).read_text() + "# my own tweak\n"
        (self.home / self.DEV).write_text(modified)
        for label, change_dist in (("same dist", False), ("newer dist", True)):
            with self.subTest(label):
                if change_dist:
                    (self.home / self.DEV).write_text(modified)
                    self.change_dist()
                for old in self.backup_dirs():
                    shutil.rmtree(old)
                before = snapshot(self.home)
                dry = self.run_script("install.sh", "--dry-run")
                self.assert_ok(dry)
                self.assertIn(f"would back up ~/{self.DEV} to ~/.ifdev-ai-tools/backups/<timestamp>-<unique>/ and copy it", dry.stdout)
                self.assertEqual(snapshot(self.home), before)

                result = self.run_script("install.sh")

                self.assert_ok(result)
                self.assertIn(f"0 linked, 1 copied, 1 backed up, {len(CODEX_MANAGED) - 1} unchanged", result.stdout)
                [backup] = self.backup_dirs()
                self.assertEqual((backup / self.DEV).read_text(), modified)
                self.assertEqual((backup / "MANIFEST").read_text(), f"{self.DEV}\n")
                self.assert_copies()

    def test_foreign_files_and_links_at_agent_paths_are_backed_up(self) -> None:
        # The generated-file header alone does not make a file the toolkit's.
        header = (self.repo / CODEX_COPIED[self.DEV]).read_text().splitlines()[0]
        self.assertTrue(header.startswith("# Generated by scripts/build_targets.py"), header)
        self.write_home(self.DEV, f'{header}\nname = "my-dev"\n')
        other = self.outside / "qa.toml"
        other.write_text('name = "other"\n')
        os.symlink(other, self.home / ".codex/agents/qa.toml")

        result = self.install(*CODEX)

        self.assertIn("2 backed up", result.stdout)
        [backup] = self.backup_dirs()
        self.assertEqual((backup / self.DEV).read_text(), f'{header}\nname = "my-dev"\n')
        self.assertEqual(os.readlink(backup / ".codex/agents/qa.toml"), str(other))
        self.assert_copies()

    def test_uninstall_removes_unmodified_copies_keeps_modified_ones_and_removes_old_links(self) -> None:
        self.install(*CODEX)
        modified = (self.home / self.DEV).read_text() + "# my own tweak\n"
        (self.home / self.DEV).write_text(modified)
        qa = self.home / ".codex/agents/qa.toml"
        qa.unlink()
        os.symlink(self.repo_real / CODEX_COPIED[".codex/agents/qa.toml"], qa)
        before = snapshot(self.home)

        dry = self.run_script("uninstall.sh", "--dry-run")
        self.assert_ok(dry)
        self.assertIn("would remove: ~/.codex/agents/po.toml", dry.stdout)
        self.assertIn("would remove: ~/.codex/agents/qa.toml", dry.stdout)
        self.assertIn(f"kept (copy modified since it was installed): ~/{self.DEV}", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        result = self.run_script("uninstall.sh")

        self.assert_ok(result)
        self.assertIn(f"kept (copy modified since it was installed): ~/{self.DEV}", result.stdout)
        self.assertIn(f"done: {len(CODEX_MANAGED) - 1} removed", result.stdout)
        self.assertEqual(sorted(p.name for p in (self.home / ".codex/agents").iterdir()), ["dev.toml"])
        self.assertEqual((self.home / self.DEV).read_text(), modified)
        self.assertEqual(self.links(), {})
        self.assert_absent(self.home / COPIES_STATE)

        # The kept copy is the user's now: a later install backs it up instead of replacing it.
        again = self.install(*CODEX)
        self.assertIn("1 backed up", again.stdout)
        [backup] = self.backup_dirs()
        self.assertEqual((backup / self.DEV).read_text(), modified)

    def test_uninstall_keeps_a_file_equal_to_dist_that_was_never_recorded(self) -> None:
        self.write_home(self.DEV, (self.repo / CODEX_COPIED[self.DEV]).read_text())
        before = snapshot(self.home)

        result = self.run_script("uninstall.sh", *CODEX)

        self.assert_ok(result)
        self.assertIn(f"kept (not a link to this repository): ~/{self.DEV}", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_current_copy_without_its_record_is_adopted(self) -> None:
        # An interrupted run can leave a current copy behind before its checksum is recorded.
        self.install(*CODEX)
        (self.home / COPIES_STATE).unlink()
        before = snapshot(self.home)

        dry = self.run_script("install.sh", "--dry-run")
        self.assert_ok(dry)
        self.assertIn(f"would adopt: ~/{self.DEV} already equals this repository's copy", dry.stdout)
        self.assertEqual(snapshot(self.home), before)

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertIn(f"adopted: ~/{self.DEV} (equal to this repository's copy; checksum recorded)", result.stdout)
        self.assertIn(f"0 linked, 0 copied, 0 backed up, {len(CODEX_MANAGED)} unchanged", result.stdout)
        self.assertEqual(set(self.records()), set(CODEX_COPIED))
        self.assert_ok(self.run_script("uninstall.sh"))
        self.assert_no_copies()

    @unittest.skipIf(os.geteuid() == 0, "root reads a file without read permission")
    def test_unreadable_source_refuses_before_any_change(self) -> None:
        source = self.repo / CODEX_COPIED[self.DEV]
        source.chmod(0)
        self.addCleanup(source.chmod, 0o644)
        before = snapshot(self.home)

        result = self.run_script("install.sh", *CODEX)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"could not compute the checksum of {self.repo_real / CODEX_COPIED[self.DEV]}", result.stderr)
        self.assertNotIn("Permission denied", result.stderr)
        self.assertEqual(snapshot(self.home), before)

    def test_shared_ownership_keeps_the_copies_and_records_with_the_codex_target(self) -> None:
        self.install("--target", "all")
        records = self.records()

        self.assert_ok(self.run_script("uninstall.sh", "--target", "claude"))

        self.assert_copies()
        self.assertEqual(self.records(), records)
        self.assertEqual(self.state(), "codex\n")
        self.assert_ok(self.run_script("uninstall.sh", "--target", "claude"))  # nothing left for claude
        self.assertEqual(self.records(), records)

        self.assert_ok(self.run_script("uninstall.sh"))
        self.assert_no_copies()
        self.assert_absent(self.home / COPIES_STATE)

    def test_unsafe_copies_state_refuses_without_changes(self) -> None:
        cases = {
            "directory": lambda path: path.mkdir(parents=True),
            "symlink": lambda path: (path.parent.mkdir(parents=True), os.symlink(self.outside / "copies", path)),
        }
        for label, create in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.home)
                self.home.mkdir()
                create(self.home / COPIES_STATE)
                before = snapshot(self.home)
                for script, args in (("install.sh", CODEX), ("install.sh", (*CODEX, "--dry-run")), ("uninstall.sh", CODEX)):
                    result = self.run_script(script, *args)

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("copies state is not a regular file", result.stderr)
                    self.assertEqual(snapshot(self.home), before)

    def run_with_commands(self, name: str, *args: str, checksum_tools: tuple[str, ...]) -> subprocess.CompletedProcess:
        """Runs a script with a PATH holding only the commands it needs plus <checksum_tools>."""
        bin_dir = self.tmp / f"bin with {'+'.join(checksum_tools) or 'no checksum tool'}"
        if not bin_dir.exists():
            bin_dir.mkdir()
            commands = ("bash", "env", "ln", "mv", "rm", "mkdir", "rmdir", "readlink", "mktemp", "date", "dirname",
                        "basename", "cp", "chmod", "cat", "python3", *checksum_tools)
            for command in commands:
                found = shutil.which(command)
                self.assertIsNotNone(found, command)
                os.symlink(found, bin_dir / command)
        env = {"PATH": str(bin_dir), "HOME": str(self.home), **self.coverage_env}
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / name), *args],
            env=env, cwd=self.tmp, capture_output=True, text=True, timeout=60,
        )

    def test_sha256sum_is_used_when_shasum_is_missing(self) -> None:
        def run(name: str) -> subprocess.CompletedProcess:
            return self.run_with_commands(name, *CODEX, checksum_tools=("sha256sum",))

        self.assert_ok(run("install.sh"))
        self.assert_copies()
        for rel, source in CODEX_COPIED.items():
            digest = hashlib.sha256((self.repo / source).read_bytes()).hexdigest()
            self.assertEqual(self.records()[rel], f"sha256:{digest}", rel)
        rerun = run("install.sh")
        self.assert_ok(rerun)
        self.assertIn(f"0 copied, 0 backed up, {len(CODEX_MANAGED)} unchanged", rerun.stdout)
        (self.home / self.DEV).write_text((self.home / self.DEV).read_text() + "# my own tweak\n")

        result = run("uninstall.sh")

        self.assert_ok(result)
        self.assertIn(f"kept (copy modified since it was installed): ~/{self.DEV}", result.stdout)
        self.assertEqual(sorted(p.name for p in (self.home / ".codex/agents").iterdir()), ["dev.toml"])

    def test_codex_without_a_sha256_tool_refuses_without_changes_but_claude_still_installs(self) -> None:
        before = snapshot(self.home)
        for script, args in (("install.sh", CODEX), ("install.sh", (*CODEX, "--dry-run")), ("uninstall.sh", CODEX)):
            with self.subTest(script=script, args=args):
                result = self.run_with_commands(script, *args, checksum_tools=())

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("required command not found: shasum or sha256sum", result.stderr)
                self.assertEqual(snapshot(self.home), before)

        # The checksum tool is only needed for the Codex agent copies.
        self.assert_ok(self.run_with_commands("install.sh", "--target", "claude", checksum_tools=()))
        self.assert_ok(self.run_with_commands("uninstall.sh", "--target", "claude", checksum_tools=()))


class CodexChecksumFailureTests(CodexTestCase):
    """A checksum tool that fails, or a record that is not a well-formed SHA-256 digest, must never
    make a file look like the toolkit's copy: nothing is adopted, overwritten or removed."""

    DEV = ".codex/agents/dev.toml"
    # A shasum stand-in: hashes the self-test input "abc" correctly, and fails on anything else.
    SELF_TEST_ONLY = 'if [ "$(cat)" = abc ]; then printf abc | "{real}"; else {failure}; fi\n'

    def setUp(self) -> None:
        super().setUp()
        self.real_sha256sum = shutil.which("sha256sum")
        if self.real_sha256sum is None:
            self.skipTest("sha256sum is not installed")

    def run_with_shasum(self, body: str, name: str, *args: str) -> subprocess.CompletedProcess:
        """Runs a script with only the commands it needs and a shasum stub (no sha256sum) running <body>."""
        bin_dir = self.tmp / "bin with a shasum stub"
        if bin_dir.exists():
            shutil.rmtree(bin_dir)
        bin_dir.mkdir()
        for command in ("bash", "env", "ln", "mv", "rm", "mkdir", "rmdir", "readlink", "mktemp", "date", "dirname",
                        "basename", "cp", "chmod", "cat", "python3"):
            os.symlink(shutil.which(command), bin_dir / command)
        stub = bin_dir / "shasum"
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o755)
        env = {"PATH": str(bin_dir), "HOME": str(self.home), **self.coverage_env}
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / name), *args],
            env=env, cwd=self.tmp, capture_output=True, text=True, timeout=60,
        )

    def test_a_failing_sha256_tool_refuses_codex_install_and_uninstall_without_changes(self) -> None:
        self.install(*CODEX)
        self.write_home(".codex/agents/extra-user-agent.toml", 'name = "mine"\n')
        before = snapshot(self.home)
        for label, body in (("fails", "exit 1\n"), ("prints nothing", "exit 0\n"), ("prints garbage", "echo oops\n")):
            for script, args in (("install.sh", CODEX), ("install.sh", (*CODEX, "--dry-run")), ("uninstall.sh", CODEX)):
                with self.subTest(label, script=script, args=args):
                    result = self.run_with_shasum(body, script, *args)

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("failed their SHA-256 self-test", result.stderr)
                    self.assertEqual(snapshot(self.home), before)

    def test_checksum_failures_after_the_self_test_never_prove_ownership(self) -> None:
        for label, failure in (("fails", "exit 1"), ("prints nothing", "exit 0")):
            with self.subTest(label):
                shutil.rmtree(self.home)
                self.home.mkdir()
                body = self.SELF_TEST_ONLY.format(real=self.real_sha256sum, failure=failure)
                # The reviewer's case: a user's file, no record. It must not be adopted.
                self.write_home(self.DEV, 'name = "my-dev"\n')
                before = snapshot(self.home)

                install = self.run_with_shasum(body, "install.sh", *CODEX)

                self.assertEqual(install.returncode, 1, install.stdout + install.stderr)
                self.assertIn("could not compute the checksum of", install.stderr)
                self.assertEqual(snapshot(self.home), before)

                # Recorded, unmodified copies are kept when their checksum cannot be computed.
                (self.home / self.DEV).unlink()
                self.install(*CODEX)

                uninstall = self.run_with_shasum(body, "uninstall.sh", *CODEX)

                self.assert_ok(uninstall)
                self.assertIn(f"kept (copy modified since it was installed): ~/{self.DEV}", uninstall.stdout)
                self.assert_copies()

    def test_malformed_records_never_prove_ownership(self) -> None:
        mine = 'name = "my-dev"\n'
        digest = hashlib.sha256(mine.encode()).hexdigest()
        records = (
            "",  # a blank line
            f"sha256: {self.DEV}",  # what a failing tool used to produce
            f"sha256:{digest[:63]} {self.DEV}",  # truncated
            f"sha256:{digest.upper()} {self.DEV}",  # not lowercase
            f"{digest} {self.DEV}",  # no algorithm
            f"sha256:{digest} .codex/AGENTS.md",  # not a copied destination
        )
        for script, args in (("uninstall.sh", CODEX), ("install.sh", CODEX)):
            with self.subTest(script):
                shutil.rmtree(self.home)
                self.home.mkdir()
                self.write_home(self.DEV, mine)
                self.write_home(COPIES_STATE, "\n".join(records) + "\n")

                result = self.run_script(script, *args)

                self.assert_ok(result)
                if script == "uninstall.sh":
                    self.assertIn(f"kept (not a link to this repository): ~/{self.DEV}", result.stdout)
                    self.assertEqual((self.home / self.DEV).read_text(), mine)
                    # Uninstalling codex drops the malformed lines.
                    self.assert_absent(self.home / COPIES_STATE)
                else:
                    self.assertIn(f"backed up: ~/{self.DEV}", result.stdout)
                    [backup] = self.backup_dirs()
                    self.assertEqual((backup / self.DEV).read_text(), mine)
                    self.assert_copies()
                    lines = (self.home / COPIES_STATE).read_text().splitlines()
                    self.assertEqual(len(lines), len(CODEX_COPIED))
                    self.assertTrue(all(line.startswith("sha256:") and line.endswith(".toml") for line in lines), lines)

    def test_crlf_records_are_tolerated(self) -> None:
        self.install(*CODEX)
        state = self.home / COPIES_STATE
        state.write_bytes(state.read_bytes().replace(b"\n", b"\r\n"))

        rerun = self.run_script("install.sh")
        self.assert_ok(rerun)
        self.assertIn(f"0 linked, 0 copied, 0 backed up, {len(CODEX_MANAGED)} unchanged", rerun.stdout)
        self.assertEqual(self.backup_dirs(), [])

        self.assert_ok(self.run_script("uninstall.sh"))
        self.assert_no_copies()
        self.assert_absent(state)

    def run_with_failing_mv(self, suffix: str, name: str, *args: str) -> subprocess.CompletedProcess:
        """Runs a script whose mv fails when its last argument ends with <suffix>."""
        fake_bin = self.tmp / "fake-mv-bin"
        if fake_bin.exists():
            shutil.rmtree(fake_bin)
        fake_bin.mkdir()
        (fake_bin / "mv").write_text(
            "#!/usr/bin/env bash\n"
            f'if [[ "${{*: -1}}" == *{suffix} ]]; then exit 1; fi\n'
            f'exec "{shutil.which("mv")}" "$@"\n'
        )
        (fake_bin / "mv").chmod(0o755)
        env = {"PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.home), **self.coverage_env}
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / name), *args],
            env=env, cwd=self.tmp, capture_output=True, text=True, timeout=60,
        )

    def test_a_failed_rename_leaves_no_temporary_file(self) -> None:
        for suffix, message in ((f"/{self.DEV}", f"could not copy {self.repo_real / CODEX_COPIED[self.DEV]} to ~/{self.DEV}"),
                                (f"/{COPIES_STATE}", "could not update")):
            with self.subTest(suffix):
                shutil.rmtree(self.home)
                self.home.mkdir()

                result = self.run_with_failing_mv(suffix, "install.sh", *CODEX)

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                leftovers = [p.name for d in (self.home / ".codex/agents", self.home / ".ifdev-ai-tools")
                             for p in d.iterdir() if p.name.startswith((".", "copies."))]
                self.assertEqual(leftovers, [])
                self.assertFalse((self.home / ".ifdev-ai-tools/lock").exists())


class CodexRetiredCopyTests(CodexTestCase):
    """A retired Codex agent is a copy, not a link: it is removed only while it matches its record."""

    RETIRED_COPY = ".codex/agents/old-role.toml"
    CONTENT = 'name = "old-role"\n'

    def setUp(self) -> None:
        super().setUp()
        common = self.repo / "scripts/lib/common.sh"
        content = common.read_text()
        start = content.index("RETIRED_ITEMS=(") + len("RETIRED_ITEMS=(")
        common.write_text(content[:start] + f'\n  "dist/codex/agents/old-role.toml|{self.RETIRED_COPY}"' + content[start:])

    def leave_retired_copy(self, content: str = CONTENT) -> None:
        """An install from before the role was retired: its copy and its record."""
        self.install(*CODEX)
        self.write_home(self.RETIRED_COPY, content)
        digest = hashlib.sha256(self.CONTENT.encode()).hexdigest()
        with (self.home / COPIES_STATE).open("a") as state:
            state.write(f"sha256:{digest} {self.RETIRED_COPY}\n")

    def recorded(self) -> bool:
        return self.RETIRED_COPY in (self.home / COPIES_STATE).read_text()

    def test_install_and_uninstall_remove_an_unmodified_retired_copy(self) -> None:
        for script in ("install.sh", "uninstall.sh"):
            with self.subTest(script):
                shutil.rmtree(self.home)
                self.home.mkdir()
                self.leave_retired_copy()
                before = snapshot(self.home)

                dry = self.run_script(script, "--dry-run")
                self.assert_ok(dry)
                self.assertIn(f"would remove retired copy: ~/{self.RETIRED_COPY}", dry.stdout)
                self.assertEqual(snapshot(self.home), before)

                result = self.run_script(script)

                self.assert_ok(result)
                self.assertIn(f"removed retired copy: ~/{self.RETIRED_COPY}", result.stdout)
                self.assertIn("1 retired removed", result.stdout)
                self.assert_absent(self.home / self.RETIRED_COPY)
                if script == "install.sh":
                    self.assertFalse(self.recorded())
                    self.assert_copies()
                else:
                    self.assert_absent(self.home / COPIES_STATE)

    def test_modified_retired_copy_is_kept_and_forgotten(self) -> None:
        self.leave_retired_copy('name = "old-role"\n# my own tweak\n')

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertIn(f"kept retired copy (modified since it was installed): ~/{self.RETIRED_COPY}", result.stdout)
        self.assertIn("0 retired removed", result.stdout)
        self.assertEqual((self.home / self.RETIRED_COPY).read_text(), 'name = "old-role"\n# my own tweak\n')
        self.assertFalse(self.recorded())

    def test_record_of_a_deleted_retired_copy_is_dropped(self) -> None:
        self.leave_retired_copy()
        (self.home / self.RETIRED_COPY).unlink()

        result = self.run_script("install.sh")

        self.assert_ok(result)
        self.assertNotIn(self.RETIRED_COPY, result.stdout)
        self.assertFalse(self.recorded())

    def test_a_claude_only_run_leaves_a_retired_copy_and_its_record(self) -> None:
        self.leave_retired_copy()
        before = {rel: value for rel, value in snapshot(self.home).items() if rel.startswith((".codex", COPIES_STATE))}

        self.install("--target", "claude")

        after = {rel: value for rel, value in snapshot(self.home).items() if rel.startswith((".codex", COPIES_STATE))}
        self.assertEqual(after, before)


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
