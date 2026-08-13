"""Security group discovery and lifecycle for edcloud.

Manages the single ``edcloud-sg`` security group with zero inbound rules
(all access via Tailscale).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from botocore.exceptions import ClientError

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    SECURITY_GROUP_DESC,
    SECURITY_GROUP_NAME,
    has_managed_tag,
)
from edcloud.discovery import default_vpc_id

log = logging.getLogger(__name__)


class TagDriftError(RuntimeError):
    """Tag-based discovery invariants were violated.

    Raised when managed-resource tags are missing, duplicated, or
    inconsistent — situations that cannot be resolved automatically.
    """


def find_security_group(client: Any) -> str | None:
    """Return the edcloud security-group ID, or ``None`` if it doesn't exist.

    Raises:
        TagDriftError: On duplicate or untagged security groups.
    """
    try:
        resp = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SECURITY_GROUP_NAME]}]
        )
    except ClientError:
        return None

    groups = resp.get("SecurityGroups", [])
    managed_groups = [g for g in groups if has_managed_tag(g.get("Tags", []))]
    unmanaged_groups = [g for g in groups if not has_managed_tag(g.get("Tags", []))]

    if len(managed_groups) > 1:
        raise TagDriftError(
            "Tag drift detected: multiple managed security groups named "
            f"`{SECURITY_GROUP_NAME}` found: {', '.join(g['GroupId'] for g in managed_groups)}\n"
            "Remediation: keep one security group and remove extras."
        )

    if managed_groups and unmanaged_groups:
        raise TagDriftError(
            "Tag drift detected: mixed tagged/untagged security groups share name "
            f"`{SECURITY_GROUP_NAME}`.\n"
            f"Managed: {', '.join(g['GroupId'] for g in managed_groups)}\n"
            f"Untagged: {', '.join(g['GroupId'] for g in unmanaged_groups)}\n"
            "Remediation: retag or delete the untagged duplicate group(s)."
        )

    if unmanaged_groups and not managed_groups:
        ids = " ".join(g["GroupId"] for g in unmanaged_groups)
        raise TagDriftError(
            f"Tag drift detected: security group(s) named `{SECURITY_GROUP_NAME}` exist but "
            f"missing `{MANAGER_TAG_KEY}={MANAGER_TAG_VALUE}`: "
            f"{', '.join(g['GroupId'] for g in unmanaged_groups)}\n"
            "Remediation:\n"
            f"  aws ec2 create-tags --resources {ids} "
            f"--tags Key={MANAGER_TAG_KEY},Value={MANAGER_TAG_VALUE}\n"
            "  or delete stale security groups."
        )

    return managed_groups[0]["GroupId"] if managed_groups else None


def ensure_security_group(
    client: Any,
    tags: dict[str, str],
) -> str:
    """Find or create the edcloud security group.

    Returns:
        Security group ID.
    """
    sg_id = find_security_group(client)
    if sg_id:
        log.info("  Security group exists: %s", sg_id)
        return sg_id

    vpc_id = default_vpc_id(client)
    resp = client.create_security_group(
        GroupName=SECURITY_GROUP_NAME,
        Description=SECURITY_GROUP_DESC,
        VpcId=vpc_id,
    )
    sg_id = str(resp["GroupId"])
    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
    client.create_tags(Resources=[sg_id], Tags=tag_list)
    log.info("  Created security group: %s (no inbound rules)", sg_id)
    return sg_id


def delete_security_group(client: Any) -> None:
    """Delete the edcloud security group if it exists. Tolerates failures."""
    sg_id = find_security_group(client)
    if not sg_id:
        return
    try:
        time.sleep(5)
        client.delete_security_group(GroupId=sg_id)
        log.info("Deleted security group: %s", sg_id)
    except ClientError as exc:
        log.warning("Could not delete security group %s: %s", sg_id, exc)
        log.warning("You may need to delete it manually after ENIs are released.")
