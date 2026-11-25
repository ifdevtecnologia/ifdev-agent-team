# Benchmark harness

The harness that runs the benchmark protocol of [ADR 0001](../docs/decisions/0001-benchmark-protocol.md): conditions A (no toolkit), B (full toolkit) and C (light mode) on the same tasks, in isolated containers, scored by hidden tests that the agent never sees. It is generic code only (Python 3.9+, standard library), tested in CI with fake `claude` and `docker` executables, and it is not a managed item: the installer never installs it.

> **Model-calling runs need the owner's authorization.** `--execute` against the real `claude` spends money. Each round (smoke, pilot, evaluation, ablations, blind review) needs the owner's explicit authorization, budget and per-run cap, as listed in ADR 0001 ("Cost per round and owner authorization"). Agents build and test the harness with fakes only; they never start a model-calling round.

## Status

| Piece | State |
|---|---|
| `task.yaml` schema and `check_task.py` | done |
| `isolate.py`, `runner.py` (plan, dry run, cost and safety guards, agent container, resume) | done |
| `evaluate.py` (final patch, evaluator containers, protected paths) | done |
| `extract.py` (numbers from the stream-json log) | done; `runs.csv` and `role_costs.csv` wait for the plan's §4.7 schema |
| `report.py` (bootstrap CIs, sign test, cost ratios, guardrails) | pending the plan's §4.6 |
| Stack images, pinned Claude Code, egress allowlist network (`docker/`) | #29 |
| Private tasks repository and tasks | #28 |
| `project-claude.md`, `prompt-template.md`, `conditions/C-light.txt` | placeholders until the measurement plan (OD-6) and the light-mode definition (OD-1) |

## Layout

```text
benchmark/
  bench/                 runner, isolate, evaluate, extract, check_task, taskspec, containers, gitops
  conditions/C-light.txt light-mode prompt line (placeholder until OD-1)
  project-claude.md      copied as CLAUDE.md into every workspace, identical in A, B and C (placeholder)
  prompt-template.md     prompt; {{TASK_PROMPT}} is replaced by the task's prompt.md (placeholder)
  results/               gitignored: <round-id>/round.json and <round-id>/<run_id>/{meta.json,prompt.md,stream.jsonl,stderr.log,final.patch,eval.json,eval.log}
```

A file that contains the marker `BENCH-PLACEHOLDER` is a placeholder. The runner refuses condition C while `C-light.txt` is one, and refuses pilot and evaluation rounds while `project-claude.md` or `prompt-template.md` is one.

## Tasks (`$BENCH_TASKS_DIR`, private repository)

```text
$BENCH_TASKS_DIR/<task_id>/
  task.yaml    schema below
  prompt.md    issue-style statement, no solution
  snapshot/    frozen starting tree, synthetic data only
  hidden/      hidden tests, each file mapped to a criterion ID
  reference/   reference solution: an overlay whose files replace or add to a copy of snapshot/ (it cannot delete files)
```

`task.yaml` uses a small subset of YAML, because the standard library has no YAML parser: top-level `key: value` lines, block lists (`  - item`) and one level of `  key: value` mappings, with comments. Quote values that start with a YAML indicator, and do not use escapes, flow collections (`[a, b]`), anchors or multi-line strings. Every file in this subset is valid YAML with the same meaning.

```yaml
id: T01                      # matches the directory name
type: bugfix                 # bugfix, feature, refactor or security (to be checked against plan §4.2)
stack: python
image: bench-python:1        # stack image from #29
commands:
  test: python3 -m unittest discover -s tests   # pre-existing suite
  build: python3 -m compileall -q src            # optional
  hidden: python3 -m unittest {test}             # run once per hidden file; {test} is bench_hidden/<file>, quoted
expected_files:              # files the solution is expected to touch (I4-I7)
  - src/app.py
protected_paths:             # restored from snapshot/ before the checks; changes are flagged
  - tests
criteria:
  - T01-C1
  - T01-C2
hidden_tests:                # every file in hidden/ maps to a criterion, every criterion has a test
  test_c1.py: T01-C1
  test_c2.py: T01-C2
```

Each check (build, suite, each hidden test) runs in its own container on a fresh copy of the workspace, so build output does not reach the other checks: `test` and `hidden` must be self-contained (build first if they need it). `protected_changed` ignores `__pycache__/`, which running the tests creates; protected paths are restored in full either way. List test-runner configuration files (for example `conftest.py`, `pytest.ini`, `package.json` scripts) in `protected_paths`, since agent code runs next to each hidden test.

Check a task without the model (the hidden tests must fail on the snapshot and pass on the reference, which must also pass the pre-existing suite and the build):

```bash
cd benchmark && python3 -m bench.check_task "$BENCH_TASKS_DIR/T01"
```

## Running a round

