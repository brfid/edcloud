"""edcloud CLI — user-facing commands for the personal cloud lab."""

from __future__ import annotations

import functools
import json
import logging
import os
import shlex
import subprocess
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError

from edcloud import ec2, snapshot, tailscale
from edcloud.aws_check import check_aws_credentials, get_region
from edcloud.config import (
    DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER,
    DEFAULT_TAILSCALE_HOSTNAME,
    InstanceConfig,
)

P = ParamSpec("P")
R = TypeVar("R")


def _resolve_ssh_target(
    info: dict[str, object],
    public_ip: bool,
    user: str,
    hostname: str,
) -> tuple[str, list[str]]:
    """Build an SSH target address and base command.

    Args:
        info: Instance status dict (from ``ec2.status()``).
        public_ip: If ``True`` use the public IP; otherwise resolve via Tailscale.
        user: Remote username.
        hostname: Tailscale MagicDNS hostname to resolve.

    Returns:
        ``(target_ip, ssh_base_command)`` tuple.

    Raises:
        RuntimeError: If the chosen network path has no reachable address.
    """
    if public_ip:
        target = str(info.get("public_ip") or "")
        if not target:
            raise RuntimeError("No public IP available. Remove --public-ip or assign a public IP.")
        ssh_base = ["ssh", "-o", "StrictHostKeyChecking=accept-new", f"{user}@{target}"]
        return target, ssh_base
    else:
        ts_ip = tailscale.get_tailscale_ip(hostname)
        if not ts_ip:
            raise RuntimeError(
                f"Tailscale IP not found for '{hostname}'. "
                "Check tailnet connectivity or use --public-ip."
            )
        ssh_base = [
            "ssh",
            "-o",
            "ProxyCommand=none",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{ts_ip}",
        ]
        return ts_ip, ssh_base


