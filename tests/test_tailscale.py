"""Tests for edcloud.tailscale — mocked subprocess calls."""

import json
from unittest.mock import MagicMock, patch

from edcloud.tailscale import (
    edcloud_name_conflicts,
    format_conflict_message,
    get_tailscale_ip,
)

MOCK_TS_STATUS = {
    "Peer": {
        "abc123": {
            "HostName": "edcloud",
            "DNSName": "edcloud.tail12345.ts.net.",
            "TailscaleIPs": ["100.64.1.42", "fd7a:115c:a1e0::1"],
            "Active": True,
        },
        "def456": {
            "HostName": "other-host",
            "DNSName": "other-host.tail12345.ts.net.",
            "TailscaleIPs": ["100.64.1.99"],
            "Active": False,
        },
    }
}


class TestGetTailscaleIP:
    @patch("edcloud.tailscale.tailscale_available", return_value=False)
    def test_returns_none_if_no_tailscale(self, _mock):
        assert get_tailscale_ip("edcloud") is None

    @patch("edcloud.tailscale.tailscale_available", return_value=True)
    @patch("subprocess.run")
    def test_finds_peer_by_hostname(self, mock_run, _mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_TS_STATUS))
        assert get_tailscale_ip("edcloud") == "100.64.1.42"

    @patch("edcloud.tailscale.tailscale_available", return_value=True)
    @patch("subprocess.run")
    def test_returns_none_for_unknown_host(self, mock_run, _mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(MOCK_TS_STATUS))
        assert get_tailscale_ip("nonexistent") is None


def test_edcloud_name_conflicts_detects_suffixed_dns() -> None:
    with patch("edcloud.tailscale.list_all_edcloud_devices") as mock_devices:
        mock_devices.return_value = [
            {
                "hostname": "edcloud",
                "ip": "100.64.1.42",
                "dns_name": "edcloud-2.tail123.ts.net.",
                "online": False,
            },
            {
                "hostname": "edcloud",
                "ip": "100.64.1.99",
                "dns_name": "edcloud.tail123.ts.net.",
                "online": True,
            },
        ]

        conflicts = edcloud_name_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["dns_name"] == "edcloud-2.tail123.ts.net."


def test_edcloud_name_conflicts_detects_suffixed_single_device() -> None:
    with patch("edcloud.tailscale.list_all_edcloud_devices") as mock_devices:
        mock_devices.return_value = [
            {
                "hostname": "edcloud",
                "ip": "100.64.1.42",
                "dns_name": "edcloud-4.tail123.ts.net.",
                "online": True,
            }
        ]

        conflicts = edcloud_name_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["dns_name"] == "edcloud-4.tail123.ts.net."


def test_edcloud_name_conflicts_allows_single_unsuffixed_device() -> None:
    with patch("edcloud.tailscale.list_all_edcloud_devices") as mock_devices:
        mock_devices.return_value = [
            {
                "hostname": "edcloud",
                "ip": "100.64.1.42",
                "dns_name": "edcloud.tail123.ts.net.",
                "online": True,
            }
        ]

        conflicts = edcloud_name_conflicts()
        assert conflicts == []


def test_format_conflict_message_contains_remediation() -> None:
    message = format_conflict_message(
        [
            {
                "hostname": "edcloud",
                "ip": "100.64.1.42",
                "dns_name": "edcloud-2.tail123.ts.net.",
                "online": False,
            }
        ]
    )
    assert "Tailscale naming conflict detected" in message
    assert "admin/machines" in message
