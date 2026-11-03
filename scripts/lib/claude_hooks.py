#!/usr/bin/env python3
"""Safe structured merge/removal of this toolkit's notification hooks in
~/.claude/settings.json (see scripts/install.sh and scripts/uninstall.sh).

Claude Code's settings.json is user-owned and mutable, so it cannot be symlinked like
the other managed items: it is merged instead. This module only ever adds or removes
the exact three hook command entries listed in HOOK_COMMANDS; every other key, event,
matcher and command in the file keeps the same semantic content (key order is
preserved; only the changed event arrays are rewritten). The file is rewritten in full
on every change (see _write), so its exact original bytes/whitespace are not preserved,
only its meaning.

Uses only the standard library (no PyYAML/dev dependency) so scripts/install.sh and
scripts/uninstall.sh only need python3 itself, matching how scripts/validate.sh
already delegates static validation to scripts/validate.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# hook_event_name -> managed command line. Claude Code runs hook commands through the
# shell, so "~" expands there; this stays portable across machines and backups instead
# of baking in an absolute path. Values are opaque to Claude Code: only this module's
# exact string match decides whether an entry is ours.
#
# Explicitly invoked through "bash" rather than executed directly: the linked hook
# script keeps this repository's regular, non-executable tracked file mode (see
# targets/claude/hooks/agent-notify-hook.sh), so nothing here needs its own execute bit set or
# weakened to run.
HOOK_COMMANDS = {
    "Stop": "bash ~/.claude/hooks/agent-notify-hook.sh stop-success",
    "StopFailure": "bash ~/.claude/hooks/agent-notify-hook.sh stop-failure",
    "Notification": "bash ~/.claude/hooks/agent-notify-hook.sh notification",
}

# Claude Code's documented attention-required "notification_type" values for the
# Notification hook. Used to narrow the "matcher" this module attaches to the managed
# Notification group below (see HOOK_MATCHERS): defense-in-depth only, since
# targets/claude/hooks/agent-notify-hook.sh already filters on this same field itself and must
# keep working even against a Claude Code version that ignores this matcher.
ATTENTION_NOTIFICATION_TYPES = (
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
)

# event -> matcher string narrowing when the managed command actually runs, for events
# whose group schema supports it. Absent for an event means no matcher is attached
# (every invocation of that event still runs the managed command, as before).
HOOK_MATCHERS = {
    "Notification": "|".join(ATTENTION_NOTIFICATION_TYPES),
}

SETTINGS_REL = ".claude/settings.json"


class HookInstallError(Exception):
    """An unsafe or ambiguous condition; refuse without changing anything."""


def _unsafe_existing(path: Path) -> str:
    """Non-empty when path exists but is not something safe to read/write in place."""
    if path.is_symlink():
        return f"refusing symlinked path: {path}"
    if path.exists() and not path.is_file():
        return f"path is not a regular file: {path}"
    return ""


def _unsafe_parent_dir(path: Path) -> str:
    """Non-empty when a directory meant to hold our file is a symlink or not a directory."""
    if path.is_symlink():
        return f"refusing symlinked parent directory: {path}"
    if path.exists() and not path.is_dir():
        return f"parent path is not a directory: {path}"
    return ""


def _settings_paths(home: Path) -> tuple:
    claude_dir = home / ".claude"
    settings = claude_dir / "settings.json"
    return claude_dir, settings


def _load(settings: Path) -> dict:
    """Empty dict for an absent or empty file; raises for anything unsafe to merge into."""
    if not settings.exists():
        return {}
    text = settings.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookInstallError(f"{settings} is not valid JSON ({exc}); fix it manually, nothing was changed") from exc
    if not isinstance(data, dict):
        raise HookInstallError(f"{settings} must contain a JSON object at the top level; nothing was changed")
    return data


def _validate_managed_hooks_shape(settings: Path, data: dict) -> None:
    """Refuses a malformed or ambiguous managed "hooks" shape outright, for both
    dry-run and apply: a top-level "hooks" (when present) must be a JSON object, and
    each of the three managed events (when present in it) must be a JSON array whose
    entries are JSON objects with a "hooks" member that is itself a JSON array. Never
    silently treated as "missing" (which would make plan_install/plan_uninstall think
    nothing is configured) or otherwise ignored."""
    if "hooks" not in data:
        return
    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        raise HookInstallError(f'{settings}: "hooks" must be a JSON object; nothing was changed')
    for event in HOOK_COMMANDS:
        if event not in hooks:
            continue
        groups = hooks[event]
        if not isinstance(groups, list):
            raise HookInstallError(f'{settings}: "hooks.{event}" must be a JSON array; nothing was changed')
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise HookInstallError(f'{settings}: "hooks.{event}[{index}]" must be a JSON object; nothing was changed')
            if not isinstance(group.get("hooks"), list):
                raise HookInstallError(f'{settings}: "hooks.{event}[{index}].hooks" must be a JSON array; nothing was changed')


def _command_entry(command: str) -> dict:
    return {"type": "command", "command": command}


def _group_payload(event: str) -> dict:
    """The exact group this module installs for `event`: a "matcher" key first (only
    for an event listed in HOOK_MATCHERS), then "hooks"."""
    matcher = HOOK_MATCHERS.get(event)
    payload = {}
    if matcher is not None:
        payload["matcher"] = matcher
    payload["hooks"] = [_command_entry(HOOK_COMMANDS[event])]
    return payload


def _group_has_command(group, command: str) -> bool:
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(entry, dict) and entry.get("type") == "command" and entry.get("command") == command for entry in hooks)


def _is_our_only_group(group: dict, command: str, event: str) -> bool:
    """True only for a group this module could have created for `event`: the exact
    command entry and nothing beyond an optional "matcher" that, when present, matches
    the one this module attaches for that event (see HOOK_MATCHERS). A group installed
    by a toolkit version before matchers existed (no "matcher" key at all) still
    qualifies, so uninstall keeps removing it cleanly on already-installed machines."""
    if group.get("hooks") != [_command_entry(command)]:
        return False
    expected_matcher = HOOK_MATCHERS.get(event)
    allowed_keys = {"hooks"} | ({"matcher"} if expected_matcher is not None else set())
    if not set(group.keys()) <= allowed_keys:
        return False
    if "matcher" in group and group["matcher"] != expected_matcher:
        return False
    return True


def plan_install(data: dict) -> list:
    """Event names that still need their managed command added."""
    hooks = data.get("hooks", {})
    missing = []
    for event, command in HOOK_COMMANDS.items():
        groups = hooks.get(event, []) if isinstance(hooks, dict) else []
        if not isinstance(groups, list) or not any(_group_has_command(group, command) for group in groups):
            missing.append(event)
    return missing


def plan_uninstall(data: dict) -> list:
    """Event names that currently carry the managed command."""
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return []
    present = []
    for event, command in HOOK_COMMANDS.items():
        groups = hooks.get(event, [])
        if isinstance(groups, list) and any(_group_has_command(group, command) for group in groups):
            present.append(event)
    return present


def _apply_install(data: dict, events: list) -> dict:
    data = dict(data)
    hooks = dict(data.get("hooks", {}))
    for event in events:
        groups = list(hooks.get(event, []))
        groups.append(_group_payload(event))
        hooks[event] = groups
    data["hooks"] = hooks
    return data


def _apply_uninstall(data: dict, events: list) -> dict:
    data = dict(data)
    hooks = dict(data.get("hooks", {}))
    for event in events:
        command = HOOK_COMMANDS[event]
        kept = []
        for group in hooks.get(event, []):
            if _is_our_only_group(group, command, event):
                continue  # drop the whole group we created; nothing else was in it
            if isinstance(group, dict) and isinstance(group.get("hooks"), list) and _group_has_command(group, command):
                group = dict(group)
                group["hooks"] = [entry for entry in group["hooks"] if not (isinstance(entry, dict) and entry.get("command") == command)]
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return data


def _backup(home: Path, settings: Path) -> Path:
    backups = home / ".ifdev-ai-tools" / "backups"
    problem = _unsafe_parent_dir(home / ".ifdev-ai-tools") or _unsafe_parent_dir(backups)
    if problem:
        raise HookInstallError(problem)
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(tempfile.mkdtemp(prefix=f"{stamp}-", suffix="-hooks", dir=str(backups)))
    target = backup_dir / SETTINGS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings, target)
    (backup_dir / "MANIFEST").write_text(SETTINGS_REL + "\n", encoding="utf-8")
    return backup_dir


def _write(settings: Path, data: dict) -> None:
    """Writes via a same-directory temporary regular file plus os.replace, so a reader
    (or a crash mid-write) never observes a partially written settings.json. Preserves
    the existing file's permission bits when it already exists; a new file gets the
    default permissions from the umask, same as a plain write. Cleans up the temporary
    file on any failure before or during the replace."""
    settings.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=False) + "\n"
    existing_mode = settings.stat().st_mode if settings.exists() else None
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".json.tmp", dir=str(settings.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if existing_mode is not None:
            os.chmod(tmp_path, stat.S_IMODE(existing_mode))
        os.replace(tmp_path, settings)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def run(action: str, home: Path, dry_run: bool) -> list:
    """Returns human-readable log lines; raises HookInstallError on unsafe input."""
    _, settings = _settings_paths(home)
    problem = _unsafe_parent_dir(home / ".claude") or _unsafe_existing(settings)
    if problem:
        raise HookInstallError(problem)
    data = _load(settings)
    _validate_managed_hooks_shape(settings, data)
    events = plan_install(data) if action == "install" else plan_uninstall(data)
    if not events:
        state = "already configured" if action == "install" else "not installed"
        return [f"unchanged: ~/{SETTINGS_REL} notification hooks {state}"]

    lines = []
    verb = "add" if action == "install" else "remove"
    for event in sorted(events, key=list(HOOK_COMMANDS).index):
        lines.append(f"{'would ' if dry_run else ''}{verb}: ~/{SETTINGS_REL} {event} hook")
    if dry_run:
        return lines

    updated = _apply_install(data, events) if action == "install" else _apply_uninstall(data, events)
    if settings.exists():
        backup_dir = _backup(home, settings)
        lines.append(f"backed up: ~/{SETTINGS_REL} -> {backup_dir}")
    _write(settings, updated)
    return lines


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    home = args.home
    if not home.is_absolute() or not home.is_dir():
        print(f"error: --home must be an existing absolute directory: {home}", file=sys.stderr)
        return 1
    try:
        for line in run(args.action, home, args.dry_run):
            print(line)
    except HookInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
