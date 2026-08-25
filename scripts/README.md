# Script replacements

The project moved its operational shell workflows into the Python CLI. The `scripts/` directory remains only to document those replacements.

- `scripts/setup-ssm-tokens.sh` became `edc setup-ssm-tokens`.
- `scripts/sync-cline-auth-to-ec2.sh` became `edc sync-cline-auth`.

## Store SSM tokens

```bash
edc setup-ssm-tokens
edc setup-ssm-tokens --github-token '<GITHUB_TOKEN>' --tailscale-auth-key '<TAILSCALE_AUTH_KEY>' --no-prompt
```

## Sync Cline authentication

`edc sync-cline-auth` copies Cline subscription OAuth files from a browser-capable machine to a headless host. It backs up remote files before replacement.

```bash
edc sync-cline-auth --remote ubuntu@edcloud
edc sync-cline-auth --secrets-only
edc sync-cline-auth --remote-diagnostics
edc sync-cline-auth --dry-run
```

## Relink dotfiles

There is no `setup-dotfiles.sh`. On a running host, follow `AGENTS.md` and `~/src/dotfiles/README.md`; create only links whose targets exist.
