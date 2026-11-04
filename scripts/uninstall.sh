#!/usr/bin/env bash
# Removes only the symlinks in $HOME that point to this repository's managed items, and the copies
# the installer made that are still unmodified.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=0
PLAN=()
FORGET_COPIES=0

usage() {
  cat <<'EOF'
Usage: scripts/uninstall.sh [--target claude|codex|all]... [--dry-run]

Removes the managed symlinks of the chosen targets that point to this repository
(also at an item's old path in this checkout), including links left by retired items,
and updates ~/.ifdev-ai-tools/targets. A Codex custom agent copy is removed only while it
still matches the checksum recorded in ~/.ifdev-ai-tools/copies; a modified copy is kept. An item shared with a target that stays
recorded (the Codex notify adapter) is kept. Without --target, removes the recorded
targets, or every target when none is recorded. Real files, directories, foreign symlinks,
sibling configuration and backups are left untouched. The claude target also removes
this toolkit's notification hook entries from ~/.claude/settings.json, leaving every
other setting untouched (requires python3; see docs/installation.md). Backups are not restored
automatically; see docs/installation.md for manual restoration.

  --target    target to remove (repeatable); available: claude, codex, or all
  --dry-run   print the planned actions without changing anything
  -h, --help  show this help
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --target)
        [[ $# -ge 2 ]] || {
          error "--target needs a value"
          usage >&2
          exit 2
        }
        add_requested_target "$2"
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        error "unknown argument: $1"
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

# Sets SELECTED_TARGETS from --target, else the targets in the state file, else every target (a
# machine installed before targets were recorded, or with nothing installed).
select_targets() {
  read_recorded_targets
  if [[ -n "$REQUESTED_TARGETS" ]]; then
    SELECTED_TARGETS=$REQUESTED_TARGETS
  elif [[ -n "$STORED_TARGETS" ]]; then
    SELECTED_TARGETS=$STORED_TARGETS
  else
    SELECTED_TARGETS=$AVAILABLE_TARGETS
  fi
}

# The recorded targets that stay installed after this run.
remaining_targets() {
  local name result=""
  for name in $RECORDED_TARGETS; do
    list_contains "$SELECTED_TARGETS" "$name" || result="${result:+$result }$name"
  done
  printf '%s' "$result"
}

# True when <destination> is an exact link to this checkout, at the item's current or old source.
is_own_link() {
  local item=$1 destination_rel
  destination_rel=$(item_destination "$item")
  is_managed_link "$HOME/$destination_rel" "$REPO_ROOT/$(item_source "$item")" || is_moved_link "$destination_rel"
}

# True when <destination> is a regular file that still matches the checksum recorded when the
# installer copied it there.
is_own_copy() {
  local destination_rel=$1 recorded
  recorded=$(recorded_checksum "$destination_rel")
  [[ -n "$recorded" && -f "$HOME/$destination_rel" && ! -L "$HOME/$destination_rel" &&
    "$(file_checksum "$HOME/$destination_rel")" == "$recorded" ]]
}

# Classifies every item into PLAN (skip, shared, remove, modified, foreign, absent); fails on unsafe
# parents, including the state/backup directories. A shared item still owned by a target that stays
# recorded is kept. Sets FORGET_COPIES when a copy record of a removed item has to be dropped.
plan_uninstall() {
  local item destination_rel destination problem failed=0 remaining
  remaining=$(remaining_targets)
  PLAN=()
  FORGET_COPIES=0
  for item in "${MANAGED_ITEMS[@]}"; do
    if ! item_in_targets "$item" "$SELECTED_TARGETS"; then
      PLAN+=("skip")
      continue
    fi
    if [[ -n "$remaining" ]] && item_in_targets "$item" "$remaining"; then
      PLAN+=("shared")
      continue
    fi
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    problem=$(parent_problem "$(dirname -- "$destination_rel")")
    if [[ -n "$problem" ]]; then
      error "$problem"
      failed=1
      PLAN+=("error")
    elif is_own_link "$item"; then
      PLAN+=("remove")
    elif is_copied_destination "$destination_rel" && is_own_copy "$destination_rel"; then
      PLAN+=("remove")
    elif is_copied_destination "$destination_rel" && [[ -n "$(recorded_checksum "$destination_rel")" ]] &&
      [[ -f "$destination" && ! -L "$destination" ]]; then
      PLAN+=("modified")
    elif [[ -e "$destination" || -L "$destination" ]]; then
      PLAN+=("foreign")
    else
      PLAN+=("absent")
    fi
    if is_copied_destination "$destination_rel" && [[ -n "$(recorded_checksum "$destination_rel")" ]]; then
      FORGET_COPIES=1
    fi
  done

  # report_backups lists this tree even on a no-op; never traverse it through a symlink.
  problem=$(parent_problem "$BACKUPS_DIR_REL")
  if [[ -n "$problem" ]]; then
    error "$problem"
    failed=1
  fi
  plan_retired || failed=1
  return "$failed"
}

plan_has_removals() {
  local action
  for action in "${PLAN[@]}"; do
    [[ "$action" == "remove" ]] && return 0
  done
  return 1
}

# Read-only: --dry-run never writes, so this is safe to call before acquiring the lock.
hooks_pending() {
  local out
  out=$(python3 "$SCRIPT_DIR/lib/claude_hooks.py" uninstall --home "$HOME" --dry-run 2>&1) || die "hooks preflight failed: $out"
  [[ "$out" != unchanged:* ]]
}

apply_uninstall() {
  local item destination_rel destination i=0 prefix="" removed=0
  [[ "$DRY_RUN" -eq 0 ]] || prefix="would "
  for item in "${MANAGED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    case "${PLAN[$i]}" in
      remove)
        if [[ "$DRY_RUN" -eq 0 ]]; then
          # shellcheck disable=SC2088 # "~/" is display text for the user, not a path to expand
          is_own_link "$item" || { is_copied_destination "$destination_rel" && is_own_copy "$destination_rel"; } ||
            die "~/$destination_rel changed during uninstallation; stopping"
          rm -- "$destination"
          removed=$((removed + 1))
        fi
        log "${prefix}remove: ~/$destination_rel"
        ;;
      shared) log "kept (shared with a target that stays installed): ~/$destination_rel" ;;
      modified) log "kept (copy modified since it was installed): ~/$destination_rel" ;;
      foreign) log "kept (not a link to this repository): ~/$destination_rel" ;;
      absent) log "absent: ~/$destination_rel" ;;
    esac
    # Once removed or kept, a copy is no longer the toolkit's: a later install backs it up.
    case "${PLAN[$i]}" in
      remove | modified | foreign | absent)
        if [[ "$DRY_RUN" -eq 0 ]] && is_copied_destination "$destination_rel" &&
          [[ -n "$(recorded_checksum "$destination_rel")" ]]; then
          write_copy_record "$destination_rel" ""
        fi
        ;;
    esac
    i=$((i + 1))
  done
  apply_retired "$DRY_RUN"
  [[ "$DRY_RUN" -eq 1 ]] || log "done: $removed removed, $RETIRED_REMOVED retired removed"
}

