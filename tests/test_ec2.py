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

        rendered = _render_user_data("tskey-test-123", "my-hostname")
        assert "tskey-test-123" in rendered
        assert "my-hostname" in rendered
        assert "${TAILSCALE_AUTH_KEY}" not in rendered
        assert "${TAILSCALE_HOSTNAME}" not in rendered


class TestProvision:
    @patch("edcloud.ec2._ec2_client")
    @patch("edcloud.ec2._find_instance", return_value=None)
    @patch("edcloud.ec2._find_security_group", return_value="sg-abc123")
    @patch("edcloud.ec2._render_user_data", return_value="#cloud-config")
    @patch("edcloud.ec2._resolve_ami", return_value="ami-abc123")
    def test_includes_persistent_state_volume(
        self,
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
        provision(cfg, "tskey-auth-test")

        kwargs = mock_client.run_instances.call_args.kwargs
        block_mappings = kwargs["BlockDeviceMappings"]
        assert len(block_mappings) == 2
        assert block_mappings[0]["DeviceName"] == "/dev/sda1"
        assert block_mappings[1]["DeviceName"] == "/dev/sdf"
        assert block_mappings[1]["Ebs"]["VolumeSize"] == 10
        assert block_mappings[1]["Ebs"]["DeleteOnTermination"] is False
