"""EC2 lifecycle: provision, start, stop, status, destroy.

Resources are tracked by AWS tags (no local state file needed).
Tag ``edcloud:managed = true`` identifies all managed resources.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from edcloud.aws_check import get_region
from edcloud.config import (
    DEFAULT_HOURS_PER_DAY,
    EBS_MONTHLY_RATE_PER_GB,
    HOURLY_RATES,
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    NAME_TAG,
    SECURITY_GROUP_DESC,
    SECURITY_GROUP_NAME,
    InstanceConfig,
    get_volume_ids,
    has_managed_tag,
    managed_filter,
)
from edcloud.iam import delete_instance_profile, ensure_instance_profile

_USER_DATA_PATH = Path(__file__).resolve().parent.parent / "cloud-init" / "user-data.yaml"
_ACTIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped"]


class TagDriftError(RuntimeError):
    """Tag-based discovery invariants were violated."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ec2_client() -> Any:
    return boto3.client("ec2")


def _ec2_resource() -> Any:
    return boto3.resource("ec2")


def _ssm_client() -> Any:
    return boto3.client("ssm")


# Backward-compat aliases for callers that import the underscore-prefixed names
_managed_filter = managed_filter
_has_managed_tag = has_managed_tag


def _instance_state_filter() -> dict[str, Any]:
    return {"Name": "instance-state-name", "Values": _ACTIVE_INSTANCE_STATES}


def _instance_summary(instances: list[dict[str, Any]]) -> str:
    return ", ".join(f"{i['InstanceId']} ({i['State']['Name']})" for i in instances)


def _list_instances(client: Any, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resp = client.describe_instances(Filters=[*filters, _instance_state_filter()])
    instances: list[dict[str, Any]] = []
    for reservation in resp.get("Reservations", []):
        instances.extend(reservation.get("Instances", []))
    return instances


def _validate_instance_volume_tags(client: Any, instance: dict[str, Any]) -> None:
    """Ensure attached volumes keep the managed tag."""
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
    """Return orphaned managed resources outside an active managed instance."""
    report: dict[str, list[str]] = {"security_groups": [], "volumes": []}

    sg_resp = client.describe_security_groups(Filters=managed_filter())
    for sg in sg_resp.get("SecurityGroups", []):
        group_name = sg.get("GroupName")
        if group_name == SECURITY_GROUP_NAME:
            report["security_groups"].append(sg["GroupId"])

    vol_resp = client.describe_volumes(Filters=managed_filter())
    for volume in vol_resp.get("Volumes", []):
        if volume.get("State") == "available":
            report["volumes"].append(volume["VolumeId"])

    return report


def _orphaned_resources_text(report: dict[str, list[str]]) -> str:
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


def _find_instance(client: Any) -> dict[str, Any] | None:
    """Find the single edcloud-managed instance (any state except terminated)."""
    managed_instances = _list_instances(client, _managed_filter())
    if len(managed_instances) > 1:
        raise TagDriftError(
            "Tag drift detected: multiple managed instances found: "
            f"{_instance_summary(managed_instances)}\n"
            "Remediation: keep only one managed instance. Terminate extras or remove "
            f"`{MANAGER_TAG_KEY}` from resources you no longer want managed."
        )

    if not managed_instances:
        named_instances = _list_instances(client, [{"Name": "tag:Name", "Values": [NAME_TAG]}])
        untagged_named = [i for i in named_instances if not _has_managed_tag(i.get("Tags", []))]
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
    """Return the edcloud security group ID, or None."""
    try:
        resp = client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SECURITY_GROUP_NAME]}]
        )
    except ClientError:
        return None

    groups = resp.get("SecurityGroups", [])
    managed_groups = [g for g in groups if _has_managed_tag(g.get("Tags", []))]
    unmanaged_groups = [g for g in groups if not _has_managed_tag(g.get("Tags", []))]

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
    """Resolve an AMI ID from an SSM public parameter."""
    ssm = _ssm_client()
    try:
        resp = ssm.get_parameter(Name=ssm_parameter)
        return resp["Parameter"]["Value"]  # type: ignore[no-any-return]
    except ClientError as exc:
        # Fallback: if SSM parameter path doesn't work, use a direct lookup
        if "ParameterNotFound" in str(exc):
            print(f"  SSM parameter {ssm_parameter} not found, falling back to AMI search...")
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
                return images[0]["ImageId"]  # type: ignore[no-any-return]
        raise


