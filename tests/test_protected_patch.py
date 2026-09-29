"""Deterministic tests for the supervisor's protected .claude/** patch handoff."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "codex/skills/supervise-claude/scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


protected_patch = load("protected_patch")

AGENT = ".claude/agents/example.md"
RENAMED = ".claude/agents/renamed.md"
SKILL = ".claude/skills/example/SKILL.md"
ORDINARY = "src/example.txt"
AGENT_TEXT = "---\nname: example\n---\nline one\nline two\nline three\n"
SKILL_TEXT = "# Example skill\nsummary: sample\nstatus: draft\n"
SKILL_FINAL = "# Example skill\nsummary: sample\nstatus: final\n"
ALL = (".claude/**",)

EDIT_SKILL = f"""diff --git a/{SKILL} b/{SKILL}
--- a/{SKILL}
+++ b/{SKILL}
@@ -1,3 +1,3 @@
 # Example skill
 summary: sample
-status: draft
+status: final
"""
NEW_AGENT = """diff --git a/.claude/agents/new.md b/.claude/agents/new.md
new file mode 100644
--- /dev/null
+++ b/.claude/agents/new.md
@@ -0,0 +1,2 @@
+# New
+body
"""
DELETE_AGENT = f"""diff --git a/{AGENT} b/{AGENT}
deleted file mode 100644
--- a/{AGENT}
+++ /dev/null
@@ -1,6 +0,0 @@
----
-name: example
----
-line one
-line two
-line three
"""
RENAME_AGENT = f"""diff --git a/{AGENT} b/{RENAMED}
rename from {AGENT}
rename to {RENAMED}
--- a/{AGENT}
+++ b/{RENAMED}
@@ -1,3 +1,3 @@
 ---
-name: example
+name: renamed
 ---
"""
PURE_RENAME = f"""diff --git a/{AGENT} b/{RENAMED}
similarity index 100%
rename from {AGENT}
rename to {RENAMED}
"""
EDIT_ORDINARY = f"""diff --git a/{ORDINARY} b/{ORDINARY}
--- a/{ORDINARY}
+++ b/{ORDINARY}
@@ -1 +1 @@
-hello
+changed
"""

# A "-U0"-shaped hunk: no leading or trailing context line, only the changed line itself.
# git apply refuses this without --unidiff-zero even though it is a single, unambiguous hunk.
ZERO_CONTEXT_TARGET = ".claude/agents/zero.md"
ZERO_CONTEXT_TEXT = "".join(f"line {n}\n" for n in range(1, 10))
ZERO_CONTEXT_FINAL = ZERO_CONTEXT_TEXT.replace("line 8\n", "line eight\n")
EDIT_ZERO_CONTEXT = f"""diff --git a/{ZERO_CONTEXT_TARGET} b/{ZERO_CONTEXT_TARGET}
--- a/{ZERO_CONTEXT_TARGET}
+++ b/{ZERO_CONTEXT_TARGET}
@@ -8,1 +8,1 @@
-line 8
+line eight
"""

# Rename with an edit expressed as a zero-context hunk, alongside an unrelated second
# protected file edit, mirroring a real-world rename-plus-zero-context-hunk artifact.
ZERO_RENAME_SOURCE = ".claude/agents/legacy.md"
ZERO_RENAME_DEST = ".claude/agents/current.md"
ZERO_RENAME_SOURCE_TEXT = "---\nname: legacy\n---\nalpha\nbeta\ngamma\ndelta\nepsilon\n"
ZERO_RENAME_DEST_TEXT = ZERO_RENAME_SOURCE_TEXT.replace("epsilon\n", "omega\n")
RENAME_WITH_ZERO_CONTEXT_EDIT = f"""diff --git a/{ZERO_RENAME_SOURCE} b/{ZERO_RENAME_DEST}
rename from {ZERO_RENAME_SOURCE}
rename to {ZERO_RENAME_DEST}
--- a/{ZERO_RENAME_SOURCE}
+++ b/{ZERO_RENAME_DEST}
@@ -8,1 +8,1 @@
-epsilon
+omega
"""


def edit_patch(path: str) -> str:
    """A syntactically valid one-line edit of an arbitrary (possibly unsafe) path."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout


class RepoFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.global_settings = self.home / ".claude/settings.json"
        self.global_settings.write_text('{"synthetic": true}\n', encoding="utf-8")
        environment = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Test Author",
            "GIT_COMMITTER_EMAIL": "author@example.invalid",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.project = self.base / "project"
        self.project.mkdir()
        run_git(self.project, "init", "-q", "-b", "main")
        self.write(AGENT, AGENT_TEXT)
        self.write(SKILL, SKILL_TEXT)
        self.write(ORDINARY, "hello\n")
        run_git(self.project, "add", "-A")
        run_git(self.project, "commit", "-q", "-m", "seed")
        self.archive = self.base / "run/applied.patch"
        self.archive.parent.mkdir()

    def write(self, rel: str, content: str) -> Path:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.project / rel).read_text(encoding="utf-8")

    def author(self, text: str | bytes) -> bytes:
        data = text.encode("utf-8") if isinstance(text, str) else text
        (self.project / protected_patch.ARTIFACT_NAME).write_bytes(data)
        return data

    def apply(self, patterns=ALL, **kwargs):
        return protected_patch.apply_patch(self.project, tuple(patterns), self.archive, **kwargs)

    def assert_rejected(self, text, patterns=ALL, message: str = "") -> protected_patch.PatchRejected:
        """Rejection leaves the artifact byte-identical and every file untouched."""
        data = self.author(text)
        before = protected_patch.snapshot(self.project)
        ordinary = self.read(ORDINARY)
        with self.assertRaises(protected_patch.PatchRejected) as caught:
            self.apply(patterns)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)
        self.assertEqual(protected_patch.snapshot(self.project), before)
        self.assertEqual(self.read(ORDINARY), ordinary)
        self.assertFalse(self.archive.exists())
        return caught.exception


class ValidPatchTests(RepoFixture):
    def assert_applied(self, data: bytes) -> None:
        self.assertFalse(os.path.lexists(self.project / protected_patch.ARTIFACT_NAME))
        self.assertEqual(self.archive.read_bytes(), data)

    def test_edit_applies_exactly_and_archives_the_artifact(self) -> None:
        data = self.author(EDIT_SKILL)
        plan = self.apply((SKILL,))
        self.assertEqual(self.read(SKILL), SKILL_FINAL)
        self.assertEqual(plan.changes, [{"kind": "modify", "old_path": SKILL, "new_path": SKILL}])
        self.assertEqual(plan.artifact_sha256, hashlib.sha256(data).hexdigest())
        self.assert_applied(data)
        self.assertEqual(self.read(AGENT), AGENT_TEXT)

    def test_new_file(self) -> None:
        data = self.author(NEW_AGENT)
        self.apply((".claude/agents/*",))
        self.assertEqual(self.read(".claude/agents/new.md"), "# New\nbody\n")
        self.assertFalse(os.stat(self.project / ".claude/agents/new.md").st_mode & 0o111)
        self.assert_applied(data)

    def test_deletion(self) -> None:
        data = self.author(DELETE_AGENT)
        self.apply((AGENT,))
        self.assertFalse(os.path.lexists(self.project / AGENT))
        self.assert_applied(data)

    def test_rename_with_edit_when_both_endpoints_are_authorized(self) -> None:
        data = self.author(RENAME_AGENT)
        plan = self.apply((AGENT, RENAMED))
        self.assertFalse(os.path.lexists(self.project / AGENT))
        self.assertEqual(self.read(RENAMED), AGENT_TEXT.replace("name: example", "name: renamed"))
        self.assertEqual(plan.changes, [{"kind": "rename", "old_path": AGENT, "new_path": RENAMED}])
        self.assert_applied(data)

    def test_pure_rename(self) -> None:
        data = self.author(PURE_RENAME)
        self.apply((".claude/agents/*.md",))
        self.assertEqual(self.read(RENAMED), AGENT_TEXT)
        self.assertFalse(os.path.lexists(self.project / AGENT))
        self.assert_applied(data)

    def test_multiple_sections_in_one_artifact(self) -> None:
        data = self.author(RENAME_AGENT + EDIT_SKILL + NEW_AGENT)
        self.apply((AGENT, RENAMED, SKILL, ".claude/agents/new.md"))
        self.assertEqual(self.read(SKILL), SKILL_FINAL)
        self.assertTrue((self.project / RENAMED).is_file())
        self.assertTrue((self.project / ".claude/agents/new.md").is_file())
        self.assert_applied(data)

    def test_missing_final_newline_is_preserved(self) -> None:
        data = self.author(
            f"diff --git a/{SKILL} b/{SKILL}\n--- a/{SKILL}\n+++ b/{SKILL}\n@@ -3 +3 @@\n"
            "-status: draft\n+status: final\n\\ No newline at end of file\n"
        )
        self.apply((SKILL,))
        self.assertEqual(self.read(SKILL), SKILL_FINAL.rstrip("\n"))
        self.assert_applied(data)

    def test_executable_file_edit_keeps_its_declared_mode(self) -> None:
        hook = self.write(".claude/hooks/check.sh", "#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        self.author(
            "diff --git a/.claude/hooks/check.sh b/.claude/hooks/check.sh\nindex 1111111..2222222 100755\n"
            "--- a/.claude/hooks/check.sh\n+++ b/.claude/hooks/check.sh\n@@ -1,2 +1,2 @@\n #!/bin/sh\n-exit 0\n+exit 1\n"
        )
        self.apply((".claude/hooks/check.sh",))
        self.assertEqual(self.read(".claude/hooks/check.sh"), "#!/bin/sh\nexit 1\n")
        self.assertTrue(os.stat(hook).st_mode & 0o111)

    def test_check_validates_without_mutating(self) -> None:
        data = self.author(EDIT_SKILL)
        plan = protected_patch.plan_patch(self.project, (SKILL,))
        self.assertEqual(plan.expected, {SKILL: SKILL_FINAL})
        self.assertEqual(self.read(SKILL), SKILL_TEXT)
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)

    def test_glob_authorization_semantics(self) -> None:
        self.assertTrue(protected_patch.is_authorized(".claude/agents/a.md", (".claude/agents/*",)))
        self.assertFalse(protected_patch.is_authorized(".claude/agents/x/a.md", (".claude/agents/*",)))
        self.assertTrue(protected_patch.is_authorized(".claude/agents/x/a.md", (".claude/agents/**",)))
        self.assertFalse(protected_patch.is_authorized(".claude/agents.md", (".claude/agents/**",)))
        self.assertFalse(protected_patch.is_authorized(".claude/agents/a.mdx", (".claude/agents/a.md",)))


