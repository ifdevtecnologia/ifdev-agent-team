# Final validation and delivery (Codex only)

Read this only when the implementation reaches the final gate. It details step 6 of the [supervise-claude skill](../SKILL.md).

Track **implementation status** separately from **delivery status** to avoid a circular COMPLETE gate. Mark implementation COMPLETE only when DoD is satisfied, required automated tests exist and all required applicable validations are green, and independent review has no unresolved significant findings. Record that decision before staging. A usable implementation with unresolved validation uncertainty is PARTIAL and must not be published; BLOCKED must never be published. An exclusively environmental delivery/tooling failure does not invalidate a previously COMPLETE implementation.

## Logical local commits

After implementation COMPLETE, Codex stages and commits without another human approval:

1. Recheck branch, baseline, worktree and index. Stage explicit task paths with `git add -- <path> ...`; never `git add .`, broad all-files staging or auto-stage commits. For mixed files, stage only proven task hunks when safely separable; otherwise BLOCKED. Never commit unrelated pre-staged work. If preserving that index while isolating task commits cannot be guaranteed, stop BLOCKED rather than unstaging/restoring the user's work.
2. Inspect `git diff --cached --stat` and `--name-status`, then `git diff --cached` (with the existing secret exclusions) before EVERY commit, and verify the entire staged set, including deletions and new files. Never stage secret/auth paths excluded from inspection. Confirm one logical change per commit and that the staged content is exactly the reviewed/validated task content. Do not rely on path names alone.
3. Use the commit convention from `~/.claude/rules/git-workflow.md` (emoji Conventional Commits with an English description), e.g. `✨ feat(auth): add Google sign-in` or `🐛 fix(api): reject requests for resources owned by other users`.
4. Inspect applicable commit/push hooks for safe effects and keep them enabled. Record hashes/messages and inspect actual committed content. If hooks change application content, return it to Claude/review within the remaining cycle budget and invalidate the COMPLETE gate until revalidated. Missing commit identity or signing support is an environmental PARTIAL; do not invent an identity or weaken signing/hooks. Never amend or rewrite existing commits unless explicitly requested.

## Safe task-branch push

Publish automatically only when implementation status is COMPLETE, relevant validations are green, the current branch is the dedicated task branch and is NOT main/master/default (except the verified bootstrap in [safety-and-preflight.md](safety-and-preflight.md#base-and-task-branch-before-implementation)), the full outgoing commit range contains only task-owned changes, the configured remote is understood/expected, and normal Git/SSH/credential-helper authentication is already available. Inspect both individual outgoing commits and the complete PR diff against the verified base; unchanged final trees can still hide unrelated commits. Reconfirm remote default/base and branch immediately before publishing. If base movement or concurrent edits invalidate validation, re-review; do not merge/rebase/rewrite automatically to resolve divergence.

Use `git push -u origin <task-branch>` or the equivalent explicit safe remote/branch command for the repository. Never force push, use `--force-with-lease`, change remote URLs, rewrite remote history or push to protected/default branches outside the verified bootstrap. Verify successful remote branch/head identity before PR creation. On authentication/environment failure, report delivery PARTIAL and preserve local commits; do not request/read tokens, keys or passwords. Unsafe rejection/history conflicts are BLOCKED; do not retry by weakening controls.

## GitHub Pull Request

After a successful task-branch publication of implementation-COMPLETE work, Codex automatically creates the PR targeting the repository default branch. Check `gh --version`, `gh auth status` without extracting tokens, and installed help for `gh pr create`/`gh pr list` options. Use explicit repository, base and head arguments. Look for an existing PR for this task's exact repository/head/base first (also after ambiguous API errors), to avoid duplicates. Do not reuse an unrelated or merged PR.

Write the exact body to an external `pr-body.md` and use `--body-file` when supported, preserving newlines. Use a concise English task title and this body:

```markdown
## Summary
- Key changes.

## Validation
- Tests/lint/build actually executed, commands and exact relevant results.

## Security / Risks
- Important security considerations, or "None identified".

## Notes
- Remaining non-blocking considerations, or "None".
```

Verify the returned PR URL, head commit and base. Never approve, merge, enable auto-merge, change branch protection or bypass required reviews. Leave the PR open for human review and merge.

Never run `gh auth login` or any interactive login from the agent; the human runs `gh auth login` and checks `gh auth status` outside the agent. If `gh` is missing or GitHub auth prevents PR creation, a safe task-branch publication is still allowed after implementation COMPLETE. Mark delivery PARTIAL and provide the exact shell-quoted command with actual repo/base/head/title/body-file path, e.g. `gh pr create --repo <owner/repo> --base <default> --head <task-branch> --title <title> --body-file <path>`. Preserve the body file for that follow-up. Do not issue this command until publication succeeded. No CLI/auth failure permits publishing an implementation that is PARTIAL or BLOCKED.
