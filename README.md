# edcloud

Single-instance AWS EC2 personal cloud lab for x86_64 Linux workloads.

- Access: Tailscale only (no public inbound rules)
- Container management: Portainer CE
- Operator interface: `edc` (`edcloud` remains a compatibility alias)

## Read first

1. `README.md`
2. `SECURITY.md`
3. `DESIGN.md`
4. `SETUP.md`
5. `AGENTS.md` (only if you use coding agents)

## Quick start

Prerequisites:

- AWS account and configured CLI credentials
- Tailscale account
- Python 3.10+
- Git

Install and provision:

```bash
git clone <repo>
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --overwrite \
  --value 'tskey-auth-...'

edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

Optional shell helper for reprovision workflows:

```bash
eval "$(edc load-tailscale-env-key)"
```

## Daily operations

```bash
edc up
edc status
edc ssh
edc verify
edc down
edc snapshot
edc snapshot --list
edc snapshot --prune --keep-weekly 8 --keep-monthly 3 --dry-run
edc destroy --confirm-instance-id <instance-id>
```

Portainer URL (from a device on your tailnet): `https://edcloud:9443`

## Architecture summary

- One `t3a.medium` instance (Ubuntu 24.04 LTS)
- Two gp3 EBS volumes:
  - 40 GB root
  - 10 GB durable state at `/opt/edcloud/state`
- `ubuntu` home persists on state volume via bind mount to `/opt/edcloud/state/home/ubuntu`
- Resource discovery by tag: `edcloud:managed=true`
- Runtime secrets from AWS SSM Parameter Store
- Reprovision baseline includes core operator/dev tools (`neovim` + LazyVim starter, `byobu`, `gh`, `git`, `tmux`, `ripgrep`, `htop`) and Homebrew

## Cost model

Typical target at 4 hours/day:

- Compute: about `$4.51/month`
- Storage: about `$4.00/month`
- Snapshots: variable, soft cap `$5/month`

Auto-shutdown stops idle instances after 30 minutes.

## Documentation

- `SECURITY.md`: threat model, assumptions, vulnerability reporting
- `DESIGN.md`: design decisions and trade-offs
- `SETUP.md`: full operator runbook and backup/recovery procedure
- `AGENTS.md`: repo workflow constraints for coding agents
