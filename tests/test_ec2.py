"""Tests for edcloud.ec2 — mocked boto3 calls."""

from unittest.mock import MagicMock, patch

import pytest

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE, InstanceConfig
from edcloud.ec2 import (
    TagDriftError,
    _find_instance,
    _find_security_group,
    _managed_filter,
    provision,
)


class TestManagedFilter:
    def test_filter_shape(self):
        f = _managed_filter()
        assert len(f) == 1
        assert f[0]["Name"] == f"tag:{MANAGER_TAG_KEY}"
        assert f[0]["Values"] == [MANAGER_TAG_VALUE]


class TestFindInstance:
    def test_returns_none_when_no_instances(self):
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": []}
        assert _find_instance(mock_client) is None

    def test_returns_instance_when_found(self):
        inst = {"InstanceId": "i-abc123", "State": {"Name": "running"}}
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": [{"Instances": [inst]}]}
        result = _find_instance(mock_client)
        assert result is not None
        assert result["InstanceId"] == "i-abc123"

    def test_uses_managed_tag_filter(self):
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": []}

        _find_instance(mock_client)

        kwargs = mock_client.describe_instances.call_args_list[0].kwargs
        expected = {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]}
        assert expected in kwargs["Filters"]

    def test_raises_on_duplicate_managed_instances(self):
        inst1 = {"InstanceId": "i-one", "State": {"Name": "running"}}
        inst2 = {"InstanceId": "i-two", "State": {"Name": "stopped"}}
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {
            "Reservations": [{"Instances": [inst1, inst2]}]
        }

        with pytest.raises(TagDriftError, match="multiple managed instances"):
            _find_instance(mock_client)

    def test_raises_on_untagged_named_instance(self):
        named_untagged = {
            "InstanceId": "i-untagged",
            "State": {"Name": "running"},
            "Tags": [{"Key": "Name", "Value": "edcloud"}],
        }
        mock_client = MagicMock()
        mock_client.describe_instances.side_effect = [
            {"Reservations": []},  # managed lookup
            {"Reservations": [{"Instances": [named_untagged]}]},  # Name=edcloud lookup
        ]

        with pytest.raises(TagDriftError, match="missing `edcloud:managed=true`"):
            _find_instance(mock_client)


