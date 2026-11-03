# shellcheck shell=bash
# Shared helpers for install.sh and uninstall.sh. Sourced, never executed directly.
# Kept compatible with Bash 3.2 (macOS default): no mapfile, no empty-array expansion.

# Managed items as "<source relative to the repository>|<destination relative to $HOME>|<targets>",
# where <targets> is the comma-separated list of install targets that own the item. An item with
# several owners is shared: uninstalling one of them keeps it while another owner stays recorded.
# Only these exact paths are ever linked or unlinked; their parent directories are never replaced.
MANAGED_ITEMS=(
  "dist/claude/CLAUDE.md|.claude/CLAUDE.md|claude"
  "core/rules/engineering-principles.md|.claude/rules/engineering-principles.md|claude"
  "core/rules/testing.md|.claude/rules/testing.md|claude"
  "core/rules/git-workflow.md|.claude/rules/git-workflow.md|claude"
  "dist/claude/agents/po.md|.claude/agents/po.md|claude"
  "dist/claude/agents/analyst.md|.claude/agents/analyst.md|claude"
  "dist/claude/agents/architect.md|.claude/agents/architect.md|claude"
  "dist/claude/agents/ux.md|.claude/agents/ux.md|claude"
  "dist/claude/agents/designer.md|.claude/agents/designer.md|claude"
  "dist/claude/agents/devops.md|.claude/agents/devops.md|claude"
  "dist/claude/agents/tech-writer.md|.claude/agents/tech-writer.md|claude"
  "dist/claude/agents/debugger.md|.claude/agents/debugger.md|claude"
  "dist/claude/agents/metrics-analyst.md|.claude/agents/metrics-analyst.md|claude"
  "dist/claude/agents/reviewer.md|.claude/agents/reviewer.md|claude"
  "dist/claude/agents/ux-reviewer.md|.claude/agents/ux-reviewer.md|claude"
  "dist/claude/agents/security-reviewer.md|.claude/agents/security-reviewer.md|claude"
  "dist/claude/agents/privacy-reviewer.md|.claude/agents/privacy-reviewer.md|claude"
  "dist/claude/agents/db-reviewer.md|.claude/agents/db-reviewer.md|claude"
  "dist/claude/agents/performance-reviewer.md|.claude/agents/performance-reviewer.md|claude"
  "dist/claude/agents/api-reviewer.md|.claude/agents/api-reviewer.md|claude"
  "dist/claude/agents/dev.md|.claude/agents/dev.md|claude"
  "dist/claude/agents/qa.md|.claude/agents/qa.md|claude"
  "core/skills/work-session|.claude/skills/work-session|claude"
  "core/skills/verify-changes|.claude/skills/verify-changes|claude"
  "core/skills/flutter-dev|.claude/skills/flutter-dev|claude"
  "core/skills/web-dev|.claude/skills/web-dev|claude"
  "core/skills/supabase-dev|.claude/skills/supabase-dev|claude"
  "core/skills/safe-refactor|.claude/skills/safe-refactor|claude"
  "core/skills/java-spring-dev|.claude/skills/java-spring-dev|claude"
  "core/skills/e2e-testing|.claude/skills/e2e-testing|claude"
  # Shared cross-agent notifier plus its Claude hook and opt-in Codex `notify` adapters. The Codex
  # adapter is shared: the claude target has installed it since before targets existed, and the
  # codex target installs it next to its own copy of the notifier, which the adapter prefers.
  "scripts/agent-notify.sh|.claude/hooks/agent-notify.sh|claude"
  "targets/claude/hooks/agent-notify-hook.sh|.claude/hooks/agent-notify-hook.sh|claude"
  "targets/codex/notify/agent-notify-codex.sh|.codex/notify/agent-notify-codex.sh|claude,codex"
  "scripts/agent-notify.sh|.codex/notify/agent-notify.sh|codex"
  # Codex target: the global instructions (rules included), one custom agent file per role and
  # the skills in the shared Agent Skills directory.
  "dist/codex/AGENTS.md|.codex/AGENTS.md|codex"
  "dist/codex/agents/po.toml|.codex/agents/po.toml|codex"
  "dist/codex/agents/analyst.toml|.codex/agents/analyst.toml|codex"
  "dist/codex/agents/architect.toml|.codex/agents/architect.toml|codex"
  "dist/codex/agents/ux.toml|.codex/agents/ux.toml|codex"
  "dist/codex/agents/designer.toml|.codex/agents/designer.toml|codex"
  "dist/codex/agents/devops.toml|.codex/agents/devops.toml|codex"
  "dist/codex/agents/tech-writer.toml|.codex/agents/tech-writer.toml|codex"
  "dist/codex/agents/debugger.toml|.codex/agents/debugger.toml|codex"
  "dist/codex/agents/metrics-analyst.toml|.codex/agents/metrics-analyst.toml|codex"
  "dist/codex/agents/reviewer.toml|.codex/agents/reviewer.toml|codex"
  "dist/codex/agents/ux-reviewer.toml|.codex/agents/ux-reviewer.toml|codex"
  "dist/codex/agents/security-reviewer.toml|.codex/agents/security-reviewer.toml|codex"
  "dist/codex/agents/privacy-reviewer.toml|.codex/agents/privacy-reviewer.toml|codex"
  "dist/codex/agents/db-reviewer.toml|.codex/agents/db-reviewer.toml|codex"
  "dist/codex/agents/performance-reviewer.toml|.codex/agents/performance-reviewer.toml|codex"
  "dist/codex/agents/api-reviewer.toml|.codex/agents/api-reviewer.toml|codex"
  "dist/codex/agents/dev.toml|.codex/agents/dev.toml|codex"
  "dist/codex/agents/qa.toml|.codex/agents/qa.toml|codex"
  "core/skills/work-session|.agents/skills/work-session|codex"
  "core/skills/verify-changes|.agents/skills/verify-changes|codex"
  "core/skills/flutter-dev|.agents/skills/flutter-dev|codex"
  "core/skills/web-dev|.agents/skills/web-dev|codex"
  "core/skills/supabase-dev|.agents/skills/supabase-dev|codex"
  "core/skills/safe-refactor|.agents/skills/safe-refactor|codex"
  "core/skills/java-spring-dev|.agents/skills/java-spring-dev|codex"
  "core/skills/e2e-testing|.agents/skills/e2e-testing|codex"
)