class MixedWorkflowTests(RepoFixture):
    def test_direct_ordinary_edit_plus_protected_handoff(self) -> None:
        # Claude edits the ordinary file directly and authors only the protected changes.
        self.write(ORDINARY, "hello from claude\n")
        self.author(RENAME_AGENT + EDIT_SKILL)
        self.apply((AGENT, RENAMED, SKILL))
        self.assertEqual(self.read(ORDINARY), "hello from claude\n")
        status = run_git(self.project, "status", "--porcelain=v1", "-uall").splitlines()
        self.assertEqual(
            sorted(status),
            sorted([f" D {AGENT}", f" M {SKILL}", f" M {ORDINARY}", f"?? {RENAMED}"]),
        )

    def test_ordinary_file_in_artifact_rejects_the_whole_artifact(self) -> None:
        self.assert_rejected(EDIT_SKILL + EDIT_ORDINARY, message="edit ordinary repository files directly")
        self.assertEqual(self.read(SKILL), SKILL_TEXT)

    def test_rejected_artifact_can_be_corrected_and_then_applied(self) -> None:
        self.assert_rejected(EDIT_SKILL.replace("status: draft", "status: stale"))
        self.author(EDIT_SKILL)
        self.apply((SKILL,))
        self.assertEqual(self.read(SKILL), SKILL_FINAL)


