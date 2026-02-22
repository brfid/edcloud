"""Declarative verification check catalog for ``edc verify``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifyCheck:
    """Single remote verification check."""

    name: str
    remote_cmd: str


VERIFY_CHECKS: tuple[VerifyCheck, ...] = (
    VerifyCheck("cloud-init status done", "cloud-init status --wait >/dev/null"),
    VerifyCheck("docker service active", "systemctl is-active --quiet docker"),
    VerifyCheck(
        "docker data-root points to state volume",
        "docker info --format '{{.DockerRootDir}}' | grep -qx /opt/edcloud/state/docker",
    ),
    VerifyCheck("portainer container running", "docker ps --format '{{.Names}}' | grep -qx portainer"),
    VerifyCheck("compose directory exists", "test -d /opt/edcloud/compose"),
    VerifyCheck("compose directory is mounted", "mountpoint -q /opt/edcloud/compose"),
    VerifyCheck(
        "compose bind mount configured in fstab",
        "grep -qE "
        "'^/opt/edcloud/state/compose[[:space:]]+/opt/edcloud/compose[[:space:]]+"
        "none[[:space:]]+bind' /etc/fstab",
    ),
    VerifyCheck("portainer data directory exists", "test -d /opt/edcloud/portainer-data"),
    VerifyCheck("portainer data directory is mounted", "mountpoint -q /opt/edcloud/portainer-data"),
    VerifyCheck(
        "portainer data bind mount configured in fstab",
        "grep -qE "
        "'^/opt/edcloud/state/portainer-data[[:space:]]+/opt/edcloud/portainer-data"
        "[[:space:]]+none[[:space:]]+bind' /etc/fstab",
    ),
    VerifyCheck("state directory exists", "test -d /opt/edcloud/state"),
    VerifyCheck("state directory is mounted", "mountpoint -q /opt/edcloud/state"),
    VerifyCheck("state directory writable", "test -w /opt/edcloud/state"),
    VerifyCheck("home directory exists", "test -d /home/ubuntu"),
    VerifyCheck("home directory is mounted", "mountpoint -q /home/ubuntu"),
    VerifyCheck(
        "home bind mount configured in fstab",
        "grep -qE "
        "'^/opt/edcloud/state/home/ubuntu[[:space:]]+/home/ubuntu[[:space:]]+"
        "none[[:space:]]+bind' /etc/fstab",
    ),
    VerifyCheck("home directory writable", "test -w /home/ubuntu"),
    VerifyCheck("tailscale state directory exists", "test -d /var/lib/tailscale"),
    VerifyCheck("tailscale state directory is mounted", "mountpoint -q /var/lib/tailscale"),
    VerifyCheck(
        "tailscale bind mount configured in fstab",
        "grep -qE "
        "'^/opt/edcloud/state/tailscale[[:space:]]+/var/lib/tailscale[[:space:]]+"
        "none[[:space:]]+bind' /etc/fstab",
    ),
    VerifyCheck("neovim installed", "command -v nvim >/dev/null"),
    VerifyCheck("byobu installed", "command -v byobu >/dev/null"),
    VerifyCheck("gh installed", "command -v gh >/dev/null"),
    VerifyCheck("lazyvim starter present", "test -f /home/ubuntu/.config/nvim/init.lua"),
)
