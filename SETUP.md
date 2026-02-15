# edcloud setup guide

Operator runbook for provisioning, operating, and recovering a single-instance edcloud deployment.

## Active priorities

Open items only:

- [x] Harden destructive lifecycle actions: require explicit instance-id confirmation for `edc destroy`, and add an option to require a fresh pre-change snapshot before deletion/rebuild.
- [ ] Add a safe rebuild workflow (`snapshot -> reprovision -> verify`) as a single documented operator path.
- [x] Persist user home on the state volume (`/opt/edcloud/state/home/ubuntu`) via cloud-init-managed bind mount and migration checks.
- [x] Define default dev environment in cloud-init: `neovim` + LazyVim bootstrap, `byobu`, `gh`, and required runtime dependencies.
- [x] Decide and codify package strategy for non-APT tooling (APT-first vs Linuxbrew) for repeatable, non-interactive provisioning.
- [ ] Evaluate a secure operator login workflow that starts from one memorized string without weakening Tailscale/AWS MFA controls.
- [ ] Centralize default SSH username in repo config (for example `edcloud/config.py`) and have `edc ssh`/`edc verify` read that value.
- [ ] Run weekly + monthly snapshot cadence for durable state.
- [ ] Keep snapshot spend under soft cap `$5/month`; adjust retention if exceeded.
- [ ] Run restore drills from recent snapshots and verify SSH, Docker, Tailscale, Portainer, and data under `/opt/edcloud/state`.
- [ ] Record restore drill date and result for auditability.
- [ ] Back up non-repo durable state under `/opt/edcloud/state`; reclone repos from upstream on rebuild.

## Prerequisites

- AWS account with CLI credentials configured
- Tailscale account
- Python 3.10+
- Git
- Linux/macOS/WSL operator environment

A small ARM Linux operator node is supported if it can run Python, AWS CLI, and Tailscale.

## 1. AWS setup

Required IAM actions:

```text
ec2:RunInstances
ec2:DescribeInstances
ec2:StartInstances
ec2:StopInstances
ec2:TerminateInstances
ec2:CreateSecurityGroup
ec2:DescribeSecurityGroups
ec2:DeleteSecurityGroup
ec2:CreateTags
ec2:DescribeVolumes
ec2:CreateSnapshot
ec2:DescribeSnapshots
ec2:DescribeImages
ssm:GetParameter
ssm:PutParameter
```

Configure and verify credentials:

```bash
aws configure
aws sts get-caller-identity
```

## 2. Tailscale auth key

Create a key in Tailscale admin:

- URL: `https://login.tailscale.com/admin/settings/keys`
- Recommended: reusable key
- Optional: ephemeral key and `tag:edcloud`

Store key in SSM Parameter Store:

```bash
aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --overwrite \
  --value 'tskey-auth-...'
```

Use SSM-based provisioning (recommended):

```bash
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

Load key into current shell when needed:

```bash
eval "$(edc load-tailscale-env-key)"
```

## 3. Install edcloud CLI

```bash
git clone <your-repo>
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

edc --version
edc --help
```

`edc` is the primary command surface. `edcloud` remains a compatibility alias.

## 4. Optional: operator wrapper for ARM/Linux nodes

This removes the need to manually activate the venv for every command.

```bash
mkdir -p ~/.local/bin ~/.config/edcloud
install -m 0755 templates/operator/edc-wrapper.sh ~/.local/bin/edc
cp templates/operator/edc.env.example ~/.config/edcloud/edc.env
```

If repo path differs from `~/edcloud`, set:

```bash
EDCLOUD_REPO=/path/to/edcloud
```

To make `edc provision` work without repeated key flags, keep this in `~/.config/edcloud/edc.env`:

```bash
TAILSCALE_AUTH_KEY_SSM_PARAMETER=/edcloud/tailscale_auth_key
```

Optional automation templates:

- `templates/operator/systemd-user/edc-weekly-snapshot.service`
- `templates/operator/systemd-user/edc-weekly-snapshot.timer`
- `templates/operator/systemd-user/edc-monthly-snapshot.service`
- `templates/operator/systemd-user/edc-monthly-snapshot.timer`
- `templates/operator/run-reprovision-verify.sh`
- `templates/operator/record-restore-drill.sh`

## 5. Provision

```bash
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

If `TAILSCALE_AUTH_KEY_SSM_PARAMETER` is set in your operator env file:

