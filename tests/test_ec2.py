"""Tests for edcloud.ec2 — mocked boto3 calls."""

from unittest.mock import MagicMock

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE
from edcloud.ec2 import _find_instance, _find_security_group, _managed_filter


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


class TestFindSecurityGroup:
    def test_returns_none_when_no_groups(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {"SecurityGroups": []}
        assert _find_security_group(mock_client) is None

    def test_returns_group_id(self):
        mock_client = MagicMock()
        mock_client.describe_security_groups.return_value = {
            "SecurityGroups": [{"GroupId": "sg-abc123"}]
        }
        result = _find_security_group(mock_client)
        assert result == "sg-abc123"


class TestUserDataRendering:
    def test_render_substitutes_variables(self):
        from edcloud.ec2 import _render_user_data

        rendered = _render_user_data("tskey-test-123", "my-hostname")
        assert "tskey-test-123" in rendered
        assert "my-hostname" in rendered
        assert "${TAILSCALE_AUTH_KEY}" not in rendered
        assert "${TAILSCALE_HOSTNAME}" not in rendered