def _get_default_vpc_id(client: Any) -> str:
    """Get the default VPC ID."""
    resp = client.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        raise RuntimeError("No default VPC found. Create one or specify a VPC ID.")
    return vpcs[0]["VpcId"]  # type: ignore[no-any-return]


def _get_aws_region() -> str:
    """Get the current AWS region from the session."""
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
) -> None:
    """Validate user-data template inputs to prevent injection attacks.

    Raises ValueError if any input contains dangerous characters or invalid format.
    """
    import re

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


def _render_user_data(
    tailscale_auth_key_ssm_parameter: str,
    tailscale_hostname: str,
    aws_region: str,
) -> str:
    """Read cloud-init template and interpolate variables."""
    _validate_user_data_inputs(
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
        aws_region=aws_region,
    )
    template = _USER_DATA_PATH.read_text()
    return (
        template.replace("${TAILSCALE_AUTH_KEY_SSM_PARAMETER}", tailscale_auth_key_ssm_parameter)
        .replace("${TAILSCALE_HOSTNAME}", tailscale_hostname)
        .replace("${AWS_REGION}", aws_region)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def provision(cfg: InstanceConfig) -> dict[str, str]:
    """Create the edcloud instance from scratch.

    The Tailscale auth key is fetched from SSM at boot time by the instance.

    Returns dict with instance_id, security_group_id, public_ip (if any).
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

    print("Provisioning edcloud instance...")

    # 1. Security group -------------------------------------------------------
    sg_id = _find_security_group(ec2)
    if sg_id:
        print(f"  Security group exists: {sg_id}")
    else:
        vpc_id = _get_default_vpc_id(ec2)
        resp = ec2.create_security_group(
            GroupName=SECURITY_GROUP_NAME,
            Description=SECURITY_GROUP_DESC,
            VpcId=vpc_id,
        )
        sg_id = resp["GroupId"]
        _apply_tags(ec2, [sg_id], cfg.tags)

        # Revoke the default "allow all outbound" rule? No — we need outbound
        # for apt, Docker pulls, Tailscale coordination, etc.
        # No inbound rules: all access comes via Tailscale tunnel.
        print(f"  Created security group: {sg_id} (no inbound rules)")

    # 2. IAM instance profile -------------------------------------------------
    profile_arn = ensure_instance_profile(cfg.tags)
    print(f"  Instance profile: {profile_arn}")

    # 3. Resolve AMI -----------------------------------------------------------
    ami_id = _resolve_ami(cfg.ami_ssm_parameter)
    print(f"  AMI: {ami_id}")

    # 4. User-data script ------------------------------------------------------
    aws_region = _get_aws_region()
    user_data = _render_user_data(
        tailscale_auth_key_ssm_parameter=cfg.tailscale_auth_key_ssm_parameter,
        tailscale_hostname=cfg.tailscale_hostname,
        aws_region=aws_region,
    )
    print(f"  Tailscale auth key will be fetched from SSM: {cfg.tailscale_auth_key_ssm_parameter}")

    # 5. Launch instance -------------------------------------------------------
    run_resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=cfg.instance_type,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Arn": profile_arn},
        UserData=user_data,
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": cfg.volume_size_gb,
                    "VolumeType": cfg.volume_type,
                    "DeleteOnTermination": False,
                    "Encrypted": True,
                },
            },
            {
                "DeviceName": cfg.state_volume_device_name,
                "Ebs": {
                    "VolumeSize": cfg.state_volume_size_gb,
                    "VolumeType": cfg.state_volume_type,
                    "DeleteOnTermination": False,
                    "Encrypted": True,
                },
            },
        ],
        TagSpecifications=[
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
        MetadataOptions={
            "HttpTokens": "required",  # IMDSv2 only
            "HttpEndpoint": "enabled",
            "HttpPutResponseHopLimit": 1,  # Prevent containers from reaching IMDS
        },
    )

    instance_id = run_resp["Instances"][0]["InstanceId"]
    print(f"  Instance launched: {instance_id}")

    # 6. Wait for running ------------------------------------------------------
    print("  Waiting for instance to reach 'running' state...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])

    # Refresh instance data
    inst = _find_instance(ec2)
    public_ip = (inst or {}).get("PublicIpAddress", "none")

    print(f"  Instance running. Public IP: {public_ip}")
    print(f"  Tailscale hostname will be: {cfg.tailscale_hostname}")
    print()
    print("  Cloud-init is installing Docker, Tailscale, and Portainer.")
    print("  This takes 2-3 minutes. Run 'edc status' to check progress.")

    return {
        "instance_id": instance_id,
        "security_group_id": sg_id,
        "public_ip": public_ip,
    }


def start() -> str:
    """Start a stopped edcloud instance. Returns instance ID."""
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

    iid = inst["InstanceId"]
    state = inst["State"]["Name"]

    if state == "running":
        print(f"Instance {iid} is already running.")
        return iid

    if state != "stopped":
        raise RuntimeError(f"Instance {iid} is in state '{state}', cannot start.")

    print(f"Starting instance {iid}...")
    ec2.start_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[iid])

    # Refresh for new IP
    inst = _find_instance(ec2)
    public_ip = (inst or {}).get("PublicIpAddress", "none")
    print(f"Instance running. Public IP: {public_ip}")
    return iid


def stop() -> str:
    """Stop a running edcloud instance. Returns instance ID."""
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

    iid = inst["InstanceId"]
    state = inst["State"]["Name"]

    if state == "stopped":
        print(f"Instance {iid} is already stopped.")
        return iid

    if state != "running":
        raise RuntimeError(f"Instance {iid} is in state '{state}', cannot stop.")

    print(f"Stopping instance {iid}...")
    ec2.stop_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[iid])
    print("Instance stopped. EBS volume preserved.")
    return iid


def status() -> dict[str, Any]:
    """Return current instance status as a dict."""
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

    # Get volume info
    volumes = []
    for bdm in inst.get("BlockDeviceMappings", []):
        vol_id = bdm.get("Ebs", {}).get("VolumeId")
        if vol_id:
            vol_resp = ec2.describe_volumes(VolumeIds=[vol_id])
            for v in vol_resp.get("Volumes", []):
                volumes.append(
                    {
                        "volume_id": v["VolumeId"],
                        "size_gb": v["Size"],
                        "type": v["VolumeType"],
                        "state": v["State"],
                    }
                )

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
        "cost_estimate": {
            "compute_monthly": round(compute_monthly, 2),
            "storage_monthly": round(storage_monthly, 2),
            "total_monthly": round(total_monthly, 2),
            "note": f"Assumes {DEFAULT_HOURS_PER_DAY}hrs/day runtime",
        },
    }
    return result


def destroy(force: bool = False) -> None:
    """Terminate the edcloud instance and clean up the security group.

    The EBS volume survives (DeleteOnTermination=false).
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
        print("No edcloud instance found. Nothing to destroy.")
        return

    iid = inst["InstanceId"]
    state = inst["State"]["Name"]

    if not force:
        print(f"This will TERMINATE instance {iid} ({state}).")
        print("The EBS volume will be preserved (detached, not deleted).")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            return

    # Terminate instance
    print(f"Terminating instance {iid}...")
    ec2.terminate_instances(InstanceIds=[iid])

    waiter = ec2.get_waiter("instance_terminated")
    waiter.wait(InstanceIds=[iid])
    print("Instance terminated.")

    # Clean up security group (may fail if other resources use it)
    sg_id = _find_security_group(ec2)
    if sg_id:
        try:
            # Wait briefly for ENI detachment
            time.sleep(5)
            ec2.delete_security_group(GroupId=sg_id)
            print(f"Deleted security group: {sg_id}")
        except ClientError as exc:
            print(f"Could not delete security group {sg_id}: {exc}")
            print("You may need to delete it manually after ENIs are released.")

    # Clean up IAM instance profile
    print()
    delete_instance_profile()

    # List orphaned volumes
    vol_resp = ec2.describe_volumes(Filters=_managed_filter())
    orphaned = vol_resp.get("Volumes", [])
    if orphaned:
        print()
        print("Preserved EBS volumes (delete manually if not needed):")
        for v in orphaned:
            print(f"  {v['VolumeId']}  {v['Size']}GB  {v['State']}")
