"""EBS snapshot management: create, list, and prune edcloud snapshots."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    get_volume_ids,
    managed_filter,
)
from edcloud.ec2 import find_instance, get_ec2_client

WEEKLY_PREFIX = "weekly-snapshot"
MONTHLY_PREFIX = "monthly-snapshot"
PRECHANGE_SNAPSHOT_PREFIX = "pre-change"
_SNAPSHOT_WAIT_TIMEOUT_S = 600
_SNAPSHOT_WAIT_POLL_S = 15


def _snapshot_start_time(start_time: str) -> datetime | None:
    """Parse an ISO-format snapshot timestamp into a UTC-aware datetime.

    Tolerates trailing ``Z`` and naive timestamps (assumed UTC).
    Returns ``None`` on any parse failure.
    """
    raw = start_time.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def wait_for_snapshot_completion(
    snapshot_ids: list[str],
    timeout_s: int = _SNAPSHOT_WAIT_TIMEOUT_S,
    poll_interval_s: int = _SNAPSHOT_WAIT_POLL_S,
) -> None:
    """Poll until all given snapshots reach ``completed`` state.

    Args:
        snapshot_ids: Snapshot IDs to wait on.
        timeout_s: Maximum seconds to wait before raising.
        poll_interval_s: Seconds between poll attempts.

    Raises:
        TimeoutError: If snapshots do not complete within *timeout_s*.
        RuntimeError: If any snapshot enters the ``error`` state.
    """
    if not snapshot_ids:
        return
    ec2 = get_ec2_client()
    deadline = time.monotonic() + timeout_s
    pending = set(snapshot_ids)
    while pending:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Timed out after {timeout_s}s waiting for snapshots to complete: "
                f"{', '.join(sorted(pending))}"
            )
        resp = ec2.describe_snapshots(SnapshotIds=list(pending))
        for s in resp.get("Snapshots", []):
            sid = s["SnapshotId"]
            state = s["State"]
            if state == "completed":
                pending.discard(sid)
            elif state == "error":
                raise RuntimeError(
                    f"Snapshot {sid} entered error state: {s.get('StateMessage', '')}"
                )
        if pending:
            time.sleep(poll_interval_s)


def find_recent_prechange_snapshot(max_age_minutes: int) -> dict[str, object] | None:
    """Return the freshest completed pre-change snapshot within *max_age_minutes*.

    Args:
        max_age_minutes: Maximum age of the snapshot in minutes.

    Returns:
        Snapshot info dict, or ``None`` if nothing qualifies.
    """
    now = datetime.now(timezone.utc)
    freshest: tuple[datetime, dict[str, object]] | None = None
    for snap_info in list_snapshots():
        description = str(snap_info.get("description", "")).strip().lower()
        if not description.startswith(PRECHANGE_SNAPSHOT_PREFIX):
            continue
        if snap_info.get("state") != "completed":
            continue
        parsed = _snapshot_start_time(str(snap_info.get("start_time", "")))
        if not parsed:
            continue
        age_minutes = (now - parsed).total_seconds() / 60
        if age_minutes < 0 or age_minutes > max_age_minutes:
            continue
        if freshest is None or parsed > freshest[0]:
            freshest = (parsed, snap_info)
    return freshest[1] if freshest else None


def auto_snapshot_before_destroy() -> list[str]:
    """Snapshot all volumes of the current instance before a destructive op.

    Waits for all snapshots to reach ``completed`` state before returning,
    so the caller can safely proceed with destructive operations.

    Returns:
        List of snapshot IDs created, or empty list if no instance exists.
    """
    ec2 = get_ec2_client()
    inst = find_instance(ec2)
    if not inst:
        # No instance to snapshot
        return []

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    description = f"auto-pre-destroy-{ts}"
    snap_ids = create_snapshot(description)
    if snap_ids:
        print("Waiting for snapshot(s) to complete before proceeding...")
        wait_for_snapshot_completion(snap_ids)
        print("Snapshot(s) completed.")
    return snap_ids


def create_snapshot(description: str | None = None) -> list[str]:
    """Snapshot every EBS volume attached to the edcloud instance.

    Args:
        description: Optional description; auto-generated if omitted.

    Returns:
        List of created snapshot IDs.

    Raises:
        RuntimeError: If no instance or no volumes are found.
    """
    ec2 = get_ec2_client()
    inst = find_instance(ec2)
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
    ec2 = get_ec2_client()
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

    ec2 = get_ec2_client()
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