def require_aws_creds(func: Callable[P, R]) -> Callable[P, R]:
    """Decorator that verifies AWS credentials before running a command.

    Catches ``RuntimeError`` from the wrapped command and converts it to a
    clean ``SystemExit(1)`` with the error message on stderr.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
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


def _fetch_tailscale_auth_key_from_ssm(parameter_name: str) -> str:
    """Read a Tailscale auth key from SSM Parameter Store."""
    return ec2.fetch_tailscale_auth_key_from_ssm(parameter_name)


def _ensure_no_tailscale_name_conflicts(base_hostname: str = DEFAULT_TAILSCALE_HOSTNAME) -> None:
    """Fail fast when Tailscale naming drift is detected.

    Raises:
        RuntimeError: If conflicting/suffixed edcloud records are found.
    """
    if not tailscale.tailscale_available():
        click.echo(
            "Warning: tailscale CLI not found on PATH; name conflict check skipped.",
            err=True,
        )
        return
    conflicts = tailscale.edcloud_name_conflicts(base_hostname=base_hostname)
    if conflicts:
        raise RuntimeError(tailscale.format_conflict_message(conflicts))


@click.group()
@click.version_option(package_name="edcloud")
def main() -> None:
    """Manage your personal cloud lab on AWS."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("edcloud").addHandler(handler)
    logging.getLogger("edcloud").setLevel(logging.INFO)


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
    help="Tailscale auth key (will be stored in SSM if provided).",
)
@click.option(
    "--tailscale-auth-key-ssm-parameter",
    default=DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER,
    envvar="TAILSCALE_AUTH_KEY_SSM_PARAMETER",
    show_default=True,
    help="SSM parameter name containing Tailscale auth key.",
)
@click.option(
    "--cleanup",
    is_flag=True,
    help="Clean up old Tailscale devices and orphaned volumes before provisioning.",
)
@click.option(
    "--allow-delete-state-volume",
    is_flag=True,
    help="Allow cleanup workflow to delete protected state volumes.",
)
@click.option(
    "--require-existing-state-volume/--allow-new-state-volume",
    default=True,
    show_default=True,
    help=(
        "Require reusable managed state volume by default; "
        "use --allow-new-state-volume to permit creating a fresh state volume."
    ),
)
@click.option(
    "--skip-snapshot",
    is_flag=True,
    help="Skip automatic snapshot before provision (if replacing existing instance).",
)
@click.option(
    "--allow-tailscale-name-conflicts",
    is_flag=True,
    help="Skip fail-fast guard for duplicate/suffixed edcloud Tailscale records.",
)
@require_aws_creds
def provision(
    instance_type: str,
    volume_size: int,
    state_volume_size: int,
    tailscale_hostname: str,
    tailscale_auth_key: str | None,
    tailscale_auth_key_ssm_parameter: str,
    cleanup: bool,
    allow_delete_state_volume: bool,
    require_existing_state_volume: bool,
    skip_snapshot: bool,
    allow_tailscale_name_conflicts: bool,
) -> None:
    """Create the edcloud EC2 instance from scratch.

    The Tailscale auth key is fetched from SSM by the instance at boot.
    """
    if not allow_tailscale_name_conflicts:
        _ensure_no_tailscale_name_conflicts(base_hostname=tailscale_hostname)

    # Pre-provision cleanup if requested
    if cleanup:
        from edcloud import cleanup as cleanup_module

        # Auto-snapshot if existing instance (unless --skip-snapshot)
        if not skip_snapshot:
            click.echo("Checking for existing instance to snapshot...")
            snap_ids = snapshot.auto_snapshot_before_destroy()
            if snap_ids:
                click.echo(f"✅ Created pre-provision snapshot(s): {', '.join(snap_ids)}")
                click.echo()
            else:
                click.echo(
                    "Info: no existing instance to snapshot (this is fine for first provision)"
                )
                click.echo()

        # Run cleanup workflow
        if not cleanup_module.run_cleanup_workflow(
            "pre-provision",
            skip_snapshot=True,
            allow_delete_state=allow_delete_state_volume,
        ):
            raise SystemExit(0)

    ssm = boto3.client("ssm")

    # If raw key is provided, store it in SSM
    if tailscale_auth_key:
        click.echo(f"Storing Tailscale auth key in SSM: {tailscale_auth_key_ssm_parameter}")
        try:
            ssm.put_parameter(
                Name=tailscale_auth_key_ssm_parameter,
                Value=tailscale_auth_key,
                Type="SecureString",
                Overwrite=True,
            )
            click.echo("  Key stored successfully.")
        except ClientError as exc:
            click.echo(f"Error storing key in SSM: {exc}", err=True)
            raise SystemExit(1) from exc

    # Verify SSM parameter exists
    try:
        ssm.get_parameter(Name=tailscale_auth_key_ssm_parameter, WithDecryption=False)
    except ClientError as exc:
        if "ParameterNotFound" in str(exc):
            click.echo(
                f"Error: Tailscale auth key not found in SSM: {tailscale_auth_key_ssm_parameter}",
                err=True,
            )
            click.echo("  Set TAILSCALE_AUTH_KEY or pass --tailscale-auth-key.", err=True)
            click.echo(
                "  Or manually create the parameter with: "
                "aws ssm put-parameter --name /edcloud/tailscale_auth_key "
                "--type SecureString --value 'tskey-auth-...'",
                err=True,
            )
            click.echo(
                "  Generate a key at: https://login.tailscale.com/admin/settings/keys",
                err=True,
            )
            raise SystemExit(1) from exc
        raise

    cfg = InstanceConfig(
        instance_type=instance_type,
        volume_size_gb=volume_size,
        state_volume_size_gb=state_volume_size,
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
    )
    result = ec2.provision(
        cfg,
        require_existing_state_volume=require_existing_state_volume,
    )
    click.echo()
    click.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# secrets helpers
