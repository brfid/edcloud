"""Shared lifecycle orchestration helpers for CLI commands.

These helpers keep command handlers focused on I/O while consolidating
reusable guardrail and snapshot/cleanup sequencing behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError


def require_confirmed_instance_id(
    info: dict[str, Any],
    confirm_instance_id: str | None,
    command_name: str,
) -> None:
    """Require explicit instance-id confirmation when an instance exists.

    Raises:
        RuntimeError: when confirmation is required but missing/mismatched.
    """
    if not info.get("exists"):
        return
    instance_id = str(info.get("instance_id", ""))
    if confirm_instance_id == instance_id:
        return
    raise RuntimeError(
        "Error: destructive action requires explicit instance ID confirmation.\n"
        f"  Re-run with: edc {command_name} --confirm-instance-id {instance_id}"
    )


def run_optional_auto_snapshot(
    *,
    skip_snapshot: bool,
    auto_snapshot: Callable[[], list[str]],
    echo: Callable[[str], None],
    echo_err: Callable[[str], None],
    confirm_continue: Callable[[str], bool],
    operation_label: str,
    continue_prompt: str = "Continue anyway?",
) -> list[str]:
    """Run optional auto snapshot with consistent UX and failure handling."""
    if skip_snapshot:
        return []

    echo(f"Creating automatic pre-{operation_label} snapshot...")
    try:
        snap_ids = auto_snapshot()
        if snap_ids:
            echo(f"✅ Created snapshot(s): {', '.join(snap_ids)}")
        else:
            echo("Info: no instance found to snapshot")
        return snap_ids
    except (RuntimeError, ClientError, BotoCoreError) as exc:
        echo_err(f"⚠️  Snapshot failed: {exc}")
        if not confirm_continue(continue_prompt):
            raise SystemExit(0) from None
        return []


def maybe_run_cleanup(
    *,
    skip_cleanup: bool,
    run_cleanup: Callable[[], None],
) -> None:
    """Invoke cleanup callback unless disabled."""
    if skip_cleanup:
        return
    run_cleanup()


def run_reprovision_flow(
    *,
    info: dict[str, Any],
    skip_snapshot: bool,
    auto_snapshot: Callable[[], list[str]],
    destroy_instance: Callable[[], None],
    cleanup_orphaned_volumes: Callable[[], object],
    provision_replacement: Callable[[], dict[str, Any]],
    echo: Callable[[str], None],
    echo_err: Callable[[str], None],
    confirm_continue: Callable[[str], bool],
) -> tuple[list[str], dict[str, Any]]:
    """Execute snapshot -> destroy -> cleanup -> provision reprovision flow."""
    snap_ids = run_optional_auto_snapshot(
        skip_snapshot=skip_snapshot,
        auto_snapshot=auto_snapshot,
        echo=echo,
        echo_err=echo_err,
        confirm_continue=confirm_continue,
        operation_label="reprovision",
        continue_prompt="Continue with reprovision anyway (no snapshot)?",
    )
    if not skip_snapshot:
        if snap_ids:
            echo(f"✅ Pre-reprovision snapshot(s) completed: {', '.join(snap_ids)}")
            echo("")
        else:
            echo("Info: no existing instance to snapshot.")
            echo("")

    if info.get("exists"):
        echo("Destroying current instance...")
        destroy_instance()
        cleanup_orphaned_volumes()
        echo("")
    else:
        echo("Info: no existing instance found — skipping destroy step.")
        echo("")

    result = provision_replacement()
    return snap_ids, result
