"""Tests for CLI behavior."""

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
    ssm.get_parameter.return_value = {"Parameter": {"Value": "tskey-auth-from-ssm"}}
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
    assert passed_key == "tskey-auth-from-ssm"
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
    assert mock_run.call_count == 7


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
