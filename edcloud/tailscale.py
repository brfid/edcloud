"""Tailscale integration — hostname resolution and connectivity checks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any


def _tailscale_status() -> dict[str, Any] | None:
    """Return `tailscale status --json` payload, or None on failure."""
    if not tailscale_available():
        return None
    try:
        result = subprocess.run(
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
    """Check if the tailscale CLI is on PATH."""
    return shutil.which("tailscale") is not None


def find_active_edcloud_device() -> tuple[str, str] | None:
    """Find the active edcloud device in Tailscale, regardless of suffix.

    Returns (hostname, ip) tuple or None if not found.
    Prefers online devices, handles edcloud, edcloud-1, edcloud-2, etc.
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

    Returns the Tailscale IP (100.x.y.z) or None if not found.

    Special case: if hostname is "edcloud", will find any active edcloud device
    (edcloud, edcloud-1, edcloud-2, etc.) and return its IP.
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
    """Ping the Tailscale hostname to check if it's reachable."""
    ip = get_tailscale_ip(hostname)
    if not ip:
        return False
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), ip],
            capture_output=True,
            timeout=timeout + 2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_active_edcloud_hostname() -> str:
    """Get the active edcloud hostname (handles edcloud-1, edcloud-2, etc.)."""
    result = find_active_edcloud_device()
    if result:
        return result[0]  # Return the hostname
    return "edcloud"  # Fall back to base name


def list_all_edcloud_devices() -> list[dict[str, str | bool]]:
    """List all edcloud devices (active and offline) in Tailscale.

    Returns list of dicts with keys: hostname, ip, online.
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
    """Return edcloud devices that indicate naming drift/conflict.

    A conflict exists when a device DNS label is incremented (for example
    ``edcloud-4.tail...``) or when multiple edcloud records are present.
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
    """Render a stable remediation message for Tailscale naming conflicts."""
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
    """Remove offline edcloud devices from Tailscale.

    Returns (count_removed, message).

    Note: This requires manual cleanup via Tailscale admin console.
    We detect offline devices but cannot remove them via CLI without API key.
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


def ssh_command(hostname: str, user: str = "ubuntu") -> list[str]:
    """Build an SSH command targeting the Tailscale hostname.

    If hostname is "edcloud", will auto-detect the active edcloud device.
    """
    ip = get_tailscale_ip(hostname)
    target = ip if ip else hostname
    return ["ssh", f"{user}@{target}"]