# Sources that moved to a new path (decision record 0004), as "<old source>|<destination>". A
# destination that is still an exact link to this checkout's old source belongs to the toolkit:
# install.sh relinks it to the new source without a backup and uninstall.sh removes it. The old
# paths stay resolvable for one release through the tracked compatibility links in claude/ and codex/.
MOVED_ITEMS=(
  "claude/CLAUDE.md|.claude/CLAUDE.md"
  "claude/rules/engineering-principles.md|.claude/rules/engineering-principles.md"
  "claude/rules/testing.md|.claude/rules/testing.md"
  "claude/rules/git-workflow.md|.claude/rules/git-workflow.md"
  "claude/agents/po.md|.claude/agents/po.md"
  "claude/agents/analyst.md|.claude/agents/analyst.md"
  "claude/agents/architect.md|.claude/agents/architect.md"
  "claude/agents/ux.md|.claude/agents/ux.md"
  "claude/agents/designer.md|.claude/agents/designer.md"
  "claude/agents/devops.md|.claude/agents/devops.md"
  "claude/agents/tech-writer.md|.claude/agents/tech-writer.md"
  "claude/agents/debugger.md|.claude/agents/debugger.md"
  "claude/agents/metrics-analyst.md|.claude/agents/metrics-analyst.md"
  "claude/agents/reviewer.md|.claude/agents/reviewer.md"
  "claude/agents/ux-reviewer.md|.claude/agents/ux-reviewer.md"
  "claude/agents/security-reviewer.md|.claude/agents/security-reviewer.md"
  "claude/agents/privacy-reviewer.md|.claude/agents/privacy-reviewer.md"
  "claude/agents/db-reviewer.md|.claude/agents/db-reviewer.md"
  "claude/agents/performance-reviewer.md|.claude/agents/performance-reviewer.md"
  "claude/agents/api-reviewer.md|.claude/agents/api-reviewer.md"
  "claude/agents/dev.md|.claude/agents/dev.md"
  "claude/agents/qa.md|.claude/agents/qa.md"
  "claude/skills/work-session|.claude/skills/work-session"
  "claude/skills/verify-changes|.claude/skills/verify-changes"
  "claude/skills/flutter-dev|.claude/skills/flutter-dev"
  "claude/skills/web-dev|.claude/skills/web-dev"
  "claude/skills/supabase-dev|.claude/skills/supabase-dev"
  "claude/skills/safe-refactor|.claude/skills/safe-refactor"
  "claude/skills/java-spring-dev|.claude/skills/java-spring-dev"
  "claude/skills/e2e-testing|.claude/skills/e2e-testing"
  "claude/hooks/agent-notify-hook.sh|.claude/hooks/agent-notify-hook.sh"
  "codex/notify/agent-notify-codex.sh|.codex/notify/agent-notify-codex.sh"
)

