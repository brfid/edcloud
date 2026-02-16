"""Cleanup operations for Tailscale devices and orphaned EBS volumes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError

from edcloud import tailscale
from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    ROOT_VOLUME_ROLE,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
    tag_value,
)


def _is_state_volume(vol: Mapping[str, Any]) -> bool:
    """Return ``True`` if *vol* is tagged as a persistent state volume."""
    return tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) == STATE_VOLUME_ROLE


def _is_root_volume(vol: Mapping[str, Any]) -> bool:
    """Return ``True`` if *vol* is tagged as a root volume."""
    return tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) == ROOT_VOLUME_ROLE


def cleanup_tailscale_devices(interactive: bool = True) -> bool:
    """Clean up offline edcloud Tailscale devices.

    Args:
        interactive: If True, prompts user for confirmation

    Returns:
        True if cleanup completed (or skipped), False if user aborted
    """
    count, message = tailscale.cleanup_offline_edcloud_devices()
    if count == 0:
        if interactive:
            click.echo("✅ No offline edcloud devices found in Tailscale.")
        return True

    click.echo(message)
    click.echo()

    if not interactive:
        return True

    return click.confirm("Have you cleaned up the Tailscale devices? Continue?")


def cleanup_orphaned_volumes(mode: str = "interactive", allow_delete_state: bool = False) -> bool:
    """Clean up orphaned (available, unattached) managed EBS volumes.

    Args:
        mode: ``"interactive"`` (prompt user), ``"delete"`` (auto-delete),
            or ``"keep"`` (skip).
        allow_delete_state: When ``True``, state and unknown-role volumes
            are also eligible for deletion.

    Returns:
        ``True`` if cleanup completed, ``False`` if the user aborted.
    """
    ec2_client = boto3.client("ec2")
    resp = ec2_client.describe_volumes(
        Filters=[
            {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]},
            {"Name": "status", "Values": ["available"]},
        ]
    )
    orphaned_volumes = resp.get("Volumes", [])

    if not orphaned_volumes:
        click.echo("✅ No orphaned volumes found.")
        return True

    # Show volumes
    click.echo(f"Found {len(orphaned_volumes)} orphaned volume(s):")
    for vol in orphaned_volumes:
        vol_id = vol["VolumeId"]
        size = vol["Size"]
        vol_type = vol.get("VolumeType", "unknown")
        role = tag_value(vol.get("Tags", []), VOLUME_ROLE_TAG_KEY) or "unknown"
        click.echo(f"  - {vol_id} ({size}GB {vol_type}, role={role})")

    state_volumes = [v for v in orphaned_volumes if _is_state_volume(v)]
    unknown_role_volumes = [
        v
        for v in orphaned_volumes
        if (volume_role := tag_value(v.get("Tags", []), VOLUME_ROLE_TAG_KEY)) is None
        or volume_role not in {ROOT_VOLUME_ROLE, STATE_VOLUME_ROLE}
    ]
    deletable_volumes = [v for v in orphaned_volumes if _is_root_volume(v)]

    if state_volumes and not allow_delete_state:
        click.echo()
        click.echo("🔒 Protected state volume(s) detected; they will not be deleted by default.")
        for vol in state_volumes:
            click.echo(f"  - {vol['VolumeId']}")

    if unknown_role_volumes and not allow_delete_state:
        click.echo()
        click.echo("🔒 Untagged/unknown-role volume(s) detected; they are protected by default.")
        click.echo(
            "   (Use --allow-delete-state-volume only when you intentionally want full deletion.)"
        )
        for vol in unknown_role_volumes:
            click.echo(f"  - {vol['VolumeId']}")

    # Handle based on mode
    if mode == "keep":
        click.echo("Keeping volumes (will reuse if possible).")
        return True

    if mode == "delete":
        target = orphaned_volumes if allow_delete_state else deletable_volumes
        if not target:
            click.echo("No deletable orphaned volumes found.")
            return True
        return _delete_volumes(ec2_client, target)

    # Interactive mode
    click.echo()
    click.echo("Options:")
    if allow_delete_state:
        click.echo("  1. Delete all orphaned volumes (including state)")
    else:
        click.echo("  1. Delete orphaned non-state volumes (state protected)")
    click.echo("  2. Keep volumes (will reuse state volume if available)")
    click.echo("  3. Abort")
    choice = click.prompt("Choose option", type=int, default=2)

    if choice == 1:
        target = orphaned_volumes if allow_delete_state else deletable_volumes
        if not target:
            click.echo("No deletable orphaned volumes found.")
            return True
        return _delete_volumes(ec2_client, target)
    elif choice == 3:
        return False
    else:
        click.echo("Keeping volumes (will reuse if possible).")
        return True


def _delete_volumes(ec2_client: Any, volumes: Sequence[Mapping[str, Any]]) -> bool:
    """Delete a list of EBS volumes, logging each result."""
    for vol in volumes:
        vol_id = vol["VolumeId"]
        try:
            ec2_client.delete_volume(VolumeId=vol_id)
            click.echo(f"✅ Deleted {vol_id}")
        except (ClientError, BotoCoreError) as e:
            click.echo(f"❌ Failed to delete {vol_id}: {e}")
    return True


def run_cleanup_workflow(
    phase: str,
    skip_snapshot: bool = False,
    interactive: bool = True,
    allow_delete_state: bool = False,
) -> bool:
    """Run the full pre-provision or post-destroy cleanup workflow.

    Steps: Tailscale device cleanup → orphaned volume cleanup.

    Args:
        phase: Human-readable label (e.g. ``"pre-provision"``).
        skip_snapshot: Unused — reserved for future snapshot-before-cleanup.
        interactive: Prompt for confirmations when ``True``.
        allow_delete_state: Pass through to volume cleanup.

    Returns:
        ``True`` if the workflow completed, ``False`` if the user aborted.
    """
    click.echo("=" * 70)
    click.echo(f"{phase.replace('-', ' ').title()} Cleanup")
    click.echo("=" * 70)
    click.echo()

    # Cleanup Tailscale devices
    if not cleanup_tailscale_devices(interactive=interactive):
        click.echo("Aborted.")
        return False

    # Cleanup orphaned volumes
    mode = "interactive" if interactive else "keep"
    if not cleanup_orphaned_volumes(mode=mode, allow_delete_state=allow_delete_state):
        click.echo("Aborted.")
        return False

    click.echo()
    click.echo("=" * 70)
    click.echo()
    return True
