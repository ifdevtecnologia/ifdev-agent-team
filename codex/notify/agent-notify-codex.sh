#!/usr/bin/env bash
# Codex CLI native `notify` adapter: forwards Codex's turn-notification payload to the
# shared scripts/agent-notify.sh notifier. Opt-in only: this toolkit never edits the
# base ~/.codex/config.toml (see README); add manually, one line, to use it:
#
#   notify = ["bash", "-c", '"$HOME/.codex/notify/agent-notify-codex.sh" "$1"', "_"]
#
# Codex execs this array directly (no shell), so a literal "~" in a config-array
# argument is never expanded and would make Codex fail to even launch the adapter.
# The "bash -c" wrapper above is a real shell, so "$HOME" there does expand; do not
# replace it with a plain two-element ["bash", "~/..."] array.
#
# Honest limitation: Codex's built-in `notify` hook only fires on turn/task
# completion, unlike Claude Code's separate Stop/StopFailure/Notification hooks. A
# standalone (non-supervised) Codex session using only this adapter can therefore only
# ever report "complete", never "attention" or "blocked", and cannot infer a PARTIAL or
# BLOCKED outcome from a malformed or otherwise unrecognized turn-complete payload
# either: an unparsable payload still falls back to a plain "complete" ping below, never
# to "blocked". Only Codex sessions running the supervise-claude skill ever send
# "attention" or "blocked", and only once per run from
# scripts/notify_final_status.py's own single explicit final notification (see
# codex/skills/supervise-claude/SKILL.md), never from this generic turn-complete adapter.
set -uo pipefail

NOTIFY="$HOME/.claude/hooks/agent-notify.sh"

# Extracts a top-level JSON string field with a small, dependency-free pattern match.
# Never evaluates the input; on anything unexpected it falls back to a fixed default.
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

main() {
  local payload=${1:-} message
  message=$(json_string_field "last-assistant-message" "Codex finished its turn" "$payload")
  [[ -x "$NOTIFY" ]] && "$NOTIFY" codex complete "$message" || true
  exit 0
}

main "$@"
