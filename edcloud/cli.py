"""edcloud CLI — user-facing commands for the personal cloud lab."""

from __future__ import annotations

import functools
import json
import logging
import os
import shlex
import shutil
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ParamSpec, TypeVar

import click
from botocore.exceptions import BotoCoreError, ClientError

from edcloud import backup_policy, ec2, iam, ops_health, permissions, snapshot, tailscale
from edcloud.aws_check import check_aws_credentials, get_region
from edcloud.aws_clients import ssm_client
from edcloud.config import (
    DEFAULT_DOTFILES_BRANCH,
    DEFAULT_DOTFILES_REPO,
    DEFAULT_SNAPSHOT_KEEP_LAST,
    DEFAULT_SSH_USER,
    DEFAULT_TAILSCALE_AUTH_KEY_SSM_PARAMETER,
    DEFAULT_TAILSCALE_HOSTNAME,
    SNAPSHOT_MONTHLY_RATE_PER_GB,
    InstanceConfig,
)
from edcloud.lifecycle import (
    maybe_run_cleanup,
    require_confirmed_instance_id,
    run_optional_auto_snapshot,
    run_reprovision_flow,
)
from edcloud.proc import run_checked as _run_checked
from edcloud.resource_audit import audit_resources
from edcloud.security_group import TagDriftError
from edcloud.verify_catalog import VERIFY_CHECKS

P = ParamSpec("P")
R = TypeVar("R")


def _resolve_ssh_target(
    info: Mapping[str, object],
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


def _print_audit_summary(phase: str) -> None:
    """Run and print a concise resource-audit summary (warn-only)."""
    try:
        report = audit_resources()
    except Exception as exc:
        click.echo(f"Resource audit ({phase}): skipped ({exc})")
        click.echo()
        return
    findings = report.findings
    click.echo(f"Resource audit ({phase}):")
    if findings:
        click.echo(f"  Findings: {len(findings)} unanticipated resource(s)")
        for finding in findings[:10]:
            cost_suffix = (
                f" (~${finding.estimated_monthly_cost:.2f}/mo)"
                if finding.estimated_monthly_cost
                else ""
            )
            click.echo(
                f"  - [{finding.category}] {finding.resource_id}: {finding.message}{cost_suffix}"
            )
        if len(findings) > 10:
            click.echo(f"  ... and {len(findings) - 10} more")
    else:
        click.echo("  Findings: none")

    click.echo(
        "  Cost summary: "
        f"managed=${report.cost.baseline_monthly:.2f}/mo, "
        f"flagged subset=${report.cost.unanticipated_monthly:.2f}/mo, "
        f"total=${report.cost.total_monthly:.2f}/mo"
    )
    click.echo(f"  Note: {report.cost.note}")
    click.echo()


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
    help="Root EBS volume size in GiB.",
)
@click.option(
    "--state-volume-size",
    default=InstanceConfig.state_volume_size_gb,
    type=int,
    show_default=True,
    help="Persistent state EBS volume size in GiB (mounted at /opt/edcloud/state).",
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
    help="Review Tailscale records and orphaned volumes before provisioning.",
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
    "--allow-tailscale-name-conflicts",
    is_flag=True,
    help="Skip fail-fast guard for duplicate/suffixed edcloud Tailscale records.",
)
@click.option(
    "--dotfiles-repo",
    default=DEFAULT_DOTFILES_REPO,
    envvar="EDCLOUD_DOTFILES_REPO",
    show_default=True,
    help=(
        "Dotfiles repo URL for bootstrap ('auto' = infer https://github.com/USER/dotfiles.git)."
    ),
)
@click.option(
    "--dotfiles-branch",
    default=DEFAULT_DOTFILES_BRANCH,
    envvar="EDCLOUD_DOTFILES_BRANCH",
    show_default=True,
    help="Dotfiles branch/ref to checkout during bootstrap.",
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
    allow_tailscale_name_conflicts: bool,
    dotfiles_repo: str,
    dotfiles_branch: str,
) -> None:
    """Provision an EC2 instance, reusing a managed state volume by default.

    The Tailscale auth key is fetched from SSM by the instance at boot.
    """
    if not allow_tailscale_name_conflicts:
        _ensure_no_tailscale_name_conflicts(base_hostname=tailscale_hostname)

    _print_audit_summary("pre-provision")

    # Pre-provision cleanup if requested
    if cleanup:
        from edcloud import cleanup as cleanup_module

        # Run cleanup workflow
        if not cleanup_module.run_cleanup_workflow(
            "pre-provision",
            allow_delete_state=allow_delete_state_volume,
            echo=click.echo,
            confirm=click.confirm,
            prompt_int=lambda msg, default: click.prompt(msg, type=int, default=default),
        ):
            raise SystemExit(0)

    ssm = ssm_client()

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
        dotfiles_repo=dotfiles_repo,
        dotfiles_branch=dotfiles_branch,
    )
    result = ec2.provision(
        cfg,
        require_existing_state_volume=require_existing_state_volume,
    )
    _print_audit_summary("post-provision")

    click.echo()
    click.echo("Automatic CLI snapshot triggers prune the managed pool to retain 3 snapshots.")
    click.echo("  Use 'edc snapshot --list' to view the managed queue.")
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
@require_aws_creds
def load_tailscale_env_key(
    tailscale_auth_key_ssm_parameter: str,
) -> None:
    """Print a command that exports TAILSCALE_AUTH_KEY from SSM."""
    try:
        key = ec2.fetch_tailscale_auth_key_from_ssm(tailscale_auth_key_ssm_parameter)
    except ClientError as exc:
        click.echo(
            "Error: could not read Tailscale auth key from SSM parameter "
            f"'{tailscale_auth_key_ssm_parameter}': {exc}",
            err=True,
        )
        raise SystemExit(1) from exc

    click.echo(f"export TAILSCALE_AUTH_KEY={shlex.quote(key)}")


