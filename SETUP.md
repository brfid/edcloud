# edcloud Setup Guide

Complete first-time setup from zero to running instance.

## Active Priorities

Canonical active TODO list for this repo:

- [x] Enforce "no local state file" as a hard invariant: commands must rely on AWS tag-based discovery (`edcloud:managed=true`) only.
- [x] Add tests/validation that CLI flows do not read or write local state files.
- [x] Add tag-drift guardrails: detect missing management tags, duplicate managed instances, and orphaned managed resources with clear remediation output.
- [x] Define and enforce transient-instance policy: running host state may live for a while, but must be treated as disposable.
- [x] Ensure active work on the instance is recoverable from upstream or reproducible sources (container images, compose configs, git repos, scripted bootstrap), not manual-only host state.
- [x] Add and mount a dedicated 10GB EBS state volume at `/opt/edcloud/state` (separate from disposable host runtime).
- [x] Keep runtime secrets in AWS SSM Parameter Store; no plaintext secrets in git.
- [x] Standardize local operator command UX on ARM Linux around `edc` (`edc up`, `edc status`, `edc ssh`, `edc down`, `edc snapshot`, `edc destroy`).
- [x] Document and template local ARM operator setup needed for orchestration (`edc` command install/wrapper, env vars, and shell/profile integration).
- [x] Provide optional local orchestration templates where useful (for example systemd user units/timers for status checks, reminders, or snapshot cadence).
- [ ] Encode core host tools/settings in `cloud-init/user-data.yaml` (no manual-only baseline drift).
- [ ] Specify and version operator tool buildout + host config baseline (`packages`, services, files, and required settings) in `cloud-init/user-data.yaml`.
- [x] Define and keep fresh-reprovision verification commands in this guide.
- [ ] Set and follow snapshot cadence + retention policy for durable state (weekly + monthly, no daily baseline).
- [x] Add explicit snapshot pruning procedure (keep 8 weekly + 3 monthly snapshots, plus pre-change snapshots).
- [ ] Keep snapshot spend under a soft cap of `$5/month` and tune retention if exceeded.
- [ ] Run restore drills from recent snapshots and verify: SSH, Docker, Tailscale, Portainer, and workload data under `/opt/edcloud/state`.
- [ ] Record restore-drill date/result so backup posture is auditable over time.
- [ ] Back up non-repo durable state under `/opt/edcloud/state`; reclone git repos from upstream on rebuild.

## Prerequisites

- **AWS account** with credentials configured
- **Tailscale account** (free tier works)
- **Python 3.10+**
- **Git**
- **Operator device** running Linux/macOS/WSL. A small ARM host is valid
  (for example, Raspberry Pi Zero 2 W) if it can run Python, AWS CLI, and Tailscale.

## 1. AWS Setup

### Create IAM user (or use existing)

Minimum required permissions:
```
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
ssm:GetParameter  # for AMI resolution
ssm:PutParameter  # for storing Tailscale auth key in SSM
```

If you want managed policies, use: `AmazonEC2FullAccess` + `AmazonSSMFullAccess`
(or a least-privilege custom SSM policy with `ssm:GetParameter` and `ssm:PutParameter`).

### Configure credentials

```bash
aws configure
# AWS Access Key ID: ...
# AWS Secret Access Key: ...
# Default region: us-east-1  (or your preferred region)
# Default output format: json
```

Verify:
```bash
aws sts get-caller-identity
```

## 2. Tailscale Setup

### Generate an auth key

1. Go to https://login.tailscale.com/admin/settings/keys
2. Click **Generate auth key**
3. Settings:
   - ✅ Reusable (so you can reprovision without generating new keys)
   - ✅ Ephemeral (optional — instance removes itself from tailnet when terminated)
   - Tag: `tag:edcloud` (optional — for ACL management)
4. Copy the key (starts with `tskey-auth-...`)

### Store auth key for provisioning

Recommended: store the key in AWS SSM Parameter Store (SecureString):

```bash
aws ssm put-parameter \
  --name /edcloud/tailscale_auth_key \
  --type SecureString \
  --overwrite \
  --value 'tskey-auth-...'
```

