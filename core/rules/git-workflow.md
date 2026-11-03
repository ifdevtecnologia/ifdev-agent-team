# Git workflow

Applies to every repository unless the project documents a stricter workflow. Explicit user restrictions (for example "do not commit") always win and are reported as intentionally omitted delivery.

## Delivery flow

1. **Task → branch:** create a dedicated branch from the verified default/base branch, named `<type>/<short-topic>` (for example `feat/user-invitations`, `fix/session-timeout`, `docs/local-setup`). Do not implement on the default branch.
2. **Implementation** with the tests required by `~/.claude/rules/testing.md`.
3. **Tests and review:** run the relevant checks and review the diff before committing.
4. **Logical commits:** one commit per logical change, using the message convention below.
5. **Push** the task branch to the expected remote.
6. **Pull request** targeting the default branch, with summary, validation actually executed, risks and notes.
7. **Human review and merge.** A human reviews and merges; agents do not.

## Authority

- When the task or an adopted workflow authorizes delivery, create the commits, push the task branch and open the pull request without asking again for each step. Human review and merge is the approval boundary.
- Without that authorization, propose the commits (files and messages) and wait for explicit approval before committing; do not push without authorization.
- Stage explicit task paths only. Never stage unrelated preexisting work; stage preexisting changes only when they are explicitly attributed to this task (file proximity alone is not ownership). Do not stage secrets or files excluded from inspection.
- Never force push (including `--force-with-lease`) or rewrite published history.
- Never push to the default branch of an established repository; the only exception is the bootstrap below.
- Do not approve your own pull request, merge it, enable auto-merge or bypass branch protection or required reviews and checks.
- Never skip hooks or signing with `--no-verify` or similar options, and do not amend existing commits unless explicitly requested.

## Project board

When the product uses a GitHub Project (owned by the repository's organization or user), its development work is tracked there, one project per product, with this `Status` field in this order:

| Status | Meaning |
|---|---|
| Backlog | Captured, not yet prioritized or refined |
| To Do | Prioritized and refined, with acceptance criteria; ready to start |
| Doing | Being implemented on a task branch |
| Review | Independent review of the diff: Reviewer and the specialized reviewers that apply |
| To Tests | No material review findings left; waiting for QA |
| Testing | QA verifies against the acceptance criteria |
| To Homologation | Verified; pull request open for human review and merge, or waiting for a preview or staging |
| Homologation | Available (preview or staging) for the user or client to validate |
| To Deploy | Merged and approved in homologation; waiting for an authorized deployment |
| Done | Deployed, or merged when there is nothing to deploy |
| Canceled | Discarded; will not be done |

- Each item is an issue in the product's repository. A draft item is allowed only while the repository does not exist and becomes an issue before leaving To Do. Link it to its user story or acceptance criteria.
- Reference the issue from the pull request with `Refs #<number>` when homologation or a deployment follows the merge; use a closing keyword (`Closes #<number>`) only when the merge itself finishes the work. Closing keywords and the project's default "closed" or "merged" automations move items to Done.
- When the task or an adopted workflow authorizes delivery, the main agent also creates the task's issue when it is missing (starting in Doing) and moves its item from To Do up to To Homologation as the work progresses, including back to Doing when review or QA finds problems, without asking again for each move. Moving an item from Backlog to To Do is a prioritization decision for the user.
- Statuses that do not apply to the product are skipped: without a preview or staging, an item goes from human merge to To Deploy, or to Done when nothing is deployed. Making an item available for homologation follows the project's preview automation or an explicitly authorized deployment.
- Canceling is the user's decision: the agent proposes it with the reason (for example duplicate, obsolete or out of scope) and, once the user agrees, closes the issue as not planned and moves the item to Canceled. It proposes closing the item's open pull request and deleting its branch, without doing either on its own; a pull request never uses a closing keyword for a canceled item.
- Moves into Homologation, To Deploy and Done follow the human merge, the human approval in homologation and the authorized deployment; agents never make them on their own judgment.
- Without delivery authorization, propose the issue and the moves together with the proposed commits. When board access is missing (for example a token without the `project` scope), report it and continue with the Git delivery.
- A product without a GitHub Project: propose creating one once, unless the project's instructions name another tracker or opt out. Creating or changing projects, fields, views or automations needs explicit authorization.
- The board holds executable work only; vision, requirements and decisions stay in the product's documentation.

## Bootstrap exception

The first publication of a brand-new repository may target its default branch directly only when BOTH conditions hold:

- the user explicitly authorized this one-off bootstrap for this specific repository; and
- the remote was verified to be completely empty (for example `git ls-remote <remote>` returned zero refs) immediately before publishing.

This exception is never general permission. After the bootstrap, all further work uses task branches and pull requests.

## Commit messages

Conventional Commits with a leading emoji:

```
<emoji> <type>[(scope)][!]: <description>
```

| Emoji | Type | Use |
|---|---|---|
| ✨ | `feat` | new feature |
| 🐛 | `fix` | bug fix |
| 📝 | `docs` | documentation only |
| ♻️ | `refactor` | structural change without behavior change |
| ✅ | `test` | add or fix tests |
| 🔧 | `chore` | maintenance, configuration, tasks without product effect |
| ⚡️ | `perf` | performance improvement |
| 👷 | `ci` | CI pipeline |
| 📦️ | `build` | build system, packaging, dependencies |

Use only these types with exactly these emojis (including the variation selector in ♻️, ⚡️ and 📦️).

- The description is always in English, in the imperative mood (`add`, `fix`, `remove`; not `added`/`adds`), starts with a lowercase letter and has no final period.
- Keep the subject short (ideally up to about 72 characters).
- Use a scope only when it adds context; prefer scopes documented by the project.
- One commit per logical change. Separate refactors from features/fixes when they can be separated.
- Optional body after a blank line explaining why, when the subject is not enough.
- Breaking change: `!` before `:` and, when needed, a `BREAKING CHANGE: <description>` footer.

```
✨ feat(auth): add Google sign-in
🐛 fix(api): reject requests for resources owned by other users
♻️ refactor(web): extract authentication state service
📝 docs: document local development setup
✨ feat!: change user invitation model

BREAKING CHANGE: replace direct account creation with invitation flow
```
