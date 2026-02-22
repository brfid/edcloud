"""AWS permission manifest, policy generation, and preflight verification helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from edcloud.aws_clients import iam_client, sts_client
from edcloud.config import (
    DLM_LIFECYCLE_ROLE_NAME,
    INSTANCE_PROFILE_NAME,
    INSTANCE_ROLE_NAME,
)


@dataclass(frozen=True)
class PermissionProfile:
    """Named set of IAM actions used by one or more command flows."""

    name: str
    description: str
    actions: tuple[str, ...]


COMMAND_PERMISSION_PROFILES: dict[str, PermissionProfile] = {
    "core": PermissionProfile(
        name="core",
        description="Credential/identity preflight used by most AWS-backed commands.",
        actions=("sts:GetCallerIdentity",),
    ),
    "provision": PermissionProfile(
        name="provision",
        description="Provision flow, including IAM profile bootstrap and instance launch.",
        actions=(
            "ec2:AttachVolume",
            "ec2:CreateSecurityGroup",
            "ec2:CreateTags",
            "ec2:DescribeImages",
            "ec2:DescribeInstances",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeSubnets",
            "ec2:DescribeVolumes",
            "ec2:DescribeVpcs",
            "ec2:RunInstances",
            "ec2:TerminateInstances",
            "iam:AddRoleToInstanceProfile",
            "iam:CreateInstanceProfile",
            "iam:CreateRole",
            "iam:GetInstanceProfile",
            "iam:GetRole",
            "iam:PutRolePolicy",
            "iam:PassRole",
            "ssm:GetParameter",
            "ssm:PutParameter",
        ),
    ),
    "lifecycle": PermissionProfile(
        name="lifecycle",
        description="Start/stop/destroy/reprovision and cleanup operations.",
        actions=(
            "ec2:DeleteSecurityGroup",
            "ec2:DeleteVolume",
            "ec2:DescribeInstances",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeVolumes",
            "ec2:StartInstances",
            "ec2:StopInstances",
            "ec2:TerminateInstances",
            "iam:DeleteInstanceProfile",
            "iam:DeleteRole",
            "iam:DeleteRolePolicy",
            "iam:ListRolePolicies",
            "iam:RemoveRoleFromInstanceProfile",
        ),
    ),
    "snapshot": PermissionProfile(
        name="snapshot",
        description="Manual snapshots, list/prune, and cost visibility paths.",
        actions=(
            "ec2:CreateSnapshot",
            "ec2:DeleteSnapshot",
            "ec2:DescribeSnapshots",
            "ec2:DescribeVolumes",
        ),
    ),
    "restore-drill": PermissionProfile(
        name="restore-drill",
        description="Non-destructive snapshot restore drill using temporary EBS volumes.",
        actions=(
            "ec2:AttachVolume",
            "ec2:CreateVolume",
            "ec2:DeleteVolume",
            "ec2:DescribeSnapshots",
            "ec2:DescribeVolumes",
            "ec2:DetachVolume",
        ),
    ),
    "backup-policy": PermissionProfile(
        name="backup-policy",
        description="DLM lifecycle policy management for state-volume backups.",
        actions=(
            "dlm:CreateLifecyclePolicy",
            "dlm:GetLifecyclePolicies",
            "dlm:GetLifecyclePolicy",
            "dlm:UpdateLifecyclePolicy",
            "iam:AttachRolePolicy",
            "iam:CreateRole",
            "iam:GetRole",
        ),
    ),
    "status-and-audit": PermissionProfile(
        name="status-and-audit",
        description="Status/audit views that inspect managed resources and cost signals.",
        actions=(
            "ec2:DescribeAddresses",
            "ec2:DescribeInstances",
            "ec2:DescribeNetworkInterfaces",
            "ec2:DescribeSecurityGroups",
            "ec2:DescribeSnapshots",
            "ec2:DescribeVolumes",
        ),
    ),
    "resize": PermissionProfile(
        name="resize",
        description="In-place instance-type and EBS expansion workflow.",
        actions=(
            "ec2:DescribeInstances",
            "ec2:DescribeVolumes",
            "ec2:DescribeVolumesModifications",
            "ec2:ModifyInstanceAttribute",
            "ec2:ModifyVolume",
            "ec2:StartInstances",
            "ec2:StopInstances",
        ),
    ),
    "secrets": PermissionProfile(
        name="secrets",
        description="SSM token setup/load helpers for operator workflows.",
        actions=(
            "ssm:DescribeParameters",
            "ssm:GetParameter",
            "ssm:PutParameter",
        ),
    ),
}


def available_profiles() -> list[str]:
    """Return sorted permission profile names."""
    return sorted(COMMAND_PERMISSION_PROFILES.keys())


def resolve_profiles(selected: tuple[str, ...]) -> list[PermissionProfile]:
    """Resolve user-selected profile names into profile objects.

    If no profile is selected or ``all`` is included, all profiles are returned.
    """
    if not selected or "all" in selected:
        return [COMMAND_PERMISSION_PROFILES[name] for name in available_profiles()]
    return [COMMAND_PERMISSION_PROFILES[name] for name in sorted(set(selected))]


def required_actions(selected: tuple[str, ...]) -> list[str]:
    """Return unique, sorted IAM actions for selected profiles."""
    actions = {
        action
        for profile in resolve_profiles(selected)
        for action in profile.actions
    }
    return sorted(actions)


def policy_document(selected: tuple[str, ...]) -> dict[str, Any]:
    """Build a practical least-privilege IAM policy document for operator usage."""
    selected_actions = set(required_actions(selected))

    ec2_actions = sorted(a for a in selected_actions if a.startswith("ec2:"))
    dlm_actions = sorted(a for a in selected_actions if a.startswith("dlm:"))
    iam_actions = sorted(a for a in selected_actions if a.startswith("iam:"))
    ssm_actions = sorted(a for a in selected_actions if a.startswith("ssm:"))
    sts_actions = sorted(a for a in selected_actions if a.startswith("sts:"))

    statements: list[dict[str, Any]] = []
    if sts_actions:
        statements.append(
            {
                "Sid": "EdcloudIdentityCheck",
                "Effect": "Allow",
                "Action": sts_actions,
                "Resource": "*",
            }
        )
    if ec2_actions:
        statements.append(
            {
                "Sid": "EdcloudEc2Lifecycle",
                "Effect": "Allow",
                "Action": ec2_actions,
                "Resource": "*",
            }
        )
    if dlm_actions:
        statements.append(
            {
                "Sid": "EdcloudDlmPolicy",
                "Effect": "Allow",
                "Action": dlm_actions,
                "Resource": "*",
            }
        )
    if ssm_actions:
        parameter_actions = sorted(a for a in ssm_actions if a != "ssm:DescribeParameters")
        if parameter_actions:
            statements.append(
                {
                    "Sid": "EdcloudSsmParameterPath",
                    "Effect": "Allow",
                    "Action": parameter_actions,
                    "Resource": "arn:aws:ssm:*:*:parameter/edcloud/*",
                }
            )
        if "ssm:DescribeParameters" in ssm_actions:
            statements.append(
                {
                    "Sid": "EdcloudSsmDescribe",
                    "Effect": "Allow",
                    "Action": ["ssm:DescribeParameters"],
                    "Resource": "*",
                }
            )
    if iam_actions:
        pass_role = [a for a in iam_actions if a == "iam:PassRole"]
        other_iam_actions = sorted(a for a in iam_actions if a != "iam:PassRole")
        iam_resources = [
            f"arn:aws:iam::*:role/{INSTANCE_ROLE_NAME}",
            f"arn:aws:iam::*:role/{DLM_LIFECYCLE_ROLE_NAME}",
            f"arn:aws:iam::*:instance-profile/{INSTANCE_PROFILE_NAME}",
        ]
        if other_iam_actions:
            statements.append(
                {
                    "Sid": "EdcloudIamManagedRolesProfiles",
                    "Effect": "Allow",
                    "Action": other_iam_actions,
                    "Resource": iam_resources,
                }
            )
        if pass_role:
            statements.append(
                {
                    "Sid": "EdcloudIamPassRole",
                    "Effect": "Allow",
                    "Action": pass_role,
                    "Resource": [
                        f"arn:aws:iam::*:role/{INSTANCE_ROLE_NAME}",
                        f"arn:aws:iam::*:role/{DLM_LIFECYCLE_ROLE_NAME}",
                    ],
                }
            )

    return {
        "Version": "2012-10-17",
        "Statement": statements,
    }


@dataclass(frozen=True)
class VerificationResult:
    """Result of best-effort permission verification for current principal."""

    ok: bool
    missing_actions: tuple[str, ...]
    principal_arn: str
    policy_source_arn: str
    detail: str


def _policy_source_arn(caller_arn: str) -> str:
    """Convert caller ARN into a source ARN accepted by SimulatePrincipalPolicy."""
    if ":assumed-role/" not in caller_arn:
        return caller_arn

    # arn:aws:sts::<acct>:assumed-role/<RoleName>/<SessionName>
    # -> arn:aws:iam::<acct>:role/<RoleName>
    parts = caller_arn.split(":", maxsplit=5)
    account_id = parts[4]
    resource = parts[5]
    _, role_name, _ = resource.split("/", maxsplit=2)
    return f"arn:aws:iam::{account_id}:role/{role_name}"


def verify_required_actions(actions: list[str]) -> VerificationResult:
    """Best-effort IAM simulation for the current operator principal.

    This uses ``iam:SimulatePrincipalPolicy``. If the caller is not permitted
    to run simulation, the result explains that verification could not run.
    """
    sts = sts_client()
    caller_arn = str(sts.get_caller_identity()["Arn"])
    source_arn = _policy_source_arn(caller_arn)
    iam = iam_client()

    try:
        resp = iam.simulate_principal_policy(
            PolicySourceArn=source_arn,
            ActionNames=actions,
            ResourceArns=["*"],
        )
    except (ClientError, BotoCoreError) as exc:
        return VerificationResult(
            ok=False,
            missing_actions=(),
            principal_arn=caller_arn,
            policy_source_arn=source_arn,
            detail=(
                "Could not verify permissions via IAM simulation. "
                "Grant iam:SimulatePrincipalPolicy or run with a principal that has it. "
                f"Underlying error: {exc}"
            ),
        )

    missing = []
    for result in resp.get("EvaluationResults", []):
        decision = str(result.get("EvalDecision", ""))
        if decision not in {"allowed", "Allowed", "allowedByPermissionsBoundary"}:
            action_name = str(result.get("EvalActionName", ""))
            if action_name:
                missing.append(action_name)

    missing_actions = tuple(sorted(set(missing)))
    if missing_actions:
        return VerificationResult(
            ok=False,
            missing_actions=missing_actions,
            principal_arn=caller_arn,
            policy_source_arn=source_arn,
            detail="Missing required actions for selected edcloud command profile(s).",
        )

    return VerificationResult(
        ok=True,
        missing_actions=(),
        principal_arn=caller_arn,
        policy_source_arn=source_arn,
        detail="All required actions are allowed for selected profile(s).",
    )


def profiles_json(selected: tuple[str, ...]) -> str:
    """Render selected profiles as JSON for CLI output."""
    payload = [
        {
            "name": profile.name,
            "description": profile.description,
            "actions": list(profile.actions),
        }
        for profile in resolve_profiles(selected)
    ]
    return json.dumps(payload, indent=2)