@main.command("setup-ssm-tokens")
@click.option(
    "--github-token",
    default=None,
    envvar="GITHUB_TOKEN",
    help="GitHub token to store in SSM (default: auto-read from gh CLI when available).",
)
@click.option(
    "--tailscale-auth-key",
    default=None,
    envvar="TAILSCALE_AUTH_KEY",
    help="Tailscale auth key to store in SSM.",
)
@click.option(
    "--prompt/--no-prompt",
    default=True,
    show_default=True,
    help="Prompt interactively for a missing Tailscale auth key.",
)
@require_aws_creds
def setup_ssm_tokens(
    github_token: str | None,
    tailscale_auth_key: str | None,
    prompt: bool,
) -> None:
    """Store GitHub and Tailscale auth tokens in SSM Parameter Store."""
    ssm = ssm_client()

    if not github_token and shutil.which("gh"):
        try:
            _run_checked(["gh", "auth", "status"])
            github_token = _run_checked(["gh", "auth", "token"]).stdout.strip()
            if github_token:
                click.echo("Using GitHub token from gh auth token")
        except RuntimeError:
            click.echo(
                "GitHub token not detected from gh; skipping unless --github-token is provided."
            )

    if github_token:
        ssm.put_parameter(
            Name="/edcloud/github_token",
            Description="GitHub personal access token for edcloud instance",
            Type="SecureString",
            Value=github_token,
            Overwrite=True,
        )
        click.echo("Stored /edcloud/github_token")

    if not tailscale_auth_key and prompt:
        tailscale_auth_key = click.prompt(
            "Paste your Tailscale auth key (starts with tskey-auth-, leave blank to skip)",
            default="",
            show_default=False,
        ).strip()
        tailscale_auth_key = tailscale_auth_key or None

    if (
        tailscale_auth_key
        and not tailscale_auth_key.startswith("tskey-auth-")
        and (
            not prompt
            or not click.confirm(
                "Key does not start with tskey-auth-. Continue anyway?",
                default=False,
            )
        )
    ):
        raise RuntimeError("Refusing to store non-standard Tailscale key.")

    if tailscale_auth_key:
        ssm.put_parameter(
            Name="/edcloud/tailscale_auth_key",
            Description="Tailscale auth key for edcloud instance provisioning",
            Type="SecureString",
            Value=tailscale_auth_key,
            Overwrite=True,
        )
        click.echo("Stored /edcloud/tailscale_auth_key")

    response = ssm.describe_parameters(
        ParameterFilters=[
            {"Key": "Name", "Option": "BeginsWith", "Values": ["/edcloud/"]},
        ]
    )
    names = sorted(p["Name"] for p in response.get("Parameters", []))
    click.echo("\nCurrent /edcloud/ parameters:")
    for name in names:
        click.echo(f"- {name}")