Then provision with:
```bash
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

Quick-start alternative: environment variable:

```bash
export TAILSCALE_AUTH_KEY='tskey-auth-...'
```

Or add to your `~/.bashrc` / `~/.zshrc`:
```bash
# edcloud
export TAILSCALE_AUTH_KEY='tskey-auth-...'
```

**Security note**: Never commit this key to git. It's already in `.gitignore`.

## 3. Install edcloud

```bash
git clone <your-fork-or-repo>
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify:
```bash
edc --version
edc --help
```

`edc` is the primary command surface for local operations. `edcloud` remains as a compatibility alias.

### 3.1 Local ARM operator command setup (recommended)

Goal: run `edc up`, `edc status`, and related commands from a small ARM Linux operator node
without manually activating the venv each time.

Install wrapper + env template:
```bash
mkdir -p ~/.local/bin ~/.config/edcloud
install -m 0755 templates/operator/edc-wrapper.sh ~/.local/bin/edc
cp templates/operator/edc.env.example ~/.config/edcloud/edc.env
```

Ensure `~/.local/bin` is on PATH (add to `~/.profile` if needed):
```bash
export PATH="$HOME/.local/bin:$PATH"
```

If your repo checkout is not `~/edcloud`, set it in `~/.config/edcloud/edc.env`:
```bash
EDCLOUD_REPO=/path/to/edcloud
```

Optional automation templates (systemd user timers):
- `templates/operator/systemd-user/edc-weekly-snapshot.service`
- `templates/operator/systemd-user/edc-weekly-snapshot.timer`
- `templates/operator/systemd-user/edc-monthly-snapshot.service`
- `templates/operator/systemd-user/edc-monthly-snapshot.timer`
- `templates/operator/run-reprovision-verify.sh`
- `templates/operator/record-restore-drill.sh`

## 4. Provision your instance

```bash
edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key
```

This takes ~3-5 minutes:
- Creates EC2 instance (t3a.medium by default)
- Creates security group (no inbound rules)
- Attaches 80GB gp3 root volume + 10GB gp3 state volume
- Runs cloud-init (installs Docker, Tailscale, Portainer)

Output will show:
```json
{
  "instance_id": "i-0abc123...",
  "security_group_id": "sg-0def456...",
  "public_ip": "54.x.y.z"
}
```

## 5. Wait for cloud-init to complete

Cloud-init installs Docker, Tailscale, and Portainer. Check progress:

```bash
edc status
```

You'll see:
```
Instance:  i-0abc123...
State:     running
Type:      t3a.medium
Public IP: 54.x.y.z
Tailscale: 100.64.x.y (edcloud)
Reachable: yes
```

When `Reachable: yes`, it's ready.

### Verify on the instance

```bash
edc ssh
# Now on the instance:
docker ps
cat /tmp/edcloud-ready
```

If you see the Portainer container running, you're good.

### Fresh reprovision verification commands (canonical)

Use the built-in verification helper:
```bash
edc verify
```

Public-IP fallback (if your local tailnet cannot resolve the peer yet):
```bash
edc verify --public-ip
```

Machine-readable output for logs/automation:
```bash
edc verify --json-output
```

Optional template script to save verification output with timestamp:
```bash
install -m 0755 templates/operator/run-reprovision-verify.sh ~/.local/bin/edc-verify-reprovision
~/.local/bin/edc-verify-reprovision
```

## 6. Access Portainer

From any device on your tailnet:
```
https://edcloud:9443
```

First-time setup:
1. Create admin password
2. Choose "Get Started" → local Docker environment
3. Done

## 7. Deploy a workload

### Example: Vintage computing lab

```bash
scp compose/vintage-lab.yml ubuntu@edcloud:/opt/edcloud/compose/
edc ssh 'docker compose -f /opt/edcloud/compose/vintage-lab.yml up -d'
```

Or use Portainer:
1. Stacks → Add stack
2. Name: `vintage-lab`
3. Upload `compose/vintage-lab.yml` or paste its contents
4. Deploy

Access VAX console:
```bash
telnet edcloud 2323
```

## 8. Daily usage

