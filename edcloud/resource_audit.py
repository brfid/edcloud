"""Managed-resource audit for provisioning guardrails and cost visibility."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from edcloud.aws_clients import ec2_client as _ec2_client
from edcloud.config import (
    DEFAULT_HOURS_PER_DAY,
    DEFAULT_SNAPSHOT_KEEP_LAST,
    EBS_MONTHLY_RATE_PER_GB,
    EIP_UNATTACHED_MONTHLY_RATE,
    HOURLY_RATES,
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    ROOT_VOLUME_ROLE,
    SECURITY_GROUP_NAME,
    SNAPSHOT_MONTHLY_RATE_PER_GB,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
    has_managed_tag,
    managed_filter,
    tag_value,
)
from edcloud.discovery import list_instances


@dataclass(frozen=True)
class AuditFinding:
    """Single unanticipated-resource finding."""

    severity: str
    category: str
    resource_id: str
    message: str
    estimated_monthly_cost: float = 0.0


@dataclass(frozen=True)
class CostLineItem:
    """One cost line item in monthly USD."""

    name: str
    monthly_cost: float
    note: str = ""


@dataclass(frozen=True)
class CostReport:
    """Monthly cost summary."""

    baseline_monthly: float
    unanticipated_monthly: float
    total_monthly: float
    line_items: list[CostLineItem]
    note: str


@dataclass(frozen=True)
class AuditReport:
    """Full audit report for provisioning/status workflows."""

    findings: list[AuditFinding]
    cost: CostReport

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to plain dict for CLI/JSON output."""
        return asdict(self)


