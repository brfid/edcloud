"""Tests for edcloud.snapshot — mocked boto3 calls."""

from unittest.mock import MagicMock, patch

from edcloud.config import MANAGER_TAG_KEY, MANAGER_TAG_VALUE
from edcloud.snapshot import list_snapshots, prune_snapshots


class TestListSnapshots:
    @patch("edcloud.snapshot.get_ec2_client")
    def test_empty_list(self, mock_client_fn):
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {"Snapshots": []}
        mock_client_fn.return_value = mock_client

        result = list_snapshots()
        assert result == []

    @patch("edcloud.snapshot.get_ec2_client")
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

    @patch("edcloud.snapshot.get_ec2_client")
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
    @patch("edcloud.snapshot.get_ec2_client")
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

    @patch("edcloud.snapshot.get_ec2_client")
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


class TestWaitForSnapshotCompletion:
    @patch("edcloud.snapshot.get_ec2_client")
    def test_returns_immediately_for_empty_list(self, mock_client_fn):
        """No API calls made for an empty snapshot list."""
        from edcloud.snapshot import wait_for_snapshot_completion
        wait_for_snapshot_completion([])
        mock_client_fn.assert_not_called()

    @patch("edcloud.snapshot.time.sleep")
    @patch("edcloud.snapshot.get_ec2_client")
    def test_returns_when_all_completed(self, mock_client_fn, mock_sleep):
        """Returns without sleeping when all snapshots are already completed."""
        from edcloud.snapshot import wait_for_snapshot_completion
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {
            "Snapshots": [
                {"SnapshotId": "snap-1", "State": "completed"},
                {"SnapshotId": "snap-2", "State": "completed"},
            ]
        }
        mock_client_fn.return_value = mock_client

        wait_for_snapshot_completion(["snap-1", "snap-2"])

        mock_client.describe_snapshots.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("edcloud.snapshot.get_ec2_client")
    def test_raises_on_error_state(self, mock_client_fn):
        """Raises RuntimeError when a snapshot enters error state."""
        import pytest

        from edcloud.snapshot import wait_for_snapshot_completion
        mock_client = MagicMock()
        mock_client.describe_snapshots.return_value = {
            "Snapshots": [
                {"SnapshotId": "snap-1", "State": "error", "StateMessage": "disk full"},
            ]
        }
        mock_client_fn.return_value = mock_client

        with pytest.raises(RuntimeError, match="error state"):
            wait_for_snapshot_completion(["snap-1"])


class TestFindRecentPrechangeSnapshot:
    @patch("edcloud.snapshot.list_snapshots")
    def test_returns_none_when_no_prechange_snapshots(self, mock_list):
        """Returns None when no pre-change snapshots exist."""
        from edcloud.snapshot import find_recent_prechange_snapshot
        mock_list.return_value = [
            {"snapshot_id": "snap-1", "description": "weekly-snapshot",
             "state": "completed", "start_time": "2026-02-14T10:00:00Z"},
        ]
        result = find_recent_prechange_snapshot(max_age_minutes=120)
        assert result is None

    @patch("edcloud.snapshot.list_snapshots")
    def test_returns_most_recent_prechange_snapshot(self, mock_list):
        """Returns the most recent completed pre-change snapshot within age limit."""
        from datetime import datetime, timedelta, timezone

        from edcloud.snapshot import find_recent_prechange_snapshot
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        old_ts = (now - timedelta(minutes=200)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        mock_list.return_value = [
            {"snapshot_id": "snap-old", "description": "pre-change-old",
             "state": "completed", "start_time": old_ts},
            {"snapshot_id": "snap-recent", "description": "pre-change-recent",
             "state": "completed", "start_time": recent_ts},
        ]
        result = find_recent_prechange_snapshot(max_age_minutes=120)
        assert result is not None
        assert result["snapshot_id"] == "snap-recent"

    @patch("edcloud.snapshot.list_snapshots")
    def test_ignores_non_completed_snapshots(self, mock_list):
        """Ignores pending or in-progress snapshots."""
        from datetime import datetime, timedelta, timezone

        from edcloud.snapshot import find_recent_prechange_snapshot
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        mock_list.return_value = [
            {"snapshot_id": "snap-pending", "description": "pre-change-test",
             "state": "pending", "start_time": recent_ts},
        ]
        result = find_recent_prechange_snapshot(max_age_minutes=120)
        assert result is None