# ---------------------------------------------------------------------------
@main.command("load-tailscale-env-key")
@click.option(
    "--tailscale-auth-key-ssm-parameter",
    default=DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER,
    envvar="TAILSCALE_AUTH_KEY_SSM_PARAMETER",
    show_default=True,
    help="SSM parameter to read (SecureString supported).",
)
@click.option(
    "--shell-export/--no-shell-export",
    default=True,
    show_default=True,
    help='Print export command for eval: eval "$(edc load-tailscale-env-key)"',
)
@require_aws_creds
def load_tailscale_env_key(
    tailscale_auth_key_ssm_parameter: str,
    shell_export: bool,
) -> None:
    """Load TAILSCALE_AUTH_KEY from SSM for local operator usage."""
    try:
        key = _fetch_tailscale_auth_key_from_ssm(tailscale_auth_key_ssm_parameter)
    except ClientError as exc:
        click.echo(
            "Error: could not read Tailscale auth key from SSM parameter "
            f"'{tailscale_auth_key_ssm_parameter}': {exc}",
            err=True,
        )
        raise SystemExit(1) from exc

    if shell_export:
        click.echo(f"export TAILSCALE_AUTH_KEY={shlex.quote(key)}")
        return

    click.echo("No output selected. Use --shell-export.", err=True)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# up / down
# ---------------------------------------------------------------------------
@main.command()
@click.option(
    "--allow-tailscale-name-conflicts",
    is_flag=True,
    help="Skip fail-fast guard for duplicate/suffixed edcloud Tailscale records.",
)
@require_aws_creds
def up(allow_tailscale_name_conflicts: bool) -> None:
    """Start the edcloud instance."""
    if not allow_tailscale_name_conflicts:
        _ensure_no_tailscale_name_conflicts()
    ec2.start()
    ts_ip = tailscale.get_tailscale_ip(DEFAULT_TAILSCALE_HOSTNAME)
    if ts_ip:
        click.echo(f"Tailscale IP: {ts_ip}")
    else:
        click.echo(
            f"Tailscale peer '{DEFAULT_TAILSCALE_HOSTNAME}' not yet visible. "
            "It may take a minute after boot."
        )


