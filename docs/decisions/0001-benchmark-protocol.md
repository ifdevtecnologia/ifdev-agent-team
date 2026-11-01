# 0001. Benchmark protocol and harness

- Status: Proposed
- Date: 2026-11-01
- Issue: #27 (epic #26). Follow-ups: #28 tasks, #29 runner, #30 report, #31 reference run.
- Implements: the measurement plan (a proposal by the metrics analyst, kept in the owner's product notes; it will be committed as `docs/benchmark/measurement-plan.md` with the pre-registration, see OD-6. Until then, "plan §N" refers to that document). This record turns that plan into an executable protocol and does not redesign it. Hypotheses, metrics, sample size, decision rules and threats to validity are defined in the plan; on any conflict, the plan wins and this record is corrected.
- Claude Code version checked: 2.1.314 (`claude --version`, 2026-11-01). Verified flags: `-p`, `--output-format`, `--verbose`, `--model`, `--effort`, `--session-id`, `--max-budget-usd`, `--permission-mode`, `--permission-prompts`, `--allowedTools`, `--disallowedTools`, `--strict-mcp-config`, `--bare`. Anything else is marked as an assumption.

## Context

`CONTRIBUTING.md` forbids claiming savings that were not measured, and today there is no evidence that the team flow reduces defects or rework. The plan defines what to measure. What is still missing is how to run Claude Code unattended, without the machine's real configuration, and how to collect the numbers.

## Protocol (from the plan)

- **Hypotheses (pre-registered):** H1 quality: B − A ≥ 15 pp on the hidden-suite success rate. H2 cost: B ≤ 4× A in USD per task and ≤ 3× in wall-clock time. H3 light mode: C at most 10 pp below B on small tasks and bug fixes, at ≤ 50% of B's cost. H4 security: B fails fewer hidden access-denied tests than A. The margins are still open (OD-5).
- **Conditions.** All three use the same task, the same short project `CLAUDE.md` and the same prompt:
  - A, baseline: an empty isolated config dir.
  - B, full flow: the toolkit installed at a pinned SHA.
  - C, light mode: the definition is pending (OD-1). Working assumption: toolkit installed, plus one fixed line appended to the prompt that invokes light mode the way a user would. It is replaced by the PO's definition before the pilot.
- **Metrics and how each is collected:**

| Plan IDs | Source |
|---|---|
| O1 success, O2 escaped defects (test part), G1 | Evaluator container: hidden tests, pre-existing suite and build, run after the agent exits |
| O2 (review part), G5 | Blind review of anonymized final diffs, run outside the conditions |
| K1 cost, K2 tokens, K4 time, K5 turns | Final `result` event of `--output-format stream-json`: `total_cost_usd`, `usage`, `modelUsage`, `duration_ms`, `num_turns` |
| K6 cost per role, agent count | Calls to the subagent tool (assumed name `Agent`, formerly `Task`; check in the smoke round) and their `subagent_type`, read from the stream. Per-subagent tokens come from the run's transcripts under `$CLAUDE_CONFIG_DIR/projects/` (assumed location, check in the smoke round) |
| I1–I3 findings, rework | Counted by script from subagent final reports (severity keywords) and edits made after a report. The heuristic is validated against a manual count in the pilot |
| I4–I7 | `final.patch` against the snapshot's base commit; expected-files list in `task.yaml` |
| I8 end state | Last report line plus the runner's own status (`timed_out`, `budget_capped`, exit code) |
| K2 cross-check (pilot only) | Claude Code OpenTelemetry console exporter; no prompt logging, no remote exporter |

- **Sample size:**
  - Pilot: 4 tasks × 3 conditions × 2 repetitions = 24 runs. Pilot tasks are excluded from the conclusions.
  - Evaluation: 12 tasks × 3 × 3 = 108 runs. The minimum is 8 tasks.
  - Run order is randomized and the conditions are interleaved within the same days.
  - Each run has a 60-minute limit. A timeout, a BLOCKED end or an empty diff counts as a failure.
  - Statistical analysis follows plan §4.6.

## Options considered

### Run isolation

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| 1. Host process with a temporary `HOME` (the pattern `tests/` uses) and `CLAUDE_CONFIG_DIR` | Cheapest; same as the existing tests | The agent's Bash can still read the real home, credentials, SSH agent, hidden tests and other repos, and can reach git remotes. The real config is not loaded, but it is reachable | Rejected for agent runs. Kept for harness unit tests with a fake `claude` |
| 2. One disposable container per run, with a temporary `HOME` and config dir inside | Only the run directory is mounted; no host home, SSH agent, Docker socket or hidden tests; Docker is already installed locally | Needs images per stack; Docker-in-Docker is excluded | **Chosen** |
| 3. Disposable VM per run | Strongest boundary; easier egress control | Slow and heavy for 108+ runs | Revisit if container egress control is not enough |
| `--restricted` or `--bare` modes | Built-in | `--restricted` ignores user, project and local settings files, which drops B's hooks and settings. `--bare` skips hooks and CLAUDE.md discovery, which breaks condition B | Rejected |

### Invocation and capture

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `--output-format json` | One object, easy to parse | No per-tool events, so subagent calls are invisible | Rejected |
| `--output-format stream-json` saved raw, then parsed | Final `result` event plus every tool use (subagent types and counts) | Large raw log that contains code | **Chosen**; the raw log stays local |
| OpenTelemetry as the primary source | Official metrics | Needs a collector; it is not per-run unless labelled | Pilot cross-check only (plan §4.7) |
| SDK host program | Full control | New dependency and language; more than needed | Rejected (KISS) |

### Where the harness lives

The harness code goes in this repository under `benchmark/` (generic code only; the repository is private today but may be shared with clients). It is tested in CI with fakes, and it is not a managed item, so it is never installed. Task snapshots, hidden tests and reference solutions go in a **separate private repository**, located through `BENCH_TASKS_DIR`. Two reasons: AGENTS.md forbids product content here, and hidden tests in any repository the agents work with or that may be shared would invalidate the benchmark (the agent or a future model could find the answers). Putting everything in this repo was rejected for both reasons. Putting the harness in its own repo was rejected because it would need its own CI, and the harness is small.

## Decision

### Per-run isolation (#29)

1. **Run directory.** The runner creates `RUN_ROOT` with `mktemp -d` under `BENCH_RUNS_DIR`, outside any repository and outside the real home. It contains:
   - `home/`, the temporary `HOME`;
   - `work/`, the workspace;
   - `toolkit/`, a `git archive` export of the pinned toolkit SHA, used by B and C only;
   - `out/`, the run's outputs.
2. **Workspace.** `work/` is a fresh copy of the frozen snapshot. The runner runs `git init`, makes one commit with a synthetic author, adds no remote, and writes a synthetic git identity to `$RUN_ROOT/home/.gitconfig` so the agent's local commits work. The local history does not contain the fix and there is nothing to push to; network access is handled in step 5.
3. **Toolkit install (B and C).** The runner executes `$RUN_ROOT/toolkit/scripts/install.sh` on the host with `env -i PATH=… HOME=$RUN_ROOT/home`. The installer reads `HOME` from the environment (`require_home` in `scripts/lib/common.sh`), links the managed items into `$RUN_ROOT/home/.claude` and merges its hooks into the settings file there. Condition A skips this step and starts with an empty `.claude/`.
4. **Agent container.**
   - `RUN_ROOT` is mounted read-write **at the same absolute path**, so the installer's symlinks and the installed hook command still resolve.
   - No other mount. In particular, no host home, Docker socket, SSH agent or tasks repository.
   - The container runs as a non-root user.
   - The environment is an explicit allowlist:
     - `HOME=$RUN_ROOT/home`;
     - `CLAUDE_CONFIG_DIR=$RUN_ROOT/home/.claude`, set explicitly so that it matches the installer's destination;
     - the auth variable of a dedicated API key, passed **by name only** (OD-2), never as a value on the command line. The agent's own Bash can read it (`env`), so "agents never read it" cannot be enforced: this is an accepted risk, mitigated by a dedicated key with a workspace spend limit and by the egress allowlist;
     - `IFDEV_AGENT_NOTIFY=0`;
     - autoupdate and non-essential traffic disabled. Variable names are assumed and checked in the smoke round.
5. **Network.** WebFetch and WebSearch are denied, there are no git credentials, and dependencies are pre-fetched into the images. Bash still has network access unless egress is restricted, so the agent could fetch public sources, such as the upstream history of a task taken from a public repository. Therefore pilot and evaluation rounds use an **egress allowlist (Anthropic API only) by default**, since the pilot calibrates task difficulty, and task snapshots come only from private or synthetic sources. Smoke and harness checks may run without the allowlist (OD-3).
6. **Evaluation.** After the agent exits:
   - The runner saves `final.patch` (working tree against the base commit, uncommitted changes included) and records whether the agent committed.
   - It then starts a **fresh evaluator container** from the same stack image, copies the workspace, restores only the paths the task lists as `protected_paths` in `task.yaml` (tests and build files the solution must not change; runs that changed them are flagged for G1 and the blind review), copies the task's `hidden/` into it, and runs the commands from `task.yaml`.
   - Hidden tests never enter the agent's container.
7. **Teardown.** Extracted numbers go to `runs.csv`. Raw logs and transcripts stay local, are never versioned, and are deleted within 90 days (plan §4.7, pending the privacy review).

### Invocation

The prompt is passed on stdin (assumed, check in the smoke round). The command is the same for A, B and C; for C, the prompt also ends with the light-mode line.

```bash
timeout 3600 claude -p --model "$MODEL" --effort "$EFFORT" \
  --output-format stream-json --verbose --session-id "$RUN_UUID" \
  --max-budget-usd "$RUN_CAP_USD" --strict-mcp-config \
  --permission-mode acceptEdits --permission-prompts none \
  --allowedTools "Read Edit Write Glob Grep Bash Agent" \
  --disallowedTools "WebFetch WebSearch" \
  < prompt.md > out/stream.jsonl 2> out/stderr.log
```

- `--model` takes a full model ID, never an alias. The toolkit's agents do not set a model (checked), so subagents use the session model; inheritance is assumed and checked.
- `--permission-prompts none` denies anything that would prompt. `--allowedTools` only pre-approves tools; any other tool that needs permission (for example Skill) is silently denied, which could keep B from using its skills. The smoke round checks this, and the runner pins the available set with `--tools` if needed. The tool allowlist and permission mode are the owner's call (OD-3). This default allows Bash because the container, not the permission prompt, is the boundary.
- `--strict-mcp-config` with no config loads no MCP servers.
- `--session-id` set to the run UUID makes the transcripts easy to find.
- If `timeout` or `--max-budget-usd` stops the run, there may be no `result` event. Cost then comes from the transcripts and the overrun flag is recorded. Either way the run counts as a failure (O1).
- The smoke round must confirm: the `stream-json` + `--verbose` requirement; how the budget cap ends a run; that `total_cost_usd` includes subagents, within ±5% of transcripts or OTel (plan pilot gate); the subagent tool name; and that skills and subagents are usable under the chosen permission settings.

### Harness layout

```text
benchmark/                      # this repo: generic code only, not installed
  README.md                     # how to run; links this record
  conditions/C-light.txt        # light-mode prompt line (placeholder until OD-1)
  project-claude.md             # plan Appendix A, identical in A/B/C
  prompt-template.md            # plan Appendix B
  bench/                        # Python 3.9+ standard library only
    runner.py  isolate.py  evaluate.py  extract.py  report.py  check_task.py
  docker/                       # per-stack images: pinned Claude Code, toolchains, warm caches
  results/                      # gitignored; <round>/<run_id>/{meta.json,stream.jsonl,final.patch,eval.json}
tests/test_benchmark_*.py       # unittest with fake `claude`/`docker` on PATH; no model calls
$BENCH_TASKS_DIR/<task_id>/     # private repo, never mounted into the agent container
  task.yaml   # id, type, stack, image, test/build/hidden commands, expected_files, protected_paths, criteria IDs
  prompt.md   # issue-style statement, no solution
  snapshot/   # frozen starting tree with synthetic seeds only
  hidden/     # hidden tests, each mapped to a criterion ID (e.g. T07-C3)
  reference/  # reference solution, used only by check_task.py
```

- `runs.csv` follows the plan §4.7 schema, with these extra columns: `harness_sha, effort, permission_profile, exit_code, result_subtype, timed_out, budget_capped, subagent_calls, committed`.
- `role_costs.csv` follows plan §4.7.
- Both files hold numbers and categories only, no free text.

### Cost per round and owner authorization

The plan's per-run figures are guesses until the smoke round (A ≈ US$1, C ≈ US$2, B ≈ US$5). Token volume is recorded, not estimated. Assumed order of magnitude: 0.5–2M tokens per A run, 1–5M per C run and 3–10M per B run, mostly cache reads. Derived per round: smoke ≈ 4–12M tokens, pilot ≈ 36–136M, evaluation ≈ 162–612M. "Cap" is the worst case with `--max-budget-usd` at the default US$15 per run (OD-2).

| Round | Runs | Expected | Cap |
|---|---|---|---|
| Smoke (#31 step 1): 1 task × A/B × 1 (C only after OD-1) | 2 | ≈ US$6 | US$30 |
| Pilot: 4 × 3 × 2 | 24 | ≈ US$64 | US$360 |
| Evaluation: 12 × 3 × 3 | 108 | ≈ US$290 (range 150–600) | US$1,620 |
| Ablations (optional): 6 × 3 × 2 | 36 | ≈ US$145 | US$540 |
| Blind review of final diffs (not costed in the plan) | 1 per run | assumed ≈ US$0.5 each | per-run cap US$2 |

**Needs the owner's explicit authorization before any run that calls the model:**

1. The total budget and the per-run cap. The protocol assumes a dedicated API key in a workspace with a spend limit; subscription (OAuth) login is out of scope, because it would require copying the real credentials into the run directory (OD-2).
2. Each round separately (smoke, pilot, evaluation, ablations, blind review), with its expected cost and cap.
3. The execution environment: the container runtime, the built images and the network egress policy.
4. The permission mode and tool allowlist inside the container.
5. The pinned model, effort level and Claude Code version.
6. The light-mode definition, before any run of C. Smoke and harness checks may run A and B only.
7. Creating the private tasks repository and using snapshots from the owner's own repositories.
8. The pre-registration commit (plan, margins, frozen tasks and hidden tests) before the first evaluation run.
9. Transcript retention, after the privacy review (plan §10).

Agents may build and test the harness with fakes, but they never start a model-calling run themselves.

## Open decisions

Each has a recommended default.

- **OD-1 Light mode:** roles and trigger defined by the PO. Until then: toolkit installed plus one fixed prompt line (implementation with tests, the `reviewer`, specialized reviewers only when their criterion applies); smoke and harness checks run only A and B.
- **OD-2 Budget and billing:** dedicated API key with a workspace spend limit, US$15 per-run cap, each round authorized separately. A subscription path would need its own credential-handling design.
- **OD-3 Environment and permissions:** disposable container per run, only the run directory mounted, `acceptEdits`, prompts denied, tool allowlist with Bash, web tools denied, dependencies baked into the images, egress allowlist (Anthropic API only) required for pilot and evaluation rounds.
- **OD-4 Pinned model:** one full model ID and an explicit effort level for every condition; subagents use the session model; Claude Code pinned at 2.1.314 in the images, autoupdate off.
- **OD-5 Margins:** accept the plan's numbers (15 pp, 10 pp, ≤ 4× cost, ≤ 3× time, ≤ 50%) and freeze them in the pre-registration commit.
- **OD-6 Plan and tasks location:** commit the measurement plan (translated to English) under `docs/benchmark/` with the pre-registration; private tasks repository location; whether to use historical snapshots from the owner's repositories.

## Consequences

- **Gains.** Runs are reproducible and isolated, nothing touches the real configuration, every number is traceable to raw local evidence, and the harness can be tested in CI without spending anything.
- **Costs and constraints:** per-stack images to maintain; Supabase tasks use a Postgres + pgTAP sidecar instead of the full local stack (no Docker-in-Docker); Spring tasks must avoid Testcontainers; for every task, `check_task.py` must show the hidden tests failing on the snapshot and passing on the reference.
- **Known limitations:**
  - The same model family solves the tasks and does the blind review.
  - Tasks are synthetic or historical, small, from one developer and four stacks.
  - Model and service drift: mitigated by pinning versions, interleaving conditions and recording dates, but not removed.
  - Headless runs have no user to answer blocking questions, which may undervalue the Analyst (measured by I8).
  - Containers lack MCP, IDE and notifications.
  - Prompt caching across runs can lower later costs, so cache tokens are recorded separately.
  - The budget cap truncates expensive conditions more often; such runs are flagged and counted as failures.
  - Findings are extracted with a heuristic.
  - `total_cost_usd` is API-equivalent under a subscription.
  - The flag behavior described here is verified only for Claude Code 2.1.314.
- **Revisit when:** the smoke round shows `total_cost_usd` excludes subagents; container egress cannot be restricted well enough (move to a VM); a Claude Code upgrade changes the output schema or flags; the PO defines light mode as something other than a prompt line.

## Implementation slicing

1. **#28 tasks** (dev, owner authoring): `task.yaml` schema; `check_task.py` (sanity check without the model); the private repository; the 4 pilot tasks, then the 12 evaluation tasks, frozen by commit hash. Covers plan §4.2–4.3.
2. **#29 runner:** (devops) stack images with pinned Claude Code, warm dependency caches and the network policy; (dev) `isolate`, `runner`, `evaluate`, `extract` → `runs.csv` and `role_costs.csv`, with unittest fakes in CI. Done when A, B and C each run end to end with a fake `claude`.
3. **#30 report** (dev): bootstrap CIs (10,000 resamples), sign test, cost ratios and guardrails from a synthetic `runs.csv`, written as Markdown. Can start once the schema is frozen.
4. **#31 reference run** (owner, after items 1, 2 and 5 of the authorization list): smoke round to check the assumptions above and validate cost capture; then the pilot, with go or no-go per plan §9.
