# Agent notifications

Get a desktop popup, a sound or a terminal bell when an agent finishes, is blocked or needs you. Notifications are best-effort and local; they never change permissions, sandboxing or command authority.

| Tool | How it is wired | Events |
| --- | --- | --- |
| Claude Code | three hooks the installer merges into `~/.claude/settings.json` | `complete`, `blocked`, `attention` |
| Codex | one line you add to `~/.codex/config.toml` (opt-in) | `complete` only |

## The notifier

[`scripts/agent-notify.sh`](../scripts/agent-notify.sh) is a standalone, safe cross-agent notifier:

```bash
scripts/agent-notify.sh <codex|claude> <attention|complete|blocked> <message>
```

It tries a desktop notification (`notify-send`) and a sound cue (`paplay`, or `pw-play` when `paplay` is missing) when available and enabled, and falls back to a terminal bell (BEL) **only when neither one actually succeeded** (both missing, disabled or failing). The message always reaches every backend as one literal argument; it is never evaluated or interpreted as a command.

- `--no-desktop`, `--no-sound`, `--no-bell` disable one channel for a single call; `--no-bell` also disables the fallback itself.
- `IFDEV_AGENT_NOTIFY=0` and `IFDEV_AGENT_SOUND=0` do the same as `--no-desktop`/`--no-sound` for every call in that environment (for example a headless CI shell); a flag always overrides its variable, never the other way around.
- Ubuntu/Debian: install the `libnotify-bin` and `pulseaudio-utils` packages for the desktop and sound backends through your usual OS package manager; neither is required, the BEL fallback always works in a terminal.

## Claude Code hooks

The installer registers three hooks in `~/.claude/settings.json` (see [what the installer guarantees](installation.md#what-the-installer-guarantees)), all routed through `~/.claude/hooks/agent-notify-hook.sh`:

| Claude Code hook | Notifier event | Behavior |
| --- | --- | --- |
| `Stop` (successful turn end) | `complete` | Always fires. |
| `StopFailure` | `blocked` | Always fires. |
| `Notification` | `attention` | Filtered by the payload's structural `notification_type` field only (Claude Code's documented field name; not `type`), **never by reading the free-text message**: a known non-attention system confirmation (currently `auth_success`) is silently skipped; a documented attention-required value (`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`) notifies, and so does anything else, including a payload with no recognizable `notification_type` at all (older or unknown Claude Code versions). This conservative default means a real request is never silently dropped. |

The installed `Notification` hook also carries a `matcher` narrowed to that same attention-required set, as defense-in-depth alongside the script-side filtering above (see `HOOK_MATCHERS` in [`scripts/lib/claude_hooks.py`](../scripts/lib/claude_hooks.py)); the script-side filtering is what actually decides whether to notify and keeps working even against a Claude Code version that ignores the matcher.

The hook script itself keeps the regular, non-executable tracked file mode; the managed command Claude Code runs is `bash ~/.claude/hooks/agent-notify-hook.sh <kind>`, so it never needs its own execute bit.

## Codex

Codex's own built-in `notify` hook is opt-in only; this toolkit never edits the base `~/.codex/config.toml`. Add one line yourself to use [`targets/codex/notify/agent-notify-codex.sh`](../targets/codex/notify/agent-notify-codex.sh):

```toml
notify = ["bash", "-c", '"$HOME/.codex/notify/agent-notify-codex.sh" "$1"', "_"]
```

The adapter forwards to the shared notifier at `~/.codex/notify/agent-notify.sh` when it is installed there, and otherwise to `~/.claude/hooks/agent-notify.sh`.

Codex execs this array directly (no shell), so a literal `~` in a config-array argument is never expanded and would make Codex fail to even launch the adapter; a plain two-element `["bash", "~/.codex/notify/agent-notify-codex.sh"]` array does not work. The `bash -c` wrapper above is a real shell, so `$HOME` there does expand, and Codex's appended JSON payload reaches the adapter as `$1`, a single literal positional argument, never interpreted as a command.

**Limitation:** Codex's `notify` hook only fires on turn or task completion, unlike Claude Code's separate `Stop`, `StopFailure` and `Notification` hooks. A Codex session using this adapter can therefore only ever report `complete`, never `attention` or `blocked`, even for a malformed or unrecognized turn-complete payload (it falls back to a generic "finished its turn" `complete` message, never to `blocked`).

## Troubleshooting

- **No popup appears:** confirm `notify-send` is installed and a desktop/D-Bus session is available (headless shells and most CI runners have neither); the terminal bell fallback still fires unless `--no-bell` is set or the terminal has bells disabled.
- **No sound:** confirm `paplay` or `pw-play` is installed and a PulseAudio/PipeWire sink is available; a missing sound theme file is not an error, it just skips silently to the next candidate or the bell fallback.
- **Too many pings in an automated or CI shell:** set `IFDEV_AGENT_NOTIFY=0` and `IFDEV_AGENT_SOUND=0` (or `--no-bell` for a single call) in that environment.
- None of this changes permissions, sandboxing or command authority for Claude or Codex; it only sends best-effort local notifications.
