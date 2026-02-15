# edcloud

Personal cloud lab on AWS: one EC2 instance for x86_64 Linux workloads, managed through Portainer, accessed via Tailscale (no exposed ports).

## Cold Start (Operator)

Read in this order:

1. `README.md`
2. `SECURITY.md`
3. `DESIGN.md`
4. `SETUP.md`
5. `AGENTS.md` (if using coding agents)

Control plane expectation: this is intended to be operable from a small ARM Linux system
(for example, Raspberry Pi Zero 2 W) as long as it has Python 3, AWS CLI credentials, and Tailscale.

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
# Store in SSM Parameter Store (recommended)
aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --overwrite \
  --value 'tskey-auth-...'

# Provision (~3 min: creates t3a.medium + 80GB root + 10GB state + Tailscale + Docker + Portainer)
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

## Usage

```bash
edc up             # Start instance
edc status         # Check state, Tailscale IP, cost estimate
edc ssh            # SSH via Tailscale
edc verify         # Fresh-reprovision verification checks
edc down           # Stop (or auto-shuts down after 30min idle)
edc snapshot       # Create EBS snapshot
edc snapshot --list
edc snapshot --prune --dry-run
edc destroy        # Terminate instance (EBS preserved)
```

Access Portainer: `https://edcloud:9443` (from any device on your tailnet)

## Cost

~$12/mo at 4hrs/day: $4.51 compute + $7.20 storage + snapshot usage (policy soft cap: $5/mo). Auto-shutdown when idle (no SSH + low CPU for 30min).

## Architecture

- **1x t3a.medium** (4GB RAM) — Docker host
- **80GB gp3 root + 10GB gp3 state EBS** — host + durable assistant state
- **Portainer CE** — container management UI
- **Tailscale** — secure access, no public inbound ports
- **Ubuntu 24.04 LTS** — 5yr security updates

Resources tracked by tag `edcloud:managed=true` (no local state file).

## Example workload

Deploy the vintage computing lab (VAX + PDP-11 SIMH):

```bash
scp compose/vintage-lab.yml ubuntu@edcloud:/opt/edcloud/compose/
edc ssh 'docker compose -f /opt/edcloud/compose/vintage-lab.yml up -d'
telnet edcloud 2323  # VAX console
```

Or use Portainer to deploy any Docker Compose stack.

## Reproducible Baseline + Backup (Current Priority)

Canonical task list: [`SETUP.md` → `Active Priorities`](SETUP.md#active-priorities).

## Documentation

- **[SECURITY.md](SECURITY.md)** — Security policy, threat model, and vulnerability reporting
- **[DESIGN.md](DESIGN.md)** — Architecture decisions and trade-offs
- **[SETUP.md](SETUP.md)** — Detailed first-time setup guide
- **[AGENTS.md](AGENTS.md)** — Agent workflow constraints for this repo

## Security

This project follows secure development practices:
- No credentials in code or git history
- Automated secret scanning (pre-commit hooks)
- Tailscale-only access (zero public inbound ports)
- Comprehensive security documentation

See [SECURITY.md](SECURITY.md) for the full security policy and threat model.