@main.command("sync-cline-auth")
@click.option("--remote", default="ubuntu@edcloud", show_default=True, help="Remote SSH target.")
@click.option(
    "--ssh-opt",
    "ssh_opts",
    multiple=True,
    help="Repeatable SSH/SCP option (example: --ssh-opt -i --ssh-opt /path/key).",
)
@click.option(
    "--source-secrets",
    default="~/.cline/data/secrets.json",
    show_default=True,
    help="Local source secrets.json path.",
)
@click.option(
    "--remote-config-dir",
    default=".cline/data",
    show_default=True,
    help="Remote Cline config directory under $HOME.",
)
@click.option(
    "--include-global-state/--secrets-only",
    default=True,
    show_default=True,
    help="Sync globalState.json alongside secrets.json (recommended for session reuse).",
)
@click.option(
    "--remote-diagnostics",
    is_flag=True,
    help="Run remote whoami/path/cline diagnostics before syncing.",
)
@click.option("--dry-run", is_flag=True, help="Print actions without changing remote state.")
def sync_cline_auth(
    remote: str,
    ssh_opts: tuple[str, ...],
    source_secrets: str,
    remote_config_dir: str,
    include_global_state: bool,
    remote_diagnostics: bool,
    dry_run: bool,
) -> None:
    """Back up and replace Cline OAuth files on a remote host."""
    from edcloud import cline_sync

    source_secrets_path = Path(source_secrets).expanduser().resolve()
    requested_global_state = include_global_state
    try:
        include_global_state = cline_sync.validate_source(
            source_secrets_path, include_global_state
        )
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Remote target: {remote}")
    click.echo(f"Source secrets: {source_secrets_path}")
    if include_global_state:
        source_global_state = source_secrets_path.with_name("globalState.json")
        click.echo(f"Source globalState: {source_global_state}")
    elif requested_global_state:
        # Requested but validate_source demoted it: the sibling file is missing.
        click.echo(
            "Warning: globalState.json not found next to secrets.json; "
            "continuing with secrets-only sync.",
            err=True,
        )

    if remote_diagnostics:
        out = cline_sync.run_remote_diagnostics(remote, ssh_opts, remote_config_dir)
        if out:
            click.echo(out)

    if dry_run:
        click.echo(f"[dry-run] Would backup and sync files under ~/{remote_config_dir}/")
        return

    cline_sync.sync_files(
        remote=remote,
        ssh_opts=ssh_opts,
        source_secrets_path=source_secrets_path,
        remote_config_dir=remote_config_dir,
        include_global_state=include_global_state,
    )
    click.echo("Cline auth sync complete.")


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

    # Run the on-start trigger without waiting for snapshot completion.
    try:
        snap_ids = snapshot.snapshot_and_prune("on-start", wait=False)
        if snap_ids:
            click.echo(f"On-start snapshot queued: {', '.join(snap_ids)}")
    except Exception as exc:
        click.echo(f"Warning: on-start snapshot skipped ({exc})", err=True)

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
    """Show instance state, addresses, and estimated compute and EBS cost."""
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
            f"Volume:    {vol['volume_id']}  {vol['size_gb']} GiB {vol['type']}  ({vol['state']})"
        )

    # Orphaned volume warning
    orphaned_vols: list[str] = info.get("orphaned_volumes", [])
    if orphaned_vols:
        click.echo()
        click.echo(f"Warning: {len(orphaned_vols)} orphaned managed volume(s) accruing cost:")
        for vol_id in orphaned_vols:
            click.echo(f"  {vol_id}")
            click.echo(f"    Delete: aws ec2 delete-volume --volume-id {shlex.quote(vol_id)}")

    # Cost
    cost = info.get("cost_estimate", {})
    if cost:
        click.echo()
        click.echo(f"Estimated monthly compute and attached-EBS cost ({cost.get('note', '')}):")
        click.echo(f"  Compute: ${cost.get('compute_monthly', 0):.2f}")
        click.echo(f"  Storage: ${cost.get('storage_monthly', 0):.2f}")
        click.echo(f"  Total:   ${cost.get('total_monthly', 0):.2f}")

    # Snapshots
    snaps = snapshot.list_snapshots()
    completed = [s for s in snaps if s["state"] == "completed"]
    click.echo()
    click.echo(
        f"Snapshots: {len(snaps)} managed ({len(completed)} completed) — use 'edc snapshot --list'"
    )


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------
@main.command()
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
    "--skip-cleanup",
    is_flag=True,
    help="Skip Tailscale cleanup guidance and orphaned-volume cleanup after destroy.",
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
    confirm_instance_id: str | None,
    require_fresh_snapshot: bool,
    fresh_snapshot_max_age_minutes: int,
    skip_cleanup: bool,
    allow_delete_state_volume: bool,
    skip_snapshot: bool,
) -> None:
    """Terminate the instance and clean up, preserving state by default."""
    if fresh_snapshot_max_age_minutes <= 0:
        click.echo("Error: --fresh-snapshot-max-age-minutes must be > 0.", err=True)
        raise SystemExit(1)

    info = ec2.status()
    try:
        require_confirmed_instance_id(
            info,
            confirm_instance_id,
            command_name="destroy",
        )
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    if info.get("exists") and require_fresh_snapshot:
        recent = snapshot.find_recent_prechange_snapshot(fresh_snapshot_max_age_minutes)
        if not recent:
            click.echo(
                "Error: no fresh pre-change snapshot found for this guardrail.",
                err=True,
            )
            click.echo(
                "  Create one: edc snapshot -d pre-change-description",
                err=True,
            )
            click.echo(
                "  Then rerun destroy with --require-fresh-snapshot.",
                err=True,
            )
            raise SystemExit(1)
        click.echo(f"Using pre-change snapshot: {recent['snapshot_id']} ({recent['start_time']})")

    run_optional_auto_snapshot(
        skip_snapshot=skip_snapshot,
        auto_snapshot=lambda: snapshot.snapshot_and_prune("pre-destroy", wait=True),
        echo=click.echo,
        echo_err=lambda msg: click.echo(msg, err=True),
        confirm_continue=lambda msg: click.confirm(msg),
        operation_label="destroy",
        continue_prompt="Continue with destroy anyway?",
    )

    ec2.destroy()

    def _run_post_destroy_cleanup() -> None:
        from edcloud import cleanup as cleanup_module

        click.echo()
        cleanup_module.run_cleanup_workflow(
            "post-destroy",
            allow_delete_state=allow_delete_state_volume,
            echo=click.echo,
            confirm=click.confirm,
            prompt_int=lambda msg, default: click.prompt(msg, type=int, default=default),
        )

    maybe_run_cleanup(skip_cleanup=skip_cleanup, run_cleanup=_run_post_destroy_cleanup)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
