# edcloud design decisions

This file records why edcloud is built as a small, single-instance system.

## Context

Goal: run x86_64 Linux workloads on AWS with low cost, low operational overhead, and strong default access control.

## Principles

- Prefer simple operating models over broad abstractions.
- Keep state explicit and discoverable from AWS, not from local files.
- Optimize for single-operator workflows.
- Treat host runtime as replaceable; preserve only explicit durable state.

## Decision log

### 1. Single instance deployment

Decision:
Use one `t3a.medium` instance for the lab.

Why:
Portainer and interactive workloads fit better on one host than split `t3a.micro` nodes.

Trade-off:
Slightly higher compute cost than multi-micro layouts, but lower operational complexity.

### 2. Python CLI with boto3 (no Terraform)

Decision:
Use a Python CLI (`edc`) and boto3 directly.

Why:
The resource graph is small (instance, security group, EBS, snapshots). Terraform state and module overhead are not necessary at this scale.

Trade-off:
No Terraform drift workflow. Resource ownership is enforced with tags.

### 3. Portainer CE for operator UX

Decision:
Use Portainer CE as the container management UI.

Why:
It provides practical day-to-day value (stack management, logs, exec) without introducing full PaaS behavior.

Trade-off:
Higher footprint than plain `docker compose`, but better operator ergonomics.

### 4. Tailscale-only networking

Decision:
Expose no inbound security group ports.

Why:
Access through tailnet identity and encrypted mesh avoids public SSH and public service endpoints.

Trade-off:
Every operator device needs Tailscale.

### 5. Storage model: root + durable state volume

Decision:
Use gp3 EBS volumes only:

- Root volume for host runtime
- Dedicated durable state volume mounted at `/opt/edcloud/state`

Why:
Single-instance workloads do not need EFS. This keeps cost and backup scope predictable.

Trade-off:
No shared filesystem for multi-instance scaling.

### 6. Snapshot policy over replication

Decision:
Protect data with periodic snapshots and restore drills.

Why:
The system is a personal lab; snapshot-based recovery is sufficient and cost-aware.

Trade-off:
Recovery depends on tested restore procedure, not instant failover.

### 7. Tag-based discovery (no local state file)

Decision:
Discover managed resources by tag (`edcloud:managed=true`).

Why:
The same command surface works from any operator node without syncing local state.

Trade-off:
Manual tag removal causes management drift and must be corrected.

### 8. Manual start + automatic idle shutdown

Decision:
Start manually with `edc up`; stop automatically after 30 minutes idle.

Why:
Usage is irregular. This model reduces cost without scheduler dependencies.

Trade-off:
The operator must start the instance when needed.

### 9. Baseline encoded in cloud-init

Decision:
Encode core host packages/settings in `cloud-init/user-data.yaml`.

Why:
Prevents undocumented snowflake drift and supports reproducible rebuilds.

Trade-off:
Operational discipline is required: persistent host changes must be codified.

### 10. Secrets via AWS SSM Parameter Store

Decision:
Source runtime secrets from SSM, not repository files.

Why:
Keeps credentials out of git history and aligns with AWS-native operator tooling.

Trade-off:
Requires IAM permissions and secret bootstrap workflow.

### 11. Lightweight operator nodes

Decision:
Keep control-plane dependencies lightweight enough for small ARM Linux systems.

Why:
Operations should not depend on a heavyweight workstation.

Trade-off:
Avoid adding heavy local tooling for normal workflows.

### 12. Test strategy: mocked AWS unit tests

Decision:
Use pytest with mocked boto3 calls.

Why:
Fast and deterministic local feedback without live AWS dependencies.

Trade-off:
Live AWS behavior still requires manual validation.

### 13. Persistent `ubuntu` home on durable state volume

Decision:
Bind-mount `/home/ubuntu` to `/opt/edcloud/state/home/ubuntu` during bootstrap.

Why:
User settings and local dev state survive reprovision when reusing the state volume.

Trade-off:
Requires careful migration/mount logic in cloud-init and stronger backup discipline for user data.

### 14. Package strategy: APT-first with Homebrew available

Decision:
Install baseline tools from Ubuntu APT and install Homebrew for optional package gaps.

Why:
APT keeps bootstrap predictable and fast; Homebrew remains available when newer or niche tools are needed.

Trade-off:
Two package ecosystems exist on the host, so baseline runbooks should stay APT-compatible by default.

## Non-goals

- Multi-region orchestration
- Multi-tenant access control
- Public service hosting
- Full production monitoring stack
- Automated infra pipelines for large fleets

## Revisit triggers

Revisit this design when any of the following become true:

- You manage multiple long-lived instances.
- You need shared state across instances.
- You need team-owned infrastructure workflows.
- You need auditable drift management beyond tag-based discovery.

At that point, move toward Terraform or CDK with explicit state and CI/CD.
