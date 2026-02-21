# edcloud runbook

Operator runbook for provisioning, operating, and recovering a single-instance edcloud deployment.

## Scope

This file is the stable operator procedure guide.

- Mutable project status, active queue, and agent working memory live in
  `CHANGELOG.md` under `## [Unreleased]`.
- Use this runbook for durable procedures, recovery drills, and operational checklists.

## Deferred backlog

Open items:

- [x] Add a safe rebuild workflow (`snapshot -> reprovision -> verify`) as a single documented operator path. (`edc reprovision` now prints a post-run reminder to run `edc verify`.)
- [ ] Improve automatic repo loading: currently dotfiles/bin/llm-config cloning depends on gh auth during cloud-init; consider making repo list configurable and/or adding explicit clone step to provision workflow (e.g., `edc provision --sync-repos`).
- [ ] Evaluate a secure operator login workflow that starts from one memorized string without weakening Tailscale/AWS MFA controls.
- [ ] Centralize default SSH username in repo config (for example `edcloud/config.py`) and have `edc ssh`/`edc verify` read that value.
- [ ] Keep snapshot spend under soft cap `$2/month`; adjust DLM retention (`edc backup-policy apply --daily-keep N --weekly-keep M --monthly-keep K`) if exceeded.
- [ ] Run restore drills from recent snapshots and verify SSH, Docker, Tailscale, Portainer, and data under `/opt/edcloud/state`.
- [ ] Record restore drill date and result for auditability.
- [ ] Back up non-repo durable state under `/opt/edcloud/state`; reclone repos from upstream on rebuild.

### Agent tooling (deferred)

- [ ] **Agent-agnostic skills system**: store reusable agent skills in a
  format-neutral source (e.g. plain markdown with structured frontmatter) and
  emit them into agent-specific formats on demand — `.claude/skills/` for
  Claude Code, `AGENTS.md` skill blocks for Codex/Cline, `GEMINI.md` sections
  for Gemini CLI, etc. Keeps skills DRY across a multi-agent workflow without
  locking into any one tool's convention. Likely a small CLI or script;
  location TBD (could live here, in a dotfiles repo, or as a standalone tool).

### Testing gaps (deferred)

- [ ] `cleanup.py` unit tests (`test_cleanup_tailscale_devices`, `test_cleanup_orphaned_volumes_delete_mode`, `test_run_cleanup_workflow`)
- [ ] Integration tests for destroy/cleanup workflow end-to-end
- [ ] End-to-end provision/destroy cycle tests

### Architectural improvements (deferred)

- [ ] **Centralize boto3 client factories**: `cli.py` and `cleanup.py` call `boto3.client()` directly instead of reusing `_ec2_client()`/`_ssm_client()` factories from `ec2.py`. Better: shared session or factory module. Simplifies mock patching in tests.
- [ ] **Declarative verification checks**: The 24-item `checks` list in `verify_cmd` (cli.py ~700-725) is maintenance-heavy inline data. Extract to typed dataclass list or YAML for easier additions and self-documenting check catalog.

## Prerequisites

- AWS account with CLI credentials configured
- Tailscale account
- Python 3.10+
- Git
- Linux/macOS/WSL operator environment

A small ARM Linux operator node is supported if it can run Python, AWS CLI, and Tailscale.

