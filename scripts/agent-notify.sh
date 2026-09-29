#!/usr/bin/env bash
# Safe, standalone cross-agent notifier: desktop (notify-send), audio (paplay) and a
# terminal bell (BEL), each independently switchable. Never evaluates the message or
# event text; every value reaches backend commands only as a literal argument.
# Kept compatible with Bash 3.2 (macOS default): no associative arrays, no mapfile.
set -euo pipefail

AGENTS="codex claude"
EVENTS="attention complete blocked"

# Freedesktop sound theme candidates, tried in order; the first that exists is played.
# None existing is not an error: sound is best-effort, like every other backend here.
SOUND_CANDIDATES="
/usr/share/sounds/freedesktop/stereo/complete.oga
/usr/share/sounds/freedesktop/stereo/dialog-information.oga
/usr/share/sounds/freedesktop/stereo/bell.oga
"

usage() {
  cat <<'EOF'
Usage: scripts/agent-notify.sh <codex|claude> <attention|complete|blocked> <message> [switches]

Sends a best-effort notification: a desktop notifier (notify-send) and an audio cue
(paplay) when available and enabled, falling back to a terminal bell (BEL) only when
neither one actually succeeded. A missing or disabled backend is skipped silently;
only invalid usage is an error. The message is never evaluated or interpreted as a
command: it always reaches backends as a single literal argument.

  --no-desktop  skip notify-send even when available
  --no-sound    skip paplay even when available
  --no-bell     never fall back to the terminal bell
  -h, --help    show this help

Environment (a flag above always wins over its variable):
  IFDEV_AGENT_NOTIFY=0  same as --no-desktop
  IFDEV_AGENT_SOUND=0   same as --no-sound
EOF
}

contains_word() {
  local needle=$1 haystack=$2 word
  for word in $haystack; do
    [[ "$word" == "$needle" ]] && return 0
  done
  return 1
}

agent_label() {
  case "$1" in
    codex) printf 'Codex' ;;
    claude) printf 'Claude' ;;
  esac
}

event_label() {
  case "$1" in
    attention) printf 'needs attention' ;;
    complete) printf 'complete' ;;
    blocked) printf 'blocked' ;;
  esac
}

# Only "blocked" is urgent enough to interrupt; the others use normal urgency.
event_urgency() {
  case "$1" in
    blocked) printf 'critical' ;;
    *) printf 'normal' ;;
  esac
}

# Returns success only when notify-send actually ran and exited zero: main() uses this
# to decide whether the BEL fallback is needed, not only whether the attempt was made.
notify_desktop() {
  local agent=$1 event=$2 message=$3 enabled=$4 title urgency
  [[ "$enabled" -eq 1 ]] || return 1
  command -v notify-send >/dev/null 2>&1 || return 1
  title="$(agent_label "$agent") - $(event_label "$event")"
  urgency=$(event_urgency "$event")
  notify-send --urgency="$urgency" --app-name=agent-notify -- "$title" "$message"
}

# Returns success only when paplay actually played a candidate and exited zero; see
# notify_desktop for why the real exit status (not a forced 0) is preserved.
notify_sound() {
  local event=$1 enabled=$2 sound
  [[ "$enabled" -eq 1 ]] || return 1
  command -v paplay >/dev/null 2>&1 || return 1
  for sound in $SOUND_CANDIDATES; do
    if [[ -r "$sound" ]]; then
      paplay -- "$sound" >/dev/null 2>&1
      return $?
    fi
  done
  return 1
}

notify_bell() {
  local enabled=$1
  [[ "$enabled" -eq 1 ]] || return 0
  printf '\a'
}

main() {
  local agent event message use_bell=1
  local use_desktop=1 use_sound=1
  # Environment sets the default; an explicit --no-* switch below always wins and
  # never re-enables a backend the environment turned off.
  [[ "${IFDEV_AGENT_NOTIFY:-1}" == "0" ]] && use_desktop=0
  [[ "${IFDEV_AGENT_SOUND:-1}" == "0" ]] && use_sound=0

  if [[ $# -ge 1 ]]; then
    case "$1" in
      -h | --help)
        usage
        exit 0
        ;;
    esac
  fi
  if [[ $# -lt 3 ]]; then
    usage >&2
    exit 2
  fi
  agent=$1
  event=$2
  message=$3
  shift 3

  contains_word "$agent" "$AGENTS" || {
    printf 'error: unknown agent: %s\n' "$agent" >&2
    exit 2
  }
  contains_word "$event" "$EVENTS" || {
    printf 'error: unknown event: %s\n' "$event" >&2
    exit 2
  }
  [[ -n "$message" ]] || {
    printf 'error: message must not be empty\n' >&2
    exit 2
  }

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-desktop) use_desktop=0 ;;
      --no-sound) use_sound=0 ;;
      --no-bell) use_bell=0 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        printf 'error: unknown argument: %s\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done

  local desktop_ok=0 sound_ok=0
  if notify_desktop "$agent" "$event" "$message" "$use_desktop"; then
    desktop_ok=1
  fi
  if notify_sound "$event" "$use_sound"; then
    sound_ok=1
  fi
  # BEL is the fallback, not an independent channel: it only fires when neither the
  # desktop notifier nor the sound cue actually got the notification through.
  if [[ "$desktop_ok" -eq 0 && "$sound_ok" -eq 0 ]]; then
    notify_bell "$use_bell"
  fi
  return 0
}

main "$@"
