"""Shared AWS resource query helpers.

Centralizes repetitive managed-resource filtering patterns used by lifecycle,
cleanup, and audit flows.
"""

from __future__ import annotations

from typing import Any

from edcloud.config import (
    VOLUME_ROLE_TAG_KEY,
    managed_filter,
)


def managed_volume_filters(*, status: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """Build EC2 filters for managed EBS volumes."""
    filters = [*managed_filter()]
    if status:
        filters.append({"Name": "status", "Values": [status]})
    if role:
        filters.append({"Name": f"tag:{VOLUME_ROLE_TAG_KEY}", "Values": [role]})
    return filters


def list_managed_volumes(
    ec2_client: Any,
    *,
    status: str | None = None,
    role: str | None = None,
) -> list[dict[str, Any]]:
    """Return managed EBS volumes with optional status/role filters."""
    resp = ec2_client.describe_volumes(Filters=managed_volume_filters(status=status, role=role))
    return list(resp.get("Volumes", []))
