#!/bin/bash
# Setup SSM parameters for edcloud authentication tokens

set -euo pipefail

echo "==================================================================="
echo "edcloud SSM Token Setup"
echo "==================================================================="
echo

# Check AWS credentials
if ! aws sts get-caller-identity &>/dev/null; then
    echo "❌ AWS credentials not configured or expired"
    echo "Run: aws configure"
    exit 1
fi

echo "✅ AWS credentials verified"
echo "   Account: $(aws sts get-caller-identity --query Account --output text)"
echo "   Region: $(aws configure get region)"
echo

# ============================================================================
# GitHub Token
# ============================================================================
echo "--- GitHub Token ---"

if ! command -v gh &>/dev/null; then
    echo "⚠️  GitHub CLI (gh) not found - skipping GitHub token"
    GITHUB_TOKEN=""
else
    if ! gh auth status &>/dev/null; then
        echo "⚠️  GitHub CLI not authenticated - skipping GitHub token"
        echo "   Run: gh auth login"
        GITHUB_TOKEN=""
    else
        GITHUB_TOKEN=$(gh auth token)
        echo "✅ Found GitHub token from gh CLI"

        # Store in SSM
        aws ssm put-parameter \
            --name /edcloud/github_token \
            --description "GitHub personal access token for edcloud instance" \
            --type SecureString \
            --value "$GITHUB_TOKEN" \
            --overwrite \
            2>/dev/null && echo "✅ Stored in SSM: /edcloud/github_token" \
            || echo "⚠️  Failed to store GitHub token in SSM"
    fi
fi

echo

# ============================================================================
# Tailscale Auth Key
# ============================================================================
echo "--- Tailscale Auth Key ---"
echo
echo "⚠️  You need to generate a NEW auth key for provisioning edcloud instances."
echo "   Your local Tailscale device key won't work for new machines."
echo
echo "Steps:"
echo "1. Open: https://login.tailscale.com/admin/settings/keys"
echo "2. Click 'Generate auth key'"
echo "3. Recommended settings:"
echo "   - ✅ Reusable (so you can reprovision multiple times)"
echo "   - ⏱️  Expiration: 90 days (or longer)"
echo "   - 🏷️  Optional: Tag with 'tag:edcloud'"
echo "4. Copy the key (starts with 'tskey-auth-')"
echo

read -rp "Paste your Tailscale auth key (or press Enter to skip): " TAILSCALE_KEY

if [ -z "$TAILSCALE_KEY" ]; then
    echo "⚠️  Skipped Tailscale auth key"
else
    if [[ ! "$TAILSCALE_KEY" =~ ^tskey-auth- ]]; then
        echo "⚠️  Warning: Key doesn't look like a Tailscale auth key (should start with 'tskey-auth-')"
        read -rp "Continue anyway? (y/N): " confirm
        if [[ ! "$confirm" =~ ^[Yy] ]]; then
            echo "Skipped Tailscale auth key"
            TAILSCALE_KEY=""
        fi
    fi

    if [ -n "$TAILSCALE_KEY" ]; then
        aws ssm put-parameter \
            --name /edcloud/tailscale_auth_key \
            --description "Tailscale auth key for edcloud instance provisioning" \
            --type SecureString \
            --value "$TAILSCALE_KEY" \
            --overwrite \
            2>/dev/null && echo "✅ Stored in SSM: /edcloud/tailscale_auth_key" \
            || echo "❌ Failed to store Tailscale key in SSM"
    fi
fi

echo
echo "==================================================================="
echo "Summary - Stored SSM Parameters:"
echo "==================================================================="

aws ssm describe-parameters \
    --filters "Key=Name,Values=/edcloud/" \
    --query 'Parameters[].{Name:Name,Type:Type,LastModified:LastModifiedDate}' \
    --output table

echo
echo "==================================================================="
echo "Next Steps:"
echo "==================================================================="
echo
echo "1. Verify parameters are accessible:"
echo "   aws ssm get-parameter --name /edcloud/github_token --with-decryption --query 'Parameter.Value' --output text"
echo "   aws ssm get-parameter --name /edcloud/tailscale_auth_key --with-decryption --query 'Parameter.Value' --output text"
echo
echo "2. Provision your edcloud instance:"
echo "   edc provision --tailscale-auth-key-ssm-parameter /edcloud/tailscale_auth_key"
echo
echo "3. (Optional) Set default SSM parameter in ~/.config/edcloud/edc.env:"
echo "   echo 'TAILSCALE_AUTH_KEY_SSM_PARAMETER=/edcloud/tailscale_auth_key' >> ~/.config/edcloud/edc.env"  # pragma: allowlist secret
echo
echo "Done! 🎉"
