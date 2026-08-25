# edcloud runbook

Historical operator procedures for provisioning, operating, and recovering a single-instance edcloud deployment.

This project is not actively maintained. The commands in this runbook can create or delete billable AWS resources; review each command and the current provider behavior before you run it.

## Prerequisites

- AWS CLI credentials and a region configured for an account with a default VPC and subnet
- Tailscale client signed into the target tailnet
- Python 3.10 or later
- Git
- Linux/macOS/WSL operator environment

A small ARM Linux operator node is supported if it can run Python, AWS CLI, and Tailscale.

## Install the edcloud CLI

```bash
git clone https://github.com/brfid/edcloud.git
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

edc --version
edc --help
```

Run `edc` from the repository virtual environment. You can activate it as shown or invoke `.venv/bin/edc` directly. `edcloud` remains a compatibility alias for the primary `edc` command.

Operator templates and helpers are in `templates/operator/`. The `edc sync-cline-auth` command transfers Cline subscription authentication from a browser-capable machine to a headless edcloud host.

## AWS setup

Configure and verify your AWS credentials:

```bash
aws configure
aws sts get-caller-identity
```

After you install the CLI, generate and verify the required IAM permissions:

```bash
edc permissions show
edc permissions policy > edcloud-operator-policy.json
edc permissions verify
```

`edc permissions verify` uses IAM simulation and may require `iam:SimulatePrincipalPolicy` on your operator principal. The generated policy is the source of truth; avoid copying a static action list into a separate policy.

### IAM: manual fallback reference

`edc provision` creates and attaches the IAM instance profile (`edcloud-instance-profile` / `edcloud-instance-role`) automatically. If automated setup fails, create it manually:

```bash
# Trust policy
aws iam create-role --role-name edcloud-instance-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

# SSM read policy (read /edcloud/* parameters)
aws iam put-role-policy --role-name edcloud-instance-role \
  --policy-name edcloud-ssm-read \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"ssm:GetParameter","Resource":"arn:aws:ssm:*:*:parameter/edcloud/*"}]}'

# Instance profile
aws iam create-instance-profile --instance-profile-name edcloud-instance-profile
aws iam add-role-to-instance-profile \
  --instance-profile-name edcloud-instance-profile \
  --role-name edcloud-instance-role
```

## Tailscale auth key

