#!/usr/bin/env bash
# Entry point for the repository's static validation (see scripts/validate.py).
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

for cmd in python3 git bash; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf 'error: required command not found: %s\n' "$cmd" >&2
    exit 1
  fi
done

if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  printf 'error: PyYAML is required; install the pinned dev dependency: python3 -m pip install -r requirements-dev.txt\n' >&2
  exit 2
fi

exec python3 "$SCRIPT_DIR/validate.py" "$@"
