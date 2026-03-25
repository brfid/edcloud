"""EC2 lifecycle management: provision, start, stop, status, destroy.

Resources are tracked by AWS tags — no local state file needed.
Tag ``edcloud:managed = true`` identifies all managed resources.
"""

from __future__ import annotations

import logging
import random  # nosec B311
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from edcloud.aws_check import get_region
from edcloud.aws_clients import ec2_client as _ec2_client
from edcloud.aws_clients import ssm_client as _ssm_client
from edcloud.config import (
    DEFAULT_HOURS_PER_DAY,
    EBS_MONTHLY_RATE_PER_GB,
    HOURLY_RATES,
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    ROOT_VOLUME_ROLE,
    SECURITY_GROUP_DESC,
    SECURITY_GROUP_NAME,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
    InstanceConfig,
    get_volume_ids,
    has_managed_tag,
    managed_filter,
    tag_value,
)
from edcloud.discovery import list_instances
from edcloud.iam import delete_instance_profile, ensure_instance_profile
from edcloud.resource_queries import list_managed_volumes

log = logging.getLogger(__name__)

_USER_DATA_PATH = Path(__file__).resolve().parent.parent / "cloud-init" / "user-data.yaml"


class TagDriftError(RuntimeError):
    """Tag-based discovery invariants were violated.

    Raised when managed-resource tags are missing, duplicated, or
    inconsistent — situations that cannot be resolved automatically.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_ec2_client() -> Any:
    """Return a low-level EC2 client (public API)."""
    return _ec2_client()


def find_instance(client: Any) -> dict[str, Any] | None:
    """Locate the managed instance (public API).

    Delegates to the internal ``_find_instance`` helper.
    """
    return _find_instance(client)


def fetch_tailscale_auth_key_from_ssm(parameter_name: str) -> str:
    """Read a Tailscale auth key from SSM Parameter Store.

    Args:
        parameter_name: The SSM parameter path (SecureString supported).

    Returns:
        The decrypted parameter value.
    """
    ssm = _ssm_client()
    resp = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
    return str(resp["Parameter"]["Value"])


def _instance_summary(instances: list[dict[str, Any]]) -> str:
    """Format a compact ``id (state)`` summary for one or more instances."""
    return ", ".join(f"{i['InstanceId']} ({i['State']['Name']})" for i in instances)


def _validate_instance_volume_tags(client: Any, instance: dict[str, Any]) -> None:
    """Ensure every volume attached to *instance* carries the managed tag.

    Raises:
        TagDriftError: If any attached volume is missing the managed tag.
    """
    volume_ids = get_volume_ids(instance)
    if not volume_ids:
        return

    resp = client.describe_volumes(VolumeIds=volume_ids)
    untagged = [
        v["VolumeId"] for v in resp.get("Volumes", []) if not has_managed_tag(v.get("Tags", []))
    ]
    if not untagged:
        return

    ids = " ".join(untagged)
    raise TagDriftError(
        "Tag drift detected: attached EBS volume(s) are missing "
        f"`{MANAGER_TAG_KEY}={MANAGER_TAG_VALUE}`: {', '.join(untagged)}\n"
        "Remediation:\n"
        f"  aws ec2 create-tags --resources {ids} "
        f"--tags Key={MANAGER_TAG_KEY},Value={MANAGER_TAG_VALUE}"
    )


def _managed_orphan_report(client: Any) -> dict[str, list[str]]:
    """Scan for managed resources not attached to an active instance.

    Returns:
        Dict with ``security_groups`` and ``volumes`` lists of orphaned IDs.
    """
    report: dict[str, list[str]] = {"security_groups": [], "volumes": []}

    sg_resp = client.describe_security_groups(Filters=managed_filter())
    for sg in sg_resp.get("SecurityGroups", []):
        group_name = sg.get("GroupName")
        if group_name == SECURITY_GROUP_NAME:
            report["security_groups"].append(sg["GroupId"])

    for volume in list_managed_volumes(client):
        if volume.get("State") == "available":
            report["volumes"].append(volume["VolumeId"])

    return report


def _orphaned_resources_text(report: dict[str, list[str]]) -> str:
    """Render human-readable lines for orphaned-resource IDs."""
    lines: list[str] = []
    security_groups = report.get("security_groups", [])
    volumes = report.get("volumes", [])
    if security_groups:
        lines.append(f"  Security groups: {', '.join(security_groups)}")
    if volumes:
        lines.append(f"  Volumes (available): {', '.join(volumes)}")
    return "\n".join(lines)


def _apply_tags(client: Any, resource_ids: list[str], tags: dict[str, str]) -> None:
    """Apply tags to one or more resources."""
    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
    client.create_tags(Resources=resource_ids, Tags=tag_list)


def _find_orphaned_state_volume_id(client: Any) -> str | None:
    """Return the single available managed state volume ID, if present.

    Raises:
        TagDriftError: If multiple available state volumes exist.
    """
    resp = client.describe_volumes(
        Filters=[
            {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]},
            {"Name": f"tag:{VOLUME_ROLE_TAG_KEY}", "Values": [STATE_VOLUME_ROLE]},
            {"Name": "status", "Values": ["available"]},
        ]
    )
    vols = [v["VolumeId"] for v in resp.get("Volumes", [])]
    if not vols:
        return None
    if len(vols) > 1:
        raise TagDriftError(
            "Tag drift detected: multiple available managed state volumes found: "
            f"{', '.join(vols)}\n"
            "Remediation: keep a single state volume for this environment."
        )
    return str(vols[0])


def _state_volume_az(client: Any, volume_id: str) -> str:
    """Return the availability zone of an EBS volume.

    Raises:
        RuntimeError: If the volume is not found.
    """
    resp = client.describe_volumes(VolumeIds=[volume_id])
    volumes = resp.get("Volumes", [])
    if not volumes:
        raise RuntimeError(f"State volume not found: {volume_id}")
    return str(volumes[0]["AvailabilityZone"])


def _default_subnet_for_az(client: Any, az: str) -> str:
    """Return the default subnet ID in a given availability zone.

    Raises:
        RuntimeError: If no default subnet exists in the AZ.
    """
    resp = client.describe_subnets(
        Filters=[
            {"Name": "availability-zone", "Values": [az]},
            {"Name": "default-for-az", "Values": ["true"]},
        ]
    )
    subnets = resp.get("Subnets", [])
    if not subnets:
        raise RuntimeError(
            f"No default subnet found in AZ {az}. "
            "Create/enable a default subnet in this AZ or choose a state volume in a supported AZ."
        )
    return str(subnets[0]["SubnetId"])


def _find_instance(client: Any) -> dict[str, Any] | None:
    """Locate the single edcloud-managed instance (any non-terminated state).

    Returns:
        Instance dict from ``describe_instances``, or ``None``.

    Raises:
        TagDriftError: On duplicate managed instances or missing tags.
    """
    managed_instances = list_instances(client, managed_filter())
    if len(managed_instances) > 1:
        raise TagDriftError(
            "Tag drift detected: multiple managed instances found: "
            f"{_instance_summary(managed_instances)}\n"
            "Remediation: keep only one managed instance. Terminate extras or remove "
            f"`{MANAGER_TAG_KEY}` from resources you no longer want managed."
        )

    if not managed_instances:
        named_instances = list_instances(client, [{"Name": "tag:Name", "Values": [NAME_TAG]}])
        untagged_named = [i for i in named_instances if not has_managed_tag(i.get("Tags", []))]
        if untagged_named:
            instance_ids = " ".join(i["InstanceId"] for i in untagged_named)
            instance_list = ", ".join(i["InstanceId"] for i in untagged_named)
            raise TagDriftError(
                "Tag drift detected: instance(s) tagged `Name=edcloud` are missing "
                f"`{MANAGER_TAG_KEY}={MANAGER_TAG_VALUE}`: {instance_list}\n"
                "Remediation:\n"
                f"  aws ec2 create-tags --resources {instance_ids} "
                f"--tags Key={MANAGER_TAG_KEY},Value={MANAGER_TAG_VALUE}\n"
                "  or terminate the stale instance(s) and run `edc provision`."
            )
        return None

    inst = managed_instances[0]
    _validate_instance_volume_tags(client, inst)
    return inst


def _find_security_group(client: Any) -> str | None:
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


def _resolve_ami(ssm_parameter: str) -> str:
    """Resolve an AMI ID from an SSM public parameter.

    Falls back to a ``describe_images`` search if the SSM parameter is
    missing.
    """
    ssm = _ssm_client()
    try:
        resp = ssm.get_parameter(Name=ssm_parameter)
        return str(resp["Parameter"]["Value"])
    except ClientError as exc:
        # Fallback: if SSM parameter path doesn't work, use a direct lookup
        if "ParameterNotFound" in str(exc):
            log.warning("SSM parameter %s not found, falling back to AMI search...", ssm_parameter)
            ec2 = _ec2_client()
            resp = ec2.describe_images(
                Owners=["099720109477"],  # Canonical's public AWS account (not a secret)
                Filters=[
                    {
                        "Name": "name",
                        "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"],
                    },
                    {"Name": "state", "Values": ["available"]},
                    {"Name": "architecture", "Values": ["x86_64"]},
                ],
            )
            images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
            if images:
                return str(images[0]["ImageId"])
        raise


def _get_default_vpc_id(client: Any) -> str:
    """Return the default VPC ID.

    Raises:
        RuntimeError: If no default VPC exists.
    """
    resp = client.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        raise RuntimeError("No default VPC found. Create one or specify a VPC ID.")
    return str(vpcs[0]["VpcId"])


def _get_aws_region() -> str:
    """Return the current AWS region from the boto3 session.

    Raises:
        RuntimeError: If no region is configured.
    """
    region = get_region()
    if not region:
        raise RuntimeError(
            "No AWS region configured. Set AWS_DEFAULT_REGION or run 'aws configure'."
        )
    return region


def _validate_user_data_inputs(
    tailscale_hostname: str,
    tailscale_auth_key: str | None = None,
    tailscale_auth_key_ssm_parameter: str | None = None,
    aws_region: str | None = None,
    dotfiles_repo: str | None = None,
    dotfiles_branch: str | None = None,
) -> None:
    """Validate user-data template inputs to prevent injection attacks.

    Args:
        tailscale_hostname: DNS-safe hostname (1-63 chars, alphanumeric/hyphen).
        tailscale_auth_key: Raw auth key value (checked for shell-dangerous chars).
        tailscale_auth_key_ssm_parameter: SSM parameter path (path-safe chars only).
        aws_region: AWS region string (e.g. ``us-east-1``).

    Raises:
        ValueError: If any input contains invalid or dangerous characters.
    """
    # Validate hostname: DNS-safe, 1-63 chars
    if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$", tailscale_hostname):
        raise ValueError(
            f"Invalid tailscale_hostname: {tailscale_hostname!r}. "
            "Must be 1-63 alphanumeric/hyphen characters, cannot start/end with hyphen."
        )

    # Validate auth key if provided (transitional, for old flow)
    if tailscale_auth_key is not None:
        dangerous_chars = ["\n", "\r", "`", "$(", "${", ";", "'", '"', "|", "&"]
        for char in dangerous_chars:
            if char in tailscale_auth_key:
                raise ValueError(
                    f"Invalid tailscale_auth_key: contains dangerous character {char!r}"
                )

    # Validate SSM parameter path if provided
    if tailscale_auth_key_ssm_parameter is not None and not re.match(
        r"^[a-zA-Z0-9/_.-]+$", tailscale_auth_key_ssm_parameter
    ):
        raise ValueError(
            f"Invalid tailscale_auth_key_ssm_parameter: {tailscale_auth_key_ssm_parameter!r}. "
            "Must contain only alphanumeric, /, _, ., - characters."
        )

    # Validate AWS region if provided
    if aws_region is not None and not re.match(r"^[a-z]{2}(-[a-z]+-[0-9]+)?$", aws_region):
        raise ValueError(
            f"Invalid aws_region: {aws_region!r}. Must match AWS region format (e.g., us-east-1)."
        )

    # Validate dotfiles repo selector
    if dotfiles_repo is not None:
        if dotfiles_repo == "auto":
            pass
        elif not re.match(
            r"^(https://github\.com|git@github\.com:)[A-Za-z0-9._/-]+\.git$",
            dotfiles_repo,
        ):
            raise ValueError(
                f"Invalid dotfiles_repo: {dotfiles_repo!r}. "
                "Use 'auto', an https GitHub URL, or an SSH GitHub URL ending in .git."
            )

    # Validate git branch/ref-ish input used for dotfiles checkout
    if dotfiles_branch is not None:
        if not re.match(r"^[A-Za-z0-9._/-]{1,100}$", dotfiles_branch):
            raise ValueError(
                f"Invalid dotfiles_branch: {dotfiles_branch!r}. "
                "Use a simple branch/ref name (alphanumeric, ., _, /, -)."
            )
        if ".." in dotfiles_branch or dotfiles_branch.startswith("-"):
            raise ValueError(
                f"Invalid dotfiles_branch: {dotfiles_branch!r}. "
                "Branch cannot contain '..' or start with '-'."
            )


def _render_user_data(
    tailscale_auth_key_ssm_parameter: str,
    tailscale_hostname: str,
    aws_region: str,
    dotfiles_repo: str,
    dotfiles_branch: str,
) -> str:
    """Read the cloud-init template and interpolate runtime variables.

    Args:
        tailscale_auth_key_ssm_parameter: SSM parameter the instance reads at boot.
        tailscale_hostname: MagicDNS hostname to register.
        aws_region: Region for SSM API calls from the instance.

    Returns:
        Rendered user-data string ready for RunInstances.
    """
    _validate_user_data_inputs(
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
        aws_region=aws_region,
        dotfiles_repo=dotfiles_repo,
        dotfiles_branch=dotfiles_branch,
    )
    template = _USER_DATA_PATH.read_text()
    return (
        template.replace("${TAILSCALE_AUTH_KEY_SSM_PARAMETER}", tailscale_auth_key_ssm_parameter)
        .replace("${TAILSCALE_HOSTNAME}", tailscale_hostname)
        .replace("${AWS_REGION}", aws_region)
        .replace("${DOTFILES_REPO}", dotfiles_repo)
        .replace("${DOTFILES_BRANCH}", dotfiles_branch)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def provision(
    cfg: InstanceConfig,
    require_existing_state_volume: bool = False,
) -> dict[str, str]:
    """Create the edcloud instance from scratch.

    The Tailscale auth key is fetched from SSM at boot time by the instance
    itself.

    Args:
        cfg: Resolved instance configuration.
        require_existing_state_volume: If ``True``, abort when no reusable
            managed state volume is available.

    Returns:
        Dict with keys ``instance_id``, ``security_group_id``, ``public_ip``.

    Raises:
        RuntimeError: If an instance already exists, or provisioning fails.
    """
    ec2 = _ec2_client()

    # Guard: already exists?
    existing = _find_instance(ec2)
    if existing:
        iid = existing["InstanceId"]
        state = existing["State"]["Name"]
        raise RuntimeError(
            f"An edcloud instance already exists: {iid} ({state}). "
            "Run 'edc destroy' first if you want to reprovision."
        )

    # Pre-provision orphan check: warn if managed resources exist without an instance
    orphaned = _managed_orphan_report(ec2)
    if orphaned["security_groups"] or orphaned["volumes"]:
        log.warning(
            "Orphaned managed resources found (no active instance). "
            "These may be reused or cleaned up during provisioning:\n%s",
            _orphaned_resources_text(orphaned),
        )

    log.info("Provisioning edcloud instance...")

    # 1. Security group -------------------------------------------------------
    sg_id = _find_security_group(ec2)
    if sg_id:
        log.info("  Security group exists: %s", sg_id)
    else:
        vpc_id = _get_default_vpc_id(ec2)
        resp = ec2.create_security_group(
            GroupName=SECURITY_GROUP_NAME,
            Description=SECURITY_GROUP_DESC,
            VpcId=vpc_id,
        )
        sg_id = str(resp["GroupId"])
        _apply_tags(ec2, [sg_id], cfg.tags)

        # Revoke the default "allow all outbound" rule? No — we need outbound
        # for apt, Docker pulls, Tailscale coordination, etc.
        # No inbound rules: all access comes via Tailscale tunnel.
        log.info("  Created security group: %s (no inbound rules)", sg_id)

    # 2. IAM instance profile -------------------------------------------------
    profile_arn = ensure_instance_profile(cfg.tags)
    log.info("  Instance profile: %s", profile_arn)

    # 3. Resolve AMI -----------------------------------------------------------
    ami_id = _resolve_ami(cfg.ami_ssm_parameter)
    log.info("  AMI: %s", ami_id)

    # 4. User-data script ------------------------------------------------------
    aws_region = _get_aws_region()
    user_data = _render_user_data(
        tailscale_auth_key_ssm_parameter=cfg.tailscale_auth_key_ssm_parameter,
        tailscale_hostname=cfg.tailscale_hostname,
        aws_region=aws_region,
        dotfiles_repo=cfg.dotfiles_repo,
        dotfiles_branch=cfg.dotfiles_branch,
    )
    log.info(
        "  Tailscale auth key will be fetched from SSM: %s",
        cfg.tailscale_auth_key_ssm_parameter,
    )

    # 3.5 Prefer reusing existing managed state volume when available ----------
    reused_state_volume_id = _find_orphaned_state_volume_id(ec2)

    if require_existing_state_volume and not reused_state_volume_id:
        raise RuntimeError(
            "No reusable managed state volume found. "
            "Refusing provision due to --require-existing-state-volume."
        )

    if reused_state_volume_id:
        state_mapping: dict[str, Any] | None = None
        log.info("  Reusing managed state volume: %s", reused_state_volume_id)
        state_volume_az = _state_volume_az(ec2, reused_state_volume_id)
        subnet_id = _default_subnet_for_az(ec2, state_volume_az)
        log.info("  Launching instance in %s to match reused state volume", state_volume_az)
    else:
        state_mapping = {
            "DeviceName": cfg.state_volume_device_name,
            "Ebs": {
                "VolumeSize": cfg.state_volume_size_gb,
                "VolumeType": cfg.state_volume_type,
                "DeleteOnTermination": False,
                "Encrypted": True,
            },
        }
        log.info("  No reusable managed state volume found; creating new state volume.")
        subnet_id = None

    # 4. Launch instance -------------------------------------------------------
    # IAM instance profile creation can be eventually consistent. Retry launch
    # briefly when AWS returns transient invalid-profile errors.
    run_kwargs: dict[str, Any] = {
        "ImageId": ami_id,
        "InstanceType": cfg.instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "SecurityGroupIds": [sg_id],
        "IamInstanceProfile": {"Arn": profile_arn},
        "UserData": user_data,
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": cfg.volume_size_gb,
                    "VolumeType": cfg.volume_type,
                    "DeleteOnTermination": True,
                    "Encrypted": True,
                },
            },
            *([state_mapping] if state_mapping else []),
        ],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [{"Key": k, "Value": v} for k, v in cfg.tags.items()],
            },
            {
                "ResourceType": "volume",
                "Tags": [{"Key": k, "Value": v} for k, v in cfg.tags.items()],
            },
        ],
        # No key pair — SSH access is via Tailscale + instance connect or SSM
        "MetadataOptions": {
            "HttpTokens": "required",  # IMDSv2 only
            "HttpEndpoint": "enabled",
            "HttpPutResponseHopLimit": 1,  # Prevent containers from reaching IMDS
        },
    }
    if subnet_id is not None:
        run_kwargs["SubnetId"] = subnet_id

    run_resp = None
    for attempt in range(1, 13):
        try:
            run_resp = ec2.run_instances(**run_kwargs)
            break
        except ClientError as exc:
            msg = str(exc)
            if (
                "Invalid IAM Instance Profile" in msg
                or ("InvalidParameterValue" in msg and "iamInstanceProfile" in msg)
            ) and attempt < 12:
                sleep_s = min(2**attempt + random.uniform(0, 1), 30)  # nosec B311
                log.info(
                    "  IAM instance profile not yet propagated; "
                    "retrying in %.1fs (attempt %d/12)...",
                    sleep_s,
                    attempt,
                )
                time.sleep(sleep_s)
                continue
            raise

    if run_resp is None:
        raise RuntimeError("Failed to launch instance after IAM profile propagation retries.")

    instance_id = run_resp["Instances"][0]["InstanceId"]
    log.info("  Instance launched: %s", instance_id)

    # If reusing an existing state volume, attach it explicitly.
    # AWS RunInstances BlockDeviceMappings does not support attaching by VolumeId.
    if reused_state_volume_id:
        log.info(
            "  Attaching reused state volume %s to %s...", reused_state_volume_id, instance_id
        )
        for _attempt in range(12):
            try:
                ec2.attach_volume(
                    VolumeId=reused_state_volume_id,
                    InstanceId=instance_id,
                    Device=cfg.state_volume_device_name,
                )
                break
            except ClientError as exc:
                msg = str(exc)
                if "IncorrectState" in msg or "is not 'available'" in msg:
                    time.sleep(2)
                    continue
                if "VolumeInUse" in msg or "already attached" in msg:
                    # Another concurrent provision likely attached the shared
                    # state volume first. Clean up this duplicate instance to
                    # avoid managed-instance tag drift.
                    log.warning(
                        "  State volume is already attached elsewhere; "
                        "terminating duplicate instance %s.",
                        instance_id,
                    )
                    with suppress(ClientError):
                        ec2.terminate_instances(InstanceIds=[instance_id])
                    raise RuntimeError(
                        "Provision aborted: reusable state volume is already attached to "
                        "another instance (likely concurrent provision). "
                        "Retry once only after confirming a single managed instance exists."
                    ) from exc
                raise
        else:
            raise RuntimeError(
                f"Failed to attach reused state volume {reused_state_volume_id} to {instance_id}."
            )

    # 5. Wait for running ------------------------------------------------------
    log.info("  Waiting for instance to reach 'running' state...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 40})

    # Refresh and ensure volume role tags are explicit (root/state)
    inst = _find_instance(ec2)
    if inst:
        bdm = inst.get("BlockDeviceMappings", [])
        root_vol_id = None
        state_vol_id = None
        for m in bdm:
            dev = m.get("DeviceName")
            vol_id = m.get("Ebs", {}).get("VolumeId")
            if not vol_id:
                continue
            if dev == "/dev/sda1":
                root_vol_id = vol_id
            if dev == cfg.state_volume_device_name:
                state_vol_id = vol_id

        if root_vol_id:
            _apply_tags(ec2, [root_vol_id], {VOLUME_ROLE_TAG_KEY: ROOT_VOLUME_ROLE})
        if state_vol_id:
            _apply_tags(ec2, [state_vol_id], {VOLUME_ROLE_TAG_KEY: STATE_VOLUME_ROLE})

    # Refresh instance data
    inst = _find_instance(ec2)
    public_ip = str((inst or {}).get("PublicIpAddress", "none"))

    log.info("  Instance running. Public IP: %s", public_ip)
    log.info("  Tailscale hostname will be: %s", cfg.tailscale_hostname)
    log.info("  Cloud-init is installing Docker, Tailscale, and Portainer.")
    log.info("  This takes 2-3 minutes. Run 'edc status' to check progress.")

    return {
        "instance_id": str(instance_id),
        "security_group_id": str(sg_id),
        "public_ip": public_ip,
    }


def start() -> str:
    """Start a stopped edcloud instance.

    Returns:
        The instance ID.

    Raises:
        RuntimeError: If no instance exists or it isn't in ``stopped`` state.
        TagDriftError: If orphaned managed resources are found instead.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        orphaned = _managed_orphan_report(ec2)
        if orphaned["security_groups"] or orphaned["volumes"]:
            raise TagDriftError(
                "No managed edcloud instance found, but orphaned managed resources exist.\n"
                f"{_orphaned_resources_text(orphaned)}\n"
                "Remediation: either clean them up manually or run `edc provision` "
                "to create a fresh managed instance."
            )
        raise RuntimeError("No edcloud instance found. Run 'edc provision' first.")

    iid = str(inst["InstanceId"])
    state = inst["State"]["Name"]

    if state == "running":
        log.info("Instance %s is already running.", iid)
        return iid

    if state != "stopped":
        raise RuntimeError(f"Instance {iid} is in state '{state}', cannot start.")

    log.info("Starting instance %s...", iid)
    ec2.start_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": 40})

    # Refresh for new IP
    inst = _find_instance(ec2)
    public_ip = str((inst or {}).get("PublicIpAddress", "none"))
    log.info("Instance running. Public IP: %s", public_ip)
    return iid


