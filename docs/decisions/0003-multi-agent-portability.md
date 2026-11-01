# 0003. Portability of the toolkit to Cursor, OpenAI Codex and Google Antigravity

- **Status:** Proposed
- **Date:** 2026-11-01
- **Issue:** #49 (spike), parent #48; builds on 0002 (plugin distribution, PR #52)
- **Evaluated against:** Claude Code `2.1.314` and Codex CLI `0.160.0` (installed, `--version`). Cursor and Antigravity are **not installed** on the evaluation machine; their facts come from the docs only. Docs fetched on 2026-11-01:
  - Codex: `developers.openai.com/codex/{guides/agents-md,skills,subagents,hooks,custom-prompts,config-reference}`
  - Cursor: `cursor.com/docs/{rules,skills,subagents,hooks,plugins,reference/plugins,reference/third-party-hooks,cli/using}.md`; Cursor CLI changelog latest entry `v2026.10.22`
  - Antigravity: `antigravity.google/docs/{rules,skills,subagents,customization-discovery,hooks,plugins,build-plugins,ide/workflows}`; changelog latest App `v2.28.2` (2026-10-29)
  - Agent Skills standard: `agentskills.io/specification.md`; AGENTS.md: `agents.md`
- **Labels:** **[V]** verified locally in a temporary HOME; **[D]** documented, not run; **[A]** assumption.

## Context

The toolkit installs Claude Code content only: `claude/CLAUDE.md`, 3 rules, 17 agents, 8 skills and notification hooks, plus an opt-in Codex `notify` adapter. Companies in the pilot may use other coding agents. The owner asked how the same content could plug into Cursor, Codex and Antigravity without breaking the rule "defined once" (AGENTS.md) or the installer's safety contract. Record 0002 keeps `CLAUDE.md` and rules as symlinks, moves agents, skills and hooks to an opt-in Claude Code plugin with a core-only symlink mode, and plans (#41) to replace the 9 skill-path references with by-name references. This record reuses both.

## Capability matrix (2026-11-01)

| Capability | Claude Code | Codex (CLI/IDE) | Cursor (IDE/CLI) | Antigravity (App/CLI/IDE) |
|---|---|---|---|---|
| Global instructions file | `~/.claude/CLAUDE.md` + user rules dir | `~/.codex/AGENTS.md` (or `AGENTS.override.md`) **[V]** | **None as a file**: User Rules live in the UI (Customize → Rules) **[D]**; the CLI reads project `AGENTS.md`/`CLAUDE.md` only | `~/.gemini/AGENTS.md`, `~/.gemini/GEMINI.md`, modular `~/.gemini/config/rules/*.md` **[D]** |
| Size limits | none relevant | project docs capped by `project_doc_max_bytes` (32 KiB); a 35,100-byte global file loaded whole **[V]** | not documented | 24,000 bytes per rule file after includes; 20,000-token budget for all always-on rules, then largest are demoted to pointers **[D]** |
| File includes | no (rules dir auto-loads) | no | `@file` is read on demand, not inlined **[D]** | `@[label]` followed by a path in parentheses inlines the file; `~/` expands to the home directory **[D]** |
| Path-scoped rules | project `.claude/rules/` | nested `AGENTS.md` | `.mdc` with `globs`, nested `AGENTS.md` | `trigger: glob`, nested `AGENTS.md` |
| Skills (Agent Skills `SKILL.md`) | user skills dir | `~/.agents/skills/` **[V]**; legacy Codex skills folder still read **[V]**; symlinked skill dirs work **[V]** | `~/.cursor/skills/`, `~/.agents/skills/`, and also the Claude and Codex user skills dirs **[D]** | `~/.gemini/config/skills/` (App and CLI), or folders listed in the personal manifest `~/.gemini/config/skills.json` **[D]** |
| Subagents, own context | yes, Markdown agents, `tools`/`disallowedTools` enforced | yes, TOML custom agents in `~/.codex/agents/`; full-history fork by default, clean only with `fork_turns: "none"`; spawns only when the user, `AGENTS.md` or a skill asks **[V]** (prompt rendered); custom agents cannot change sandbox or approvals, only disable features such as `features.shell_tool` **[D]** | yes, Markdown in `~/.cursor/agents/` and also the Claude and Codex user agents dirs (`.cursor/` wins on name clashes) **[D]**; `readonly: true` enforced, `tools` not supported | yes, Markdown in `~/.gemini/config/agents/`, clean slate; `tools` allowlist with Antigravity tool names and `commandExecutionPolicy`; an unknown tool name may hang the subagent (known issue) **[D]** |
| Hooks / notifications | Stop, StopFailure, Notification (settings merge) | `notify` (completion only) and hooks incl. `Stop`, `PermissionRequest` in `~/.codex/hooks.json`, each needing trust review **[D]** | `~/.cursor/hooks.json`; with third-party import on (default), Claude user settings hooks run, but only `Stop` of ours maps: `Notification` is "Supported: No" and `StopFailure` is not listed **[D]** | `~/.gemini/config/hooks.json`: `Stop`, tool and invocation events; no attention event **[D]** |
| Plugin packaging | yes (0002) | yes (skills, MCP, hooks) **[D]** | yes; private only through a Teams marketplace or a local copy in `~/.cursor/plugins/local` (symlinks outside it are skipped; off by default on Enterprise) **[D]** | yes, may ship `rules/AGENTS.md`; one root can hold `.claude-plugin`, `.cursor-plugin` and `.antigravity-plugin` manifests **[D]** |
| Per-project override | project `CLAUDE.md`, `.claude/` | project/nested `AGENTS.md`, `.codex/` | `.cursor/rules`, `AGENTS.md`, project wins over user | workspace rules/skills win over personal |
| Deprecated surfaces to avoid | — | custom prompts (deprecated) | — | IDE workflows: deprecated, stop working on 2026-10-19, replacement announced; skills meanwhile |

