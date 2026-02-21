# Architecture

## Module structure

```
edcloud/
├── cli.py          # Click commands (thin wrappers)
├── ec2.py          # EC2 lifecycle via boto3
├── snapshot.py     # EBS snapshot ops + auto-snapshot
├── dlm.py          # AWS DLM lifecycle policy management (planned — see below)
├── tailscale.py    # Device discovery, SSH helpers
├── cleanup.py      # Cleanup orchestration
├── iam.py          # IAM role + instance profile management
├── config.py       # Defaults, InstanceConfig dataclass
└── aws_check.py    # Credential validation
```

## Design principles

**DRY:** Cleanup logic centralized in `cleanup.py` (was duplicated in destroy/provision).

**Separation of concerns:**
- CLI: User interaction, flags
- Modules: Business logic
- No cross-contamination

**Composition:** Commands compose reusable modules.

## Key refactoring

### Before
```python
# destroy command: ~50 lines inline cleanup logic
# provision command: ~60 lines duplicate cleanup logic
# Total: ~110 lines duplicated
```

### After
```python
# destroy command
if cleanup:
    snapshot.auto_snapshot_before_destroy()
    cleanup_module.run_cleanup_workflow("post-destroy")

# provision command
if cleanup:
    snapshot.auto_snapshot_before_destroy()
    cleanup_module.run_cleanup_workflow("pre-provision")

# Total: ~10 lines, zero duplication
```

## cleanup.py API

```python
cleanup_tailscale_devices(interactive: bool) -> bool
    # Detects offline edcloud devices, shows removal instructions

cleanup_orphaned_volumes(mode: str) -> bool
    # Modes: "interactive", "delete", "keep"
    # Finds volumes with edcloud:managed=true, status=available

run_cleanup_workflow(phase: str, skip_snapshot: bool, interactive: bool, allow_delete_state: bool) -> bool
    # Orchestrates full cleanup (Tailscale + volumes)
    # Phases: "pre-provision" or "post-destroy"
```

## snapshot.py API

```python
auto_snapshot_before_destroy() -> list[str]
    # Snapshots state volume only; returns [] if no instance exists
    # Called by default on destroy/reprovision; opt-out with --skip-snapshot

create_snapshot(description) -> list[str]
    # Snapshots only volumes tagged edcloud:volume-role=state
    # Root volume is disposable (rebuilt by cloud-init) and never snapshotted
    # Falls back to all volumes if no state-tagged volume is found

prune_snapshots(keep_last=3, dry_run=True) -> dict
    # Deletes all but the most recent keep_last snapshots
    # All snapshots eligible regardless of description prefix
    # CLI: edc snapshot --prune [--keep N] [--apply]
```

## Default behaviors

**Auto-snapshot on destroy:** ON by default. Opt-out: `--skip-snapshot`.

**Cleanup on destroy:** ON by default (Tailscale devices + orphaned volumes). Opt-out: `--skip-cleanup`.

**Root volume lifecycle:** `DeleteOnTermination=True` — root volume is auto-deleted by AWS on instance termination. Only the state volume survives.

**Snapshot scope:** State volume only. Root is disposable and rebuilt by cloud-init on every provision; snapshotting it is wasteful. ~$1.50/month for 3 × 30GB snapshots.

**Rationale:**
- Prevents state data loss across reprovision cycles
- Default-safe: no orphaned volumes from normal destroy/reprovision cycles

**Volume cleanup modes:**
- Interactive: Prompt with options (default for `provision --cleanup`)
- Delete: Auto-delete root-tagged volumes (used by `reprovision` and `destroy` cleanup)
- Keep: Skip deletion (reuse state volume)

## Data flow

### destroy (default)
```
Auto-snapshot → Destroy (root vol auto-deleted) → Tailscale cleanup → Orphaned volume cleanup
```

### provision --cleanup
```
Auto-snapshot (if instance exists) → Tailscale cleanup prompt → Volume cleanup → Provision
```

## DLM snapshot lifecycle (planned — not yet implemented)

Replace the ad-hoc weekly/monthly systemd timers with AWS Data Lifecycle Manager (DLM), which handles scheduling, retention, and pruning natively at no extra cost.

### What changes

| Before | After |
|---|---|
| `templates/operator/systemd-user/edc-weekly-snapshot.*` | deleted |
| `templates/operator/systemd-user/edc-monthly-snapshot.*` | deleted |
| `prune_snapshots()` / `edc snapshot --prune` | kept as manual escape hatch only |
| No automatic pruning | DLM handles it |

### New resources provisioned by `edc provision`

**IAM role: `edcloud-dlm-role`**
- Trust: `dlm.amazonaws.com`
- Attached managed policy: `arn:aws:iam::aws:policy/service-role/AmazonDLMServiceRole`
- Created in `dlm.py:ensure_dlm_role()`, deleted in `dlm.py:delete_dlm_role()`

**DLM lifecycle policy**
- Description (used as stable identifier): `edcloud-snapshot-lifecycle`
- Targets volumes tagged: `edcloud:volume-role=state`
- Tagged: `edcloud:managed=true`
- Three schedules, each with `RetainRule.Count=1`:
  - `daily` — every 24h at 03:00 UTC
  - `weekly` — cron `cron(0 3 ? * SUN *)`
  - `monthly` — cron `cron(0 3 1 * ? *)`
- Result: 3 managed snapshots at steady state; ramps up naturally over ~30 days
- Created in `dlm.py:ensure_dlm_policy()`, deleted in `dlm.py:delete_dlm_policy()`

### dlm.py API (to be created)

```python
ensure_dlm_role(tags: dict[str, str]) -> str        # idempotent; returns role ARN
ensure_dlm_policy(role_arn: str, tags: dict) -> str  # idempotent; returns policy ID
find_dlm_policy() -> str | None                      # finds by TargetTag + description
delete_dlm_policy() -> None
delete_dlm_role() -> None
```

### Integration points

- `ec2.py provision()`: after step 2 (IAM instance profile), call `ensure_dlm_role()` then `ensure_dlm_policy()`
- `ec2.py destroy()`: after `delete_instance_profile()`, call `delete_dlm_policy()` then `delete_dlm_role()`

### Key boto3 API notes

```python
dlm = boto3.client('dlm')

# Tags on the DLM policy itself use dict format (not [{Key,Value}] list)
Tags={'edcloud:managed': 'true'}

# Finding existing policy — filter format is "key=value" string
dlm.get_lifecycle_policies(TargetTags=['edcloud:volume-role=state'])
# Then filter results by Description == 'edcloud-snapshot-lifecycle'

# CronExpression and Interval+IntervalUnit are mutually exclusive in CreateRule
# IntervalUnit for CreateRule is 'HOURS' only (not DAYS)
```

### Pre-destroy snapshots

`auto_snapshot_before_destroy()` (called on `edc destroy`/`reprovision`) creates on-demand snapshots that DLM does **not** manage or prune. These accumulate slowly and can be manually pruned with `edc snapshot --prune --apply`. No change needed there.

### Operator IAM requirements added

The operator running `edc provision` needs:
- `dlm:CreateLifecyclePolicy`, `dlm:DeleteLifecyclePolicy`, `dlm:GetLifecyclePolicies`
- `iam:PassRole` (to pass the DLM role to the DLM service)

## Future enhancements

**Tailscale API:** Programmatic device removal (requires API key mgmt). Current manual approach simpler for single-operator.

**`edc reprovision`:** Implemented in v0.2.

**Config file:** `~/.config/edcloud/config.yaml` for persistent defaults. Current env vars sufficient for now.
