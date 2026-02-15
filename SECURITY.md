# Security Policy

## Threat Model

**edcloud** is designed for personal use: a single-user cloud lab accessible only via Tailscale. It is **not** designed for multi-tenant scenarios or public-facing services.

### Security Assumptions

1. **Tailscale-only access**: The EC2 security group has zero inbound rules. All access (SSH, Portainer, container consoles) is via Tailscale's encrypted mesh network.
2. **Single operator**: No IAM/RBAC within the instance — the ubuntu user has full Docker access.
3. **Trusted workloads**: Containers run with default Docker privileges. Do not run untrusted code.
4. **AWS credentials**: The CLI relies on your local AWS credentials. Use IAM roles with least-privilege policies.

### What edcloud Protects Against

- Public SSH exposure (port 22 is never open to 0.0.0.0/0)
- IMDS attacks (IMDSv2 required, hop limit = 1)
- Idle cost (auto-shutdown after 30 minutes of inactivity)
- Accidental public service exposure (no inbound security group rules)

### What edcloud Does NOT Protect Against

- Compromised Tailscale credentials (attacker gains full instance access)
- Malicious containers (no AppArmor/SELinux hardening by default)
- AWS account compromise (attacker can destroy/modify infrastructure)
- Physical access to devices in your tailnet

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |

This is a personal project without formal versioning. Security fixes are applied to `main`.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

If you discover a security issue, please report it privately:

1. **Email**: Send details to the repository owner (check git commit history for contact info)
2. **Scope**: Report vulnerabilities in the edcloud code, not in upstream dependencies (Docker, Tailscale, SIMH, etc.)

### What to Include

- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact
- Suggested fix (optional)

### Response Timeline

- **Initial acknowledgment**: Within 7 days
- **Fix timeline**: Depends on severity. Critical issues (credential exposure, remote code execution) will be prioritized.
- **Disclosure**: Coordinated disclosure after fix is merged to `main`.

## Security Best Practices for Users

1. **Rotate Tailscale auth keys**: Use ephemeral keys when possible.
2. **Enable AWS MFA**: Protect your AWS account with multi-factor authentication.
3. **Review Portainer access logs**: Check for unexpected container operations.
4. **Keep Docker updated**: The cloud-init script installs Docker CE. Periodically recreate the instance to get latest packages.
5. **Snapshot before experiments**: Use `edcloud snapshot` before running unfamiliar workloads.
6. **Monitor costs**: Use `edcloud status` and AWS Cost Explorer to catch runaway resources.

## Dependencies

edcloud relies on these third-party services:

- **AWS EC2**: Compute platform (security updates via Ubuntu's unattended-upgrades)
- **Tailscale**: Zero-trust network (audited by Cure53, SOC 2 Type II certified)
- **Docker**: Container runtime (official Ubuntu packages)
- **Portainer CE**: Container UI (community edition, self-hosted)

Vulnerabilities in these dependencies should be reported to their respective maintainers.

## Out of Scope

- "Security issues" that require physical access to your devices
- Social engineering attacks on your Tailscale/AWS accounts
- Denial-of-service via AWS API rate limits (boto3 has no retry limits configured)
- Issues with workloads you deploy (VAX/PDP-11 SIMH, etc.)

## License

This security policy follows the same license as the project (see [LICENSE](LICENSE)).
