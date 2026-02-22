# Script migrations

The repository intentionally moved non-trivial operational shell scripts to
Python CLI commands for maintainability and consistency.

## Replacements

- `scripts/setup-ssm-tokens.sh` → `edc setup-ssm-tokens`
- `scripts/sync-cline-auth-to-ec2.sh` → `edc sync-cline-auth`

## `edc setup-ssm-tokens`

Stores GitHub + Tailscale tokens in AWS SSM Parameter Store.

```bash
edc setup-ssm-tokens
edc setup-ssm-tokens --github-token <GITHUB_TOKEN> --tailscale-auth-key <TAILSCALE_AUTH_KEY> --no-prompt
```

## `edc sync-cline-auth`

Syncs Cline ChatGPT Subscription OAuth auth from a browser-capable source
machine to a headless remote host (for example, edcloud EC2), with safe backup
before overwrite.

```bash
edc sync-cline-auth --remote ubuntu@edcloud
edc sync-cline-auth               # syncs secrets + globalState by default
edc sync-cline-auth --secrets-only
edc sync-cline-auth --remote-diagnostics
edc sync-cline-auth --dry-run
```