@main.command("snapshot")
@click.option("--list", "list_", is_flag=True, help="List existing snapshots.")
@click.option("--description", "-d", default=None, help="Snapshot description.")
@click.option(
    "--prune",
    is_flag=True,
    help="Preview snapshots beyond the retention limit; use --apply to delete them.",
)
@click.option(
    "--keep",
    default=DEFAULT_SNAPSHOT_KEEP_LAST,
    type=int,
    show_default=True,
    help="Number of most-recent snapshots to retain when pruning.",
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
    keep: int,
    dry_run: bool,
) -> None:
    """Create, list, or prune EBS snapshots."""
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
        click.echo(f"{'ID':<25} {'Source GiB':>10} {'State':<12} {'Started':<20} {'Description'}")
        click.echo("-" * 90)
        for s in snaps:
            click.echo(
                f"{s['snapshot_id']:<25} {s['size_gb']:>10} {s['state']:<12} "
                f"{s['start_time'][:19]:<20} {s['description']}"
            )
    elif prune:
        result = snapshot.prune_snapshots(keep_last=keep, dry_run=dry_run)
        if result["delete_count"] == 0:
            click.echo(f"Nothing to prune — {result['total']} snapshot(s), keep={keep}.")
            return
        click.echo(
            f"{'Would delete' if dry_run else 'Deleting'} {result['delete_count']} of "
            f"{result['total']} snapshot(s) (keeping {keep} most recent):"
        )
        for snap in result["to_delete"]:
            click.echo(f"  {snap['snapshot_id']}  {snap['description']}")
        if dry_run:
            click.echo("Re-run with --apply to delete these snapshots.")
    else:
        snapshot.create_snapshot(description)