Create a key in the [Tailscale keys settings](https://login.tailscale.com/admin/settings/keys). The original setup used a reusable key; an ephemeral key with `tag:edcloud` is also supported.

Store the key in AWS Systems Manager Parameter Store:

```bash
aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --overwrite \
  --value '<TAILSCALE_AUTH_KEY>'
```

Secret behavior on new builds:

- SSM values are consumed by bootstrap/provision steps when needed.
- They are **not** automatically exported as persistent login-shell environment variables.
- Keep runtime secrets in SSM (or local non-git files such as `~/.secrets`) and load explicitly when required.

Load key into current shell when needed:

```bash
eval "$(edc load-tailscale-env-key)"
```

## Bootstrap SSM parameters

The instance IAM role grants `ssm:GetParameter` on all `/edcloud/*` parameters. The following are pulled automatically during every build — store them once and they apply to every reprovision:

- `/edcloud/tailscale_auth_key`: Joins the Tailscale network; required.
- `/edcloud/github_token`: Authenticates the GitHub CLI.
- `/edcloud/rclone_config`: Writes `~/.config/rclone/rclone.conf` and enables `rclone-dropbox.service` to mount `~/Dropbox` with FUSE.

Store each as `SecureString`:

```bash
# GitHub personal access token
aws ssm put-parameter \
  --name /edcloud/github_token \
  --type SecureString \
  --overwrite \
  --value '<GITHUB_TOKEN>'

# rclone config (run rclone config on a machine with browser access first)
aws ssm put-parameter \
  --name /edcloud/rclone_config \
  --type SecureString \
  --overwrite \
  --value "$(cat ~/.config/rclone/rclone.conf)"
```

The `tailscale_auth_key` parameter is required. The other parameters are optional; if one is absent at boot, bootstrap skips the corresponding step.

## Provision

```bash
edc provision --allow-new-state-volume
```

For later provisions, reuse the existing managed state volume:

```bash
edc provision
```

State-volume guardrails:

- Reusing an existing managed state volume is the default. The command stops if none exists.
- Use `--allow-new-state-volume` only for the first deployment or an intentional replacement.

Expected resources:

- One EC2 instance (`t3a.small` by default; use `--instance-type t3a.medium` for more memory)
- Security group with zero inbound rules
- 30 GiB gp3 root volume (expandable; use `--volume-size` to override)
- 30 GiB gp3 state volume mounted at `/opt/edcloud/state` (expandable; use `--state-volume-size` to override)

Tailscale identity guardrails:

- `edc provision` fails fast if duplicate or suffixed `edcloud` Tailscale records exist.
- Use `edc tailscale reconcile` to inspect conflicts before provisioning.
- Break-glass override: `--allow-tailscale-name-conflicts`.

## Verify bootstrap

Check status until reachable, then wait for cloud-init to finish:

```bash
edc status
edc ssh 'cloud-init status --wait'
```

Run verification:

```bash
edc verify
edc verify --json-output
```

Manual check:

```bash
edc ssh
edc ssh 'docker ps'
```

**Note:** `edc ssh` automatically detects the active edcloud device (handles edcloud, edcloud-2, edcloud-3, etc.). Use `edc tailscale reconcile` before lifecycle actions to surface naming conflicts.

Preflight recommended before rebuild/provision:

```bash
edc tailscale reconcile
```

## Access Portainer

From any tailnet device:

```text
https://edcloud:9443
```

First login:

1. Set admin password.
2. Select local Docker environment.

## Deploy the example workload

```bash
scp compose/vintage-lab.yml ubuntu@edcloud:~/vintage-lab.yml
edc ssh 'sudo install -m 0644 ~/vintage-lab.yml /opt/edcloud/compose/vintage-lab.yml'
edc ssh 'docker compose -f /opt/edcloud/compose/vintage-lab.yml up -d'
telnet edcloud 2323
```

## Daily operations

```bash
edc up
edc status
edc ssh
edc down
```

The instance also auto-shuts down after 30 minutes of idle activity.

To change the instance type, create a snapshot and resize the instance in place:

```bash
edc snapshot -d pre-resize-to-medium
edc snapshot --list
```

Wait until the new snapshot reports `completed` before you resize the instance.

```bash
edc resize --instance-type t3a.medium
edc verify
```

The state volume is independent of the instance type, so resizing preserves:

- SSH keys and logins
- Tailscale identity (same hostname/IP)
- Docker images and containers
- All files in `/home/ubuntu` and `/opt/edcloud/state`

Destroy safety guardrails:

```bash
INSTANCE_ID=i-0123456789abcdef0
edc destroy --confirm-instance-id "$INSTANCE_ID"
edc destroy --confirm-instance-id "$INSTANCE_ID" --require-fresh-snapshot
```

Cleanup volume protection defaults:

- Cleanup only deletes orphaned `root` role volumes by default.
- Orphaned `state` and unknown-role volumes are protected by default.
- Override only when intentionally performing full cleanup:

```bash
INSTANCE_ID=i-0123456789abcdef0
edc destroy --confirm-instance-id "$INSTANCE_ID" --allow-delete-state-volume
edc provision --cleanup --allow-delete-state-volume --allow-new-state-volume
```

### Tailscale naming and cleanup

Use this flow to keep DNS label stability (`edcloud` instead of `edcloud-N`) and avoid orphaned managed resources:

```bash
INSTANCE_ID=i-0123456789abcdef0
edc tailscale reconcile
edc destroy --confirm-instance-id "$INSTANCE_ID"
edc provision
```

Notes:

- `edc destroy` runs pre-destroy snapshot + cleanup by default.
- Cleanup deletes orphaned managed `root` volumes and protects `state` volumes by default.
- `edc provision` fails fast on Tailscale naming conflicts unless break-glass override is used.
- If stale offline `edcloud*` devices are reported, remove them in the [Tailscale machines admin page](https://login.tailscale.com/admin/machines) and rerun reconcile.

## Default host toolset baseline

Core host tools are part of `cloud-init/user-data.yaml` and applied at provision time.

Persistent home baseline:

- `~/` for `ubuntu` is bind-mounted to `/opt/edcloud/state/home/ubuntu`.
- First boot migrates existing `/home/ubuntu` contents into the state volume.
- This keeps shell/editor/tool settings across reprovision when reusing the state volume.

Persistent Tailscale identity baseline:

- `/var/lib/tailscale` is bind-mounted to `/opt/edcloud/state/tailscale`.
- This preserves node identity across reprovision and helps prevent DNS suffix drift.

Persistent compose + Portainer baseline:

- `/opt/edcloud/compose` is bind-mounted to `/opt/edcloud/state/compose`.
- `/opt/edcloud/portainer-data` is bind-mounted to `/opt/edcloud/state/portainer-data`.
- Portainer runs with `-v /opt/edcloud/portainer-data:/data`, preserving Portainer state across reprovision.

Persistent Docker engine baseline:

- Docker daemon `data-root` is set to `/opt/edcloud/state/docker`.
- This keeps Docker images/layers/volumes on the durable state volume across reprovision.

Swap baseline:

- Cloud-init provisions a single 4 GiB swapfile at `/swapfile` on the root volume.
- Swap is configured persistently in `/etc/fstab`, so it returns automatically after `edc down` / `edc up`.
- `vm.swappiness` is set to `10` to prefer RAM and use swap as burst/OOM protection.
- Provisioning logic is idempotent (single file + single fstab entry), preventing duplicate/stale swap entries.

Volume role tagging baseline:

- Managed volumes are explicitly tagged with `edcloud:volume-role`:
  - `root` for `/dev/sda1`
  - `state` for the configured persistent state device (default `/dev/sdf`)
- Cleanup and reuse behavior rely on these role tags for safety.

The exact package and tool versions are defined in `cloud-init/user-data.yaml`. Run `edc verify` after provisioning instead of maintaining a duplicate package checklist here.

### Safe rebuild

`edc reprovision` creates a pre-reprovision snapshot by default, destroys the instance, reuses the managed state volume, and provisions a replacement:

```bash
INSTANCE_ID=i-0123456789abcdef0
edc tailscale reconcile
edc reprovision --confirm-instance-id "$INSTANCE_ID"
edc status  # Repeat until Reachable: yes.
edc ssh 'cloud-init status --wait'
edc verify
```

Expected outcome:

- Fresh instance is provisioned.
- Existing managed state volume is reused.
- Tailscale identity and durable state under `/opt/edcloud/state` persist.
- SSH host identity persists via `/opt/edcloud/state/ssh-host-keys`.
- `edc verify` passes before resuming normal operations.

### Expand volumes

Use `edc resize` to request volume expansion without stopping the instance:

```bash
edc resize --volume-size 40
edc resize --state-volume-size 40
```

After AWS completes the modification, follow the command output to grow the partition or filesystem. EBS volumes cannot be shrunk; to reduce a volume, create a smaller replacement and copy the data.

## Backup and recovery

Operating policy:

- Treat host runtime as transient and rebuildable.
- Persist durable state under `/opt/edcloud/state`.
- Reclone git repositories from upstream on rebuild.
- Store bootstrap secrets in Parameter Store. Keep materialized credentials and OAuth files in access-restricted, untracked files.

Cloud-init can sync dotfiles and selected non-secret repositories from the authenticated GitHub account. Configure dotfiles with `--dotfiles-repo` and `--dotfiles-branch`. The `auto` repository setting resolves the authenticated user's dotfiles repository or the persisted checkout's origin. Cloud-init clones dotfiles but does not apply them; follow the dotfiles repository's instructions after a rebuild.

Ad-hoc snapshot operations (manual guardrails / pre-change points):

```bash
edc snapshot                        # Snapshot state volume
edc snapshot --list                 # List managed snapshots
edc snapshot-cost                   # Compare capacity-based cost proxy with soft cap
edc snapshot-cost --fail-on-cap     # Non-zero exit when proxy exceeds cap
edc snapshot -d pre-change-description # Named pre-change snapshot
edc restore-drill --attach-managed-instance  # Test snapshot-to-volume restoration
```

Snapshot creation is asynchronous. Before a destructive change, run `edc snapshot --list` until the new snapshot reports `completed`.

Manual retention cleanup applies to all managed snapshots, including DLM snapshots:

```bash
edc snapshot --prune                 # Dry-run: show what would be deleted (keep last 3)
edc snapshot --prune --apply         # Delete all but the 3 most recent snapshots
edc snapshot --prune --keep 5 --apply  # Keep 5 instead
```

The CLI-managed snapshot queue is the primary retention mechanism. Automatic lifecycle triggers prune the managed pool to retain three snapshots; manual snapshots remain until an explicit or later automatic prune places them beyond the limit. DLM support is opt-in and defaults to one daily, one weekly, and one monthly snapshot:

```bash
edc backup-policy status
edc backup-policy apply
edc backup-policy disable
```

DLM snapshots and CLI snapshots share the `edcloud:managed=true` tag. CLI pruning therefore does not preserve separate DLM retention tiers. Do not rely on both retention models at the same time without first changing the tagging or pruning behavior.

Run a non-destructive restore drill regularly:

```bash
# Use latest completed state-volume snapshot; temp volume is auto-cleaned up
edc restore-drill --attach-managed-instance

# Optional: target a specific snapshot
edc restore-drill --snapshot-id snap-xxxxxxxx --attach-managed-instance

# Optional: keep temp volume for deeper manual inspection (remember cleanup)
edc restore-drill --attach-managed-instance --keep-temporary-volume
```

Safety notes:

- Keep temporary drill resources tagged for purpose/audit (`purpose=restore-drill`).
- Keep temporary drill resources **out of managed discovery** (`edcloud:managed=false`).
- The default drill proves only that AWS can create and optionally attach a volume; it does not mount or inspect the filesystem.
- To validate files, use `--keep-temporary-volume`, mount the volume read-only, inspect it, then unmount, detach, and delete it manually.

Optional drill record helper:

```bash
install -m 0755 templates/operator/record-restore-drill.sh ~/.local/bin/edc-record-restore-drill
~/.local/bin/edc-record-restore-drill pass snap-xxxxxxxx "monthly drill"
cat ~/.config/edcloud/restore-drill.tsv
```

## Cost controls

`edc status` estimates compute and EBS storage from the rates and assumption of eight runtime hours per day in `edcloud/config.py`. `edc snapshot-cost` provides a capacity-based upper-bound proxy and can fail when it exceeds a chosen soft cap. These estimates do not replace the current AWS pricing pages or Cost Explorer.

## Troubleshooting

- Validate AWS identity: `aws sts get-caller-identity`
- Validate local tailnet state: `tailscale status`
- Validate instance and reachability: `edc status`

### SSH host key mismatch after rebuild

`edc reprovision` preserves host keys on the state volume. A fresh state volume generates new keys. `edc ssh` uses `StrictHostKeyChecking=accept-new`; it accepts unseen hosts but rejects a changed key.

After you verify that the instance was intentionally replaced, remove the stale entry:

```bash
IP_FROM_ERROR=100.64.0.1
ssh-keygen -f ~/.ssh/known_hosts -R "$IP_FROM_ERROR"
```

If Cline still asks for browser login on the instance:

Sync credentials from a browser-capable source machine:

```bash
# Run from your browser-capable source machine where Cline auth works
edc sync-cline-auth --remote ubuntu@edcloud
# Optional: print remote user/home/config path + cline version before syncing
edc sync-cline-auth --remote ubuntu@edcloud --remote-diagnostics
```

This command backs up and replaces `~/.cline/data/secrets.json` and `~/.cline/data/globalState.json` on the remote host. Use `--secrets-only` to skip `globalState.json`.

To authenticate interactively with port forwarding:

1. Start auth on edcloud and note the localhost port it prints:

   ```bash
   edc ssh "cline auth"
   ```

2. From your laptop, open an SSH tunnel to that same port (example `3000`):

   ```bash
   ssh -N -L 3000:127.0.0.1:3000 ubuntu@edcloud
   ```

3. Open your local browser to `http://127.0.0.1:3000` and complete the OAuth flow.
