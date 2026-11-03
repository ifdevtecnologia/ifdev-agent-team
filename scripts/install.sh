#!/usr/bin/env bash
# Links the managed global files of the chosen targets from this repository into $HOME.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=0
PLAN=()

usage() {
  cat <<'EOF'
Usage: scripts/install.sh [--target claude|codex|all]... [--dry-run]

Creates symlinks in $HOME for the items of the chosen targets (claude: thirty-three
items below ~/.claude and ~/.codex/notify; codex: twenty-nine items below ~/.codex
and ~/.agents/skills, one of them the notify adapter both targets share) and
records the installed targets in ~/.ifdev-ai-tools/targets. Without --target,
reinstalls the recorded targets; a machine installed before targets were recorded
is detected as claude, and a machine with nothing installed must choose a target.
Existing files, directories or foreign/dangling symlinks at those exact paths are
moved to ~/.ifdev-ai-tools/backups/<timestamp>-<unique>/ before linking; a link to
an item's old path in this checkout is relinked without a backup. Links left by
retired items are removed only when they still point to this checkout. The claude
target also merges this toolkit's notification hooks into ~/.claude/settings.json,
preserving every other setting (requires python3; see README). The codex target
never edits ~/.codex/config.toml and warns when ~/.codex/AGENTS.override.md exists.

  --target    target to install (repeatable); available: claude, codex, or all
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

# Sets SELECTED_TARGETS from --target, else the recorded targets, else claude on a machine
# installed before targets were recorded. Read-only; exits 2 when no target can be chosen.
select_targets() {
  read_recorded_targets
  if [[ -n "$REQUESTED_TARGETS" ]]; then
    SELECTED_TARGETS=$REQUESTED_TARGETS
  elif [[ -n "$RECORDED_TARGETS" ]]; then
    SELECTED_TARGETS=$RECORDED_TARGETS
  elif has_legacy_install; then
    SELECTED_TARGETS="claude"
  else
    error "nothing is installed from this checkout yet: choose --target ($AVAILABLE_TARGETS or all); no changes were made"
    usage >&2
    exit 2
  fi
}

# Classifies every item into PLAN (skip, ok, relink, create, backup) without touching the
# filesystem. Returns non-zero after reporting every problem, so nothing is changed on partial safety.
plan_install() {
  local item source_rel destination_rel target destination problem failed=0
  PLAN=()
  for item in "${MANAGED_ITEMS[@]}"; do
    if ! item_in_targets "$item" "$SELECTED_TARGETS"; then
      PLAN+=("skip")
      continue
    fi
    source_rel=$(item_source "$item")
    destination_rel=$(item_destination "$item")
    target="$REPO_ROOT/$source_rel"
    destination="$HOME/$destination_rel"

    if [[ -L "$target" || ! -e "$target" ]]; then
      error "managed source is missing or is a symlink: $target"
      failed=1
      PLAN+=("error")
      continue
    fi
    problem=$(parent_problem "$(dirname -- "$source_rel")" "$REPO_ROOT")
    if [[ -z "$problem" ]]; then
      problem=$(parent_problem "$(dirname -- "$destination_rel")")
    fi
    if [[ -n "$problem" ]]; then
      error "$problem"
      failed=1
      PLAN+=("error")
      continue
    fi

    if is_managed_link "$destination" "$target"; then
      PLAN+=("ok")
    elif is_moved_link "$destination_rel"; then
      PLAN+=("relink")
    elif [[ -e "$destination" || -L "$destination" ]]; then
      PLAN+=("backup")
    else
      PLAN+=("create")
    fi
  done

  problem=$(parent_problem "$BACKUPS_DIR_REL")
  if [[ -n "$problem" ]]; then
    error "$problem"
    failed=1
  fi
  plan_retired || failed=1
  return "$failed"
}

# Same order as apply_install: retired links first, then the managed items.
print_plan() {
  local item destination_rel i=0
  apply_retired 1
  for item in "${MANAGED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    case "${PLAN[$i]}" in
      ok) log "unchanged: ~/$destination_rel already links to this repository" ;;
      create) log "would link: ~/$destination_rel -> $REPO_ROOT/$(item_source "$item")" ;;
      relink) log "would relink: ~/$destination_rel -> $REPO_ROOT/$(item_source "$item") (moved source)" ;;
      backup) log "would back up ~/$destination_rel to ~/$BACKUPS_DIR_REL/<timestamp>-<unique>/ and link it" ;;
    esac
    i=$((i + 1))
  done
}

create_backup_dir() {
  local backups="$HOME/$BACKUPS_DIR_REL"
  mkdir -p -- "$backups"
  [[ -d "$backups" && ! -L "$backups" ]] || die "unsafe backup directory: $backups"
  mktemp -d "$backups/$(date +%Y%m%d-%H%M%S)-XXXXXX"
}

