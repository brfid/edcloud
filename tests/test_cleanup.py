"""Tests for cleanup volume protection behavior."""

from unittest.mock import MagicMock, patch

from edcloud.cleanup import (
    cleanup_orphaned_volumes,
    cleanup_tailscale_devices,
    run_cleanup_workflow,
)


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


# ---------------------------------------------------------------------------
# cleanup_tailscale_devices
# ---------------------------------------------------------------------------


@patch("edcloud.cleanup.tailscale.cleanup_offline_edcloud_devices")
def test_cleanup_tailscale_devices_no_offline_devices(mock_cleanup):
    """Returns True immediately when there are no offline devices."""
    mock_cleanup.return_value = (0, "")
    result = cleanup_tailscale_devices(interactive=False)
    assert result is True
    mock_cleanup.assert_called_once()


@patch("edcloud.cleanup.tailscale.cleanup_offline_edcloud_devices")
def test_cleanup_tailscale_devices_noninteractive_always_returns_true(mock_cleanup):
    """Non-interactive mode returns True even when devices are found."""
    mock_cleanup.return_value = (2, "Found 2 offline devices. Remove them in Tailscale admin.")
    result = cleanup_tailscale_devices(interactive=False)
    assert result is True


# ---------------------------------------------------------------------------
# run_cleanup_workflow
# ---------------------------------------------------------------------------


@patch("edcloud.cleanup.cleanup_orphaned_volumes")
@patch("edcloud.cleanup.cleanup_tailscale_devices")
def test_run_cleanup_workflow_completes_noninteractive(mock_ts_cleanup, mock_vol_cleanup):
    """Workflow returns True when both steps succeed in non-interactive mode."""
    mock_ts_cleanup.return_value = True
    mock_vol_cleanup.return_value = True

    result = run_cleanup_workflow("post-destroy", skip_snapshot=True, interactive=False)

    assert result is True
    mock_ts_cleanup.assert_called_once_with(interactive=False)
    mock_vol_cleanup.assert_called_once_with(mode="keep", allow_delete_state=False)


@patch("edcloud.cleanup.cleanup_orphaned_volumes")
@patch("edcloud.cleanup.cleanup_tailscale_devices")
def test_run_cleanup_workflow_aborts_when_tailscale_step_fails(mock_ts_cleanup, mock_vol_cleanup):
    """Workflow returns False when Tailscale cleanup step returns False."""
    mock_ts_cleanup.return_value = False
    mock_vol_cleanup.return_value = True

    result = run_cleanup_workflow("pre-provision", skip_snapshot=True, interactive=True)

    assert result is False
    mock_vol_cleanup.assert_not_called()


@patch("edcloud.cleanup.cleanup_orphaned_volumes")
@patch("edcloud.cleanup.cleanup_tailscale_devices")
def test_run_cleanup_workflow_aborts_when_volume_step_fails(mock_ts_cleanup, mock_vol_cleanup):
    """Workflow returns False when volume cleanup step returns False."""
    mock_ts_cleanup.return_value = True
    mock_vol_cleanup.return_value = False

    result = run_cleanup_workflow("pre-provision", skip_snapshot=True, interactive=True)

    assert result is False