report_backups() {
  local backups="$HOME/$BACKUPS_DIR_REL" entry found=0
  [[ -d "$backups" && ! -L "$backups" ]] || return 0
  for entry in "$backups"/*; do
    [[ -d "$entry" && ! -L "$entry" ]] || continue
    if [[ "$found" -eq 0 ]]; then
      log "backups kept (restore manually if needed, see docs/installation.md):"
      found=1
    fi
    log "  $entry"
  done
}

manage_claude_hooks() {
  # No empty-array expansion (kept Bash 3.2 compatible): branch instead of building argv.
  if [[ "$DRY_RUN" -eq 1 ]]; then
    python3 "$SCRIPT_DIR/lib/claude_hooks.py" uninstall --home "$HOME" --dry-run
  else
    python3 "$SCRIPT_DIR/lib/claude_hooks.py" uninstall --home "$HOME"
  fi
}

main() {
  parse_args "$@"
  require_commands rm mv mkdir mktemp rmdir readlink dirname python3
  require_checksum_command
  require_home
  select_targets
  plan_uninstall || die "no changes were made"

  # Always preflighted (never short-circuited by plan_has_removals): invalid JSON or
  # an unsafe settings path must abort before apply_uninstall touches any symlink.
  # Only the claude target owns the settings hooks.
  local hooks_change=0 claude=0
  list_contains "$SELECTED_TARGETS" claude && claude=1
  if [[ "$claude" -eq 1 ]] && hooks_pending; then
    hooks_change=1
  fi

  # Locked whenever applying would actually change something (a managed or retired link or copy
  # removal, a pending hooks change, a copy record or targets state update), never for a pure no-op: keeps
  # the lock, and the ~/.ifdev-ai-tools state directory it creates, scoped to actual changes.
  if [[ "$DRY_RUN" -eq 0 ]] && { plan_has_removals || retired_has_removals || [[ "$hooks_change" -eq 1 ]] ||
    [[ "$FORGET_COPIES" -eq 1 ]] ||
    [[ "$(remaining_targets)" != "$STORED_TARGETS" ]]; }; then
    acquire_lock
    select_targets
    plan_uninstall || die "no changes were made"
    # The recorded targets may have changed before the lock was taken.
    claude=0
    list_contains "$SELECTED_TARGETS" claude && claude=1
    # Re-preflight under the lock, immediately before any link removal: another
    # process may have changed ~/.claude/settings.json since the first check above
    # (mirrors install.sh's re-plan/preflight under its own lock).
    [[ "$claude" -eq 0 ]] || hooks_pending || true
  fi
  apply_uninstall
  [[ "$claude" -eq 0 ]] || manage_claude_hooks
  local remaining
  remaining=$(remaining_targets)
  if [[ "$remaining" != "$STORED_TARGETS" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      log "would record targets: ${remaining:-none}"
    else
      write_recorded_targets "$remaining"
      log "recorded targets: ${remaining:-none}"
    fi
  fi
  report_backups
  [[ "$DRY_RUN" -eq 0 ]] || log "dry run: no changes were made"
}

main "$@"
