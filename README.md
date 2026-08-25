# edcloud

edcloud is a single-instance AWS EC2 personal cloud lab for x86_64 Linux workloads.

This repository is preserved for historical reference and is not actively maintained.

## What it does

- Provisions, verifies, resizes, snapshots, reprovisions, and destroys one Ubuntu EC2 instance through the `edc` CLI.
- Creates the EC2 security group without inbound rules; operator access uses Tailscale.
- Discovers managed AWS resources by the `edcloud:managed=true` tag instead of a local state file.
- Keeps `/home/ubuntu`, Tailscale identity, Docker data, compose files, and Portainer data on a persistent state volume.
- Stores bootstrap secrets in AWS Systems Manager Parameter Store.

It is not a production or multi-tenant platform.

## Quick start

Running `edc provision` creates billable AWS resources. You need AWS CLI credentials and a region configured for an account with a default VPC and subnet, a local Tailscale client signed into the target tailnet, Git, and Python 3.10 or later.

```bash
git clone https://github.com/brfid/edcloud.git
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Store a Tailscale auth key in Parameter Store:

```bash
aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --value '<TAILSCALE_AUTH_KEY>'
```

For the first deployment, allow edcloud to create a state volume:

```bash
edc provision --allow-new-state-volume
edc status  # Repeat until Reachable: yes.
edc ssh 'cloud-init status --wait'
edc verify
```

Later provisions require an existing managed state volume by default. Use `--allow-new-state-volume` again only when you intend to create a replacement state volume.

## Common workflows

Start, connect to, and stop the instance:

```bash
edc up
edc status
edc ssh
edc down
```

Use the safe rebuild sequence for disruptive host changes:

```bash
INSTANCE_ID=i-0123456789abcdef0
edc tailscale reconcile
edc reprovision --confirm-instance-id "$INSTANCE_ID"
edc status  # Repeat until Reachable: yes.
edc ssh 'cloud-init status --wait'
edc verify
```

Manage and test backups:

```bash
edc snapshot --list
edc snapshot-cost --soft-cap-usd 2.0
edc restore-drill --attach-managed-instance
```

The CLI-managed queue is the primary snapshot retention mechanism. AWS Data Lifecycle Manager (DLM) support is available for experimentation, but its snapshots share the CLI's managed tag and are eligible for CLI pruning. Do not rely on both retention models at the same time without changing the tagging or pruning behavior.

Run `edc --help` or `edc <COMMAND> --help` for the complete command reference. Use `--allow-tailscale-name-conflicts` only for recovery when you have reviewed the reported naming conflict.

## Architecture

- **Compute:** `t3a.small` instance running Ubuntu 24.04 by default.
- **Network:** Tailscale access only; no inbound security group rules.
- **Storage:** 30 GiB disposable root volume and 30 GiB persistent state volume at `/opt/edcloud/state` by default.
- **Discovery:** `edcloud:managed=true` tag on managed resources.
- **Secrets:** Parameter Store under `/edcloud/*`, read by the instance role at boot.
- **Bootstrap:** `cloud-init/user-data.yaml` defines Docker, Portainer, development tools, persistent mounts, and the idle-shutdown timer.

Cloud-init reads these parameters when present:

- `/edcloud/tailscale_auth_key`: joins the tailnet; required.
- `/edcloud/github_token`: authenticates the GitHub CLI; optional.
- `/edcloud/rclone_config`: mounts `~/Dropbox` through rclone; optional.

Cloud-init can clone dotfiles and selected non-secret repositories. It does not apply the dotfiles. After a rebuild, follow the dotfiles repository's instructions to link them.

Automatic CLI lifecycle triggers snapshot the state volume and prune the managed pool to retain three snapshots. Manual snapshots remain until an explicit or later automatic prune places them beyond the limit.

## Cost controls

The instance shuts down after 30 minutes without Tailscale connections or meaningful CPU load. `edc status` estimates compute and storage cost from the rates and assumption of eight runtime hours per day in `edcloud/config.py`; use AWS Cost Explorer for actual charges. `edc snapshot-cost` provides a capacity-based snapshot cost proxy.

## Validate the project

```bash
pytest -q
ruff check .
mypy edcloud/
```

## Documentation

- [Operator runbook](RUNBOOK.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Security model](SECURITY.md)
- [Project history](CHANGELOG.md)
- [Agent workflow](AGENTS.md)
- [Script replacements](scripts/README.md)

The project is licensed under the [MIT License](LICENSE).
