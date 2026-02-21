# Cleanup Workflow

**Purpose:** Keep Tailscale hostname consistent (`edcloud`), prevent orphaned volumes.

## Usage

### Destroy (cleanup runs by default)
```bash
edc destroy --confirm-instance-id i-xxx
# Auto-snapshots → destroys → Tailscale/volume cleanup runs automatically
```

To skip cleanup or snapshot individually:
```bash
edc destroy --confirm-instance-id i-xxx --skip-snapshot   # No pre-destroy snapshot
edc destroy --confirm-instance-id i-xxx --skip-cleanup    # No post-destroy cleanup
```

### Provision with cleanup
```bash
edc provision --cleanup
# Auto-snapshots existing instance (if any) → cleanup prompts → provisions
```

### Skip auto-snapshot on provision
```bash
edc provision --cleanup --skip-snapshot
```

## Default behavior on destroy

1. Auto-snapshot before destroy (skip with `--skip-snapshot`)
2. Terminate instance (root volume auto-deleted — `DeleteOnTermination=True`)
3. Detect offline Tailscale devices → show manual removal instructions
4. Detect orphaned managed volumes → delete root-tagged volumes automatically

**Tailscale cleanup:** Detection is automated (`edc tailscale reconcile` / `edc tailscale reconcile --dry-run`). Deletion requires the Tailscale admin web UI (https://login.tailscale.com/admin/machines). Delete offline `edcloud*` devices to prevent name incrementing.

**Volume cleanup:**
- Root volumes are deleted automatically at termination (`DeleteOnTermination=True`)
- Orphaned root-tagged volumes from older instances are deleted by cleanup
- State volumes are protected by default; use `--allow-delete-state-volume` to override

## Recommended workflow

```bash
edc destroy --confirm-instance-id i-xxx
# Clean up Tailscale devices if prompted (30sec manual step in admin UI)
edc provision
# State volume is reused automatically; data is preserved
```

**Result:** Instance named `edcloud` (no suffix), data preserved on state volume.

## Cost

Snapshots: ~$0.05/GB/month. Only the 30GB state volume is snapshotted (root is disposable). 3 snapshots × 30GB ≈ $1.50/month. Run `edc snapshot --prune --apply` after reprovisioning to stay within the keep-last-3 policy.

## Atomic alternative: edc reprovision

Instead of the manual destroy → provision workflow, use `edc reprovision` for an atomic snapshot → destroy → cleanup → provision cycle:

```bash
edc reprovision --confirm-instance-id i-xxx
```

This is equivalent to the recommended workflow but runs as a single command.

## Alternative: Ephemeral Tailscale keys

Generate ephemeral key at https://login.tailscale.com/admin/settings/keys (enable "Ephemeral" + "Reusable").

Devices auto-delete when offline → no manual cleanup needed → always get `edcloud` name.

Trade-off: Can't see offline devices in admin (for debugging).