@main.command("snapshot-cost")
@click.option(
    "--soft-cap-usd",
    default=2.0,
    type=float,
    show_default=True,
    help="Soft cap for the capacity-based monthly cost proxy.",
)
@click.option(
    "--gb-month-rate",
    default=SNAPSHOT_MONTHLY_RATE_PER_GB,
    type=float,
    show_default=True,
    help="Planning rate per source-volume GiB-month.",
)
@click.option(
    "--fail-on-cap",
    is_flag=True,
    help="Exit non-zero when the cost proxy exceeds the soft cap.",
)
@require_aws_creds
def snapshot_cost_cmd(soft_cap_usd: float, gb_month_rate: float, fail_on_cap: bool) -> None:
    """Compare a conservative snapshot-cost proxy with a soft cap."""
    if soft_cap_usd <= 0:
        click.echo("Error: --soft-cap-usd must be > 0.", err=True)
        raise SystemExit(1)
    if gb_month_rate <= 0:
        click.echo("Error: --gb-month-rate must be > 0.", err=True)
        raise SystemExit(1)

    report = ops_health.estimate_snapshot_monthly_cost(
        snapshot.list_snapshots(),
        gb_month_rate=gb_month_rate,
        soft_cap_usd=soft_cap_usd,
    )
    click.echo(json.dumps(report, indent=2))

    if report["over_soft_cap"]:
        click.echo(
            "Warning: snapshot cost proxy exceeds soft cap. "
            "Review managed snapshots and the active retention mechanism.",
            err=True,
        )
        if fail_on_cap:
            raise SystemExit(1)


@main.command("restore-drill")
@click.option(
    "--snapshot-id",
    default=None,
    help="Specific completed snapshot ID for the state volume (default: latest completed).",
)
@click.option(
    "--instance-id",
    default=None,
    help="Instance ID to temporarily attach restored volume to.",
)
@click.option(
    "--attach-managed-instance",
    is_flag=True,
    help="Attach temporary restored volume to the managed running edcloud instance.",
)
@click.option(
    "--device-name",
    default="/dev/sdg",
    show_default=True,
    help="Linux device name used when attaching temporary drill volume.",
)
@click.option(
    "--keep-temporary-volume",
    is_flag=True,
    help="Keep temporary restored volume for manual inspection (no auto-delete).",
)
@require_aws_creds
def restore_drill_cmd(
    snapshot_id: str | None,
    instance_id: str | None,
    attach_managed_instance: bool,
    device_name: str,
    keep_temporary_volume: bool,
) -> None:
    """Test snapshot-to-volume restoration without mounting or checking files."""
    if instance_id and attach_managed_instance:
        click.echo(
            "Error: use either --instance-id or --attach-managed-instance (not both).",
            err=True,
        )
        raise SystemExit(1)

    target_instance_id = instance_id
    if attach_managed_instance:
        info = ec2.status()
        if not info.get("exists"):
            click.echo(
                "Error: no managed instance exists to attach restore-drill volume.", err=True
            )
            raise SystemExit(1)
        if info.get("state") != "running":
            click.echo(
                f"Error: managed instance is {info.get('state')}; must be running to attach.",
                err=True,
            )
            raise SystemExit(1)
        target_instance_id = str(info["instance_id"])

    result = snapshot.run_restore_drill(
        snapshot_id=snapshot_id,
        instance_id=target_instance_id,
        device_name=device_name,
        keep_temporary_volume=keep_temporary_volume,
    )
    click.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# backup-policy (AWS DLM)
