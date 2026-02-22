# Architecture

## Module structure (current)

```text
edcloud/
├── cli.py              # Click command adapters (entrypoint)
├── lifecycle.py        # Shared lifecycle orchestration helpers for CLI
├── verify_catalog.py   # Declarative `edc verify` check catalog
├── resource_queries.py # Shared managed-resource query/filter helpers
├── ec2.py              # EC2 lifecycle core (provision/start/stop/status/destroy/resize)
├── snapshot.py         # Snapshot create/list/prune + auto pre-destroy snapshots
├── backup_policy.py    # AWS DLM backup policy management
├── cleanup.py          # Tailscale + orphaned volume cleanup workflow
├── tailscale.py        # Tailscale discovery/conflict/SSH helpers
├── iam.py              # IAM role/profile setup + teardown
├── resource_audit.py   # Drift + cost audit
├── aws_clients.py      # Shared boto3 session/client factories
├── aws_check.py        # Credential/region checks
├── discovery.py        # Shared EC2 instance discovery helpers
└── config.py           # Constants, tags, defaults, InstanceConfig
```

## Design principles

- **Thin command adapters:** `cli.py` should focus on options, user I/O, and delegation.
- **Centralized orchestration:** repeated lifecycle flows live in `lifecycle.py`.
- **Declarative checks:** verification checks live in `verify_catalog.py`, not inline command code.
- **Shared query primitives:** managed-resource filter/query composition lives in `resource_queries.py`.
- **Tag-based source of truth:** no local state file; AWS tags define ownership and discovery.

## Architecture decisions (ADR summary)

- **Single-instance topology:** one EC2 host (`t3a.small` default) optimizes for low-cost, low-ops personal use.
- **Python + boto3 over Terraform:** small resource graph and tag-based ownership make stateful IaC overhead unnecessary here.
- **Tailscale-only access:** zero inbound SG rules; access is identity-based over tailnet.
- **Durable state volume + disposable root:** host runtime is replaceable; durable data lives under `/opt/edcloud/state`.
- **AWS-native backup lifecycle:** DLM retention + restore drills instead of replication/failover complexity.
- **SSM-backed runtime secrets:** secrets stay out of git and host bootstrap payloads.
- **Cloud-init as baseline contract:** reproducible host/tooling baseline is codified in `cloud-init/user-data.yaml`.
- **CLI-first operations model:** commands must remain safe/repeatable from lightweight ARM/Linux operator nodes.

## Key runtime flows

### Destroy (default)

1. Confirm instance ID guardrail
2. Optional pre-destroy snapshot (enabled by default)
3. Destroy instance and clean IAM/security group state
4. Optional post-destroy cleanup workflow (enabled by default)

### Reprovision

1. Confirm instance ID guardrail
2. Optional pre-reprovision snapshot
3. Destroy current instance
4. Cleanup orphaned non-state volumes
5. Provision replacement instance with state-volume reuse requirement

### Verify

- `edc verify` iterates `VERIFY_CHECKS` from `verify_catalog.py` and executes each check over SSH.

## DRY consolidation implemented

- **Lifecycle guardrails/snapshot flow:** shared in `lifecycle.py` (`require_confirmed_instance_id`, `run_optional_auto_snapshot`, `maybe_run_cleanup`).
- **Managed volume query filters:** shared in `resource_queries.py` (`managed_volume_filters`, `list_managed_volumes`) and reused by `cleanup.py`/`ec2.py`.
- **Verification check catalog:** extracted into `verify_catalog.py` and consumed by `cli.verify_cmd`.

## Notes

- AWS DLM policy management is implemented in `backup_policy.py`.
- Root volume remains disposable; state volume is durable and role-tagged.
- Cloud-init runs `loginctl enable-linger ubuntu` so user systemd services start at boot without a login session. Personal services (e.g. `rclone-dropbox.service`) are stored in `~/.config/systemd/user/` on the state volume and therefore survive reprovision automatically. Templates for optional user services live in `templates/operator/systemd-user/`.

## Non-goals

- Multi-region orchestration
- Multi-tenant isolation model
- Public internet service exposure
- Fleet-scale infrastructure automation

## Revisit triggers

Revisit this architecture when you need multiple long-lived instances, shared state
across hosts, team-managed infrastructure workflows, or stronger drift/audit
requirements than tag-based discovery.
