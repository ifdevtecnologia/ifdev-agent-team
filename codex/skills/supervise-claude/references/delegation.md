# Delegation and Claude execution

Read this before writing a prompt and invoking Claude. It details steps 3 and 4 of the [supervise-claude skill](../SKILL.md).

## Initial prompt

Write `claude-prompt-N.md` with clearly delimited sections. Keep it task-focused: the role, the task, the scope, the relevant baseline facts, the Definition of Done, the specific validations, the constraints and the instruction file paths. Claude reads and follows the original instruction files; do not paste rules, documents, logs or history unless a concrete discovery, conflict or authorization makes a specific excerpt necessary. Include actual task context, never just a pointer Claude cannot access:

```text
<role>
You implement; Codex planned and will independently review and deliver.
Do a brief sanity-check of the plan, then Implement, run the targeted Tests and do a
concise self-review. Do not write a second long plan. Report real plan defects; adjust
reversible implementation details within scope; never expand scope silently.
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
hooks or security controls to unblock yourself. Report blockers instead.
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

Before constructing the command, inspect the installed `claude --help` again if it has not already been captured for this run. Use only advertised options and values. Use a Bash argument array, quoted paths and stdin/file input supported by that version; never `eval` or concatenate user content into a shell command.

- Prefer `--print` with task input on stdin, if supported.
- Use an explicit turn cap (default 20) only when advertised. Do not assume `--max-turns` exists. If absent, disclose this and use a bounded process deadline (default 15 minutes per cycle) with an available, verified process supervisor such as `timeout`; verify its help before selecting options. Kill and reap the invocation at the deadline; do not leave a background implementer running while reviewing. If no reliable execution bound exists, BLOCKED.
- The three-cycle ceiling is mandatory even when a per-invocation turn cap is unavailable. A time limit is not a turn limit; report which bound was used.
- Prefer a supported permission mode that denies unapproved calls non-interactively (for example `dontAsk`, when advertised), together with a narrow allowlist. Never use bypass modes, `--dangerously-skip-permissions`, or equivalent Codex bypasses.
- Limit available tools where supported (`--tools`), not just automatic approvals (`--allowedTools`). Start with required read/search/edit tools. Restrict writes to agreed project paths through the available permission controls. Allow only the specific inspected Bash validation commands needed for this task; never grant unrestricted Bash, shell interpreters, arbitrary script execution, MCP, browser or network tools. Validate permission-rule syntax for the installed version instead of guessing patterns.
- Add explicit CLI deny rules as defense in depth when `claude --help` advertises them (e.g. `--disallowedTools` with its documented `Bash(...)` syntax). For Claude (not the Codex delivery step), deny `git add`, `git push`, `git commit`, `gh pr create`, `gh pr merge`, `gh pr review`, `git reset --hard`, destructive `git clean`, `sudo`, and the deployment commands identified during pre-flight. Prefer broader denials of reset/clean command families over fragile destructive-flag matching when supported. Discover syntax from installed help; never guess flags or matching semantics. If help does not establish a safe granular rule, deny the whole relevant tool when supported, or report the limitation and BLOCKED if safe execution cannot be bounded. Keep prompt prohibitions, the narrow allowlist and inherited deny rules; deny patterns do not cover every alias, wrapper or hook.
- Preserve project rules, skills and agents. Do not use bare/safe modes that discard project instructions. Do not enable extra agents/plugins/tools merely because they exist. If required project behavior conflicts with the safety boundary, stop and explain.
- Inspect effective inherited settings and hooks: an added allowlist does not revoke broader inherited grants. Do not weaken existing deny rules. If safe effective permissions cannot be established without changing security settings, BLOCKED.
- Discover capabilities on each run; an option observed in one installed version is not a permanent assumption.

Save stdout AND stderr to `claude-round-N.log` (or separate named files), the exact argument list without secrets, start/end times and exit code. Feed the saved prompt as input. Read Claude's final report into context; open the rest of the log only as needed. Handle failures explicitly so they still reach review and final reporting. Exit 0 or Claude's self-reported success is not evidence of completion. Authentication, quota, permission, timeout and transport errors are blockers to classify; do not retry indefinitely or request secret values.
