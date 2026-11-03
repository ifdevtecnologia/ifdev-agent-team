# Project instructions

<One or two sentences: what this repository is and who uses it.>

These instructions complement the global defaults in `~/.claude/CLAUDE.md` and take precedence where they are more specific or explicitly conflict. Area-specific rules are in `.claude/rules/` and load only for their paths.

## Stack

- <language and version>, <framework and version>
- <database or storage>
- <how the repository is organized, for example: `<app directory>/` web client, `<service directory>/` API>

## Validation

Run from the repository root, in this order. All commands are non-interactive and local only.

```bash
<install command>
<format check command>
<lint command>
<test command>
<build command>
```

- <area>: `<area test command>` (needs <local service, started with `<start command>`>).
- Never run against a shared or production environment: <how the local configuration is selected>.

## Domain rules

- <Rule stated as a testable fact, for example: an amount is stored as an integer in minor units, never as a float.>
- <Rule about who may read or change which data.>
- Glossary: <term> means <definition>.

## Overrides of global defaults

Delete the lines that do not apply.

- Work tracking: <this project does not use a GitHub Project board; do not propose one | issues live in <tracker>>.
- Release notes: <this project keeps no CHANGELOG.md | add an entry to <release notes file>>.
- Git (stricter than the global workflow): <branch name format>; commit scopes are <scope list>.

## Project reviewers

Run alongside the global Reviewer when the diff meets the criterion; they complement the global reviewers and never replace them.

- `domain-reviewer`: when the diff touches <paths or concern>.
