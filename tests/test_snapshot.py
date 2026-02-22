"""Tests for edcloud.snapshot — mocked boto3 calls."""

from unittest.mock import MagicMock, patch

from edcloud.config import (
    MANAGER_TAG_KEY,
    MANAGER_TAG_VALUE,
    STATE_VOLUME_ROLE,
    VOLUME_ROLE_TAG_KEY,
)
from edcloud.snapshot import create_snapshot, list_snapshots, prune_snapshots, run_restore_drill


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
    def test_dry_run_keeps_newest(self, mock_list, mock_client_fn):
        """Dry run identifies oldest snapshots beyond keep_last; does not delete."""
        mock_client_fn.return_value = MagicMock()
        mock_list.return_value = [
            {
                "snapshot_id": "snap-1",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-21",
            },
            {
                "snapshot_id": "snap-2",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-17",
            },
            {"snapshot_id": "snap-3", "description": "pre-change-foo", "start_time": "2026-02-16"},
            {
                "snapshot_id": "snap-4",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-15",
            },
        ]

        result = prune_snapshots(keep_last=3, dry_run=True)

        assert result["delete_count"] == 1
        assert result["to_delete"][0]["snapshot_id"] == "snap-4"
        mock_client_fn.return_value.delete_snapshot.assert_not_called()

    @patch("edcloud.snapshot.get_ec2_client")
    @patch("edcloud.snapshot.list_snapshots")
    def test_apply_prune_deletes_oldest(self, mock_list, mock_client_fn):
        """Apply mode deletes all snapshots beyond keep_last."""
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_list.return_value = [
            {
                "snapshot_id": "snap-1",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-21",
            },
            {
                "snapshot_id": "snap-2",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-17",
            },
            {
                "snapshot_id": "snap-3",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-16",
            },
        ]

        result = prune_snapshots(keep_last=2, dry_run=False)

        assert result["delete_count"] == 1
        mock_client.delete_snapshot.assert_called_once_with(SnapshotId="snap-3")

    @patch("edcloud.snapshot.get_ec2_client")
    @patch("edcloud.snapshot.list_snapshots")
    def test_nothing_to_prune(self, mock_list, mock_client_fn):
        """Returns zero deletions when snapshot count is within keep_last."""
        mock_client_fn.return_value = MagicMock()
        mock_list.return_value = [
            {
                "snapshot_id": "snap-1",
                "description": "auto-pre-destroy",
                "start_time": "2026-02-21",
            },
        ]

        result = prune_snapshots(keep_last=3, dry_run=True)

        assert result["delete_count"] == 0
        assert result["to_delete"] == []


class TestCreateSnapshot:
    def _make_instance(self, vol_ids):
        return {
            "InstanceId": "i-abc",
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": v}} for v in vol_ids],
        }

    def _make_volume(self, vol_id, role):
        return {
            "VolumeId": vol_id,
            "Tags": [
                {"Key": VOLUME_ROLE_TAG_KEY, "Value": role},
                {"Key": "edcloud:managed", "Value": "true"},
            ],
        }

    @patch("edcloud.snapshot.get_ec2_client")
    @patch("edcloud.snapshot.find_instance")
    def test_only_snapshots_state_volumes(self, mock_find, mock_client_fn):
        """create_snapshot skips root-tagged volumes and only snapshots state volumes."""
        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2
        mock_find.return_value = self._make_instance(["vol-root", "vol-state"])
        mock_ec2.describe_volumes.return_value = {
            "Volumes": [
                self._make_volume("vol-root", "root"),
                self._make_volume("vol-state", STATE_VOLUME_ROLE),
            ]
        }
        mock_ec2.create_snapshot.return_value = {"SnapshotId": "snap-new"}

        create_snapshot("test-desc")

        calls = [c.kwargs["VolumeId"] for c in mock_ec2.create_snapshot.call_args_list]
        assert calls == ["vol-state"]
        assert "vol-root" not in calls

    @patch("edcloud.snapshot.get_ec2_client")
    @patch("edcloud.snapshot.find_instance")
    def test_falls_back_to_all_volumes_when_no_state_tag(self, mock_find, mock_client_fn):
        """Falls back to snapshotting all volumes if none have the state role tag."""
        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2
        mock_find.return_value = self._make_instance(["vol-a", "vol-b"])
        mock_ec2.describe_volumes.return_value = {
            "Volumes": [
                {"VolumeId": "vol-a", "Tags": []},
                {"VolumeId": "vol-b", "Tags": []},
            ]
        }
        mock_ec2.create_snapshot.return_value = {"SnapshotId": "snap-new"}

        create_snapshot("fallback-test")

        calls = [c.kwargs["VolumeId"] for c in mock_ec2.create_snapshot.call_args_list]
        assert set(calls) == {"vol-a", "vol-b"}


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
            {
                "snapshot_id": "snap-1",
                "description": "weekly-snapshot",
                "state": "completed",
                "start_time": "2026-02-14T10:00:00Z",
            },
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
            {
                "snapshot_id": "snap-old",
                "description": "pre-change-old",
                "state": "completed",
                "start_time": old_ts,
            },
            {
                "snapshot_id": "snap-recent",
                "description": "pre-change-recent",
                "state": "completed",
                "start_time": recent_ts,
            },
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
            {
                "snapshot_id": "snap-pending",
                "description": "pre-change-test",
                "state": "pending",
                "start_time": recent_ts,
            },
        ]
        result = find_recent_prechange_snapshot(max_age_minutes=120)
        assert result is None


