"""Cleanup operations for Tailscale devices and orphaned EBS volumes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from edcloud import tailscale
from edcloud.aws_clients import ec2_client as _ec2_client
from edcloud.config import (
    ROOT_VOLUME_ROLE,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
    tag_value,
)
from edcloud.resource_queries import list_managed_volumes

# Default I/O callbacks — callers can override these to decouple from any
# particular UI framework (click, logging, etc.).
_ECHO: Callable[[str], None] = print


def _CONFIRM(msg: str) -> bool:  # noqa: N802
    return input(f"{msg} [y/N] ").strip().lower() == "y"


def _PROMPT_INT(msg: str, default: int) -> int:  # noqa: N802
    raw = input(f"{msg} [{default}]: ")
    return int(raw) if raw else default


def _is_state_volume(vol: Mapping[str, Any]) -> bool:
    """Return ``True`` if *vol* is tagged as a persistent state volume."""
    return tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) == STATE_VOLUME_ROLE


def _is_root_volume(vol: Mapping[str, Any]) -> bool:
    """Return ``True`` if *vol* is tagged as a root volume."""
    return tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) == ROOT_VOLUME_ROLE


def cleanup_tailscale_devices(
    interactive: bool = True,
    *,
    echo: Callable[[str], None] = _ECHO,
    confirm: Callable[[str], bool] = _CONFIRM,
) -> bool:
    """Clean up offline edcloud Tailscale devices.

    Args:
        interactive: If True, prompts user for confirmation.
        echo: Callable for output messages.
        confirm: Callable for yes/no confirmation prompts.

    Returns:
        True if cleanup completed (or skipped), False if user aborted.
    """
    count, message = tailscale.cleanup_offline_edcloud_devices()
    if count == 0:
        if interactive:
            echo("No offline edcloud devices found in Tailscale.")
        return True

    echo(message)
    echo("")

    if not interactive:
        return True

    return confirm("Have you cleaned up the Tailscale devices? Continue?")


def cleanup_orphaned_volumes(
    mode: str = "interactive",
    allow_delete_state: bool = False,
    *,
    echo: Callable[[str], None] = _ECHO,
    prompt_int: Callable[[str, int], int] = _PROMPT_INT,
) -> bool:
    """Clean up orphaned (available, unattached) managed EBS volumes.

    Args:
        mode: ``"interactive"`` (prompt user), ``"delete"`` (auto-delete),
            or ``"keep"`` (skip).
        allow_delete_state: When ``True``, state and unknown-role volumes
            are also eligible for deletion.
        echo: Callable for output messages.
        prompt_int: Callable for integer prompts ``(message, default) -> int``.

    Returns:
        ``True`` if cleanup completed, ``False`` if the user aborted.
    """
    ec2_client = _ec2_client()
    orphaned_volumes = list_managed_volumes(ec2_client, status="available")

    if not orphaned_volumes:
        echo("No orphaned volumes found.")
        return True

    # Show volumes
    echo(f"Found {len(orphaned_volumes)} orphaned volume(s):")
    for vol in orphaned_volumes:
        vol_id = vol["VolumeId"]
        size = vol["Size"]
        vol_type = vol.get("VolumeType", "unknown")
        role = tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) or "unknown"
        echo(f"  - {vol_id} ({size}GB {vol_type}, role={role})")

    state_volumes = [v for v in orphaned_volumes if _is_state_volume(v)]
    unknown_role_volumes = [
        v
        for v in orphaned_volumes
        if (volume_role := tag_value(v.get("Tags", []), VOLUME_ROLE_TAG_KEY)) is None
        or volume_role not in {ROOT_VOLUME_ROLE, STATE_VOLUME_ROLE}
    ]
    deletable_volumes = [v for v in orphaned_volumes if _is_root_volume(v)]

    if state_volumes and not allow_delete_state:
        echo("")
        echo("Protected state volume(s) detected; they will not be deleted by default.")
        for vol in state_volumes:
            echo(f"  - {vol['VolumeId']}")

    if unknown_role_volumes and not allow_delete_state:
        echo("")
        echo("Untagged/unknown-role volume(s) detected; they are protected by default.")
        echo(
            "   (Use --allow-delete-state-volume only when you intentionally want full deletion.)"
        )
        for vol in unknown_role_volumes:
            echo(f"  - {vol['VolumeId']}")

    # Handle based on mode
    if mode == "keep":
        echo("Keeping volumes (will reuse if possible).")
        return True

    if mode == "delete":
        target = orphaned_volumes if allow_delete_state else deletable_volumes
        if not target:
            echo("No deletable orphaned volumes found.")
            return True
        return _delete_volumes(ec2_client, target, echo=echo)

    # Interactive mode
    echo("")
    echo("Options:")
    if allow_delete_state:
        echo("  1. Delete all orphaned volumes (including state)")
    else:
        echo("  1. Delete orphaned non-state volumes (state protected)")
    echo("  2. Keep volumes (will reuse state volume if available)")
    echo("  3. Abort")
    choice = prompt_int("Choose option", 2)

    if choice == 1:
        target = orphaned_volumes if allow_delete_state else deletable_volumes
        if not target:
            echo("No deletable orphaned volumes found.")
            return True
        return _delete_volumes(ec2_client, target, echo=echo)
    elif choice == 3:
        return False
    else:
        echo("Keeping volumes (will reuse if possible).")
        return True


def _delete_volumes(
    ec2_client: Any,
    volumes: Sequence[Mapping[str, Any]],
    *,
    echo: Callable[[str], None] = _ECHO,
) -> bool:
    """Delete a list of EBS volumes, logging each result."""
    for vol in volumes:
        vol_id = vol["VolumeId"]
        try:
            ec2_client.delete_volume(VolumeId=vol_id)
            echo(f"Deleted {vol_id}")
        except (ClientError, BotoCoreError) as e:
            echo(f"Failed to delete {vol_id}: {e}")
    return True


def run_cleanup_workflow(
    phase: str,
    skip_snapshot: bool = False,
    interactive: bool = True,
    allow_delete_state: bool = False,
    *,
    echo: Callable[[str], None] = _ECHO,
    confirm: Callable[[str], bool] = _CONFIRM,
    prompt_int: Callable[[str, int], int] = _PROMPT_INT,
) -> bool:
    """Run the full pre-provision or post-destroy cleanup workflow.

    Steps: Tailscale device cleanup -> orphaned volume cleanup.

    Args:
        phase: Human-readable label (e.g. ``"pre-provision"``).
        skip_snapshot: Unused -- reserved for future snapshot-before-cleanup.
        interactive: Prompt for confirmations when ``True``.
        allow_delete_state: Pass through to volume cleanup.
        echo: Callable for output messages.
        confirm: Callable for yes/no confirmation prompts.
        prompt_int: Callable for integer prompts.

    Returns:
        ``True`` if the workflow completed, ``False`` if the user aborted.
    """
    echo("=" * 70)
    echo(f"{phase.replace('-', ' ').title()} Cleanup")
    echo("=" * 70)
    echo("")

    # Cleanup Tailscale devices
    if not cleanup_tailscale_devices(interactive=interactive, echo=echo, confirm=confirm):
        echo("Aborted.")
        return False

    # Cleanup orphaned volumes
    mode = "interactive" if interactive else "keep"
    if not cleanup_orphaned_volumes(
        mode=mode, allow_delete_state=allow_delete_state, echo=echo, prompt_int=prompt_int
    ):
        echo("Aborted.")
        return False

    echo("")
    echo("=" * 70)
    echo("")
    return True
