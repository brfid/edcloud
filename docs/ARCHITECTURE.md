# Architecture

## System model

- **Topology:** One `t3a.small` EC2 instance running Ubuntu 24.04 by default.
- **Access:** Tailscale carries operator traffic. Newly created security groups have no inbound rules.
- **Ownership:** The `edcloud:managed=true` tag identifies resources; edcloud does not use a local state file.
- **Storage:** The 30 GiB root volume is disposable. The 30 GiB state volume persists data under `/opt/edcloud/state` and is reused during reprovisioning.
- **Secrets:** The instance role reads bootstrap secrets from `/edcloud/*` in AWS Systems Manager Parameter Store.
- **Bootstrap:** `cloud-init/user-data.yaml` defines the host baseline, persistent mounts, services, and repository sync.

The single-instance design favors low operating cost and simple recovery over high availability, horizontal scaling, or multi-user isolation.

## Package boundaries

- `cli.py` defines Click commands and user interaction. `lifecycle.py` contains shared command workflows.
- `ec2.py`, `iam.py`, and `security_group.py` manage the instance and its supporting AWS resources.
- `snapshot.py` manages the CLI snapshot queue and restore-to-volume drills. `backup_policy.py` manages the optional AWS Data Lifecycle Manager (DLM) policy.
- `aws_clients.py`, `aws_check.py`, `discovery.py`, and `resource_queries.py` contain shared AWS access and discovery helpers.
- `cleanup.py`, `resource_audit.py`, `ops_health.py`, and `permissions.py` provide operational safeguards and reporting.
- `tailscale.py` handles tailnet discovery and conflict guidance. `cline_sync.py` transfers Cline authentication files to the host.
- `config.py`, `types.py`, and `verify_catalog.py` define configuration, typed return contracts, and verification checks.
- `user_data.py` validates bootstrap inputs and renders the cloud-init template. `proc.py` provides the shared subprocess wrapper.

Only `cli.py` depends on Click. Library modules return values or accept callbacks instead of writing directly to the terminal.

## Resource discovery

edcloud queries AWS by tags and expects at most one active managed instance, one managed security group, and one available managed state volume. Duplicate resources violate the discovery invariant and raise `TagDriftError`.

Volumes also use `edcloud:volume-role=root` or `edcloud:volume-role=state`. Cleanup preserves state and unknown-role volumes by default and deletes them only after an explicit override.

New security groups are created without inbound rules. Discovery does not audit or remove rules from an existing tagged security group, so operators must preserve that invariant.

## Provisioning and reprovisioning

Provisioning performs these operations:

1. Check for an existing managed instance and resource drift.
2. Find or create the security group and instance profile.
3. Resolve the Ubuntu AMI and render cloud-init.
4. Reuse an available managed state volume, or create one only when `--allow-new-state-volume` is set.
5. Launch the instance, attach the state volume, and apply role tags.

Reprovisioning snapshots the current state volume by default, terminates the instance, cleans orphaned root volumes, and launches a replacement that must reuse the state volume. The workflow is not atomic: a launch failure can occur after termination, so the snapshot and state volume remain the recovery boundary.

## Persistent state

Cloud-init bind-mounts these paths from the state volume:

- `/home/ubuntu`
- `/var/lib/tailscale`
- `/opt/edcloud/compose`
- `/opt/edcloud/portainer-data`

Docker stores its data under `/opt/edcloud/state/docker`, and SSH host keys persist under `/opt/edcloud/state/ssh-host-keys`.

## Repository bootstrap

`InstanceConfig.dotfiles_repo` and `dotfiles_branch` control dotfiles sync. With the default `auto` setting, cloud-init resolves the authenticated GitHub user's dotfiles URL or falls back to the persisted checkout's origin. Cloud-init clones or updates the repository but does not run an installer or create links. Apply the dotfiles after provisioning by following that repository's instructions.

Cloud-init also performs best-effort sync for selected non-secret repositories and installs local wrappers for the `oldspeak` MCP service. Application code remains outside this infrastructure repository.

## Snapshots and recovery

CLI lifecycle triggers use `prune → snapshot → prune` to retain three snapshots in the managed pool. Manual snapshots remain until an explicit or later automatic prune places them beyond the limit. Snapshots normally cover the state-tagged volume; if volume-role tags are missing, the implementation falls back to all attached volumes.

DLM is optional. DLM and CLI snapshots use the same managed tag, so CLI pruning can remove DLM-created snapshots and does not preserve separate DLM tiers. Treat the mechanisms as alternatives unless their tagging or pruning behavior is changed.

`edc restore-drill` verifies that a snapshot can create a temporary EBS volume and can optionally attach it to the managed instance. It does not mount the filesystem or validate files.

## Non-goals

- Multi-region or fleet orchestration
- Multi-tenant isolation
- Public service exposure
- High availability
- Full infrastructure-as-code state management
