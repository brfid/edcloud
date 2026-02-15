"""Centralized configuration for edcloud resources."""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# AWS resource tags — used to find managed resources without local state
# ---------------------------------------------------------------------------
MANAGER_TAG_KEY = "edcloud:managed"
MANAGER_TAG_VALUE = "true"
NAME_TAG = "edcloud"

# ---------------------------------------------------------------------------
# EC2 defaults
# ---------------------------------------------------------------------------
DEFAULT_INSTANCE_TYPE = "t3a.medium"
DEFAULT_VOLUME_SIZE_GB = 80
DEFAULT_VOLUME_TYPE = "gp3"
DEFAULT_STATE_VOLUME_SIZE_GB = 10
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

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
SECURITY_GROUP_NAME = "edcloud-sg"
SECURITY_GROUP_DESC = "edcloud - no public inbound; all access via Tailscale"


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
