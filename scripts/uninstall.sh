#!/usr/bin/env bash
# Removes only the symlinks in $HOME that point to this repository's managed items.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=0
PLAN=()

usage() {
  cat <<'EOF'
Usage: scripts/uninstall.sh [--dry-run]

Removes the managed symlinks that point to this repository. Real files, directories,
foreign symlinks, sibling configuration and backups are left untouched. Also removes
this toolkit's notification hook entries from ~/.claude/settings.json, leaving every
other setting untouched (requires python3; see README). Backups are not restored
automatically; see README for manual restoration.

  --dry-run   print the planned actions without changing anything
  -h, --help  show this help
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
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

# Classifies every item into PLAN (remove, foreign, absent); fails on unsafe parents,
# including the state/backup directories.
plan_uninstall() {
  local item destination_rel destination problem failed=0
  PLAN=()
  for item in "${MANAGED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    problem=$(parent_problem "$(dirname -- "$destination_rel")")
    if [[ -n "$problem" ]]; then
      error "$problem"
      failed=1
      PLAN+=("error")
    elif is_managed_link "$destination" "$REPO_ROOT/$(item_source "$item")"; then
      PLAN+=("remove")
    elif [[ -e "$destination" || -L "$destination" ]]; then
      PLAN+=("foreign")
    else
      PLAN+=("absent")
    fi
  done

  # report_backups lists this tree even on a no-op; never traverse it through a symlink.
  problem=$(parent_problem "$BACKUPS_DIR_REL")
  if [[ -n "$problem" ]]; then
    error "$problem"
    failed=1
  fi
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
  local item destination_rel destination i=0 prefix=""
  [[ "$DRY_RUN" -eq 0 ]] || prefix="would "
  for item in "${MANAGED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    case "${PLAN[$i]}" in
      remove)
        if [[ "$DRY_RUN" -eq 0 ]]; then
          is_managed_link "$destination" "$REPO_ROOT/$(item_source "$item")" ||
            die "~/$destination_rel changed during uninstallation; stopping"
          rm -- "$destination"
        fi
        log "${prefix}remove: ~/$destination_rel"
        ;;
      foreign) log "kept (not a link to this repository): ~/$destination_rel" ;;
      absent) log "absent: ~/$destination_rel" ;;
    esac
    i=$((i + 1))
  done
}

report_backups() {
  local backups="$HOME/$BACKUPS_DIR_REL" entry found=0
  [[ -d "$backups" && ! -L "$backups" ]] || return 0
  for entry in "$backups"/*; do
    [[ -d "$entry" && ! -L "$entry" ]] || continue
    if [[ "$found" -eq 0 ]]; then
      log "backups kept (restore manually if needed, see README):"
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
  require_commands rm mkdir rmdir readlink dirname python3
  require_home
  plan_uninstall || die "no changes were made"

  # Always preflighted (never short-circuited by plan_has_removals): invalid JSON or
  # an unsafe settings path must abort before apply_uninstall touches any symlink.
  local hooks_change=0
  if hooks_pending; then
    hooks_change=1
  fi

  # Locked whenever applying would actually change something (a managed link removal
  # or a pending hooks change), never for a pure no-op: keeps the lock, and the
  # ~/.ifdev-ai-tools state directory it creates, scoped to actual changes.
  if [[ "$DRY_RUN" -eq 0 ]] && { plan_has_removals || [[ "$hooks_change" -eq 1 ]]; }; then
    acquire_lock
    plan_uninstall || die "no changes were made"
    # Re-preflight under the lock, immediately before any link removal: another
    # process may have changed ~/.claude/settings.json since the first check above
    # (mirrors install.sh's re-plan/preflight under its own lock).
    hooks_pending || true
  fi
  apply_uninstall
  manage_claude_hooks
  report_backups
  [[ "$DRY_RUN" -eq 0 ]] || log "dry run: no changes were made"
}

main "$@"
