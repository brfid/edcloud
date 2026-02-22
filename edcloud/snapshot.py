"""EBS snapshot management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE, NAME_TAG
from edcloud.ec2 import _find_instance, _managed_filter


def _ec2_client() -> Any:
    return boto3.client("ec2")


def _get_volume_ids(instance: dict[str, Any]) -> list[str]:
    """Extract EBS volume IDs from an instance description."""
    vol_ids = []
    for bdm in instance.get("BlockDeviceMappings", []):
        vid = bdm.get("Ebs", {}).get("VolumeId")
        if vid:
            vol_ids.append(vid)
    return vol_ids


def create_snapshot(description: str | None = None) -> list[str]:
    """Snapshot all EBS volumes attached to the edcloud instance.

    Returns list of snapshot IDs.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        raise RuntimeError("No edcloud instance found. Nothing to snapshot.")

    iid = inst["InstanceId"]
    vol_ids = _get_volume_ids(inst)
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
    print("Snapshots are creating in the background. Use 'edcloud snapshot --list' to check.")
    return snapshot_ids


def list_snapshots() -> list[dict[str, Any]]:
    """List all edcloud-managed snapshots."""
    ec2 = _ec2_client()
    resp = ec2.describe_snapshots(
        Filters=_managed_filter(),
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
