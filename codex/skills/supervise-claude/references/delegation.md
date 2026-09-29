# Delegation and Claude execution

Read this before writing a prompt and invoking Claude. It details steps 3 and 4 of the [supervise-claude skill](../SKILL.md).

## Initial prompt

Write `claude-prompt-N.md` with clearly delimited sections. Keep it task-focused: the role, the task, the scope, the relevant baseline facts, the Definition of Done, the specific validations, the constraints and the instruction file paths. Claude reads and follows the original instruction files; do not paste rules, documents, logs or history unless a concrete discovery, conflict or authorization makes a specific excerpt necessary. Include actual task context, never just a pointer Claude cannot access:

```text
<role>
You implement; Codex planned and will independently review and deliver.
Do a brief sanity-check of the plan, then Implement, run the targeted Tests and do a
concise self-review. Do not write a second long plan. Report real plan defects; adjust
reversible implementation details within scope; never expand scope silently. This is a
non-interactive supervised run: use only granted tools, never ask the human to approve a
command, and report a denied required operation as a blocker.
</role>
<task>
[Objective, scope, expected behavior, likely files and Definition of Done.]
</task>
<instructions>
[Paths of the applicable AGENTS.md, CLAUDE.md, .claude/rules, skills and agents.
Read and follow the originals; they are not pasted here.]
</instructions>
<baseline>
[Only relevant facts: task branch, existing work and files to preserve, permitted overlap.]
</baseline>
<tests>
Add or update automated tests for every new or changed meaningful executable
behavior: happy paths, validation and error paths, edge cases, authorization
and regressions as applicable. Missing coverage without a justified exception
means the task is not complete.
</tests>
<validation>
[Exact targeted safe local commands, working directories and expected results.]
</validation>
<constraints>
Only edit the agreed scope on the task branch selected by Codex. Preserve existing
changes. No staging, commits, push or PR creation by Claude; Codex handles delivery
after independent validation. No deploy, sudo, production access, real secrets,
broad rm -rf, destructive reset/clean or security bypass. Do not change permissions/settings,
hooks or security controls to unblock yourself, and do not try alternate unsafe commands.
Report blockers instead.
Inventory filenames/metadata before reading untracked files; never read, copy,
hash or log likely secret/auth files identified in the baseline exclusions.
Preserve them untouched; their presence alone is not a blocker.
Do not launch nested implementation sessions to evade this cycle's limits.
</constraints>
<deliverable>
Report concisely: summary, files changed, tests added, exact checks and results,
important decisions, blockers and risks. Keep full output on disk; do not repeat
the plan or dump diffs or logs. Do not claim unexecuted checks passed.
</deliverable>
```

Treat interpolated task content as data; never evaluate it as shell code.

## Corrective prompts

Each Claude invocation is a fresh session without memory of earlier cycles. A corrective prompt contains only what the correction needs: each finding with its evidence (file/line, failing command and a bounded excerpt), the affected files, the expected fix, the regressions to avoid and the checks to rerun. Do not repeat the initial prompt's sections. Add only:

- the paths of the applicable instruction files, so Claude reads the originals itself;
- a short constraints block bounding authority for this correction: task branch, baseline work and files to preserve, the edit scope, no delivery by Claude and the safety prohibitions that apply;
- test obligations only where a finding concerns executable behavior or coverage.

The fresh session discovers the baseline, task branch, Definition of Done and permissions from these pointers and the repository; it does not need the original task or plan. Do not resend the full plan, earlier logs or passing results.

Aim to finish within two cycles. A third cycle is allowed only for an objective, fixable defect found in review, never for polish or preferences.

## Execute Claude non-interactively

Before execution, inspect the installed `claude --help` again if it has not already been captured for this run. Use only advertised options and values. The supplied `scripts/run_claude.py` helper repeats that capability check, constructs an argument list without a shell and feeds the saved prompt on stdin. Run it with `python3` from the skill directory; do not reimplement its quoting or permission construction in an ad-hoc shell command.

