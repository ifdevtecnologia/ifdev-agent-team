#!/usr/bin/env bash
# Reports, without changing anything, the toolkit version of this checkout and the state of every
# managed item and Claude notification hook installed from it.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

PROBLEMS=0

usage() {
  cat <<'EOF'
Usage: scripts/status.sh [--target claude|codex|all]...

Read-only: prints the toolkit version of this checkout (the latest released section of
CHANGELOG.md and the Git commit) and, for each installed target, the state of every managed
item below $HOME and, for claude, of the notification hooks in ~/.claude/settings.json.
Without --target, reports the targets recorded in ~/.ifdev-ai-tools/targets. It never writes,
never takes the installer lock and never follows a symlinked parent directory.

Item states: ok, outdated (rerun install.sh), modified (a Codex agent copy edited since it was
installed), missing, broken (a dangling link), foreign (not this toolkit's), unsafe-parent.
Exit status: 0 when everything is ok, 1 when something needs attention, 2 on invalid usage.

  --target    target to report (repeatable); available: claude, codex, or all
  -h, --help  show this help
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
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

# Prints the latest released version in CHANGELOG.md, or nothing.
changelog_version() {
  local file="$REPO_ROOT/CHANGELOG.md"
  [[ -f "$file" ]] || return 0
  sed -n 's/^## \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\) - .*/\1/p' "$file" | head -n 1
}

# True when the Unreleased section of CHANGELOG.md lists at least one entry.
has_unreleased_entries() {
  local file="$REPO_ROOT/CHANGELOG.md"
  [[ -f "$file" ]] || return 1
  awk '/^## /{ in_section = ($0 == "## Unreleased"); next } in_section && /[^[:space:]]/{ found = 1 } END{ exit !found }' "$file"
}

# Leaves even the checkout untouched: `describe --dirty` refreshes .git/index and takes its lock,
# ignoring GIT_OPTIONAL_LOCKS, so dirtiness comes from `status`, which honours it.
git_description() {
  local description commit
  command -v git >/dev/null 2>&1 || return 0
  description=$(GIT_OPTIONAL_LOCKS=0 git -C "$REPO_ROOT" describe --tags --always 2>/dev/null) || return 0
  commit=$(GIT_OPTIONAL_LOCKS=0 git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null) || return 0
  if [[ -n "$(GIT_OPTIONAL_LOCKS=0 git -C "$REPO_ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    description="$description-dirty"
  fi
  printf '%s, commit %s' "$description" "$commit"
}

print_version() {
  local version description
  version=$(changelog_version)
  description=$(git_description)
  log "toolkit: ${version:-unknown version} (${description:-not a Git checkout})"
  log "checkout: $REPO_ROOT"
  if has_unreleased_entries; then
    log "note: CHANGELOG.md lists unreleased changes newer than ${version:-the last release}"
  fi
}

report() {
  printf '%-14s~/%s\n' "$1" "$2"
  [[ "$1" == "ok" ]] || PROBLEMS=$((PROBLEMS + 1))
}

# State of a copied item (a Codex custom agent) whose source is <source>.
copy_state() {
  local destination=$1 destination_rel=$2 source=$3 checksum source_checksum
  if [[ -L "$destination" ]]; then
    if is_managed_link "$destination" "$source"; then
      printf 'outdated'
    elif [[ -e "$destination" ]]; then
      printf 'foreign'
    else
      printf 'broken'
    fi
  elif [[ ! -e "$destination" ]]; then
    printf 'missing'
  elif [[ ! -f "$destination" ]] || ! checksum=$(file_checksum "$destination"); then
    printf 'foreign'
  elif source_checksum=$(file_checksum "$source") && [[ "$checksum" == "$source_checksum" ]]; then
    # Equal but unrecorded: not the toolkit's until the installer adopts it (records its checksum).
    if [[ "$checksum" == "$(recorded_checksum "$destination_rel")" ]]; then
      printf 'ok'
    else
      printf 'outdated'
    fi
  elif [[ "$checksum" == "$(recorded_checksum "$destination_rel")" ]]; then
    printf 'outdated'
  elif [[ -n "$(recorded_checksum "$destination_rel")" ]]; then
    printf 'modified'
  else
    printf 'foreign'
  fi
}

# State of a linked item whose source is <source>.
link_state() {
  local destination=$1 destination_rel=$2 source=$3
  if is_managed_link "$destination" "$source"; then
    if [[ -e "$destination" ]]; then
      printf 'ok'
    else
      printf 'broken'
    fi
  elif is_moved_link "$destination_rel"; then
    printf 'outdated'
  elif [[ -L "$destination" && ! -e "$destination" ]]; then
    printf 'broken'
  elif [[ -e "$destination" || -L "$destination" ]]; then
    printf 'foreign'
  else
    printf 'missing'
  fi
}

report_items() {
  local item destination_rel destination source
  log "items:"
  for item in "${MANAGED_ITEMS[@]}"; do
    item_in_targets "$item" "$SELECTED_TARGETS" || continue
    destination_rel=$(item_destination "$item")
    destination="$HOME/$destination_rel"
    source="$REPO_ROOT/$(item_source "$item")"
    if [[ -n "$(parent_problem "$(dirname -- "$destination_rel")")" ]]; then
      report "unsafe-parent" "$destination_rel"
    elif is_copied_destination "$destination_rel"; then
      report "$(copy_state "$destination" "$destination_rel" "$source")" "$destination_rel"
    else
      report "$(link_state "$destination" "$destination_rel" "$source")" "$destination_rel"
    fi
  done
}

report_hooks() {
  local output line
  log "hooks (~/.claude/settings.json):"
  if ! command -v python3 >/dev/null 2>&1; then
    log "  unknown: python3 not found"
    PROBLEMS=$((PROBLEMS + 1))
    return 0
  fi
  if ! output=$(python3 "$SCRIPT_DIR/lib/claude_hooks.py" status --home "$HOME" 2>&1); then
    log "  unsafe: ${output#error: }"
    PROBLEMS=$((PROBLEMS + 1))
    return 0
  fi
  while IFS= read -r line; do
    log "  $line"
    [[ "$line" == present:* ]] || PROBLEMS=$((PROBLEMS + 1))
  done <<<"$output"
}

report_codex_override() {
  local override="$HOME/.codex/AGENTS.override.md"
  list_contains "$SELECTED_TARGETS" codex || return 0
  [[ -e "$override" || -L "$override" ]] || return 0
  log "note: ~/.codex/AGENTS.override.md exists: Codex reads it instead of ~/.codex/AGENTS.md"
  PROBLEMS=$((PROBLEMS + 1))
}

main() {
  parse_args "$@"
  require_commands readlink dirname sed awk
  require_home
  print_version
  read_recorded_targets
  SELECTED_TARGETS=${REQUESTED_TARGETS:-$RECORDED_TARGETS}
  if [[ -z "$SELECTED_TARGETS" ]]; then
    log "targets: none installed from this checkout (install with scripts/install.sh --target)"
    return 0
  fi
  log "targets: $SELECTED_TARGETS${STORED_TARGETS:+ (recorded: $STORED_TARGETS)}"
  require_checksum_command
  report_items
  if list_contains "$SELECTED_TARGETS" claude; then
    report_hooks
  fi
  report_codex_override
  if [[ "$PROBLEMS" -eq 0 ]]; then
    log "summary: everything is ok"
    return 0
  fi
  log "summary: $PROBLEMS item(s) need attention; see \"Check the installation\" in docs/installation.md"
  return 1
}

main "$@"
