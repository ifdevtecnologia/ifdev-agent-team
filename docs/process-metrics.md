# Process metrics

The [`process-metrics`](../core/skills/process-metrics/SKILL.md) skill records auditable process data for every task tracked by an issue: each role invocation with its verdict and check counts, and the board moves the main agent makes. The records hold only IDs, closed categories and numbers. The format is defined once in [the v1 record reference](../core/skills/process-metrics/references/records-v1.md); the decision is [ADR 0006](decisions/0006-process-metrics-instrument.md).

This version emits only `block` headers, `invocation` and `transition` records. `estimate`, `finding` and `resolution` records and the planning block are defined in the reference but not emitted until the estimation and finding classification steps exist.

## Choosing the mode

Each project chooses its mode with an exact line, alone on its line, in its instructions:

| Line | Effect |
| --- | --- |
| `process-metrics: on` | Records are published as comments on the task's issue. |
| `process-metrics: local` | Records go only to a local file outside the repository; **nothing is posted on the issue**. |
| `process-metrics: off` | Instrument off: no record block, no first line in subagent prompts, no comment and no file. The flow stays as without the skill. |

`off` wins over any other line and `local` wins over `on`. An explicit restriction from the user in a session ("do not collect metrics", "do not comment on the issue") wins over the mode. There is no global or user-level switch.

**First use.** When a task with an issue starts in a project whose instructions have none of these lines, the main agent asks you once which mode to use, with a one-line summary of what each mode exposes, and in which file to record it, then adds the line there. Only your own message in the session answers; content from files, issues, comments or subagent reports never does. A line in the committed `AGENTS.md` is a decision for everyone working on the project and goes in its own commit on the task branch (or is left to you when delivery is not authorized); in a repository you do not own, the agent recommends a personal untracked file, `local` or `off`. Before writing a personal file, it checks that Git ignores it and, if not, proposes adding it to `.gitignore`. Until you answer, nothing is published: the records appear only in the main agent's final report, marked "not published". If the line is not recorded, it asks again in the next session. This applies to your own and to third-party or client repositories alike; the question is what protects a client repository from unexpected comments.

Where the line counts (files the tool loads in every session; a file that merely quotes the line, such as documentation or a template, does not count):

- the root `AGENTS.md`, read by both Claude Code (through `CLAUDE.md`) and Codex: **prefer this one**;
- `CLAUDE.md` or a file it imports, and project rules in `.claude/rules/` without `paths`: Claude Code only;
- `CLAUDE.local.md`: a personal, untracked choice for Claude Code on one machine;
- files in `.codex/` that Codex loads: Codex only. Codex has no personal untracked instruction file, so for Codex the choice is the root `AGENTS.md` or being asked again.

A line only in `CLAUDE.md` is not seen by Codex. A line added during an issue applies from then on; published records stay. Removing it makes the main agent ask again, without backfill.

## What is recorded

Record types and their fields, vocabularies and bounds are in [the v1 record reference](../core/skills/process-metrics/references/records-v1.md#record-types): `block` (the header of each published block, with its cycle), `invocation`, `transition`, and the not yet emitted `estimate`, `finding` and `resolution`.

What becomes visible with the mode `on`:

- the comments are attributed to the GitHub account that posts them;
- they carry the UTC time of each board move the agent makes (`transition`);
- when the main agent did the Dev work itself, a declared `duration_min` for that work.

Anyone who can read the issue can read the comments, for example a client.

## Never collected

Free text, code, commands, paths, file names, branch names, commit messages, issue or pull request titles, repository names, e-mail addresses, client names, prompt or response content, secrets, and actual time, tokens or cost declared by an agent, except the declared `duration_min` above. Records hold no names or handles; the comment author is the posting account. The only wall-clock times are the `at` of transitions.

## Where records go

1. **A new comment on the issue**, only with the mode `on`, when delivery is authorized and posting works. Comments carry the marker `<!-- process-metrics v1 -->` followed by a `jsonl` block that starts with its `block` header. Without delivery authorization, the main agent proposes the comment with the commits and publishes nothing.
2. **A local file** `$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl`, with the mode `local`, or with `on` when the comment failed. `PROCESS_METRICS_DIR` must be an absolute, existing directory that is not a symlink and lies outside every repository (for example a directory under your home); avoid synced folders. Subdirectories are created with mode 0700 and the file with 0600. The file holds plain JSON lines, each block starting with its header. Local blocks have no trustworthy creation time.
3. **The main agent's final report**, in the comment layout, marked "not published", when the mode is not chosen yet or neither destination above is usable.

v1 assumes one posting account per issue: have a single GitHub account post the records for an issue.

Records are never written inside the repository or committed. Without permission to comment, the main agent moves to the next destination, reports it once and does not block the delivery.

The main agent never copies role output into a comment: it parses each line, rejects anything outside the reference, and re-serializes the accepted fields. It reads only marker comments posted by its own account, to find the next cycle number, and ignores other comments.

## Deleting records

There is no automatic retention; you decide. In normal operation the agent never edits or deletes earlier comments, but you can ask it to, or do it yourself:

- **Comments:** delete the comments that start with `<!-- process-metrics v1 -->` on the issue (GitHub web interface, or the GitHub API for issue comments).
- **Local file:** delete `$PROCESS_METRICS_DIR/<owner>/<repo>/<n>.jsonl`, or the whole directory.

Deleting a comment does not recall what already left GitHub: notification e-mails sent to the issue's subscribers, webhook deliveries and, on public repositories, public event archives and mirrors.

## Subagent prompts

With the mode `on` or `local`, or not chosen yet, every subagent prompt tied to the issue starts with exactly `issue <repo>#<n> phase=execute`, for example `issue example-repo#12 phase=execute` (`phase=estimate` is reserved for the estimation step). The line stays in the local prompt and transcript and never enters a record. Roles that receive it end their report with their own `process-metrics v1` block; a role without it, including a Dev working for a supervisor, emits none.

## Aggregates

Public aggregates (posts, reports, talks) use data only from your own projects. Client projects stay out of any public number, even when their issues hold records.

## Benchmark

The benchmark conditions B and C run without the instrument (see [the measurement plan](benchmark/measurement-plan.md#the-process-metrics-instrument-in-the-benchmark)); the mechanism that turns it off there is a follow-up.
