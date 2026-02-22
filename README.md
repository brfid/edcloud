# edcloud

Personal cloud lab on AWS: one EC2 instance for x86_64 Linux workloads, managed through Portainer, accessed via Tailscale (no exposed ports).

## Setup

```bash
# Prerequisites: AWS credentials configured, Tailscale account

# Install
git clone <repo>
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Get Tailscale auth key from https://login.tailscale.com/admin/settings/keys
export TAILSCALE_AUTH_KEY='tskey-auth-...'

# Provision (~3 min: creates t3a.medium + 80GB gp3 + Tailscale + Docker + Portainer)
edcloud provision
```

## Usage

```bash
edcloud up             # Start instance
edcloud status         # Check state, Tailscale IP, cost estimate
edcloud ssh            # SSH via Tailscale
edcloud down           # Stop (or auto-shuts down after 30min idle)
edcloud snapshot       # Create EBS snapshot
edcloud snapshot --list
edcloud destroy        # Terminate instance (EBS preserved)
```

Access Portainer: `https://edcloud:9443` (from any device on your tailnet)

## Cost

~$11/mo at 4hrs/day: $4.51 compute + $6.40 storage + $0.40 snapshots. Auto-shutdown when idle (no SSH + low CPU for 30min).

## Architecture

- **1x t3a.medium** (4GB RAM) — Docker host
- **80GB gp3 EBS** — persistent storage (survives stop/start)
- **Portainer CE** — container management UI
- **Tailscale** — secure access, no public inbound ports
- **Ubuntu 24.04 LTS** — 5yr security updates

Resources tracked by tag `edcloud:managed=true` (no local state file).

## Example workload

Deploy the vintage computing lab (VAX + PDP-11 SIMH):

```bash
edcloud ssh
docker compose -f /opt/edcloud/compose/vintage-lab.yml up -d
telnet edcloud 2323  # VAX console
```

Or use Portainer to deploy any Docker Compose stack.
