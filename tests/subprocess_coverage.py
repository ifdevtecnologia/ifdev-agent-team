"""Coverage of the scripts that the tests run in subprocesses with a minimal environment."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

# Set by `coverage run` with `patch = subprocess` (.coveragerc): a subprocess that inherits it records coverage.
CONFIG_VARIABLE = "COVERAGE_PROCESS_CONFIG"


def coverage_env(scripts_copy: Path | None = None) -> dict[str, str]:
    """Returns the environment entries a subprocess needs to record coverage.

    Empty unless the tests run under coverage, so a plain test run passes exactly the same environment.
    `scripts_copy` is a fixture's copy of the working tree's scripts/: it is measured too, and the
    `[paths]` section of .coveragerc maps it back to scripts/ when the data is combined.
    """
    config = os.environ.get(CONFIG_VARIABLE)
    if config is None:
        return {}
    if scripts_copy is not None:
        # coverage.py serializes its configuration as base64-encoded JSON, with absolute source paths.
        data = json.loads(base64.b64decode(config))
        data["source"] = [*(data.get("source") or []), str(scripts_copy)]
        config = base64.b64encode(json.dumps(data).encode()).decode()
    return {CONFIG_VARIABLE: config}
