# 0002. Distribute agents, skills and hooks as a Claude Code plugin (hybrid)

- **Status:** Proposed
- **Date:** 2026-11-01
- **Issue:** #39 (spike), parent #38; follow-ups #40, #41, #42
- **Evaluated against:** Claude Code `2.1.314` (`claude --version`). Official docs fetched on 2026-11-01: `docs.claude.com/en/docs/claude-code/{plugins,plugins-reference,plugin-marketplaces,sub-agents,skills,memory,hooks}` and `code.claude.com/docs/en/plugins/{components,loading,marketplace-reference,install}`. The plugin feature changes often; revisit this record when the CLI's minor version moves a lot.

## Context

`scripts/install.sh` symlinks the 32 `MANAGED_ITEMS` into `$HOME`: the global `CLAUDE.md`, 3 rules, 17 agents, 8 skills and 3 notification scripts. It also merges three hooks into the user's Claude Code settings file through `scripts/lib/claude_hooks.py`. This needs a clone that stays in place, and it shows no installed version. Claude Code plugins and marketplaces package components with a version and an update path. This record decides whether, and how, the toolkit should use them.

## What a plugin can and cannot deliver (Claude Code 2.1.314)

| Component | Plugin? | Source / evidence |
|---|---|---|
| Agents (`agents/*.md`) | Yes. Namespaced `<plugin>:<name>`. `permissionMode`, `hooks`, `mcpServers` and `initialPrompt` are ignored | plugins/components "Agents"; sub-agents "Plugin subagents" |
| Skills (`skills/<name>/SKILL.md`, with `references/`) | Yes. Namespaced `/<plugin>:<name>` | plugins/components "Skills"; skills "Resolve skills that share a name" |
| Hooks (`hooks/hooks.json`, same shape as the settings file) | Yes. Registered whenever the plugin is enabled | plugins/components "Hooks"; PoC |
| Commands, MCP, LSP, output styles, monitors, `bin/`, plugin settings (`agent` and `subagentStatusLine` only) | Yes | plugins-reference "Standard layout" |
| Global user `CLAUDE.md` | **No.** A `CLAUDE.md` at the plugin root is not loaded as context, and `claude plugin validate` warns | plugins-reference "Standard layout"; plugins/components "Skills" |
| User-level rules | **No.** There is no rules component; rules load only from the user rules directory or a project's `.claude/rules/` | memory "User-level rules"; plugins-reference "Fields" |
| Writing to arbitrary user config paths | **No.** A component path outside the plugin root, including through a symlink, is rejected | plugins/loading "Paths that escape the plugin directory" |

A SessionStart hook could inject the global instructions through `additionalContext`, but that text is capped at 10,000 characters per string with no setting to raise it; anything longer is saved to a file that Claude is not told to read (hooks "additionalContext"). `claude/CLAUDE.md` is 13,336 characters and the rules add 21,302 more, so this workaround is not viable.

Other facts that matter here:

- **Version.** `plugin.json` `version` pins the version, and `claude plugin list --json` shows it.
- **Updates.** Auto-update is off by default for third-party marketplaces.
- **Private repos.** A private GitHub marketplace uses the machine's own git credentials. This repository is private.
- **Local-path marketplaces.** A plugin with a relative path in a marketplace added from a local path loads **in place**: edits take effect at the next session, the same as symlinks (plugins/loading "In-place and copied plugins"; confirmed in the PoC).

## Hard-coded paths in the installed content

A grep for home-relative config paths under `claude/` found 51 references in 21 files (excluding a comment in `claude/hooks/agent-notify-hook.sh`):

| Reference | Count | Where |
|---|---|---|
| rules `testing.md` | 20 | CLAUDE.md (3), dev, devops, qa, reviewer, debugger, db-reviewer, 7 skills, 2 rules |
| rules `engineering-principles.md` | 12 | CLAUDE.md, architect, dev, devops, reviewer, 7 skills |
| rules `git-workflow.md` | 8 | CLAUDE.md, safe-refactor, verify-changes (+ref), work-session (3, +ref) |
| skills `<name>/SKILL.md` (generic) | 5 | CLAUDE.md, dev, qa, reviewer, security-reviewer |
| skills `safe-refactor`, `e2e-testing`, `supabase-dev`, `java-spring-dev` | 4 | dev, qa, db-reviewer (2) |
| global `CLAUDE.md` | 2 | work-session |