Operator model note: this runbook follows the canonical policy in `README.md`
section **"Operator model"** (CLI-first on a lightweight terminal device;
AWS Console as inspection/break-glass fallback).

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
dlm:CreateLifecyclePolicy
dlm:GetLifecyclePolicies
dlm:GetLifecyclePolicy
dlm:UpdateLifecyclePolicy
ssm:GetParameter
ssm:PutParameter
iam:CreateRole
iam:GetRole
iam:PutRolePolicy
iam:AttachRolePolicy
iam:DeleteRolePolicy
iam:ListRolePolicies
iam:DeleteRole
iam:CreateInstanceProfile
iam:GetInstanceProfile
iam:AddRoleToInstanceProfile
iam:RemoveRoleFromInstanceProfile
iam:DeleteInstanceProfile
iam:PassRole
sts:GetCallerIdentity
```

Configure and verify credentials:

```bash
aws configure
aws sts get-caller-identity
```

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

Secret behavior on new builds:

- SSM values are consumed by bootstrap/provision steps when needed.
- They are **not** automatically exported as persistent login-shell environment variables.
- Keep runtime secrets in SSM (or local non-git files such as `~/.secrets`) and load explicitly when required.

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

## 4. Operator command execution on ARM/Linux nodes

Run `edc` from the repo-local virtualenv to keep tooling reproducible and avoid
system-level installs.

```bash
source .venv/bin/activate
edc --version
edc status
```

Or invoke directly without activating:

```bash
.venv/bin/edc --version
.venv/bin/edc status
```

Optional shell convenience for this terminal session:

```bash
alias edc="$PWD/.venv/bin/edc"
```

To make `edc provision` work without repeating key flags, set:

```bash
export TAILSCALE_AUTH_KEY_SSM_PARAMETER=/edcloud/tailscale_auth_key
```

Optional automation templates:

- `templates/operator/run-reprovision-verify.sh`
- `templates/operator/record-restore-drill.sh`
- `edc sync-cline-auth` (sync Cline ChatGPT Subscription auth from a browser-capable machine to a headless edcloud host)

## 5. Provision

```bash
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

If `TAILSCALE_AUTH_KEY_SSM_PARAMETER` is set in your operator env file:

```bash
edc provision
```

Common size configurations:

```bash
# Minimal (saves max money: ~$6/month total)
edc provision --instance-type t3a.small --volume-size 12 --state-volume-size 15

# Default (balanced: ~$7/month total)
edc provision  # Uses: t3a.small, 16GB root, 20GB state

# Comfortable (more headroom: ~$9/month total)
edc provision --instance-type t3a.small --volume-size 20 --state-volume-size 30

# Power user (heavier workloads: ~$12/month total)
edc provision --instance-type t3a.medium --volume-size 30 --state-volume-size 40
```

State-volume guardrails:

- Reuse existing managed state volume is now the default (fail-fast if none exists).
- Allow creating a new state volume only when intentionally needed:

```bash
edc provision --allow-new-state-volume
```

Expected resources:

- 1x EC2 instance (`t3a.small` default; use `--instance-type t3a.medium` for heavier workloads)
- Security group with zero inbound rules
- 16 GB gp3 root volume (expandable; use `--volume-size` to override)
- 20 GB gp3 state volume mounted at `/opt/edcloud/state` (expandable; use `--state-volume-size` to override)

Tailscale identity guardrails:

- `edc provision` now fails fast if duplicate/suffixed `edcloud` Tailscale records exist.
- Use `edc tailscale reconcile --dry-run` to inspect conflicts before provisioning.
- Break-glass override: `--allow-tailscale-name-conflicts`.

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
edc ssh 'cloud-init status --wait'
```

**Note:** `edc ssh` automatically detects the active edcloud device (handles edcloud, edcloud-2, edcloud-3, etc.). See `docs/TAILSCALE-CLEANUP.md` for managing multiple devices.

Preflight recommended before rebuild/provision:

```bash
edc tailscale reconcile --dry-run
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

Switching instance types (resize for heavier workloads):

```bash
# Snapshot before any destructive operation
edc snapshot -d pre-resize-to-medium

# Destroy current instance (state volume is preserved!)
edc destroy --confirm-instance-id <instance-id>

# Reprovision with larger instance type
edc provision --instance-type t3a.medium

# Verify everything works - all your data/logins/Tailscale identity persist
edc verify
```

Your state volume is completely independent of instance type, so resizing preserves:
- SSH keys and logins
- Tailscale identity (same hostname/IP)
- Docker images and containers
- All files in `/home/ubuntu` and `/opt/edcloud/state`

Destroy safety guardrails:

```bash
edc destroy --confirm-instance-id <instance-id>
edc destroy --confirm-instance-id <instance-id> --require-fresh-snapshot
```

Cleanup volume protection defaults:

- Cleanup only deletes orphaned `root` role volumes by default.
- Orphaned `state` and unknown-role volumes are protected by default.
- Override only when intentionally performing full cleanup:

