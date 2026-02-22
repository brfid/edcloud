"""Tests for CLI behavior."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from edcloud.cli import main


@patch("edcloud.cli.boto3.client")
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_reads_tailscale_key_from_ssm(
    _mock_creds,
    _mock_region,
    mock_provision,
    mock_boto3_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tailscale-test-key"}}
    mock_boto3_client.return_value = ssm
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
    _, passed_key = mock_provision.call_args.args
    assert passed_key == "tailscale-test-key"
    ssm.get_parameter.assert_called_once_with(
        Name="/edcloud/tailscale_auth_key",
        WithDecryption=True,
    )


@patch("edcloud.cli.boto3.client")
@patch("edcloud.cli.ec2.provision")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_reads_tailscale_key_from_ssm_envvar(
    _mock_creds,
    _mock_region,
    mock_provision,
    mock_boto3_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tailscale-test-key"}}
    mock_boto3_client.return_value = ssm
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
    _, passed_key = mock_provision.call_args.args
    assert passed_key == "tailscale-test-key"
    ssm.get_parameter.assert_called_once_with(
        Name="/edcloud/tailscale_auth_key",
        WithDecryption=True,
    )


@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_provision_requires_tailscale_key(_mock_creds, _mock_region):
    runner = CliRunner()
    result = runner.invoke(main, ["provision"])

    assert result.exit_code == 1
    assert "Tailscale auth key required." in result.output


@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_snapshot_rejects_conflicting_modes(_mock_creds, _mock_region):
    runner = CliRunner()
    result = runner.invoke(main, ["snapshot", "--list", "--prune"])

    assert result.exit_code == 1
    assert "use either --list or --prune" in result.output


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
    assert mock_run.call_count == 14


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


@patch("edcloud.cli.boto3.client")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_load_tailscale_env_key_shell_export(
    _mock_creds,
    _mock_region,
    mock_boto3_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tailscale-test-key"}}
    mock_boto3_client.return_value = ssm

    runner = CliRunner()
    result = runner.invoke(main, ["load-tailscale-env-key"])

    assert result.exit_code == 0
    assert "export TAILSCALE_AUTH_KEY=tailscale-test-key" in result.output
    ssm.get_parameter.assert_called_once_with(
        Name="/edcloud/tailscale_auth_key",
        WithDecryption=True,
    )


@patch("edcloud.cli.boto3.client")
@patch("edcloud.cli.get_region", return_value="us-east-1")
@patch("edcloud.cli.check_aws_credentials", return_value=(True, "ok"))
def test_load_tailscale_env_key_requires_output_mode(
    _mock_creds,
    _mock_region,
    mock_boto3_client,
):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tailscale-test-key"}}
    mock_boto3_client.return_value = ssm

    runner = CliRunner()
    result = runner.invoke(main, ["load-tailscale-env-key", "--no-shell-export"])

    assert result.exit_code == 1
    assert "No output selected" in result.output


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
    result = runner.invoke(main, ["destroy", "--force", "--confirm-instance-id", "i-abc123"])

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
        ],
    )

    assert result.exit_code == 0
    assert "Using pre-change snapshot: snap-abc123" in result.output
    mock_destroy.assert_called_once_with(force=True)