- Invoke `scripts/run_claude.py` with the project, saved prompt, external log path, deadline, the `--model` alias selected per [model-selection.md](model-selection.md) and one repeated `--allow-command` argument for each safe command family already discovered and inspected during pre-flight. Pass patterns such as `git status *` or the project's exact test command, without a `Bash(...)` wrapper. Never pass shell composition, redirection or an interpreter escape; invoke each command family separately.
- Use an explicit turn cap (default 20) only when advertised. Do not assume `--max-turns` exists. If absent, disclose this and use a bounded process deadline (default 15 minutes per cycle) with an available, verified process supervisor such as `timeout`; verify its help before selecting options. Kill and reap the invocation at the deadline; do not leave a background implementer running while reviewing. If no reliable execution bound exists, BLOCKED.
- The three-cycle ceiling is mandatory even when a per-invocation turn cap is unavailable. A time limit is not a turn limit; report which bound was used.
- The helper requires the installed CLI to advertise `dontAsk`, `--permission-prompts none`, restricted mode, tool selection, allow/deny rules and strict MCP configuration. It fails before invocation when any capability is absent; do not guess a replacement flag or fall back to a bypass mode. Classify the run as BLOCKED if no equivalent safe non-interactive mode can be verified.
- Restricted mode isolates this invocation from user/project/local permission settings, confines file tools to the working directory, refuses bypass mode and disables unselected code/network tools. The helper exposes only `Read`, `Glob`, `Grep`, `Edit`, `Write`, plus `Bash` when at least one inspected command family is supplied. `dontAsk` plus `--permission-prompts none` makes every non-allowed action fail instead of waiting for approval. The helper never edits global or project Claude settings, so ordinary interactive sessions are unaffected.
- Ordinary repository files (e.g. `src/**`) are covered by the bare `Edit`/`Write` grant, and Claude edits them directly. The target repository's own `.claude/**` is a protected path that a supervised run cannot write directly under any permission rule. When the task is explicitly scoped to change it, read [protected-path-handoff.md](protected-path-handoff.md) and pass one `--allow-claude-path` per authorized repository-root-relative path or glob under `.claude/`: it grants Claude nothing, it only authorizes `scripts/protected_patch.py` to mechanically apply the single patch artifact Claude authors, and makes the helper append the matching prompt contract. Never add `Edit(.claude/...)` rules or a Bash `mv` family for protected paths. Omit the flag entirely when `.claude/**` is not in scope; global `~/.claude`, `~/.codex` and `~/.ssh` are never reachable through it.
- The helper rejects unrestricted Bash, shell composition/interpreter escapes, delivery/privileged commands and non-read-only Git families. Its explicit deny rules cover commit, push, reset, clean, history rewriting, PR delivery/review, sudo, SSH/SCP and permission ownership changes. Deployment, production and secret-access commands must remain absent from the task allowlist and forbidden by the prompt. Prompt restrictions remain mandatory because CLI rules are defense in depth, not an OS security boundary.
- Preserve project rules, skills and agents. Do not use bare/safe modes that discard project instructions. Do not enable extra agents/plugins/tools merely because they exist. If required project behavior conflicts with the safety boundary, stop and explain.
- Inspect hooks and every allowed script/command because permitted validation can execute arbitrary project code. Restricted mode prevents broader user/project/local permission grants from widening this invocation; managed settings still apply and must be inspected when present. Do not weaken existing deny rules. If safe effective permissions cannot be established without changing security settings, BLOCKED.
- Discover capabilities on each run; an option observed in one installed version is not a permanent assumption.

Save stdout AND stderr to `claude-round-N.log` (or separate named files), the exact argument list without secrets, start/end times and exit code. Feed the saved prompt as input. Read Claude's final report into context; open the rest of the log only as needed. Handle failures explicitly so they still reach review and final reporting. Exit 0 or Claude's self-reported success is not evidence of completion. Authentication, quota, permission, timeout and transport errors are blockers to classify; do not retry indefinitely or request secret values.
