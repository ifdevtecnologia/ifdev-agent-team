"""Shared fixtures of the benchmark harness tests: fake `docker` and `claude` executables and
synthetic tasks, all generated in temporary directories. No test calls the model or Docker."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
# Test modules add it to sys.path before importing the `bench` package.
BENCH_DIR = REPO_ROOT / "benchmark"

AUTH_ENV = "BENCH_TEST_AUTH"
AUTH_SENTINEL = "synthetic-auth-sentinel-0123"

# Runs the container command locally: honours -w, -e (NAME=value or NAME passed through) and a
# leading `timeout N`, and logs every invocation as one JSON line.
FAKE_DOCKER = r'''
import json, os, subprocess, sys

argv = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps({"argv": argv}) + "\n")
if os.environ.get("FAKE_DOCKER_FORBID"):
    sys.exit(99)
if argv[:1] == ["inspect"]:
    sys.exit(0 if os.environ.get("FAKE_DOCKER_INSPECT_PRESENT") else 1)
if not argv or argv[0] != "run":
    sys.exit(0)
with_value = {"--name", "--user", "--network", "--cap-drop", "--security-opt", "--mount", "-w", "-e",
              "--pids-limit", "--memory", "--memory-swap", "--cpus"}
env, workdir, i = {}, None, 1
while i < len(argv):
    arg = argv[i]
    if arg in ("--rm", "-i"):
        i += 1
        continue
    if arg in with_value:
        value = argv[i + 1]
        if arg == "-w":
            workdir = value
        elif arg == "-e":
            key, sep, val = value.partition("=")
            env[key] = val if sep else os.environ.get(key, "")
        i += 2
        continue
    break
command = argv[i + 1:]
child_env = {"PATH": os.environ["FAKE_CONTAINER_PATH"]}
child_env.update({k: v for k, v in os.environ.items() if k.startswith("FAKE_CLAUDE_")})
child_env.update(env)
timeout = None
if command[:1] == ["timeout"]:
    timeout, command = float(command[1]), command[2:]
try:
    sys.exit(subprocess.run(command, cwd=workdir, env=child_env, timeout=timeout).returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
'''

# Emits a stream-json log; FAKE_CLAUDE_MODE selects the ending and FAKE_CLAUDE_SCRIPT is a shell
# command run in the workspace (the agent's "work").
FAKE_CLAUDE = r'''
import json, os, subprocess, sys, time

prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
                                 "env": {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR", "IFDEV_AGENT_NOTIFY")},
                                 "env_names": sorted(os.environ)}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "solve")
def emit(event):
    print(json.dumps(event), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "x"})
emit({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "SECRET-FREE-TEXT plan"},
    {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "reviewer", "prompt": "SECRET-FREE-TEXT"}}]}})
if mode == "sleep":
    time.sleep(30)
script = os.environ.get("FAKE_CLAUDE_SCRIPT")
if script:
    subprocess.run(["sh", "-c", script], check=False)
if mode == "noresult":
    sys.exit(1)
if mode == "garbage":
    print("this line is not JSON", flush=True)
if mode == "instrument":
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "dev", "prompt": "issue example-repo#12 phase=execute"}}]}})
if mode == "leak":
    print(json.dumps({"type": "user", "tool_result": os.environ.get("BENCH_TEST_AUTH", "")}), flush=True)
subtype = "error_max_budget_usd" if mode == "budget" else "success"
text = "Report\nStatus: BLOCKED" if mode == "blocked" else "Report\nStatus: COMPLETE SECRET-FREE-TEXT"
emit({"type": "result", "subtype": subtype, "is_error": mode == "budget", "total_cost_usd": 0.42,
      "duration_ms": 1234, "num_turns": 7, "result": text,
      "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "note": "SECRET-FREE-TEXT"},
      "modelUsage": {"claude-test-1": {"costUSD": 0.42, "inputTokens": 10}}})
'''

TASK_YAML = """\
# Synthetic task for the harness tests.
id: {task_id}
type: bugfix
stack: shell
image: bench-shell:test
commands:
  test: sh tests/check.sh
  hidden: sh {{test}}
expected_files:
  - app.txt
protected_paths:
  - tests
criteria:
  - {task_id}-C1
  - {task_id}-C2
hidden_tests:
  test_fixed.sh: {task_id}-C1
  test_note.sh: {task_id}-C2
"""


def write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_task(tasks_dir: Path, task_id: str = "T01", yaml_text: str | None = None) -> Path:
    """A shell task: app.txt must say 'fixed' (C1) and NOTE.txt must exist (C2)."""
    task = tasks_dir / task_id
    (task / "snapshot" / "tests").mkdir(parents=True)
    (task / "hidden").mkdir()
    (task / "reference").mkdir()
    (task / "task.yaml").write_text(yaml_text or TASK_YAML.format(task_id=task_id), encoding="utf-8")
    (task / "prompt.md").write_text("Fix app.txt so that it says fixed, and add NOTE.txt.\n", encoding="utf-8")
    (task / "snapshot" / "app.txt").write_text("broken\n", encoding="utf-8")
    (task / "snapshot" / "tests" / "check.sh").write_text("test -f app.txt\n", encoding="utf-8")
    (task / "hidden" / "test_fixed.sh").write_text("grep -q fixed app.txt\n", encoding="utf-8")
    (task / "hidden" / "test_note.sh").write_text("test -f NOTE.txt\n", encoding="utf-8")
    (task / "reference" / "app.txt").write_text("fixed\n", encoding="utf-8")
    (task / "reference" / "NOTE.txt").write_text("note\n", encoding="utf-8")

    return task


SOLVE_SCRIPT = "echo fixed > app.txt && echo note > NOTE.txt"


class BenchTestCase(unittest.TestCase):
    """Temporary HOME, tasks, runs and results directories, and fakes first on PATH."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bench-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.tasks_dir = self.tmp / "tasks"
        self.runs_dir = self.tmp / "runs"
        self.results_dir = self.tmp / "results"
        self.bin = self.tmp / "bin"

        for path in (self.home, self.tasks_dir, self.bin):
            path.mkdir()

        write_executable(self.bin / "docker", FAKE_DOCKER)
        write_executable(self.bin / "claude", FAKE_CLAUDE)
        self.docker_log = self.tmp / "docker.jsonl"
        self.claude_log = self.tmp / "claude.jsonl"
        system_path = os.environ.get("PATH", "/usr/bin:/bin")
        patcher = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin}{os.pathsep}{system_path}",
                "FAKE_CONTAINER_PATH": f"{self.bin}{os.pathsep}{system_path}",
                "FAKE_DOCKER_LOG": str(self.docker_log),
                "FAKE_CLAUDE_LOG": str(self.claude_log),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        for name in ("BENCH_TASKS_DIR", "BENCH_RUNS_DIR", AUTH_ENV, "FAKE_DOCKER_FORBID", "FAKE_CLAUDE_MODE", "FAKE_CLAUDE_SCRIPT"):
            os.environ.pop(name, None)

    def docker_calls(self) -> list[list[str]]:
        if not self.docker_log.exists():
            return []

        return [json.loads(line)["argv"] for line in self.docker_log.read_text().splitlines()]

    def claude_calls(self) -> list[dict]:
        if not self.claude_log.exists():
            return []

        return [json.loads(line) for line in self.claude_log.read_text().splitlines()]


def head_sha() -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