# Formerly managed items, as "<old source>|<destination>". Their sources no longer exist; install.sh and
# uninstall.sh remove a destination only when it is still an exact link to this checkout's
# old source, and leave anything else at that path untouched.
RETIRED_ITEMS=(
  "claude/agents/senior-reviewer.md|.claude/agents/senior-reviewer.md"
  # Codex supervision, removed together with its opt-in profile. A directory link is removed
  # as a link only; whatever the old source directory still holds is never followed.
  "codex/skills/supervise-claude|.codex/skills/supervise-claude"
  "codex/profiles/ifdev-autonomous.config.toml|.codex/ifdev-autonomous.config.toml"
)
RETIRED_PLAN=()
RETIRED_REMOVED=0

# Install targets that --target accepts, in their canonical order. cursor is deferred (decision
# record 0004) and refused until its items exist.
AVAILABLE_TARGETS="claude codex"
REQUESTED_TARGETS=""
RECORDED_TARGETS=""
SELECTED_TARGETS=""

STATE_DIR_REL=".ifdev-ai-tools"
BACKUPS_DIR_REL="$STATE_DIR_REL/backups"
# The installed targets, one name per line. Written only under the lock.
TARGETS_FILE_REL="$STATE_DIR_REL/targets"
LOCK_DIR=""

log() { printf '%s\n' "$*"; }
error() { printf 'error: %s\n' "$*" >&2; }
die() {
  error "$*"
  exit 1
}

