# Security policy

## Security model

edcloud is a single-operator personal lab, not a multi-tenant platform.

This repository is preserved for historical reference and is not actively maintained. It has no supported release or security-fix commitment.

Core assumptions:

- Access is Tailscale-only.
- The EC2 security group has no inbound rules.
- The operator controls AWS and Tailscale identities.
- Workloads are trusted by the operator.

## What this project is designed to prevent

- Public SSH exposure
- Public exposure of Portainer or workload ports (Portainer binds to Tailscale interface only)
- IMDSv1 usage (IMDSv2 is required, hop limit set to 1)
- Credentials in user-data (auth keys fetched from SSM at boot)

## What this project does not try to prevent

- Compromise of your AWS or Tailscale account
- Malicious or vulnerable containers you choose to run
- Physical compromise of devices in your tailnet
- Multi-user isolation and tenant-level access control
- Docker socket exposure in Portainer (accepted risk for single-operator convenience)

## Required operator practices

- Keep bootstrap secrets in AWS Systems Manager Parameter Store.
- Keep materialized credentials and OAuth files in access-restricted, untracked files on the operator device or host.
- Do not commit credentials, keys, or tokens to git.
- Use MFA on AWS and your identity provider.
- Rotate Tailscale auth keys and remove unused devices.
- Monitor the CLI-managed snapshot queue and snapshot cost.
- Run restore-to-volume drills and validate files separately before relying on a backup.

## Secret and PII guards

Automated checks reduce the chance of committing secrets or personal data:

- `gitleaks` scans staged changes as a pre-commit hook and scans full history in CI (`.github/workflows/secret-scan.yml`). Rules and allowlist live in `.gitleaks.toml`, including a Tailscale auth-key pattern.
- `detect-secrets` runs as a pre-commit hook against `.secrets.baseline`.
- `.gitignore` excludes operator-local auth material: Tailscale keys, rclone and Cline OAuth files (`secrets.json`, `globalState.json`), `.env` / `.envrc`, and AWS CLI credential and output files.

Install the hooks with `pip install pre-commit && pre-commit install`. These guards are defense-in-depth, not a substitute for handling bootstrap and materialized secrets as described above.

## Reporting and support

Do not include credentials, tokens, personal data, or active resource identifiers in a public issue. Because this repository is unmaintained, reports might not receive a response or fix. Report vulnerabilities in AWS, Ubuntu, Docker, Tailscale, Portainer, or another dependency to that project's current maintainer.