class AuthorizationRejectionTests(RepoFixture):
    def test_unauthorized_path(self) -> None:
        self.assert_rejected(EDIT_SKILL, (AGENT,), "not authorized")

    def test_rename_requires_both_endpoints(self) -> None:
        self.assert_rejected(RENAME_AGENT, (AGENT,), f"{RENAMED}: not authorized")
        self.assert_rejected(RENAME_AGENT, (RENAMED,), f"{AGENT}: not authorized")

    def test_normal_files_are_not_routed_through_the_handoff(self) -> None:
        for path in (ORDINARY, "CLAUDE.md", ".git/config", ".claude", ".claudex/a.md"):
            with self.subTest(path=path):
                self.assert_rejected(edit_patch(path))

    def test_absolute_home_and_traversal_paths(self) -> None:
        for path in (
            "/etc/passwd",
            str(self.global_settings),
            "~/.claude/settings.json",
            ".claude/../src/example.txt",
            ".claude/agents/../../../outside.md",
            "../other-repo/.claude/agents/example.md",
            ".claude/./agents/example.md",
            "C:/Config/.claude/settings.json",
        ):
            with self.subTest(path=path):
                self.assert_rejected(edit_patch(path))
        self.assertEqual(self.global_settings.read_text(encoding="utf-8"), '{"synthetic": true}\n')

    def test_fake_home_global_settings_are_rejected(self) -> None:
        patch = (
            "diff --git a/~/.claude/settings.json b/~/.claude/settings.json\n--- a/~/.claude/settings.json\n"
            '+++ b/~/.claude/settings.json\n@@ -1 +1 @@\n-{"synthetic": true}\n+{"synthetic": false}\n'
        )
        self.assert_rejected(patch)
        self.assertEqual(self.global_settings.read_text(encoding="utf-8"), '{"synthetic": true}\n')

    def test_project_that_is_the_home_directory_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": str(self.project)}):
            self.assert_rejected(EDIT_SKILL, message="global configuration")

    def test_project_under_home_ssh_is_refused(self) -> None:
        nested = self.home / ".ssh" / "nested-project"
        (nested / ".claude/skills/example").mkdir(parents=True)
        run_git(nested, "init", "-q", "-b", "main")
        (nested / ".claude/skills/example/SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
        run_git(nested, "add", "-A")
        run_git(nested, "commit", "-q", "-m", "seed")
        (nested / protected_patch.ARTIFACT_NAME).write_text(EDIT_SKILL, encoding="utf-8")
        with self.assertRaises(protected_patch.PatchRejected) as caught:
            protected_patch.plan_patch(nested, (SKILL,))
        self.assertIn("global configuration", str(caught.exception))
        self.assertEqual((nested / SKILL).read_text(encoding="utf-8"), SKILL_TEXT)

    def test_quoted_or_whitespace_paths_are_rejected(self) -> None:
        self.assert_rejected('diff --git "a/.claude/a b.md" "b/.claude/a b.md"\n', message="expected 'diff --git")
        self.assert_rejected(edit_patch(".claude/agents/a$b.md"), message="unsupported characters")

    def test_unsafe_authorizations_are_configuration_errors(self) -> None:
        self.author(EDIT_SKILL)
        for pattern in ("", "~/.claude/settings.json", "/etc", "../x/.claude/a", ".claude", "src/**",
                        ".claude/[ab].md", ".claude/{a,b}.md", ".claude/./a.md"):
            with self.subTest(pattern=pattern), self.assertRaises(protected_patch.ConfigurationError):
                self.apply((pattern,))
        with self.assertRaises(protected_patch.ConfigurationError):
            self.apply(())


class SensitivePathRejectionTests(RepoFixture):
    """Credentials, secrets, auth and token material is never handled by this mechanism,
    even when an authorization would otherwise be broad enough to cover it."""

    SENSITIVE_PATHS = (
        ".claude/.ssh/id_rsa",
        ".claude/ssh/config",
        ".claude/credentials.json",
        ".claude/.credentials",
        ".claude/secrets/api.md",
        ".claude/agents/auth/token.md",
        ".claude/agents/token.md",
        ".claude/agents/.netrc",
        ".claude/agents/deploy.pem",
        ".claude/agents/id_ed25519",
    )

    def test_sensitive_authored_patch_paths_are_rejected_under_a_broad_authorization(self) -> None:
        # ".claude/**" (the default authorization here) would otherwise authorize every one
        # of these paths; the sensitivity check must still fail closed before that.
        for path in self.SENSITIVE_PATHS:
            with self.subTest(path=path):
                self.assert_rejected(edit_patch(path), message="credentials, secrets, auth or token")

    def test_sensitive_allow_claude_path_authorizations_are_configuration_errors(self) -> None:
        data = self.author(EDIT_SKILL)
        for path in self.SENSITIVE_PATHS:
            with self.subTest(path=path):
                with self.assertRaises(protected_patch.ConfigurationError):
                    self.apply((path,))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)

    def test_sensitive_rename_source_is_rejected(self) -> None:
        # A rename whose *source* names credential/token-like material is refused even
        # though the destination is an ordinary, otherwise-authorized agent path.
        self.write(".claude/agents/credentials.json", '{"token": "x"}\n')
        patch = (
            "diff --git a/.claude/agents/credentials.json b/.claude/agents/renamed.md\n"
            "rename from .claude/agents/credentials.json\nrename to .claude/agents/renamed.md\n"
        )
        self.assert_rejected(patch, (".claude/**",), message="credentials, secrets, auth or token")

    def test_sensitive_rename_destination_is_rejected(self) -> None:
        # A rename whose *destination* names credential/token-like material is refused even
        # though the source is an ordinary, otherwise-authorized agent path.
        patch = (
            f"diff --git a/{AGENT} b/.claude/agents/token.md\n"
            f"rename from {AGENT}\nrename to .claude/agents/token.md\n"
        )
        self.assert_rejected(patch, (".claude/**",), message="credentials, secrets, auth or token")