apply_install() {
  local item source_rel destination_rel target destination backup_dir="" i=0
  local linked=0 backed_up=0 unchanged=0
  apply_retired 0
  for item in "${MANAGED_ITEMS[@]}"; do
    source_rel=$(item_source "$item")
    destination_rel=$(item_destination "$item")
    target="$REPO_ROOT/$source_rel"
    destination="$HOME/$destination_rel"

    case "${PLAN[$i]}" in
      ok)
        unchanged=$((unchanged + 1))
        log "unchanged: ~/$destination_rel"
        ;;
      relink)
        is_moved_link "$destination_rel" || die "~/$destination_rel changed during installation; stopping"
        rm -- "$destination"
        ln -s -- "$target" "$destination"
        linked=$((linked + 1))
        log "relinked: ~/$destination_rel -> $target (moved source)"
        ;;
      create | backup)
        mkdir -p -- "$(dirname -- "$destination")"
        if [[ "${PLAN[$i]}" == "backup" ]]; then
          [[ -n "$backup_dir" ]] || backup_dir=$(create_backup_dir)
          mkdir -p -- "$backup_dir/$(dirname -- "$destination_rel")"
          mv -- "$destination" "$backup_dir/$destination_rel"
          printf '%s\n' "$destination_rel" >>"$backup_dir/MANIFEST"
          backed_up=$((backed_up + 1))
          log "backed up: ~/$destination_rel -> $backup_dir/$destination_rel"
        fi
        if [[ -e "$destination" || -L "$destination" ]]; then
          die "~/$destination_rel reappeared during installation; stopping"
        fi
        ln -s -- "$target" "$destination"
        linked=$((linked + 1))
        log "linked: ~/$destination_rel -> $target"
        ;;
    esac
    i=$((i + 1))
  done
  log "done: $linked linked, $backed_up backed up, $unchanged unchanged, $RETIRED_REMOVED retired removed"
  [[ -z "$backup_dir" ]] || log "backups: $backup_dir (see README for manual restoration)"
}

manage_claude_hooks() {
  # No empty-array expansion (kept Bash 3.2 compatible): branch instead of building argv.
  if [[ "$DRY_RUN" -eq 1 ]]; then
    python3 "$SCRIPT_DIR/lib/claude_hooks.py" install --home "$HOME" --dry-run
  else
    python3 "$SCRIPT_DIR/lib/claude_hooks.py" install --home "$HOME"
  fi
}

# Read-only: --dry-run never writes, so this is safe to call before any symlink is
# touched. Surfaces invalid JSON, unsafe paths and malformed hooks/event shapes early
# so a broken ~/.claude/settings.json can never leave apply_install's symlinks applied
# with the hooks step failing afterward.
preflight_claude_hooks() {
  python3 "$SCRIPT_DIR/lib/claude_hooks.py" install --home "$HOME" --dry-run >/dev/null
}

# The recorded targets after this run: the ones already recorded plus the ones installed now.
targets_to_record() {
  canonical_targets "$RECORDED_TARGETS $SELECTED_TARGETS"
}

# Plans and preflights the selected targets; the Claude settings hooks only for claude.
plan_all() {
  select_targets
  plan_install || die "no changes were made"
  if list_contains "$SELECTED_TARGETS" claude; then
    preflight_claude_hooks || die "no changes were made"
  fi
}

# Codex reads ~/.codex/AGENTS.override.md instead of ~/.codex/AGENTS.md when it exists; the
# installer never touches it (it is the user's file), so it only warns.
warn_codex_override() {
  local override="$HOME/.codex/AGENTS.override.md"
  list_contains "$SELECTED_TARGETS" codex || return 0
  [[ -e "$override" || -L "$override" ]] || return 0
  printf 'warning: %s\n' "~/.codex/AGENTS.override.md exists: Codex reads it instead of ~/.codex/AGENTS.md, so the installed global instructions do not load until you merge it into them or remove it (left untouched)" >&2
}

main() {
  parse_args "$@"
  require_commands ln mv rm mkdir rmdir readlink mktemp date dirname python3
  require_home
  plan_all
  warn_codex_override

  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_plan
    if list_contains "$SELECTED_TARGETS" claude; then
      manage_claude_hooks
    fi
    [[ "$(targets_to_record)" == "$RECORDED_TARGETS" ]] || log "would record targets: $(targets_to_record)"
    log "dry run: no changes were made"
    return 0
  fi

  acquire_lock
  # Re-plan under the lock: another process may have changed HOME since the first check.
  plan_all
  apply_install
  if list_contains "$SELECTED_TARGETS" claude; then
    manage_claude_hooks
  fi
  if [[ "$(targets_to_record)" != "$RECORDED_TARGETS" ]]; then
    write_recorded_targets "$(targets_to_record)"
    log "recorded targets: $(targets_to_record)"
  fi
}

main "$@"
