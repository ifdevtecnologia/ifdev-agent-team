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

- When the task or an adopted workflow (such as the `supervise-claude` skill) authorizes delivery, create the commits, push the task branch and open the pull request without asking again for each step. Human review and merge is the approval boundary.
- Without that authorization, propose the commits (files and messages) and wait for explicit approval before committing; do not push without authorization.
- Stage explicit task paths only. Never stage unrelated preexisting work; stage preexisting changes only when they are explicitly attributed to this task (file proximity alone is not ownership). Do not stage secrets or files excluded from inspection.
- Never force push (including `--force-with-lease`) or rewrite published history.
- Never push to the default branch of an established repository; the only exception is the bootstrap below.
- Do not approve your own pull request, merge it, enable auto-merge or bypass branch protection or required reviews and checks.
- Never skip hooks or signing with `--no-verify` or similar options, and do not amend existing commits unless explicitly requested.

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
