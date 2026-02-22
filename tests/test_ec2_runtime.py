"""Runtime behavior tests for ec2 guardrails."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from edcloud.ec2 import TagDriftError, start, status


@patch("edcloud.ec2._find_instance", return_value=None)
@patch("edcloud.ec2._ec2_client")
def test_status_includes_orphaned_resources(mock_client_fn, _mock_find_instance):
    mock_client = MagicMock()
    mock_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-abc123", "GroupName": "edcloud-sg"}]
    }
    mock_client.describe_volumes.return_value = {
        "Volumes": [{"VolumeId": "vol-abc123", "State": "available"}]
    }
    mock_client_fn.return_value = mock_client

    result = status()

    assert result["exists"] is False
    assert result["orphaned_resources"]["security_groups"] == ["sg-abc123"]
    assert result["orphaned_resources"]["volumes"] == ["vol-abc123"]


@patch("edcloud.ec2._find_instance", return_value=None)
@patch("edcloud.ec2._ec2_client")
def test_start_raises_with_orphaned_resources(mock_client_fn, _mock_find_instance):
    mock_client = MagicMock()
    mock_client.describe_security_groups.return_value = {
        "SecurityGroups": [{"GroupId": "sg-abc123", "GroupName": "edcloud-sg"}]
    }
    mock_client.describe_volumes.return_value = {"Volumes": []}
    mock_client_fn.return_value = mock_client

    with pytest.raises(TagDriftError, match="orphaned managed resources"):
        start()
