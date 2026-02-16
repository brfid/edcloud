"""Tests for cleanup volume protection behavior."""

from unittest.mock import MagicMock, patch

from edcloud.cleanup import cleanup_orphaned_volumes


def _vol(volume_id: str, role: str | None) -> dict:
    tags = [] if role is None else [{"Key": "edcloud:volume-role", "Value": role}]
    return {
        "VolumeId": volume_id,
        "Size": 10,
        "VolumeType": "gp3",
        "Tags": tags,
    }


@patch("edcloud.cleanup.boto3.client")
def test_cleanup_delete_mode_protects_state_and_unknown_by_default(mock_boto_client):
    ec2_client = MagicMock()
    ec2_client.describe_volumes.return_value = {
        "Volumes": [
            _vol("vol-root", "root"),
            _vol("vol-state", "state"),
            _vol("vol-unknown", None),
        ]
    }
    mock_boto_client.return_value = ec2_client

    ok = cleanup_orphaned_volumes(mode="delete", allow_delete_state=False)

    assert ok is True
    ec2_client.delete_volume.assert_called_once_with(VolumeId="vol-root")


@patch("edcloud.cleanup.boto3.client")
def test_cleanup_delete_mode_deletes_all_when_override_enabled(mock_boto_client):
    ec2_client = MagicMock()
    ec2_client.describe_volumes.return_value = {
        "Volumes": [
            _vol("vol-root", "root"),
            _vol("vol-state", "state"),
            _vol("vol-unknown", None),
        ]
    }
    mock_boto_client.return_value = ec2_client

    ok = cleanup_orphaned_volumes(mode="delete", allow_delete_state=True)

    assert ok is True
    deleted = [kwargs["VolumeId"] for _, kwargs in ec2_client.delete_volume.call_args_list]
    assert deleted == ["vol-root", "vol-state", "vol-unknown"]


@patch("edcloud.cleanup.boto3.client")
def test_cleanup_keep_mode_never_deletes(mock_boto_client):
    ec2_client = MagicMock()
    ec2_client.describe_volumes.return_value = {
        "Volumes": [
            _vol("vol-root", "root"),
            _vol("vol-state", "state"),
        ]
    }
    mock_boto_client.return_value = ec2_client

    ok = cleanup_orphaned_volumes(mode="keep", allow_delete_state=False)

    assert ok is True
    ec2_client.delete_volume.assert_not_called()