class SymlinkRejectionTests(RepoFixture):
    def setUp(self) -> None:
        super().setUp()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "example.md").write_text(AGENT_TEXT, encoding="utf-8")

    def test_symlinked_directory_escaping_the_project(self) -> None:
        (self.project / ".claude/linked").symlink_to(self.outside)
        self.assert_rejected(edit_patch(".claude/linked/example.md"), message="symlinks are not allowed")
        self.assertEqual((self.outside / "example.md").read_text(encoding="utf-8"), AGENT_TEXT)

    def test_dangling_symlink_as_new_file_target(self) -> None:
        (self.project / ".claude/agents/new.md").symlink_to(self.outside / "missing.md")
        self.assert_rejected(NEW_AGENT, message="symlinks are not allowed")
        self.assertFalse((self.outside / "missing.md").exists())

    def test_dangling_symlinked_directory(self) -> None:
        (self.project / ".claude/gone").symlink_to(self.outside / "missing-dir")
        patch = NEW_AGENT.replace(".claude/agents/new.md", ".claude/gone/new.md")
        self.assert_rejected(patch, message="symlinks are not allowed")
        self.assertFalse((self.outside / "missing-dir").exists())

    def test_symlinked_file_inside_the_project(self) -> None:
        (self.project / ".claude/agents/alias.md").symlink_to(self.project / AGENT)
        self.assert_rejected(edit_patch(".claude/agents/alias.md"), message="symlinks are not allowed")

    def test_rename_onto_a_symlink(self) -> None:
        (self.project / RENAMED).symlink_to(self.outside / "example.md")
        self.assert_rejected(RENAME_AGENT, message="symlinks are not allowed")

    def test_symlinked_protected_root(self) -> None:
        run_git(self.project, "mv", ".claude", "config")
        (self.project / ".claude").symlink_to(self.project / "config")
        self.assert_rejected(EDIT_SKILL, message="symlinks are not allowed")

    def test_symlinked_protected_root_escaping_the_project(self) -> None:
        run_git(self.project, "mv", ".claude", "config")
        (self.project / ".claude").symlink_to(self.outside)
        self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.ConfigurationError):
            self.apply((SKILL,))

    def test_symlinked_artifact(self) -> None:
        target = self.base / "authored.patch"
        target.write_text(EDIT_SKILL, encoding="utf-8")
        (self.project / protected_patch.ARTIFACT_NAME).symlink_to(target)
        with self.assertRaises(protected_patch.PatchRejected):
            self.apply()
        self.assertEqual(self.read(SKILL), SKILL_TEXT)
        self.assertEqual(target.read_text(encoding="utf-8"), EDIT_SKILL)