```bash
edc provision
```

Expected resources:

- 1x EC2 instance (`t3a.medium` default)
- Security group with zero inbound rules
- 40 GB gp3 root volume
- 10 GB gp3 state volume mounted at `/opt/edcloud/state`

## 6. Verify bootstrap

Check status until reachable:

```bash
edc status
```

Run canonical verification:

```bash
edc verify
edc verify --public-ip
edc verify --json-output
```

Manual check:

```bash
edc ssh
docker ps
cat /tmp/edcloud-ready
```

## 7. Access Portainer

From any tailnet device:

```text
https://edcloud:9443
```

First login:

1. Set admin password.
2. Select local Docker environment.

## 8. Deploy workload example

```bash
scp compose/vintage-lab.yml ubuntu@edcloud:/opt/edcloud/compose/
edc ssh 'docker compose -f /opt/edcloud/compose/vintage-lab.yml up -d'
telnet edcloud 2323
```

## 9. Daily operations

```bash
edc up
edc status
edc ssh
edc down
```

The instance also auto-shuts down after 30 minutes of idle activity.

Destroy safety guardrails:

```bash
edc destroy --confirm-instance-id <instance-id>
edc destroy --confirm-instance-id <instance-id> --require-fresh-snapshot
```

## Default host toolset baseline

Core host tools are part of `cloud-init/user-data.yaml` and applied at provision time.

Persistent home baseline:

- `~/` for `ubuntu` is bind-mounted to `/opt/edcloud/state/home/ubuntu`.
- First boot migrates existing `/home/ubuntu` contents into the state volume.
- This keeps shell/editor/tool settings across reprovision when reusing the state volume.

Baseline packages:

- `bash-completion`
- `byobu`
- `dnsutils`
- `fd-find`
- `fzf`
- `gh`
- `git`
- `htop`
- `jq`
- `neovim`
- `ripgrep`
- `rsync`
- `tmux`
- `tree`
- `unattended-upgrades`
- `unzip`
- `vim-tiny`
- `xclip`
- `zip`

Package strategy:

- Prefer Ubuntu APT packages for baseline reproducibility and low friction.
- Install Homebrew by default for optional package gaps and operator preference.
- Keep core runbook/tooling functional without requiring Homebrew formulas.

Quick verification:

```bash
edc ssh 'git --version && tmux -V && rg --version && fdfind --version && htop --version | head -n 1'
edc ssh 'nvim --version | head -n 1 && byobu -V && gh --version | head -n 1 && brew --version | head -n 1'
edc ssh 'findmnt /home/ubuntu && df -h /home/ubuntu /opt/edcloud/state'
```

## 10. Backup and recovery standard

Operating policy:

- Treat host runtime as transient and rebuildable.
- Persist durable state under `/opt/edcloud/state`.
- Reclone git repositories from upstream on rebuild.
- Store secrets in SSM, not in git.

Snapshot operations:

```bash
edc snapshot
edc snapshot --list
edc snapshot -d pre-change-<reason>
```

Retention and pruning:

```bash
edc snapshot --prune --keep-weekly 8 --keep-monthly 3 --dry-run
edc snapshot --prune --keep-weekly 8 --keep-monthly 3 --apply
```

Policy targets:

- Weekly + monthly periodic snapshots
- Keep 8 weekly and 3 monthly snapshots
- Keep pre-change snapshots only while rollback value exists
- Keep snapshot spend under `$5/month`

Restore drill baseline (monthly):

1. Restore from a recent snapshot.
2. Verify Tailscale connectivity.
3. Verify Docker and Portainer.
4. Verify durable data under `/opt/edcloud/state`.
5. Run `edc verify`.

Optional drill record helper:

```bash
install -m 0755 templates/operator/record-restore-drill.sh ~/.local/bin/edc-record-restore-drill
~/.local/bin/edc-record-restore-drill pass snap-xxxxxxxx "monthly drill"
cat ~/.config/edcloud/restore-drill.tsv
```

## 11. Cost guardrail

Typical target at 4 hours/day:

- Compute: about `$4.51/month`
- Storage: about `$4.00/month`
- Snapshots: variable, target soft cap `$5/month`

Use `edc status` and AWS Cost Explorer to track drift.

## Troubleshooting

- Validate AWS identity: `aws sts get-caller-identity`
- Validate local tailnet state: `tailscale status`
- Validate instance and reachability: `edc status`