```bash
edc destroy --confirm-instance-id <instance-id> --allow-delete-state-volume
edc provision --cleanup --allow-delete-state-volume
```

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

Volume role tagging baseline:

- Managed volumes are explicitly tagged with `edcloud:volume-role`:
  - `root` for `/dev/sda1`
  - `state` for the configured persistent state device (default `/dev/sdf`)
- Cleanup and reuse behavior rely on these role tags for safety.

Neovim + LazyVim baseline:

- Cloud-init pins Neovim to upstream `v0.11.3` (installed under `/opt/nvim-linux-x86_64` and linked at `/usr/local/bin/nvim`).
- This satisfies LazyVim's minimum requirement (`>= 0.11.2`) on fresh builds.

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
- `neomutt`
- `neovim`
- `python3-dev`
- `python3-pip`
- `python3-venv`
- `rclone`
- `ripgrep`
- `screen`
- `rsync`
- `tmux`
- `tree`
- `unattended-upgrades`
- `unzip`
- `vim-tiny`
- `xclip`
- `zip`

AI + Python dev baseline:

- Node.js LTS with pinned global AI CLIs:
  - `npm@11.9.0`
  - `@openai/codex@0.98.0`
  - `cline@2.2.2`
  - `@google/gemini-cli`
- Node.js LTS with latest-at-build global AI CLI:
  - `@anthropic-ai/claude-code` (intentionally unpinned)
- Python developer tools (user-local):
  - `ruff`
  - `mypy`
  - `pytest`
  - `ipython`

Default profile notes:

- Game packages are intentionally excluded from the default host build.
- Baseline focuses on headless/server operations, AI CLIs, and Python development tooling.

Package strategy:

- Prefer Ubuntu APT packages for baseline reproducibility and low friction.
- Install Homebrew by default for optional package gaps and operator preference.
- Keep core runbook/tooling functional without requiring Homebrew formulas.

Quick verification:

```bash
edc ssh 'git --version && tmux -V && rg --version && fdfind --version && htop --version | head -n 1'
edc ssh 'nvim --version | head -n 1 && byobu -V && gh --version | head -n 1 && brew --version | head -n 1'
edc ssh 'node --version && npm --version && codex --version && cline --version && gemini --version && claude --version'
edc ssh 'python3 --version && ruff --version && mypy --version && pytest --version'
edc ssh 'findmnt /home/ubuntu /var/lib/tailscale /opt/edcloud/compose /opt/edcloud/portainer-data && df -h /home/ubuntu /opt/edcloud/state'
edc ssh "docker info --format '{{.DockerRootDir}}'"
```

### Canonical safe rebuild workflow (golden path)

Use this as the default operator drill whenever making potentially disruptive host changes.

```bash
edc tailscale reconcile --dry-run
edc snapshot -d pre-change-rebuild
edc reprovision --confirm-instance-id <instance-id>
edc verify
```

Expected outcome:

- Fresh instance is provisioned.
- Existing managed state volume is reused.
- Tailscale identity and durable state under `/opt/edcloud/state` persist.
- `edc verify` passes before resuming normal operations.

Manual equivalent (same persistent state volume, no Tailscale name increment):

```bash
edc tailscale reconcile --dry-run
edc snapshot -d pre-change-rebuild
edc destroy --confirm-instance-id <instance-id>
edc provision
edc verify
```

`edc reprovision` is the preferred single-command path for destroy + provision.

```bash
edc reprovision --confirm-instance-id <instance-id>
```

Volume size adjustment:

For an online volume expand, use `edc resize`:

```bash
edc resize --volume-size 24          # expand root volume online
edc resize --state-volume-size 30    # expand state volume online
```

The manual AWS CLI commands below remain as a reference:

```bash
# Provision with custom sizes (smaller or larger)
edc provision --volume-size 12 --state-volume-size 15

# Check current usage
edc ssh 'df -h / /opt/edcloud/state'

# Expand volumes online (no rebuild needed!)
# 1. Get volume IDs
edc ssh 'lsblk -o NAME,SIZE,MOUNTPOINT,TYPE | grep -E "disk|part"'

# 2. Modify volume size (example: expand state volume to 30GB)
aws ec2 modify-volume --volume-id vol-xxxxxx --size 30

# 3. Wait for modification to complete (~1 min)
aws ec2 describe-volumes-modifications --volume-id vol-xxxxxx

# 4. Extend filesystem to use new space
edc ssh 'sudo resize2fs /dev/nvme1n1'  # state volume
edc ssh 'sudo resize2fs /dev/root'     # root volume
```

**Note:** EBS volumes can only be expanded, not shrunk. To reduce size, you must create a new smaller volume and copy data (or reprovision with smaller `--volume-size` flags).

## 10. Backup and recovery standard

Operating policy:

- Treat host runtime as transient and rebuildable.
- Persist durable state under `/opt/edcloud/state`.
- Reclone git repositories from upstream on rebuild.
- Store secrets in SSM, not in git.

Non-secret repo sync baseline:

- If `gh` is authenticated during cloud-init, bootstrap attempts to pull/update:
  - `https://github.com/<gh-user>/dotfiles.git` → `~/src/dotfiles`
  - `https://github.com/<gh-user>/bin.git` → `~/src/bin`
  - `https://github.com/<gh-user>/llm-config.git` → `~/src/llm-config`
- If `~/src/dotfiles/install.sh` exists and is executable, it is run.
- Executable files in `~/src/bin` are symlinked into `~/.local/bin`.
- Keep these repos non-secret; secrets still belong in SSM/local private files.

AWS-native policy operations (recommended baseline):

```bash
edc backup-policy status
edc backup-policy apply                    # daily keep 7 + weekly keep 4 + monthly keep 2
edc backup-policy apply --daily-keep 7 --weekly-keep 4 --monthly-keep 2
edc backup-policy disable
```

Ad-hoc snapshot operations (manual guardrails / pre-change points):

```bash
edc snapshot                        # Snapshot state volume
edc snapshot --list                 # List all snapshots
edc snapshot -d pre-change-<reason> # Named pre-change snapshot
```

Manual retention cleanup for ad-hoc snapshots (state volume only; root is never snapshotted):

```bash
edc snapshot --prune                 # Dry-run: show what would be deleted (keep last 3)
edc snapshot --prune --apply         # Delete all but the 3 most recent snapshots
edc snapshot --prune --keep 5 --apply  # Keep 5 instead
```

Policy targets:

- Use DLM retention tiers: daily keep 7 + weekly keep 4 + monthly keep 2 (~1-2 month recovery points)
- Keep snapshot spend under `$2/month`

Restore drill baseline (monthly, non-destructive "shadow" drill):

Use a temporary volume restored from a snapshot. Do **not** detach or modify the
production state volume.

