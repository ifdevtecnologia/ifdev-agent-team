---
name: verify-changes
description: Selects and runs the local checks (format, lint, tests, build) relevant to the current changes in any repository using the commands the project defines, maps changed behavior to test coverage, reviews quality/security/overengineering and validates proposed commit messages. Use to validate a delivery, investigate failing checks or review a diff and its messages before committing.
argument-hint: "[diff | all | commit | <area or path>]"
---

# Verify changes

Input: `$ARGUMENTS`. Without an argument, verify the current task's diff; do not guess a base branch.
Work from the repository root. The rule sources are `~/.claude/rules/testing.md`, `~/.claude/rules/engineering-principles.md` and `~/.claude/rules/git-workflow.md` (in the toolkit repository: `claude/rules/`). Use them without duplicating their content here.

## Discover the commands

1. Read the project instructions: `CLAUDE.md`, `AGENTS.md` and, under `.claude/rules/`, the rules for the changed areas (verification sections, when present).
2. Read manifests, scripts (`package.json`, `Makefile`, `scripts/`, `pyproject.toml`, `pubspec.yaml`, etc.) and the CI configuration.
3. Prefer the command the project already uses, in its non-interactive form (no watch mode, formatters in check mode). Do not invent a missing script or assume an aggregate script exists.
4. Inspect a script before its first execution: a name such as `test` or `check` does not guarantee the absence of remote calls, deploys or data resets.

## Run

1. Identify changed files (including new ones), their consumers and the relevant new/changed behaviors. Map each behavior to appropriate existing/new tests or to an explicit, justified exception under the testing rule; a coverage percentage alone does not prove this.
2. Check for regressions in consumers of changed contracts, not only in the changed files.
3. Run the smallest set that verifies the changed behavior; `all` covers every area present in the repository.
4. Failure caused by the task: fix it within scope and rerun only the affected checks.
5. Preexisting failure: distinguish it from the task and report it without fixing it out of scope.
6. Missing tool, SDK, service or permission: mark it BLOCKED. Do not install globally, change versions or use a remote environment as a substitute for the local one.
7. Local services: confirm configuration and target before starting. Reset or recreate data only on a disposable database or with authorized loss.

Do not update lockfiles or format files unrelated to the task just to run a check.
Documentation or static configuration changes without relevant behavior: validate references and formats; do not require an application build. Executable or security configuration is judged by behavior and risk.

## Review

Before finishing, review the diff against the self-review checklist in `engineering-principles.md`: correctness, security (secrets, authorization, input validation), error handling, unnecessary complexity and overengineering, duplication, dead code and deprecated APIs. Confirm no obvious unjustified violations were introduced. Record relevant findings and tradeoffs without demanding refactors of unrelated code.

## Commit messages

When messages are proposed (or `$ARGUMENTS` asks for `commit`), validate each one against `git-workflow.md`:

- Subject in the format `<emoji> <type>[(scope)][!]: <description>`, with the exact emoji/type pair from the table.
- Description in English, imperative, lowercase first letter and no final period; short subject.
- Scope absent or useful; if the project documents preferred scopes, use one or justify a new one.
- The type matches the diff content (`refactor` does not change behavior; `docs` touches only documentation).
- One commit per logical change; flag commits mixing refactors with features/fixes.
- Incompatible changes use `!` and, when needed, a `BREAKING CHANGE:` footer.

Report each message as OK or with the suggested correction. Validating messages does not by itself authorize committing.

## Result

For each check: command, directory, result and short evidence of failure, if any.
Group into PASSED, FAILED, BLOCKED and NOT APPLICABLE. Not executed is not passed.
Report coverage per behavior, actual results and justified exceptions. Do not report COMPLETE if relevant tests are missing without a justified exception, mandatory tests fail or necessary validation is uncertain; use PARTIAL/BLOCKED as defined in `testing.md`.
After success, do not repeat the full suite without a new change or a concrete doubt.
