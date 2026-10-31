# ifdev-ai-tools

Versioned, reproducible source of truth for **global** Claude Code instructions: engineering principles, testing and Git rules, the Analyst, Architect, UX designer, Dev, DevOps, Reviewer, QA, UX and security reviewer agents and a few general-purpose skills, plus cross-agent notifications for Claude Code and Codex. The installer links these files into `$HOME` so every machine uses the same reviewed content.

## Global versus project instructions

This toolkit only provides defaults that apply to every repository. Each project keeps its own `CLAUDE.md` (and `AGENTS.md` when used) plus `.claude/rules/`, `.claude/skills/` and `.claude/agents/` for local behavior: stack, commands, domain rules and specialized reviewers. Project instructions take precedence when they are more specific or explicitly conflict with the global defaults.

Two different `CLAUDE.md` files live here:

- [`claude/CLAUDE.md`](claude/CLAUDE.md) is the **installed** global instruction file (linked to `~/.claude/CLAUDE.md`).
- [`CLAUDE.md`](CLAUDE.md) at the repository root (with [`AGENTS.md`](AGENTS.md)) contains instructions for **developing this toolkit** and is never installed.

## Layout

```
claude/
  CLAUDE.md                          -> ~/.claude/CLAUDE.md
  rules/engineering-principles.md    -> ~/.claude/rules/engineering-principles.md
  rules/testing.md                   -> ~/.claude/rules/testing.md
  rules/git-workflow.md              -> ~/.claude/rules/git-workflow.md
  agents/analyst.md                  -> ~/.claude/agents/analyst.md (Analyst)
  agents/architect.md                -> ~/.claude/agents/architect.md (Architect)
  agents/ux.md                       -> ~/.claude/agents/ux.md (UX designer)
  agents/devops.md                   -> ~/.claude/agents/devops.md (DevOps)
  agents/reviewer.md                 -> ~/.claude/agents/reviewer.md (Reviewer)
  agents/ux-reviewer.md              -> ~/.claude/agents/ux-reviewer.md (specialized UX review)
  agents/security-reviewer.md        -> ~/.claude/agents/security-reviewer.md (specialized security review)
  agents/privacy-reviewer.md         -> ~/.claude/agents/privacy-reviewer.md (specialized privacy review)
  agents/db-reviewer.md              -> ~/.claude/agents/db-reviewer.md (specialized database review)
  agents/performance-reviewer.md     -> ~/.claude/agents/performance-reviewer.md (specialized performance review)
  agents/dev.md                      -> ~/.claude/agents/dev.md (Dev)
  agents/qa.md                       -> ~/.claude/agents/qa.md (QA)
  skills/work-session/               -> ~/.claude/skills/work-session (with references/)
  skills/verify-changes/             -> ~/.claude/skills/verify-changes (with references/)
  hooks/agent-notify-hook.sh         -> ~/.claude/hooks/agent-notify-hook.sh (Stop/StopFailure/Notification adapter)
codex/
  notify/agent-notify-codex.sh       -> ~/.codex/notify/agent-notify-codex.sh (opt-in native `notify` adapter)
scripts/
  install.sh, uninstall.sh           link / unlink the twenty-one items above and merge/remove Claude hooks
  lib/common.sh                      managed item list and shared safety helpers
  lib/claude_hooks.py                safe structured merge of the notification hooks into ~/.claude/settings.json
  agent-notify.sh                    -> ~/.claude/hooks/agent-notify.sh (shared cross-agent notifier)
  validate.sh, validate.py           static repository validation
tests/                               isolated automated tests (Python unittest)
requirements-dev.txt                 pinned development dependency (PyYAML) for validation/tests
.github/workflows/validate.yml       CI: syntax, validator and tests
```

## Prerequisites