Codex PoC **[V]**: `env -i PATH=$PATH HOME=$T CODEX_HOME=$T/.codex codex debug prompt-input` (renders the model input locally; no login, no model call), with `$T` from `mktemp -d`. A generated `AGENTS.md` (`CLAUDE.md` + 3 rules, 35,100 bytes, also as a symlink) appeared whole, followed by the project `AGENTS.md`; `safe-refactor`, `web-dev` and a symlinked `e2e-testing` appeared in the skills list. A custom agent TOML did not appear in the rendered input; its registration was **not verified**.

## Classification of the content

| Content | Class | Why |
|---|---|---|
| Rule text (`claude/rules/*.md`) | Portable as is | Tool-neutral prose. Only the `~/.claude/...` path references need a decision (below) |
| `claude/CLAUDE.md` | Portable with adaptation | Names agents by role and says "in a fresh context"; each tool needs a short note on how to start one (Codex: `fork_turns: "none"`) |
| 6 skills (stack skills, `safe-refactor`, `e2e-testing`) | Portable as is | Plain Agent Skills (`name`, `description`) |
| `verify-changes`, `work-session` | Portable with adaptation | `$ARGUMENTS` and `argument-hint` are Claude Code substitutions. `disable-model-invocation` is honoured by Cursor; Codex needs `agents/openai.yaml` with `policy.allow_implicit_invocation: false` **[D]**; for Antigravity none is documented, so `work-session` may trigger on its own **[A]** |
| 17 agent bodies | Portable with adaptation | Prose ports; the frontmatter does not: Codex needs TOML, Antigravity needs mapped tool names, Cursor needs `readonly` |
| Read-only enforcement (`tools`, `disallowedTools`) | Claude Code only as written | Cursor `readonly`; Antigravity partial (omit write tools, and `run_command` or a strict `commandExecutionPolicy`); Codex partial at best (`features.shell_tool = false` also removes inspection, so mostly instruction only) |
| Notification hooks, settings merge | Claude Code only | Cursor maps only `Stop` (no attention or failure notifications) **[D]**; Codex has its own adapter; Antigravity would need a new one |
| Plugin manifests, `${CLAUDE_PLUGIN_ROOT}` | Claude Code only | Per-tool manifests if plugins are ever used (0002, option C here) |

## Path references inside the text

0002 counts 51 `~/.claude/...` references: 40 to rules, 9 to skills, 2 to the global `CLAUDE.md`.

- **Skills:** #41 rewrites the 9 as names ("the `verify-changes` skill"). Names port to all four tools.
- **Global `CLAUDE.md` (2 references, `work-session`):** not in #41's scope as 0002 defines it; this record extends #41 to reword them to "the global instructions".
- **Rules (recommended, R1):** every target also installs the 3 rule links at their current `~/.claude/rules/` destinations, so every reference resolves on disk in any tool, with zero text rewriting. These links become **shared items** (see the foundation slice). Where the rules are also in context (Codex `AGENTS.md`, Antigravity includes, Cursor User Rules), the path is just a pointer.
- **Rules (alternative, R2):** the generator rewrites the 3 known reference forms to a neutral link location per target. Cleaner for machines without Claude Code, but adds a tested text transform and a second place to look.

## How the team flow degrades

