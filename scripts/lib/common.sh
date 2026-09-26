# shellcheck shell=bash
# Shared helpers for install.sh and uninstall.sh. Sourced, never executed directly.
# Kept compatible with Bash 3.2 (macOS default): no mapfile, no empty-array expansion.

# Managed items as "<source relative to the repository>|<destination relative to $HOME>".
# Only these exact paths are ever linked or unlinked; their parent directories are never replaced.
MANAGED_ITEMS=(
  "claude/CLAUDE.md|.claude/CLAUDE.md"
  "claude/rules/engineering-principles.md|.claude/rules/engineering-principles.md"
  "claude/rules/testing.md|.claude/rules/testing.md"
  "claude/rules/git-workflow.md|.claude/rules/git-workflow.md"
  "claude/agents/senior-reviewer.md|.claude/agents/senior-reviewer.md"
  "claude/skills/work-session|.claude/skills/work-session"
  "claude/skills/verify-changes|.claude/skills/verify-changes"
  "codex/skills/supervise-claude|.codex/skills/supervise-claude"
  # Opt-in profile file only; the base ~/.codex/config.toml is never touched.
  "codex/profiles/ifdev-autonomous.config.toml|.codex/ifdev-autonomous.config.toml"
)

STATE_DIR_REL=".ifdev-ai-tools"
BACKUPS_DIR_REL="$STATE_DIR_REL/backups"
LOCK_DIR=""

log() { printf '%s\n' "$*"; }
error() { printf 'error: %s\n' "$*" >&2; }
die() {
  error "$*"
  exit 1
}

item_source() { printf '%s' "${1%%|*}"; }
item_destination() { printf '%s' "${1#*|}"; }

require_commands() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
  done
}

# HOME itself may be a symlink (some systems relocate /home); only paths below it are checked.
require_home() {
  [[ -n "${HOME:-}" ]] || die "HOME is not set"
  [[ "$HOME" == /* ]] || die "HOME must be an absolute path: $HOME"
  [[ "$HOME" != "/" ]] || die "refusing to use / as HOME"
  [[ -d "$HOME" ]] || die "HOME is not a directory: $HOME"
}

# Prints a problem when a directory between $HOME and <relative dir> is a symlink or not a
# directory. Following such a parent could write into, or unlink from, a tree outside HOME.
parent_problem() {
  local rel_dir=$1 path=$HOME part
  local -a parts
  [[ "$rel_dir" == "." || -z "$rel_dir" ]] && return 0
  IFS=/ read -r -a parts <<<"$rel_dir"
  for part in "${parts[@]}"; do
    path="$path/$part"
    if [[ -L "$path" ]]; then
      printf 'refusing symlinked parent directory: %s\n' "$path"
      return 0
    fi
    if [[ -e "$path" && ! -d "$path" ]]; then
      printf 'parent path is not a directory: %s\n' "$path"
      return 0
    fi
  done
}

# True when <destination> is a symlink whose literal target is exactly <target>.
is_managed_link() {
  local destination=$1 target=$2
  [[ -L "$destination" && "$(readlink -- "$destination")" == "$target" ]]
}

release_lock() {
  if [[ -n "$LOCK_DIR" ]]; then
    rmdir -- "$LOCK_DIR" 2>/dev/null || true
    LOCK_DIR=""
  fi
  return 0
}

# mkdir is atomic and portable (flock is absent on macOS). A crashed run leaves the lock
# behind; the message explains the manual, verified recovery instead of guessing staleness.
acquire_lock() {
  local state_dir="$HOME/$STATE_DIR_REL" problem
  problem=$(parent_problem "$STATE_DIR_REL")
  [[ -z "$problem" ]] || die "$problem"
  mkdir -p -- "$state_dir"
  if ! mkdir -- "$state_dir/lock" 2>/dev/null; then
    die "another install/uninstall run holds $state_dir/lock; if none is active, remove that empty directory and retry"
  fi
  LOCK_DIR="$state_dir/lock"
  trap release_lock EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}
