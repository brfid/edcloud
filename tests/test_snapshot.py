"""Tests for edcloud.snapshot — mocked boto3 calls."""

from unittest.mock import MagicMock, patch

from edcloud.snapshot import list_snapshots


class TestListSnapshots:
    @patch("edcloud.snapshot._ec2_client")
    def test_empty_list(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {"Snapshots": []}
        mock_client_fn.return_value = mock_client

        result = list_snapshots()
        assert result == []

    @patch("edcloud.snapshot._ec2_client")
    def test_returns_sorted_snapshots(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {
            "Snapshots": [
                {
                    "SnapshotId": "snap-old",
                    "VolumeId": "vol-1",
                    "VolumeSize": 80,
                    "State": "completed",
                    "Progress": "100%",
                    "StartTime": "2026-02-10T10:00:00Z",
                    "Description": "old snap",
                    "Tags": [{"Key": "Name", "Value": "edcloud-snap-old"}],
                },
                {
                    "SnapshotId": "snap-new",
                    "VolumeId": "vol-1",
                    "VolumeSize": 80,
                    "State": "completed",
                    "Progress": "100%",
                    "StartTime": "2026-02-14T10:00:00Z",
                    "Description": "new snap",
                    "Tags": [{"Key": "Name", "Value": "edcloud-snap-new"}],
                },
            ]
        }
        mock_client_fn.return_value = mock_client

        result = list_snapshots()
        assert len(result) == 2
        assert result[0]["snapshot_id"] == "snap-new"  # newest first
        assert result[1]["snapshot_id"] == "snap-old"
