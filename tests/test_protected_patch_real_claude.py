"""Real Claude Code fixture for the protected .claude/** patch handoff.

Opt-in because it launches a real, billed Claude Code session. Run it explicitly:

    IFDEV_REAL_CLAUDE_FIXTURE=1 python3 -m unittest tests.test_protected_patch_real_claude -v

It creates a synthetic temporary Git repository and runs Claude through
``run_claude.py`` in the normal supervised configuration (restricted, ``dontAsk``,
``--permission-prompts none``, strict MCP, the unchanged denylist, only file tools and no
Bash). Claude must edit ``src/example.txt`` directly and author one patch artifact for a
semantic edit+rename of ``.claude/agents/example.md`` and an edit of
``.claude/skills/example/SKILL.md``; the supervisor applicator then applies it and the test
proves the result exactly matches the artifact. It also proves an artifact requesting a
fake-HOME ``~/.claude/settings.json`` change is rejected, and that an artifact naming a
credential/token-like project ``.claude/**`` target (e.g. ``.claude/credentials.json``) is
rejected even under a broad authorization. The real HOME is only used by the Claude CLI for
its own existing authentication; this test never reads it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "codex/skills/supervise-claude/scripts"
ENABLED = os.environ.get("IFDEV_REAL_CLAUDE_FIXTURE") == "1"
CLAUDE = os.environ.get("IFDEV_REAL_CLAUDE_BIN", "claude")
TIMEOUT_SECONDS = 600


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
UNRELATED = ".claude/agents/unrelated.md"
ORDINARY = "src/example.txt"
AGENT_TEXT = "---\nname: example\ndescription: Example agent.\n---\nReview the example.\n"
SKILL_TEXT = "---\nname: example\ndescription: Example skill.\n---\nstatus: draft\n"
FILES = {
    AGENT: AGENT_TEXT,
    SKILL: SKILL_TEXT,
    UNRELATED: "---\nname: unrelated\n---\nLeave me alone.\n",
    ORDINARY: "status: draft\n",
}
EXPECTED_ORDINARY = "status: final\n"
EXPECTED_RENAMED = AGENT_TEXT.replace("name: example", "name: renamed")
EXPECTED_SKILL = SKILL_TEXT.replace("status: draft", "status: final")
AUTHORIZED = (AGENT, RENAMED, SKILL)

PROMPT = f"""<role>
You implement a small synthetic fixture task. This is a non-interactive supervised run: use only
granted tools, never ask for approval, and report a denied operation as a blocker.
</role>
<task>
Make exactly these three changes in this repository and nothing else:
1. Edit the ordinary file `{ORDINARY}` directly so its whole content is the single line
   `status: final` (followed by a newline).
2. Rename `{AGENT}` to `{RENAMED}` and, in the renamed file, change the line `name: example` to
   `name: renamed`. Every other line stays identical.
3. In `{SKILL}`, change the line `status: draft` to `status: final`. Every other line stays
   identical.