What changes when agents and skills come from a plugin:

- **Rules.** In the hybrid, rules stay in the user rules directory, so all 40 rule references keep working unchanged. A full migration would need the rules copied inside the plugin and referenced as `${CLAUDE_PLUGIN_ROOT}/rules/...`; Claude Code substitutes that variable in agent and skill bodies, but not in `CLAUDE.md`, which is not plugin content.
- **Skill paths.** The 9 skill paths would point to files that no longer exist, because plugin files live in the plugin cache or the in-place source. Replace them with a reference by name ("the `verify-changes` skill", invoked through the Skill tool as `ifdev:verify-changes`). Use `${CLAUDE_PLUGIN_ROOT}/skills/<name>/SKILL.md` only inside agent and skill bodies.
- **Agent names.** Agents become `ifdev:dev` and so on, while `CLAUDE.md` names agents by bare name. The docs say a bare name resolves for a plugin agent (`claude --agent`); whether the Agent tool does the same is **not verified** (assumption A2).
- **Validator.** `scripts/validate.py` (`GLOBAL_REFERENCE` and `MANAGED_REFERENCE_PREFIXES`) must learn the new reference forms.

## Coexistence of plugin and symlink installs

| Component | Both installed | Effect |
|---|---|---|
| Agent `dev` | user agent (priority 4) and `ifdev:dev` (plugin, priority 5) | Assumed: both register, because the names differ, which costs tokens and makes delegation ambiguous; a bare `dev` likely resolves to the user agent. The docs only say same-name agents resolve by priority; not verified in a session |
| Skill `safe-refactor` | `/safe-refactor` and `/ifdev:safe-refactor` | Both load, because plugin skills are namespaced (skills docs) |
| Notification hooks | settings file and plugin `hooks.json` | Both run, because the two command strings differ (settings path vs `${CLAUDE_PLUGIN_ROOT}` path). Result: double notifications |
| `CLAUDE.md`, rules | Symlink only | No conflict |

The plugin and the symlinked agents, skills and hooks must therefore be **exclusive** on a machine.

`RETIRED_ITEMS` does not fit this migration: its contract is "sources no longer exist", but in the hybrid the sources remain as the plugin's content. Migration needs an installer mode that installs only the core and removes, through the existing exact-link checks, only links to this checkout's agents, skills and `agent-notify-hook.sh`, plus the toolkit's own settings hook entries. It keeps `.claude/hooks/agent-notify.sh` and the Codex adapter, because `codex/notify/agent-notify-codex.sh` calls `$HOME/.claude/hooks/agent-notify.sh` directly.

## Notification hooks as plugin hooks

`claude/hooks/hooks.json` reproduces `HOOK_COMMANDS` and `HOOK_MATCHERS`: the command becomes `bash "${CLAUDE_PLUGIN_ROOT}/hooks/agent-notify-hook.sh" <kind>`, and the Notification matcher is unchanged.

- There is no settings merge or backup and no `claude_hooks.py` path for plugin users; uninstalling or disabling the plugin removes the hooks.
- `agent-notify-hook.sh` finds `agent-notify.sh` through `SCRIPT_DIR`, and the plugin cannot reach `scripts/`, which is outside the plugin root. So `scripts/agent-notify.sh` must move to `claude/hooks/agent-notify.sh`, keeping the `MANAGED_ITEMS` destination. Moving a managed source follows the AGENTS.md compatibility rules: after `git pull` and before the installer is rerun, existing links dangle and notifications stop, and the installer would treat the dangling link as foreign and back it up. #40 therefore adds an explicit stale-source rule (the old source path is recognized as this checkout's own and relinked, and uninstall removes it) or a retired entry for the old source, tests for both, and an **Upgrade** note in the CHANGELOG.
- Plugin hooks receive `CLAUDE_PLUGIN_ROOT` in their environment and run in subagents as well, the same as today.

## Options considered

