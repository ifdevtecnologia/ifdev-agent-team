"""Sanity check of a benchmark task without the model (ADR 0001, #28).

    python3 -m bench.check_task <task_dir> [<task_dir> ...]

A task passes when its task.yaml is valid, every file in hidden/ maps to a criterion ID and every
criterion has a hidden test, every hidden test fails on snapshot/ and passes on the reference
solution, and the reference also passes the pre-existing suite and the build. reference/ is an
overlay: its files replace or add to a copy of snapshot/. The checks run in the task's image,
through the same evaluator containers as the benchmark runs.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from bench.evaluate import DEFAULT_COMMAND_TIMEOUT_S, run_checks
from bench.gitops import copy_tree
from bench.taskspec import Task, TaskError, load_task


def mapping_problems(task: Task) -> list[str]:
    """Hidden test files without a criterion, criteria without a test and mapped files that do not exist."""
    if not task.hidden.is_dir():
        return ["missing hidden/ directory"]

    on_disk = {p.relative_to(task.hidden).as_posix() for p in task.hidden.rglob("*") if p.is_file() and not p.name.startswith(".")}
    problems = [f"hidden test '{f}' is not mapped to a criterion in hidden_tests" for f in sorted(on_disk - set(task.hidden_tests))]
    problems += [f"hidden_tests lists '{f}', which does not exist in hidden/" for f in sorted(set(task.hidden_tests) - on_disk)]
    covered = set(task.hidden_tests.values())
    problems += [f"criterion '{c}' has no hidden test" for c in task.criteria if c not in covered]

    return problems


def structure_problems(task: Task) -> list[str]:
    missing = [name for name, path in (("snapshot/", task.snapshot), ("reference/", task.reference)) if not path.is_dir()]
    problems = [f"missing {name} directory" for name in missing]

    if not task.prompt.is_file():
        problems.append("missing prompt.md")

    return problems


def behaviour_problems(task: Task, scratch: Path, timeout_s: float) -> list[str]:
    snapshot, reference = scratch / "snapshot", scratch / "reference"
    copy_tree(task.snapshot, snapshot)
    copy_tree(task.snapshot, reference)
    copy_tree(task.reference, reference)
    before = run_checks(task, snapshot, scratch / "snapshot.log", timeout_s)
    after = run_checks(task, reference, scratch / "reference.log", timeout_s)
    problems = [f"hidden test '{h['test']}' passes on the snapshot (it must fail before the fix)" for h in before["hidden"] if h["passed"]]
    problems += [f"hidden test '{h['test']}' fails on the reference solution" for h in after["hidden"] if not h["passed"]]

    if not after["suite"]["passed"]:
        problems.append("the pre-existing suite fails on the reference solution")

    if after["build"] is not None and not after["build"]["passed"]:
        problems.append("the build fails on the reference solution")

    return problems


def check_task(directory: Path, timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> list[str]:
    """Returns the task's problems; an empty list means the task passes."""
    try:
        task = load_task(directory)
    except TaskError as error:
        return [str(error)]

    problems = structure_problems(task) + mapping_problems(task)

    if problems:
        return problems

    scratch = Path(tempfile.mkdtemp(prefix=f"check-{task.id}-"))

    try:
        return behaviour_problems(task, scratch, timeout_s)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m bench.check_task", description=__doc__.split("\n\n")[0])
    parser.add_argument("tasks", nargs="+", type=Path, help="task directories (each with task.yaml)")
    parser.add_argument("--command-timeout", type=float, default=DEFAULT_COMMAND_TIMEOUT_S, help="seconds per check")
    args = parser.parse_args(argv)
    failed = 0

    for directory in args.tasks:
        problems = check_task(directory.resolve(), args.command_timeout)

        if problems:
            failed += 1
            print(f"FAIL {directory}")

            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {directory}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
