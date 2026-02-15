# Security policy

## Security model

edcloud is a single-operator personal lab, not a multi-tenant platform.

Core assumptions:

- Access is Tailscale-only.
- The EC2 security group has no inbound rules.
- The operator controls AWS and Tailscale identities.
- Workloads are trusted by the operator.

## What this project is designed to prevent

- Public SSH exposure
- Public exposure of Portainer or workload ports
- IMDSv1 usage (IMDSv2 is required)
- Avoidable idle spend (automatic idle shutdown)

## What this project does not try to prevent

- Compromise of your AWS or Tailscale account
- Malicious or vulnerable containers you choose to run
- Physical compromise of devices in your tailnet
- Multi-user isolation and tenant-level access control

## Required operator practices

- Keep runtime secrets in AWS SSM Parameter Store.
- Do not commit credentials, keys, or tokens to git.
- Use MFA on AWS and your identity provider.
- Rotate Tailscale auth keys and remove unused devices.
- Run restore drills and validate backup recovery.

## Vulnerability reporting

Do not open public issues for security vulnerabilities.

Report privately to the repository owner and include:

- A clear description
- Reproduction steps
- Expected impact
- Suggested remediation (optional)

Response targets:

- Initial acknowledgment: within 7 days
- Fix priority: based on impact and exploitability
- Public disclosure: after a fix is available

## Supported code line

Security fixes are applied on `main`.

## Dependency scope

Security issues in upstream dependencies (AWS, Ubuntu, Docker, Tailscale, Portainer) should also be reported to the relevant maintainers.
