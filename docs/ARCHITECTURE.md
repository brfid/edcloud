# Architecture

## Module structure

```
edcloud/
├── cli.py          # Click commands (thin wrappers)
├── ec2.py          # EC2 lifecycle via boto3
├── snapshot.py     # EBS snapshot ops + auto-snapshot
├── tailscale.py    # Device discovery, SSH helpers
├── cleanup.py      # Cleanup orchestration (NEW)
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

run_cleanup_workflow(phase: str, skip_snapshot: bool, interactive: bool) -> bool
    # Orchestrates full cleanup (Tailscale + volumes)
    # Phases: "pre-provision" or "post-destroy"
```

## snapshot.py additions

```python
auto_snapshot_before_destroy() -> list[str]
    # Auto-snapshot with timestamped description
    # Returns [] if no instance exists (safe for first provision)
    # Used by --cleanup (default on, opt-out with --skip-snapshot)
```

## Default behaviors

**Auto-snapshot:** ON by default with `--cleanup`. Opt-out: `--skip-snapshot`.

**Rationale:**
- Snapshots cheap (~$2-5/month)
- Prevents data loss
- Industry standard (AWS RDS auto-snapshots)

**Volume cleanup modes:**
- Interactive: Prompt with options (default)
- Delete: Auto-delete all (for testing)
- Keep: Skip deletion (reuse state volume)

## Data flow

### destroy --cleanup
```
Auto-snapshot → Destroy → Tailscale cleanup prompt → Volume cleanup prompt
```

### provision --cleanup
```
Auto-snapshot (if instance exists) → Tailscale cleanup prompt → Volume cleanup → Provision
```

## Code metrics

| Metric | Before | After |
|--------|--------|-------|
| CLI lines | 650 | 550 |
| Duplication | ~110 lines | 0 |
| Modules | 6 | 7 |
| Tests passing | 42 | 42 |

## Testing gaps

**Current:** Unit tests for tailscale, snapshot, ec2, config.

**Missing:**
- `cleanup.py` unit tests
- Integration tests for --cleanup workflow
- End-to-end provision/destroy cycles

**Add:**
```python
# tests/test_cleanup.py
def test_cleanup_tailscale_devices()
def test_cleanup_orphaned_volumes_delete_mode()
def test_run_cleanup_workflow()
```

## Future enhancements

**Tailscale API:** Programmatic device removal (requires API key mgmt). Current manual approach simpler for single-operator.

**Reprovision command:** `edc reprovision` = snapshot + destroy --cleanup + provision --cleanup. Atomic operation, less flexible.

**Config file:** `~/.config/edcloud/config.yaml` for persistent defaults. Current env vars sufficient for now.
