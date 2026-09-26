#!/usr/bin/env bash
# Links the managed global Claude Code and Codex files from this repository into $HOME.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

DRY_RUN=0
PLAN=()

usage() {
  cat <<'EOF'
Usage: scripts/install.sh [--dry-run]

Creates symlinks in $HOME for the nine items managed by this repository.
Existing files, directories or foreign/dangling symlinks at those exact paths are
moved to ~/.ifdev-ai-tools/backups/<timestamp>-<unique>/ before linking.

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

# Classifies every item into PLAN (ok, create, backup) without touching the filesystem.
# Returns non-zero after reporting every problem, so nothing is changed on partial safety.
plan_install() {
  local item source_rel destination_rel target destination problem failed=0
  PLAN=()
  for item in "${MANAGED_ITEMS[@]}"; do
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
    problem=$(parent_problem "$(dirname -- "$destination_rel")")
    if [[ -n "$problem" ]]; then
      error "$problem"
      failed=1
      PLAN+=("error")
      continue
    fi

    if is_managed_link "$destination" "$target"; then
      PLAN+=("ok")
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
  return "$failed"
}

print_plan() {
  local item destination_rel i=0
  for item in "${MANAGED_ITEMS[@]}"; do
    destination_rel=$(item_destination "$item")
    case "${PLAN[$i]}" in
      ok) log "unchanged: ~/$destination_rel already links to this repository" ;;
      create) log "would link: ~/$destination_rel -> $REPO_ROOT/$(item_source "$item")" ;;
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
  log "done: $linked linked, $backed_up backed up, $unchanged unchanged"
  [[ -z "$backup_dir" ]] || log "backups: $backup_dir (see README for manual restoration)"
}

main() {
  parse_args "$@"
  require_commands ln mv mkdir rmdir readlink mktemp date dirname
  require_home
  plan_install || die "no changes were made"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    print_plan
    log "dry run: no changes were made"
    return 0
  fi

  acquire_lock
  # Re-plan under the lock: another process may have changed HOME since the first check.
  plan_install || die "no changes were made"
  apply_install
}

main "$@"