class ContentRejectionTests(RepoFixture):
    def test_binary_patches(self) -> None:
        self.assert_rejected(f"diff --git a/{SKILL} b/{SKILL}\nindex 1111111..2222222\nGIT binary patch\nliteral 1\nAcmZQz0\n",
                             message="binary")
        self.assert_rejected(f"diff --git a/{SKILL} b/{SKILL}\nBinary files a/{SKILL} and b/{SKILL} differ\n",
                             message="binary")
        self.assert_rejected(EDIT_SKILL.encode("utf-8") + b"\0", message="binary")
        self.assert_rejected(EDIT_SKILL.encode("utf-8").replace(b"final", b"\xff\xfe"), message="UTF-8")

    def test_binary_target_file(self) -> None:
        (self.project / SKILL).write_bytes(b"\0\x01\x02\n")
        self.assert_rejected(edit_patch(SKILL), message="binary")

    def test_mode_and_ownership_manipulation(self) -> None:
        cases = (
            f"diff --git a/{SKILL} b/{SKILL}\nold mode 100644\nnew mode 100755\n",
            NEW_AGENT.replace("new file mode 100644", "new file mode 100755"),
            NEW_AGENT.replace("new file mode 100644", "new file mode 120000"),
            NEW_AGENT.replace("new file mode 100644", "new file mode 160000"),
            DELETE_AGENT.replace("deleted file mode 100644", "deleted file mode 100755"),
            EDIT_SKILL.replace(f"--- a/{SKILL}", f"index 1111111..2222222 100755\n--- a/{SKILL}"),
            f"diff --git a/{AGENT} b/{RENAMED}\ncopy from {AGENT}\ncopy to {RENAMED}\n",
        )
        for patch in cases:
            with self.subTest(patch=patch.splitlines()[1]):
                self.assert_rejected(patch)

    def test_stale_context(self) -> None:
        self.write(SKILL, SKILL_TEXT.replace("draft", "reviewed"))
        self.assert_rejected(EDIT_SKILL, message="stale")

    def test_offset_hunk_is_stale_even_when_git_would_accept_it(self) -> None:
        self.write(SKILL, "# Preface\n" + SKILL_TEXT)
        self.assert_rejected(EDIT_SKILL, message="stale")

    def test_stale_structural_state(self) -> None:
        self.write(".claude/agents/new.md", "already here\n")
        self.assert_rejected(NEW_AGENT, message="already exists")
        (self.project / ".claude/agents/new.md").unlink()
        self.write(RENAMED, "occupied\n")
        self.assert_rejected(RENAME_AGENT, message="destination already exists")
        (self.project / RENAMED).unlink()
        self.write(AGENT, AGENT_TEXT + "extra\n")
        self.assert_rejected(DELETE_AGENT, message="whole current content")
        (self.project / AGENT).unlink()
        self.assert_rejected(RENAME_AGENT, message="does not exist")

    def test_malformed_patches(self) -> None:
        cases = {
            "empty": "",
            "whitespace only": "\n\n",
            "preamble": "Here is the patch:\n" + EDIT_SKILL,
            "no final newline": EDIT_SKILL.rstrip("\n"),
            "truncated hunk": EDIT_SKILL.replace("+status: final\n", ""),
            "extra hunk line": EDIT_SKILL + "+status: extra\n",
            "malformed hunk header": EDIT_SKILL.replace("@@ -1,3 +1,3 @@", "@@ -1,3 @@"),
            "inconsistent new start": EDIT_SKILL.replace("@@ -1,3 +1,3 @@", "@@ -1,3 +2,3 @@"),
            "empty hunk": f"diff --git a/{SKILL} b/{SKILL}\n--- a/{SKILL}\n+++ b/{SKILL}\n@@ -1,0 +1,0 @@\n",
            "duplicate section": EDIT_SKILL + EDIT_SKILL,
            "rename overlaps edit": RENAME_AGENT + edit_patch(AGENT),
            "mismatched rename headers": RENAME_AGENT.replace(f"rename to {RENAMED}", "rename to .claude/agents/other.md"),
            "half rename": RENAME_AGENT.replace(f"rename to {RENAMED}\n", ""),
            "paths differ without rename": edit_patch(SKILL).replace(f"b/{SKILL}", f"b/{AGENT}", 1),
            "mismatched --- path": EDIT_SKILL.replace(f"--- a/{SKILL}", f"--- a/{AGENT}"),
            "missing +++": EDIT_SKILL.replace(f"+++ b/{SKILL}\n", ""),
            "hunks without file headers": EDIT_SKILL.replace(f"--- a/{SKILL}\n+++ b/{SKILL}\n", ""),
            "modification without hunks": f"diff --git a/{SKILL} b/{SKILL}\n",
            "unknown header": EDIT_SKILL.replace(f"--- a/{SKILL}", f"dissimilarity index 90%\n--- a/{SKILL}"),
            "trailing garbage": EDIT_SKILL + "garbage\n",
            "misplaced no-newline marker": EDIT_SKILL.replace(
                " # Example skill\n", " # Example skill\n\\ No newline at end of file\n"
            ),
            "new file with wrong markers": NEW_AGENT.replace("--- /dev/null", "--- a/.claude/agents/new.md"),
            "deletion with wrong markers": DELETE_AGENT.replace("+++ /dev/null", f"+++ b/{AGENT}"),
            "added and deleted": NEW_AGENT.replace("new file mode 100644", "new file mode 100644\ndeleted file mode 100644"),
            "similarity without rename": EDIT_SKILL.replace(f"--- a/{SKILL}", f"similarity index 90%\n--- a/{SKILL}"),
        }
        for name, patch in cases.items():
            with self.subTest(case=name):
                self.assert_rejected(patch)
        self.assertIn("may only follow the last line", str(self.assert_rejected(cases["misplaced no-newline marker"])))

    def test_missing_or_tracked_artifact(self) -> None:
        with self.assertRaises(protected_patch.PatchRejected):
            self.apply()
        self.author(EDIT_SKILL)
        run_git(self.project, "add", "--", protected_patch.ARTIFACT_NAME)
        with self.assertRaises(protected_patch.PatchRejected) as caught:
            self.apply()
        self.assertIn("untracked", str(caught.exception))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)