class TestFindSecurityGroup:
    def test_returns_none_when_no_groups(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {"SecurityGroups": []}
        assert _find_security_group(mock_client) is None

    def test_returns_group_id(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-abc123",
                    "Tags": [{"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE}],
                }
            ]
        }
        result = _find_security_group(mock_client)
        assert result == "sg-abc123"

    def test_uses_group_name_filter(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {"SecurityGroups": []}

        _find_security_group(mock_client)

        kwargs = mock_client.describe_security_groups.call_args.kwargs
        assert kwargs["Filters"] == [{"Name": "group-name", "Values": ["edcloud-sg"]}]

    def test_raises_on_untagged_group(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {
            "SecurityGroups": [{"GroupId": "sg-untagged", "Tags": []}]
        }

        with pytest.raises(TagDriftError, match="missing `edcloud:managed=true`"):
            _find_security_group(mock_client)

    def test_raises_on_duplicate_managed_groups(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {
            "SecurityGroups": [
                {
                    "GroupId": "sg-one",
                    "Tags": [{"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE}],
                },
                {
                    "GroupId": "sg-two",
                    "Tags": [{"Key": MANAGER_TAG_KEY, "Value": MANAGER_TAG_VALUE}],
                },
            ]
        }

        with pytest.raises(TagDriftError, match="multiple managed security groups"):
            _find_security_group(mock_client)


class TestUserDataRendering:
    def test_render_substitutes_variables(self):
        from edcloud.ec2 import _render_user_data

        rendered = _render_user_data(
            "/edcloud/tailscale_auth_key",
            "my-hostname",
            "us-east-1",
        )
        assert "/edcloud/tailscale_auth_key" in rendered
        assert "my-hostname" in rendered
        assert "us-east-1" in rendered
        assert "${TAILSCALE_AUTH_KEY_SSM_PARAMETER}" not in rendered
        assert "${TAILSCALE_HOSTNAME}" not in rendered
        assert "${AWS_REGION}" not in rendered


class TestInputValidation:
    def test_valid_hostname_passes(self):
        from edcloud.ec2 import _validate_user_data_inputs

        # Should not raise
        _validate_user_data_inputs("edcloud", tailscale_auth_key="tskey-auth-valid123")
        _validate_user_data_inputs("my-host-123", tailscale_auth_key="tskey-valid")
        _validate_user_data_inputs("a", tailscale_auth_key="tskey")

    def test_invalid_hostname_raises(self):
        from edcloud.ec2 import _validate_user_data_inputs

        with pytest.raises(ValueError, match="Invalid tailscale_hostname"):
            _validate_user_data_inputs("-invalid", tailscale_auth_key="tskey")

        with pytest.raises(ValueError, match="Invalid tailscale_hostname"):
            _validate_user_data_inputs("invalid-", tailscale_auth_key="tskey")

        with pytest.raises(ValueError, match="Invalid tailscale_hostname"):
            _validate_user_data_inputs("host_name", tailscale_auth_key="tskey")

        with pytest.raises(ValueError, match="Invalid tailscale_hostname"):
            _validate_user_data_inputs("host name", tailscale_auth_key="tskey")

    def test_injection_attempts_rejected(self):
        from edcloud.ec2 import _validate_user_data_inputs

        dangerous_auth_keys = [
            "tskey-$(whoami)",
            "tskey-`id`",
            "tskey; rm -rf /",
            "tskey\nmalicious",
            'tskey"bad',
            "tskey'bad",
            "tskey|cat /etc/passwd",
            "tskey&background",
        ]

        for dangerous_key in dangerous_auth_keys:
            with pytest.raises(ValueError, match="dangerous character"):
                _validate_user_data_inputs("edcloud", tailscale_auth_key=dangerous_key)

    def test_valid_ssm_parameter_passes(self):
        from edcloud.ec2 import _validate_user_data_inputs

        _validate_user_data_inputs(
            "edcloud",
            tailscale_auth_key_ssm_parameter="/edcloud/tailscale_auth_key",
        )
        _validate_user_data_inputs(
            "edcloud",
            tailscale_auth_key_ssm_parameter="/my-app/nested/param_name.value",
        )

    def test_invalid_ssm_parameter_raises(self):
        from edcloud.ec2 import _validate_user_data_inputs

        with pytest.raises(ValueError, match="Invalid tailscale_auth_key_ssm_parameter"):
            _validate_user_data_inputs(
                "edcloud",
                tailscale_auth_key_ssm_parameter="/bad/param;injection",
            )

    def test_valid_region_passes(self):
        from edcloud.ec2 import _validate_user_data_inputs

        _validate_user_data_inputs("edcloud", aws_region="us-east-1")
        _validate_user_data_inputs("edcloud", aws_region="eu-west-2")
        _validate_user_data_inputs("edcloud", aws_region="ap-southeast-3")

    def test_invalid_region_raises(self):
        from edcloud.ec2 import _validate_user_data_inputs

        with pytest.raises(ValueError, match="Invalid aws_region"):
            _validate_user_data_inputs("edcloud", aws_region="USEAST1")

        with pytest.raises(ValueError, match="Invalid aws_region"):
            _validate_user_data_inputs("edcloud", aws_region="us-east-1; whoami")


class TestProvision:
    @patch("edcloud.ec2._ec2_client")
    @patch("edcloud.ec2._find_instance", return_value=None)
    @patch("edcloud.ec2._find_security_group", return_value="sg-abc123")
    @patch("edcloud.ec2._render_user_data", return_value="#cloud-config")
    @patch("edcloud.ec2._resolve_ami", return_value="ami-abc123")
    @patch("edcloud.ec2._get_aws_region", return_value="us-east-1")
    @patch(
        "edcloud.ec2.ensure_instance_profile",
        return_value="arn:aws:iam::123:instance-profile/edcloud",
    )
    def test_includes_persistent_state_volume(
        self,
        _mock_ensure_profile,
        _mock_get_region,
        _mock_resolve_ami,
        _mock_render_user_data,
        _mock_find_security_group,
        _mock_find_instance,
        mock_ec2_client,
    ):
        mock_client = MagicMock()
        mock_client.run_instances.return_value = {"Instances": [{"InstanceId": "i-abc123"}]}
        mock_client.get_waiter.return_value = MagicMock()
        mock_ec2_client.return_value = mock_client

        cfg = InstanceConfig()
        provision(cfg)

        kwargs = mock_client.run_instances.call_args.kwargs
        block_mappings = kwargs["BlockDeviceMappings"]
        assert len(block_mappings) == 2
        assert block_mappings[0]["DeviceName"] == "/dev/sda1"
        assert block_mappings[1]["DeviceName"] == "/dev/sdf"
        assert block_mappings[1]["Ebs"]["VolumeSize"] == 10
        assert block_mappings[1]["Ebs"]["DeleteOnTermination"] is False
        # Verify IMDS settings
        metadata_opts = kwargs["MetadataOptions"]
        assert metadata_opts["HttpTokens"] == "required"
        assert metadata_opts["HttpPutResponseHopLimit"] == 1
        # Verify IAM instance profile is attached
        assert "IamInstanceProfile" in kwargs
        assert kwargs["IamInstanceProfile"]["Arn"] == "arn:aws:iam::123:instance-profile/edcloud"


class TestStart:
    @patch("edcloud.ec2._ec2_client")
    @patch("edcloud.ec2._find_instance")
    def test_starts_stopped_instance(
        self, mock_find_instance: MagicMock, mock_client_fn: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_find_instance.side_effect = [
            {"InstanceId": "i-abc123", "State": {"Name": "stopped"}},
            {"InstanceId": "i-abc123", "PublicIpAddress": "3.3.3.3"},
        ]
        mock_client_fn.return_value = mock_client

        from edcloud.ec2 import start

        result = start()

        assert result == "i-abc123"
        mock_client.start_instances.assert_called_once_with(InstanceIds=["i-abc123"])
        mock_client.get_waiter.assert_called_once_with("instance_running")

    @patch("edcloud.ec2._find_instance")
    def test_noop_when_already_running(self, mock_find_instance: MagicMock) -> None:
        mock_find_instance.return_value = {"InstanceId": "i-abc123", "State": {"Name": "running"}}

        from edcloud.ec2 import start

        result = start()

        assert result == "i-abc123"


class TestStop:
    @patch("edcloud.ec2._ec2_client")
    @patch("edcloud.ec2._find_instance")
    def test_stops_running_instance(
        self, mock_find_instance: MagicMock, mock_client_fn: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_find_instance.return_value = {"InstanceId": "i-abc123", "State": {"Name": "running"}}
        mock_client_fn.return_value = mock_client

        from edcloud.ec2 import stop

        result = stop()

        assert result == "i-abc123"
        mock_client.stop_instances.assert_called_once_with(InstanceIds=["i-abc123"])
        mock_client.get_waiter.assert_called_once_with("instance_stopped")

    @patch("edcloud.ec2._find_instance")
    def test_noop_when_already_stopped(self, mock_find_instance: MagicMock) -> None:
        mock_find_instance.return_value = {"InstanceId": "i-abc123", "State": {"Name": "stopped"}}

        from edcloud.ec2 import stop

        result = stop()

        assert result == "i-abc123"


class TestStatus:
    @patch("edcloud.ec2._ec2_client")
    @patch("edcloud.ec2._find_instance")
    def test_returns_full_status(
        self, mock_find_instance: MagicMock, mock_client_fn: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_find_instance.return_value = {
            "InstanceId": "i-abc123",
            "State": {"Name": "running"},
            "InstanceType": "t3a.medium",
            "PublicIpAddress": "3.3.3.3",
            "LaunchTime": "2026-02-15T10:00:00Z",
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-123"}}],
        }
        mock_client.describe_volumes.return_value = {
            "Volumes": [
                {"VolumeId": "vol-123", "Size": 40, "VolumeType": "gp3", "State": "in-use"}
            ]
        }
        mock_client_fn.return_value = mock_client

        from edcloud.ec2 import status

        result = status()

        assert result["exists"] is True
        assert result["instance_id"] == "i-abc123"
        assert result["state"] == "running"
        assert result["instance_type"] == "t3a.medium"
        assert result["public_ip"] == "3.3.3.3"
        assert len(result["volumes"]) == 1
        assert result["volumes"][0]["volume_id"] == "vol-123"
        assert "cost_estimate" in result
        assert result["cost_estimate"]["compute_monthly"] > 0
