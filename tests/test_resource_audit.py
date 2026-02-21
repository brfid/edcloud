"""Tests for managed resource audit and static cost estimation."""

from unittest.mock import MagicMock

from edcloud.resource_audit import audit_resources


def _instance(instance_id: str, instance_type: str = "t3a.small", *, managed: bool = True) -> dict:
    tags = [{"Key": "Name", "Value": "edcloud"}]
    if managed:
        tags.append({"Key": "edcloud:managed", "Value": "true"})
    return {
        "InstanceId": instance_id,
        "InstanceType": instance_type,
        "State": {"Name": "running"},
        "Tags": tags,
    }


def test_audit_resources_clean_environment() -> None:
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [
        {"Reservations": [{"Instances": [_instance("i-main")]}]},
        {"Reservations": [{"Instances": [_instance("i-main")]}]},
    ]
    ec2.describe_security_groups.side_effect = [
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-main",
                    "GroupName": "edcloud-sg",
                    "Tags": [{"Key": "edcloud:managed", "Value": "true"}],
                }
            ]
        },
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-main",
                    "GroupName": "edcloud-sg",
                    "Tags": [{"Key": "edcloud:managed", "Value": "true"}],
                }
            ]
        },
    ]
    ec2.describe_volumes.return_value = {
        "Volumes": [
            {
                "VolumeId": "vol-root",
                "Size": 16,
                "State": "in-use",
                "Tags": [
                    {"Key": "edcloud:managed", "Value": "true"},
                    {"Key": "edcloud:volume-role", "Value": "root"},
                ],
            },
            {
                "VolumeId": "vol-state",
                "Size": 20,
                "State": "in-use",
                "Tags": [
                    {"Key": "edcloud:managed", "Value": "true"},
                    {"Key": "edcloud:volume-role", "Value": "state"},
                ],
            },
        ]
    }
    ec2.describe_snapshots.return_value = {"Snapshots": []}
    ec2.describe_addresses.return_value = {"Addresses": []}
    ec2.describe_network_interfaces.return_value = {"NetworkInterfaces": []}

    report = audit_resources(ec2_client=ec2)

    assert report.findings == []
    assert report.cost.unanticipated_monthly == 0
    assert report.cost.total_monthly == report.cost.baseline_monthly
    assert report.cost.baseline_monthly > 0


def test_audit_resources_detects_unanticipated_costs() -> None:
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [
        {
            "Reservations": [
                {"Instances": [_instance("i-main"), _instance("i-extra", "t3a.medium")]}
            ]
        },
        {"Reservations": [{"Instances": [_instance("i-lookalike", managed=False)]}]},
    ]
    ec2.describe_security_groups.side_effect = [
        {
            "SecurityGroups": [
                {
                    "GroupId": "sg-main",
                    "GroupName": "edcloud-sg",
                    "Tags": [{"Key": "edcloud:managed", "Value": "true"}],
                }
            ]
        },
        {"SecurityGroups": [{"GroupId": "sg-untagged", "GroupName": "edcloud-sg", "Tags": []}]},
    ]
    ec2.describe_volumes.return_value = {
        "Volumes": [
            {
                "VolumeId": "vol-orphan",
                "Size": 10,
                "State": "available",
                "Tags": [{"Key": "edcloud:managed", "Value": "true"}],
            },
        ]
    }
    ec2.describe_snapshots.return_value = {
        "Snapshots": [
            {"SnapshotId": "snap-1", "VolumeSize": 10, "StartTime": "2026-01-05"},
            {"SnapshotId": "snap-2", "VolumeSize": 10, "StartTime": "2026-01-04"},
            {"SnapshotId": "snap-3", "VolumeSize": 10, "StartTime": "2026-01-03"},
            {"SnapshotId": "snap-4", "VolumeSize": 10, "StartTime": "2026-01-02"},
        ]
    }
    ec2.describe_addresses.return_value = {
        "Addresses": [
            {"AllocationId": "eipalloc-1", "Tags": [{"Key": "edcloud:managed", "Value": "true"}]}
        ]
    }
    ec2.describe_network_interfaces.return_value = {
        "NetworkInterfaces": [
            {
                "NetworkInterfaceId": "eni-1",
                "Status": "available",
                "TagSet": [{"Key": "edcloud:managed", "Value": "true"}],
            }
        ]
    }

    report = audit_resources(ec2_client=ec2, snapshot_keep_last=3)
    categories = {f.category for f in report.findings}

    assert "duplicate-managed-instance" in categories
    assert "orphaned-managed-volume" in categories
    assert "snapshot-over-retention" in categories
    assert "unattached-elastic-ip" in categories
    assert report.cost.unanticipated_monthly > 0
