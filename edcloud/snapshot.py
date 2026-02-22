"""EBS snapshot management: create, list, and prune edcloud snapshots."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
    get_volume_ids,
    managed_filter,
)
from edcloud.ec2 import find_instance, get_ec2_client

log = logging.getLogger(__name__)

PRECHANGE_SNAPSHOT_PREFIX = "pre-change"
_SNAPSHOT_WAIT_TIMEOUT_S = 600
_SNAPSHOT_WAIT_POLL_S = 15
_RESTORE_DRILL_TEMP_NAME = "restore-drill-temp"


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


def _find_single_state_volume(ec2: Any) -> dict[str, Any]:
    """Return the single managed state volume.

    Raises:
        RuntimeError: If zero or multiple managed state volumes are found.
    """
    resp = ec2.describe_volumes(
        Filters=[
            {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]},
            {"Name": f"tag:{VOLUME_ROLE_TAG_KEY}", "Values": [STATE_VOLUME_ROLE]},
        ]
    )
    volumes = list(resp.get("Volumes", []))
    if not volumes:
        raise RuntimeError("No managed state volume found for restore drill.")
    if len(volumes) > 1:
        ids = ", ".join(v["VolumeId"] for v in volumes)
        raise RuntimeError(
            f"Restore drill requires a single managed state volume, but found multiple: {ids}"
        )
    return volumes[0]


def _latest_completed_snapshot_for_volume(ec2: Any, volume_id: str) -> dict[str, Any]:
    """Return the most recent completed snapshot for *volume_id*."""
    resp = ec2.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "volume-id", "Values": [volume_id]},
            {"Name": "status", "Values": ["completed"]},
        ],
    )
    snapshots = list(resp.get("Snapshots", []))
    if not snapshots:
        raise RuntimeError(f"No completed snapshots found for state volume {volume_id}.")
    snapshots.sort(key=lambda s: s.get("StartTime", ""), reverse=True)
    return snapshots[0]


def _validated_snapshot_for_volume(ec2: Any, snapshot_id: str, volume_id: str) -> dict[str, Any]:
    """Validate and return a specific snapshot for *volume_id*."""
    resp = ec2.describe_snapshots(SnapshotIds=[snapshot_id], OwnerIds=["self"])
    snapshots = list(resp.get("Snapshots", []))
    if not snapshots:
        raise RuntimeError(f"Snapshot not found: {snapshot_id}")
    snap = snapshots[0]
    snap_volume_id = str(snap.get("VolumeId", ""))
    if snap_volume_id != volume_id:
        raise RuntimeError(
            f"Snapshot {snapshot_id} is for volume {snap_volume_id}, expected {volume_id}."
        )
    if snap.get("State") != "completed":
        raise RuntimeError(
            f"Snapshot {snapshot_id} is in state {snap.get('State')}; must be completed."
        )
    return snap


def run_restore_drill(
    snapshot_id: str | None = None,
    instance_id: str | None = None,
    device_name: str = "/dev/sdg",
    keep_temporary_volume: bool = False,
) -> dict[str, Any]:
    """Run a non-destructive EBS restore drill.

    Creates a temporary volume from a state-volume snapshot. Optionally attaches
    it to a running instance, then detaches/deletes it unless explicitly kept.
    """
    ec2 = get_ec2_client()
    state_volume = _find_single_state_volume(ec2)
    state_volume_id = str(state_volume["VolumeId"])
    az = str(state_volume["AvailabilityZone"])

    snapshot = (
        _validated_snapshot_for_volume(ec2, snapshot_id, state_volume_id)
        if snapshot_id
        else _latest_completed_snapshot_for_volume(ec2, state_volume_id)
    )
    selected_snapshot_id = str(snapshot["SnapshotId"])

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    created = ec2.create_volume(
        SnapshotId=selected_snapshot_id,
        AvailabilityZone=az,
        VolumeType="gp3",
        TagSpecifications=[
            {
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Name", "Value": f"{_RESTORE_DRILL_TEMP_NAME}-{ts}"},
                    {"Key": "purpose", "Value": "restore-drill"},
                    {"Key": MANAGER_TAG_KEY, "Value": "false"},
                    {"Key": "edcloud:restore-drill", "Value": "true"},
                    {
                        "Key": "edcloud:restore-drill-source-snapshot",
                        "Value": selected_snapshot_id,
                    },
                    {"Key": "edcloud:restore-drill-source-volume", "Value": state_volume_id},
                ],
            }
        ],
    )
    temp_volume_id = str(created["VolumeId"])

    attached = False
    cleanup_errors: list[str] = []
    try:
        ec2.get_waiter("volume_available").wait(VolumeIds=[temp_volume_id])
        if instance_id:
            ec2.attach_volume(VolumeId=temp_volume_id, InstanceId=instance_id, Device=device_name)
            ec2.get_waiter("volume_in_use").wait(VolumeIds=[temp_volume_id])
            attached = True
    finally:
        if not keep_temporary_volume:
            if instance_id and attached:
                try:
                    ec2.detach_volume(VolumeId=temp_volume_id)
                    ec2.get_waiter("volume_available").wait(VolumeIds=[temp_volume_id])
                except Exception as exc:  # best-effort cleanup
                    cleanup_errors.append(f"detach failed for {temp_volume_id}: {exc}")
            try:
                ec2.delete_volume(VolumeId=temp_volume_id)
            except Exception as exc:  # best-effort cleanup
                cleanup_errors.append(f"delete failed for {temp_volume_id}: {exc}")

    if cleanup_errors:
        detail = " | ".join(cleanup_errors)
        raise RuntimeError(
            "Restore drill encountered cleanup errors. "
            f"Temporary volume {temp_volume_id} may need manual cleanup: {detail}"
        )

    return {
        "success": True,
        "state_volume_id": state_volume_id,
        "snapshot_id": selected_snapshot_id,
        "temporary_volume_id": temp_volume_id,
        "attached_to_instance": bool(instance_id),
        "instance_id": instance_id,
        "device_name": device_name if instance_id else None,
        "temporary_volume_kept": keep_temporary_volume,
    }


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
        log.info("Waiting for snapshot(s) to complete before proceeding...")
        wait_for_snapshot_completion(snap_ids)
        log.info("Snapshot(s) completed.")
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
    all_vol_ids = get_volume_ids(inst)
    if not all_vol_ids:
        raise RuntimeError(f"No EBS volumes found on instance {iid}.")

    # Only snapshot state volumes — root is disposable and rebuilt by cloud-init.
    vol_resp = ec2.describe_volumes(VolumeIds=all_vol_ids)
    vol_ids = [
        v["VolumeId"]
        for v in vol_resp.get("Volumes", [])
        if {t["Key"]: t["Value"] for t in v.get("Tags", [])}.get(VOLUME_ROLE_TAG_KEY)
        == STATE_VOLUME_ROLE
    ]
    if not vol_ids:
        log.warning("No state-tagged volumes found; snapshotting all volumes as fallback.")
        vol_ids = all_vol_ids

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    desc = description or f"edcloud snapshot {ts}"

    snapshot_ids = []
    for vid in vol_ids:
        log.info("Creating snapshot of %s...", vid)
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
        log.info("  Snapshot started: %s", sid)

    log.info("Snapshots are creating in the background. Use 'edc snapshot --list' to check.")
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
    keep_last: int = 3,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete all but the most recent *keep_last* snapshots.

    Snapshots are ordered newest-first; the oldest beyond *keep_last* are
    removed.  All snapshots are eligible regardless of description prefix.

    Args:
        keep_last: Number of most-recent snapshots to retain.
        dry_run: Preview deletions without applying when ``True``.

    Returns:
        Summary dict with counts and the list of snapshots to delete.

    Raises:
        ValueError: If *keep_last* is negative.
    """
    if keep_last < 0:
        raise ValueError("keep_last must be >= 0.")

    ec2 = get_ec2_client()
    snapshots = list_snapshots()  # newest first

    to_delete = snapshots[keep_last:]

    if not dry_run:
        for snap in to_delete:
            ec2.delete_snapshot(SnapshotId=snap["snapshot_id"])

    return {
        "keep_last": keep_last,
        "dry_run": dry_run,
        "total": len(snapshots),
        "delete_count": len(to_delete),
        "to_delete": to_delete,
    }
