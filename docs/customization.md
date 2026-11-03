# Customizing the toolkit per project

For the tech lead of a team that adopts the toolkit: how to adapt a repository to your stack, commands, domain rules and reviewers **without editing the global toolkit**. Starting files are in [`templates/project/`](templates/project/).

## Start with `AGENTS.md`

Put the project's shared instructions in one `AGENTS.md` at the repository root. Every supported tool reads it:

| Tool | Reads the project's `AGENTS.md` | Tool-specific project files |
| --- | --- | --- |
| Codex | directly, after the global `~/.codex/AGENTS.md` | `.codex/`; nested `AGENTS.md` files for subdirectories |
| Claude Code | through a short `CLAUDE.md` that imports it with `@AGENTS.md` (Claude Code reads `AGENTS.md` directly only when no `CLAUDE.md` exists) | `.claude/rules/`, `.claude/agents/`, `.claude/skills/` |

1. Copy [`AGENTS.md`](templates/project/AGENTS.md) and fill in the stack, the **Validation** commands, the domain rules, your overrides and your project reviewers.
2. Copy [`CLAUDE.md`](templates/project/CLAUDE.md) unchanged: it only contains `@AGENTS.md`. This repository uses the same layout (its [`CLAUDE.md`](../CLAUDE.md) imports [`AGENTS.md`](../AGENTS.md)).
3. Add path-scoped rules and project reviewers only when you need them (below).