| | A. Keep symlink only | **B. Hybrid** (plugin: agents, skills, hooks; symlink: `CLAUDE.md`, rules) | C. Full migration to the plugin | D. Skills-dir plugin (symlink `claude/` as one skills folder) |
|---|---|---|---|---|
| Delivers the team process (`CLAUDE.md`) | Yes | Yes (symlink) | **No**; only through a capped SessionStart hook, which truncates | Yes (symlink) |
| Version and update | No | Yes for components; the core follows `git pull` | Yes | No (loads in place, no version) |
| Clone required | Yes | Yes for the core | No | Yes |
| Effort | None | Medium | High, and it loses behaviour | Low |
| Risk | Status quo | Double loading if both modes are installed; two install paths to test | Loss of global instructions | Namespacing churn without the benefit |
| Reversibility | n/a | High: uninstall the plugin and rerun the full installer | Low | High |

## Decision

Adopt **B. Hybrid**, kept opt-in:

- The repository becomes a marketplace (`.claude-plugin/marketplace.json`, name `ifdev-ai-tools`) with a single plugin (`ifdev`) whose root is `claude/`.
- The plugin carries `agents/`, `skills/` and `hooks/` (`hooks.json` and the scripts). `claude/CLAUDE.md` stays outside the plugin; it may need moving to avoid the validator's root `CLAUDE.md` warning, decided in #40.
- `install.sh` keeps today's full mode as the default and gains a core-only mode (`CLAUDE.md` and rules).
- The plugin version is `0.y.z` and equals the CHANGELOG release.
- Nothing is published outside the repository without the owner's explicit authorization.

Why the others lost: **A** leaves the versioning and update need of #38 unmet; **C** cannot deliver `CLAUDE.md` or the rules; **D** adds namespacing without a version.

## Proof of concept (local, isolated)

Everything ran with `env -i PATH=$PATH HOME=$T CLAUDE_CONFIG_DIR=$T/.claude DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, with `M` and `T` both from `mktemp -d`. No credentials were read, no prompt was sent to a model and nothing was published.

`M` held `.claude-plugin/marketplace.json` (`name: ifdev-poc`, one entry `ifdev-ai-tools` with `source: ./plugins/ifdev-ai-tools`), `plugins/ifdev-ai-tools/.claude-plugin/plugin.json` with `version: 0.0.0-poc`, and `agents/dev.md` and `skills/safe-refactor/SKILL.md` copied from the repository.

```text
claude plugin validate $M                        -> Validation passed (exit 0)
claude plugin validate $M/plugins/ifdev-ai-tools -> passed; directory-profile findings only:
                                                    README_MISSING, LICENSE_MISSING, ICON_MISSING
claude plugin marketplace add --json $M          -> {"outcome":"ok","marketplace":"ifdev-poc", ...}
claude plugin install --json ifdev-ai-tools@ifdev-poc -> {"outcome":"ok","scope":"user"}
claude plugin list --json                        -> id ifdev-ai-tools@ifdev-poc, version 0.0.0-poc,
                                                    enabled, installPath $T/.claude/plugins/cache/
                                                    ifdev-poc/ifdev-ai-tools/0.0.0-poc,
                                                    readFromFolder $M/plugins/ifdev-ai-tools
claude plugin details ifdev-ai-tools@ifdev-poc   -> Skills (1) safe-refactor; Agents (1) dev;
                                                    always-on ~190 tok
