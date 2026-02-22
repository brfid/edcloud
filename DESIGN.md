# edcloud Design Decisions

Why edcloud is built the way it is — rationale and trade-offs.

## Context

Portfolio piece demonstrating practical cloud infrastructure for running x86_64 Linux workloads (initially vintage computing experiments via SIMH) in a cost-effective, secure, and maintainable way.

## Decision log

### 1. Single t3a.medium vs 2x t3a.micro

**Decision**: One t3a.medium instance running all workloads.

**Why**:
- Two micros (1GB each) creates cross-host networking complexity (Docker overlay, shared volumes via EFS, etc.)
- Portainer CE needs ~1.5-2GB RAM — won't run on a micro
- Single-host Docker networking is trivial (`docker network`, shared filesystems via bind mounts)
- Cost at 4hrs/day: 2× micro = $2.26/mo, 1× medium = $4.51/mo — the $2.25/mo savings isn't worth the operational complexity

**Trade-off**: Less cost-efficient at 24/7 usage, but target is 4hrs/day with auto-shutdown.

### 2. Python + boto3 vs Terraform vs CDK

**Decision**: Python CLI with boto3, no Terraform.

**Why**:
- Infrastructure is trivial: 1 instance, 1 SG, 1 volume. Terraform's value (drift detection, state management, dependency graphs) doesn't apply.
- Python is consistent with the rest of the codebase and allows better testing (pytest + mocks).
- "It's the standard" isn't sufficient reason to use Terraform for a 5-resource deployment.
- Existing CDK stack in brfid.github.io was overkill for this simpler setup.

**Portfolio justification**: Document the *decision process* — showing judgment about when IaC is warranted. A "migration story" (CLI → Terraform when complexity grows) is a better signal than blindly using Terraform from day one.

**Trade-off**: No state file, so resources are tracked by AWS tags (`edcloud:managed=true`). If tags are manually removed, the CLI loses visibility.

### 3. Portainer CE vs Coolify vs plain compose

**Decision**: Portainer CE.

**Why**:
- Coolify is designed for web apps (reverse proxy, TLS, git deploys). Workloads here are interactive emulators accessed over Tailscale, not HTTPS services.
- Portainer's web terminal (exec into containers) is genuinely useful for SIMH console access.
- 256MB RAM footprint vs Coolify's ~1.5GB.
- Industry recognition: hiring managers know Portainer.

**Alternative considered**: Dockge (~128MB, compose-file manager with UI). Lighter but less capable — no web terminal, no image management, smaller ecosystem.

**Trade-off**: Portainer is more heavyweight than raw `docker compose` but provides a "platform UX" without Coolify's overhead.

### 4. Tailscale-only networking (no public inbound ports)

**Decision**: Security group has zero inbound rules. All access via Tailscale.

**Why**:
- SSH exposure to 0.0.0.0/0 is a known attack vector.
- Tailscale MagicDNS provides stable hostnames (`edcloud`) even when public IP changes on start/stop.
- Services (Portainer, SIMH consoles) are only reachable from your tailnet.
- Simpler than VPN or bastion host setups.

**Trade-off**: Requires Tailscale on all accessing devices. Not viable for public-facing services (but that's not the goal here).

### 5. gp3 EBS only (no EFS)

**Decision**: 80GB gp3 EBS with `DeleteOnTermination=false`, weekly snapshots.

**Why**:
- EFS is $0.30/GB/mo (standard tier) vs gp3 at $0.08/GB/mo.
- Single instance = no need for shared storage.
- EBS survives stop/start; snapshots provide backup/recovery.

**When to use EFS**: Multi-instance setups where persistent shared state is needed.

**Trade-off**: No automatic replication. Rely on snapshots for backup.

### 6. Instance self-shutdown + manual start

**Decision**: No Pi scheduler for start. Manual `edcloud up`. Instance shuts itself down after 30 minutes idle.

**Why**:
- Unpredictable usage patterns — "warm and waiting at 5pm" scheduler assumes regularity.
- Idle-shutdown (systemd timer checking Tailscale SSH + CPU load) is more flexible.
- Manual start from anywhere via CLI is simple enough.

**Alternative considered**: Pi cron job to start at scheduled time + hard-stop safety net. Adds dependency on the Pi being up and reachable.

**Trade-off**: Must remember to `edcloud up`. But auto-shutdown prevents runaway costs from forgetting to stop.

### 7. Tag-based resource discovery (no local state)

**Decision**: All managed resources tagged with `edcloud:managed=true`. CLI queries AWS for them.

**Why**:
- Simpler than maintaining local state files.
- Enables multi-device usage (run CLI from laptop, Pi, etc. without syncing state).
- No risk of state file corruption or divergence.

**Constraint**: Don't manually remove the `edcloud:managed` tag or the CLI loses track.

**Trade-off**: Slightly more AWS API calls (describe-instances on every command). Acceptable for this scale.

### 8. Ubuntu 24.04 LTS

**Decision**: Latest Ubuntu LTS via SSM parameter lookup.

**Why**:
- 5 years of security updates (until 2029).
- Docker/Tailscale official repos support it.
- Cloud-init is well-tested on Ubuntu.

**Alternative considered**: Amazon Linux 2023. Less familiar development environment.

### 9. Automatic security updates enabled

**Decision**: `unattended-upgrades` installed in cloud-init.

**Why**:
- Instance may sit idle for days/weeks. Want security patches applied automatically.

**Trade-off**: Small risk of update breaking something (e.g., Docker). Mitigated by EBS snapshots before major changes.

### 10. No SSH key pair provisioned

**Decision**: No EC2 key pair. Tailscale provides SSH (`tailscale up --ssh`).

**Why**:
- Don't need to manage/protect private keys.
- Tailscale SSH uses your existing identity (SSO if configured).
- Fallback: AWS SSM Session Manager (if Tailscale fails).

**Trade-off**: Must have Tailscale working to access the instance. SSM Session Manager is available as emergency access.

### 11. Tested via pytest with mocked boto3

**Decision**: Test suite uses `pytest-mock` to mock boto3 calls, not live AWS integration tests.

**Why**:
- Fast (no network I/O).
- No AWS credentials needed for CI/development.
- Deterministic (no eventual-consistency issues).

**Coverage**: Tests verify logic, not live AWS behavior. Provision/start/stop need manual validation in a real AWS environment.

**Trade-off**: Doesn't catch AWS API changes or region-specific issues.

## Non-goals

Things explicitly **not** built (and why):

- **Multi-region support**: Single-region is simpler. Add if needed.
- **VPC creation**: Use default VPC. Custom VPC adds unnecessary complexity for one instance.
- **IAM role creation via CLI**: Assume user has AWS credentials configured with sufficient permissions.
- **Cloud-init status checking**: Could poll instance until `/tmp/edcloud-ready` exists, but `edcloud status` + manual check is sufficient.
- **Blue/green deploys**: Overkill for a personal lab instance.
- **Monitoring/alerting**: CloudWatch is available but not configured. This isn't a production service.

## Migration path (if outgrowing this design)

When complexity increases:
- **Multiple instances**: → Terraform (manage fleet state), or AWS CDK (if Python preference remains)
- **Custom VPC with subnets/NACLs**: → Terraform modules or CDK constructs
- **CI/CD for infrastructure**: → GitHub Actions calling Terraform/CDK
- **Production-grade monitoring**: → CloudWatch dashboards, SNS alerts
- **Team access**: → IAM roles with scoped permissions, Terraform Cloud/state locking

The current design is optimized for "single-user personal lab" — document the upgrade path, don't build it prematurely.
