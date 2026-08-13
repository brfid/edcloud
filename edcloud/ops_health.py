"""Operational health helpers (cost checks, reporting signals)."""

from __future__ import annotations

from collections.abc import Sequence

from edcloud.config import SNAPSHOT_MONTHLY_RATE_PER_GB
from edcloud.types import SnapshotCostReport, SnapshotInfo


def estimate_snapshot_monthly_cost(
    snapshots: Sequence[SnapshotInfo],
    *,
    gb_month_rate: float = SNAPSHOT_MONTHLY_RATE_PER_GB,
    soft_cap_usd: float = 2.0,
) -> SnapshotCostReport:
    """Estimate monthly EBS snapshot spend from snapshot list output.

    The estimate is intentionally simple and conservative for operator guardrails:
    ``sum(completed snapshot size_gb) * gb_month_rate``.
    """
    completed = [s for s in snapshots if str(s.get("state", "")).lower() == "completed"]
    total_gb = float(sum(int(s.get("size_gb", 0) or 0) for s in completed))
    estimated_monthly_usd = round(total_gb * gb_month_rate, 2)
    return {
        "completed_snapshot_count": len(completed),
        "completed_snapshot_gb": total_gb,
        "gb_month_rate": gb_month_rate,
        "estimated_monthly_usd": estimated_monthly_usd,
        "soft_cap_usd": soft_cap_usd,
        "over_soft_cap": estimated_monthly_usd > soft_cap_usd,
    }