def stop() -> str:
    """Stop a running edcloud instance.

    Returns:
        The instance ID.

    Raises:
        RuntimeError: If no instance exists or it isn't in ``running`` state.
        TagDriftError: If orphaned managed resources are found instead.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        orphaned = _managed_orphan_report(ec2)
        if orphaned["security_groups"] or orphaned["volumes"]:
            raise TagDriftError(
                "No managed edcloud instance found, but orphaned managed resources exist.\n"
                f"{_orphaned_resources_text(orphaned)}\n"
                "Remediation: clean up stale resources or reprovision."
            )
        raise RuntimeError("No edcloud instance found.")

    iid = str(inst["InstanceId"])
    state = inst["State"]["Name"]

    if state == "stopped":
        log.info("Instance %s is already stopped.", iid)
        return iid

    if state != "running":
        raise RuntimeError(f"Instance {iid} is in state '{state}', cannot stop.")

    log.info("Stopping instance %s...", iid)
    ec2.stop_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    log.info("Instance stopped. EBS volume preserved.")
    return iid


def status() -> dict[str, Any]:
    """Return current instance status.

    Returns:
        Dict with at least ``exists: bool``.  When the instance exists the
        dict also contains ``instance_id``, ``state``, ``instance_type``,
        ``public_ip``, ``launch_time``, ``volumes``, and ``cost_estimate``.
        When it does not exist, an ``orphaned_resources`` summary is included.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)

    if not inst:
        orphaned = _managed_orphan_report(ec2)
        return {
            "exists": False,
            "orphaned_resources": orphaned,
        }

    iid = inst["InstanceId"]
    state = inst["State"]["Name"]
    launch_time = inst.get("LaunchTime")
    public_ip = inst.get("PublicIpAddress")
    instance_type = inst.get("InstanceType", "unknown")

    # Get volume info (single API call for all attached volumes)
    vol_ids = get_volume_ids(inst)
    volumes = []
    if vol_ids:
        vol_resp = ec2.describe_volumes(VolumeIds=vol_ids)
        for v in vol_resp.get("Volumes", []):
            volumes.append(
                {
                    "volume_id": v["VolumeId"],
                    "size_gb": v["Size"],
                    "type": v["VolumeType"],
                    "state": v["State"],
                }
            )

    # Orphaned managed volumes (available, not attached to this instance)
    orphaned_vol_resp = ec2.describe_volumes(
        Filters=[
            {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]},
            {"Name": "status", "Values": ["available"]},
        ]
    )
    orphaned_volumes = [v["VolumeId"] for v in orphaned_vol_resp.get("Volumes", [])]

    # Cost estimate
    hourly_rate = HOURLY_RATES.get(instance_type, 0.0)
    compute_monthly = hourly_rate * DEFAULT_HOURS_PER_DAY * 30
    storage_monthly = sum(v["size_gb"] for v in volumes) * EBS_MONTHLY_RATE_PER_GB
    total_monthly = compute_monthly + storage_monthly

    result: dict[str, Any] = {
        "exists": True,
        "instance_id": iid,
        "state": state,
        "instance_type": instance_type,
        "public_ip": public_ip,
        "launch_time": str(launch_time) if launch_time else None,
        "volumes": volumes,
        "orphaned_volumes": orphaned_volumes,
        "cost_estimate": {
            "compute_monthly": round(compute_monthly, 2),
            "storage_monthly": round(storage_monthly, 2),
            "total_monthly": round(total_monthly, 2),
            "note": f"Assumes {DEFAULT_HOURS_PER_DAY}hrs/day runtime",
        },
    }
    return result


