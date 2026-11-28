# 0006. Process metrics instrument as an on-demand skill with a per-project mode

- **Status:** Accepted (2026-11-27)
- **Date:** 2026-11-27
- **Issue:** #77 (epic #75). Builds on #76 (where actual values are read). Extended by #78 to #81 (estimates, the estimation step, finding classification) and read by #82 (extractor); actual values come from #83 (telemetry).
- **Labels:** **[V]** verified locally; **[A]** assumption.

## Context

The owner wants auditable process data for every task tracked by an issue: how each invocation ended, how the item moved on the board and, in later steps, what each role estimated and which findings were raised. Records go to the issue as append-only comments, with a local file outside the repository as the alternative without remote writes, and public aggregates use only the owner's own projects.

The owner first chose "on by default with an opt-out line". After the reviews of the first implementation, the owner replaced it (2026-11-27) with a **mode chosen per project on first use**: `on`, `local` or `off`, asked once when a project has no mode line, so that client repositories never receive comments without a decision.

The constraint is the always-loaded context ([development.md](../development.md#always-loaded-context-baseline)): `core/instructions.md` and the three rules load in every session for every user, so anything placed there costs context even for projects that turn the instrument off.

## Options considered

- **A. A new always-loaded rule** (`core/rules/process-metrics.md`): simple to reference, but the whole schema and procedure would load in every session, including for projects that turn it off.
- **B. An on-demand skill plus one sentence in the always-loaded files** (chosen): the schema and procedure live in `core/skills/process-metrics/` (`SKILL.md` and a reference read only before writing, checking or publishing records); `core/instructions.md` holds one sentence and `core/rules/git-workflow.md` one bullet covering the issue comments under the delivery authorization.
- **C. Text inside each role definition**: duplicates the format in many files and loads it in every role, against the define-once principle.

## Decision

1. **Format.** Skill `process-metrics`, model-invocable (no `disable-model-invocation`, no Codex policy hiding it), installed for both targets as new managed items: `~/.claude/skills/process-metrics` and `~/.agents/skills/process-metrics`. [`references/records-v1.md`](../../core/skills/process-metrics/references/records-v1.md) is the single source of the `process-metrics v1` schema.
2. **Always-loaded text.** One sentence in `core/instructions.md` (follow the skill on tasks with an issue; the project's instructions choose the mode with `process-metrics: on`, `local` or `off`, and without that line the skill asks the user first; report a missing skill and continue) and one bullet in the Project board section of `core/rules/git-workflow.md` (with the mode `on`, the delivery authorization covers posting the records as new comments in the skill's exact layout on the task's issue only, never editing or deleting earlier ones). Measured with `scripts/context_budget.py`: **+717 bytes (~+179 estimated tokens)** for each target (`claude` 39301 to 40018 bytes, `codex` 39538 to 40255 bytes) [V].
3. **Mode.** Exact lines in the project's instructions loaded every session, with no global or user-level switch: `process-metrics: on` (issue comments), `local` (local file only, never posted) and `off` (no block, no first line, no comment, no file). `off` wins over any other line and `local` over `on`. Without a line, the main agent asks the user once and records the answer in the file the user picks; until then, records go only to its final report, marked "not published". An explicit user restriction in the session wins.
4. **Records.** One flat JSON object per line with closed vocabularies and upper bounds: a `block` header first in every published block (`kind` `planning` or `cycle`, with the cycle number), `invocation` and `transition`. `estimate` (#80), `finding` and `resolution` (#81) are defined but not emitted yet. Only IDs, categories and numbers; the issue is implicit in the location, so no repository name enters a record. Actual time, tokens and cost are never declared by an agent, except a declared `duration_min` when the main agent did the Dev work itself; the only wall-clock times are the `at` of transitions.
5. **Publishing.** At the start of each cycle the main agent fixes the cycle number from the highest header among its own marker comments (comments by other accounts are ignored and never interpreted), the local file and the blocks of the session that were only proposed or reported as not published; a proposed block may be posted once the user approves it in the same session, before any later block, but a proposed or reported block is never pasted in a later session, and a repeated header is a duplicate for readers. It parses and re-serializes every role line, never copying them literally, and posts one comment per cycle end marked `<!-- process-metrics v1 -->`, append-only in normal operation, from a private temporary file outside the repository. Destinations in order: issue comment (mode `on` only), the local file `$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl` (absolute, not a symlink, outside the repository, directories 0700 and file 0600), the final report marked "not published". Never inside the repository.
6. **Readers.** Cycles come from the headers, never from counting; for a repeated header, a comment block wins over a local block and the earliest comment wins (ties broken by comment ID); for a repeated estimate or finding ID the first wins; rejected lines are counted.
7. **First line.** With the mode `on` or `local`, or not chosen yet, every subagent prompt tied to the issue starts with `issue <repo>#<n> phase=execute` (`phase=estimate` is reserved for #80); it stays local and never enters a record.
8. **Evolution.** A new optional field, vocabulary value or `rec` type keeps `v1`, as does a new required field on a type not emitted yet; making a required field optional, renaming, removing or changing the meaning or type of a field needs `v2` with a new marker. New string fields declare a vocabulary or ID pattern and go through a privacy review.

## Consequences and risks

- With the mode `on`, comments are attributed to the posting GitHub account and show the UTC time of each board move the agent makes (and a declared duration when the main agent did the Dev work itself), visible to anyone who can read the issue, including clients. Mitigation: the mode question on first use, documented modes and deletion ([process-metrics.md](../process-metrics.md)) and the Upgrade note.
- A mode line written only in a Claude-only file is not seen by Codex, and Codex has no personal untracked instruction file; the documentation recommends the root `AGENTS.md` [A: relies on each tool loading the documented files].
- Until the installer is rerun, the always-loaded text names a skill that is not installed; the sentence tells the main agent to report it and continue.
- There is no automatic retention; the user deletes comments or the local file when they choose.
- v1 assumes one posting account per issue. Readers (the #82 extractor) take a list of trusted posting logins from the owner, kept outside the repository; a repeated header from a different trusted login is a conflict to report, not a silent duplicate.
- There is no machine validator for the records yet; the reference examples are parsed by the toolkit's tests, and #82 validates against the reference.
- The benchmark conditions B and C must run without the instrument; the harness removes it from their run `HOME` ([ADR 0001 amendment](0001-benchmark-protocol.md#amendment-toolkit-treatment-for-conditions-b-and-c-96)).

## Out of scope

Changes to the role definitions (#78, #79, #81), the estimation step and the Doing gate (#80), the extractor (#82) and telemetry (#83).