class ZeroContextPatchTests(RepoFixture):
    """Coverage for --unidiff-zero: zero-context ("-U0"-shaped) hunks like "@@ -8,1 +8,1 @@"
    with no leading or trailing context line. Validation here still pins every hunk to its
    exact stated position before git ever runs, so these fail closed exactly like
    context-bearing hunks do."""

    def setUp(self) -> None:
        super().setUp()
        self.write(ZERO_CONTEXT_TARGET, ZERO_CONTEXT_TEXT)

    def test_check_passes_without_mutation(self) -> None:
        data = self.author(EDIT_ZERO_CONTEXT)
        plan = protected_patch.plan_patch(self.project, (ZERO_CONTEXT_TARGET,))
        self.assertEqual(plan.expected, {ZERO_CONTEXT_TARGET: ZERO_CONTEXT_FINAL})
        self.assertEqual(self.read(ZERO_CONTEXT_TARGET), ZERO_CONTEXT_TEXT)
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)

    def test_apply_applies_exactly_and_archives_the_artifact(self) -> None:
        data = self.author(EDIT_ZERO_CONTEXT)
        plan = self.apply((ZERO_CONTEXT_TARGET,))
        self.assertEqual(self.read(ZERO_CONTEXT_TARGET), ZERO_CONTEXT_FINAL)
        self.assertEqual(
            plan.changes, [{"kind": "modify", "old_path": ZERO_CONTEXT_TARGET, "new_path": ZERO_CONTEXT_TARGET}]
        )
        self.assertFalse(os.path.lexists(self.project / protected_patch.ARTIFACT_NAME))
        self.assertEqual(self.archive.read_bytes(), data)

    def test_wrong_old_content_is_rejected(self) -> None:
        patch = EDIT_ZERO_CONTEXT.replace("-line 8\n", "-line ate\n")
        self.assert_rejected(patch, (ZERO_CONTEXT_TARGET,), message="stale or mismatched context")

    def test_wrong_position_is_rejected(self) -> None:
        patch = EDIT_ZERO_CONTEXT.replace("@@ -8,1 +8,1 @@", "@@ -3,1 +3,1 @@")
        self.assert_rejected(patch, (ZERO_CONTEXT_TARGET,), message="stale or mismatched context")

    def test_stale_source_is_rejected(self) -> None:
        # Every line shifts down by one after authoring; the hunk's stated position 8 now
        # holds different content, so it must be rejected even though it is fully unambiguous.
        self.write(ZERO_CONTEXT_TARGET, "line 0\n" + ZERO_CONTEXT_TEXT)
        self.assert_rejected(EDIT_ZERO_CONTEXT, (ZERO_CONTEXT_TARGET,), message="stale or mismatched context")

    def test_unauthorized_path_is_rejected(self) -> None:
        self.assert_rejected(EDIT_ZERO_CONTEXT, (SKILL,), message="not authorized")

    def test_malformed_hunk_is_rejected(self) -> None:
        patch = EDIT_ZERO_CONTEXT.replace("-line 8\n+line eight\n", "-line 8\n-line 9\n+line eight\n")
        self.assert_rejected(patch, (ZERO_CONTEXT_TARGET,), message="more lines than its header declares")


class RenameWithZeroContextEditTests(RepoFixture):
    """A rename with an edit expressed as a zero-context hunk, combined with an unrelated
    second protected file edit in the same artifact."""

    def setUp(self) -> None:
        super().setUp()
        self.write(ZERO_RENAME_SOURCE, ZERO_RENAME_SOURCE_TEXT)

    def test_check_and_apply_rename_plus_second_file_edit(self) -> None:
        artifact = RENAME_WITH_ZERO_CONTEXT_EDIT + EDIT_SKILL
        data = self.author(artifact)
        patterns = (ZERO_RENAME_SOURCE, ZERO_RENAME_DEST, SKILL)

        plan = protected_patch.plan_patch(self.project, patterns)
        self.assertEqual(
            plan.changes,
            [
                {"kind": "rename", "old_path": ZERO_RENAME_SOURCE, "new_path": ZERO_RENAME_DEST},
                {"kind": "modify", "old_path": SKILL, "new_path": SKILL},
            ],
        )
        self.assertTrue(os.path.lexists(self.project / ZERO_RENAME_SOURCE))
        self.assertFalse(os.path.lexists(self.project / ZERO_RENAME_DEST))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)

        self.apply(patterns)
        self.assertFalse(os.path.lexists(self.project / ZERO_RENAME_SOURCE))
        self.assertEqual(self.read(ZERO_RENAME_DEST), ZERO_RENAME_DEST_TEXT)
        self.assertEqual(self.read(SKILL), SKILL_FINAL)
        self.assertFalse(os.path.lexists(self.project / protected_patch.ARTIFACT_NAME))
        self.assertEqual(self.archive.read_bytes(), data)
        # Unrelated protected content is untouched by the rename-plus-edit artifact.
        self.assertEqual(self.read(AGENT), AGENT_TEXT)


FAKE_GIT = """#!{python}
import subprocess
import sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["apply"] and {mode!r} == "refuse-check" and "--check" in args:
    sys.stderr.write("error: synthetic refusal\\n")
    raise SystemExit(1)
if args[:1] == ["apply"] and "--check" not in args and {mode!r} == "tamper":
    code = subprocess.call(["git", *args])
    Path({target!r}).write_text("tampered\\n", encoding="utf-8")
    raise SystemExit(code)
if args[:1] == ["apply"] and "--check" not in args and {mode!r} == "fail":
    raise SystemExit(1)
raise SystemExit(subprocess.call(["git", *args]))
"""


