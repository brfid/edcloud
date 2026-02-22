# Tailscale Device Management

## Problem

Repeated reprovisioning can accumulate stale tailnet machine records, causing DNS label drift
(`edcloud`, `edcloud-2`, `edcloud-3`, ...).

## V2 default guardrails

The repo now applies two guardrails by default:

1. **Persistent node identity**
   - `cloud-init/user-data.yaml` bind-mounts `/var/lib/tailscale` to
     `/opt/edcloud/state/tailscale`.
   - Rebuilds reuse Tailscale state, reducing identity churn.

2. **Fail-fast conflict detection**
   - `edc provision` and `edc up` fail if duplicate/suffixed `edcloud` records are detected.
   - Override exists for break-glass use: `--allow-tailscale-name-conflicts`.

## Reconcile workflow

Preview conflicts:

```bash
edc tailscale reconcile --dry-run
```

If conflicts are reported:

1. Go to https://login.tailscale.com/admin/machines
2. Search `edcloud`
3. Delete stale offline records
4. Ensure active device resolves as `edcloud.tail...` (no `-N` suffix)
5. Re-run `edc tailscale reconcile --dry-run` until clean

## Notes on auth key type

Ephemeral keys can reduce stale machine buildup, but key type alone is not a complete fix.
The durable fix is identity persistence + preflight conflict guardrails.