# ---------------------------------------------------------------------------
@main.group("backup-policy")
def backup_policy_group() -> None:
    """Manage AWS-native DLM snapshot lifecycle policy."""


@backup_policy_group.command("status")
@require_aws_creds
def backup_policy_status_cmd() -> None:
    """Show current DLM backup policy status."""
    status = backup_policy.policy_status()
    click.echo(json.dumps(status, indent=2))


@backup_policy_group.command("apply")
@click.option("--daily-keep", default=1, type=int, show_default=True)
@click.option("--weekly-keep", default=1, type=int, show_default=True)
@click.option("--monthly-keep", default=1, type=int, show_default=True)
@click.option("--disabled", is_flag=True, help="Create/update policy in DISABLED state.")
@require_aws_creds
def backup_policy_apply_cmd(
    daily_keep: int,
    weekly_keep: int,
    monthly_keep: int,
    disabled: bool,
) -> None:
    """Create or update the managed DLM backup policy."""
    role_arn = iam.ensure_dlm_lifecycle_role(
        {
            "edcloud:managed": "true",
            "Name": "edcloud",
        }
    )
    result = backup_policy.ensure_policy(
        execution_role_arn=role_arn,
        daily_keep=daily_keep,
        weekly_keep=weekly_keep,
        monthly_keep=monthly_keep,
        enabled=not disabled,
    )
    click.echo(json.dumps(result, indent=2))


