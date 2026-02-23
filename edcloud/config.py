"""Centralized configuration and tag-based discovery primitives.

All AWS resource names, tag conventions, and default tuning knobs live here
so that the rest of the package can import a single source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# AWS resource tags — used to find managed resources without local state
# ---------------------------------------------------------------------------
MANAGER_TAG_KEY = "edcloud:managed"
MANAGER_TAG_VALUE = "true"
VOLUME_ROLE_TAG_KEY = "edcloud:volume-role"
ROOT_VOLUME_ROLE = "root"
STATE_VOLUME_ROLE = "state"
NAME_TAG = "edcloud"

# ---------------------------------------------------------------------------
# EC2 defaults
# ---------------------------------------------------------------------------
DEFAULT_INSTANCE_TYPE = "t3a.small"
DEFAULT_VOLUME_SIZE_GB = 30  # Root: OS + dev tools (containerd data lives on state volume)
DEFAULT_VOLUME_TYPE = "gp3"
DEFAULT_STATE_VOLUME_SIZE_GB = 30  # State: home + Docker + containerd data
DEFAULT_STATE_VOLUME_TYPE = "gp3"
DEFAULT_STATE_VOLUME_DEVICE_NAME = "/dev/sdf"

# Ubuntu 24.04 LTS — resolve via SSM parameter at provision time
AMI_SSM_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
)

# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------
DEFAULT_TAILSCALE_HOSTNAME = "edcloud"
DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER = "/edcloud/tailscale_auth_key"
DEFAULT_SSH_USER = "ubuntu"

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
INSTANCE_ROLE_NAME = "edcloud-instance-role"
INSTANCE_PROFILE_NAME = "edcloud-instance-profile"
DLM_LIFECYCLE_ROLE_NAME = "edcloud-dlm-lifecycle-role"
DLM_LIFECYCLE_POLICY_NAME = "edcloud-dlm-policy"

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
SECURITY_GROUP_NAME = "edcloud-sg"
SECURITY_GROUP_DESC = "edcloud - no public inbound; all access via Tailscale"

# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------
# Note: rates are approximate us-east-1 on-demand prices (USD/hr).
# Verify against https://aws.amazon.com/ec2/pricing/on-demand/ for your region.
HOURLY_RATES: dict[str, float] = {
    "t3a.micro": 0.0094,  # 2 vCPU, 1 GB RAM - minimal workloads only
    "t3a.small": 0.0188,  # 2 vCPU, 2 GB RAM - default for light dev work
    "t3a.medium": 0.0376,  # 2 vCPU, 4 GB RAM - heavier Docker workloads
}
EBS_MONTHLY_RATE_PER_GB = 0.08
SNAPSHOT_MONTHLY_RATE_PER_GB = 0.05
EIP_UNATTACHED_MONTHLY_RATE = 3.60
DEFAULT_HOURS_PER_DAY = 8
DEFAULT_SNAPSHOT_KEEP_LAST = 3


# ---------------------------------------------------------------------------
# Shared helpers — tag-based discovery primitives
# ---------------------------------------------------------------------------


def managed_filter() -> list[dict[str, Any]]:
    """Return an EC2 ``Filters`` list matching edcloud-managed resources.

    Returns:
        Single-element filter list suitable for ``describe_*`` API calls.
    """
    return [{"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]}]


def tag_value(tags: Sequence[Mapping[str, Any]] | None, key: str) -> str | None:
    """Look up a single tag value from an AWS-format tag list.

    Args:
        tags: AWS tag list (``[{"Key": …, "Value": …}, …]``), or ``None``.
        key: Tag key to search for.

    Returns:
        The tag's value as a string, or ``None`` if not found.
    """
    if not tags:
        return None
    for t in tags:
        if t.get("Key") == key:
            return str(t.get("Value", ""))
    return None


def has_managed_tag(tags: list[dict[str, str]] | None) -> bool:
    """Check whether a resource's tag list includes the managed marker."""
    return tag_value(tags, MANAGER_TAG_KEY) == MANAGER_TAG_VALUE


def get_volume_ids(instance: dict[str, Any]) -> list[str]:
    """Extract EBS volume IDs from an EC2 instance description.

    Args:
        instance: A single item from ``describe_instances`` Reservations.

    Returns:
        Volume IDs in block-device-mapping order (may be empty).
    """
    return [
        vid
        for bdm in instance.get("BlockDeviceMappings", [])
        if (vid := bdm.get("Ebs", {}).get("VolumeId"))
    ]


@dataclass(frozen=True)
class InstanceConfig:
    """Runtime-resolved configuration for provisioning an edcloud instance.

    All fields carry sensible defaults so callers can override only what
    they need.  The ``tags`` dict is always pre-populated with the
    managed-resource marker and a human-friendly Name tag.
    """

    instance_type: str = DEFAULT_INSTANCE_TYPE
    volume_size_gb: int = DEFAULT_VOLUME_SIZE_GB
    volume_type: str = DEFAULT_VOLUME_TYPE
    state_volume_size_gb: int = DEFAULT_STATE_VOLUME_SIZE_GB
    state_volume_type: str = DEFAULT_STATE_VOLUME_TYPE
    state_volume_device_name: str = DEFAULT_STATE_VOLUME_DEVICE_NAME
    tailscale_hostname: str = DEFAULT_TAILSCALE_HOSTNAME
    tailscale_auth_key_ssm_parameter: str = DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER
    ami_ssm_parameter: str = AMI_SSM_PARAMETER
    tags: dict[str, str] = field(
        default_factory=lambda: {
            MANAGER_TAG_KEY: MANAGER_TAG_VALUE,
            "Name": NAME_TAG,
        }
    )

    @property
    def name_tag(self) -> str:
        """Shortcut for the ``Name`` tag value."""
        return self.tags.get("Name", NAME_TAG)
