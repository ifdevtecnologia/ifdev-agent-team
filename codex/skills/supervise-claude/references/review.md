# Independent Codex review

Read this before reviewing a Claude invocation. It details step 5 of the [supervise-claude skill](../SKILL.md).

After EVERY invocation, including failure, inspect the resulting repository before deciding next steps.

## Inspect with bounded context

1. Metadata first: `git status --porcelain=v1 -uall`, then `--stat` and `--name-status` of both `git diff` and `git diff --cached`, covering created, deleted and renamed files. Apply the pre-flight metadata-first secret exclusions before reading, copying or hashing anything.
2. Then read selectively: the diffs of task-relevant non-secret paths and the contents of every relevant safe new file, since `git diff` omits untracked files. Open lockfiles, generated files and snapshots only as far as needed, but always check that dependency changes are consistent (manifest and lockfile change together and only as the task expects).
3. Compare against the pre-flight baseline, not only HEAD. Check that preexisting work remains intact and that concurrent or unrelated edits are preserved and not misattributed.

## Evaluate

1. Review correctness, security, scope, project instructions, unnecessary complexity, regressions, documentation and Definition of Done. Check for unexpected changes to hooks, settings, dependencies, lockfiles and generated files. Unexpected concurrent changes or unsafe effects require stopping, not cleanup by Codex.
2. Map every new or changed meaningful executable behavior to the automated tests that cover it. Missing coverage without an explicit, justified exception, tests that only exercise the happy path when failure behavior matters, or weakened/deleted tests are correction findings.
3. Execute checks independently, allocated by phase. On an intermediate review (correction still expected), run the targeted checks that confirm or refute the findings and the affected regressions; do not run the full gate on every correction by rote. Once the implementation appears ready, run the complete relevant final gate for the task's impact classification, as identified in the plan (tests, lint, typecheck, build), omitting no mandatory check. Inspect scripts first; validations can have side effects. Mark each check with its applicability status from `~/.claude/rules/testing.md` (APPLICABLE, NOT APPLICABLE, SKIPPED WITH JUSTIFICATION or BLOCKED) with reasons; never fabricate green results. Ensure validation did not unexpectedly modify source.
4. Reuse a passed result only within the same run and only while its relevant inputs (tree, dependencies, configuration) are unchanged; do not rerun a passed full gate on an unchanged tree. Historical successes from earlier runs and Claude's self-reported results are never independent validation.
5. Keep full validation output in `validation-N.log`. Bring into context only the command, cwd, exit code, a short summary and optionally the duration; for failures, add excerpts and expand them as needed to diagnose. Do not dump full logs by default.
6. Write `codex-review-N.md`: findings with file/line evidence and severity, baseline comparison, behavior-to-test mapping, check results, DoD assessment, and decision (complete, correction needed, partial or blocked).

## Decide

If correction is needed and fewer than three cycles have started, write the next prompt as described in [delegation.md](delegation.md#corrective-prompts): each defect, evidence, expected fix, bounded file scope and required regression checks. Claude must make the correction; Codex must not patch application files, even for trivial fixes. The only exception is a failure eligible for [Mechanical remediation](mechanical-remediation.md), which consumes no cycle and remains available after cycle 3 when it is the sole remaining failure. Keep baseline protections and safety constraints binding through instruction-file pointers and the correction-specific constraints block. Do not automatically rerun Claude if the blocker cannot be solved by code changes.
