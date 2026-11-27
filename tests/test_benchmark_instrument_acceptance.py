"""Acceptance tests of issue #96 (conditions B and C run without the process-metrics instrument) through the
runner's command-line entry point, started as a subprocess from benchmark/ like the documented command.

The real toolkit HEAD is exported and installed into each run's temporary HOME; `claude` and `docker` are the
fakes of benchmark_support, so no test calls the model or Docker. The fake agent probes the HOME it was given
and, through FAKE_CLAUDE_SCRIPT, can write extra stream events to stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from benchmark_support import (
    AUTH_ENV, AUTH_SENTINEL, BENCH_DIR, REPO_ROOT, SOLVE_SCRIPT, BenchTestCase, head_sha, make_task,
)

MODEL = "claude-test-model-1-0"
ZERO_MARKERS = {"first_lines": 0, "record_blocks": 0, "mentions": 0}
# Lists every file under the HOME's config that mentions the instrument, following the installer's links.
MENTIONS_PROBE = 'grep -RIilE "process[- ]metrics" "$HOME/.claude" > "$probe/mentions" 2>/dev/null; '
# Run by the fake agent inside the fake container: records what the run HOME exposes to the agent, then solves the task.
PROBE_SCRIPT = (
    'probe="$FAKE_CLAUDE_PROBE/$(basename "$(dirname "$HOME")" | cut -d- -f1)"; mkdir -p "$probe"; '
    'find "$HOME/.claude" -mindepth 1 > "$probe/config_entries" 2>/dev/null; '
    'ls -A "$HOME/.claude/skills" > "$probe/skills" 2>/dev/null; '
    f"{MENTIONS_PROBE}"
    'find -L "$HOME/.." -type d -name process-metrics > "$probe/skill_dirs" 2>/dev/null; '
    'cp CLAUDE.md "$probe/project_claude"; '
    f"{SOLVE_SCRIPT}"
)


def event_script(text: str) -> str:
    """A FAKE_CLAUDE_SCRIPT that solves the task and writes one assistant text event into the stream."""
    event = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})

    return f"{SOLVE_SCRIPT} && printf '%s\\n' '{event}'"


class InstrumentAcceptanceTests(BenchTestCase):
    def setUp(self) -> None:
        super().setUp()
        make_task(self.tasks_dir, "T01")
        self.prompts = self.tmp / "prompts"
        self.prompts.mkdir()
        self.project_claude = self.prompts / "project-claude.md"
        self.project_claude.write_text("# Project\nSynthetic shared project file.\n")
        (self.prompts / "prompt-template.md").write_text("Task:\n{{TASK_PROMPT}}\n")
        (self.prompts / "C-light.txt").write_text("Use light mode.\n")
        self.probe = self.tmp / "probe"
        self.probe.mkdir()
        os.environ[AUTH_ENV] = AUTH_SENTINEL
        os.environ["FAKE_CLAUDE_PROBE"] = str(self.probe)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_PROBE", None)

    def run_round(self, conditions: str) -> subprocess.CompletedProcess:
        argv = [
            "--round", "harness", "--round-id", "instrument", "--tasks", "T01", "--tasks-dir", str(self.tasks_dir),
            "--runs-dir", str(self.runs_dir), "--results-dir", str(self.results_dir), "--conditions", conditions,
            "--seed", "96", "--model", MODEL, "--effort", "high", "--run-cap-usd", "15", "--round-budget-usd", "100",
            "--toolkit-sha", head_sha(), "--auth-env", AUTH_ENV, "--project-claude", str(self.project_claude),
            "--prompt-template", str(self.prompts / "prompt-template.md"),
            "--light-line", str(self.prompts / "C-light.txt"), "--execute",
        ]

        return subprocess.run([sys.executable, "-m", "bench.runner", *argv], cwd=BENCH_DIR, env=dict(os.environ),
                              capture_output=True, text=True, timeout=900)

    def meta(self, run_id: str) -> dict:
        return json.loads((self.results_dir / "instrument" / run_id / "meta.json").read_text())

    def probed(self, run_id: str, name: str) -> str:
        path = self.probe / run_id / name

        return path.read_text() if path.exists() else ""

    def call_of(self, condition: str) -> dict:
        return next(call for call in self.claude_calls()
                    if Path(call["env"]["HOME"]).parent.name.startswith(f"T01_{condition}_r1-"))

    def prompt_of(self, condition: str) -> str:
        return self.call_of(condition)["prompt"]

    def argv_of(self, condition: str) -> list[str]:
        """The fake agent's arguments with the per-run session id masked."""
        argv = list(self.call_of(condition)["argv"])
        index = argv.index("--session-id")
        argv[index + 1] = "<session>"

        return argv

    def test_mentions_probe_finds_a_mention_reached_through_a_link(self) -> None:
        home = self.tmp / "probe-home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").symlink_to(REPO_ROOT / "dist" / "claude" / "CLAUDE.md")
        probe = self.tmp / "probe-control"
        probe.mkdir()

        subprocess.run(["bash", "-c", MENTIONS_PROBE], env={**os.environ, "HOME": str(home), "probe": str(probe)},
                       check=False)

        self.assertIn("CLAUDE.md", (probe / "mentions").read_text())

    def test_ac1_ac2_ac4_ac6_round_a_b_c_runs_b_and_c_without_the_instrument_and_differs_only_by_the_treatment(self) -> None:
        os.environ["FAKE_CLAUDE_SCRIPT"] = PROBE_SCRIPT

        result = self.run_round("A,B,C")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("instrument leak", result.stdout)
        metas = {condition: self.meta(f"T01_{condition}_r1") for condition in "ABC"}

        for condition, meta in metas.items():
            run_id = f"T01_{condition}_r1"

            with self.subTest(condition=condition):
                # AC 4 and 6: the fields the smoke round reads are in every run's meta.json.
                self.assertEqual(meta["status"], "finished")
                self.assertTrue(meta["o1_success"], meta)
                self.assertIn("toolkit_treatment", meta)
                self.assertIs(meta["instrument_leak"], False)
                self.assertEqual(meta["stream"]["instrument_markers"], ZERO_MARKERS)
                # AC 1: what the agent saw at run time has neither the skill nor any mention of it.
                self.assertNotIn("process-metrics", self.probed(run_id, "skills").split())
                self.assertEqual(self.probed(run_id, "mentions"), "")
                self.assertEqual(self.probed(run_id, "skill_dirs"), "")
                # AC 2: the project file is the same, byte for byte, in every condition.
                self.assertEqual(self.probed(run_id, "project_claude"), self.project_claude.read_text())

        # Condition A installs nothing, so it has nothing to remove: its config directory is empty.
        self.assertIsNone(metas["A"]["toolkit_treatment"])
        self.assertEqual(self.probed("T01_A_r1", "config_entries"), "")

        for condition in "BC":
            with self.subTest(condition=condition):
                treatment = metas[condition]["toolkit_treatment"]
                self.assertTrue(self.probed(f"T01_{condition}_r1", "config_entries"), "the toolkit is installed")
                self.assertTrue(self.probed(f"T01_{condition}_r1", "skills").split(), "other skills stay installed")
                self.assertEqual(treatment["instrument"], "removed")
                self.assertEqual([(e["path"], e["rule"], e["count"]) for e in treatment["removed"]], [
                    (".claude/CLAUDE.md", "paragraph", 1),
                    (".claude/CLAUDE.md", "skill_list_item", 1),
                    (".claude/rules/git-workflow.md", "bullet", 1),
                ])
                self.assertIn("CLAUDE.md", treatment["loaded_sha256"])
                self.assertNotIn("skills/process-metrics/SKILL.md", treatment["loaded_sha256"])

        # AC 2: B and C load exactly the same files for the same SHA; A and B get the same prompt, and every
        # condition the same invocation apart from its session id.
        self.assertEqual(metas["B"]["toolkit_treatment"], metas["C"]["toolkit_treatment"])
        self.assertEqual(self.argv_of("A"), self.argv_of("B"))
        self.assertEqual(self.argv_of("A"), self.argv_of("C"))
        self.assertEqual(self.prompt_of("A"), self.prompt_of("B"))
        self.assertTrue(self.prompt_of("C").startswith(self.prompt_of("A").rstrip("\n")))

    def test_ac4_leak_in_condition_a_is_counted_as_a_negative_control(self) -> None:
        os.environ["FAKE_CLAUDE_MODE"] = "instrument"

        result = self.run_round("A")

        meta = self.meta("T01_A_r1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("instrument leak T01_A_r1", result.stdout)
        self.assertEqual(meta["status"], "finished")
        self.assertIsNone(meta["toolkit_treatment"])
        self.assertTrue(meta["instrument_leak"])
        self.assertEqual(meta["stream"]["instrument_markers"]["first_lines"], 1)

    def test_ac4_mode_question_or_record_block_in_the_stream_fails_the_round(self) -> None:
        for name, text, expected in (
            ("mode question", "Which process-metrics mode should this project use?",
             {"first_lines": 0, "record_blocks": 0, "mentions": 1}),
            ("record block", "Report\n```process-metrics v1\n{}\n```",
             {"first_lines": 0, "record_blocks": 1, "mentions": 1}),
        ):
            with self.subTest(case=name):
                os.environ["FAKE_CLAUDE_SCRIPT"] = event_script(text)
                self.runs_dir = self.tmp / f"runs-{name.replace(' ', '-')}"
                self.results_dir = self.tmp / f"results-{name.replace(' ', '-')}"

                result = self.run_round("B")

                meta = self.meta("T01_B_r1")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("instrument leak T01_B_r1", result.stdout)
                self.assertEqual(meta["toolkit_treatment"]["instrument"], "removed")
                self.assertEqual(meta["stream"]["instrument_markers"], expected)
                self.assertTrue(meta["instrument_leak"])
                self.assertTrue(meta["o1_success"], "a leak is reported, not turned into an O1 failure")