**Start:**
```bash
edc up
```

**Check status:**
```bash
edc status
```

**SSH in:**
```bash
edc ssh
```

**Stop (manual):**
```bash
edc down
```

**Or let it auto-shutdown** after 30 minutes idle (no Tailscale SSH, low CPU).

## 9. Backup

Operating model (agreed baseline):
- Treat the EC2 host/runtime as transient and rebuildable.
- Keep durable assistant/workflow state under `/opt/edcloud/state` (target mount path).
- Reclone git repos from upstream on rebuild.
- Keep secrets out of git; store runtime secrets in AWS SSM Parameter Store.

What gets backed up:
- Durable state volume at `/opt/edcloud/state`
  (assistant memory/state and non-repo configs/data).
- Pre-change safety snapshots before risky infrastructure or workload changes.
- Current CLI behavior note: `edc snapshot` captures all EBS volumes attached to the instance.

Snapshot cadence (initial tempo):
- Weekly snapshot of durable state.
- Monthly snapshot of durable state.
- No daily baseline by default.

Retention target:
- Keep last 8 weekly snapshots.
- Keep last 3 monthly snapshots.
- Keep pre-change snapshots only as long as needed for rollback.

Create snapshots:
```bash
edc snapshot
```

List snapshots:
```bash
edc snapshot --list
```

Prune old periodic snapshots (safe preview first):
```bash
edc snapshot --prune --keep-weekly 8 --keep-monthly 3 --dry-run
edc snapshot --prune --keep-weekly 8 --keep-monthly 3 --apply
```

Pre-change snapshots:
```bash
edc snapshot -d pre-change-<reason>
```

Notes:
- Pruning targets snapshots with description prefixes `weekly-snapshot` and `monthly-snapshot`.
- Pre-change snapshots are not pruned by default.

Cost guardrail:
- Soft cap snapshot spend at `$5/month`; reduce retention windows if exceeded.

## 10. Core Host Baseline (Rebuild-Safe)

Current direction: define a core set of host tools/settings and make them reproducible.

Source of truth:
- `cloud-init/user-data.yaml` for baseline packages/services/files
- `compose/` for platform-managed workloads
- this `SETUP.md` for operational procedure

Rules:
- If you manually install a tool that should persist across rebuilds, add it to `cloud-init/user-data.yaml`.
- Keep dotfiles/system config that matter to operations in `write_files` or versioned scripts.
- Avoid "snowflake host" drift.

Suggested baseline workflow:
1. Define core tools list in `cloud-init/user-data.yaml` (`packages` + `runcmd`).
2. Reprovision a fresh instance and verify tooling from scratch.
3. Record verification commands in this document.

## 11. Backup + Recovery Standard

Policy baseline:
1. Weekly + monthly snapshots for durable state volume.
2. Monthly restore drill:
   - create/attach instance from recent snapshot,
   - verify Tailscale connectivity,
   - verify Docker and Portainer,
   - verify workload data under `/opt/edcloud/state`,
   - verify assistant state/memory expected to persist is present,
   - run `edc verify`.
3. Keep at least one known-good snapshot window before major changes.
4. Before risky changes, take an on-demand pre-change snapshot.
5. Prune snapshots beyond retention (`8 weekly + 3 monthly`) and track spend against the `$5/month` soft cap.

Restore drill audit record (recommended):
```bash
install -m 0755 templates/operator/record-restore-drill.sh ~/.local/bin/edc-record-restore-drill
~/.local/bin/edc-record-restore-drill pass snap-xxxxxxxx "monthly drill"
cat ~/.config/edcloud/restore-drill.tsv
```

## 12. Cost management

At 4 hours/day:
- Compute: ~$4.51/mo
- Storage: ~$7.20/mo
- Snapshots: variable (target soft cap `$5/mo`; baseline often lower)
- **Total: typically ~$12/mo + snapshot usage**

The instance auto-shuts-down when idle (no active SSH + low CPU for 30 minutes).

## Troubleshooting

- Run `edc status` for instance/connection/cost visibility.
- Validate AWS identity with `aws sts get-caller-identity`.
- Validate local Tailscale with `tailscale status`.
