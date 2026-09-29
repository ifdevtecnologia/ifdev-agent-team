# Protected-path patch handoff

Read this only when a task is explicitly scoped to change the target repository's own `.claude/**` (agents, skills, rules, settings, hooks). It details how those changes flow through steps 3–5 of the [supervise-claude skill](../SKILL.md).

## Why a handoff

Claude Code treats a repository's `.claude/**` as a protected path. A supervised non-interactive invocation (restricted, `dontAsk`, `--permission-prompts none`) cannot write it directly, and no permission rule changes that: an `Edit(path)` grant and an exact Bash `mv` rule do not work and are not used. Do not relax the mode, grant blanket `.claude/**` access, use bypass modes or touch any Claude settings to work around it.

Ordinary repository files stay directly editable by Claude. Only `<repo>/.claude/**` goes through the handoff; the global Claude/Codex configuration in the home directory is never reachable through it.

## Contract

1. Pass one `--allow-claude-path` to `scripts/run_claude.py` per repository-root-relative path or glob (`*` within one segment, `**` across segments) under `.claude/` the task needs, including both endpoints of a rename. It validates each one before invocation (no absolute, home, `..`, non-`.claude/` or symlink-escaping path, no `[]`/`{}` globs). It adds **no** Claude permission; it only authorizes the supervisor to apply Claude's authored patch to those paths and appends a deterministic `<protected-path-contract>` block to the prompt.
2. Claude edits ordinary files directly, does not attempt direct protected writes and writes exactly one git-style unified diff with only the intended protected changes to `claude-protected.patch` at the repository root. Edits, new files (mode `100644`), deletions and renames (`rename from`/`rename to`, optionally with hunks) are supported.
3. After the cycle, run `python3 scripts/protected_patch.py apply --project <repo> --allow-claude-path ... --archive <run-dir>/protected-N.patch` with the same authorizations (`check` validates without mutating). It prints a JSON report.

## Validation and application

`scripts/protected_patch.py` fails closed. It rejects, without modifying the artifact or any file:

- paths that are absolute, home-relative, contain `..`/`.`/empty segments, quotes or whitespace, are outside `.claude/` (ordinary files must be edited directly), or are not authorized (both rename endpoints must be);
- a path (existing, added, or either endpoint of a rename) that names credential, secret, auth or token material, by segment, dot-prefixed variant, stem or file extension — never handled by this mechanism even under an authorization broad enough to otherwise cover it;
- a project whose resolved root is the home directory or lies beneath the global Claude/Codex configuration or the current user's `~/.ssh`; `scripts/run_claude.py` refuses this before launch too, not only `protected_patch.py` at apply time;
- symlinks anywhere on a touched path, including dangling ones and a symlinked `.claude/` or artifact;
- binary patches or content, mode changes, new files not `100644`, declared modes that differ from the current file, copies and any unknown header;
- malformed or ambiguous patches: preambles, missing final newline, hunk counts that differ from their headers, a path touched by more than one section, mismatched headers;
- stale source context: every hunk must match the current file exactly at its stated line (no offset or fuzz), new files and rename destinations must not exist, and a deletion must remove the whole current content;
- a missing, tracked, oversized or non-regular artifact, or a `git apply --check` refusal.

When valid, it applies the artifact mechanically with `git apply` (worktree only, `--whitespace=nowarn`), then compares the whole `.claude/**` tree with the post-state it computed independently from the artifact: touched paths must match byte for byte (executable bit kept), removed paths must be gone and every untouched protected file must be unchanged. On success it moves the unchanged artifact to `--archive` so a later cycle cannot re-apply it.

Exit codes: `0` valid/applied, `1` rejected, `2` supervisor configuration error, `3` application or verification failed.

## Authorship and cycles

Application is mechanical execution of Claude-authored semantics, not supervisor implementation: Codex never writes, edits, regenerates or repairs the artifact, and never reapplies part of it. Running the applicator is not a Claude invocation and consumes no cycle.

- **Rejected (`1`):** leave the artifact as it is and put the reported error verbatim into the next corrective prompt, within the normal cycle budget. Claude overwrites the artifact with a corrected version.
- **Failed (`3`) or configuration error (`2`):** BLOCKED for human review; do not retry by weakening checks or patching files by hand.
- **Applied (`0`):** review the resulting protected diff like any other Claude change. Keep the archived artifact with the run artifacts; never stage or commit `claude-protected.patch`.

## Real Claude fixture

In the toolkit repository, `tests/test_protected_patch_real_claude.py` runs real Claude Code through `run_claude.py` in the normal supervised configuration against a synthetic temporary repository. It is skipped unless `IFDEV_REAL_CLAUDE_FIXTURE=1` is set, because it starts a billed session:

```bash
IFDEV_REAL_CLAUDE_FIXTURE=1 python3 -m unittest tests.test_protected_patch_real_claude -v
```