@backup_policy_group.command("disable")
@require_aws_creds
def backup_policy_disable_cmd() -> None:
    """Disable the managed DLM backup policy."""
    result = backup_policy.disable_policy()
    click.echo(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
@main.command("verify")
@click.option("--user", default=DEFAULT_SSH_USER, show_default=True, help="SSH user.")
@click.option(
    "--public-ip",
    is_flag=True,
    help="Use public IP instead of Tailscale (requires an inbound security group rule).",
)
@click.option("--json-output", is_flag=True, help="Emit verification results as JSON.")
@require_aws_creds
def verify_cmd(user: str, public_ip: bool, json_output: bool) -> None:
    """Verify the running host's bootstrap baseline."""
    info = ec2.status()
    if not info.get("exists"):
        raise RuntimeError("No edcloud instance found. Run 'edc provision' first.")
    if info.get("state") != "running":
        raise RuntimeError(f"Instance is {info.get('state')}, must be running for verification.")

    target, ssh_base = _resolve_ssh_target(info, public_ip, user, DEFAULT_TAILSCALE_HOSTNAME)
    # Add verify-specific options
    ssh_base.extend(["-o", "BatchMode=yes", "-o", "ConnectTimeout=12"])

    results: list[dict[str, str | bool]] = []
    for check in VERIFY_CHECKS:
        remote = f"bash -lc {shlex.quote(check.remote_cmd)}"
        cmd = [*ssh_base, remote]
        try:
            proc = subprocess.run(  # nosec B603
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append({"check": check.name, "ok": False, "detail": str(exc)})
            continue

        detail = proc.stderr.strip() or proc.stdout.strip()
        results.append({"check": check.name, "ok": proc.returncode == 0, "detail": detail})

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
@click.option("--user", default=DEFAULT_SSH_USER, show_default=True, help="SSH user.")
@click.option(
    "--public-ip",
    is_flag=True,
    help="Use public IP instead of Tailscale (requires security group rule).",
)
@click.argument("ssh_args", nargs=-1, type=click.UNPROCESSED)
@require_aws_creds
def ssh_cmd(user: str, public_ip: bool, ssh_args: tuple[str, ...]) -> None:
    """Connect through Tailscale interactively or run a remote command.

    --public-ip requires a temporary inbound security group rule for port 22.
    Remove that rule after use.
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

    os.execvp(cmd[0], cmd)  # nosec B606


@main.group("tailscale")
def tailscale_group() -> None:
    """Tailscale reconciliation and guardrail helpers."""


@tailscale_group.command("reconcile")
@click.option("--dry-run/--apply", default=True, hidden=True)
def tailscale_reconcile(dry_run: bool) -> None:
    """Report Tailscale naming conflicts and manual remediation steps."""
    if not tailscale.tailscale_available():
        click.echo("tailscale CLI not found on this operator node.", err=True)
        raise SystemExit(1)

    conflicts = tailscale.edcloud_name_conflicts()
    if not conflicts:
        click.echo("No Tailscale naming conflicts detected for edcloud.")
        return

    click.echo(tailscale.format_conflict_message(conflicts), err=not dry_run)
    if not dry_run:
        click.echo("No changes applied; resolve the conflicts in Tailscale admin.", err=True)
    raise SystemExit(1)


@main.group("permissions")
def permissions_group() -> None:
    """Inspect and verify AWS permissions required by edcloud."""


def _permission_profile_choice() -> click.Choice:  # type: ignore[type-arg]
    return click.Choice(["all", *permissions.available_profiles()])


@permissions_group.command("show")
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    type=_permission_profile_choice(),
    help="Permission profile(s) to show. Repeatable. Defaults to all profiles.",
)
@click.option("--json-output", is_flag=True, help="Emit profile details as JSON.")
def permissions_show_cmd(profiles: tuple[str, ...], json_output: bool) -> None:
    """Show required IAM actions by profile."""
    if json_output:
        click.echo(permissions.profiles_json(profiles))
        return

    for profile in permissions.resolve_profiles(profiles):
        click.echo(f"[{profile.name}] {profile.description}")
        for action in profile.actions:
            click.echo(f"  - {action}")
        click.echo()


@permissions_group.command("policy")
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    type=_permission_profile_choice(),
    help="Permission profile(s) to include in generated policy. Defaults to all profiles.",
)
def permissions_policy_cmd(profiles: tuple[str, ...]) -> None:
    """Generate an operator IAM policy document for selected profiles."""
    click.echo(json.dumps(permissions.policy_document(profiles), indent=2))


@permissions_group.command("verify")
@click.option(
    "--profile",
    "profiles",
    multiple=True,
    type=_permission_profile_choice(),
    help="Permission profile(s) to verify. Defaults to all profiles.",
)
@click.option("--json-output", is_flag=True, help="Emit verification result as JSON.")
@require_aws_creds
def permissions_verify_cmd(profiles: tuple[str, ...], json_output: bool) -> None:
    """Verify selected permissions for the current AWS principal."""
    required = permissions.required_actions(profiles)
    result = permissions.verify_required_actions(required)

    if json_output:
        click.echo(
            json.dumps(
                {
                    "ok": result.ok,
                    "principal_arn": result.principal_arn,
                    "policy_source_arn": result.policy_source_arn,
                    "missing_actions": list(result.missing_actions),
                    "required_action_count": len(required),
                    "detail": result.detail,
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Principal: {result.principal_arn}")
        click.echo(f"Policy source: {result.policy_source_arn}")
        click.echo(f"Required actions: {len(required)}")
        click.echo(result.detail)
        if result.missing_actions:
            click.echo("Missing actions:")
            for action in result.missing_actions:
                click.echo(f"  - {action}")

    if not result.ok:
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
    help="Root EBS volume size in GiB.",
)
@click.option(
    "--state-volume-size",
    default=InstanceConfig.state_volume_size_gb,
    type=int,
    show_default=True,
    help="Persistent state EBS volume size in GiB.",
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
@click.option(
    "--dotfiles-repo",
    default=DEFAULT_DOTFILES_REPO,
    envvar="EDCLOUD_DOTFILES_REPO",
    show_default=True,
    help=(
        "Dotfiles repo URL for bootstrap ('auto' = infer https://github.com/USER/dotfiles.git)."
    ),
)
@click.option(
    "--dotfiles-branch",
    default=DEFAULT_DOTFILES_BRANCH,
    envvar="EDCLOUD_DOTFILES_BRANCH",
    show_default=True,
    help="Dotfiles branch/ref to checkout during bootstrap.",
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
    dotfiles_repo: str,
    dotfiles_branch: str,
) -> None:
    """Snapshot, terminate, and rebuild the instance with its state volume.

    If a managed instance exists, the command takes a pre-reprovision snapshot
    unless --skip-snapshot is set, destroys the instance, and provisions a
    replacement. It prints available snapshot IDs if a later step fails.

    Note: provisioning always requires an existing state volume. If the state
    volume was deleted, use 'edc provision --allow-new-state-volume' directly.
    """
    if not allow_tailscale_name_conflicts:
        _ensure_no_tailscale_name_conflicts(base_hostname=tailscale_hostname)

    # Read status once to select the flow and confirm the destructive action.
    info = ec2.status()
    try:
        require_confirmed_instance_id(
            info,
            confirm_instance_id,
            command_name="reprovision",
        )
    except RuntimeError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    cfg = InstanceConfig(
        instance_type=instance_type,
        volume_size_gb=volume_size,
        state_volume_size_gb=state_volume_size,
        tailscale_hostname=tailscale_hostname,
        tailscale_auth_key_ssm_parameter=tailscale_auth_key_ssm_parameter,
        dotfiles_repo=dotfiles_repo,
        dotfiles_branch=dotfiles_branch,
    )
    from edcloud import cleanup as cleanup_module

    snap_ids: list[str] = []

    def _snapshot_for_reprovision() -> list[str]:
        created = snapshot.snapshot_and_prune("pre-reprovision", wait=True)
        snap_ids.extend(created)
        return created

    try:
        completed_snap_ids, result = run_reprovision_flow(
            info=info,
            skip_snapshot=skip_snapshot,
            auto_snapshot=_snapshot_for_reprovision,
            destroy_instance=lambda: ec2.destroy(),
            cleanup_orphaned_volumes=lambda: cleanup_module.cleanup_orphaned_volumes(
                mode="delete", allow_delete_state=False, echo=click.echo
            ),
            provision_replacement=lambda: ec2.provision(cfg, require_existing_state_volume=True),
            echo=click.echo,
            echo_err=lambda msg: click.echo(msg, err=True),
            confirm_continue=lambda msg: click.confirm(msg),
        )
        snap_ids = completed_snap_ids
    except (RuntimeError, TagDriftError, ClientError, BotoCoreError) as exc:
        click.echo(f"❌ Reprovisioning failed: {exc}", err=True)
        if snap_ids:
            click.echo("", err=True)
            click.echo(
                "⚠️  A pre-reprovision snapshot is available.",
                err=True,
            )
            click.echo(
                f"   Snapshot IDs for manual restore: {', '.join(snap_ids)}",
                err=True,
            )
            click.echo(
                "   Run 'edc status' to determine the instance state before recovery.",
                err=True,
            )
        raise SystemExit(1) from exc

    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Repeat 'edc status' until it reports 'Reachable: yes'.")
    click.echo("  2. Run: edc ssh 'cloud-init status --wait'")
    click.echo("  3. Run: edc verify")
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
    help="New root EBS capacity in GiB (expand only; grow the filesystem separately).",
)
@click.option(
    "--state-volume-size",
    default=None,
    type=int,
    help="New state EBS capacity in GiB (expand only; grow the filesystem separately).",
)
@require_aws_creds
def resize_cmd(
    instance_type: str | None,
    volume_size: int | None,
    state_volume_size: int | None,
) -> None:
    """Change the instance type or request EBS volume expansion.

    Instance type changes require a stop/start cycle (data is preserved).
    Volume changes request online EBS expansion; grow the partition or filesystem
    separately after AWS completes the modification. AWS does not support shrinking.
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