class MechanicalApplicationTests(RepoFixture):
    def fake_git(self, mode: str, target: str = SKILL) -> str:
        path = self.base / f"fake-git-{mode}"
        path.write_text(
            FAKE_GIT.format(python=sys.executable, mode=mode, target=str(self.project / target)), encoding="utf-8"
        )
        path.chmod(0o700)
        return str(path)

    def test_git_apply_check_refusal_rejects_without_mutation(self) -> None:
        data = self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.PatchRejected) as caught:
            self.apply((SKILL,), git=self.fake_git("refuse-check"))
        self.assertIn("git apply --check", str(caught.exception))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)

    def test_result_that_differs_from_the_patch_fails_without_repair(self) -> None:
        data = self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.ApplicationFailed) as caught:
            self.apply((SKILL,), git=self.fake_git("tamper"))
        self.assertIn(f"{SKILL}: content differs", str(caught.exception))
        self.assertEqual(self.read(SKILL), "tampered\n")
        self.assertEqual((self.project / protected_patch.ARTIFACT_NAME).read_bytes(), data)
        self.assertFalse(self.archive.exists())

    def test_untouched_protected_file_change_fails_verification(self) -> None:
        self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.ApplicationFailed) as caught:
            self.apply((SKILL,), git=self.fake_git("tamper", AGENT))
        self.assertIn(f"{AGENT}: untouched protected file changed", str(caught.exception))

    def test_failed_git_apply_is_an_application_failure(self) -> None:
        self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.ApplicationFailed):
            self.apply((SKILL,), git=self.fake_git("fail"))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)

    def test_archive_must_be_new_and_outside_the_project(self) -> None:
        self.author(EDIT_SKILL)
        for archive in (self.project / "applied.patch", self.project / ".claude/applied.patch"):
            with self.subTest(archive=archive), self.assertRaises(protected_patch.ConfigurationError):
                protected_patch.apply_patch(self.project, (SKILL,), archive)
        self.archive.write_text("previous\n", encoding="utf-8")
        with self.assertRaises(protected_patch.ConfigurationError):
            self.apply((SKILL,))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)

    def test_project_must_be_a_git_root(self) -> None:
        self.author(EDIT_SKILL)
        with self.assertRaises(protected_patch.ConfigurationError):
            protected_patch.plan_patch(self.project / ".claude", (".claude/**",))


class ProtectedPatchCliTests(RepoFixture):
    def cli(self, *args: str) -> tuple:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = protected_patch.main([*args, "--project", str(self.project)])
        return code, json.loads(output.getvalue())

    def test_check_then_apply(self) -> None:
        self.author(EDIT_SKILL)
        code, report = self.cli("check", "--allow-claude-path", SKILL)
        self.assertEqual((code, report["status"]), (0, "valid"))
        self.assertEqual(self.read(SKILL), SKILL_TEXT)
        code, report = self.cli("apply", "--allow-claude-path", SKILL, "--archive", str(self.archive))
        self.assertEqual((code, report["status"]), (0, "applied"))
        self.assertEqual(report["changes"], [{"kind": "modify", "old_path": SKILL, "new_path": SKILL}])
        self.assertEqual(self.read(SKILL), SKILL_FINAL)

    def test_rejection_reports_the_error_for_the_next_claude_cycle(self) -> None:
        self.author(EDIT_SKILL)
        code, report = self.cli("apply", "--allow-claude-path", AGENT, "--archive", str(self.archive))
        self.assertEqual((code, report["status"]), (protected_patch.EXIT_REJECTED, "rejected"))
        self.assertIn(f"{SKILL}: not authorized", report["error"])

    def test_configuration_errors(self) -> None:
        self.author(EDIT_SKILL)
        code, report = self.cli("apply", "--allow-claude-path", SKILL)
        self.assertEqual((code, report["status"]), (protected_patch.EXIT_CONFIGURATION, "configuration-error"))
        code, report = self.cli("check", "--allow-claude-path", "~/.claude/settings.json")
        self.assertEqual(code, protected_patch.EXIT_CONFIGURATION)

    def test_runs_as_a_script(self) -> None:
        self.author(EDIT_SKILL)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "protected_patch.py"), "check", "--project", str(self.project),
             "--allow-claude-path", SKILL],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "valid")


class PromptContractTests(unittest.TestCase):
    def test_contract_is_deterministic_and_explicit(self) -> None:
        contract = protected_patch.prompt_contract((AGENT, RENAMED))
        self.assertEqual(contract, protected_patch.prompt_contract((AGENT, RENAMED)))
        self.assertIn(f"- {AGENT}\n- {RENAMED}", contract)
        self.assertIn(protected_patch.ARTIFACT_NAME, contract)
        self.assertIn("Do not attempt to\ncreate, edit, delete or rename anything under `.claude/`", contract)
        self.assertIn("and nothing else", contract)
        self.assertIn("Never put ordinary files in it", contract)
        self.assertIn("rename from", contract)
        self.assertNotIn("mv ", contract)


if __name__ == "__main__":
    unittest.main()