Do not touch `{UNRELATED}` or any other file. Changes 2 and 3 are protected; follow the
protected-path contract below for them.
</task>
<deliverable>
Report briefly which files you changed directly and what the artifact contains.
</deliverable>
"""


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
        cwd=cwd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout


def applicator(project: Path, action: str, *extra: str, env: dict | None = None, patterns: tuple = AUTHORIZED) -> tuple:
    args = [sys.executable, str(SCRIPTS / "protected_patch.py"), action, "--project", str(project)]
    for pattern in patterns:
        args += ["--allow-claude-path", pattern]
    result = subprocess.run(
        [*args, *extra], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=120,
    )
    return result.returncode, json.loads(result.stdout)


@unittest.skipUnless(ENABLED, "set IFDEV_REAL_CLAUDE_FIXTURE=1 to run the real Claude Code fixture")
class RealClaudeProtectedPatchFixture(unittest.TestCase):
    def setUp(self) -> None:
        # Enabled but unavailable is a failure, never a silent skip that looks like a pass.
        self.assertIsNotNone(shutil.which(CLAUDE), f"Claude Code CLI not found: {CLAUDE}")
        tmp = tempfile.TemporaryDirectory(prefix="ifdev-real-claude-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        self.run_dir = self.base / "run"
        self.run_dir.mkdir()
        git(self.project, "init", "-q", "-b", "main")
        for rel, content in FILES.items():
            path = self.project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "fixture")

    def read(self, rel: str) -> str:
        return (self.project / rel).read_text(encoding="utf-8")

    def test_mixed_direct_edit_and_protected_patch_handoff(self) -> None:
        version = subprocess.run([CLAUDE, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, timeout=60).stdout.strip()
        (self.run_dir / "claude-version.txt").write_text(version + "\n", encoding="utf-8")
        prompt = self.run_dir / "prompt.md"
        prompt.write_text(PROMPT, encoding="utf-8")
        log = self.run_dir / "claude.log"
        protected_before = protected_patch.snapshot(self.project)

        command = [sys.executable, str(SCRIPTS / "run_claude.py"), "--project", str(self.project),
                   "--prompt", str(prompt), "--log", str(log), "--claude", CLAUDE, "--model", "sonnet",
                   "--timeout-seconds", str(TIMEOUT_SECONDS)]
        for pattern in AUTHORIZED:
            command += ["--allow-claude-path", pattern]
        # stdin is closed: an interactive permission prompt could never be answered, so a
        # run that waited for one would hit the deadline instead of passing.
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=TIMEOUT_SECONDS + 60)
        transcript = log.read_text(encoding="utf-8") if log.exists() else ""
        context = f"claude {version}; exit {result.returncode}; stderr: {result.stderr[-2000:]}\nlog tail:\n{transcript[-4000:]}"
        self.assertEqual(result.returncode, 0, context)

        # Ordinary file: edited directly by Claude.
        self.assertEqual(self.read(ORDINARY), EXPECTED_ORDINARY, context)
        # Protected tree: untouched by Claude; its changes exist only in the artifact.
        self.assertEqual(protected_patch.snapshot(self.project), protected_before, context)
        artifact = self.project / protected_patch.ARTIFACT_NAME
        self.assertTrue(artifact.is_file() and not artifact.is_symlink(), context)
        authored = artifact.read_bytes()
        shutil.copyfile(artifact, self.run_dir / "authored.patch")

        code, report = applicator(self.project, "check")
        self.assertEqual((code, report["status"]), (0, "valid"), f"{report}\nartifact:\n{authored.decode('utf-8', 'replace')}")
        archive = self.run_dir / "applied.patch"
        code, report = applicator(self.project, "apply", "--archive", str(archive))
        self.assertEqual((code, report["status"]), (0, "applied"), report)
        self.assertEqual(
            sorted((c["kind"], c["old_path"], c["new_path"]) for c in report["changes"]),
            [("modify", SKILL, SKILL), ("rename", AGENT, RENAMED)],
        )

        # Exact result: the applied files equal the semantics Claude authored, and nothing else changed.
        self.assertEqual(archive.read_bytes(), authored)
        self.assertFalse(os.path.lexists(artifact))
        self.assertFalse(os.path.lexists(self.project / AGENT))
        self.assertEqual(self.read(RENAMED), EXPECTED_RENAMED)
        self.assertEqual(self.read(SKILL), EXPECTED_SKILL)
        self.assertEqual(self.read(UNRELATED), FILES[UNRELATED])
        self.assertEqual(self.read(ORDINARY), EXPECTED_ORDINARY)
        status = sorted(git(self.project, "status", "--porcelain=v1", "-uall").splitlines())
        self.assertEqual(status, sorted([f" D {AGENT}", f" M {SKILL}", f" M {ORDINARY}", f"?? {RENAMED}"]))

    def test_fake_home_global_settings_change_is_rejected(self) -> None:
        fake_home = self.base / "fake-home"
        settings = fake_home / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"synthetic": true}\n', encoding="utf-8")
        env = {**os.environ, "HOME": str(fake_home)}
        for target in ("~/.claude/settings.json", str(settings)):
            with self.subTest(target=target):
                artifact = self.project / protected_patch.ARTIFACT_NAME
                data = (
                    f"diff --git a/{target} b/{target}\n--- a/{target}\n+++ b/{target}\n"
                    '@@ -1 +1 @@\n-{"synthetic": true}\n+{"synthetic": false}\n'
                ).encode("utf-8")
                artifact.write_bytes(data)
                code, report = applicator(self.project, "apply", "--archive", str(self.run_dir / "never.patch"), env=env)
                self.assertEqual((code, report["status"]), (protected_patch.EXIT_REJECTED, "rejected"), report)
                self.assertEqual(artifact.read_bytes(), data)
                self.assertEqual(settings.read_text(encoding="utf-8"), '{"synthetic": true}\n')
                self.assertFalse((self.run_dir / "never.patch").exists())

    def test_sensitive_project_claude_target_is_rejected(self) -> None:
        # Even a broad ".claude/**" authorization must not let the supervisor apply a
        # patch naming credential/token-like material inside the project's own .claude/**.
        target = ".claude/credentials.json"
        artifact = self.project / protected_patch.ARTIFACT_NAME
        data = (
            f"diff --git a/{target} b/{target}\nnew file mode 100644\n--- /dev/null\n+++ b/{target}\n"
            '@@ -0,0 +1,1 @@\n+{"token": "x"}\n'
        ).encode("utf-8")
        artifact.write_bytes(data)
        code, report = applicator(
            self.project, "apply", "--archive", str(self.run_dir / "never-sensitive.patch"), patterns=(".claude/**",)
        )
        self.assertEqual((code, report["status"]), (protected_patch.EXIT_REJECTED, "rejected"), report)
        self.assertIn("credentials, secrets, auth or token", report["error"])
        self.assertEqual(artifact.read_bytes(), data)
        self.assertFalse((self.run_dir / "never-sensitive.patch").exists())


if __name__ == "__main__":
    unittest.main()