```bash
# 0) Identify current state volume + latest completed snapshot
STATE_VOL_ID=$(aws ec2 describe-volumes \
  --filters Name=tag:edcloud:managed,Values=true Name=tag:edcloud:volume-role,Values=state \
  --query 'Volumes[0].VolumeId' --output text)

SNAP_ID=$(aws ec2 describe-snapshots \
  --filters Name=volume-id,Values="$STATE_VOL_ID" Name=status,Values=completed \
  --owner-ids self \
  --query 'reverse(sort_by(Snapshots,&StartTime))[0].SnapshotId' --output text)

# 1) Create temporary restore-drill volume (same AZ as live instance)
AZ=$(aws ec2 describe-volumes --volume-ids "$STATE_VOL_ID" --query 'Volumes[0].AvailabilityZone' --output text)
TMP_VOL_ID=$(aws ec2 create-volume \
  --snapshot-id "$SNAP_ID" \
  --availability-zone "$AZ" \
  --volume-type gp3 \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=restore-drill-temp},{Key=purpose,Value=restore-drill},{Key=edcloud:managed,Value=false}]' \
  --query 'VolumeId' --output text)

aws ec2 wait volume-available --volume-ids "$TMP_VOL_ID"

# 2) Attach temp volume to live instance as a secondary device (do not replace state volume)
INSTANCE_ID=$(edc status | awk '/^Instance:/ {print $2}')
aws ec2 attach-volume --volume-id "$TMP_VOL_ID" --instance-id "$INSTANCE_ID" --device /dev/sdg
aws ec2 wait volume-in-use --volume-ids "$TMP_VOL_ID"

# 3) Use direct SSH (not edc subcommands) while temp unmanaged volume is attached
ssh ubuntu@edcloud 'lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE'

# 4) Mount restored volume read-only and verify expected durable layout
ssh ubuntu@edcloud 'sudo mkdir -p /mnt/restore-drill && sudo mount -o ro,noload /dev/nvme2n1 /mnt/restore-drill'
ssh ubuntu@edcloud 'findmnt /mnt/restore-drill && sudo ls -la /mnt/restore-drill'

# Optional write-safety check (should fail with Read-only file system)
ssh ubuntu@edcloud "sudo sh -c 'touch /mnt/restore-drill/.restore-drill-write-test'" || true

# 5) Cleanup (always): unmount, detach, delete temp volume
ssh ubuntu@edcloud 'sudo umount /mnt/restore-drill && sudo rmdir /mnt/restore-drill'
aws ec2 detach-volume --volume-id "$TMP_VOL_ID"
aws ec2 wait volume-available --volume-ids "$TMP_VOL_ID"
aws ec2 delete-volume --volume-id "$TMP_VOL_ID"

# 6) Final normal checks on live host
edc status
edc verify
```

Safety notes:

- Keep temporary drill resources tagged for purpose/audit (`purpose=restore-drill`).
- Keep temporary drill resources **out of managed discovery** (`edcloud:managed=false`).
- While a temporary unmanaged volume is attached, some `edc` subcommands may fail
  with tag-drift guardrails; use direct `ssh` for drill checks and run `edc`
  commands again after cleanup.

Optional drill record helper:

```bash
install -m 0755 templates/operator/record-restore-drill.sh ~/.local/bin/edc-record-restore-drill
~/.local/bin/edc-record-restore-drill pass snap-xxxxxxxx "monthly drill"
cat ~/.config/edcloud/restore-drill.tsv
```

## 11. Cost guardrail

Typical target at 4 hours/day with default settings (`t3a.small`, 16GB root, 30GB state):

- Compute: about `$2.26/month` (t3a.small) or `$4.51/month` (t3a.medium)
- Storage: about `$3.68/month` (46GB total: 16GB root + 30GB state)
- Snapshots: ~`$1.50/month` (3 × 30GB state snapshots at $0.05/GB)
- **Monthly total: ~$7–8**

Instance type selection:

- `t3a.small` (default): 2 vCPU, 2 GB RAM - suitable for light Docker + dev work
- `t3a.medium`: 2 vCPU, 4 GB RAM - use `--instance-type t3a.medium` for heavier workloads
- State volume persists across instance type changes, so you can resize without data loss

Use `edc status` and AWS Cost Explorer to track drift.

## Troubleshooting

- Validate AWS identity: `aws sts get-caller-identity`
- Validate local tailnet state: `tailscale status`
- Validate instance and reachability: `edc status`

If Cline still asks for browser login on the instance:

Preferred (automation-friendly) path:

```bash
# Run from your browser-capable source machine where Cline auth works
cd ~/edcloud
edc sync-cline-auth --remote ubuntu@edcloud
# Optional: print remote user/home/config path + cline version before syncing
edc sync-cline-auth --remote ubuntu@edcloud --remote-diagnostics
```

This syncs `~/.cline/data/secrets.json` and `~/.cline/data/globalState.json`
to the remote with backup/permissions and avoids interactive browser auth on
EC2. Use `--secrets-only` to skip `globalState.json` when needed.

Fallback interactive path (port forward):

1. Start auth on edcloud and note the localhost port it prints:

   ```bash
   edc ssh "cline auth"
   ```

2. From your laptop, open an SSH tunnel to that same port (example `3000`):

   ```bash
   ssh -N -L 3000:127.0.0.1:3000 ubuntu@edcloud
   ```

3. Open your local browser to `http://127.0.0.1:3000` and complete the OAuth flow.
