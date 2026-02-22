"""Tests for shared lifecycle orchestration helpers."""

from unittest.mock import MagicMock

from edcloud.lifecycle import run_reprovision_flow


def test_run_reprovision_flow_happy_path_with_existing_instance() -> None:
    destroy_instance = MagicMock()
    cleanup_orphaned = MagicMock()
    provision_replacement = MagicMock(return_value={"instance_id": "i-new"})
    auto_snapshot = MagicMock(return_value=["snap-1"])

    snap_ids, result = run_reprovision_flow(
        info={"exists": True, "instance_id": "i-old"},
        skip_snapshot=False,
        auto_snapshot=auto_snapshot,
        destroy_instance=destroy_instance,
        cleanup_orphaned_volumes=cleanup_orphaned,
        provision_replacement=provision_replacement,
        echo=lambda _msg: None,
        echo_err=lambda _msg: None,
        confirm_continue=lambda _msg: True,
    )

    assert snap_ids == ["snap-1"]
    assert result["instance_id"] == "i-new"
    destroy_instance.assert_called_once()
    cleanup_orphaned.assert_called_once()
    provision_replacement.assert_called_once()


def test_run_reprovision_flow_skips_destroy_when_no_instance() -> None:
    destroy_instance = MagicMock()
    cleanup_orphaned = MagicMock()
    provision_replacement = MagicMock(return_value={"instance_id": "i-new"})

    snap_ids, result = run_reprovision_flow(
        info={"exists": False},
        skip_snapshot=True,
        auto_snapshot=lambda: ["snap-unused"],
        destroy_instance=destroy_instance,
        cleanup_orphaned_volumes=cleanup_orphaned,
        provision_replacement=provision_replacement,
        echo=lambda _msg: None,
        echo_err=lambda _msg: None,
        confirm_continue=lambda _msg: True,
    )

    assert snap_ids == []
    assert result["instance_id"] == "i-new"
    destroy_instance.assert_not_called()
    cleanup_orphaned.assert_not_called()
    provision_replacement.assert_called_once()
