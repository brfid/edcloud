"""Tests for operational health helper calculations."""

from edcloud.ops_health import estimate_snapshot_monthly_cost


def test_estimate_snapshot_monthly_cost_counts_completed_only() -> None:
    report = estimate_snapshot_monthly_cost(
        [
            {"snapshot_id": "snap-1", "state": "completed", "size_gb": 20},
            {"snapshot_id": "snap-2", "state": "pending", "size_gb": 20},
            {"snapshot_id": "snap-3", "state": "completed", "size_gb": 10},
        ],
        gb_month_rate=0.05,
        soft_cap_usd=2.0,
    )

    assert report["completed_snapshot_count"] == 2
    assert report["completed_snapshot_gb"] == 30.0
    assert report["estimated_monthly_usd"] == 1.5
    assert report["over_soft_cap"] is False


def test_estimate_snapshot_monthly_cost_over_cap() -> None:
    report = estimate_snapshot_monthly_cost(
        [
            {"snapshot_id": "snap-1", "state": "completed", "size_gb": 30},
            {"snapshot_id": "snap-2", "state": "completed", "size_gb": 20},
        ],
        gb_month_rate=0.05,
        soft_cap_usd=2.0,
    )

    assert report["estimated_monthly_usd"] == 2.5
    assert report["over_soft_cap"] is True