```

- The temporary settings file gained `extraKnownMarketplaces.ifdev-poc` (`source: directory`) and `enabledPlugins["ifdev-ai-tools@ifdev-poc"]: true`; `plugins/known_marketplaces.json`, `plugins/installed_plugins.json` and a cache copy were created.
- Hooks: adding `hooks/hooks.json` (Stop, StopFailure, Notification with the current matcher) after the install, without an update, made `details` report `Hooks (3) Stop, StopFailure, Notification`, confirming that a local-path marketplace loads in place.
- Uninstall: `claude plugin uninstall --json ifdev-ai-tools@ifdev-poc` returned `ok`; the list became `[]`, `enabledPlugins` became `{}`, and the cached version was marked orphaned (removed after 14 days). The marketplace stays declared until `marketplace remove`.
- The first `marketplace add` without `--json` timed out after 60 s; a retry with `--json` succeeded at once. The cause was not investigated; automated tests use `--json`.

Not verified, because each needs a model session: substitution of `${CLAUDE_PLUGIN_ROOT}` in agent and skill bodies; whether the Agent tool resolves a bare agent name to the plugin agent; how duplicates actually appear in a session.

## Consequences and risks

- **Double loading.** A machine with both modes gets duplicate agents and skills and double notifications. Mitigation: the installer detects the enabled plugin and refuses or switches to core-only, and the README documents exclusivity.
- **Two install paths.** They double the test surface; a test must compare the plugin inventory with `MANAGED_ITEMS`.
- **Core and plugin drift.** The core follows the clone's HEAD while the plugin is a pinned cached version, so `CLAUDE.md` can name agents or skills the installed plugin version lacks. Mitigation: release both together (plugin version equals the CHANGELOG release) and have #42 report both versions.
- **Feature churn.** Claude Code changes plugin behaviour often. Pin the evaluated version in tests and the README, and revisit on breaking notes.
- **Private repo.** Every machine needs git credentials to add the marketplace, and background auto-update needs them non-interactively.
- **No updates by default.** Updates are manual unless `autoUpdate` is enabled, and the `version` in `plugin.json` must be bumped on every release or users stay on the old copy.
- **Name change.** Agents and skills are renamed to `ifdev:*`; texts that mention paths must refer to skills by name.
- **Revisit when** Claude Code adds a plugin component for memory (`CLAUDE.md`) or rules, or raises the `additionalContext` cap; option C would then become viable.

## Implementation slicing

1. **#40, dev (with validator and tests).** Add `claude/.claude-plugin/plugin.json` (`name: ifdev`, `version` equal to the CHANGELOG version) and `.claude-plugin/marketplace.json` (`source: ./claude`); add `claude/hooks/hooks.json` and move `agent-notify.sh` into `claude/hooks/` with the stale-source handling and Upgrade note above; teach the validator manifest ↔ `MANAGED_ITEMS` consistency, version equals CHANGELOG, and no root `CLAUDE.md` inside the plugin; add a test that runs `claude plugin validate`, `marketplace add` and `install` in a temporary HOME and compares agents and skills with `MANAGED_ITEMS` (skipped and reported when `claude` is missing), using the real layout with `claude/` as the plugin root; update the README (two install paths, exclusivity) and the CHANGELOG. devops decides whether CI installs the `claude` CLI for that test. Publishing anywhere outside the repository needs the owner's authorization.
2. **#41, dev.** Reword the 9 skill-path references to by-name references and update the validator's mapping; add `install.sh --core` (or detection of the enabled plugin) that installs `CLAUDE.md` and rules, removes only exact links to this checkout's agents, skills and `agent-notify-hook.sh`, keeps `agent-notify.sh` and the Codex adapter, and removes the toolkit's hook entries through `claude_hooks.py`, keeping dry-run and idempotency; write the migration guide and the reverse path; tests in a temporary HOME cover full → core migration, foreign links kept, Codex notify still working and rerun idempotency; one manual session check for A2 and the substitution of `${CLAUDE_PLUGIN_ROOT}`.
3. **#42, dev.** Reduce scope to a read-only status of the symlinked core (`CLAUDE.md`, rules, hooks in symlink mode) plus the plugin version via `claude plugin list --json` when available, or close it if the plugin's own listing is enough.

## Open questions

Blocking (owner):

- Plugin and marketplace names (`ifdev` / `ifdev-ai-tools`).
- A licence (needed only for Anthropic's directory checks).
- The auto-update policy.
- Whether git credentials for the private repository on every machine are acceptable.
- What happens to #42.

Recorded assumptions:

- **A1.** Exclusivity is enforced by the installer, not by Claude Code; reversible if Claude Code adds deduplication.
- **A2.** A bare agent name in `CLAUDE.md` resolves to `ifdev:<name>` when no user agent exists. Verify in #41; if it fails, switch the names in `CLAUDE.md` to the scoped form.
