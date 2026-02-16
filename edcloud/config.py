"""Centralized configuration for edcloud resources."""

from __future__ import annotations

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
DEFAULT_INSTANCE_TYPE = "t3a.medium"
DEFAULT_VOLUME_SIZE_GB = 30
DEFAULT_VOLUME_TYPE = "gp3"
DEFAULT_STATE_VOLUME_SIZE_GB = 30
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

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
INSTANCE_ROLE_NAME = "edcloud-instance-role"
INSTANCE_PROFILE_NAME = "edcloud-instance-profile"

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
SECURITY_GROUP_NAME = "edcloud-sg"
SECURITY_GROUP_DESC = "edcloud - no public inbound; all access via Tailscale"

# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------
HOURLY_RATES: dict[str, float] = {
    "t3a.medium": 0.0376,
    "t3a.small": 0.0188,
}
EBS_MONTHLY_RATE_PER_GB = 0.08
DEFAULT_HOURS_PER_DAY = 4


# ---------------------------------------------------------------------------
# Shared helpers — tag-based discovery primitives
# ---------------------------------------------------------------------------


def managed_filter() -> list[dict[str, Any]]:
    """Tag filter that matches edcloud-managed resources."""
    return [{"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]}]


def has_managed_tag(tags: list[dict[str, str]] | None) -> bool:
    """Check whether a resource's tag list includes the managed tag."""
    if not tags:
        return False
    return any(
        t.get("Key") == MANAGER_TAG_KEY and t.get("Value") == MANAGER_TAG_VALUE for t in tags
    )


def get_volume_ids(instance: dict[str, Any]) -> list[str]:
    """Extract EBS volume IDs from an instance description."""
    return [
        vid
        for bdm in instance.get("BlockDeviceMappings", [])
        if (vid := bdm.get("Ebs", {}).get("VolumeId"))
    ]


@dataclass(frozen=True)
class InstanceConfig:
    """Runtime-resolved configuration for an edcloud instance."""

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
        return self.tags.get("Name", NAME_TAG)
