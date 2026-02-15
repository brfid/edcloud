"""edcloud CLI — manage your personal cloud lab."""

from __future__ import annotations

import functools
import json
import os
import shlex
import subprocess

import boto3
import click
from botocore.exceptions import ClientError

from edcloud import ec2, snapshot, tailscale
from edcloud.aws_check import check_aws_credentials, get_region
from edcloud.config import DEFAULT_TAILSCALE_HOSTNAME, InstanceConfig


def require_aws_creds(func):
    """Decorator: verify AWS credentials before running command."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        valid, message = check_aws_credentials()
        if not valid:
            click.echo(f"AWS credentials error: {message}", err=True)
            raise SystemExit(1)
        region = get_region()
        if not region:
            click.echo("Warning: No AWS region configured. Using default.", err=True)
        try:
            return func(*args, **kwargs)
        except RuntimeError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(1) from exc

    return wrapper


@click.group()
@click.version_option(package_name="edcloud")
def main() -> None:
    """Manage your personal cloud lab on AWS."""


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------
@main.command()
@click.option(
    "--instance-type",
    default=InstanceConfig.instance_type,
    show_default=True,
    help="EC2 instance type.",
)
@click.option(
    "--volume-size",
    default=InstanceConfig.volume_size_gb,
    type=int,
    show_default=True,
    help="Root EBS volume size in GB.",
)
@click.option(
    "--state-volume-size",
    default=InstanceConfig.state_volume_size_gb,
    type=int,
    show_default=True,
    help="Persistent state EBS volume size in GB (mounted at /opt/edcloud/state).",
)
@click.option(
    "--tailscale-hostname",
    default=DEFAULT_TAILSCALE_HOSTNAME,
    show_default=True,
    help="Tailscale MagicDNS hostname.",
)
@click.option(
    "--tailscale-auth-key",
    envvar="TAILSCALE_AUTH_KEY",
    help="Tailscale auth key (or set TAILSCALE_AUTH_KEY env var).",
)
@click.option(
    "--tailscale-auth-key-ssm-parameter",
    default=None,
    help="SSM parameter name containing Tailscale auth key (SecureString supported).",
)
@require_aws_creds
def provision(
    instance_type: str,
    volume_size: int,
    state_volume_size: int,
    tailscale_hostname: str,
    tailscale_auth_key: str | None,
    tailscale_auth_key_ssm_parameter: str | None,
) -> None:
    """Create the edcloud EC2 instance from scratch."""
    if not tailscale_auth_key and tailscale_auth_key_ssm_parameter:
        try:
            ssm = boto3.client("ssm")
            resp = ssm.get_parameter(Name=tailscale_auth_key_ssm_parameter, WithDecryption=True)
            tailscale_auth_key = resp["Parameter"]["Value"]
        except ClientError as exc:
            click.echo(
                "Error: could not read Tailscale auth key from SSM parameter "
                f"'{tailscale_auth_key_ssm_parameter}': {exc}",
                err=True,
            )
            raise SystemExit(1) from exc

    if not tailscale_auth_key:
        click.echo("Error: Tailscale auth key required.", err=True)
        click.echo("  Set TAILSCALE_AUTH_KEY or pass --tailscale-auth-key.", err=True)
        click.echo(
            "  Or pass --tailscale-auth-key-ssm-parameter /path/to/parameter.",
            err=True,
        )
        click.echo("  Generate one at: https://login.tailscale.com/admin/settings/keys", err=True)
        raise SystemExit(1)

    cfg = InstanceConfig(
        instance_type=instance_type,
        volume_size_gb=volume_size,
        state_volume_size_gb=state_volume_size,
        tailscale_hostname=tailscale_hostname,
    )
    result = ec2.provision(cfg, tailscale_auth_key)
    click.echo()
    click.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# up / down
# ---------------------------------------------------------------------------
@main.command()
@require_aws_creds
def up() -> None:
    """Start the edcloud instance."""
    ec2.start()
    ts_ip = tailscale.get_tailscale_ip(DEFAULT_TAILSCALE_HOSTNAME)
    if ts_ip:
        click.echo(f"Tailscale IP: {ts_ip}")
    else:
        click.echo(
            f"Tailscale peer '{DEFAULT_TAILSCALE_HOSTNAME}' not yet visible. "
            "It may take a minute after boot."
        )


@require_aws_creds
@main.command()
def down() -> None:
    """Stop the edcloud instance."""
    ec2.stop()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
@main.command()
@require_aws_creds
def status() -> None:
    """Show instance state, IPs, and cost estimate."""
    info = ec2.status()

    if not info.get("exists"):
        click.echo("No edcloud instance found. Run 'edc provision' to create one.")
        orphaned = info.get("orphaned_resources", {})
        security_groups = orphaned.get("security_groups", [])
        volumes = orphaned.get("volumes", [])
        if security_groups or volumes:
            click.echo()
            click.echo("Detected orphaned managed resources:")
            if security_groups:
                click.echo(f"  Security groups: {', '.join(security_groups)}")
            if volumes:
                click.echo(f"  Volumes (available): {', '.join(volumes)}")
            click.echo(
                "Remediation: clean up stale resources or reprovision and reattach data as needed."
            )
        return

    click.echo(f"Instance:  {info['instance_id']}")
    click.echo(f"State:     {info['state']}")
    click.echo(f"Type:      {info['instance_type']}")

    if info.get("public_ip"):
        click.echo(f"Public IP: {info['public_ip']}")

    # Tailscale
    ts_ip = tailscale.get_tailscale_ip(DEFAULT_TAILSCALE_HOSTNAME)
    if ts_ip:
        click.echo(f"Tailscale: {ts_ip} ({DEFAULT_TAILSCALE_HOSTNAME})")
        reachable = tailscale.is_reachable(DEFAULT_TAILSCALE_HOSTNAME)
        click.echo(f"Reachable: {'yes' if reachable else 'no'}")
    else:
        click.echo("Tailscale: not visible on tailnet")

    if info.get("launch_time"):
        click.echo(f"Launched:  {info['launch_time']}")

    # Volumes
    for vol in info.get("volumes", []):
        click.echo(
            f"Volume:    {vol['volume_id']}  {vol['size_gb']}GB {vol['type']}  ({vol['state']})"
        )

    # Cost
    cost = info.get("cost_estimate", {})
    if cost:
        click.echo()
        click.echo(f"Estimated monthly cost ({cost.get('note', '')}):")
        click.echo(f"  Compute: ${cost.get('compute_monthly', 0):.2f}")
        click.echo(f"  Storage: ${cost.get('storage_monthly', 0):.2f}")
        click.echo(f"  Total:   ${cost.get('total_monthly', 0):.2f}")


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------
@main.command()
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
@require_aws_creds
def destroy(force: bool) -> None:
    """Terminate the instance and clean up. EBS volume is preserved."""
    ec2.destroy(force=force)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
@main.command("snapshot")
@click.option("--list", "list_", is_flag=True, help="List existing snapshots.")
@click.option("--description", "-d", default=None, help="Snapshot description.")
@click.option("--prune", is_flag=True, help="Prune old periodic snapshots per retention settings.")
@click.option(
    "--keep-weekly",
    default=8,
    type=int,
    show_default=True,
    help="Weekly snapshots to keep when pruning.",
)
@click.option(
    "--keep-monthly",
    default=3,
    type=int,
    show_default=True,
    help="Monthly snapshots to keep when pruning.",
)
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview prune deletions or apply them.",
)
@require_aws_creds
def snapshot_cmd(
    list_: bool,
    description: str | None,
    prune: bool,
    keep_weekly: int,
    keep_monthly: int,
    dry_run: bool,
) -> None:
    """Create or list EBS snapshots."""
    modes_selected = int(list_) + int(prune)
    if modes_selected > 1:
        click.echo("Error: use either --list or --prune (not both).", err=True)
        raise SystemExit(1)
    if (list_ or prune) and description is not None:
        click.echo("Error: --description is only valid when creating snapshots.", err=True)
        raise SystemExit(1)

    if list_:
        snaps = snapshot.list_snapshots()
        if not snaps:
            click.echo("No edcloud snapshots found.")
            return
        click.echo(f"{'ID':<25} {'Size':>5} {'State':<12} {'Started':<20} {'Description'}")
        click.echo("-" * 90)
        for s in snaps:
            click.echo(
                f"{s['snapshot_id']:<25} {s['size_gb']:>4}GB {s['state']:<12} "
                f"{s['start_time'][:19]:<20} {s['description']}"
            )
    elif prune:
        result = snapshot.prune_snapshots(
            keep_weekly=keep_weekly,
            keep_monthly=keep_monthly,
            dry_run=dry_run,
        )
        if result["delete_count"] == 0:
            click.echo("No snapshots matched prune criteria.")
            return
        click.echo(
            f"{'Would delete' if dry_run else 'Deleting'} {result['delete_count']} snapshots "
            f"(weekly keep={keep_weekly}, monthly keep={keep_monthly}):"
        )
        for snap in result["to_delete"]:
            click.echo(f"  {snap['snapshot_id']}  {snap['description']}")
        if dry_run:
            click.echo("Re-run with --apply to delete these snapshots.")
    else:
        snapshot.create_snapshot(description)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
@main.command("verify")
@click.option("--user", default="ubuntu", show_default=True, help="SSH user.")
@click.option(
    "--public-ip",
    is_flag=True,
    help="Use public IP for checks instead of Tailscale.",
)
@click.option("--json-output", is_flag=True, help="Emit verification results as JSON.")
@require_aws_creds
def verify_cmd(user: str, public_ip: bool, json_output: bool) -> None:
    """Run fresh-reprovision verification checks."""
    info = ec2.status()
    if not info.get("exists"):
        raise RuntimeError("No edcloud instance found. Run 'edc provision' first.")
    if info.get("state") != "running":
        raise RuntimeError(f"Instance is {info.get('state')}, must be running for verification.")

    target = ""
    ssh_base: list[str] = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=12",
    ]
    if public_ip:
        target = str(info.get("public_ip") or "")
        if not target:
            raise RuntimeError(
                "No public IP available. Start without --public-ip or assign a public IP."
            )
        ssh_base.append(f"{user}@{target}")
    else:
        ts_ip = tailscale.get_tailscale_ip(DEFAULT_TAILSCALE_HOSTNAME)
        if not ts_ip:
            raise RuntimeError(
                "Tailscale IP not found. Verify local tailnet connectivity or use --public-ip."
            )
        target = ts_ip
        ssh_base.extend(["-o", "ProxyCommand=none", f"{user}@{target}"])

    checks: list[tuple[str, str]] = [
        ("cloud-init completion marker", "test -f /tmp/edcloud-ready"),
        ("docker service active", "systemctl is-active --quiet docker"),
        ("portainer container running", "docker ps --format '{{.Names}}' | grep -qx portainer"),
        ("compose directory exists", "test -d /opt/edcloud/compose"),
        ("state directory exists", "test -d /opt/edcloud/state"),
        ("state directory is mounted", "mountpoint -q /opt/edcloud/state"),
        ("state directory writable", "test -w /opt/edcloud/state"),
    ]

    results: list[dict[str, str | bool]] = []
    for check_name, remote_cmd in checks:
        remote = f"bash -lc {shlex.quote(remote_cmd)}"
        cmd = [*ssh_base, remote]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append({"check": check_name, "ok": False, "detail": str(exc)})
            continue

        detail = proc.stderr.strip() or proc.stdout.strip()
        results.append({"check": check_name, "ok": proc.returncode == 0, "detail": detail})

    success = all(bool(r["ok"]) for r in results)

    if json_output:
        click.echo(
            json.dumps(
                {
                    "target": target,
                    "public_ip_mode": public_ip,
                    "success": success,
                    "checks": results,
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Verification target: {target}")
        for result in results:
            status = "PASS" if result["ok"] else "FAIL"
            line = f"{status:<4} {result['check']}"
            if not result["ok"] and result["detail"]:
                line += f" ({result['detail']})"
            click.echo(line)
        click.echo(f"Overall: {'PASS' if success else 'FAIL'}")

    if not success:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# ssh
# ---------------------------------------------------------------------------
@main.command("ssh", context_settings={"ignore_unknown_options": True})
@click.option("--user", default="ubuntu", show_default=True, help="SSH user.")
@click.option(
    "--public-ip",
    is_flag=True,
    help="Use public IP instead of Tailscale (requires security group rule).",
)
@click.argument("ssh_args", nargs=-1, type=click.UNPROCESSED)
@require_aws_creds
def ssh_cmd(user: str, public_ip: bool, ssh_args: tuple[str, ...]) -> None:
    """SSH into the instance.

    Pass additional arguments to execute remote commands:
      edc ssh 'docker ps'
      edc ssh ls -la /opt

    Default: Uses Tailscale network. No exposed ports required.

    Note: If Tailscale SSH is enabled on your tailnet, it may require browser authentication.
    Use --public-ip for direct SSH (requires security group rule: port 22 from your IP).
    """
    # Get instance info
    info = ec2.status()
    if not info.get("exists"):
        click.echo("Error: No edcloud instance found.", err=True)
        raise SystemExit(1)
    if info["state"] != "running":
        click.echo(f"Error: Instance is {info['state']}, not running.", err=True)
        raise SystemExit(1)

    # Choose target: Tailscale IP (default) or public IP
    target = None
    if public_ip:
        target = info.get("public_ip")
        if not target:
            click.echo("Error: No public IP available.", err=True)
            raise SystemExit(1)
        click.echo(f"Connecting via public IP: {target}", err=True)
        click.echo("Note: Security group must allow SSH (port 22) from your IP", err=True)
        cmd = ["ssh", f"{user}@{target}"]
    else:
        ts_ip = tailscale.get_tailscale_ip(DEFAULT_TAILSCALE_HOSTNAME)
        if ts_ip:
            target = ts_ip
            click.echo(f"Connecting via Tailscale: {ts_ip}", err=True)
            click.echo(
                "Note: May trigger Tailscale SSH browser auth if enabled on your tailnet", err=True
            )
            # Use ProxyCommand=none to attempt regular SSH over Tailscale network
            # (Tailscale SSH may still intercept depending on tailnet settings)
            cmd = ["ssh", "-o", "ProxyCommand=none", f"{user}@{target}"]
        else:
            click.echo("Error: Tailscale IP not found. Is tailscale running?", err=True)
            click.echo("  Try: tailscale status", err=True)
            click.echo("  Or use: edc ssh --public-ip", err=True)
            raise SystemExit(1)

    if ssh_args:
        cmd.extend(ssh_args)

    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
