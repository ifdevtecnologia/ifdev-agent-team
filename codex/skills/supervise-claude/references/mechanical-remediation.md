# Mechanical remediation (Codex only)

Read this before Codex changes any file itself. It details step 6 of the [supervise-claude skill](../SKILL.md). The deterministic boundaries below are encoded in `scripts/supervision_policy.py`; run `python3 scripts/supervision_policy.py check-remediation --request <file.json>` from the skill directory with the planned operation and current state, and do not proceed unless it reports `"allowed": true`.

## Policy

- **Eligible operations only:** an already-inspected, deterministic formatter configured by the project (for example its format script), or whitespace/line-ending normalization matching the project's configured rules (`.editorconfig`, `.gitattributes`). Nothing else: no ad-hoc edits by hand or by script.
- **Task scope only:** pass explicit task-owned paths to the command; never format the whole repository, baseline work or unrelated files.
- **Forbidden:** semantic or correctness fixes, test or assertion changes, refactors, SQL/RLS/auth/security code, migrations, dependencies or lockfiles, architecture or behavior changes, and lint/type/test suppressions. These stay Claude's corrections.
- **Doubt fails closed:** when a failure or a resulting change cannot be classified as purely formatting, whitespace or line endings, treat it as non-mechanical: delegate it to Claude within the remaining cycle budget, or stop BLOCKED when the budget is exhausted.

## Cycle budget

Mechanical remediation is not a Claude invocation: never increment the cycle counter for it. Before the budget is exhausted, it may resolve a mechanical failure without spending a cycle. After cycle 3 it is still allowed, but only when the sole remaining failures are mechanical and no semantic, security or correctness finding remains; anything else is BLOCKED.

## Procedure

1. Record in `state.md` the failing check, the exact command, the task paths and the policy decision.
2. Run the command with explicit paths and keep its output in `remediation-N.log`.
3. Review the resulting diff independently (metadata first, then the diff of every changed path). Only changed task paths may appear. For whitespace or line-ending normalization, confirm each file with `check-normalization <before> <after>` (before content from `git show`, written outside the repository). For a formatter, confirm that every hunk is layout-only. Any other change, or a changed path outside the plan: stop and delegate or report BLOCKED; do not revert user work to hide it.
4. Rerun the failing check and the relevant final gate affected by the changed files. COMPLETE is allowed only after this revalidation passes.
5. Report each remediation in `codex-review-N.md` and `final-report.md`: command, files, diff review result, revalidation and that no cycle was consumed. Delivery then follows [delivery.md](delivery.md) unchanged: Codex commits, pushes the task branch and opens the PR; a human alone reviews and merges.
