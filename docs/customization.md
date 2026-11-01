# Customizing the toolkit per project

This guide is for the tech lead of a team that adopts the toolkit. It shows how to adapt a repository to your stack, commands, domain rules and reviewers **without editing the global toolkit**. Starting files are in [`templates/project/`](templates/project/).

## What stays global and what goes in the project

| Global (this toolkit, installed into `~/.claude/`) | Project (versioned in each repository) |
| --- | --- |
| Lifecycle, team roles and when each role runs ([`claude/CLAUDE.md`](../claude/CLAUDE.md)) | Stack, architecture and module layout |
| Engineering principles, testing and Git rules ([`claude/rules/`](../claude/rules/)) | Build, lint, test and validation commands |
| The seventeen team agents ([`claude/agents/`](../claude/agents/)) | Domain and business rules, glossary |
| General-purpose and stack skills ([`claude/skills/`](../claude/skills/)) | Rules for one area of the code (`.claude/rules/` with `paths`) |
| Changelog convention, safety and delivery limits | Project reviewers (`.claude/agents/`) and procedures (`.claude/skills/`) |

Do not put product names, business domains or project reviewers in the toolkit; see [CONTRIBUTING.md](../CONTRIBUTING.md#scope). Changes to the global defaults go through a pull request to this repository.

## The precedence rule

The installed [`claude/CLAUDE.md`](../claude/CLAUDE.md) states:

> Project instructions (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/`, `.claude/skills/`, `.claude/agents/`) take precedence when more specific or explicitly conflicting; state the tradeoff when you follow them.

Claude Code itself does not merge or override instruction files: it loads the global and project files together, and when two instructions contradict each other it may follow either ([Claude Code memory docs](https://code.claude.com/docs/en/memory)). The precedence comes from the sentence above, so make every override **explicit**: name the global default you are replacing and the reason. Silent contradictions are unreliable.

## Project files and how they load

All paths are relative to the repository root. The loading behavior below comes from the official Claude Code documentation ([memory](https://code.claude.com/docs/en/memory), [subagents](https://code.claude.com/docs/en/sub-agents), [skills](https://code.claude.com/docs/en/skills)).

### `CLAUDE.md` and `AGENTS.md`

- `CLAUDE.md` loads in full at every session start. `@path` imports inside it are expanded at the same time, so an import does not save context.
- `AGENTS.md` is also read by other coding agents (for example Codex). Claude Code reads it directly only when no `CLAUDE.md` exists, so keep the shared content in `AGENTS.md` and import it from a short `CLAUDE.md`. This repository uses the same layout: its [`CLAUDE.md`](../CLAUDE.md) contains `@AGENTS.md`.
- Put validation commands in a clearly titled section. The Dev, QA and the `verify-changes` skill read these files and the rules for the changed areas to find the project's verification commands; `verify-changes` falls back to manifests and CI only after them.
- Personal, machine-specific notes go in a git-ignored `CLAUDE.local.md`, never in the shared files.

Templates: [`CLAUDE.md`](templates/project/CLAUDE.md) and [`AGENTS.md`](templates/project/AGENTS.md).

### `.claude/rules/`

- A rule without frontmatter loads every session, like `CLAUDE.md`.
- A rule with a `paths` field in its YAML frontmatter loads only when Claude reads or edits a matching file. `paths` takes a YAML list of glob patterns (or a comma-separated string), relative to the directory that contains `.claude/`. It is the only frontmatter field Claude Code reads in a rule.
- If the frontmatter is not valid YAML, Claude Code ignores it and loads the rule every session, so a typo silently removes the scoping.
- Do not `@`-import a path-scoped rule from `CLAUDE.md`: the import loads it every session.

```yaml
---
paths:
  - "src/api/**/*.ts"
  - "db/migrations/**"
---
```

Keep one file per area (database, web, mobile, contracts) and give each a verification section with that area's commands. See also [Scoped project rules](../README.md#scoped-project-rules). Template: [`.claude/rules/area.md`](templates/project/.claude/rules/area.md).

### `.claude/agents/`

Project agents use the same frontmatter as the global agents: `name` (lowercase kebab-case, matching the file name), `description` (what it reviews and when to use it), `tools` and, for a read-only reviewer, `disallowedTools: Write, Edit`. The global agents do not set `model`; add it only if you have a reason.

- **Project reviewers complement the global ones and do not replace them.** The global Reviewer and the specialized reviewers still run on their own criteria; your reviewer adds domain checks (for example access rules for your data model). When the diff touches its domain, the global Reviewer recommends it in its report instead of invoking it; list each project reviewer and its criterion in `AGENTS.md` so the main agent knows when to run it.
- **Give it a unique name.** A project agent with the same `name` as a global one (for example `reviewer`) takes priority over it and replaces it in that project, which breaks the team flow. Use names such as `access-reviewer` or `billing-rules-reviewer`.
- Read-only means read-only: the blocked tools do not stop writes through Bash, so the agent body must say it does not write.

Template: [`.claude/agents/domain-reviewer.md`](templates/project/.claude/agents/domain-reviewer.md).

### `.claude/skills/`

A project skill is a directory `.claude/skills/<name>/` with a `SKILL.md` whose frontmatter has `name` and `description`. Use one for a repeatable project procedure (for example generating API clients or a release checklist), and keep phase-specific detail in `references/` files linked from `SKILL.md`, like the global skills do.

When a project skill and a global skill share a name, Claude Code runs the **global** one. Do not try to override `verify-changes` or a stack skill by copying it into the project. Give your skill a new name, or put the project-specific part in a rule that the global skill already reads.

## Overriding or switching off a global default

Below are common overrides the global rules explicitly allow; other more specific project instructions also take precedence, within the limits listed after the table. Each example is a line for your project's `AGENTS.md` (or `CLAUDE.md`).

| Situation | What the global rule says | What to write in the project |
| --- | --- | --- |
| No GitHub Project board | [git-workflow.md](../claude/rules/git-workflow.md#project-board): without a GitHub Project, propose creating one once, *unless the project's instructions name another tracker or opt out*. | `Work tracking: this project does not use a GitHub Project board; do not propose one.` or `Work tracking: issues live in <tracker>; reference them as <issue key format>.` |
| Board without preview or staging | [git-workflow.md](../claude/rules/git-workflow.md#project-board): statuses that do not apply to the product are skipped; without a preview or staging, an item goes from the merge to To Deploy, or to Done when nothing is deployed. | `Board: there is no preview or staging; after the merge, items go to To Deploy (Done when nothing is deployed).` |
| No `CHANGELOG.md` | [claude/CLAUDE.md](../claude/CLAUDE.md#changelog): the changelog entry is required *when the project keeps a `CHANGELOG.md`*. | Nothing is needed. If you record changes elsewhere, say where: `Release notes: add an entry to <release notes file> instead of a CHANGELOG.md.` |
| Stricter Git workflow | [git-workflow.md](../claude/rules/git-workflow.md): it applies *unless the project documents a stricter workflow*; scopes follow the ones the project documents. | `Git: branch names include the issue number (<type>/<issue>-<topic>); commit scopes are <scope list>; pull requests need two human approvals.` |
| Own validation commands | [testing.md](../claude/rules/testing.md): follow the project's testing conventions and select commands from the project. | A `Validation` section with the exact non-interactive commands, as in the [`AGENTS.md` template](templates/project/AGENTS.md). |
| Different testing tools or conventions | [testing.md](../claude/rules/testing.md): explicit project conflicts take precedence and must be explained. | `Testing: unit tests live next to the source as <pattern>; we do not write snapshot tests because <reason>.` |
| Code conventions that differ from the principles | [engineering-principles.md](../claude/rules/engineering-principles.md): on explicit conflict, follow the project's conventions and explain the tradeoff. | `Conventions: <convention> because <reason>.` |

The toolkit offers no project opt-out for these, so do not write project instructions that try to relax them:

- the Git workflow in a looser direction: a project can only make it stricter;
- the safety rules in `claude/CLAUDE.md` (secrets, destructive operations, permissions and hooks, the user's work) and the deploy and remote-operation limits;
- the completion rule in `testing.md`: mandatory checks and test coverage for meaningful behavior stay mandatory;
- the team flow: roles are skipped only on the criteria `claude/CLAUDE.md` defines, and never silently.

An explicit restriction from the user in a session still wins over any project or global default.

## Pitfalls

- **No secrets.** Never put tokens, keys, passwords, connection strings or real personal data in `CLAUDE.md`, `AGENTS.md` or `.claude/`. These files are versioned and loaded into every session. Refer to where a secret is configured (for example "the local `.env`, never read by agents"), not to its value.
- **No machine paths.** Use paths relative to the repository root, not absolute home directory paths. Personal notes go in `CLAUDE.local.md`.
- **Keep always-loaded files short.** `CLAUDE.md`, `AGENTS.md` and rules without `paths` load in every session, on top of the global files. Claude Code recommends staying under about 200 lines per `CLAUDE.md`. Move area-specific detail to path-scoped rules and long procedures to skills.
- **Do not copy global content.** Link to the global rule (for example `~/.claude/rules/testing.md`) instead of pasting it; copies drift when the toolkit is updated.
- **Write overrides as overrides.** "Do not propose a board" is followed; an `AGENTS.md` that just never mentions the board is not an override.
- **Check for conflicts.** Running `/doctor prompt-audit` in Claude Code reports instruction files that contradict each other or reference commands that do not exist.

## Checking your project files

The toolkit's validator checks its own repository and does not run on your project. It validates the templates in `docs/templates/project/` like any other Markdown file in this repository (links, home paths, credential patterns, unsafe instructions). After you adapt the templates:

1. Replace every `<placeholder>`; `grep -rn '<[a-z]' CLAUDE.md AGENTS.md .claude/` should find none left.
2. Confirm that each frontmatter block parses as YAML and that each agent's `name` matches its file name.
3. Start a session and run `/context` and check **Memory files** to see which instruction files loaded (`/memory` lists locations, including files that do not exist yet); open a file covered by a path-scoped rule and confirm the rule loads only then.