| Tool | Fresh contexts | Read-only roles | Expected quality vs Claude Code |
|---|---|---|---|
| Claude Code | yes | enforced | reference |
| Cursor | yes (Task subagents, own context) | enforced with `readonly: true`, if the Markdown agent is the one loaded | close, **if** the model delegates; delegation is model-decided **[A]** |
| Antigravity | yes (`invoke_subagent`, clean slate) | partial (tool allowlist, execution policy) | close for review roles **[A]**; generated frontmatter must use exact tool names |
| Codex | yes, only if the spawn sets `fork_turns: "none"`; otherwise the "independent" reviewer inherits the whole history | **not enforced**; Analyst, Architect and reviewers could edit files | noticeably weaker independence; read-only depends on the model following text |
| Any tool without subagents | no | no | **Large loss**: same-context review shares the author's blind spots. The main agent must say "independent review not performed" and report PARTIAL, or the user opens a new session per role with a written hand-off |

## Options considered

| | A. Docs only | **B. Link-based targets from one source** | C. Per-tool plugins | D. Neutral source, generate Claude files |
|---|---|---|---|---|
| What | README explains manual copy per tool | `install.sh --target <tool>` links sources and git-ignored generated files (`dist/<tool>/`) into each tool's home; tiny committed per-tool adapters in `targets/<tool>/` | One plugin root with Claude, Cursor and Antigravity manifests | Rewrite `claude/` as `AGENTS.md`-first content |
| Rules defined once | yes, but users copy | yes, except Cursor's pasted User Rules: an untracked copy that drifts until re-pasted | yes | yes |
| Safety contract | n/a | kept: only exact links to this checkout are created or removed; shared items stay while any target needs them | Cursor needs a copy install (no links); private distribution limits | kept |
| Global instructions | manual | Codex, Antigravity automatic; Cursor manual paste (no file exists) | Cursor and Antigravity via plugin rules; Codex plugins cannot carry `AGENTS.md` **[A]**; Claude cannot (0002) | same as B |
| Effort / risk | low / drift | medium / three tools that change monthly | high / marketplace and plan constraints | high / breaks installed machines and Claude quality |
| Reversibility | high | high, once shared-item ownership is in place (per-target uninstall keeps what other targets use) | medium | low |

## Decision (proposed)

Adopt **B**, opt-in per target, Claude Code stays the default and the reference. Use **R1** for rule paths. Generate neither Antigravity workflows (deprecated) nor Codex custom prompts (deprecated).

Per target:

- **Codex:** `dist/codex/AGENTS.md` = `targets/codex/preamble.md` (how to start a fresh-context role, read-only by instruction) + `claude/CLAUDE.md` + 3 rules, linked as `~/.codex/AGENTS.md`; skill dirs linked into `~/.agents/skills/`; agents generated as TOML (`name`, `description`, `developer_instructions` = body) in `dist/codex/agents/`, linked into `~/.codex/agents/`. Warn when an `AGENTS.override.md` would shadow it. The `notify` adapter is unchanged.
- **Cursor:** compatibility first, valid **only with today's full symlink mode** of the Claude target. In 0002's core-only + plugin mode, agents and skills live in the Claude plugin cache, which Cursor is not documented to read, so the Cursor target then needs its own links (`~/.cursor/agents/`, `~/.cursor/skills/`). Add `readonly: true` to the 10 read-only agents in the source (Claude Code tolerance of the extra key is **[A]**). Global instructions: generate `dist/cursor/user-rules.md` with a first line naming the toolkit version and content hash, and document pasting it into User Rules; the installer prints a re-paste reminder when the hash changes. The local plugin copy install is deferred because it needs a new copy-ownership mechanism and is off by default on Enterprise.
- **Antigravity:** committed wrappers `targets/antigravity/rules/ifdev-*.md` (`trigger: always_on`, plus one include per wrapper written as `@[label]` followed by the home-relative R1 path in parentheses, for example `~/.claude/rules/testing.md`; never absolute or relative paths), linked into `~/.gemini/config/rules/`, each under 24,000 bytes after expansion; skill dirs linked into `~/.gemini/config/skills/`; agents generated with mapped frontmatter (`subagent: true`, `mainAgent: false`, tools mapped from a tested table, read-only roles without write tools) into `~/.gemini/config/agents/`. A no-symlink alternative is listing the checkout's folders in the personal manifests in `~/.gemini/config/`, which need absolute paths written at install time, so that file is not a link. Hooks deferred.

Why the others lost: **A** guarantees drift and gives no read-only mapping; **C** cannot deliver Codex global instructions, needs a paid Cursor plan or copies for private use, and should wait for 0002 to land; **D** violates the compatibility rule in AGENTS.md for every installed machine.

## Effort and order

| Step | Effort (dev-days, incl. tests, review, docs) | Risk |
|---|---|---|
| 0. Foundation: #41 done first; generator (stdlib Python), `--target` in install/uninstall, shared-item ownership, validator | 3–4 | low–medium |
| 1. Codex | 2–3 | low–medium (verified PoC, tool installed) |
| 2. Cursor (compatibility path) | 1–2, plus a manual check on a Cursor machine | medium (not installed; compatibility toggle can be off) |
| 3. Antigravity | 3–5, plus a manual check on an Antigravity machine | high (not installed, fast churn, tool-name hang) |