item_source() { printf '%s' "${1%%|*}"; }
item_destination() {
  local rest=${1#*|}
  printf '%s' "${rest%%|*}"
}
# Only for MANAGED_ITEMS, the one table with a third field.
item_targets() { printf '%s' "${1##*|}"; }

# True when the space-separated <list> contains <name>.
list_contains() {
  local name
  for name in $1; do
    [[ "$name" == "$2" ]] && return 0
  done
  return 1
}

# True when the managed <item> is owned by a target in the space-separated <targets>.
item_in_targets() {
  local name owners
  owners=$(item_targets "$1")
  for name in $2; do
    list_contains "${owners//,/ }" "$name" && return 0
  done
  return 1
}

# Prints the names of the space-separated <list> in canonical order, without duplicates.
canonical_targets() {
  local name result=""
  for name in $AVAILABLE_TARGETS; do
    list_contains "$1" "$name" && result="${result:+$result }$name"
  done
  printf '%s' "$result"
}

# Validates a --target value (exit 2 on an unsupported one) and adds it to REQUESTED_TARGETS.
add_requested_target() {
  local name=$1
  if [[ "$name" == "all" ]]; then
    REQUESTED_TARGETS=$AVAILABLE_TARGETS
    return 0
  fi
  if ! list_contains "$AVAILABLE_TARGETS" "$name"; then
    error "target '$name' is not supported yet; available targets: $AVAILABLE_TARGETS"
    exit 2
  fi
  REQUESTED_TARGETS=$(canonical_targets "$REQUESTED_TARGETS $name")
}

# Prints the old source of a moved <destination>, or nothing when it did not move.
moved_source() {
  local item
  [[ ${#MOVED_ITEMS[@]} -gt 0 ]] || return 0
  for item in "${MOVED_ITEMS[@]}"; do
    if [[ "$(item_destination "$item")" == "$1" ]]; then
      item_source "$item"
      return 0
    fi
  done
}

# True when <destination> is an exact link to the moved item's old source in this checkout.
is_moved_link() {
  local old
  old=$(moved_source "$1")
  [[ -n "$old" ]] && is_managed_link "$HOME/$1" "$REPO_ROOT/$old"
}

# Reads ~/.ifdev-ai-tools/targets into RECORDED_TARGETS (empty when the file is absent).
# Refuses an unsafe state directory or file and any name this checkout does not support.
read_recorded_targets() {
  local file="$HOME/$TARGETS_FILE_REL" problem name
  RECORDED_TARGETS=""
  problem=$(parent_problem "$STATE_DIR_REL")
  [[ -z "$problem" ]] || die "$problem"
  [[ -e "$file" || -L "$file" ]] || return 0
  [[ -f "$file" && ! -L "$file" ]] || die "targets state is not a regular file: $file"
  while IFS= read -r name || [[ -n "$name" ]]; do
    # Tolerates hand edits: surrounding whitespace, CRLF line endings and blank lines.
    name=${name#"${name%%[![:space:]]*}"}
    name=${name%"${name##*[![:space:]]}"}
    [[ -n "$name" ]] || continue
    list_contains "$AVAILABLE_TARGETS" "$name" || die "unsupported target '$name' recorded in $file"
    RECORDED_TARGETS=$(canonical_targets "$RECORDED_TARGETS $name")
  done <"$file"
}

# True when any destination owned only by claude is an exact link to this checkout, at its current
# or its old (moved) source: a machine installed before targets were recorded, which only had the
# claude items. Shared and codex items are left out, so they never make a machine look like claude.
has_legacy_install() {
  local item destination_rel
  for item in "${MANAGED_ITEMS[@]}"; do
    [[ "$(item_targets "$item")" == "claude" ]] || continue
    destination_rel=$(item_destination "$item")
    if is_managed_link "$HOME/$destination_rel" "$REPO_ROOT/$(item_source "$item")" || is_moved_link "$destination_rel"; then
      return 0
    fi
  done
  return 1
}

# Writes the space-separated <targets> atomically (temporary file, then rename); an empty list
# removes the file. Call only while holding the lock, after read_recorded_targets.
write_recorded_targets() {
  local file="$HOME/$TARGETS_FILE_REL" tmp name
  if [[ -z "$1" ]]; then
    rm -f -- "$file"
    return 0
  fi
  tmp=$(mktemp "$HOME/$STATE_DIR_REL/targets.XXXXXX")
  for name in $1; do
    printf '%s\n' "$name"
  done >"$tmp"
  mv -f -- "$tmp" "$file"
}

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

# Prints a problem when a directory between <base> (default $HOME) and <relative dir> is a
# symlink or not a directory. Following such a parent could write into, or unlink from, a tree
# outside <base>.
parent_problem() {
  local rel_dir=$1 path=${2:-$HOME} part
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

# Classifies every retired item into RETIRED_PLAN (remove, foreign, absent) without touching
# the filesystem. Returns non-zero after reporting every unsafe parent. Requires REPO_ROOT.
plan_retired() {
  local item destination_rel destination problem failed=0
  RETIRED_PLAN=()
  # No empty-array expansion (Bash 3.2 with set -u): the list may become empty later.
  [[ ${#RETIRED_ITEMS[@]} -gt 0 ]] || return 0
  for item in "${RETIRED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    problem=$(parent_problem "$(dirname -- "$destination_rel")")
    if [[ -n "$problem" ]]; then
      error "$problem"
      failed=1
      RETIRED_PLAN+=("error")
    elif is_managed_link "$destination" "$REPO_ROOT/$(item_source "$item")"; then
      RETIRED_PLAN+=("remove")
    elif [[ -e "$destination" || -L "$destination" ]]; then
      RETIRED_PLAN+=("foreign")
    else
      RETIRED_PLAN+=("absent")
    fi
  done
  return "$failed"
}

retired_has_removals() {
  local action
  [[ ${#RETIRED_PLAN[@]} -gt 0 ]] || return 1
  for action in "${RETIRED_PLAN[@]}"; do
    [[ "$action" == "remove" ]] && return 0
  done
  return 1
}

# Applies RETIRED_PLAN (or only reports it when <dry run> is 1), counting removals in
# RETIRED_REMOVED. Absent paths are silent so an up-to-date machine sees no extra output.
apply_retired() {
  local dry_run=$1 item destination_rel destination i=0
  RETIRED_REMOVED=0
  [[ ${#RETIRED_ITEMS[@]} -gt 0 ]] || return 0
  for item in "${RETIRED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    case "${RETIRED_PLAN[$i]}" in
      remove)
        if [[ "$dry_run" -eq 1 ]]; then
          log "would remove retired link: ~/$destination_rel"
        else
          is_managed_link "$destination" "$REPO_ROOT/$(item_source "$item")" ||
            die "~/$destination_rel changed while removing retired links; stopping"
          rm -- "$destination"
          RETIRED_REMOVED=$((RETIRED_REMOVED + 1))
          log "removed retired link: ~/$destination_rel"
        fi
        ;;
      foreign) log "kept retired path (not a link to this repository): ~/$destination_rel" ;;
    esac
    i=$((i + 1))
  done
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
