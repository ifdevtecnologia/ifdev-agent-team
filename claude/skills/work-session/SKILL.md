---
name: work-session
description: Runs a bounded objective in any repository with a short plan, verification and a resume report. Use when the user explicitly asks for a work session, a long/unattended run or milestone-based continuation.
disable-model-invocation: true
argument-hint: "<objective, completion criterion and optional limit>"
---

# Bounded work session

Input: `$ARGUMENTS`. Follow the project's instructions and preserve authorizations already granted.
Without an identifiable objective in the request or in a resume report, ask for the objective before editing.

## Where to record

Use the progress directory the project documents (for example `docs/progress/`).
If there is none, ask before creating one; without an answer, record only in the final conversation report.
Use a descriptive, timestamped ID (`YYYY-MM-DD-HHMM-<topic>`) so earlier sessions are never overwritten.

## Steps

Follow **Analyze → Plan → Implement → Test → Review → Document**. Apply `~/.claude/rules/engineering-principles.md`, `~/.claude/rules/testing.md` and `~/.claude/rules/git-workflow.md`; do not copy their content into the plan.

1. **Analyze:** check the checkout, branch, initial diff and any relevant previous report; identify the affected behavior and the project instructions. A previous report does not prove the current state.
2. **Plan:** write `<id>-plan.md` with objective, acceptance criteria, scope, behavior to cover, planned tests and authorized limits (services, destinations). The plan itself does not authorize delivery steps.
3. **Implement:** work in verifiable steps with the appropriate tests, on a dedicated task branch as defined by the git workflow. Do not expand to new milestones or refactor code outside the scope.
4. **Test:** use `verify-changes` to map changed behavior to coverage and run the relevant checks. Reread failures; if the same cause persists without a new hypothesis, record the blocker and continue only with independent work. Missing coverage without a justified exception, failing mandatory tests or uncertain validation prevent COMPLETE.
5. **Review:** explicitly review the final diff against the engineering checklist and the testing rule. Fix relevant findings and rerun the affected checks before finishing. Use the `senior-reviewer` agent when the risk warrants an independent review.
6. **Document:** when reaching the objective, limit or a blocker, write `<id>-result.md` with actual results, justified exceptions and risks.

## Delivery

1. Inspect `git status`, `git diff` and `git diff --staged`, including new files. Separate the session's work from what existed before it.
2. Group by logical change: one commit per feature, fix, refactor, test or docs change that makes sense on its own; keep refactors separate from features/fixes when separable. Order commits so each one is coherent alone.
3. Write each message following the commit convention in `~/.claude/rules/git-workflow.md` and check it with `verify-changes`.
4. Commit, push and open a pull request only as `~/.claude/rules/git-workflow.md` allows: proceed without redundant approval when the task or workflow authorizes delivery; otherwise present the proposal below and wait for explicit approval. Honor any explicit restriction such as "no commits".

Proposal format, per commit:

````
### Commit N — <summary>
Files: <list; indicate partial hunks when a file is split>
Covered by: <session checks>

```
<emoji> <type>[(scope)]: <description>

<optional body>
```
````

If the user is absent and delivery is not authorized, record the proposal in `<id>-result.md` and leave the changes uncommitted.

## Resume report

- Status: COMPLETE, PARTIAL or BLOCKED, according to the acceptance criteria and `~/.claude/rules/testing.md`.
- Objective, changes and relevant files.
- Checks executed with results; pending checks with reasons.
- Commits proposed or created (messages, files, hashes), pushed branch and pull request, when applicable.
- Concrete next step, pending decisions and how to reproduce relevant failures.

Do not record tokens, real personal data or dumps.
Do not block implementation waiting for approval of a routine plan already covered by the request.
If an action needs new authorization and the user is absent, record it and do not execute it.
Invoking this skill does not schedule future runs or grant permissions beyond the request.