@main.command()
@require_aws_creds
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
@click.option(
    "--confirm-instance-id",
    default=None,
    help="Required safety confirmation. Must match current managed instance ID.",
)
@click.option(
    "--require-fresh-snapshot",
    is_flag=True,
    help="Require a recent pre-change snapshot before destroy.",
)
@click.option(
    "--fresh-snapshot-max-age-minutes",
    default=120,
    type=int,
    show_default=True,
    help="Maximum snapshot age for --require-fresh-snapshot.",
)
@click.option(
    "--cleanup",
    is_flag=True,
    help="Clean up Tailscale devices and orphaned volumes after destroy.",
)
@click.option(
    "--allow-delete-state-volume",
    is_flag=True,
    help="Allow cleanup workflow to delete protected state volumes.",
)
@click.option(
    "--skip-snapshot",
    is_flag=True,
    help="Skip automatic snapshot before destroy (faster but risky).",
)
@require_aws_creds
def destroy(
    force: bool,
    confirm_instance_id: str | None,
    require_fresh_snapshot: bool,
    fresh_snapshot_max_age_minutes: int,
    cleanup: bool,
    allow_delete_state_volume: bool,
    skip_snapshot: bool,
) -> None:
    """Terminate the instance and clean up. EBS volume is preserved."""
    if fresh_snapshot_max_age_minutes <= 0:
        click.echo("Error: --fresh-snapshot-max-age-minutes must be > 0.", err=True)
        raise SystemExit(1)

    info = ec2.status()
    if info.get("exists"):
        instance_id = str(info.get("instance_id", ""))
        if confirm_instance_id != instance_id:
            click.echo(
                "Error: destructive action requires explicit instance ID confirmation.",
                err=True,
            )
            click.echo(
                f"  Re-run with: edc destroy --confirm-instance-id {instance_id}",
                err=True,
            )
            raise SystemExit(1)

        if require_fresh_snapshot:
            recent = snapshot.find_recent_prechange_snapshot(fresh_snapshot_max_age_minutes)
            if not recent:
                click.echo(
                    "Error: no fresh pre-change snapshot found for this guardrail.",
                    err=True,
                )
                click.echo(
                    "  Create one: edc snapshot -d pre-change-<reason>",
                    err=True,
                )
                click.echo(
                    "  Then rerun destroy with --require-fresh-snapshot.",
                    err=True,
                )
                raise SystemExit(1)
            click.echo(
                f"Using pre-change snapshot: {recent['snapshot_id']} ({recent['start_time']})"
            )

    # Auto-snapshot before destroy (default, unless --skip-snapshot)
    if cleanup and not skip_snapshot:
        click.echo("Creating automatic pre-destroy snapshot...")
        try:
            snap_ids = snapshot.auto_snapshot_before_destroy()
            if snap_ids:
                click.echo(f"✅ Created snapshot(s): {', '.join(snap_ids)}")
            else:
                click.echo("Info: no instance found to snapshot")
        except (RuntimeError, ClientError, BotoCoreError) as e:
            click.echo(f"⚠️  Snapshot failed: {e}", err=True)
            if not click.confirm("Continue with destroy anyway?"):
                click.echo("Aborted.")
                raise SystemExit(0) from None

    ec2.destroy(force=force)

    # Post-destroy cleanup
    if cleanup:
        from edcloud import cleanup as cleanup_module

        click.echo()
        cleanup_module.run_cleanup_workflow(
            "post-destroy",
            skip_snapshot=True,
            allow_delete_state=allow_delete_state_volume,
        )


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

    target, ssh_base = _resolve_ssh_target(info, public_ip, user, DEFAULT_TAILSCALE_HOSTNAME)
    # Add verify-specific options
    ssh_base.extend(["-o", "BatchMode=yes", "-o", "ConnectTimeout=12"])

    checks: list[tuple[str, str]] = [
        ("cloud-init status done", "cloud-init status --wait >/dev/null"),
        ("docker service active", "systemctl is-active --quiet docker"),
        (
            "docker data-root points to state volume",
            "docker info --format '{{.DockerRootDir}}' | grep -qx /opt/edcloud/state/docker",
        ),
        ("portainer container running", "docker ps --format '{{.Names}}' | grep -qx portainer"),
        ("compose directory exists", "test -d /opt/edcloud/compose"),
        ("compose directory is mounted", "mountpoint -q /opt/edcloud/compose"),
        (
            "compose bind mount configured in fstab",
            "grep -qE "
            "'^/opt/edcloud/state/compose[[:space:]]+/opt/edcloud/compose[[:space:]]+"
            "none[[:space:]]+bind' /etc/fstab",
        ),
        ("portainer data directory exists", "test -d /opt/edcloud/portainer-data"),
        ("portainer data directory is mounted", "mountpoint -q /opt/edcloud/portainer-data"),
        (
            "portainer data bind mount configured in fstab",
            "grep -qE "
            "'^/opt/edcloud/state/portainer-data[[:space:]]+/opt/edcloud/portainer-data"
            "[[:space:]]+none[[:space:]]+bind' /etc/fstab",
        ),
        ("state directory exists", "test -d /opt/edcloud/state"),
        ("state directory is mounted", "mountpoint -q /opt/edcloud/state"),
        ("state directory writable", "test -w /opt/edcloud/state"),
        ("home directory exists", "test -d /home/ubuntu"),
        ("home directory is mounted", "mountpoint -q /home/ubuntu"),
        (
            "home bind mount configured in fstab",
            "grep -qE "
            "'^/opt/edcloud/state/home/ubuntu[[:space:]]+/home/ubuntu[[:space:]]+"
            "none[[:space:]]+bind' /etc/fstab",
        ),
        ("home directory writable", "test -w /home/ubuntu"),
        ("tailscale state directory exists", "test -d /var/lib/tailscale"),
        ("tailscale state directory is mounted", "mountpoint -q /var/lib/tailscale"),
        (
            "tailscale bind mount configured in fstab",
            "grep -qE "
            "'^/opt/edcloud/state/tailscale[[:space:]]+/var/lib/tailscale[[:space:]]+"
            "none[[:space:]]+bind' /etc/fstab",
        ),
        ("neovim installed", "command -v nvim >/dev/null"),
        ("byobu installed", "command -v byobu >/dev/null"),
        ("gh installed", "command -v gh >/dev/null"),
        ("lazyvim starter present", "test -f /home/ubuntu/.config/nvim/init.lua"),
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

    # Resolve SSH target
    try:
        target, cmd = _resolve_ssh_target(info, public_ip, user, DEFAULT_TAILSCALE_HOSTNAME)
    except RuntimeError as exc:
        click.echo(f"Error: {exc}", err=True)
        if not public_ip:
            click.echo("  Try: tailscale status", err=True)
            click.echo("  Or use: edc ssh --public-ip", err=True)
        raise SystemExit(1) from exc

    # Log connection details
    if public_ip:
        click.echo(f"Connecting via public IP: {target}", err=True)
        click.echo("Note: Security group must allow SSH (port 22) from your IP", err=True)
    else:
        click.echo(f"Connecting via Tailscale: {target}", err=True)
        click.echo(
            "Note: May trigger Tailscale SSH browser auth if enabled on your tailnet", err=True
        )

    if ssh_args:
        cmd.extend(ssh_args)

    os.execvp(cmd[0], cmd)


@main.group("tailscale")
def tailscale_group() -> None:
    """Tailscale reconciliation and guardrail helpers."""


@tailscale_group.command("reconcile")
@click.option(
    "--dry-run/--apply",
    default=True,
    show_default=True,
    help="Preview conflicts or apply manual reconciliation workflow guidance.",
)
def tailscale_reconcile(dry_run: bool) -> None:
    """Show or reconcile Tailscale naming conflicts for edcloud."""
    if not tailscale.tailscale_available():
        click.echo("tailscale CLI not found on this operator node.", err=True)
        raise SystemExit(1)

    conflicts = tailscale.edcloud_name_conflicts()
    if not conflicts:
        click.echo("No Tailscale naming conflicts detected for edcloud.")
        return

    click.echo(tailscale.format_conflict_message(conflicts), err=not dry_run)
    if dry_run:
        raise SystemExit(1)

    click.echo()
    click.echo("Applied mode: manual reconciliation required in Tailscale admin.")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# reprovision
# ---------------------------------------------------------------------------
@main.command("reprovision")
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
    help="Persistent state EBS volume size in GB.",
)
@click.option(
    "--tailscale-hostname",
    default=DEFAULT_TAILSCALE_HOSTNAME,
    show_default=True,
    help="Tailscale MagicDNS hostname.",
)
@click.option(
    "--tailscale-auth-key-ssm-parameter",
    default=DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER,
    envvar="TAILSCALE_AUTH_KEY_SSM_PARAMETER",
    show_default=True,
    help="SSM parameter name containing Tailscale auth key.",
)
@click.option(
    "--skip-snapshot",
    is_flag=True,
    help="Skip automatic pre-reprovision snapshot (faster but risky).",
)
@click.option(
    "--confirm-instance-id",
    default=None,
    help="Required safety confirmation. Must match current managed instance ID.",
)
@click.option(
    "--allow-tailscale-name-conflicts",
    is_flag=True,
    help="Skip fail-fast guard for duplicate/suffixed edcloud Tailscale records.",
)
@require_aws_creds
def reprovision(
    instance_type: str,
    volume_size: int,
    state_volume_size: int,
    tailscale_hostname: str,
    tailscale_auth_key_ssm_parameter: str,
    skip_snapshot: bool,
    confirm_instance_id: str | None,
    allow_tailscale_name_conflicts: bool,
) -> None:
    """Atomically snapshot → destroy → provision.

    Takes a pre-reprovision snapshot (unless --skip-snapshot), destroys the
    current instance, then provisions a fresh one. If provisioning fails after
    destroy, the snapshot IDs are printed prominently so you can restore
    manually.

    Note: provisioning always requires an existing state volume. If the state
    volume was deleted, use 'edc provision --allow-new-state-volume' directly.
    """
    if not allow_tailscale_name_conflicts:
        _ensure_no_tailscale_name_conflicts(base_hostname=tailscale_hostname)

    # Pre-flight: get current instance state once; use it for confirmation and destroy.
    info = ec2.status()
    if info.get("exists"):
        instance_id = str(info.get("instance_id", ""))
        if confirm_instance_id != instance_id:
            click.echo(
                "Error: destructive action requires explicit instance ID confirmation.",
                err=True,
            )
            click.echo(
                f"  Re-run with: edc reprovision --confirm-instance-id {instance_id}",
                err=True,
            )
            raise SystemExit(1)

    snap_ids: list[str] = []

    # 1. Pre-reprovision snapshot -----------------------------------------------
    if not skip_snapshot:
        click.echo("Creating pre-reprovision snapshot...")
        try:
            snap_ids = snapshot.auto_snapshot_before_destroy()
            if snap_ids:
                click.echo(f"✅ Pre-reprovision snapshot(s) completed: {', '.join(snap_ids)}")
                click.echo()
            else:
                click.echo("Info: no existing instance to snapshot.")
                click.echo()
        except (RuntimeError, BotoCoreError, ClientError) as exc:
            click.echo(f"⚠️  Snapshot failed: {exc}", err=True)
            if not click.confirm("Continue with reprovision anyway (no snapshot)?"):
                click.echo("Aborted.")
                raise SystemExit(0) from None

    # 2. Destroy ----------------------------------------------------------------
    if info.get("exists"):
        click.echo("Destroying current instance...")
        ec2.destroy(force=True)
        click.echo()
    else:
        click.echo("Info: no existing instance found — skipping destroy step.")
        click.echo()

    # 3. Provision --------------------------------------------------------------
    cfg = InstanceConfig(
        instance_type=instance_type,
        volume_size_gb=volume_size,
        state_volume_size_gb=state_volume_size,
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
    )
    try:
        result = ec2.provision(cfg, require_existing_state_volume=True)
    except (RuntimeError, ec2.TagDriftError, ClientError, BotoCoreError) as exc:
        click.echo(f"❌ Provisioning failed: {exc}", err=True)
        if snap_ids:
            click.echo("", err=True)
            click.echo(
                "⚠️  The instance was destroyed but reprovisioning failed.",
                err=True,
            )
            click.echo(
                f"   Snapshot IDs for manual restore: {', '.join(snap_ids)}",
                err=True,
            )
            click.echo(
                "   Use 'edc provision' after restoring the state volume from a snapshot.",
                err=True,
            )
        raise SystemExit(1) from exc

    click.echo()
    click.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# resize
# ---------------------------------------------------------------------------
@main.command("resize")
@click.option(
    "--instance-type",
    default=None,
    help="New EC2 instance type (e.g. t3a.medium). Requires stop/start cycle.",
)
@click.option(
    "--volume-size",
    default=None,
    type=int,
    help="New root EBS volume size in GB (expand only, applied online).",
)
@click.option(
    "--state-volume-size",
    default=None,
    type=int,
    help="New state EBS volume size in GB (expand only, applied online).",
)
@require_aws_creds
def resize_cmd(
    instance_type: str | None,
    volume_size: int | None,
    state_volume_size: int | None,
) -> None:
    """Resize the instance type and/or EBS volumes in place.

    Instance type changes require a stop/start cycle (data is preserved).
    Volume size changes are applied online without a restart.
    Volume shrinking is not supported by AWS.
    """
    if instance_type is None and volume_size is None and state_volume_size is None:
        click.echo(
            "Error: specify at least one of --instance-type, --volume-size, "
            "or --state-volume-size.",
            err=True,
        )
        raise SystemExit(1)

    result = ec2.resize(
        instance_type=instance_type,
        volume_size_gb=volume_size,
        state_volume_size_gb=state_volume_size,
    )
    click.echo()
    click.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
