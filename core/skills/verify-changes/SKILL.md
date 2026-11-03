---
name: verify-changes
description: Selects and runs the local checks (format, lint, tests, build) relevant to the current changes in any repository using the commands the project defines, maps changed behavior to test coverage, reviews quality/security/overengineering and validates proposed commit messages. Use to validate a delivery, investigate failing checks or review a diff and its messages before committing.
argument-hint: "[diff | all | commit | <area or path>]"
---

# Verify changes

Input: the request that invoked this skill (`diff`, `all`, `commit` or an area or path). When it names none, verify the current task's diff; do not guess a base branch.
Work from the repository root. The rule sources are the `testing`, `engineering-principles` and `git-workflow` rules. Use them without duplicating their content here.

## Discover the commands

1. Read the project's instructions, including the rules for the changed areas (verification sections, when present).
2. Read manifests, scripts (`package.json`, `Makefile`, `scripts/`, `pyproject.toml`, `pubspec.yaml`, etc.) and the CI configuration.
3. Prefer the command the project already uses, in its non-interactive form (no watch mode, formatters in check mode). Do not invent a missing script or assume an aggregate script exists.
4. Inspect a script before its first execution: a name such as `test` or `check` does not guarantee the absence of remote calls, deploys or data resets.

## Run

1. Start from metadata: `git status`, then `--stat`/`--name-status` of the staged and unstaged diffs, including new files. Read the relevant diffs and new files next; open lockfiles, generated files and snapshots only as needed, while still checking that dependency changes are consistent.
2. Classify the impact and each candidate check's applicability as defined in `testing.md`. Map each relevant new/changed behavior to appropriate existing/new tests or to an explicit, justified exception; a coverage percentage alone does not prove this.
3. Check for regressions in consumers of changed contracts, not only in the changed files.
4. Run the smallest set that verifies the changed behavior; `all` covers every area present in the repository.
5. Failure caused by the task: fix it within scope and rerun only the affected checks.
6. Preexisting failure: distinguish it from the task and report it without fixing it out of scope.
7. Missing tool, SDK, service or permission: mark it BLOCKED. Do not install globally, change versions or use a remote environment as a substitute for the local one.
8. Local services: confirm configuration and target before starting. Reset or recreate data only on a disposable database or with authorized loss.

Do not update lockfiles or format files unrelated to the task just to run a check. Keep durable full output in log files in a safe directory outside the repository (never containing secrets); bring only summaries and failure excerpts into context. If writing logs is denied, say so, report the summary and excerpts you have and do not claim the full output was saved.

## Review

Before finishing, review the diff against the self-review checklist in `engineering-principles.md`: correctness, security (secrets, authorization, input validation), error handling, unnecessary complexity and overengineering, duplication, dead code and deprecated APIs. Confirm no obvious unjustified violations were introduced. Record relevant findings and tradeoffs without demanding refactors of unrelated code.

When commit messages are proposed (or the request that invoked this skill asks for `commit`), apply [the commit message checks](references/commit-messages.md).

## Result

For each check: command, directory, exit status and short evidence of failure, if any.
Group into PASSED, FAILED, BLOCKED, SKIPPED WITH JUSTIFICATION and NOT APPLICABLE, with reasons for the last three. Not executed is not passed.
Report coverage per behavior, actual results and justified exceptions. Do not report COMPLETE if relevant tests are missing without a justified exception, mandatory tests fail or necessary validation is uncertain; use PARTIAL/BLOCKED as defined in `testing.md`.
After success, do not repeat the full suite without a new change or a concrete doubt. Reuse a result only within the same run on unchanged relevant inputs; historical successes do not count.