- Bash (3.2 or newer), `git`, standard POSIX utilities (`ln`, `mv`, `mkdir`, `readlink`, `mktemp`) and `python3` (standard library only). `scripts/install.sh`/`scripts/uninstall.sh` use `python3` to safely merge or remove this toolkit's notification hooks in `~/.claude/settings.json` (a user-owned, mutable file that cannot be symlinked like everything else); no pinned Python dependency is needed for this.
- For validation and tests only: Python 3.9+ and PyYAML 6.0.3, pinned in [`requirements-dev.txt`](requirements-dev.txt). Install it with `python3 -m pip install -r requirements-dev.txt`, inside a virtual environment if your system Python is externally managed.
- Optional, for [agent notifications](#agent-notifications): `notify-send` (`libnotify-bin` on Debian/Ubuntu) for desktop notifications and `paplay` (`pulseaudio-utils` on Debian/Ubuntu) or `pw-play` (`pipewire-bin`) for a sound cue. Neither is required: a missing or disabled backend falls back to a terminal bell (BEL).
- Claude Code and/or Codex installed separately; this toolkit does not install them and, apart from the opt-in Codex notify adapter below, does not configure them.

## Install on a new machine

```bash
git clone https://github.com/ifdevtecnologia/ifdev-ai-tools.git
cd ifdev-ai-tools
./scripts/install.sh --dry-run   # review the plan; changes nothing
./scripts/install.sh
./scripts/validate.sh            # optional: validate the checkout (needs the dev dependency)
```

The entry-point scripts are versioned with the executable bit (the validator enforces it). If your checkout lost it, `bash scripts/install.sh` works the same way.

The clone location is free: links point to wherever the repository lives, so keep the checkout in place after installing. Updating is a normal `git pull`; the links already point to the updated files. If you move the checkout, run `./scripts/install.sh` again from the new location (the old, now dangling links are backed up and replaced).

### What the installer does

- Uses only `$HOME`, without elevated privileges, and resolves the repository root from the script's own location.
- Creates symlinks for exactly the twenty-one destinations above and the missing parent directories. It never links or replaces `~/.claude`, `~/.codex`, `~/.claude/skills`, `~/.claude/agents` or `~/.codex/skills` as a whole, and never touches the base `~/.codex/config.toml`, so unknown siblings (your settings, other skills and agents) are preserved.
- Checks prerequisites, every source, every destination and every parent directory before changing anything. It refuses symlinked parent directories (for example `~/.claude/rules` pointing elsewhere) to avoid writing outside `$HOME` or into third-party trees.
- A destination that already links to this repository is left as is. A real file, a directory, a foreign symlink or a dangling symlink is **moved** (not deleted) to a unique backup directory before linking.
- Also merges this toolkit's three notification hooks (`Stop`, `StopFailure`, `Notification`) into `~/.claude/settings.json`, keeping every other key, hook and event semantically unchanged (the file is rewritten in full through a same-directory temporary file and an atomic rename, so its exact original bytes/whitespace are not preserved, only its meaning and permissions); a preflight (invalid JSON, a non-object top level, a malformed managed-hooks shape, an unsafe symlinked path or a symlinked `~/.claude`) refuses before any symlink is touched, and an existing file is backed up first into its own `~/.ifdev-ai-tools/backups/<timestamp>-hooks/` directory. See [Agent notifications](#agent-notifications).
- Removes links left by retired (renamed or removed) items, listed in `RETIRED_ITEMS` in `scripts/lib/common.sh`, only when the link still points exactly to this checkout's old source; a file, a directory or a link to anywhere else at that path is kept and reported. For example, the old `senior-reviewer.md` link in `~/.claude/agents/` is removed after its rename to `reviewer.md`, and so are the removed Codex supervision's `supervise-claude` skill directory link (in the Codex skills directory) and its `~/.codex/ifdev-autonomous.config.toml` profile link. A directory link is removed as a link only: nothing inside the old source directory is followed or deleted.
- Is idempotent: a second run reports every managed item as unchanged, creates no backup and reports the hooks as already configured.
- Uses a lock directory (`~/.ifdev-ai-tools/lock`) so concurrent runs cannot collide, held only while an actual change (a link or a hooks update) is being applied. If a run is interrupted hard, the message explains how to remove the stale lock after confirming no run is active.
- `--dry-run` prints the plan, including the hooks that would be added or removed, and makes no changes at all (no directories, backups or locks).

### Backups and manual restoration

Backups go to `~/.ifdev-ai-tools/backups/YYYYMMDD-HHMMSS-<unique>/`, preserving the relative path, for example `.../.claude/CLAUDE.md` or `.../.claude/skills/work-session/`. Each backup directory has a `MANIFEST` listing the moved paths. The toolkit never restores backups automatically; restoration is manual and deliberate, one `MANIFEST` entry at a time:

```bash
./scripts/uninstall.sh                          # remove this toolkit's links first
backup="$HOME/.ifdev-ai-tools/backups/<chosen-backup>"
cat "$backup/MANIFEST"                          # review what was moved
item=".claude/CLAUDE.md"                        # one entry from MANIFEST
# The destination must be completely absent: no file, directory or (even dangling) symlink.
if [ -e "$HOME/$item" ] || [ -L "$HOME/$item" ]; then
  echo "conflict: $HOME/$item exists; resolve it manually, nothing was moved" >&2
else
  mv -n -- "$backup/$item" "$HOME/$item"
fi
```

Repeat the check for every item; never move a backup over an existing destination. On a conflict, compare both versions and decide yourself what to keep (for example rename the current one first); the backup stays where it is until you do. Backups are never removed automatically; delete them yourself once you no longer need them.

## Uninstall

```bash
./scripts/uninstall.sh --dry-run
./scripts/uninstall.sh
```

Removes only symlinks that point exactly to this checkout's managed items (and to retired items' old sources), and only this toolkit's three hook entries from `~/.claude/settings.json` (backed up first, every other key/hook/event left untouched; the file itself is never deleted, only emptied of this toolkit's entries). Real files, directories, symlinks to other locations, sibling configuration, parent directories and backups are kept. It refuses to operate through symlinked parent directories (including `~/.ifdev-ai-tools` and its `backups/` directory, checked before anything else) and through an unsafe `~/.claude/settings.json` (invalid JSON, non-object top level or a symlink), and lists existing backups at the end.

