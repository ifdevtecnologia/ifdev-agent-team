"""Builds and runs the `docker run` commands of the agent and evaluator containers.

Every container mounts exactly one host directory, at the same absolute path, runs as the host's
non-root user without capabilities, and receives only the environment it is given.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DOCKER = "docker"
# Resource limits of every container, so a runaway process cannot freeze the owner's machine.
PIDS_LIMIT = "1024"
MEMORY_LIMIT = "8g"
CPU_LIMIT = "4"


class ContainerError(RuntimeError):
    """The container could not be configured safely."""


class ContainerNotStopped(ContainerError):
    """A timed-out container could not be confirmed killed: the run must not be evaluated or retried."""


def host_user() -> str:
    """`uid:gid` of the host user, refused when it is root (the containers never run as root)."""
    uid, gid = os.getuid(), os.getgid()

    if uid == 0:
        raise ContainerError("refusing to run containers as root; run the harness as a regular user")

    return f"{uid}:{gid}"


def bind_mount(path: Path) -> str:
    text = str(path)

    if "," in text or "=" in text or "\n" in text:
        raise ContainerError(f"unsupported character in mounted path: {text!r}")

    return f"type=bind,source={text},target={text}"


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    mount: Path
    workdir: Path
    network: str
    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    passthrough_env: tuple[str, ...] = ()
    name: str | None = None
    interactive: bool = False

    def argv(self) -> list[str]:
        argv = [DOCKER, "run", "--rm"]

        if self.interactive:
            argv.append("-i")

        if self.name:
            argv += ["--name", self.name]

        argv += [
            "--user", host_user(),
            "--network", self.network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", PIDS_LIMIT,
            "--memory", MEMORY_LIMIT,
            "--memory-swap", MEMORY_LIMIT,
            "--cpus", CPU_LIMIT,
            "--mount", bind_mount(self.mount),
            "-w", str(self.workdir),
        ]

        for key, value in self.env:
            argv += ["-e", f"{key}={value}"]

        # Passed by name only: Docker reads the value from its own environment, so it never
        # appears on the command line.
        for key in self.passthrough_env:
            argv += ["-e", key]

        return [*argv, self.image, *self.command]


def container_gone(name: str, attempts: int = 10, delay_s: float = 1.0) -> bool:
    """True once `docker inspect` no longer finds the container (it was killed and removed)."""
    for attempt in range(attempts):
        result = subprocess.run([DOCKER, "inspect", "--type", "container", name], capture_output=True, check=False, timeout=60)

        if result.returncode != 0:
            return True

        if attempt + 1 < attempts:
            time.sleep(delay_s)

    return False


def run_container(spec: ContainerSpec, timeout_s: float, stdin=None, stdout=None, stderr=None) -> tuple[int, bool]:
    """Runs the container and returns (exit code, timed out). On a host-side timeout a named
    container is killed, and ContainerError is raised unless it is confirmed gone, so nothing is
    evaluated while the agent could still be running."""
    try:
        completed = subprocess.run(spec.argv(), stdin=stdin, stdout=stdout, stderr=stderr, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as error:
        if spec.name:
            subprocess.run([DOCKER, "kill", spec.name], capture_output=True, check=False, timeout=60)

            if not container_gone(spec.name):
                raise ContainerNotStopped(f"container {spec.name} is still running after the time limit") from error

        return 124, True

    return completed.returncode, False
