"""Tests for edcloud.snapshot — mocked boto3 calls."""

from unittest.mock import MagicMock, patch

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE
from edcloud.snapshot import list_snapshots, prune_snapshots


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

    @patch("edcloud.snapshot._ec2_client")
    def test_uses_managed_tag_filter(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {"Snapshots": []}
        mock_client_fn.return_value = mock_client

        list_snapshots()

        kwargs = mock_client.describe_snapshots.call_args.kwargs
        assert kwargs["OwnerIds"] == ["self"]
        assert kwargs["Filters"] == [
            {"Name": f"tag:{MANAGER_TAG_KEY}", "Values": [MANAGER_TAG_VALUE]}
        ]


class TestPruneSnapshots:
    @patch("edcloud.snapshot._ec2_client")
    @patch("edcloud.snapshot.list_snapshots")
    def test_dry_run_prune(self, mock_list, mock_client_fn):
        mock_client_fn.return_value = MagicMock()
        mock_list.return_value = [
            {
                "snapshot_id": "snap-w1",
                "description": "weekly-snapshot",
                "start_time": "2026-02-14",
            },
            {
                "snapshot_id": "snap-w2",
                "description": "weekly-snapshot",
                "start_time": "2026-02-13",
            },
            {
                "snapshot_id": "snap-w3",
                "description": "weekly-snapshot",
                "start_time": "2026-02-12",
            },
            {
                "snapshot_id": "snap-m1",
                "description": "monthly-snapshot",
                "start_time": "2026-02-01",
            },
            {
                "snapshot_id": "snap-m2",
                "description": "monthly-snapshot",
                "start_time": "2026-01-01",
            },
            {
                "snapshot_id": "snap-pre",
                "description": "pre-change-foo",
                "start_time": "2026-02-15",
            },
        ]

        result = prune_snapshots(keep_weekly=2, keep_monthly=1, dry_run=True)

        assert result["delete_count"] == 2
        assert [s["snapshot_id"] for s in result["to_delete"]] == ["snap-w3", "snap-m2"]
        mock_client_fn.return_value.delete_snapshot.assert_not_called()

    @patch("edcloud.snapshot._ec2_client")
    @patch("edcloud.snapshot.list_snapshots")
    def test_apply_prune_deletes_snapshots(self, mock_list, mock_client_fn):
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_list.return_value = [
            {
                "snapshot_id": "snap-w1",
                "description": "weekly-snapshot",
                "start_time": "2026-02-14",
            },
            {
                "snapshot_id": "snap-w2",
                "description": "weekly-snapshot",
                "start_time": "2026-02-13",
            },
        ]

        result = prune_snapshots(keep_weekly=1, keep_monthly=3, dry_run=False)

        assert result["delete_count"] == 1
        mock_client.delete_snapshot.assert_called_once_with(SnapshotId="snap-w2")