def destroy() -> None:
    """Terminate the edcloud instance and clean up its security group and IAM.

    The root EBS volume is deleted automatically on termination
    (``DeleteOnTermination=True``).  The state volume survives and is
    reattached on the next provision.
    """
    ec2 = _ec2_client()
    inst = _find_instance(ec2)

    if not inst:
        orphaned = _managed_orphan_report(ec2)
        if orphaned["security_groups"] or orphaned["volumes"]:
            raise TagDriftError(
                "No managed edcloud instance found, but orphaned managed resources exist.\n"
                f"{_orphaned_resources_text(orphaned)}\n"
                "Remediation: delete stale resources manually in AWS or reprovision "
                "and then run `edc destroy` again."
            )
        log.info("No edcloud instance found. Nothing to destroy.")
        return

    iid = inst["InstanceId"]

    # Terminate instance
    log.info("Terminating instance %s...", iid)
    ec2.terminate_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_terminated")
    waiter.wait(InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    log.info("Instance terminated.")

    # Clean up security group (may fail if other resources use it)
    sg_id = _find_security_group(ec2)
    if sg_id:
        try:
            # Wait briefly for ENI detachment
            time.sleep(5)
            ec2.delete_security_group(GroupId=sg_id)
            log.info("Deleted security group: %s", sg_id)
        except ClientError as exc:
            log.warning("Could not delete security group %s: %s", sg_id, exc)
            log.warning("You may need to delete it manually after ENIs are released.")

    # Clean up IAM instance profile
    delete_instance_profile()

    # Report surviving managed volumes (state volume is expected; others are not)
    surviving = list_managed_volumes(ec2)
    for v in surviving:
        role = tag_value(v.get("Tags", []), VOLUME_ROLE_TAG_KEY) or "unknown"
        if role == STATE_VOLUME_ROLE:
            log.info("State volume preserved: %s  %sGB", v["VolumeId"], v["Size"])
        else:
            log.warning(
                "Unexpected orphaned volume: %s  %sGB  role=%s (delete manually or run cleanup)",
                v["VolumeId"],
                v["Size"],
                role,
            )


def resize(
    instance_type: str | None = None,
    volume_size_gb: int | None = None,
    state_volume_size_gb: int | None = None,
) -> dict[str, Any]:
    """Resize the edcloud instance type and/or EBS volumes in place.

    Instance type change requires a stop → modify → start cycle.
    Volume size changes (expand only) are applied online without a restart.

    Args:
        instance_type: New EC2 instance type (e.g. ``"t3a.medium"``).
        volume_size_gb: New root volume size in GiB (must be >= current size).
        state_volume_size_gb: New state volume size in GiB (must be >= current size).

    Returns:
        Dict summarising the changes applied.

    Raises:
        RuntimeError: If no managed instance exists or an operation fails.
        ValueError: If no resize parameters are specified.
    """
    if instance_type is None and volume_size_gb is None and state_volume_size_gb is None:
        raise ValueError(
            "At least one of --instance-type, --volume-size, or "
            "--state-volume-size must be specified."
        )

    ec2 = _ec2_client()
    inst = _find_instance(ec2)
    if not inst:
        raise RuntimeError("No edcloud instance found. Run 'edc provision' first.")

    iid = inst["InstanceId"]
    current_state = inst["State"]["Name"]
    result: dict[str, Any] = {"instance_id": iid}

    # ------------------------------------------------------------------
    # Volume resizes (online — no instance stop required)
    # ------------------------------------------------------------------
    if volume_size_gb is not None or state_volume_size_gb is not None:
        bdm = inst.get("BlockDeviceMappings", [])
        for mapping in bdm:
            dev = mapping.get("DeviceName")
            vol_id = mapping.get("Ebs", {}).get("VolumeId")
            if not vol_id:
                continue

            vol_resp = ec2.describe_volumes(VolumeIds=[vol_id])
            vol_info = vol_resp.get("Volumes", [{}])[0]
            current_size = vol_info.get("Size", 0)
            tags = {t["Key"]: t["Value"] for t in vol_info.get("Tags", [])}
            role = tags.get(VOLUME_ROLE_TAG_KEY)

            if dev == "/dev/sda1" and volume_size_gb is not None:
                if volume_size_gb <= current_size:
                    log.info(
                        "  Root volume %s: requested %dGB <= current %dGB — skipping.",
                        vol_id,
                        volume_size_gb,
                        current_size,
                    )
                else:
                    log.info(
                        "  Expanding root volume %s: %dGB → %dGB",
                        vol_id,
                        current_size,
                        volume_size_gb,
                    )
                    ec2.modify_volume(VolumeId=vol_id, Size=volume_size_gb)
                    result["root_volume_id"] = vol_id
                    result["root_volume_new_size_gb"] = volume_size_gb
                    log.info(
                        "    Volume modification initiated (async). May take several minutes.\n"
                        "    Poll: aws ec2 describe-volumes-modifications --volume-ids %s\n"
                        "    After completion, find device with: lsblk\n"
                        "      Root volume (partitioned): "
                        "sudo growpart <dev> 1 && sudo resize2fs <dev>p1",
                        vol_id,
                    )

            elif role == STATE_VOLUME_ROLE and state_volume_size_gb is not None:
                if state_volume_size_gb <= current_size:
                    log.info(
                        "  State volume %s: requested %dGB <= current %dGB — skipping.",
                        vol_id,
                        state_volume_size_gb,
                        current_size,
                    )
                else:
                    log.info(
                        "  Expanding state volume %s: %dGB → %dGB",
                        vol_id,
                        current_size,
                        state_volume_size_gb,
                    )
                    ec2.modify_volume(VolumeId=vol_id, Size=state_volume_size_gb)
                    result["state_volume_id"] = vol_id
                    result["state_volume_new_size_gb"] = state_volume_size_gb
                    log.info(
                        "    Volume modification initiated (async). May take several minutes.\n"
                        "    Poll: aws ec2 describe-volumes-modifications --volume-ids %s\n"
                        "    After completion, find device with: lsblk\n"
                        "      State volume (no partition): sudo resize2fs <dev>",
                        vol_id,
                    )

    # ------------------------------------------------------------------
    # Instance type change (requires stop → modify → start)
    # ------------------------------------------------------------------
    if instance_type is not None:
        current_type = inst.get("InstanceType")
        if instance_type == current_type:
            log.info("  Instance type is already %s — skipping.", current_type)
        else:
            # Stop if running
            stopped_here = False
            if current_state == "running":
                log.info("  Stopping instance %s to change type...", iid)
                ec2.stop_instances(InstanceIds=[iid])
                waiter = ec2.get_waiter("instance_stopped")
                waiter.wait(InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
                log.info("  Instance stopped.")
                stopped_here = True
            elif current_state != "stopped":
                raise RuntimeError(
                    f"Instance {iid} is in state '{current_state}'; "
                    "cannot change instance type unless running or stopped."
                )

            log.info("  Changing instance type: %s → %s", current_type, instance_type)
            try:
                ec2.modify_instance_attribute(
                    InstanceId=iid,
                    InstanceType={"Value": instance_type},
                )
            except Exception:
                if stopped_here:
                    log.warning(
                        "  modify_instance_attribute failed; restarting instance %s"
                        " before re-raising...",
                        iid,
                    )
                    with suppress(ClientError):
                        ec2.start_instances(InstanceIds=[iid])
                raise
            result["instance_type_old"] = str(current_type)
            result["instance_type_new"] = instance_type

            # Restart if we stopped it
            if stopped_here:
                log.info("  Restarting instance %s...", iid)
                ec2.start_instances(InstanceIds=[iid])
                waiter = ec2.get_waiter("instance_running")
                waiter.wait(InstanceIds=[iid], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
                # Refresh for new IP
                refreshed = _find_instance(ec2)
                public_ip = str((refreshed or {}).get("PublicIpAddress", "none"))
                log.info("  Instance running. Public IP: %s", public_ip)
                result["public_ip"] = public_ip

    return result
