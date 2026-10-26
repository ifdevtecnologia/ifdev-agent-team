#!/usr/bin/env bash
# Claude Code hook adapter: translates the Stop/StopFailure/Notification hook protocol
# into calls to the shared scripts/agent-notify.sh notifier. Registered in
# ~/.claude/settings.json by scripts/lib/claude_hooks.py (see scripts/install.sh).
#
# Always exits 0: a nonzero exit on the Stop hook tells Claude Code to keep going
# instead of stopping, which this notifier must never trigger by accident.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
NOTIFY="$SCRIPT_DIR/agent-notify.sh"

# Claude Code's documented Notification hook payload carries the discriminating field
# as "notification_type" (never "type"). Known attention-required values include
# "permission_prompt", "idle_prompt", "elicitation_dialog", "elicitation_url_dialog"
# and "agent_needs_input" (also used to build the JSON hook matcher
# scripts/lib/claude_hooks.py attaches to the Notification hook, see HOOK_MATCHERS
# there); this script stays a conservative denylist instead of duplicating that list,
# so any of them, and anything not on the denylist below, still notifies.
#
# Structural "notification_type" values known to be routine system confirmations, not
# something the user needs to act on. Matched by exact value on a dedicated field only,
# never against the free-text "message" a user would read: that message can legitimately
# contain any of these words without changing what kind of event this is.
NON_ATTENTION_NOTIFICATION_TYPES="auth_success"

# Extracts a top-level JSON string field with a small, dependency-free pattern match.
# Never evaluates the input; on anything unexpected it falls back to a fixed default,
# it never fails the hook.
json_string_field() {
  local field=$1 default=$2 json=$3 line value
  line=$(printf '%s' "$json" | grep -o "\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"\\]\|\\\\.\)*\"" | head -n 1) || true
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return 0
  fi
  value=${line#*:}
  value=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*"//' -e 's/"[[:space:]]*$//')
  value=${value//\\\"/\"}
  value=${value//\\\\/\\}
  if [[ -z "$value" ]]; then
    printf '%s' "$default"
  else
    printf '%s' "$value"
  fi
}

# True only for a "notification_type" value on the denylist above. Absent, empty or
# unrecognized values (older Claude Code versions, or a future type this list does not
# know yet) are treated as attention-worthy: the documented conservative choice is to
# notify rather than silently drop a real permission/idle/elicitation request.
is_non_attention_notification() {
  local notification_type=$1 known
  [[ -n "$notification_type" ]] || return 1
  for known in $NON_ATTENTION_NOTIFICATION_TYPES; do
    [[ "$notification_type" == "$known" ]] && return 0
  done
  return 1
}

main() {
  local kind=${1:-} json message notification_type

  case "$kind" in
    stop-success)
      "$NOTIFY" claude complete "Claude Code finished responding" || true
      ;;
    stop-failure)
      "$NOTIFY" claude blocked "Claude Code stopped with an error" || true
      ;;
    notification)
      json=$(cat)
      notification_type=$(json_string_field "notification_type" "" "$json")
      if ! is_non_attention_notification "$notification_type"; then
        message=$(json_string_field "message" "Claude Code needs your attention" "$json")
        "$NOTIFY" claude attention "$message" || true
      fi
      ;;
    *) : ;; # unknown hook kind: nothing to notify, never fail the hook
  esac
  exit 0
}

main "$@"