def _list_instances(ec2_client: Any, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list_instances(ec2_client, filters)


def _managed_addresses(ec2_client: Any) -> list[dict[str, Any]]:
    try:
        resp = ec2_client.describe_addresses(Filters=managed_filter())
        return list(resp.get("Addresses", []))
    except Exception:
        resp = ec2_client.describe_addresses()
        return [a for a in resp.get("Addresses", []) if has_managed_tag(a.get("Tags", []))]


def _monthly_compute(instance_type: str) -> float:
    hourly = HOURLY_RATES.get(instance_type, 0.0)
    return hourly * DEFAULT_HOURS_PER_DAY * 30


def audit_resources(
    *,
    ec2_client: Any | None = None,
    snapshot_keep_last: int = DEFAULT_SNAPSHOT_KEEP_LAST,
) -> AuditReport:
    """Audit managed/lookalike resources and estimate monthly cost impact."""
    ec2 = ec2_client or _ec2_client()

    findings: list[AuditFinding] = []

    managed_instances = _list_instances(ec2, managed_filter())
    named_instances = _list_instances(ec2, [{"Name": "tag:Name", "Values": [NAME_TAG]}])
    untagged_named_instances = [
        i for i in named_instances if not has_managed_tag(i.get("Tags", []))
    ]

    managed_sgs = ec2.describe_security_groups(Filters=managed_filter()).get("SecurityGroups", [])
    named_sgs = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SECURITY_GROUP_NAME]}]
    ).get("SecurityGroups", [])
    untagged_named_sgs = [g for g in named_sgs if not has_managed_tag(g.get("Tags", []))]

    managed_volumes = ec2.describe_volumes(Filters=managed_filter()).get("Volumes", [])
    orphaned_volumes = [v for v in managed_volumes if v.get("State") == "available"]

    state_volumes = [
        v
        for v in managed_volumes
        if tag_value(v.get("Tags", []), VOLUME_ROLE_TAG_KEY) == STATE_VOLUME_ROLE
    ]
    unknown_role_volumes = [
        v
        for v in managed_volumes
        if tag_value(v.get("Tags", []), VOLUME_ROLE_TAG_KEY)
        not in {ROOT_VOLUME_ROLE, STATE_VOLUME_ROLE}
    ]

    managed_snapshots = ec2.describe_snapshots(Filters=managed_filter(), OwnerIds=["self"]).get(
        "Snapshots", []
    )
    managed_snapshots.sort(key=lambda s: str(s.get("StartTime", "")), reverse=True)

    managed_addresses = _managed_addresses(ec2)
    unattached_eips = [a for a in managed_addresses if not a.get("AssociationId")]

    managed_enis = ec2.describe_network_interfaces(Filters=managed_filter()).get(
        "NetworkInterfaces", []
    )

    # Findings -----------------------------------------------------------------
    if len(managed_instances) > 1:
        for extra in managed_instances[1:]:
            iid = str(extra.get("InstanceId", "unknown"))
            instance_type = str(extra.get("InstanceType", "unknown"))
            findings.append(
                AuditFinding(
                    severity="warning",
                    category="duplicate-managed-instance",
                    resource_id=iid,
                    message=(
                        "Additional managed instance detected. edcloud expects a single managed "
                        "instance."
                    ),
                    estimated_monthly_cost=round(_monthly_compute(instance_type), 2),
                )
            )

    for inst in untagged_named_instances:
        iid = str(inst.get("InstanceId", "unknown"))
        findings.append(
            AuditFinding(
                severity="warning",
                category="untagged-lookalike-instance",
                resource_id=iid,
                message=(
                    f"Instance Name={NAME_TAG} is missing {MANAGER_TAG_KEY}={MANAGER_TAG_VALUE}."
                ),
            )
        )

    if len([g for g in managed_sgs if g.get("GroupName") == SECURITY_GROUP_NAME]) > 1:
        for sg in managed_sgs:
            if sg.get("GroupName") == SECURITY_GROUP_NAME:
                findings.append(
                    AuditFinding(
                        severity="warning",
                        category="duplicate-managed-security-group",
                        resource_id=str(sg.get("GroupId", "unknown")),
                        message="Multiple managed edcloud security groups detected.",
                    )
                )

    for sg in untagged_named_sgs:
        findings.append(
            AuditFinding(
                severity="warning",
                category="untagged-lookalike-security-group",
                resource_id=str(sg.get("GroupId", "unknown")),
                message=(
                    f"Security group {SECURITY_GROUP_NAME} is missing "
                    f"{MANAGER_TAG_KEY}={MANAGER_TAG_VALUE}."
                ),
            )
        )

    for volume in orphaned_volumes:
        vol_id = str(volume.get("VolumeId", "unknown"))
        size_gb = int(volume.get("Size", 0))
        findings.append(
            AuditFinding(
                severity="warning",
                category="orphaned-managed-volume",
                resource_id=vol_id,
                message="Managed unattached volume detected.",
                estimated_monthly_cost=round(size_gb * EBS_MONTHLY_RATE_PER_GB, 2),
            )
        )

    if len(state_volumes) > 1:
        for volume in state_volumes:
            findings.append(
                AuditFinding(
                    severity="warning",
                    category="duplicate-state-volume",
                    resource_id=str(volume.get("VolumeId", "unknown")),
                    message="Multiple managed state volumes detected.",
                )
            )

    for volume in unknown_role_volumes:
        findings.append(
            AuditFinding(
                severity="warning",
                category="unknown-volume-role",
                resource_id=str(volume.get("VolumeId", "unknown")),
                message=(
                    f"Managed volume missing or using unknown {VOLUME_ROLE_TAG_KEY}; "
                    "cleanup automation will treat it as protected."
                ),
            )
        )

    if len(managed_snapshots) > snapshot_keep_last:
        for snapshot in managed_snapshots[snapshot_keep_last:]:
            sid = str(snapshot.get("SnapshotId", "unknown"))
            size_gb = int(snapshot.get("VolumeSize", 0))
            findings.append(
                AuditFinding(
                    severity="warning",
                    category="snapshot-over-retention",
                    resource_id=sid,
                    message=(
                        "Managed snapshot exceeds "
                        f"keep-last-{snapshot_keep_last} retention target."
                    ),
                    estimated_monthly_cost=round(size_gb * SNAPSHOT_MONTHLY_RATE_PER_GB, 2),
                )
            )

    for eip in unattached_eips:
        allocation_id = str(eip.get("AllocationId", eip.get("PublicIp", "unknown")))
        findings.append(
            AuditFinding(
                severity="warning",
                category="unattached-elastic-ip",
                resource_id=allocation_id,
                message="Managed Elastic IP is unattached.",
                estimated_monthly_cost=round(EIP_UNATTACHED_MONTHLY_RATE, 2),
            )
        )

    for eni in managed_enis:
        eni_id = str(eni.get("NetworkInterfaceId", "unknown"))
        status = str(eni.get("Status", "unknown"))
        if status == "available":
            findings.append(
                AuditFinding(
                    severity="warning",
                    category="orphaned-network-interface",
                    resource_id=eni_id,
                    message="Managed network interface is unattached.",
                )
            )

    # Cost ---------------------------------------------------------------------
    primary_instance_compute = 0.0
    if managed_instances:
        primary_instance_compute = _monthly_compute(
            str(managed_instances[0].get("InstanceType", ""))
        )

    managed_storage_cost = (
        sum(int(v.get("Size", 0)) for v in managed_volumes) * EBS_MONTHLY_RATE_PER_GB
    )
    managed_snapshot_cost = (
        sum(int(s.get("VolumeSize", 0)) for s in managed_snapshots) * SNAPSHOT_MONTHLY_RATE_PER_GB
    )
    managed_unattached_eip_cost = len(unattached_eips) * EIP_UNATTACHED_MONTHLY_RATE

    unanticipated_monthly = round(
        sum(f.estimated_monthly_cost for f in findings),
        2,
    )
    baseline_monthly = round(
        primary_instance_compute
        + managed_storage_cost
        + managed_snapshot_cost
        + managed_unattached_eip_cost,
        2,
    )
    total_monthly = round(baseline_monthly + unanticipated_monthly, 2)

    line_items = [
        CostLineItem(
            name="primary-instance-compute",
            monthly_cost=round(primary_instance_compute, 2),
            note=f"Assumes {DEFAULT_HOURS_PER_DAY}hrs/day runtime",
        ),
        CostLineItem(
            name="managed-ebs-volumes",
            monthly_cost=round(managed_storage_cost, 2),
            note=f"{len(managed_volumes)} managed volume(s)",
        ),
        CostLineItem(
            name="managed-snapshots",
            monthly_cost=round(managed_snapshot_cost, 2),
            note=f"{len(managed_snapshots)} managed snapshot(s)",
        ),
        CostLineItem(
            name="managed-unattached-eips",
            monthly_cost=round(managed_unattached_eip_cost, 2),
            note=f"{len(unattached_eips)} unattached EIP(s)",
        ),
    ]
    cost = CostReport(
        baseline_monthly=baseline_monthly,
        unanticipated_monthly=unanticipated_monthly,
        total_monthly=total_monthly,
        line_items=line_items,
        note="Static us-east-1 approximations; verify against AWS pricing for your region.",
    )

    return AuditReport(findings=findings, cost=cost)
