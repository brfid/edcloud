"""Tests for CLI behavior."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from edcloud.cli import main


@patch("edcloud.cli.ssm_client")
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_reads_tailscale_key_from_ssm(
    _mock_creds,
    _mock_region,
    _mock_conflicts,
    mock_provision,
    mock_ssm_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tailscale-test-key"}}
    mock_ssm_client.return_value = ssm
    mock_provision.return_value = {
        "instance_id": "i-abc123",
        "security_group_id": "sg-abc123",
        "public_ip": "none",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["provision", "--tailscale-auth-key-ssm-parameter", "/edcloud/tailscale_auth_key"],
    )

    assert result.exit_code == 0
    # Provision now only receives config, SSM parameter is verified but not fetched
    cfg = mock_provision.call_args.args[0]
    assert cfg.tailscale_auth_key_ssm_parameter == "/edcloud/tailscale_auth_key"
    assert mock_provision.call_args.kwargs["require_existing_state_volume"] is True
    # Verify SSM parameter existence is checked (not fetched with decryption)
    assert ssm.get_parameter.called
    call_kwargs = ssm.get_parameter.call_args.kwargs
    assert call_kwargs["Name"] == "/edcloud/tailscale_auth_key"
    assert call_kwargs["WithDecryption"] is False


@patch("edcloud.cli.ssm_client")
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_reads_tailscale_key_from_ssm_envvar(
    _mock_creds,
    _mock_region,
    _mock_conflicts,
    mock_provision,
    mock_ssm_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "exists"}}
    mock_ssm_client.return_value = ssm
    mock_provision.return_value = {
        "instance_id": "i-abc123",
        "security_group_id": "sg-abc123",
        "public_ip": "none",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["provision"],
        env={"TAILSCALE_AUTH_KEY_SSM_PARAMETER": "/edcloud/tailscale_auth_key"},
    )

    assert result.exit_code == 0
    cfg = mock_provision.call_args.args[0]
    assert cfg.tailscale_auth_key_ssm_parameter == "/edcloud/tailscale_auth_key"
    assert mock_provision.call_args.kwargs["require_existing_state_volume"] is True
    call_kwargs = ssm.get_parameter.call_args.kwargs
    assert call_kwargs["Name"] == "/edcloud/tailscale_auth_key"
    assert call_kwargs["WithDecryption"] is False


@patch("edcloud.cli.ssm_client")
@patch("edcloud.cli.tailscale.tailscale_available", return_value=False)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_requires_tailscale_key(
    _mock_creds,
    _mock_region,
    _mock_tailscale_available,
    mock_ssm_client,
):
    from botocore.exceptions import ClientError

    ssm = MagicMock()
    ssm.get_parameter.side_effect = ClientError(
        {"Error": {"Code": "ParameterNotFound"}},
        "GetParameter",
    )
    mock_ssm_client.return_value = ssm
    runner = CliRunner()
    result = runner.invoke(main, ["provision"])

    assert result.exit_code == 1
    assert "Tailscale auth key not found in SSM" in result.output


@patch("edcloud.cli.tailscale.edcloud_name_conflicts")
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_fails_on_tailscale_name_conflict(
    _mock_creds,
    _mock_region,
    _mock_available,
    mock_conflicts,
):
    mock_conflicts.return_value = [
        {"hostname": "edcloud", "ip": "100.64.1.42", "dns_name": "edcloud-2.tail.ts.net."}
    ]

    runner = CliRunner()
    result = runner.invoke(main, ["provision", "--tailscale-auth-key", "tskey-test"])

    assert result.exit_code == 1
    assert "Tailscale naming conflict detected" in result.output


@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.ec2.start")
@patch("edcloud.cli.tailscale.get_tailscale_ip", return_value="100.64.1.42")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_up_runs_with_clean_tailscale_conflict_guard(
    _mock_creds,
    _mock_region,
    _mock_ip,
    mock_start,
    _mock_available,
    _mock_conflicts,
):
    runner = CliRunner()
    result = runner.invoke(main, ["up"])
    assert result.exit_code == 0
    mock_start.assert_called_once()


@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_passes_require_existing_state_volume_flag(
    _mock_creds,
    _mock_region,
    _mock_conflicts,
    mock_provision,
):
    mock_provision.return_value = {
        "instance_id": "i-abc123",
        "security_group_id": "sg-abc123",
        "public_ip": "none",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "provision",
            "--tailscale-auth-key",
            "tskey-test",
            "--require-existing-state-volume",
        ],
    )

    assert result.exit_code == 0
    assert mock_provision.call_args.kwargs["require_existing_state_volume"] is True


@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_allow_new_state_volume_disables_requirement(
    _mock_creds,
    _mock_region,
    _mock_conflicts,
    mock_provision,
):
    mock_provision.return_value = {
        "instance_id": "i-abc123",
        "security_group_id": "sg-abc123",
        "public_ip": "none",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "provision",
            "--tailscale-auth-key",
            "tskey-test",
            "--allow-new-state-volume",
        ],
    )

    assert result.exit_code == 0
    assert mock_provision.call_args.kwargs["require_existing_state_volume"] is False


@patch("edcloud.cleanup.run_cleanup_workflow", return_value=True)
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_cleanup_passes_allow_delete_state_volume(
    _mock_creds,
    _mock_region,
    _mock_conflicts,
    mock_provision,
    mock_cleanup,
):
    mock_provision.return_value = {
        "instance_id": "i-abc123",
        "security_group_id": "sg-abc123",
        "public_ip": "none",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "provision",
            "--tailscale-auth-key",
            "tskey-test",
            "--cleanup",
            "--skip-snapshot",
            "--allow-delete-state-volume",
        ],
    )

    assert result.exit_code == 0
    assert mock_cleanup.call_args.kwargs["allow_delete_state"] is True


@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_snapshot_rejects_conflicting_modes(_mock_creds, _mock_region):
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot", "--list", "--prune"])

    assert result.exit_code == 1
    assert "use either --list or --prune" in result.output


@patch("edcloud.cli.snapshot.list_snapshots")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_snapshot_cost_reports_over_cap_warning(
    _mock_creds,
    _mock_region,
    mock_list_snapshots,
):
    mock_list_snapshots.return_value = [
        {"snapshot_id": "snap-1", "state": "completed", "size_gb": 30},
        {"snapshot_id": "snap-2", "state": "completed", "size_gb": 20},
    ]
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot-cost"])
    assert result.exit_code == 0
    assert '"over_soft_cap": true' in result.output
    assert "exceeds soft cap" in result.output


@patch("edcloud.cli.snapshot.list_snapshots")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_snapshot_cost_fail_on_cap_returns_nonzero(
    _mock_creds,
    _mock_region,
    mock_list_snapshots,
):
    mock_list_snapshots.return_value = [
        {"snapshot_id": "snap-1", "state": "completed", "size_gb": 50},
    ]
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot-cost", "--fail-on-cap"])
    assert result.exit_code == 1


@patch("edcloud.cli.snapshot.list_snapshots")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_snapshot_cost_rejects_invalid_inputs(
    _mock_creds,
    _mock_region,
    mock_list_snapshots,
):
    mock_list_snapshots.return_value = []
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot-cost", "--soft-cap-usd", "0"])
    assert result.exit_code == 1
    assert "soft-cap-usd must be > 0" in result.output


@patch("edcloud.cli.snapshot.run_restore_drill")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_restore_drill_runs_with_defaults(
    _mock_creds,
    _mock_region,
    mock_restore_drill,
):
    mock_restore_drill.return_value = {
        "success": True,
        "snapshot_id": "snap-abc",
        "temporary_volume_id": "vol-temp",
    }

    runner = CliRunner()
    result = runner.invoke(main, ["restore-drill"])

    assert result.exit_code == 0
    mock_restore_drill.assert_called_once_with(
        snapshot_id=None,
        instance_id=None,
        device_name="/dev/sdg",
        keep_temporary_volume=False,
    )


@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.snapshot.run_restore_drill")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_restore_drill_attach_managed_instance_uses_running_instance(
    _mock_creds,
    _mock_region,
    mock_restore_drill,
    mock_status,
):
    mock_status.return_value = {
        "exists": True,
        "state": "running",
        "instance_id": "i-abc123",
    }
    mock_restore_drill.return_value = {
        "success": True,
        "snapshot_id": "snap-abc",
        "temporary_volume_id": "vol-temp",
        "instance_id": "i-abc123",
    }

    runner = CliRunner()
    result = runner.invoke(main, ["restore-drill", "--attach-managed-instance"])

    assert result.exit_code == 0
    mock_restore_drill.assert_called_once_with(
        snapshot_id=None,
        instance_id="i-abc123",
        device_name="/dev/sdg",
        keep_temporary_volume=False,
    )


@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_restore_drill_rejects_conflicting_instance_flags(_mock_creds, _mock_region):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["restore-drill", "--instance-id", "i-abc123", "--attach-managed-instance"],
    )

    assert result.exit_code == 1
    assert "use either --instance-id or --attach-managed-instance" in result.output


@patch("edcloud.cli.backup_policy.policy_status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_backup_policy_status_outputs_json(
    _mock_creds,
    _mock_region,
    mock_status,
):
    mock_status.return_value = {
        "exists": True,
        "policy_id": "policy-123",
        "state": "ENABLED",
    }
    runner = CliRunner()
    result = runner.invoke(main, ["backup-policy", "status"])
    assert result.exit_code == 0
    assert "policy-123" in result.output


@patch("edcloud.cli.backup_policy.ensure_policy")
@patch("edcloud.cli.iam.ensure_dlm_lifecycle_role")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_backup_policy_apply_uses_defaults(
    _mock_creds,
    _mock_region,
    mock_ensure_role,
    mock_ensure_policy,
):
    mock_ensure_role.return_value = "arn:aws:iam::123:role/edcloud-dlm-lifecycle-role"
    mock_ensure_policy.return_value = {
        "action": "created",
        "policy_id": "policy-123",
    }
    runner = CliRunner()
    result = runner.invoke(main, ["backup-policy", "apply"])
    assert result.exit_code == 0
    mock_ensure_policy.assert_called_once_with(
        execution_role_arn="arn:aws:iam::123:role/edcloud-dlm-lifecycle-role",
        daily_keep=1,
        weekly_keep=1,
        monthly_keep=1,
        quarterly_keep=1,
        enabled=True,
    )


@patch("edcloud.cli.backup_policy.disable_policy")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_backup_policy_disable(
    _mock_creds,
    _mock_region,
    mock_disable,
):
    mock_disable.return_value = {"exists": True, "state": "DISABLED"}
    runner = CliRunner()
    result = runner.invoke(main, ["backup-policy", "disable"])
    assert result.exit_code == 0
    assert "DISABLED" in result.output


@patch("edcloud.cli.subprocess.run")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_verify_passes_when_remote_checks_pass(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_run,
):
    mock_status.return_value = {"exists": True, "state": "running", "public_ip": "203.0.113.10"}
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--public-ip"])

    assert result.exit_code == 0
    assert "Overall: PASS" in result.output
    assert mock_run.call_count == 24
    first_remote = mock_run.call_args_list[0].args[0][-1]
    assert "cloud-init status --wait" in first_remote


@patch("edcloud.cli.subprocess.run")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_verify_fails_when_remote_checks_fail(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_run,
):
    mock_status.return_value = {"exists": True, "state": "running", "public_ip": "203.0.113.10"}
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="failed")

    runner = CliRunner()
    result = runner.invoke(main, ["verify", "--public-ip"])

    assert result.exit_code == 1
    assert "Overall: FAIL" in result.output


@patch("edcloud.cli._fetch_tailscale_auth_key_from_ssm", return_value="tailscale-test-key")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_load_tailscale_env_key_shell_export(
    _mock_creds,
    _mock_region,
    _mock_fetch,
):
    runner = CliRunner()
    result = runner.invoke(main, ["load-tailscale-env-key"])

    assert result.exit_code == 0
    assert "export TAILSCALE_AUTH_KEY=tailscale-test-key" in result.output


@patch("edcloud.cli._fetch_tailscale_auth_key_from_ssm", return_value="tailscale-test-key")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_load_tailscale_env_key_requires_output_mode(
    _mock_creds,
    _mock_region,
    _mock_fetch,
):
    runner = CliRunner()
    result = runner.invoke(main, ["load-tailscale-env-key", "--no-shell-export"])

    assert result.exit_code == 1
    assert "No output selected" in result.output


@patch("edcloud.cli.ssm_client")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_setup_ssm_tokens_stores_tokens(_mock_creds, _mock_region, mock_ssm_client):
    ssm = MagicMock()
    ssm.describe_parameters.return_value = {
        "Parameters": [
            {"Name": "/edcloud/github_token"},
            {"Name": "/edcloud/tailscale_auth_key"},
        ]
    }
    mock_ssm_client.return_value = ssm

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "setup-ssm-tokens",
            "--github-token",
            "ghp-test",
            "--tailscale-auth-key",
            "tskey-auth-test",
            "--no-prompt",
        ],
    )

    assert result.exit_code == 0, result.output
    assert ssm.put_parameter.call_count == 2
    put_calls = [call.kwargs["Name"] for call in ssm.put_parameter.call_args_list]
    assert "/edcloud/github_token" in put_calls
    assert "/edcloud/tailscale_auth_key" in put_calls


@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_setup_ssm_tokens_rejects_nonstandard_tailscale_key_without_prompt(
    _mock_creds, _mock_region
):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "setup-ssm-tokens",
            "--tailscale-auth-key",
            "bad-key",
            "--no-prompt",
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to store non-standard Tailscale key" in result.output


def test_sync_cline_auth_dry_run_success(tmp_path):
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(
        '{"openai-codex-oauth-credentials": {"token": "abc"}}',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync-cline-auth",
            "--source-secrets",
            str(secrets_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Would backup and sync files" in result.output


def test_sync_cline_auth_missing_expected_key_fails(tmp_path):
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text('{"other": true}', encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync-cline-auth", "--source-secrets", str(secrets_file), "--dry-run"],
    )

    assert result.exit_code == 1
    assert "Missing expected key in secrets.json" in result.output


@patch("edcloud.cli._run_checked")
def test_sync_cline_auth_runs_ssh_scp_flow(mock_run_checked, tmp_path):
    secrets_file = tmp_path / "secrets.json"
    global_state_file = tmp_path / "globalState.json"
    secrets_file.write_text(
        '{"openai-codex-oauth-credentials": {"token": "abc"}}',
        encoding="utf-8",
    )
    global_state_file.write_text('{"x": 1}', encoding="utf-8")

    mock_run_checked.return_value = MagicMock(stdout="")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync-cline-auth",
            "--remote",
            "ubuntu@edcloud",
            "--source-secrets",
            str(secrets_file),
            "--include-global-state",
        ],
    )

    assert result.exit_code == 0, result.output
    assert mock_run_checked.call_count == 5


def test_sync_cline_auth_missing_global_state_warns_and_continues_dry_run(tmp_path):
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(
        '{"openai-codex-oauth-credentials": {"token": "abc"}}',
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync-cline-auth",
            "--source-secrets",
            str(secrets_file),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Warning: globalState.json not found" in result.output
    assert "Would backup and sync files" in result.output


@patch("edcloud.cli._run_checked")
def test_sync_cline_auth_remote_diagnostics_runs_before_sync(mock_run_checked, tmp_path):
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(
        '{"openai-codex-oauth-credentials": {"token": "abc"}}',
        encoding="utf-8",
    )

    mock_run_checked.side_effect = [
        MagicMock(stdout="remote diagnostics:\n  user: ubuntu\n"),
        MagicMock(stdout=""),
        MagicMock(stdout=""),
        MagicMock(stdout=""),
        MagicMock(stdout=""),
    ]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "sync-cline-auth",
            "--source-secrets",
            str(secrets_file),
            "--remote-diagnostics",
            "--secrets-only",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "remote diagnostics:" in result.output
    assert mock_run_checked.call_count == 5
    first_cmd = mock_run_checked.call_args_list[0].args[0]
    assert first_cmd[0] == "ssh"
    assert "whoami" in first_cmd[-1]


@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_destroy_requires_confirm_instance_id(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_destroy,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}

    runner = CliRunner()
    result = runner.invoke(main, ["destroy", "--force"])

    assert result.exit_code == 1
    assert "requires explicit instance ID confirmation" in result.output
    assert "--confirm-instance-id i-abc123" in result.output
    mock_destroy.assert_not_called()


@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_destroy_with_matching_confirm_id_calls_destroy(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_destroy,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "destroy",
            "--force",
            "--confirm-instance-id",
            "i-abc123",
            "--skip-snapshot",
            "--skip-cleanup",
        ],
    )

    assert result.exit_code == 0
    mock_destroy.assert_called_once_with(force=True)


@patch("edcloud.cli.snapshot.list_snapshots")
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_destroy_require_fresh_snapshot_fails_without_recent_snapshot(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_destroy,
    mock_list_snapshots,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_list_snapshots.return_value = []

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "destroy",
            "--force",
            "--confirm-instance-id",
            "i-abc123",
            "--require-fresh-snapshot",
        ],
    )

    assert result.exit_code == 1
    assert "no fresh pre-change snapshot found" in result.output
    mock_destroy.assert_not_called()


@patch("edcloud.cli.snapshot.list_snapshots")
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_destroy_require_fresh_snapshot_passes_with_recent_snapshot(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_destroy,
    mock_list_snapshots,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_list_snapshots.return_value = [
        {
            "snapshot_id": "snap-abc123",
            "description": "pre-change-test",
            "state": "completed",
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
    ]

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "destroy",
            "--force",
            "--confirm-instance-id",
            "i-abc123",
            "--require-fresh-snapshot",
            "--skip-snapshot",
            "--skip-cleanup",
        ],
    )

    assert result.exit_code == 0
    assert "Using pre-change snapshot: snap-abc123" in result.output
    mock_destroy.assert_called_once_with(force=True)


@patch("edcloud.cli.tailscale.get_tailscale_ip", return_value="100.64.1.1")
@patch("edcloud.cli.ec2.start")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_up_calls_start(_mock_creds, _mock_region, mock_start, _mock_tailscale):
    runner = CliRunner()
    result = runner.invoke(main, ["up"])

    assert result.exit_code == 0
    mock_start.assert_called_once()
    assert "100.64.1.1" in result.output


@patch("edcloud.cli.ec2.stop")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_down_calls_stop(_mock_creds, _mock_region, mock_stop):
    runner = CliRunner()
    result = runner.invoke(main, ["down"])

    assert result.exit_code == 0
    mock_stop.assert_called_once()


@patch("edcloud.cli.tailscale.get_tailscale_ip", return_value="100.64.1.1")
@patch("edcloud.cli.tailscale.is_reachable", return_value=True)
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_status_displays_instance_info(
    _mock_creds, _mock_region, mock_status, _mock_reachable, _mock_tailscale
):
    mock_status.return_value = {
        "exists": True,
        "instance_id": "i-abc123",
        "state": "running",
        "instance_type": "t3a.medium",
        "public_ip": "3.3.3.3",
        "launch_time": "2026-02-15T10:00:00Z",
        "volumes": [{"volume_id": "vol-123", "size_gb": 40, "type": "gp3", "state": "in-use"}],
        "cost_estimate": {
            "compute_monthly": 13.54,
            "storage_monthly": 3.20,
            "total_monthly": 16.74,
            "note": "Assumes 4hrs/day runtime",
        },
    }

    runner = CliRunner()
    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "i-abc123" in result.output
    assert "running" in result.output
    assert "t3a.medium" in result.output
    assert "100.64.1.1" in result.output
    assert "yes" in result.output  # reachable
    assert "$16.74" in result.output


@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_status_shows_no_instance(_mock_creds, _mock_region, mock_status):
    mock_status.return_value = {"exists": False, "orphaned_resources": {}}

    runner = CliRunner()
    result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "No edcloud instance found" in result.output


@patch("edcloud.cleanup.run_cleanup_workflow", return_value=True)
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_destroy_cleanup_passes_allow_delete_state_volume(
    _mock_creds,
    _mock_region,
    mock_status,
    mock_destroy,
    mock_cleanup,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "destroy",
            "--force",
            "--confirm-instance-id",
            "i-abc123",
            "--skip-snapshot",
            "--allow-delete-state-volume",
        ],
    )

    assert result.exit_code == 0
    mock_destroy.assert_called_once_with(force=True)
    assert mock_cleanup.call_args.kwargs["allow_delete_state"] is True


@patch("edcloud.cli.tailscale.edcloud_name_conflicts")
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
def test_tailscale_reconcile_dry_run_reports_conflicts(
    _mock_available,
    mock_conflicts,
):
    mock_conflicts.return_value = [
        {
            "hostname": "edcloud",
            "ip": "100.64.1.42",
            "dns_name": "edcloud-2.tail.ts.net.",
            "online": False,
        }
    ]
    runner = CliRunner()
    result = runner.invoke(main, ["tailscale", "reconcile", "--dry-run"])
    assert result.exit_code == 1
    assert "Tailscale naming conflict detected" in result.output


# ---------------------------------------------------------------------------
# Tailscale check warning when CLI not available
# ---------------------------------------------------------------------------


@patch("edcloud.cli.tailscale.tailscale_available", return_value=False)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
@patch("edcloud.cli.ec2.start")
@patch("edcloud.cli.tailscale.get_tailscale_ip", return_value=None)
def test_tailscale_check_logs_warning_when_cli_not_found(
    _mock_ip,
    _mock_start,
    _mock_creds,
    _mock_region,
    _mock_available,
):
    """When tailscale binary is absent, a warning is printed to stderr."""
    runner = CliRunner()
    result = runner.invoke(main, ["up"])
    assert result.exit_code == 0
    assert "tailscale CLI not found" in result.output


# ---------------------------------------------------------------------------
# reprovision command
# ---------------------------------------------------------------------------


@patch("edcloud.cleanup.cleanup_orphaned_volumes", return_value=True)
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.snapshot.auto_snapshot_before_destroy")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_reprovision_snapshots_destroys_and_provisions(
    _mock_creds,
    _mock_region,
    _mock_available,
    _mock_conflicts,
    mock_snapshot,
    mock_status,
    mock_destroy,
    mock_provision,
    _mock_vol_cleanup,
):
    """reprovision: snapshot → destroy → provision in order."""
    mock_snapshot.return_value = ["snap-abc"]
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_provision.return_value = {
        "instance_id": "i-new",
        "security_group_id": "sg-new",
        "public_ip": "1.2.3.4",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reprovision",
            "--confirm-instance-id",
            "i-abc123",
            "--tailscale-auth-key-ssm-parameter",
            "/edcloud/tailscale_auth_key",
        ],
    )

    assert result.exit_code == 0, result.output
    mock_snapshot.assert_called_once()
    mock_destroy.assert_called_once_with(force=True)
    mock_provision.assert_called_once()
    assert "Next step: run 'edc verify' to confirm rebuild health." in result.output


@patch("edcloud.cleanup.cleanup_orphaned_volumes", return_value=True)
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.snapshot.auto_snapshot_before_destroy")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_reprovision_skip_snapshot_skips_snapshot(
    _mock_creds,
    _mock_region,
    _mock_available,
    _mock_conflicts,
    mock_snapshot,
    mock_status,
    mock_destroy,
    mock_provision,
    _mock_vol_cleanup,
):
    """reprovision --skip-snapshot does not call snapshot."""
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_provision.return_value = {
        "instance_id": "i-new",
        "security_group_id": "sg-new",
        "public_ip": "1.2.3.4",
    }

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reprovision",
            "--skip-snapshot",
            "--confirm-instance-id",
            "i-abc123",
            "--tailscale-auth-key-ssm-parameter",
            "/edcloud/tailscale_auth_key",
        ],
    )

    assert result.exit_code == 0, result.output
    mock_snapshot.assert_not_called()
    mock_provision.assert_called_once()


@patch("edcloud.cleanup.cleanup_orphaned_volumes", return_value=True)
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.snapshot.auto_snapshot_before_destroy")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_reprovision_surfaces_snapshot_ids_on_provision_failure(
    _mock_creds,
    _mock_region,
    _mock_available,
    _mock_conflicts,
    mock_snapshot,
    mock_status,
    mock_destroy,
    mock_provision,
    _mock_vol_cleanup,
):
    """On provision failure after destroy, snapshot IDs are shown prominently."""
    mock_snapshot.return_value = ["snap-abc123"]
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_provision.side_effect = RuntimeError("launch failed")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reprovision",
            "--confirm-instance-id",
            "i-abc123",
            "--tailscale-auth-key-ssm-parameter",
            "/edcloud/tailscale_auth_key",
        ],
    )

    assert result.exit_code == 1
    assert "snap-abc123" in result.output


@patch("edcloud.cli.run_reprovision_flow")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_reprovision_delegates_to_lifecycle_flow(
    _mock_creds,
    _mock_region,
    _mock_available,
    _mock_conflicts,
    mock_status,
    mock_flow,
):
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}
    mock_flow.return_value = (
        ["snap-abc123"],
        {"instance_id": "i-new", "security_group_id": "sg-new", "public_ip": "1.2.3.4"},
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reprovision",
            "--confirm-instance-id",
            "i-abc123",
            "--tailscale-auth-key-ssm-parameter",
            "/edcloud/tailscale_auth_key",
        ],
    )

    assert result.exit_code == 0
    assert mock_flow.called


@patch("edcloud.cli.ec2.destroy")
@patch("edcloud.cli.ec2.status")
@patch("edcloud.cli.tailscale.edcloud_name_conflicts", return_value=[])
@patch("edcloud.cli.tailscale.tailscale_available", return_value=True)
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_reprovision_requires_confirm_instance_id(
    _mock_creds,
    _mock_region,
    _mock_available,
    _mock_conflicts,
    mock_status,
    mock_destroy,
):
    """reprovision without --confirm-instance-id is rejected when an instance exists."""
    mock_status.return_value = {"exists": True, "instance_id": "i-abc123"}

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "reprovision",
            "--skip-snapshot",
            "--tailscale-auth-key-ssm-parameter",
            "/edcloud/tailscale_auth_key",
        ],
    )

    assert result.exit_code == 1
    assert "requires explicit instance ID confirmation" in result.output
    assert "--confirm-instance-id i-abc123" in result.output
    mock_destroy.assert_not_called()


# ---------------------------------------------------------------------------
# resize command
# ---------------------------------------------------------------------------


@patch("edcloud.cli.ec2.resize")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_resize_requires_at_least_one_option(_mock_creds, _mock_region, mock_resize):
    """resize without any options exits with error."""
    runner = CliRunner()
    result = runner.invoke(main, ["resize"])
    assert result.exit_code == 1
    assert "specify at least one" in result.output


@patch("edcloud.cli.ec2.resize")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_resize_instance_type_calls_ec2_resize(_mock_creds, _mock_region, mock_resize):
    """resize --instance-type delegates to ec2.resize."""
    mock_resize.return_value = {
        "instance_id": "i-abc123",
        "instance_type_old": "t3a.small",
        "instance_type_new": "t3a.medium",
    }

    runner = CliRunner()
    result = runner.invoke(main, ["resize", "--instance-type", "t3a.medium"])

    assert result.exit_code == 0, result.output
    mock_resize.assert_called_once_with(
        instance_type="t3a.medium",
        volume_size_gb=None,
        state_volume_size_gb=None,
    )


@patch("edcloud.cli.permissions.required_actions", return_value=["ec2:DescribeInstances"])
@patch("edcloud.cli.permissions.verify_required_actions")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_permissions_verify_success(
    _mock_creds,
    _mock_region,
    mock_verify,
    _mock_required,
):
    mock_verify.return_value = MagicMock(
        ok=True,
        principal_arn="arn:aws:iam::123:user/test",
        policy_source_arn="arn:aws:iam::123:user/test",
        missing_actions=(),
        detail="All required actions are allowed for selected profile(s).",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["permissions", "verify", "--profile", "core"])

    assert result.exit_code == 0
    assert "All required actions are allowed" in result.output


@patch("edcloud.cli.permissions.required_actions", return_value=["iam:PassRole"])
@patch("edcloud.cli.permissions.verify_required_actions")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_permissions_verify_failure_lists_missing_actions(
    _mock_creds,
    _mock_region,
    mock_verify,
    _mock_required,
):
    mock_verify.return_value = MagicMock(
        ok=False,
        principal_arn="arn:aws:iam::123:user/test",
        policy_source_arn="arn:aws:iam::123:user/test",
        missing_actions=("iam:PassRole",),
        detail="Missing required actions for selected edcloud command profile(s).",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["permissions", "verify", "--profile", "provision"])

    assert result.exit_code == 1
    assert "Missing actions" in result.output
    assert "iam:PassRole" in result.output


def test_permissions_policy_outputs_policy_json():
    runner = CliRunner()
    result = runner.invoke(main, ["permissions", "policy", "--profile", "core"])
    assert result.exit_code == 0
    assert '"Version": "2012-10-17"' in result.output
