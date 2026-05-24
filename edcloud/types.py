"""Shared type definitions for edcloud API contracts.

Provides ``TypedDict`` shapes for the major dict-based return values
used across modules, making the implicit schemas explicit and
type-checker-visible.
"""

from __future__ import annotations

from typing import TypedDict


class VolumeInfo(TypedDict):
    """Single EBS volume summary returned by ``ec2.status()``."""

    volume_id: str
    size_gb: int
    type: str
    state: str


class CostEstimate(TypedDict):
    """Monthly cost estimate returned by ``ec2.status()``."""

    compute_monthly: float
    storage_monthly: float
    total_monthly: float
    note: str


class OrphanedResources(TypedDict):
    """Orphaned managed resources when no instance exists."""

    security_groups: list[str]
    volumes: list[str]


class InstanceStatus(TypedDict, total=False):
    """Return type of ``ec2.status()``.

    ``exists`` is always present. Other fields are present only when
    ``exists is True``.
    """

    exists: bool
    instance_id: str
    state: str
    instance_type: str
    public_ip: str | None
    launch_time: str | None
    volumes: list[VolumeInfo]
    orphaned_volumes: list[str]
    cost_estimate: CostEstimate
    orphaned_resources: OrphanedResources


class ProvisionResult(TypedDict):
    """Return type of ``ec2.provision()``."""

    instance_id: str
    security_group_id: str
    public_ip: str


class SnapshotInfo(TypedDict):
    """Single snapshot summary returned by ``snapshot.list_snapshots()``."""

    snapshot_id: str
    volume_id: str | None
    size_gb: int
    state: str
    progress: str
    start_time: str
    description: str
    name: str


class PruneResult(TypedDict):
    """Return type of ``snapshot.prune_snapshots()``."""

    keep_last: int
    dry_run: bool
    total: int
    delete_count: int
    to_delete: list[SnapshotInfo]


class RestoreDrillResult(TypedDict):
    """Return type of ``snapshot.run_restore_drill()``."""

    success: bool
    state_volume_id: str
    snapshot_id: str
    temporary_volume_id: str
    attached_to_instance: bool
    instance_id: str | None
    device_name: str | None
    temporary_volume_kept: bool


class ResizeResult(TypedDict, total=False):
    """Return type of ``ec2.resize()``."""

    instance_id: str
    root_volume_id: str
    root_volume_new_size_gb: int
    state_volume_id: str
    state_volume_new_size_gb: int
    instance_type_old: str
    instance_type_new: str
    public_ip: str


class BackupPolicyResult(TypedDict, total=False):
    """Return type of ``backup_policy.ensure_policy()``."""

    action: str
    policy_id: str
    state: str
    daily_keep: int
    weekly_keep: int
    monthly_keep: int
    exists: bool
    policy_name: str


class SnapshotCostReport(TypedDict):
    """Return type of ``ops_health.estimate_snapshot_monthly_cost()``."""

    completed_snapshot_count: int
    completed_snapshot_gb: float
    gb_month_rate: float
    estimated_monthly_usd: float
    soft_cap_usd: float
    over_soft_cap: bool


# Re-export for convenience — modules can ``from edcloud.types import Any``
# when they also need the escape hatch.
__all__: list[str] = [
    "BackupPolicyResult",
    "CostEstimate",
    "InstanceStatus",
    "OrphanedResources",
    "ProvisionResult",
    "PruneResult",
    "ResizeResult",
    "RestoreDrillResult",
    "SnapshotCostReport",
    "SnapshotInfo",
    "VolumeInfo",
]
