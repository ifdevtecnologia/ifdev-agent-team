"""Benchmark runner (ADR 0001): plans a round, refuses unsafe or unbudgeted rounds and runs each
task × condition × repetition in its own isolated run directory and containers.

    cd benchmark && python3 -m bench.runner --round smoke --tasks T01 ...     # dry run: prints the plan
    cd benchmark && python3 -m bench.runner --round smoke --tasks T01 ... --execute

Without --execute nothing is started: the plan and every docker/claude command are printed. Agents
never start a model-calling round themselves: --execute against the real `claude` needs the owner's
authorization listed in ADR 0001.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import random
import re
import shlex
import subprocess
import sys
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from bench.containers import ContainerError, ContainerNotStopped, ContainerSpec, run_container
from bench.evaluate import DEFAULT_COMMAND_TIMEOUT_S, evaluate_run
from bench.extract import DEFAULT_SUBAGENT_TOOL, parse_stream
from bench.gitops import git, isolated_git_env, remove_path
from bench.isolate import CONDITIONS_WITH_TOOLKIT, IsolationError, check_runs_dir, prepare_run, resolve_sha
from bench.taskspec import TASK_ID, Task, TaskError, load_task

BENCH_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BENCH_DIR.parent
ROUNDS = ("harness", "smoke", "pilot", "evaluation")
RESTRICTED_ROUNDS = ("pilot", "evaluation")
CONDITIONS = ("A", "B", "C")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
PLACEHOLDER_MARKER = "BENCH-PLACEHOLDER"
TASK_PROMPT_TOKEN = "{{TASK_PROMPT}}"
# Assumed name of the dedicated API key variable (OD-2); passed to the container by name only.
DEFAULT_AUTH_ENV = "ANTHROPIC_API_KEY"
# Docker networks that do not restrict egress; pilot and evaluation rounds need an allowlist network (OD-3).
UNRESTRICTED_NETWORKS = ("bridge", "host", "default")
DEFAULT_NETWORK = "bridge"
DEFAULT_TIME_LIMIT_MIN = 60
HOST_GRACE_S = 120
PERMISSION_PROFILE = "acceptEdits+prompts-none+allowlist-v1"
ALLOWED_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep", "Bash")
DISALLOWED_TOOLS = ("WebFetch", "WebSearch")
# Variable names assumed by ADR 0001 and checked in the smoke round.
CONTAINER_FLAGS = (("IFDEV_AGENT_NOTIFY", "0"), ("DISABLE_AUTOUPDATER", "1"), ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"))
MODEL_ID = re.compile(r"^claude-[a-z0-9]+(?:-[a-z0-9.]+)+$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
FINISHED = "finished"
ROUND_FILE = "round.json"
REDACTED = b"[REDACTED-AUTH-VALUE]"
REDACTED_OUTPUTS = ("stream.jsonl", "stderr.log", "final.patch", "eval.log", "eval.json")


@dataclass(frozen=True)
class PlannedRun:
    order: int
    task_id: str
    condition: str
    repetition: int

    @property
    def run_id(self) -> str:
        return f"{self.task_id}_{self.condition}_r{self.repetition}"


def build_plan(task_ids: list[str], conditions: list[str], repetitions: int, seed: int) -> list[PlannedRun]:
    """Random order with the conditions interleaved: each (task, repetition) block holds every
    condition in random order, and the blocks are shuffled. The same seed gives the same plan."""
    rng = random.Random(seed)
    blocks = []

    for task_id in task_ids:
        for repetition in range(1, repetitions + 1):
            block = [(task_id, condition, repetition) for condition in conditions]
            rng.shuffle(block)
            blocks.append(block)

    rng.shuffle(blocks)
    flat = [entry for block in blocks for entry in block]

    return [PlannedRun(order, *entry) for order, entry in enumerate(flat, start=1)]


def is_placeholder(path: Path) -> bool:
    try:
        return PLACEHOLDER_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return True


def valid_model_id(model: str | None) -> bool:
    """A full model ID: not an alias such as `opus` or `sonnet`, and not a `-latest` pointer."""
    return bool(model and MODEL_ID.match(model) and any(ch.isdigit() for ch in model) and not model.endswith("-latest"))


def compose_prompt(template: str, task_prompt: str, condition: str, light_line: str) -> str:
    """Same prompt for A, B and C; C also ends with the light-mode line."""
    prompt = template.replace(TASK_PROMPT_TOKEN, task_prompt.strip())

    if condition == "C":
        prompt = prompt.rstrip("\n") + "\n\n" + light_line.strip() + "\n"

    return prompt


def time_limit_s(args: argparse.Namespace) -> int:
    return max(1, round(args.time_limit_minutes * 60))


def claude_command(args: argparse.Namespace, run_uuid: str) -> tuple[str, ...]:
    """The invocation of ADR 0001 ("Invocation"); the prompt goes on stdin."""
    return (
        "timeout", str(time_limit_s(args)),
        "claude", "-p",
        "--model", args.model,
        "--effort", args.effort,
        "--output-format", "stream-json", "--verbose",
        "--session-id", run_uuid,
        "--max-budget-usd", f"{args.run_cap_usd:g}",
        "--strict-mcp-config",
        "--permission-mode", "acceptEdits",
        "--permission-prompts", "none",
        "--allowedTools", " ".join((*ALLOWED_TOOLS, args.subagent_tool)),
        "--disallowedTools", " ".join(DISALLOWED_TOOLS),
    )


def agent_container(args: argparse.Namespace, task: Task, run_root: Path, run_uuid: str) -> ContainerSpec:
    home = run_root / "home"
    env = (("HOME", str(home)), ("CLAUDE_CONFIG_DIR", str(home / ".claude")), *CONTAINER_FLAGS)

    return ContainerSpec(
        image=task.image,
        mount=run_root,
        workdir=run_root / "work",
        network=args.network,
        command=claude_command(args, run_uuid),
        env=env,
        passthrough_env=(args.auth_env,),
        name=f"bench-{run_uuid}",
        interactive=True,
    )


def harness_sha() -> str:
    try:
        return git(["-C", str(REPO_DIR), "rev-parse", "HEAD"], isolated_git_env(REPO_DIR)).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return "unknown"


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python3 -m bench.runner", description=__doc__.split("\n\n")[0])
    parser.add_argument("--round", required=True, choices=ROUNDS, help="round kind; pilot and evaluation need a restricted network")
    parser.add_argument("--round-id", help="results directory name (default: the round kind)")
    parser.add_argument("--tasks", required=True, type=_csv, help="comma-separated task IDs under the tasks directory")
    parser.add_argument("--tasks-dir", type=Path, default=os.environ.get("BENCH_TASKS_DIR"), help="default: $BENCH_TASKS_DIR")
    parser.add_argument("--runs-dir", type=Path, default=os.environ.get("BENCH_RUNS_DIR"), help="default: $BENCH_RUNS_DIR")
    parser.add_argument("--results-dir", type=Path, default=BENCH_DIR / "results")
    parser.add_argument("--conditions", type=_csv, default=["A", "B"], help="comma-separated, default A,B (C only after OD-1)")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--seed", type=int, help="plan seed (default: random, printed and recorded)")
    parser.add_argument("--model", help="full model ID (never an alias)")
    parser.add_argument("--effort", choices=EFFORTS)
    parser.add_argument("--run-cap-usd", type=float, help="--max-budget-usd of each run")
    parser.add_argument("--round-budget-usd", type=float, help="refuses the round when runs x run cap exceeds it")
    parser.add_argument("--toolkit-sha", help="toolkit commit installed for B and C")
    parser.add_argument("--toolkit-repo", type=Path, default=REPO_DIR)
    parser.add_argument("--network", default=DEFAULT_NETWORK, help="docker network of the agent container")
    parser.add_argument("--auth-env", default=DEFAULT_AUTH_ENV, help="name of the API key variable, passed by name only")
    parser.add_argument("--time-limit-minutes", type=float, default=DEFAULT_TIME_LIMIT_MIN)
    parser.add_argument("--command-timeout", type=float, default=DEFAULT_COMMAND_TIMEOUT_S, help="seconds per evaluator check")
    parser.add_argument("--subagent-tool", default=DEFAULT_SUBAGENT_TOOL, help="subagent tool name (check in the smoke round)")
    parser.add_argument("--project-claude", type=Path, default=BENCH_DIR / "project-claude.md")
    parser.add_argument("--prompt-template", type=Path, default=BENCH_DIR / "prompt-template.md")
    parser.add_argument("--light-line", type=Path, default=BENCH_DIR / "conditions" / "C-light.txt")
    parser.add_argument("--execute", action="store_true", help="start the runs (default: dry run)")
    args = parser.parse_args(argv)
    args.round_id = args.round_id or args.round
    args.results_dir = args.results_dir.resolve()

    return args


def _option_problems(args: argparse.Namespace, runs: int) -> list[str]:
    problems = []

    if args.run_cap_usd is None or args.round_budget_usd is None:
        problems.append("--run-cap-usd and --round-budget-usd are required")
    elif not (math.isfinite(args.run_cap_usd) and math.isfinite(args.round_budget_usd)) or args.run_cap_usd <= 0:
        problems.append("--run-cap-usd and --round-budget-usd must be finite and the run cap positive")
    elif runs * args.run_cap_usd > args.round_budget_usd:
        problems.append(f"{runs} runs x US${args.run_cap_usd:g} exceed the round budget of US${args.round_budget_usd:g}")

    if not valid_model_id(args.model):
        problems.append("--model must be a full model ID, not an alias")

    if args.effort is None:
        problems.append("--effort is required")

    if not set(args.conditions) <= set(CONDITIONS) or len(set(args.conditions)) != len(args.conditions) or not args.conditions:
        problems.append(f"--conditions must be distinct values among {','.join(CONDITIONS)}")

    if len(set(args.tasks)) != len(args.tasks):
        problems.append("--tasks must not repeat a task ID")

    if not math.isfinite(args.command_timeout) or args.command_timeout <= 0:
        problems.append("--command-timeout must be positive and finite")

    if args.repetitions < 1 or not math.isfinite(args.time_limit_minutes) or args.time_limit_minutes <= 0:
        problems.append("--repetitions must be at least 1 and --time-limit-minutes positive and finite")

    if args.network.startswith("container:"):
        problems.append("--network must not share another container's network")

    if not ENV_NAME.match(args.auth_env):
        problems.append("--auth-env must be an environment variable name")

    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", args.round_id):
        problems.append("--round-id may only contain letters, digits, '.', '_' and '-'")

    return problems


def _prompt_problems(args: argparse.Namespace) -> list[str]:
    problems = []

    if "C" in args.conditions and is_placeholder(args.light_line):
        problems.append("condition C needs the light-mode definition (OD-1); conditions/C-light.txt is a placeholder")

    if args.round in RESTRICTED_ROUNDS:
        for path in (args.project_claude, args.prompt_template):
            if is_placeholder(path):
                problems.append(f"a {args.round} round needs the plan's content in {path.name}, which is a placeholder")

        if args.network in UNRESTRICTED_NETWORKS:
            problems.append(f"a {args.round} round needs a docker network with the egress allowlist (OD-3), not '{args.network}'")

    try:
        if TASK_PROMPT_TOKEN not in args.prompt_template.read_text(encoding="utf-8"):
            problems.append(f"{args.prompt_template.name} must contain {TASK_PROMPT_TOKEN}")

        args.project_claude.read_text(encoding="utf-8")
    except OSError as error:
        problems.append(f"cannot read a prompt file: {error.filename}")

    return problems


def _environment_problems(args: argparse.Namespace) -> list[str]:
    problems = []

    if any(c in CONDITIONS_WITH_TOOLKIT for c in args.conditions):
        if not args.toolkit_sha:
            problems.append("--toolkit-sha is required for conditions B and C")
        else:
            try:
                args.toolkit_sha = resolve_sha(args.toolkit_repo, args.toolkit_sha)
            except IsolationError as error:
                problems.append(str(error))

    if args.execute:
        if not os.environ.get(args.auth_env):
            problems.append(f"the API key variable {args.auth_env} is not set (it is passed to the container by name only)")

        if args.runs_dir is None:
            problems.append("BENCH_RUNS_DIR (or --runs-dir) is required with --execute")
        else:
            try:
                args.runs_dir = check_runs_dir(args.runs_dir)
            except (IsolationError, OSError) as error:
                problems.append(str(error))

    return problems


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def round_parameters(args: argparse.Namespace) -> dict[str, object]:
    """Everything that must stay the same across the invocations of one round (resume included)."""
    return {
        "round": args.round,
        "tasks": args.tasks,
        "conditions": args.conditions,
        "repetitions": args.repetitions,
        "model": args.model,
        "effort": args.effort,
        "run_cap_usd": args.run_cap_usd,
        "round_budget_usd": args.round_budget_usd,
        "toolkit_sha": args.toolkit_sha,
        "network": args.network,
        "time_limit_minutes": args.time_limit_minutes,
        "command_timeout": args.command_timeout,
        "subagent_tool": args.subagent_tool,
        "harness_sha": harness_sha(),
        "project_claude_sha256": _file_digest(args.project_claude),
        "prompt_template_sha256": _file_digest(args.prompt_template),
        "light_line_sha256": _file_digest(args.light_line) if "C" in args.conditions else None,
    }


def _round_problems(args: argparse.Namespace) -> list[str]:
    """Reuses the seed of a round already started and refuses changed parameters; otherwise picks
    the seed (random when not given)."""
    path = args.results_dir / args.round_id / ROUND_FILE

    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if args.seed is None:
            args.seed = random.SystemRandom().randrange(2**31)

        return []
    except (OSError, ValueError) as error:
        return [f"cannot read {path}: {type(error).__name__}"]

    if args.seed is None:
        args.seed = recorded.get("seed")

    current = {**round_parameters(args), "seed": args.seed}
    changed = sorted(key for key in current if recorded.get(key) != current[key])

    if changed:
        return [f"round {args.round_id} was started with other parameters ({', '.join(changed)}); use a new --round-id"]

    return []


def write_round_file(args: argparse.Namespace) -> None:
    path = args.results_dir / args.round_id / ROUND_FILE

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**round_parameters(args), "seed": args.seed}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_tasks(args: argparse.Namespace) -> tuple[dict[str, Task], list[str]]:
    if args.tasks_dir is None or not Path(args.tasks_dir).is_dir():
        return {}, ["BENCH_TASKS_DIR (or --tasks-dir) must point to the tasks directory"]

    tasks, problems = {}, []

    for task_id in args.tasks:
        if not TASK_ID.match(task_id):
            problems.append(f"invalid task ID {task_id!r}")
            continue

        try:
            task = load_task(Path(args.tasks_dir).resolve() / task_id)
        except TaskError as error:
            problems.append(f"{task_id}: {error}")
            continue

        if task.id != task_id or not task.prompt.is_file() or not task.snapshot.is_dir():
            problems.append(f"{task_id}: the task.yaml id must match its directory, with prompt.md and snapshot/")
            continue

        tasks[task_id] = task

    return tasks, problems


def print_plan(args: argparse.Namespace, plan: list[PlannedRun], tasks: dict[str, Task]) -> None:
    print(f"round {args.round_id} ({args.round}), seed {args.seed}, {len(plan)} runs, model {args.model}, effort {args.effort}")
    print(f"run cap US${args.run_cap_usd:g}, round budget US${args.round_budget_usd:g}, network {args.network}")

    for run in plan:
        print(f"{run.order:4d}  {run.run_id}  task={run.task_id} condition={run.condition} repetition={run.repetition}")
        placeholder_root = Path(str(args.runs_dir or "$BENCH_RUNS_DIR")) / f"{run.run_id}-XXXXXX"
        spec = agent_container(args, tasks[run.task_id], placeholder_root, "<run-uuid>")
        print("      " + shlex.join(spec.argv()))


def _base_meta(args: argparse.Namespace, run: PlannedRun, run_uuid: str, sha: str) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "run_uuid": run_uuid,
        "round": args.round,
        "round_id": args.round_id,
        "task_id": run.task_id,
        "condition": run.condition,
        "repetition": run.repetition,
        "order": run.order,
        "seed": args.seed,
        "model": args.model,
        "effort": args.effort,
        "run_cap_usd": args.run_cap_usd,
        "time_limit_minutes": args.time_limit_minutes,
        "permission_profile": PERMISSION_PROFILE,
        "network": args.network,
        "harness_sha": sha,
        "toolkit_sha": args.toolkit_sha if run.condition in CONDITIONS_WITH_TOOLKIT else None,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def _stream_failures(stream: dict, timed_out: bool) -> dict[str, bool]:
    return {
        "timed_out": timed_out,
        "budget_capped": bool(stream["budget_capped"]),
        "no_result": not stream["has_result"],
        "ended_blocked": bool(stream["ended_blocked"]),
    }


def _outcome(stream: dict, evaluation: dict, timed_out: bool) -> dict[str, object]:
    hidden_ok = evaluation["hidden_total"] > 0 and evaluation["hidden_passed"] == evaluation["hidden_total"]
    failures = {
        **_stream_failures(stream, timed_out),
        "empty_diff": bool(evaluation["empty_diff"]),
        "workspace_invalid": bool(evaluation["workspace_invalid"]),
    }

    return {**failures, "o1_success": hidden_ok and not any(failures.values())}


def finished(run_dir: Path) -> bool:
    try:
        return json.loads((run_dir / "meta.json").read_text(encoding="utf-8")).get("status") == FINISHED
    except (OSError, ValueError, AttributeError):
        return False


def redact_outputs(run_dir: Path, secret: str) -> bool:
    """Replaces the API key value in the run's outputs (the agent can read and print it); returns
    whether it was found."""
    found = False

    for name in REDACTED_OUTPUTS:
        path = run_dir / name

        if not path.is_file() or path.is_symlink() or not secret:
            continue

        data = path.read_bytes()

        if secret.encode() in data:
            path.write_bytes(data.replace(secret.encode(), REDACTED))
            found = True

    return found


def _agent_run(args: argparse.Namespace, run_dir: Path, task: Task, run_root: Path, run_uuid: str) -> tuple[int, bool]:
    spec = agent_container(args, task, run_root, run_uuid)

    with open(run_dir / "prompt.md", "rb") as stdin, open(run_dir / "stream.jsonl", "wb") as stdout, open(
        run_dir / "stderr.log", "wb"
    ) as stderr:
        return run_container(spec, time_limit_s(args) + HOST_GRACE_S, stdin, stdout, stderr)


def _evaluate(args: argparse.Namespace, run_dir: Path, task: Task, run_root: Path, base_commit: str, exit_code: int,
              host_timeout: bool) -> dict[str, object]:
    """Everything after the agent ran. Any failure here is final for the run (never retried, so a
    run is never paid for twice) and counts as an O1 failure."""
    stream = parse_stream(run_dir / "stream.jsonl", args.subagent_tool)
    result: dict[str, object] = {"status": FINISHED, "exit_code": exit_code, "stream": stream}

    try:
        evaluation = evaluate_run(task, run_root, run_dir, args.project_claude, base_commit, args.command_timeout, args.runs_dir)
    except (ContainerError, OSError, ValueError, subprocess.SubprocessError) as error:
        return {**result, **_stream_failures(stream, host_timeout or exit_code == 124),
                "evaluation_error": type(error).__name__, "o1_success": False}

    return {
        **result,
        "evaluation_error": None,
        "committed": evaluation["committed"],
        "protected_changed": evaluation["protected_changed"],
        "hidden_passed": evaluation["hidden_passed"],
        "hidden_total": evaluation["hidden_total"],
        **_outcome(stream, evaluation, host_timeout or exit_code == 124),
    }


def execute_run(args: argparse.Namespace, run: PlannedRun, task: Task, sha: str) -> dict[str, object]:
    run_dir = args.results_dir / args.round_id / run.run_id

    for name in ("meta.json", "stream.jsonl", "stderr.log", "final.patch", "eval.json", "eval.log", "prompt.md"):
        remove_path(run_dir / name)

    run_dir.mkdir(parents=True, exist_ok=True)
    run_uuid = str(uuid.uuid4())
    meta = _base_meta(args, run, run_uuid, sha)

    try:
        run_root, base_commit = prepare_run(
            args.runs_dir, run.run_id, task, run.condition, args.project_claude, args.toolkit_repo, args.toolkit_sha
        )
        meta.update(run_root=str(run_root), base_commit=base_commit)
        prompt = compose_prompt(
            args.prompt_template.read_text(encoding="utf-8"),
            task.prompt.read_text(encoding="utf-8"),
            run.condition,
            args.light_line.read_text(encoding="utf-8") if run.condition == "C" else "",
        )
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        exit_code, host_timeout = _agent_run(args, run_dir, task, run_root, run_uuid)
    except ContainerNotStopped:
        # The agent ran and may still be running: never evaluated, never retried (it would be paid twice).
        meta.update(status=FINISHED, evaluation_error="container_not_stopped", timed_out=True, o1_success=False)
    except (IsolationError, ContainerError, TaskError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        # A harness failure before or while starting the agent: recorded, and retried on resume.
        meta.update(status="error", error=type(error).__name__)
    else:
        meta.update(_evaluate(args, run_dir, task, run_root, base_commit, exit_code, host_timeout))

    meta["auth_value_redacted"] = redact_outputs(run_dir, os.environ.get(args.auth_env, ""))

    meta["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return meta


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks, problems = load_tasks(args)
    problems += _option_problems(args, len(args.tasks) * len(args.conditions) * max(args.repetitions, 1))
    problems += _prompt_problems(args) + _environment_problems(args)
    problems += _round_problems(args) if not problems else []

    if problems:
        print("refusing to start the round:", file=sys.stderr)

        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)

        return 2

    plan = build_plan(args.tasks, args.conditions, args.repetitions, args.seed)

    try:
        print_plan(args, plan, tasks)
    except ContainerError as error:
        print(f"refusing to start the round: {error}", file=sys.stderr)

        return 2

    if not args.execute:
        print("dry run: nothing was started (add --execute after the owner's authorization)")

        return 0

    write_round_file(args)
    sha = harness_sha()
    errors = 0

    for run in plan:
        if finished(args.results_dir / args.round_id / run.run_id):
            print(f"skip {run.run_id} (already finished)")
            continue

        meta = execute_run(args, run, tasks[run.task_id], sha)
        errors += meta["status"] != FINISHED
        print(f"{meta['status']:8s} {run.run_id} o1_success={meta.get('o1_success')}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
