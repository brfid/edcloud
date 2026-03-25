"""Tailscale integration — hostname resolution, connectivity, and cleanup.

All interaction with Tailscale goes through the local ``tailscale`` CLI;
no API key is required on the operator node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
from typing import Any


def _tailscale_status() -> dict[str, Any] | None:
    """Run ``tailscale status --json`` and return the parsed payload.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    if not tailscale_available():
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        parsed = json.loads(result.stdout)
        if isinstance(parsed, dict):
            return parsed
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    return None


def tailscale_available() -> bool:
    """Return ``True`` if the ``tailscale`` CLI is on ``$PATH``."""
    return shutil.which("tailscale") is not None


def find_active_edcloud_device() -> tuple[str, str] | None:
    """Find the active edcloud device in Tailscale.

    Handles hostname suffixes (``edcloud``, ``edcloud-1``, …) and prefers
    online devices.  Does *not* fall back to offline records.

    Returns:
        ``(hostname, ip)`` tuple, or ``None`` if no online device is found.
    """
    edcloud_devices = list_all_edcloud_devices()

    if not edcloud_devices:
        return None

    # Prefer online devices
    online_devices = [d for d in edcloud_devices if d["online"]]
    if online_devices:
        # Return the first online device
        device = online_devices[0]
        return (str(device["hostname"]), str(device["ip"]))

    # Do not fall back to offline devices; stale records can point status/
    # verify at dead nodes and mask the active replacement instance.
    return None


def get_tailscale_ip(hostname: str) -> str | None:
    """Resolve a Tailscale MagicDNS hostname to its IP.

    When *hostname* is ``"edcloud"``, auto-detects the active device
    even if Tailscale appended a numeric suffix.

    Args:
        hostname: Tailscale hostname or MagicDNS prefix.

    Returns:
        Tailscale IP (``100.x.y.z``) or ``None`` if not found.
    """
    status = _tailscale_status()
    if not status:
        return None

    # Special handling for edcloud to auto-detect active device
    if hostname == "edcloud":
        result = find_active_edcloud_device()
        if result:
            return result[1]  # Return the IP
        return None
    peers = status.get("Peer", {})
    for peer_info in peers.values():
        peer_hostname = str(peer_info.get("HostName", ""))
        dns_name = str(peer_info.get("DNSName", ""))
        if peer_hostname == hostname or dns_name.startswith(f"{hostname}."):
            addrs = peer_info.get("TailscaleIPs", [])
            if addrs:
                return str(addrs[0])
    return None


def is_reachable(hostname: str, timeout: int = 5) -> bool:
    """Ping the Tailscale peer to check reachability.

    Args:
        hostname: Tailscale hostname to resolve and ping.
        timeout: Ping timeout in seconds.
    """
    ip = get_tailscale_ip(hostname)
    if not ip:
        return False
    try:
        result = subprocess.run(  # nosec B603 B607
            ["ping", "-c", "1", "-W", str(timeout), ip],
            capture_output=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_active_edcloud_hostname() -> str:
    """Return the active edcloud hostname, falling back to ``"edcloud"``."""
    result = find_active_edcloud_device()
    if result:
        return result[0]  # Return the hostname
    return "edcloud"  # Fall back to base name


def list_all_edcloud_devices() -> list[dict[str, str | bool]]:
    """List all edcloud devices (online and offline) visible on the tailnet.

    Returns:
        List of dicts with keys ``hostname``, ``ip``, ``dns_name``, ``online``.
    """
    status = _tailscale_status()
    if not status:
        return []
    peers = status.get("Peer", {})
    devices: list[dict[str, str | bool]] = []

    def append_if_edcloud(device_info: dict[str, Any]) -> None:
        hostname = str(device_info.get("HostName", ""))
        dns_name = str(device_info.get("DNSName", ""))
        dns_label = dns_name.rstrip(".").split(".", 1)[0] if dns_name else ""
        if not (hostname.startswith("edcloud") or dns_label.startswith("edcloud")):
            return
        addrs = device_info.get("TailscaleIPs", [])
        if not addrs:
            return
        devices.append(
            {
                "hostname": hostname,
                "ip": str(addrs[0]),
                "dns_name": dns_name,
                # Prefer explicit Online when present; otherwise fall back to
                # Active for compatibility with older/mock tailscale status.
                "online": bool(device_info.get("Online", device_info.get("Active", False))),
            }
        )

    self_device = status.get("Self")
    if isinstance(self_device, dict):
        append_if_edcloud(self_device)

    for peer_info in peers.values():
        if isinstance(peer_info, dict):
            append_if_edcloud(peer_info)

    deduped: list[dict[str, str | bool]] = []
    seen: set[tuple[str, str]] = set()
    for device in devices:
        key = (str(device.get("hostname", "")), str(device.get("ip", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(device)
    return deduped


def edcloud_name_conflicts(base_hostname: str = "edcloud") -> list[dict[str, str | bool]]:
    """Detect Tailscale naming drift for edcloud devices.

    A conflict exists when a device's DNS label has a numeric suffix
    (e.g. ``edcloud-4``) or when multiple edcloud records coexist.

    Returns:
        Conflicting device dicts, or empty list if clean.
    """
    devices = list_all_edcloud_devices()
    if not devices:
        return []

    suffix_re = re.compile(rf"^{re.escape(base_hostname)}-\d+$")
    conflicts: list[dict[str, str | bool]] = []
    for device in devices:
        dns_name = str(device.get("dns_name", "")).rstrip(".")
        dns_label = dns_name.split(".", 1)[0] if dns_name else ""
        if suffix_re.match(dns_label):
            conflicts.append(device)

    if conflicts:
        return conflicts
    if len(devices) > 1:
        return devices
    return []


def format_conflict_message(conflicts: list[dict[str, str | bool]]) -> str:
    """Render a user-facing remediation message for Tailscale naming conflicts."""
    lines = [
        "Tailscale naming conflict detected for edcloud devices.",
        "Conflicting/duplicate entries:",
    ]
    for device in conflicts:
        lines.append(
            "  - "
            f"dns={device.get('dns_name', '')} "
            f"hostname={device.get('hostname', '')} "
            f"online={device.get('online', False)} "
            f"ip={device.get('ip', '')}"
        )
    lines.extend(
        [
            "Remediation:",
            "  1. Open https://login.tailscale.com/admin/machines",
            "  2. Search for 'edcloud'",
            "  3. Delete offline stale edcloud entries",
            "  4. Ensure active device resolves as edcloud.tail... (no -N suffix)",
            "Then rerun the command.",
        ]
    )
    return "\n".join(lines)


def cleanup_offline_edcloud_devices() -> tuple[int, str]:
    """Detect offline edcloud devices and produce cleanup guidance.

    The Tailscale CLI cannot remove devices without an API key, so this
    returns instructions for manual cleanup via the admin console.

    Returns:
        ``(count, message)`` — count of offline devices and a human-readable
        remediation message.
    """
    devices = list_all_edcloud_devices()
    offline = [d for d in devices if not d["online"]]

    if not offline:
        return (0, "No offline edcloud devices found")

    # We can't remove devices via CLI without API access
    # Return list for manual cleanup
    device_list = "\n".join(f"  - {d['hostname']} ({d['ip']})" for d in offline)
    message = (
        f"Found {len(offline)} offline edcloud device(s):\n{device_list}\n\n"
        "To remove them:\n"
        "1. Go to: https://login.tailscale.com/admin/machines\n"
        "2. Search for 'edcloud'\n"
        "3. Delete the offline devices listed above\n\n"
        "This will ensure your next provision uses 'edcloud' (no suffix)."
    )
    return (len(offline), message)
