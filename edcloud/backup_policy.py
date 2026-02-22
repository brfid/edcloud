"""AWS-native backup lifecycle policy management (DLM-backed)."""

from __future__ import annotations

from typing import Any

from edcloud.aws_clients import dlm_client as _shared_dlm_client
from edcloud.config import (
    DLM_LIFECYCLE_POLICY_NAME,
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
)


def _dlm_client() -> Any:
    return _shared_dlm_client()


def _target_tags() -> list[dict[str, str]]:
    return [
        {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
        {"Key": VOLUME_ROLE_TAG_KEY, "Value": STATE_VOLUME_ROLE},
    ]


def _policy_details(daily_keep: int, weekly_keep: int, monthly_keep: int) -> dict[str, Any]:
    return {
        "PolicyType": "EBS_SNAPSHOT_MANAGEMENT",
        "ResourceTypes": ["VOLUME"],
        "TargetTags": _target_tags(),
        "Schedules": [
            {
                "Name": "daily-7",
                "CopyTags": True,
                "CreateRule": {
                    "Interval": 24,
                    "IntervalUnit": "HOURS",
                    "Times": ["03:00"],
                },
                "RetainRule": {"Count": daily_keep},
                "TagsToAdd": [
                    {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
                    {"Key": "Name", "Value": f"{NAME_TAG}-dlm-daily"},
                    {"Key": "edcloud:backup-tier", "Value": "daily"},
                ],
            },
            {
                "Name": "weekly-4",
                "CopyTags": True,
                "CreateRule": {
                    "CronExpression": "cron(0 4 ? * SUN *)",
                },
                "RetainRule": {"Count": weekly_keep},
                "TagsToAdd": [
                    {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
                    {"Key": "Name", "Value": f"{NAME_TAG}-dlm-weekly"},
                    {"Key": "edcloud:backup-tier", "Value": "weekly"},
                ],
            },
            {
                "Name": "monthly-2",
                "CopyTags": True,
                "CreateRule": {
                    "CronExpression": "cron(0 5 1 * ? *)",
                },
                "RetainRule": {"Count": monthly_keep},
                "TagsToAdd": [
                    {"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE},
                    {"Key": "Name", "Value": f"{NAME_TAG}-dlm-monthly"},
                    {"Key": "edcloud:backup-tier", "Value": "monthly"},
                ],
            },
        ],
    }


def _find_policy_summary() -> dict[str, Any] | None:
    dlm = _dlm_client()
    resp = dlm.get_lifecycle_policies()
    for policy in resp.get("Policies", []):
        if policy.get("Description") == DLM_LIFECYCLE_POLICY_NAME:
            return policy
    return None


def policy_status() -> dict[str, Any]:
    """Return status for the managed DLM backup policy."""
    summary = _find_policy_summary()
    if not summary:
        return {"exists": False, "policy_name": DLM_LIFECYCLE_POLICY_NAME}
    dlm = _dlm_client()
    details = dlm.get_lifecycle_policy(PolicyId=summary["PolicyId"]).get("Policy", {})
    return {
        "exists": True,
        "policy_name": DLM_LIFECYCLE_POLICY_NAME,
        "policy_id": summary["PolicyId"],
        "state": summary.get("State", "UNKNOWN"),
        "details": details.get("PolicyDetails", {}),
    }


def ensure_policy(
    *,
    execution_role_arn: str,
    daily_keep: int = 7,
    weekly_keep: int = 4,
    monthly_keep: int = 2,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create or update the managed DLM policy with tiered retention."""
    if daily_keep <= 0 or weekly_keep <= 0 or monthly_keep <= 0:
        raise ValueError("daily_keep, weekly_keep, and monthly_keep must be > 0")

    dlm = _dlm_client()
    state = "ENABLED" if enabled else "DISABLED"
    details = _policy_details(
        daily_keep=daily_keep,
        weekly_keep=weekly_keep,
        monthly_keep=monthly_keep,
    )
    summary = _find_policy_summary()

    if not summary:
        resp = dlm.create_lifecycle_policy(
            ExecutionRoleArn=execution_role_arn,
            Description=DLM_LIFECYCLE_POLICY_NAME,
            State=state,
            PolicyDetails=details,
            Tags={MANAGER_TAG_KEY: MANAGER_TAG_VALUE, "Name": DLM_LIFECYCLE_POLICY_NAME},
        )
        return {
            "action": "created",
            "policy_id": resp["PolicyId"],
            "state": state,
            "daily_keep": daily_keep,
            "weekly_keep": weekly_keep,
            "monthly_keep": monthly_keep,
        }

    policy_id = summary["PolicyId"]
    dlm.update_lifecycle_policy(
        PolicyId=policy_id,
        ExecutionRoleArn=execution_role_arn,
        Description=DLM_LIFECYCLE_POLICY_NAME,
        State=state,
        PolicyDetails=details,
    )
    return {
        "action": "updated",
        "policy_id": policy_id,
        "state": state,
        "daily_keep": daily_keep,
        "weekly_keep": weekly_keep,
        "monthly_keep": monthly_keep,
    }


def disable_policy() -> dict[str, Any]:
    """Disable the managed DLM policy if it exists."""
    summary = _find_policy_summary()
    if not summary:
        return {"exists": False, "policy_name": DLM_LIFECYCLE_POLICY_NAME}
    _dlm_client().update_lifecycle_policy(
        PolicyId=summary["PolicyId"],
        State="DISABLED",
    )
    return {"exists": True, "policy_id": summary["PolicyId"], "state": "DISABLED"}
