"""Small subprocess helper shared across CLI and sync workflows."""

from __future__ import annotations

import shlex
import subprocess  # nosec B404


def run_checked(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, capturing output, and raise ``RuntimeError`` on failure.

    The error message includes the shell-quoted command and the first
    non-empty of stderr/stdout so failures are self-describing.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"Command failed: {' '.join(shlex.quote(p) for p in cmd)}\n{detail}")
    return proc
