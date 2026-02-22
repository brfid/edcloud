# Scripts

## setup-ssm-tokens.sh

Stores GitHub + Tailscale tokens in AWS SSM Parameter Store.

**Usage:**
```bash
./scripts/setup-ssm-tokens.sh
```

**Actions:**
1. Extracts GitHub token from `gh auth token`
2. Prompts for Tailscale auth key
3. Stores both as SecureString in SSM:
   - `/edcloud/github_token`
   - `/edcloud/tailscale_auth_key`

**Prerequisites:** AWS CLI, `gh` authenticated, Tailscale key from https://login.tailscale.com/admin/settings/keys

**Verification:**
```bash
aws ssm describe-parameters --filters "Key=Name,Values=/edcloud/"
```

**Cost:** Free (standard parameters, AWS-managed KMS).
