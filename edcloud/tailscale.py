"""Tailscale integration — hostname resolution and connectivity checks."""

from __future__ import annotations

import json
import shutil
import subprocess


def tailscale_available() -> bool:
    """Check if the tailscale CLI is on PATH."""
    return shutil.which("tailscale") is not None


def get_tailscale_ip(hostname: str) -> str | None:
    """Resolve a Tailscale MagicDNS hostname to its IP.

    Returns the Tailscale IP (100.x.y.z) or None if not found.
    """
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
        status = json.loads(result.stdout)
        peers = status.get("Peer", {})
        for peer_info in peers.values():
            peer_hostname = peer_info.get("HostName", "")
            dns_name = peer_info.get("DNSName", "")
            if peer_hostname == hostname or dns_name.startswith(f"{hostname}."):
                addrs = peer_info.get("TailscaleIPs", [])
                if addrs:
                    return addrs[0]  # type: ignore[no-any-return]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
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


def ssh_command(hostname: str, user: str = "ubuntu") -> list[str]:
    """Build an SSH command targeting the Tailscale hostname."""
    ip = get_tailscale_ip(hostname)
    target = ip if ip else hostname
    return ["ssh", f"{user}@{target}"]