Prerequisites: Docker, the stack images (#29), `BENCH_TASKS_DIR`, and `BENCH_RUNS_DIR` (an absolute directory outside your home and outside any Git repository; the runner refuses anything else). With `--execute`, the API key variable named by `--auth-env` (default `ANTHROPIC_API_KEY`, OD-2) must be set: it is passed to the container by name only, never as a value on a command line, in `meta.json` or in the logs. The harness refuses to run as root.

Dry run (default): prints the plan, the seed and every `docker`/`claude` command, and starts nothing.

```bash
cd benchmark && python3 -m bench.runner --round smoke --tasks T01 --conditions A,B \
  --model <full-model-id> --effort <level> --run-cap-usd 15 --round-budget-usd 30 \
  --toolkit-sha <toolkit-commit> --seed 1
```

After the owner authorizes the round, the same command with `--execute` runs it. A pilot also needs the plan's prompt files and the egress allowlist network:

```bash
cd benchmark && python3 -m bench.runner --round pilot --tasks T01,T02,T03,T04 --conditions A,B,C --repetitions 2 \
  --model <full-model-id> --effort <level> --run-cap-usd 15 --round-budget-usd 360 \
  --toolkit-sha <toolkit-commit> --network <egress-allowlist-network> --seed <seed> --execute
```

The runner refuses to start, before anything runs, when:

- runs × `--run-cap-usd` exceeds `--round-budget-usd`, or either is missing;
- `--model` is an alias (`opus`, `sonnet`, `...-latest`) instead of a full model ID, or `--effort` is missing;
- condition C is requested while `C-light.txt` is a placeholder (OD-1);
- a `pilot` or `evaluation` round has placeholder prompt files or uses an unrestricted network (`bridge`, `host`, `default`);
- B or C has no `--toolkit-sha`, or the SHA is not a commit;
- `--tasks` repeats an ID, a cap, budget or time limit is not a finite number, or `--network` shares another container's network (`container:...`);
- the round was already started (`results/<round-id>/round.json`) with other parameters: model, effort, caps, tasks, conditions, repetitions, toolkit SHA, network, time limit, seed or prompt files;
- with `--execute`: the API key variable is not set, or `BENCH_RUNS_DIR` is missing or unsafe.

Each run:

1. Creates `RUN_ROOT` under `BENCH_RUNS_DIR` with `home/`, `work/`, `toolkit/` and `out/`. `work/` is the snapshot plus `project-claude.md` as `CLAUDE.md`, committed once with a synthetic author and no remote. For B and C, `toolkit/` is a `git archive` of `--toolkit-sha` without the harness (`benchmark/`, `docs/benchmark/`, `tests/` and ADR 0001), installed with `install.sh --target claude` and only `PATH` and the temporary `HOME`.
2. Runs `claude -p` (the invocation of ADR 0001) in a container that mounts only `RUN_ROOT`, at the same path, as the host's non-root user, without capabilities and with process, memory and CPU limits. The prompt goes on stdin; the time limit is `--time-limit-minutes` (default 60). If the host has to kill the container, nothing is evaluated until Docker confirms it is gone.
3. Works in a scratch directory under `BENCH_RUNS_DIR`, outside the mounted `RUN_ROOT`: writes `final.patch` (working tree against the base commit, untracked files included, computed in a harness-owned Git directory; host-side Git never reads the agent's `.git`), copies the workspace without special files, always restores the protected paths from the snapshot, and runs build, suite and each hidden test in its own evaluator container without network, each on a fresh copy. Only the hidden checks get the hidden tests.
4. Records `meta.json`: plan entry, seed, model, effort, caps, harness and toolkit SHAs, base commit, exit code, the numbers from the stream (cost, tokens, turns, subagent calls), hidden results, and the failure flags `timed_out`, `budget_capped`, `no_result`, `ended_blocked`, `empty_diff` and `workspace_invalid`. Any failure flag makes `o1_success` false.

Outputs are written by the host straight to `results/<round-id>/<run_id>/`, outside the mounted `RUN_ROOT`, so the agent cannot change them (`RUN_ROOT/out/` stays empty; this differs from the ADR's layout for that reason). The agent can read the API key from its environment (an accepted risk in the ADR), so the runner replaces the key's value in the outputs and records `auth_value_redacted` in `meta.json`. `RUN_ROOT` is kept (transcripts under `home/.claude/projects/` are needed for per-role costs); delete it under the retention rule of plan §4.7 (90 days, pending the privacy review). Rerunning the same command resumes the round with the seed recorded in `round.json`: finished runs are skipped, and runs that failed for a harness reason before the agent ran (`status: error`) are retried. A failure after the agent ran (for example files the evaluator cannot read) is final: the run is `finished` with `evaluation_error` or `workspace_invalid` and counts as an O1 failure, so no run is paid for twice.

## Assumptions to confirm in the smoke round

From ADR 0001; each is a constant or option in the code:

- the prompt can be passed on stdin, and `stream-json` needs `--verbose`;
- how `--max-budget-usd` ends a run (assumed: a `result` subtype that mentions the budget);
- `total_cost_usd` includes subagents (±5% of transcripts or OTel);
- the subagent tool name (`--subagent-tool`, default `Agent`, formerly `Task`) and the transcripts under `$CLAUDE_CONFIG_DIR/projects/`;
- skills and subagents are usable with `--permission-mode acceptEdits --permission-prompts none` and the tool allowlist;
- the container variables `DISABLE_AUTOUPDATER` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, and the API key variable name.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_benchmark_*.py' -v
```

The tests put fake `docker` and `claude` executables first on `PATH`: the fake `docker` runs the container command locally and logs its arguments, and the fake `claude` writes a synthetic stream. They never call the model or Docker.
