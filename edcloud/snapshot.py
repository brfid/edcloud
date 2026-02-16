"""EBS snapshot management: create, list, and prune edcloud snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    get_volume_ids,
    managed_filter,
)
from edcloud.ec2 import _ec2_client, _find_instance

WEEKLY_PREFIX = "weekly-snapshot"
MONTHLY_PREFIX = "monthly-snapshot"


def auto_snapshot_before_destroy() -> list[str]:
    """Snapshot all volumes of the current instance before a destructive op.

    Returns:
        List of snapshot IDs created, or empty list if no instance exists.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        # No instance to snapshot
        return []

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    description = f"auto-pre-destroy-{ts}"
    return create_snapshot(description)


def create_snapshot(description: str | None = None) -> list[str]:
    """Snapshot every EBS volume attached to the edcloud instance.

    Args:
        description: Optional description; auto-generated if omitted.

    Returns:
        List of created snapshot IDs.

    Raises:
        RuntimeError: If no instance or no volumes are found.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        raise RuntimeError("No edcloud instance found. Nothing to snapshot.")

    iid = inst["InstanceId"]
    vol_ids = get_volume_ids(inst)
    if not vol_ids:
        raise RuntimeError(f"No EBS volumes found on instance {iid}.")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    desc = description or f"edcloud snapshot {ts}"

    snapshot_ids = []
    for vid in vol_ids:
        print(f"Creating snapshot of {vid}...")
        resp = ec2.create_snapshot(
            VolumeId=vid,
            Description=desc,
            TagSpecifications=[
                {
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
                        {"Key": "Name", "Value": f"{NAME_TAG}-snap-{ts}"},
                        {"Key": "edcloud:source-volume", "Value": vid},
                        {"Key": "edcloud:source-instance", "Value": iid},
                    ],
                },
            ],
        )
        sid = resp["SnapshotId"]
        snapshot_ids.append(sid)
        print(f"  Snapshot started: {sid}")

    print()
    print("Snapshots are creating in the background. Use 'edc snapshot --list' to check.")
    return snapshot_ids


def list_snapshots() -> list[dict[str, Any]]:
    """List all edcloud-managed snapshots, most recent first.

    Returns:
        Dicts with keys: ``snapshot_id``, ``volume_id``, ``size_gb``,
        ``state``, ``progress``, ``start_time``, ``description``, ``name``.
    """
    ec2 = _ec2_client()
    resp = ec2.describe_snapshots(
        Filters=managed_filter(),
        OwnerIds=["self"],
    )
    snapshots = []
    for s in resp.get("Snapshots", []):
        tags = {t["Key"]: t["Value"] for t in s.get("Tags", [])}
        snapshots.append(
            {
                "snapshot_id": s["SnapshotId"],
                "volume_id": s.get("VolumeId"),
                "size_gb": s["VolumeSize"],
                "state": s["State"],
                "progress": s.get("Progress", ""),
                "start_time": str(s.get("StartTime", "")),
                "description": s.get("Description", ""),
                "name": tags.get("Name", ""),
            }
        )
    # Most recent first
    snapshots.sort(key=lambda x: x["start_time"], reverse=True)
    return snapshots


def prune_snapshots(
    keep_weekly: int = 8,
    keep_monthly: int = 3,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prune old periodic snapshots per a retention policy.

    Retention:
        * Keep newest *keep_weekly* snapshots prefixed ``weekly-snapshot``.
        * Keep newest *keep_monthly* snapshots prefixed ``monthly-snapshot``.
        * Snapshots with other descriptions (e.g. ``pre-change``) are never pruned.

    Args:
        keep_weekly: Number of weekly snapshots to retain.
        keep_monthly: Number of monthly snapshots to retain.
        dry_run: Preview deletions without applying when ``True``.

    Returns:
        Summary dict with counts and the list of snapshots to delete.

    Raises:
        ValueError: If retention counts are negative.
    """
    if keep_weekly < 0 or keep_monthly < 0:
        raise ValueError("Retention counts must be >= 0.")

    ec2 = _ec2_client()
    snapshots = list_snapshots()

    weekly = [s for s in snapshots if s["description"].startswith(WEEKLY_PREFIX)]
    monthly = [s for s in snapshots if s["description"].startswith(MONTHLY_PREFIX)]

    to_delete = [*weekly[keep_weekly:], *monthly[keep_monthly:]]

    if not dry_run:
        for snap in to_delete:
            ec2.delete_snapshot(SnapshotId=snap["snapshot_id"])

    return {
        "keep_weekly": keep_weekly,
        "keep_monthly": keep_monthly,
        "dry_run": dry_run,
        "weekly_total": len(weekly),
        "monthly_total": len(monthly),
        "delete_count": len(to_delete),
        "to_delete": to_delete,
    }
