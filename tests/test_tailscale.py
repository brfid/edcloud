"""Tests for edcloud.tailscale — mocked subprocess calls."""

import json
from unittest.mock import MagicMock, patch

from edcloud.tailscale import get_tailscale_ip, ssh_command

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


class TestSSHCommand:
    @patch("edcloud.tailscale.get_tailscale_ip", return_value="100.64.1.42")
    def test_uses_tailscale_ip(self, _mock):
        cmd = ssh_command("edcloud")
        assert cmd == ["ssh", "ubuntu@100.64.1.42"]

    @patch("edcloud.tailscale.get_tailscale_ip", return_value=None)
    def test_falls_back_to_hostname(self, _mock):
        cmd = ssh_command("edcloud")
        assert cmd == ["ssh", "ubuntu@edcloud"]
