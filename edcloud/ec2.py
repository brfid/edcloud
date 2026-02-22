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

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    SECURITY_GROUP_DESC,
    SECURITY_GROUP_NAME,
    InstanceConfig,
)

_USER_DATA_PATH = Path(__file__).resolve().parent.parent / "cloud-init" / "user-data.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ec2_client() -> Any:
    return boto3.client("ec2")


def _ec2_resource() -> Any:
    return boto3.resource("ec2")


def _ssm_client() -> Any:
    return boto3.client("ssm")


def _managed_filter() -> list[dict[str, Any]]:
    """Tag filter that matches edcloud-managed resources."""
    return [{"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]}]


def _apply_tags(client: Any, resource_ids: list[str], tags: dict[str, str]) -> None:
    """Apply tags to one or more resources."""
    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
    client.create_tags(Resources=resource_ids, Tags=tag_list)


def _find_instance(client: Any) -> dict[str, Any] | None:
    """Find the single edcloud-managed instance (any state except terminated)."""
    resp = client.describe_instances(
        Filters=[
            *_managed_filter(),
            {
                "Name": "instance-state-name",
                "Values": [
                    "pending",
                    "running",
                    "stopping",
                    "stopped",
                ],
            },
        ]
    )
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            return inst  # type: ignore[no-any-return]
    return None


def _find_security_group(client: Any) -> str | None:
    """Return the edcloud security group ID, or None."""
    try:
        resp = client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [SECURITY_GROUP_NAME]},
                *_managed_filter(),
            ]
        )
    except ClientError:
        return None
    groups = resp.get("SecurityGroups", [])
    return groups[0]["GroupId"] if groups else None


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
                Owners=["099720109477"],  # Canonical
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


def _render_user_data(tailscale_auth_key: str, tailscale_hostname: str) -> str:
    """Read cloud-init template and interpolate variables."""
    template = _USER_DATA_PATH.read_text()
    return template.replace("${TAILSCALE_AUTH_KEY}", tailscale_auth_key).replace(
        "${TAILSCALE_HOSTNAME}", tailscale_hostname
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def provision(cfg: InstanceConfig, tailscale_auth_key: str) -> dict[str, str]:
    """Create the edcloud instance from scratch.

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
            "Run 'edcloud destroy' first if you want to reprovision."
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

    # 2. Resolve AMI -----------------------------------------------------------
    ami_id = _resolve_ami(cfg.ami_ssm_parameter)
    print(f"  AMI: {ami_id}")

    # 3. User-data script ------------------------------------------------------
    user_data = _render_user_data(tailscale_auth_key, cfg.tailscale_hostname)

    # 4. Launch instance -------------------------------------------------------
    run_resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=cfg.instance_type,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[sg_id],
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
        },
    )

    instance_id = run_resp["Instances"][0]["InstanceId"]
    print(f"  Instance launched: {instance_id}")

    # 5. Wait for running ------------------------------------------------------
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
    print("  This takes 2-3 minutes. Run 'edcloud status' to check progress.")

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
        raise RuntimeError("No edcloud instance found. Run 'edcloud provision' first.")

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
        return {"exists": False}

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
    hours_per_day = 4
    hourly_rate = {"t3a.medium": 0.0376, "t3a.small": 0.0188}.get(instance_type, 0.0)
    compute_monthly = hourly_rate * hours_per_day * 30
    storage_monthly = sum(v["size_gb"] for v in volumes) * 0.08
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
            "note": f"Assumes {hours_per_day}hrs/day runtime",
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

    # List orphaned volumes
    vol_resp = ec2.describe_volumes(Filters=_managed_filter())
    orphaned = vol_resp.get("Volumes", [])
    if orphaned:
        print()
        print("Preserved EBS volumes (delete manually if not needed):")
        for v in orphaned:
            print(f"  {v['VolumeId']}  {v['Size']}GB  {v['State']}")
