# Installation, upgrade and uninstall

For anyone installing the agent team on a machine. The short version is in the [README quick start](../README.md#-quick-start).

## Prerequisites

- Bash (3.2 or newer), `git`, standard POSIX utilities (`ln`, `mv`, `cp`, `chmod`, `mkdir`, `readlink`, `mktemp`; and, for the `codex` target, `shasum` or `sha256sum` to checksum the agent copies) and `python3` (standard library only). `scripts/install.sh` and `scripts/uninstall.sh` use `python3` to safely merge or remove this toolkit's notification hooks in `~/.claude/settings.json` (a user-owned, mutable file that cannot be linked or copied like the managed items); no pinned Python dependency is needed for this.
- For validation, lint and tests only: Python 3.9+ and the pinned development tools, see [development.md](development.md#prerequisites).
- Optional, for [notifications](notifications.md): `notify-send` (`libnotify-bin` on Debian/Ubuntu) for desktop notifications and `paplay` (`pulseaudio-utils` on Debian/Ubuntu) or `pw-play` (`pipewire-bin`) for a sound cue. Neither is required: a missing or disabled backend falls back to a terminal bell (BEL).
- To let agents open pull requests: the GitHub CLI, authenticated once by you in your own terminal (`gh auth login`, then `gh auth status`); agents never run the login or read its token.
- Claude Code and/or Codex, installed separately. This toolkit does not install them and, apart from the opt-in Codex notify adapter, does not configure them.

## Install

```bash
git clone https://github.com/ifdevtecnologia/ifdev-agent-team.git
cd ifdev-agent-team
./scripts/install.sh --target claude --dry-run   # review the plan; changes nothing
./scripts/install.sh --target claude             # or --target codex, or --target all for both
./scripts/validate.sh                            # optional: validate the checkout (needs the dev dependency)
```

The entry-point scripts are versioned with the executable bit (the validator enforces it). If your checkout lost it, `bash scripts/install.sh` works the same way.

The clone location is free: links point to wherever the repository lives, so keep the checkout in place after installing. The one exception is the Codex custom agents (`~/.codex/agents/<role>.toml`): they are installed as regular-file copies, because Codex does not load a symlinked agent file (it lists the agent, but starting it fails with "agent type is currently not available").

## Targets

`--target` chooses what to install: `claude`, `codex` or `all` (both); repeat it to choose several. The installed targets are recorded in `~/.ifdev-ai-tools/targets`, so a later bare `./scripts/install.sh` reinstalls them. A machine installed before targets were recorded (any link to this checkout) is treated as `claude`; on a machine with nothing installed, a bare `./scripts/install.sh` refuses and changes nothing until you choose a target.

| Target | Roles run as | Read-only roles | Notifications | Status |
| --- | --- | --- | --- | --- |
| `claude` (Claude Code) | subagents of the same name | enforced (edit tools blocked) | `Stop`, `StopFailure` and `Notification` hooks | supported |
| `codex` (OpenAI Codex CLI) | custom agents started with `spawn_agent` and `fork_turns: "none"` | by instruction only | opt-in `notify` adapter (turn completion only) | supported; custom agent registration still needs a manual check in a Codex session |

The exact items of each target are listed in [development.md](development.md#layout): thirty-three links for `claude`, and for `codex` eleven links plus the eighteen agent copies (the Codex notify adapter is shared by both).

## What the installer guarantees

- Uses only `$HOME`, without elevated privileges, and resolves the repository root from the script's own location.
- Creates symlinks (copies for the Codex custom agents) for exactly the destinations of the chosen targets and the missing parent directories. It never links or replaces `~/.claude`, `~/.codex`, `~/.claude/skills`, `~/.claude/agents`, `~/.codex/agents`, `~/.codex/skills`, `~/.agents` or `~/.agents/skills` as a whole, and never touches the base `~/.codex/config.toml`, so unknown siblings (your settings, other skills and agents) are preserved.
- Checks prerequisites, every source, every destination and every parent directory before changing anything. It refuses symlinked parent directories (for example `~/.claude/rules` pointing elsewhere) to avoid writing outside `$HOME` or into third-party trees, and a managed source below a symlinked directory of the checkout, so every link loads content from the checkout itself.
- A destination that already links to this repository is left as is. A link to an item's old path in this checkout (listed in `MOVED_ITEMS` in `scripts/lib/common.sh`) is relinked to the new path without a backup. A real file, a directory, a foreign symlink or a dangling symlink is **moved** (not deleted) to a unique backup directory before linking.
- For `claude`: merges this toolkit's three notification hooks (`Stop`, `StopFailure`, `Notification`) into `~/.claude/settings.json`, keeping every other key, hook and event semantically unchanged. The file is rewritten in full through a same-directory temporary file and an atomic rename, so its exact original bytes and whitespace are not preserved, only its meaning and permissions. A preflight (invalid JSON, a non-object top level, a malformed managed-hooks shape, an unsafe symlinked path or a symlinked `~/.claude`) refuses before any symlink is touched, and an existing file is backed up first into its own `~/.ifdev-ai-tools/backups/<timestamp>-hooks/` directory. See [notifications.md](notifications.md#claude-code-hooks).
- For `codex`: each custom agent is written as a regular file through a temporary file in `~/.codex/agents/` and an atomic rename, and its SHA-256 checksum is recorded in `~/.ifdev-ai-tools/copies`. A copy that still matches its record is the toolkit's: a rerun updates it in place without a backup, and a link to this checkout's agent file (left by earlier versions) is replaced with a copy the same way. A file equal to the current agent is kept as unchanged. Anything else at that path, including a copy you edited, a file you wrote with the same generated header or a link elsewhere, is backed up first. An existing `~/.codex/AGENTS.md` is backed up and replaced like any other destination, and an existing `~/.codex/AGENTS.override.md` (which Codex reads instead) is left untouched with a warning. The custom agents are read-only by instruction only: Codex does not enforce read-only roles.
- Removes links left by retired (renamed or removed) items, listed in `RETIRED_ITEMS` in `scripts/lib/common.sh`, only when the link still points exactly to this checkout's old source; a file, a directory or a link to anywhere else at that path is kept and reported. For example, the old `senior-reviewer.md` link in `~/.claude/agents/` is removed after its rename to `reviewer.md`, and so are the removed Codex supervision's `supervise-claude` skill directory link (in the Codex skills directory) and its `~/.codex/ifdev-autonomous.config.toml` profile link. A directory link is removed as a link only: nothing inside the old source directory is followed or deleted.
- Is idempotent: a second run reports every managed item as unchanged, creates no backup and reports the hooks as already configured. The summary counts links (`linked`), Codex agent copies written or updated (`copied`), backups and unchanged items separately.
- Uses a lock directory (`~/.ifdev-ai-tools/lock`) so concurrent runs cannot collide, held only while an actual change (a link or a hooks update) is being applied. If a run is interrupted hard, the message explains how to remove the stale lock after confirming no run is active.
- `--dry-run` prints the plan, including the hooks that would be added or removed, and makes no changes at all (no directories, backups or locks).

## Backups and manual restoration

The state directory keeps the project's former name, `~/.ifdev-ai-tools`, so machines installed before the rename keep their lock and backups. Backups go to `~/.ifdev-ai-tools/backups/YYYYMMDD-HHMMSS-<unique>/`, preserving the relative path, for example `.../.claude/CLAUDE.md` or `.../.claude/skills/work-session/`. Each backup directory has a `MANIFEST` listing the moved paths. The toolkit never restores backups automatically; restoration is manual and deliberate, one `MANIFEST` entry at a time:

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

## Upgrade

- **Update:** `git pull`, then `./scripts/install.sh`. The links already point to the updated files, but the Codex custom agents are copies: they change only when the installer runs again, which updates every unmodified copy and backs up a copy you edited.
- **Rerun the installer when a release adds, moves or retires managed items:** `./scripts/install.sh` (idempotent). A bare run reinstalls the recorded targets. Until you rerun it, the global instructions tell the main agent to report a missing agent and run its role in a separate context. The **Upgrade** notes in [CHANGELOG.md](../CHANGELOG.md) say when a release needs it.
- **Moved sources:** the old paths `claude/CLAUDE.md`, `claude/rules`, `claude/skills`, `claude/agents`, `claude/hooks` and `codex/notify` are compatibility links to the new locations in `dist/`, `core/` and `targets/`, kept for one release so a plain `git pull` keeps old installs working. Rerunning `./scripts/install.sh` moves your links to the new sources without backups and records the `claude` target.
- **Codex agents installed as links by an earlier version:** Codex lists them but cannot start them. Rerun `./scripts/install.sh`; it replaces each link to this checkout with a copy, without a backup.
- **Adding Codex to an existing install:** `./scripts/install.sh --target codex` once; review `--dry-run` first if you keep your own `~/.codex/AGENTS.md`, because it is moved to the backups, not merged.
- **Moved checkout:** run `./scripts/install.sh --target claude` (or your targets) again from the new location; the old, now dangling links are backed up and replaced. A bare run may refuse there, because nothing links to the new location yet and a checkout installed before targets were recorded has no recorded target.
- **Removed Codex supervision:** earlier versions shipped an optional Codex supervisor (the `supervise-claude` skill and the opt-in `ifdev-autonomous` profile). It was removed because the team now covers complete deliveries on its own; rerunning `./scripts/install.sh` removes its old links (see the Upgrade notes in the changelog).

## Uninstall

```bash
./scripts/uninstall.sh --dry-run
./scripts/uninstall.sh
```

- Without `--target`, removes the recorded targets (every target when none is recorded) and updates `~/.ifdev-ai-tools/targets`; `--target claude` or `--target codex` removes one.
- The shared Codex notify adapter is kept while the other target stays recorded, and the settings hooks are removed only with `claude`.
- Removes only symlinks that point exactly to this checkout's managed items (and to moved and retired items' old sources), the Codex agent copies that still match their recorded checksum (a copy you edited is kept and reported) together with their records in `~/.ifdev-ai-tools/copies`, and only this toolkit's three hook entries from `~/.claude/settings.json` (backed up first, every other key, hook and event left untouched; the file itself is never deleted, only emptied of this toolkit's entries).
- Keeps real files, directories, symlinks to other locations, sibling configuration, parent directories and backups.
- Refuses to operate through symlinked parent directories (including `~/.ifdev-ai-tools` and its `backups/` directory, checked before anything else) and through an unsafe `~/.claude/settings.json` (invalid JSON, non-object top level or a symlink), and lists existing backups at the end.

## What the toolkit owns

The repository owns only the linked items of the installed targets (thirty-three for `claude`, including the opt-in Codex notify adapter, and eleven for `codex`), the Codex agent copies that still match their recorded checksum (eighteen), plus the three notification hook entries merged into `~/.claude/settings.json`. It does not version or manage the base Codex `config.toml`, the rest of `~/.claude/settings.json`, or other Claude or Codex permissions, authentication, caches, plugins, histories, built-in or third-party skills, or project trust configuration; those stay machine-local and untouched. Installing, uninstalling and every notification backend never change permissions, sandbox settings or command authority.

## Troubleshooting

- **A bare `./scripts/install.sh` refuses on a new machine:** choose a target with `--target claude`, `--target codex` or `--target all`.
- **The installer refuses a symlinked parent directory:** a directory such as `~/.claude/rules` points elsewhere. The installer will not write through it; replace it with a real directory yourself, then rerun.
- **The installer refuses `~/.claude/settings.json`:** fix the JSON (or replace the symlink with a real file); nothing was changed.
- **A stale lock after a hard interruption:** confirm no run is active, then remove `~/.ifdev-ai-tools/lock` as the error message explains.
- **Codex lists a team agent but reports "agent type is currently not available":** the agent file is a symlink from an earlier version; rerun `./scripts/install.sh` to replace it with a copy.
- **Codex ignores the global instructions:** an existing `~/.codex/AGENTS.override.md` takes precedence; the installer warns about it and leaves it untouched.
- **Links are dangling after moving the checkout:** see [Moved checkout](#upgrade).
- **Notifications:** see [notifications.md](notifications.md#troubleshooting).
