"""edcloud CLI — manage your personal cloud lab."""

from __future__ import annotations

import functools
import json
import os

import click

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
        return func(*args, **kwargs)

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
@require_aws_creds
def provision(
    instance_type: str,
    volume_size: int,
    tailscale_hostname: str,
    tailscale_auth_key: str | None,
) -> None:
    """Create the edcloud EC2 instance from scratch."""
    if not tailscale_auth_key:
        click.echo("Error: Tailscale auth key required.", err=True)
        click.echo("  Set TAILSCALE_AUTH_KEY or pass --tailscale-auth-key.", err=True)
        click.echo("  Generate one at: https://login.tailscale.com/admin/settings/keys", err=True)
        raise SystemExit(1)

    cfg = InstanceConfig(
        instance_type=instance_type,
        volume_size_gb=volume_size,
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
        click.echo("No edcloud instance found. Run 'edcloud provision' to create one.")
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
@require_aws_creds
def snapshot_cmd(list_: bool, description: str | None) -> None:
    """Create or list EBS snapshots."""
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
    else:
        snapshot.create_snapshot(description)


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
      edcloud ssh 'docker ps'
      edcloud ssh ls -la /opt

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
            click.echo("  Or use: edcloud ssh --public-ip", err=True)
            raise SystemExit(1)

    if ssh_args:
        cmd.extend(ssh_args)

    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
