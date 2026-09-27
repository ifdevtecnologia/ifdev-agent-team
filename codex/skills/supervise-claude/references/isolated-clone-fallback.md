# Isolated clone fallback

Read this when the pre-flight metadata probe reports that the original checkout's Git metadata is not operable (for example a read-only `.git`). It extends pre-flight and delivery of the [supervise-claude skill](../SKILL.md); every safety boundary there still applies.

## Metadata probe and selection

In pre-flight, after the baseline inventory and remote discovery and before creating the task branch, run `python3 scripts/clone_fallback.py prepare` from the skill directory with `--project`, the external `--run-dir`, the delivery `--remote` and `--expected-repository` (`OWNER/REPO` or `HOST/OWNER/REPO`) established from repository configuration plus task context, the dedicated `--task-branch`, and only the attestations Codex has actually verified: `--starts-from-default` (the task can begin entirely from the current remote default branch) and `--local-state-irrelevant` (existing local-only state is unrelated to the task). The helper:

- verifies a valid Git worktree and records a metadata-only snapshot of the original (HEAD, branch, status names, refs and stat data; never file contents);
- probes the per-worktree and shared Git directories, objects, refs and the directories a branch update writes into (the task branch's and the symbolic HEAD target's loose ref and reflog parents, or their nearest existing ancestor such as `refs/heads`; never creating missing ones) by creating and immediately removing a uniquely named empty file. It does not trust mode bits and never changes permissions, ownership or configuration. If a probe file cannot be removed, probing stops and the decision is BLOCKED with the residue path (`probe_residue`) for manual cleanup; no clone is created;
- writes `workspace.json` in the run directory (never overwriting one) with the decision: **NORMAL** (metadata operable; continue in the original as usual), **FALLBACK** or **BLOCKED** with reasons. Exit 0 means NORMAL/FALLBACK, 1 BLOCKED.

FALLBACK is selected only when all of these hold; anything else is BLOCKED:

- the delivery remote is configured with exactly one identical fetch/push URL, carries no embedded credentials and matches the expected repository;
- the original has no staged, unstaged, untracked, stashed or unpublished local commits, or Codex verified they are unrelated. Ignored local-only inputs the task needs (such as local environment files) also count as relevant state; never copy local uncommitted files into the clone;
- the task starts entirely from the remote default branch, freshly discovered with `git ls-remote --symref` (never the stale local HEAD or a cached remote HEAD), and a network clone is possible under the current sandbox profile;
- the task branch is not the default branch.

The clone is created in a unique `workspace-*` directory inside the run directory (outside the original tree), checked out at the discovered default head, then switched to the task branch. The record holds original HEAD, remote default branch/head, clone start head, clone path and a credential-free repository identity; the remote URL is never recorded. The clone is retained through final reporting; do not delete it.

## Working in the clone

- The original becomes inspection-only: no fetch, switch, stage, commit, reset, stash, restore, clean, configuration or application change there, and no permission workaround.
- Re-read project instructions, commands, hooks and settings from the clone, and capture its own baseline, before delegation. Run Claude with `--project` set to the clone (the record's `workspace`). Validation, review, Mechanical remediation and delivery happen only in the clone.
- Before staging, committing, pushing or opening the PR, `check-target --record <run-dir>/workspace.json --repo <clone>` must allow the target: the recorded clone, on its task branch, which is not the default branch.
- Publish with `push --record ...`: it pushes only `refs/heads/<task-branch>`, never forced, then verifies the published head. Exit 1 (rejection, history conflict or target violation) is BLOCKED; exit 3 (network or authentication) is an environmental PARTIAL delivery.
- `pr-command --record ... --title ... --body-file ...` prints the `gh pr create` argument list and working directory (the clone), targeting the discovered default branch with the task branch as head. Merging remains a human action.
- At final reporting, run `verify-original --record ...`; a changed original is reported as BLOCKED, honestly including anything already published.

## Status

- **COMPLETE**: implementation COMPLETE in the clone, task branch published and PR created, original verified unchanged.
- **PARTIAL**: implementation COMPLETE but publication or PR creation failed for environmental reasons; keep the clone and its commits and report the exact follow-up.
- **BLOCKED**: fallback preconditions unmet, clone head mismatch, delivery target violation, rejected push or changed original. Never publish BLOCKED work.