class TestRunRestoreDrill:
    @patch("edcloud.snapshot.get_ec2_client")
    def test_uses_latest_completed_snapshot_and_cleans_up(self, mock_client_fn):
        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2

        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-state", "AvailabilityZone": "us-east-1a"}]
        }
        mock_ec2.describe_snapshots.return_value = {
            "Snapshots": [
                {
                    "SnapshotId": "snap-old",
                    "VolumeId": "vol-state",
                    "State": "completed",
                    "StartTime": "2026-02-01T10:00:00Z",
                },
                {
                    "SnapshotId": "snap-new",
                    "VolumeId": "vol-state",
                    "State": "completed",
                    "StartTime": "2026-02-10T10:00:00Z",
                },
            ]
        }
        mock_ec2.create_volume.return_value = {"VolumeId": "vol-temp"}

        result = run_restore_drill()

        assert result["snapshot_id"] == "snap-new"
        assert result["temporary_volume_id"] == "vol-temp"
        mock_ec2.delete_volume.assert_called_once_with(VolumeId="vol-temp")

    @patch("edcloud.snapshot.get_ec2_client")
    def test_validates_explicit_snapshot_volume_match(self, mock_client_fn):
        import pytest

        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2

        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-state", "AvailabilityZone": "us-east-1a"}]
        }
        mock_ec2.describe_snapshots.return_value = {
            "Snapshots": [
                {
                    "SnapshotId": "snap-x",
                    "VolumeId": "vol-other",
                    "State": "completed",
                }
            ]
        }

        with pytest.raises(RuntimeError, match="expected vol-state"):
            run_restore_drill(snapshot_id="snap-x")

    @patch("edcloud.snapshot.get_ec2_client")
    def test_attach_and_detach_when_instance_id_provided(self, mock_client_fn):
        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2

        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-state", "AvailabilityZone": "us-east-1a"}]
        }
        mock_ec2.describe_snapshots.return_value = {
            "Snapshots": [
                {
                    "SnapshotId": "snap-new",
                    "VolumeId": "vol-state",
                    "State": "completed",
                    "StartTime": "2026-02-10T10:00:00Z",
                }
            ]
        }
        mock_ec2.create_volume.return_value = {"VolumeId": "vol-temp"}

        result = run_restore_drill(instance_id="i-abc123", device_name="/dev/sdh")

        assert result["attached_to_instance"] is True
        mock_ec2.attach_volume.assert_called_once_with(
            VolumeId="vol-temp", InstanceId="i-abc123", Device="/dev/sdh"
        )
        mock_ec2.detach_volume.assert_called_once_with(VolumeId="vol-temp")

    @patch("edcloud.snapshot.get_ec2_client")
    def test_keep_temporary_volume_skips_delete(self, mock_client_fn):
        mock_ec2 = MagicMock()
        mock_client_fn.return_value = mock_ec2

        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-state", "AvailabilityZone": "us-east-1a"}]
        }
        mock_ec2.describe_snapshots.return_value = {
            "Snapshots": [
                {
                    "SnapshotId": "snap-new",
                    "VolumeId": "vol-state",
                    "State": "completed",
                    "StartTime": "2026-02-10T10:00:00Z",
                }
            ]
        }
        mock_ec2.create_volume.return_value = {"VolumeId": "vol-temp"}

        result = run_restore_drill(keep_temporary_volume=True)

        assert result["temporary_volume_kept"] is True
        mock_ec2.delete_volume.assert_not_called()