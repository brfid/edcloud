# Architecture

## Module structure

```
edcloud/
├── cli.py          # Click commands (thin wrappers)
├── ec2.py          # EC2 lifecycle via boto3
├── snapshot.py     # EBS snapshot ops + auto-snapshot
├── tailscale.py    # Device discovery, SSH helpers
├── cleanup.py      # Cleanup orchestration
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

## Future enhancements

**Tailscale API:** Programmatic device removal (requires API key mgmt). Current manual approach simpler for single-operator.

**`edc reprovision`:** Implemented in v0.2.

**Config file:** `~/.config/edcloud/config.yaml` for persistent defaults. Current env vars sufficient for now.