## Agent notifications

[`scripts/agent-notify.sh`](scripts/agent-notify.sh) is a standalone, safe cross-agent notifier: `scripts/agent-notify.sh <codex|claude> <attention|complete|blocked> <message>`. It tries a desktop notification (`notify-send`) and a sound cue (`paplay`, or `pw-play` when `paplay` is missing) when available and enabled, and falls back to a terminal bell (BEL) **only when neither one actually succeeded** (both missing, disabled or failing). The message always reaches every backend as one literal argument; it is never evaluated or interpreted as a command.

- `--no-desktop`, `--no-sound`, `--no-bell` disable one channel for a single call; `--no-bell` also disables the fallback itself.
- `IFDEV_AGENT_NOTIFY=0` and `IFDEV_AGENT_SOUND=0` do the same as `--no-desktop`/`--no-sound` for every call in that environment (for example a headless CI shell); a flag always overrides its variable, never the other way around.
- Ubuntu/Debian: install the `libnotify-bin` and `pulseaudio-utils` packages for the desktop/sound backends through your usual OS package manager; neither is required, the BEL fallback always works in a terminal.

### Claude Code hooks

The installer registers three hooks in `~/.claude/settings.json` (see [What the installer does](#what-the-installer-does)), all routed through `~/.claude/hooks/agent-notify-hook.sh`:

| Claude Code hook | Notifier event | Behavior |
| --- | --- | --- |
| `Stop` (successful turn end) | `complete` | Always fires. |
| `StopFailure` | `blocked` | Always fires. |
| `Notification` | `attention` | Filtered by the payload's structural `notification_type` field only (Claude Code's documented field name; not `type`), **never by reading the free-text message**: a known non-attention system confirmation (currently `auth_success`) is silently skipped; a documented attention-required value (`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`) notifies, and so does anything else, including a payload with no recognizable `notification_type` at all (older or unknown Claude Code versions) — the documented conservative default, so a real request is never silently dropped. |

The installed `Notification` hook also carries a `matcher` narrowed to that same attention-required set, as defense-in-depth alongside the script-side filtering above (see `HOOK_MATCHERS` in [`scripts/lib/claude_hooks.py`](scripts/lib/claude_hooks.py)); the script-side filtering is what actually decides whether to notify and keeps working even against a Claude Code version that ignores the matcher.

The hook script itself keeps the regular, non-executable tracked file mode; the managed command Claude Code runs is `bash ~/.claude/hooks/agent-notify-hook.sh <kind>`, so it never needs its own execute bit.

### Codex

Codex's own built-in `notify` hook is opt-in only; this toolkit never edits the base `~/.codex/config.toml`. Add one line yourself to use [`codex/notify/agent-notify-codex.sh`](codex/notify/agent-notify-codex.sh):

```toml
notify = ["bash", "-c", '"$HOME/.codex/notify/agent-notify-codex.sh" "$1"', "_"]
```

Codex execs this array directly (no shell), so a literal `~` in a config-array argument is never expanded and would make Codex fail to even launch the adapter; a plain two-element `["bash", "~/.codex/notify/agent-notify-codex.sh"]` array does not work. The `bash -c` wrapper above is a real shell, so `$HOME` there does expand, and Codex's appended JSON payload reaches the adapter as `$1`, a single literal positional argument, never interpreted as a command. **Honest limitation:** Codex's `notify` hook only fires on turn/task completion, unlike Claude Code's separate Stop/StopFailure/Notification hooks. A Codex session using this adapter can therefore only ever report `complete`, never `attention` or `blocked`, even for a malformed or unrecognized turn-complete payload (it falls back to a generic "finished its turn" `complete` message, never to `blocked`).

### Troubleshooting

- No popup appears: confirm `notify-send` is installed and a desktop/D-Bus session is available (headless shells and most CI runners have neither); the terminal bell fallback still fires unless `--no-bell`/the terminal has bells disabled.
- No sound: confirm `paplay` or `pw-play` is installed and a PulseAudio/PipeWire sink is available; a missing sound theme file is not an error, it just skips silently to the next candidate or the bell fallback.
- Too many pings in an automated/CI shell: set `IFDEV_AGENT_NOTIFY=0` and `IFDEV_AGENT_SOUND=0` (or `--no-bell` for a single call) in that environment.
- None of this changes permissions, sandboxing or command authority for Claude or Codex; it only sends best-effort local notifications.

## Validation and tests

```bash
python3 -m pip install -r requirements-dev.txt   # once; pinned PyYAML
for script in scripts/*.sh scripts/lib/*.sh; do bash -n "$script"; done
./scripts/validate.sh
python3 -m unittest discover -s tests -v
```

`validate.sh` is a bounded static lint. It checks:

- the required file tree and the executable bit of `scripts/install.sh`, `scripts/uninstall.sh` and `scripts/validate.sh` (in the working tree and in the Git index);
- skill and agent frontmatter, parsed as real YAML with PyYAML's safe loader (no arbitrary object construction): it must be a mapping without duplicate keys, with non-empty string `name` (matching the directory/file) and `description`, and no duplicate names. Definitions are taken from the full file inventory, so a missing, unreadable or unsafe (for example symlinked) `SKILL.md` or agent file fails;
- broken local Markdown links and references to installed files (`~/.claude/rules/...` is mapped to `claude/rules/...`, `~/.codex/skills/<name>` to `codex/skills/<name>`);
- hardcoded machine-specific home paths and accidental product/domain terms;
- sensitive paths in the Git index (reported by path only, even when ignored or deleted from the working tree), credential-like patterns in safe files (reported without the value), symlinks escaping the repository and files below a symlinked parent directory (reported, never read);
- unsafe prescriptions (permission or hook bypasses, destructive commands, privileged execution, protected-branch bypasses) that appear without a prohibition in the same sentence;
- obsolete delivery instructions in the installed `claude/` and `codex/` content that contradict `claude/rules/git-workflow.md`: requiring approval for every commit, or forbidding pushes even when delivery is authorized. Narrower restrictions are not reported: conditional wording (such as "without authorization" or "unless"), explicit user restrictions and prohibitions limited to force pushes or default/protected branches;
- consistency of the commit emoji/type convention;
- `MANAGED_ITEMS` in `scripts/lib/common.sh`: every source exists, destinations are unique and none replaces `~/.codex/config.toml`; `RETIRED_ITEMS` sources have no tracked or untracked, non-ignored content left (Git-ignored leftovers such as `__pycache__/` do not count) and their destinations do not collide with a managed one;
- Bash syntax of every `*.sh` file.

The file inventory comes from `git ls-files --cached --others --exclude-standard`, so it works on a fresh clone and on an uncommitted working tree. Paths that look like secrets are classified by name before anything is opened, checking every path component (so a matching directory covers everything below it): `.env*`, `credential*`, `secret*` and `token*` (also dot-prefixed, such as `.credentials.json`), `auth` and `auth.*` (such as `auth.json`, `auth.yaml`), private keys and key stores (`id_rsa*`, `id_ed25519*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.ppk`, `*.jks`, `*.keystore`), credential files (`.netrc`, `.git-credentials`, `.pypirc`, `.npmrc`) and cloud/tool credential directories (`.aws`, `.azure`, `.gcloud`, `.kube`, `.docker`, `.oci`, `.ssh`, `.gnupg`, `.config/gcloud`, `.config/gh`). They are never read, hashed or copied, and their index content is never inspected; untracked ones are left untouched.

**Limitations:** the checks are heuristics. The unsafe-prescription and obsolete-delivery checks are sentence-based pattern matches, not semantic analysis: they can miss cleverly phrased instructions or accept a qualifier word used out of context. The credential scan covers common token formats only, and the sensitive-name list can flag harmless names. Passing validation is not a substitute for human semantic and security review.

The tests run the real scripts in subprocesses with a temporary `HOME` and temporary repositories; they never touch your actual home directory.

## Adding content

- **Rule:** add `claude/rules/<name>.md`, reference it from `claude/CLAUDE.md` when it should always apply, add it to `MANAGED_ITEMS` in `scripts/lib/common.sh` and to `REQUIRED_FILES` in `scripts/validate.py`, and extend the tests that enumerate managed links.
- **Skill:** add `claude/skills/<name>/SKILL.md` (or `codex/skills/<name>/SKILL.md`) with `name: <name>` and a `description` in the frontmatter; register the directory as above. Link the skill directory, never the whole `skills/` directory. Phase-specific procedures may go in `references/*.md` inside the skill directory (plain Markdown, no frontmatter), linked from `SKILL.md`; add mandatory ones to `REQUIRED_FILES`. They are installed with the directory link and checked like any other content.
- **Agent:** add `claude/agents/<name>.md` with `name` and `description`; register it as above.
- Keep content general: no product names, business domains, stack-specific business rules or machine paths. Those belong in each project's own `.claude/`.

## Team agents

Twelve global agents, all senior, form the team described in [`claude/CLAUDE.md`](claude/CLAUDE.md#team-analyst--dev--reviewer--qa):

| Role | Agent | Does | Never does |
|---|---|---|---|
| Analyst | [`analyst`](claude/agents/analyst.md) | Read-only requirements for new features, rule changes or ambiguous requests: scope, numbered testable acceptance criteria, blocking questions, recorded assumptions, risks and slicing | Implement, design detailed architecture, invent business rules, remote operations |
| Architect | [`architect`](claude/agents/architect.md) | Read-only technical decisions when a change needs one: drivers and constraints, options compared, recommendation, boundaries and contracts, data model, security boundaries, risks, implementation slicing and a draft decision record | Implement, write files, invent business rules or budgets, remote operations |
| UX designer | [`ux`](claude/agents/ux.md) | Flows, screen inventory with every state, interaction and accessibility notes, copy and, when asked, a low/mid-fidelity prototype for new or redesigned screens, writing only design files where the main agent says | Edit production code, tests or configuration, invent business rules or brand, publish, remote operations |
| Dev | [`dev`](claude/agents/dev.md) | Implements the planned change with tests and runs the affected checks | Commit, push, deploy, remote changes |
| DevOps | [`devops`](claude/agents/devops.md) | Holds the Dev role for CI/CD, build and container configuration, infrastructure as code, environments, observability (logs, metrics, traces, alerts, SLOs) and release automation; verifies only locally (lint, build, dry run or plan against local or mocked state) and lists the remote steps that need authorization | Deploy, apply, release or act on any remote environment, read credentials, commit, push |
| Reviewer | [`reviewer`](claude/agents/reviewer.md) | Independent read-only review of the diff | Edit or run application scripts |
| UX reviewer | [`ux-reviewer`](claude/agents/ux-reviewer.md) | Read-only review of interface changes: loading/empty/error states, action feedback, accessibility, i18n, consistency across clients, small screens, copy, and conformance to the confirmed design when the UX designer ran | Edit, run builds, remote operations |
| Security reviewer | [`security-reviewer`](claude/agents/security-reviewer.md) | Read-only review of auth, authorization and data isolation, secrets in clients, untrusted input, personal data and logs, dependencies, platform permissions, with concrete attack scenarios | Edit, run exploits, remote operations, read credentials |
| Privacy reviewer | [`privacy-reviewer`](claude/agents/privacy-reviewer.md) | Read-only review of personal and sensitive data: purpose and legal basis, consent, minimization, retention and deletion, data subject rights, sharing and transfers, analytics | Edit, give legal opinions, read real personal data, remote operations |
| Database reviewer | [`db-reviewer`](claude/agents/db-reviewer.md) | Read-only review of schema and migrations (data loss, locking, rollback), constraints, indexes and queries, transactions, functions and access policies | Edit, run migrations, connect to databases, remote operations |
| Performance reviewer | [`performance-reviewer`](claude/agents/performance-reviewer.md) | Read-only review of hot paths, round trips, payload and bundle size, client rendering and memory, caching, concurrency and cost, against realistic volumes | Edit, run benchmarks or builds, remote operations |
| QA | [`qa`](claude/agents/qa.md) | Derives scenarios, reruns the local verification end to end, reports gaps and a verdict | Edit, destructive or remote operations |

The Analyst runs before the Dev only when the request needs requirements; the main agent takes its blocking questions to the user, and its acceptance criteria guide the Dev and QA. The Architect runs after the Analyst only when the change needs a technical decision; its confirmed decision guides the Dev and the Reviewer. The UX designer runs after the Analyst only when the request creates or substantially changes screens or navigation; its confirmed design guides the Dev, the UX reviewer and QA. The DevOps agent takes the Dev role (or works with the Dev on separate slices) when the change is mainly infrastructure, delivery automation or observability. The UX, security, privacy, database and performance reviewers run alongside the Reviewer only when the diff meets the criteria in `claude/CLAUDE.md` (interface changes; authentication, data access, secrets, permissions, backup or transfer configuration, or dependencies; personal data flows; schema, migrations or access policies; hot paths, large data or performance targets). The main agent orchestrates the team, fixes material findings and delivers through the Git workflow. Project-specific reviewers (for example access-policy reviewers) complement the global ones and do not replace them. Projects can add specialized reviewers in their own `.claude/agents/`, but must not name them `analyst`, `architect`, `ux`, `dev`, `devops`, `reviewer`, `ux-reviewer`, `security-reviewer`, `privacy-reviewer`, `db-reviewer`, `performance-reviewer` or `qa`: a project agent takes precedence over a global one with the same name and would hide it.

Updating with `git pull` refreshes existing links, but a release that adds managed items (such as these agents) needs `./scripts/install.sh` again to create the new links; it is idempotent. Until then the global instructions tell the main agent to report the missing agent and run its role in a separate context.

Earlier versions also shipped an optional Codex supervisor (the `supervise-claude` skill and the opt-in `ifdev-autonomous` profile). It was removed because this team now covers complete deliveries on its own; running `./scripts/install.sh` again removes its old links (see the Upgrade notes in `CHANGELOG.md`).

## Context efficiency

Principle: **load the minimum context required to safely complete the current task.** Safety, testing and Git obligations are never traded for fewer tokens; what changes is when and how much content is read.

### What loads when

According to the official Claude Code documentation ([How Claude remembers your project](https://code.claude.com/docs/en/memory), including [path-specific rules](https://code.claude.com/docs/en/memory#path-specific-rules)):

- `~/.claude/CLAUDE.md` and the `CLAUDE.md` files in the working directory and its ancestor directories load in full at session start, and `@path` imports inside them are expanded eagerly.
- `CLAUDE.md` files in subdirectories below the working directory load on demand, when Claude reads files in those subdirectories.
- Rules in `.claude/rules/` and `~/.claude/rules/` without a `paths` frontmatter field load every session.
- Rules with a `paths` field (a YAML list of glob patterns) load when Claude reads matching files.
- An ordinary Markdown link is not an import; the linked file is read only when opened.

So `claude/CLAUDE.md` and the three global rules are **always loaded**; they stay concise and canonical, and installing them does not make them lazy. Skills keep purpose, boundaries, the core workflow and stop/report requirements in `SKILL.md` and move phase-specific procedures to `references/` files reached through ordinary links:

| Entrypoint | Read only when needed |
| --- | --- |
| `claude/skills/verify-changes/SKILL.md` | `references/commit-messages.md` (when commit messages are proposed) |
| `claude/skills/work-session/SKILL.md` | `references/commit-proposal.md` (when delivery is not authorized) |

A reference is binding once its phase starts. Reference directories ride on the existing skill directory links, so adding references does not change the managed paths.

Static entrypoint sizes in lines (not a token or runtime measurement): `claude/CLAUDE.md` 42 → 39, `verify-changes` 54 → 43, `work-session` 64 → 48. Fewer lines do not always mean fewer bytes: `claude/CLAUDE.md` grew from 3505 to 3645 bytes, so no byte or token saving is claimed for it.

### Scoped project rules

Path scoping belongs in each **project's** `.claude/rules/`, since global files cannot know a project's layout. Keep each area's rules in a separate file whose frontmatter lists its paths, for example `.claude/rules/database.md`:

```yaml
---
paths:
  - "supabase/**"
---
```

and likewise `paths: ["apps/web/**"]` in `web.md`, `paths: ["apps/mobile/**"]` in `mobile.md` and `paths: ["packages/contracts/**"]` in `contracts.md`. Do not `@`-import these files from the root `CLAUDE.md`: an import loads them every session and defeats the scoping.

### Tests, sessions and output

- [`claude/rules/testing.md`](claude/rules/testing.md) classifies impact (documentation, web, database, mobile, contracts and consumers, cross-stack) and gives each check an applicability status (APPLICABLE, NOT APPLICABLE, SKIPPED WITH JUSTIFICATION, BLOCKED) separate from its result. Clearly irrelevant checks do not reduce COMPLETE to PARTIAL; relevant uncertainty does. Mandatory checks and behavior coverage stay mandatory.
- Start a fresh session for each meaningful task; durable knowledge belongs in the repository (instructions, rules, docs), not in long conversation history.
- Allocate checks by phase as in `testing.md`: the implementer runs impacted checks each cycle, the independent reviewer runs targeted checks on intermediate reviews and the complete final gate once ready. Reuse a passed result only within the same run on unchanged inputs.
- Inspect Git metadata (`status`, `--stat`, `--name-status`, staged and unstaged) before reading selected diffs; read every relevant safe new file, and lockfiles, generated files and snapshots only as far as needed.
- Keep durable full logs on disk in a safe directory outside the repository, never containing secrets; the context gets command, working directory, exit status, a short summary and optionally the duration, with failure excerpts expanded as needed.
- Within one run, CLI help/versions, discovered instructions, the repository root, the base branch and project commands may be reused; invalidate them when their source changes. Never keep a persistent cache of permissions, credentials or safety decisions.

## Commits and delivery

Commits follow the emoji Conventional Commits convention and the branch/PR flow in [claude/rules/git-workflow.md](claude/rules/git-workflow.md). Contribution rules are in [CONTRIBUTING.md](CONTRIBUTING.md).

GitHub CLI authentication is a one-time human step outside the agent: run `gh auth login` in your own terminal and confirm with `gh auth status`. Agents use the existing authentication and never run the login themselves or read its token.

## Scope and ownership

The repository owns only the twenty-one linked items, including the opt-in Codex notify adapter, plus the three notification hook entries merged into `~/.claude/settings.json`. It does not version or manage the base Codex `config.toml`, the rest of `~/.claude/settings.json`, or other Claude/Codex permissions, authentication, caches, plugins, histories, built-in or third-party skills, or project trust configuration; those stay machine-local and untouched. Installing, uninstalling and every notification backend never change permissions, sandbox settings or command authority.
