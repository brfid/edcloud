# Cleanup Workflow

**Purpose:** Keep Tailscale hostname consistent (`edcloud`), prevent orphaned volumes.

## Usage

### Destroy with cleanup
```bash
edc destroy --confirm-instance-id i-xxx --cleanup
# Auto-snapshots → destroys → shows Tailscale/volume cleanup prompts
```

### Provision with cleanup
```bash
edc provision --cleanup
# Auto-snapshots existing instance (if any) → cleanup prompts → provisions
```

### Skip auto-snapshot
```bash
edc destroy --cleanup --skip-snapshot  # Faster, for testing
edc provision --cleanup --skip-snapshot
```

## Default behavior

**With `--cleanup`:**
1. Auto-snapshot before destructive operation (skip with `--skip-snapshot`)
2. Detect offline Tailscale devices → show manual removal instructions
3. Detect orphaned volumes → prompt for delete/keep/abort

**Tailscale cleanup:** Detection is automated (`edc tailscale reconcile` / `edc tailscale reconcile --dry-run`). Deletion requires the Tailscale admin web UI (https://login.tailscale.com/admin/machines). Delete offline `edcloud*` devices to prevent name incrementing.

**Volume cleanup:**
- Option 1: Delete all (fresh start)
- Option 2: Keep (reuses state volume = preserves data)
- Option 3: Abort

## Recommended workflow

```bash
# Snapshot happens automatically with --cleanup
edc destroy --confirm-instance-id i-xxx --cleanup
# Clean up Tailscale devices (30sec manual step)
edc provision --cleanup
# Choose option 2 to keep state volume
```

**Result:** Instance named `edcloud` (no suffix), data preserved on state volume.

## Cost

Snapshots: ~$0.05/GB/month. 36GB ≈ $1.80/month (default: 16GB root + 20GB state). Use `edc snapshot --prune` for retention management.

## Atomic alternative: edc reprovision

Instead of the four-step manual workflow above, use `edc reprovision` for an atomic snapshot → destroy → provision cycle:

```bash
edc reprovision --confirm-instance-id i-xxx
```

This is equivalent to the recommended workflow but runs as a single command.

## Alternative: Ephemeral Tailscale keys

Generate ephemeral key at https://login.tailscale.com/admin/settings/keys (enable "Ephemeral" + "Reusable").

Devices auto-delete when offline → no manual cleanup needed → always get `edcloud` name.

Trade-off: Can't see offline devices in admin (for debugging).
