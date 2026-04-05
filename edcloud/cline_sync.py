"""Cline OAuth secret synchronization to remote hosts.

Handles backup-then-replace semantics for syncing Cline auth files
from a local operator machine to a remote edcloud instance over SSH.
"""

from __future__ import annotations

import json
import shlex
import subprocess  # nosec B404
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _run_checked(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and raise RuntimeError on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise RuntimeError(f"Command failed: {' '.join(shlex.quote(p) for p in cmd)}\n{detail}")
    return proc


def validate_source(
    source_secrets_path: Path,
    include_global_state: bool,
) -> tuple[dict[str, Any], bool]:
    """Validate source files and return parsed secrets + adjusted global_state flag.

    Raises:
        FileNotFoundError: If secrets file is missing.
        ValueError: If secrets file is missing expected keys.
    """
    if not source_secrets_path.exists():
        raise FileNotFoundError(f"Source secrets file not found: {source_secrets_path}")

    with source_secrets_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if "openai-codex-oauth-credentials" not in payload:
        raise ValueError("Missing expected key in secrets.json: openai-codex-oauth-credentials")

    source_global_state = source_secrets_path.with_name("globalState.json")
    if include_global_state and not source_global_state.exists():
        include_global_state = False

    return payload, include_global_state


def run_remote_diagnostics(
    remote: str,
    ssh_opts: tuple[str, ...],
    remote_config_dir: str,
) -> str:
    """Run remote diagnostics and return output."""
    script = (
        "set -euo pipefail; "
        'echo "remote diagnostics:"; '
        'echo "  user: $(whoami)"; '
        'echo "  home: $HOME"; '
        f'echo "  config_dir: $HOME/{remote_config_dir}"; '
        "if command -v cline >/dev/null 2>&1; then "
        'echo "  cline_path: $(command -v cline)"; '
        'echo "  cline_version: $(cline --version 2>/dev/null || echo unknown)"; '
        'else echo "  cline_path: missing"; fi'
    )
    result = _run_checked(["ssh", *ssh_opts, remote, script])
    return result.stdout.strip()


def sync_files(
    remote: str,
    ssh_opts: tuple[str, ...],
    source_secrets_path: Path,
    remote_config_dir: str,
    include_global_state: bool,
) -> None:
    """Backup remote files, upload new ones, and verify.

    Raises:
        RuntimeError: If any SSH/SCP step fails.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    remote_backup_dir = f"{remote_config_dir}/backups"
    remote_secrets = f"{remote_config_dir}/secrets.json"
    remote_global_state = f"{remote_config_dir}/globalState.json"
    gs_flag = 1 if include_global_state else 0

    # Backup
    backup_script = (
        "set -euo pipefail; "
        f'mkdir -p "$HOME/{remote_config_dir}" "$HOME/{remote_backup_dir}"; '
        f'if [[ -f "$HOME/{remote_secrets}" ]]; then cp "$HOME/{remote_secrets}" '
        f'"$HOME/{remote_backup_dir}/secrets.json.{ts}"; fi; '
        f'if [[ {gs_flag} -eq 1 && -f "$HOME/{remote_global_state}" ]]; '
        f'then cp "$HOME/{remote_global_state}" '
        f'"$HOME/{remote_backup_dir}/globalState.json.{ts}"; fi'
    )
    _run_checked(["ssh", *ssh_opts, remote, backup_script])

    # Upload
    _run_checked([
        "scp", *ssh_opts, str(source_secrets_path),
        f"{remote}:~/{remote_config_dir}/secrets.json.new",
    ])
    if include_global_state:
        source_global_state = source_secrets_path.with_name("globalState.json")
        _run_checked([
            "scp", *ssh_opts, str(source_global_state),
            f"{remote}:~/{remote_config_dir}/globalState.json.new",
        ])

    # Install
    install_script = (
        "set -euo pipefail; "
        f'mv "$HOME/{remote_config_dir}/secrets.json.new" "$HOME/{remote_secrets}"; '
        f'chmod 600 "$HOME/{remote_secrets}"; '
        f"if [[ {gs_flag} -eq 1 ]]; "
        f'then mv "$HOME/{remote_config_dir}/globalState.json.new" '
        f'"$HOME/{remote_global_state}"; chmod 600 "$HOME/{remote_global_state}"; fi'
    )
    _run_checked(["ssh", *ssh_opts, remote, install_script])

    # Verify
    verify_script = (
        "set -euo pipefail; "
        f'test -s "$HOME/{remote_secrets}"; '
        f"grep -q 'openai-codex-oauth-credentials' \"$HOME/{remote_secrets}\"; "
        f"stat -c 'remote file: %n owner=%U:%G mode=%a size=%s' \"$HOME/{remote_secrets}\"; "
        f"if [[ {gs_flag} -eq 1 ]]; then "
        f'test -s "$HOME/{remote_global_state}"; '
        f"stat -c 'remote file: %n owner=%U:%G mode=%a size=%s' \"$HOME/{remote_global_state}\"; "
        "fi"
    )
    _run_checked(["ssh", *ssh_opts, remote, verify_script])
