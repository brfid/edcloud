# edcloud

Single-instance AWS EC2 personal cloud lab for x86_64 Linux workloads.

## Portfolio snapshot (concise)

This project is a practical reliability/cost/security lab rather than a
production platform.

- **What it does well today:** repeatable single-host lifecycle (`provision`,
  `reprovision`, `verify`), Tailscale-only access, and snapshot/restore drill
  workflows.
- **Primary constraints:** single operator, single instance, low monthly spend,
  no public inbound network exposure.
- **Tradeoffs made intentionally:** operational simplicity and clear guardrails
  over multi-node scale and deep automation complexity.
- **Current focus areas:** continued thin-CLI extraction, restore-drill
  discipline, and documentation clarity.

Quick proof commands:

```bash
edc verify
edc restore-drill --attach-managed-instance
edc snapshot-cost --soft-cap-usd 2.0
```

Minimal demo flow (3-5 minutes):

```bash
edc tailscale reconcile --dry-run
edc snapshot -d pre-demo
edc reprovision --confirm-instance-id <instance-id>
edc verify
```

## Operator model

edcloud is designed to be operated from a lightweight terminal device (including
small ARM/Linux nodes) using AWS + CLI tooling, not primarily from the AWS
Console.

- Primary path: `edc` + AWS CLI + Python + Tailscale from an operator device.
- AWS Console path: inspection and break-glass/manual fallback.
- Recurring lifecycle workflows (provision, verify, snapshot, reprovision,
  cleanup) should stay CLI-driven for repeatability and safety guardrails.

**Core design:**
- Tailscale-only access (zero inbound rules)
- Tag-based resource discovery (no state files)
- Persistent home on state volume
- Persistent Tailscale node identity on state volume
- Portainer for container management

## Quick start

```bash
# Prerequisites: AWS CLI, Python 3.10+, Tailscale account

git clone <repo> && cd edcloud
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Store Tailscale key in SSM
aws ssm put-parameter --name /edcloud/tailscale_auth_key \
  --type SecureString --value '<TAILSCALE_AUTH_KEY>'

# Provision
edc provision
```

ARM/Linux operator note:

- Prefer running from the repo-local virtualenv (`.venv`) to keep tooling reproducible.
- Use either `source .venv/bin/activate` or invoke commands directly as `.venv/bin/edc ...`.

## Commands

```bash
edc tailscale reconcile --dry-run   # Detect edcloud naming conflicts before lifecycle actions
edc provision [--cleanup]  # Create instance (requires existing state volume by default)
edc up/down                          # Start/stop instance (up also fail-fast on naming conflicts)
edc ssh [command]                    # SSH via Tailscale
edc status                   # Instance state, IPs, cost estimate
edc setup-ssm-tokens         # Store GitHub/Tailscale tokens in SSM
edc permissions show         # Show required IAM actions by command profile
edc permissions policy       # Emit operator least-privilege IAM policy JSON
edc permissions verify       # Preflight-check your current AWS principal permissions
edc sync-cline-auth          # Sync Cline OAuth secrets to headless remote host
edc sync-cline-auth --remote-diagnostics  # Print remote Cline path/version + config target
edc verify                   # Bootstrap validation
edc backup-policy status     # Show AWS DLM backup policy status
edc backup-policy apply      # Ensure DLM policy (daily 7 + weekly 4 + monthly 2)
edc backup-policy disable    # Disable managed DLM policy
edc snapshot [-d desc]       # Create snapshot (state volume only)
edc snapshot --list          # List snapshots
edc snapshot --prune [--keep N] [--apply]  # Optional manual cleanup for ad-hoc snapshots
edc snapshot-cost [--soft-cap-usd 2.0] [--fail-on-cap]  # Snapshot spend guardrail signal
edc restore-drill [--attach-managed-instance]  # Run non-destructive restore drill with temp volume
edc destroy --confirm-instance-id ID  # Terminate instance (snapshot + cleanup run by default)
```

Use `--allow-tailscale-name-conflicts` only for break-glass cases.

Auth-sync diagnostics note:

- `--remote-diagnostics` is provided by `edc sync-cline-auth`.
- It is **not** a flag on `cline auth`.

Safe rebuild golden path:

```bash
edc tailscale reconcile --dry-run
edc snapshot -d pre-change-rebuild
edc reprovision --confirm-instance-id <instance-id>
edc verify
```

See `RUNBOOK.md` section **"Canonical safe rebuild workflow (golden path)"** for details and expected outcomes.

**Destroy defaults:** Auto-snapshot and cleanup (Tailscale devices + orphaned volumes) both run by default. Opt-out: `--skip-snapshot`, `--skip-cleanup`.

**Backup defaults:** AWS DLM is the native retention engine. Recommended baseline:
`edc backup-policy apply` (daily keep 7 + weekly keep 4 + monthly keep 2 ≈ dense recent points plus ~1 and ~2 month recovery points).

Volume safety guardrails:

- Managed volumes are role-tagged with `edcloud:volume-role` (`root` or `state`).
- Cleanup protects `state` and unknown-role volumes by default.
- To allow full deletion during cleanup, use `--allow-delete-state-volume`.
- Provision now **requires** reusing an existing managed state volume by default.
- To allow creating a fresh state volume (break-glass/new setup), use `--allow-new-state-volume`.

LazyVim compatibility:

- Cloud-init installs Neovim `v0.11.3` from upstream release tarball so LazyVim's `>= 0.11.2` requirement is met on new builds.

## Architecture

**Compute:** t3a.small, Ubuntu 24.04, Tailscale SSH only
**Storage:** 16GB root (disposable), 20GB state at `/opt/edcloud/state` (persistent)
**Discovery:** Tag `edcloud:managed=true` on all resources
**Secrets:** AWS SSM Parameter Store
**Baseline:** Docker, Portainer, Node.js, Python, and dev tooling are defined in `cloud-init/user-data.yaml`.

For full technical detail, see:

- `RUNBOOK.md` for durable host baseline, rebuild workflow, backup/restore operations, and operator procedures.
- `docs/ARCHITECTURE.md` for code/module boundaries and architecture decisions.

## Cost

4hr/day usage: ~$2.26 compute + ~$2.88 storage + ~$1.00 snapshots ≈ **~$6–7/month**
Auto-shutdown after 30min idle.

## Docs

- **Cold-start sequence (recommended):** `README.md` → `CHANGELOG.md` (`[Unreleased]`) → `SECURITY.md` → `RUNBOOK.md` → `docs/ARCHITECTURE.md`.
- `RUNBOOK.md` - Complete operator runbook
- `CHANGELOG.md` - Project history + current mutable status (`[Unreleased]`)
- `SECURITY.md` - Threat model
- `AGENTS.md` - AI agent constraints
- `docs/ARCHITECTURE.md` - Code structure and architecture decisions
