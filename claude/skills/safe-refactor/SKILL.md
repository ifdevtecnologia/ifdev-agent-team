---
name: safe-refactor
description: Procedure for a refactoring that must not change observable behavior in any repository — confirm the reason, pin current behavior with characterization tests, change the structure in small verified steps and keep refactoring separate from behavior changes. Use when a task, story or review finding asks to simplify, deduplicate, extract, rename, split or restructure existing code, or to pay down technical debt.
---

# Safe refactor

A refactoring changes structure, not behavior. The yardstick for what to improve is `~/.claude/rules/engineering-principles.md`; the test rules are in `~/.claude/rules/testing.md`. This skill adds the procedure that keeps the change safe.

## 1. Confirm the reason

- Name the concrete problem (duplication that already caused a bug, a unit too complex to change safely, a responsibility in the wrong place) and the cost of leaving it. Taste is not a reason.
- Check the project's decisions (architecture records, specs, rules) first; a refactor that contradicts one needs the decision revisited, not ignored.
- Bound the scope: list the files and the public interfaces that will change. Anything outside it is a separate task.

## 2. Pin current behavior

- Find the tests that exercise the code. When coverage is missing or only covers the happy path, first add characterization tests that record what the code does today through its public interface, including edge and error cases.
- A characterization test records current behavior, even when it looks wrong. If you find a bug, report it and fix it in a separate change; do not fold it into the refactor.
- Run the tests and keep the green baseline before changing structure.

## 3. Change in small steps

- One kind of move at a time: rename, extract, inline, move, replace a conditional with polymorphism, introduce a value object. Run the tests after each step.
- Keep public contracts stable. A contract whose callers are all inside the repository may change in steps: add the new form, migrate the callers, then remove the old one. Changing a contract used outside the deployable unit or published (APIs, events, database schema, library interfaces) is not a refactor: stop and report it.
- Delete dead code instead of commenting it out; version control keeps the history.
- Prefer the IDE-safe or mechanical form of each move (consistent renames, compiler-guided changes) over hand edits across many files.
- Stop and report when a step needs a behavior change, breaks a contract or grows past the planned scope.

## 4. Deliver

- Keep the refactoring separable from behavior changes so the delivery can make them separate commits (`♻️ refactor`) and, when large, separate pull requests, following `~/.claude/rules/git-workflow.md`.
- In the report and the pull request, say what changed structurally, why, how behavior was pinned (the tests), and that observable behavior did not change.
- Run the complete relevant checks at the end, not only the tests of the touched files: refactors break callers.

## Signs it went wrong

More files, layers or configuration than before without a matching gain; an abstraction with one implementation and no real boundary; tests rewritten to fit the new structure instead of the old behavior; a diff that mixes refactor and feature. Undo those parts of your own change.
