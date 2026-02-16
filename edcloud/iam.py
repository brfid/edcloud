"""IAM role and instance-profile management for edcloud.

The instance role grants the EC2 instance read access to ``/edcloud/*``
SSM parameters (used to retrieve the Tailscale auth key at boot).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from edcloud.config import (
    INSTANCE_PROFILE_NAME,
    INSTANCE_ROLE_NAME,
)


def _iam_client() -> Any:
    return boto3.client("iam")


def _ssm_resource_arn() -> str:
    """Build the SSM parameter ARN pattern for ``/edcloud/*``."""
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    return f"arn:aws:ssm:*:{account_id}:parameter/edcloud/*"


def _trust_policy() -> dict[str, Any]:
    """Return the EC2 service trust policy for ``sts:AssumeRole``."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }


def _ssm_read_policy() -> dict[str, Any]:
    """Return the inline policy allowing ``ssm:GetParameter`` under ``/edcloud/*``."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "ssm:GetParameter",
                "Resource": _ssm_resource_arn(),
            }
        ],
    }


def find_instance_profile() -> str | None:
    """Return the edcloud instance-profile ARN, or ``None`` if it doesn't exist."""
    iam = _iam_client()
    try:
        resp = iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        return str(resp["InstanceProfile"]["Arn"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            return None
        raise


def ensure_instance_profile(tags: dict[str, str]) -> str:
    """Idempotently create the IAM role and instance profile for edcloud.

    Steps: create role → attach inline SSM policy → create instance profile
    → bind role to profile.  Each step is a no-op if the resource exists.

    Args:
        tags: AWS tags to apply to newly created IAM resources.

    Returns:
        The instance-profile ARN.
    """
    iam = _iam_client()

    # 1. Create role if needed
    try:
        iam.get_role(RoleName=INSTANCE_ROLE_NAME)
        print(f"  IAM role exists: {INSTANCE_ROLE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            iam.create_role(
                RoleName=INSTANCE_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(_trust_policy()),
                Description="edcloud instance role - SSM parameter access",
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )
            print(f"  Created IAM role: {INSTANCE_ROLE_NAME}")
        else:
            raise

    # 2. Attach inline policy
    policy_name = "edcloud-ssm-read"
    with contextlib.suppress(ClientError):
        # Policy may already exist; put is idempotent
        iam.put_role_policy(
            RoleName=INSTANCE_ROLE_NAME,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(_ssm_read_policy()),
        )

    # 3. Create instance profile if needed
    try:
        resp = iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        profile_arn = resp["InstanceProfile"]["Arn"]
        print(f"  Instance profile exists: {INSTANCE_PROFILE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchEntity":
            resp = iam.create_instance_profile(
                InstanceProfileName=INSTANCE_PROFILE_NAME,
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )
            profile_arn = resp["InstanceProfile"]["Arn"]
            print(f"  Created instance profile: {INSTANCE_PROFILE_NAME}")
        else:
            raise

    # 4. Add role to instance profile if not already added
    resp = iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
    roles = resp["InstanceProfile"]["Roles"]
    if not any(r["RoleName"] == INSTANCE_ROLE_NAME for r in roles):
        iam.add_role_to_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            RoleName=INSTANCE_ROLE_NAME,
        )
        print(f"  Added {INSTANCE_ROLE_NAME} to {INSTANCE_PROFILE_NAME}")

    return str(profile_arn)


def delete_instance_profile() -> None:
    """Remove the edcloud IAM role and instance profile.

    Each step tolerates ``NoSuchEntity`` so the function is safe to call
    even when resources have already been partially cleaned up.
    """
    iam = _iam_client()

    # 1. Remove role from instance profile
    try:
        iam.remove_role_from_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            RoleName=INSTANCE_ROLE_NAME,
        )
        print(f"  Removed role from instance profile: {INSTANCE_PROFILE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ["NoSuchEntity", "ValidationError"]:
            pass
        else:
            print(f"  Could not remove role from instance profile: {exc}")

    # 2. Delete instance profile
    try:
        iam.delete_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        print(f"  Deleted instance profile: {INSTANCE_PROFILE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            print(f"  Could not delete instance profile: {exc}")

    # 3. Delete inline policies on role
    try:
        resp = iam.list_role_policies(RoleName=INSTANCE_ROLE_NAME)
        for policy_name in resp.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=INSTANCE_ROLE_NAME, PolicyName=policy_name)
            print(f"  Deleted inline policy: {policy_name}")
    except ClientError:
        pass

    # 4. Delete role
    try:
        iam.delete_role(RoleName=INSTANCE_ROLE_NAME)
        print(f"  Deleted IAM role: {INSTANCE_ROLE_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            print(f"  Could not delete IAM role: {exc}")
