"""Shared EC2 discovery helpers used across lifecycle and audit modules."""

from __future__ import annotations

from typing import Any

ACTIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped"]


def instance_state_filter(states: list[str] | None = None) -> dict[str, Any]:
    """Return an EC2 filter for non-terminated (or provided) instance states."""
    return {"Name": "instance-state-name", "Values": states or ACTIVE_INSTANCE_STATES}


def list_instances(ec2_client: Any, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe instances matching *filters* and active-state filter."""
    resp = ec2_client.describe_instances(Filters=[*filters, instance_state_filter()])
    instances: list[dict[str, Any]] = []
    for reservation in resp.get("Reservations", []):
        instances.extend(reservation.get("Instances", []))
    return instances
