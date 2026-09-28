#!/usr/bin/env python3
"""Sends the supervise-claude skill's single final status notification.

Called by Codex exactly once per supervised run, after `final-report.md` is written
(see SKILL.md "Stop and report"), never from inside Claude's own cycle. This is the
one place a supervised run's COMPLETE/PARTIAL/BLOCKED outcome is announced; Codex's
own native `notify` turn-complete event (see codex/notify/agent-notify-codex.sh) is a
separate, standalone-only adapter and is never used to carry this status.

Maps status to the shared scripts/agent-notify.sh event and default message:
  COMPLETE -> complete  "Task complete — PR ready"
  PARTIAL  -> attention "Task needs attention"
  BLOCKED  -> blocked   "Task blocked"

A run-directory marker file makes this exactly-once: the marker is claimed through an
atomic exclusive-create (os.open with O_CREAT|O_EXCL), so even concurrent calls for the
same run directory race safely at the OS level and only the single winner ever proceeds
to notify; every other call is a silent no-op, never a duplicate notification. Sending
is always best-effort: a missing or failing notifier, or any runtime problem invoking
it, never raises or affects the run's reported status.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

STATUS_EVENTS = {
    "COMPLETE": ("complete", "Task complete — PR ready"),
    "PARTIAL": ("attention", "Task needs attention"),
    "BLOCKED": ("blocked", "Task blocked"),
}

MARKER_NAME = ".final-status-notified"


def default_notifier() -> Path:
    """The shared notifier bundled in this same checkout, resolved from this file's own
    location so it works for any target project the skill supervises, installed or not:
    codex/skills/supervise-claude/scripts/ -> repository root -> scripts/agent-notify.sh."""
    return Path(__file__).resolve().parents[4] / "scripts" / "agent-notify.sh"


def send_final_status(run_dir: Path, status: str, message: str = "", notifier: Path | None = None) -> bool:
    """Best-effort, exactly once per run_dir, safe under concurrent callers. Returns
    True for the single call that actually wins the marker (and attempts the
    notification), False for every other call for that same run_dir. Never raises for
    a missing, non-executable or failing notifier."""
    if status not in STATUS_EVENTS:
        raise ValueError(f"unsupported status: {status}")
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = run_dir / MARKER_NAME
    # Atomic exclusive create: the OS guarantees only one caller ever succeeds here,
    # even under real concurrent processes/threads racing for the same run_dir, so
    # this is the actual claim, not merely an existence check before writing.
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{status}\n")
    event, default_message = STATUS_EVENTS[status]
    text = message.strip() or default_message
    script = notifier if notifier is not None else default_notifier()
    try:
        if script.is_file():
            subprocess.run([str(script), "codex", event, text], check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--status", required=True, choices=tuple(STATUS_EVENTS))
    parser.add_argument("--message", default="")
    parser.add_argument("--notifier", type=Path, default=None, help="override for tests; defaults to the bundled scripts/agent-notify.sh")
    args = parser.parse_args(argv)
    send_final_status(args.run_dir, args.status, args.message, args.notifier)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