Personal, machine-specific notes go in a git-ignored `CLAUDE.local.md`, never in the shared files. The Codex loading behavior comes from [decision record 0003](decisions/0003-multi-agent-portability.md); the Claude Code behavior from the official documentation ([memory](https://code.claude.com/docs/en/memory), [subagents](https://code.claude.com/docs/en/sub-agents), [skills](https://code.claude.com/docs/en/skills)).

## What stays global and what goes in the project

| Global (this toolkit) | Project (versioned in each repository) |
| --- | --- |
| Lifecycle, team roles and when each role runs ([`core/instructions.md`](../core/instructions.md)) | Stack, architecture and module layout |
| Engineering principles, testing and Git rules ([`core/rules/`](../core/rules/)) | Build, lint, test and validation commands |
| The eighteen team roles ([`core/agents/`](../core/agents/)) | Domain and business rules, glossary |
| General-purpose and stack skills ([`core/skills/`](../core/skills/)) | Rules for one area of the code |
| Changelog convention, safety and delivery limits | Project reviewers and procedures |

Do not put product names, business domains or project reviewers in the toolkit; see [CONTRIBUTING.md](../CONTRIBUTING.md#scope). Changes to the global defaults go through a pull request to this repository.

## The precedence rule

The global instructions ([`core/instructions.md`](../core/instructions.md)) state:

> The **project's instructions** are its `AGENTS.md` plus the coding tool's own project files (for example `CLAUDE.md` and `.claude/` with its rules, skills and agents, or `.codex/`); they take precedence when more specific or explicitly conflicting; state the tradeoff when you follow them.

The tools do not merge or override instruction files: they load the global and project files together, and when two instructions contradict each other the model may follow either. The precedence comes from the sentence above, so make every override **explicit**: name the global default you are replacing and the reason. Silent contradictions are unreliable.

## Validation commands

Put them in a clearly titled `Validation` section of `AGENTS.md`, as in the template. The Dev, QA and the `verify-changes` skill read the project's instructions and the rules for the changed areas to find the verification commands; `verify-changes` falls back to manifests and CI only after them.

## Path-scoped rules (Claude Code)

Area-specific rules go in `.claude/rules/<area>.md` (in Codex, a nested `AGENTS.md` in that directory plays the same role). Template: [`.claude/rules/area.md`](templates/project/.claude/rules/area.md).

- A rule without frontmatter loads every session, like `CLAUDE.md`.
- A rule with a `paths` field in its YAML frontmatter loads only when Claude reads or edits a matching file. `paths` takes a YAML list of glob patterns (or a comma-separated string), relative to the directory that contains `.claude/`. It is the only frontmatter field Claude Code reads in a rule.
- If the frontmatter is not valid YAML, Claude Code ignores it and loads the rule every session, so a typo silently removes the scoping.
- Do not `@`-import a path-scoped rule from `CLAUDE.md`: an import loads it every session and defeats the scoping. `@path` imports are expanded at session start, so an import never saves context.

```yaml
---
paths:
  - "src/api/**/*.ts"
  - "db/migrations/**"
---
```

Keep one file per area, each with a verification section for that area's commands, for example `database.md` with `paths: ["supabase/**"]`, `web.md` with `paths: ["apps/web/**"]`, `mobile.md` with `paths: ["apps/mobile/**"]` and `contracts.md` with `paths: ["packages/contracts/**"]`.

## Project reviewers

Template: [`.claude/agents/domain-reviewer.md`](templates/project/.claude/agents/domain-reviewer.md). Project agents use the same frontmatter as the generated global Claude agents: `name` (lowercase kebab-case, matching the file name), `description` (what it reviews and when to use it), `tools` and, for a read-only reviewer, `disallowedTools: Write, Edit`. The global agents do not set `model`; add it only if you have a reason.

- **They complement the global reviewers and do not replace them.** The global Reviewer and the specialized reviewers still run on their own criteria; your reviewer adds domain checks (for example access rules for your data model). When the diff touches its domain, the global Reviewer recommends it in its report instead of invoking it. List each project reviewer and its criterion in `AGENTS.md` so the main agent knows when to run it.
- **Give it a unique name.** A project agent with the same `name` as a global one (for example `reviewer`) takes priority over it and replaces it in that project, which breaks the team flow. Use names such as `access-reviewer` or `billing-rules-reviewer`; the reserved names are listed in [team.md](team.md#project-reviewers-and-name-clashes).
- **Read-only means read-only:** the blocked tools do not stop writes through Bash, so the agent body must say it does not write.

## Project skills

A Claude Code project skill is a directory `.claude/skills/<name>/` with a `SKILL.md` whose frontmatter has `name` and `description`. Use one for a repeatable project procedure (for example generating API clients or a release checklist), and keep phase-specific detail in `references/` files linked from `SKILL.md`, like the global skills do.

When a project skill and a global skill share a name, Claude Code runs the **global** one. Do not try to override `verify-changes` or a stack skill by copying it into the project. Give your skill a new name, or put the project-specific part in a rule that the global skill already reads.

## Overriding or switching off a global default

Common overrides the global rules explicitly allow; other more specific project instructions also take precedence, within the limits listed after the table. Each example is a line for your project's `AGENTS.md`.

| Situation | What the global rule says | What to write in the project |
| --- | --- | --- |
| No GitHub Project board | [git-workflow.md](../core/rules/git-workflow.md#project-board): without a GitHub Project, propose creating one once, *unless the project's instructions name another tracker or opt out*. | `Work tracking: this project does not use a GitHub Project board; do not propose one.` or `Work tracking: issues live in <tracker>; reference them as <issue key format>.` |
| Board without preview or staging | [git-workflow.md](../core/rules/git-workflow.md#project-board): statuses that do not apply to the product are skipped; without a preview or staging, an item goes from the merge to To Deploy, or to Done when nothing is deployed. | `Board: there is no preview or staging; after the merge, items go to To Deploy (Done when nothing is deployed).` |
| No `CHANGELOG.md` | [core/instructions.md](../core/instructions.md#changelog): the changelog entry is required *when the project keeps a `CHANGELOG.md`*. | Nothing is needed. If you record changes elsewhere, say where: `Release notes: add an entry to <release notes file> instead of a CHANGELOG.md.` |
| Stricter Git workflow | [git-workflow.md](../core/rules/git-workflow.md): it applies *unless the project documents a stricter workflow*; scopes follow the ones the project documents. | `Git: branch names include the issue number (<type>/<issue>-<topic>); commit scopes are <scope list>; pull requests need two human approvals.` |
| Own validation commands | [testing.md](../core/rules/testing.md): follow the project's testing conventions and select commands from the project. | A `Validation` section with the exact non-interactive commands, as in the [`AGENTS.md` template](templates/project/AGENTS.md). |
| Different testing tools or conventions | [testing.md](../core/rules/testing.md): explicit project conflicts take precedence and must be explained. | `Testing: unit tests live next to the source as <pattern>; we do not write snapshot tests because <reason>.` |
| Code conventions that differ from the principles | [engineering-principles.md](../core/rules/engineering-principles.md): on explicit conflict, follow the project's conventions and explain the tradeoff. | `Conventions: <convention> because <reason>.` |

The toolkit offers no project opt-out for these, so do not write project instructions that try to relax them:

- the Git workflow in a looser direction: a project can only make it stricter;
- the safety rules in `core/instructions.md` (secrets, destructive operations, permissions and hooks, the user's work) and the deploy and remote-operation limits;
- the completion rule in `testing.md`: mandatory checks and test coverage for meaningful behavior stay mandatory;
- the team flow: roles are skipped only on the criteria `core/instructions.md` defines, and never silently.

An explicit restriction from the user in a session still wins over any project or global default.

## Pitfalls

- **No secrets.** Never put tokens, keys, passwords, connection strings or real personal data in `AGENTS.md`, `CLAUDE.md`, `.claude/` or `.codex/`. These files are versioned and loaded into every session. Refer to where a secret is configured (for example "the local `.env`, never read by agents"), not to its value.
- **No machine paths.** Use paths relative to the repository root, not absolute home directory paths. Personal notes go in `CLAUDE.local.md`.
- **Keep always-loaded files short.** `AGENTS.md`, `CLAUDE.md` and rules without `paths` load in every session, on top of the global files. Claude Code recommends staying under about 200 lines per `CLAUDE.md`. Move area-specific detail to path-scoped rules and long procedures to skills.
- **Do not copy global content.** Refer to the global rule by name (for example "the `testing` rule") instead of pasting it; copies drift when the toolkit is updated.
- **Write overrides as overrides.** "Do not propose a board" is followed; an `AGENTS.md` that just never mentions the board is not an override.
- **Check for conflicts.** Running `/doctor prompt-audit` in Claude Code reports instruction files that contradict each other or reference commands that do not exist.

## Checking your project files

The toolkit's validator checks its own repository and does not run on your project. It validates the templates in `docs/templates/project/` like any other Markdown file in this repository (links, home paths, credential patterns, unsafe instructions). After you adapt the templates:

1. Replace every `<placeholder>`; `grep -rn '<[a-z]' AGENTS.md CLAUDE.md .claude/` should find none left.
2. Confirm that each frontmatter block parses as YAML and that each agent's `name` matches its file name.
3. In Claude Code, run `/context` and check **Memory files** to see which instruction files loaded (`/memory` lists locations, including files that do not exist yet); open a file covered by a path-scoped rule and confirm the rule loads only then.