Recommendation: there is still no observed demand (issue #49). Do nothing now beyond #41 (with the extension above), which removes the main portability blocker. When a pilot company asks, build 0 + the requested target, in the order **Codex → Cursor → Antigravity**. Do Antigravity only on concrete demand.

## Risks and revisit triggers

- **Churn.** All three tools changed these surfaces in 2026 (Antigravity deprecated workflows, Codex deprecated prompts and added hooks). Pin evaluated versions in tests and the README; recheck each target at every toolkit release.
- **Silent weaker reviews** in Codex and in tools without subagents. Mitigation: the preamble requires naming the degradation in the report.
- **Shadowing.** `AGENTS.override.md` (Codex), Cursor project rules and Antigravity workspace items win over the toolkit. This is intended, but the installer warns about the Codex override.
- **Duplicates.** Cursor also reads `~/.agents/skills/` and the Codex user agents and skills dirs. With the Claude and Codex targets both installed, Cursor sees each skill twice and may pick a Codex TOML agent (no `readonly`) over the Claude Markdown one; the name precedence between the Claude and Codex dirs is not documented. A Cursor plugin plus the Claude-dir import, or the 0002 plugin plus symlinks, also double-load. Mitigation: the installer warns when both targets are present, and the Cursor checklist checks which agent wins.
- **Pasted User Rules drift** in Cursor. Mitigation: version and hash line plus a re-paste reminder.
- **Stale build output** after `git pull` without rerunning the installer. Mitigation: the installer regenerates, and the validator flags tracked `dist/`.
- **Revisit when** Cursor offers a file-based user rule location, Codex plugins can carry instructions, a tool adds per-agent sandboxing, or demand appears for another agent.

## Implementation slicing (backlog)

1. **Foundation (dev).** Prerequisite #41 (extended to the 2 global `CLAUDE.md` references). Add `targets/` and git-ignored `dist/`; `scripts/build_targets.py` (stdlib only, deterministic); per-target item arrays next to `MANAGED_ITEMS`. Shared items (the R1 rule links) are recorded with their owning targets in the state dir; uninstalling one target removes a shared link only when no other installed target needs it. `install.sh`/`uninstall.sh --target`, with the same rules as today: validate before mutating, links only, exact-link removal, refuse symlinked parents, backups, dry run, idempotent. Tests in a temporary HOME per target, including "uninstall Codex keeps the rule links while Claude is installed" and the reverse. Validator checks: target items exist, `dist/` untracked, wrapper sizes. README and CHANGELOG with an Upgrade note.
2. **Codex target (dev).** Preamble, AGENTS.md build, agent TOML generation, skill links, `agents/openai.yaml` for `work-session`. Tests: generated files, links, override warning; optional `codex debug prompt-input` check, skipped and reported when `codex` is missing (devops decides on CI). Manual check that custom agents are offered to `spawn_agent`.
3. **Cursor target (dev, tech-writer).** `readonly: true` on the 10 read-only agents, with a proposed validator check pairing it with `disallowedTools`; `dist/cursor/user-rules.md` with version and hash, plus the re-paste reminder; own links when the Claude target runs in 0002's core-only mode; docs. Manual checklist on a Cursor machine: agents and skills listed from the Claude dirs, symlinked files followed, `readonly` honoured, which agent wins when the Codex target is also installed, the `Stop` notification firing once, and no attention or failure notifications (documented gap).
4. **Antigravity target (dev), on demand.** Wrappers with home-relative includes, tool-name map with tests, agent generation, skill links; manual checklist on an Antigravity machine (`/skills`, `/agents`, Customizations token view, symlinked files followed, includes expanded).
5. **Follow-up, independent (dev).** Codex hooks (`Stop`, `PermissionRequest`) adapter to lift the completion-only limitation of `notify`.

## Open questions

Blocking (owner): whether to build anything before demand; which target first; R1 (rule links in the Claude dir on every machine) or R2; whether Cursor's manual paste with a re-paste reminder is acceptable or a local plugin copy install is wanted.

Recorded assumptions, each reversible by dropping the dependent step:

- **A1** Claude Code ignores an unknown `readonly` agent key.
- **A2** Codex custom agents load from symlinked TOML files.
- **A3** Cursor's Claude-dir import covers user agents, skills and the `Stop` hook with our current frontmatter, and holds only in full symlink mode. Cursor's precedence between the Claude and Codex dirs is undocumented.
- **A4** Codex plugins cannot deliver global instructions.
- **A5** Cursor and Antigravity follow symlinked agent, skill and rule files (Codex does, **[V]**), and Antigravity expands the home-relative include from a symlinked wrapper.
