# edcloud Setup Guide

Complete first-time setup from zero to running instance.

## Prerequisites

- **AWS account** with credentials configured
- **Tailscale account** (free tier works)
- **Python 3.10+**
- **Git**

## 1. AWS Setup

### Create IAM user (or use existing)

Minimum required permissions:
```
ec2:RunInstances
ec2:DescribeInstances
ec2:StartInstances
ec2:StopInstances
ec2:TerminateInstances
ec2:CreateSecurityGroup
ec2:DescribeSecurityGroups
ec2:DeleteSecurityGroup
ec2:CreateTags
ec2:DescribeVolumes
ec2:CreateSnapshot
ec2:DescribeSnapshots
ec2:DescribeImages
ssm:GetParameter  # for AMI resolution
```

If you want managed policies, use: `AmazonEC2FullAccess` + `AmazonSSMReadOnlyAccess`.

### Configure credentials

```bash
aws configure
# AWS Access Key ID: ...
# AWS Secret Access Key: ...
# Default region: us-east-1  (or your preferred region)
# Default output format: json
```

Verify:
```bash
aws sts get-caller-identity
```

## 2. Tailscale Setup

### Generate an auth key

1. Go to https://login.tailscale.com/admin/settings/keys
2. Click **Generate auth key**
3. Settings:
   - ✅ Reusable (so you can reprovision without generating new keys)
   - ✅ Ephemeral (optional — instance removes itself from tailnet when terminated)
   - Tag: `tag:edcloud` (optional — for ACL management)
4. Copy the key (starts with `tskey-auth-...`)

### Set environment variable

```bash
export TAILSCALE_AUTH_KEY='tskey-auth-...'
```

Or add to your `~/.bashrc` / `~/.zshrc`:
```bash
# edcloud
export TAILSCALE_AUTH_KEY='tskey-auth-...'
```

**Security note**: Never commit this key to git. It's already in `.gitignore`.

## 3. Install edcloud

```bash
git clone <your-fork-or-repo>
cd edcloud
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify:
```bash
edcloud --version
edcloud --help
```

## 4. Provision your instance

```bash
edcloud provision
```

This takes ~3-5 minutes:
- Creates EC2 instance (t3a.medium by default)
- Creates security group (no inbound rules)
- Attaches 80GB gp3 EBS volume
- Runs cloud-init (installs Docker, Tailscale, Portainer)

Output will show:
```json
{
  "instance_id": "i-0abc123...",
  "security_group_id": "sg-0def456...",
  "public_ip": "54.x.y.z"
}
```

## 5. Wait for cloud-init to complete

Cloud-init installs Docker, Tailscale, and Portainer. Check progress:

```bash
edcloud status
```

You'll see:
```
Instance:  i-0abc123...
State:     running
Type:      t3a.medium
Public IP: 54.x.y.z
Tailscale: 100.64.x.y (edcloud)
Reachable: yes
```

When `Reachable: yes`, it's ready.

### Verify on the instance

```bash
edcloud ssh
# Now on the instance:
docker ps
cat /tmp/edcloud-ready
```

If you see the Portainer container running, you're good.

## 6. Access Portainer

From any device on your tailnet:
```
https://edcloud:9443
```

First-time setup:
1. Create admin password
2. Choose "Get Started" → local Docker environment
3. Done

## 7. Deploy a workload

### Example: Vintage computing lab

```bash
edcloud ssh
# On the instance:
cd /opt/edcloud/compose
docker compose -f vintage-lab.yml up -d
```

Or use Portainer:
1. Stacks → Add stack
2. Name: `vintage-lab`
3. Upload `compose/vintage-lab.yml` or paste its contents
4. Deploy

Access VAX console:
```bash
telnet edcloud 2323
```

## 8. Daily usage

**Start:**
```bash
edcloud up
```

**Check status:**
```bash
edcloud status
```

**SSH in:**
```bash
edcloud ssh
```

**Stop (manual):**
```bash
edcloud down
```

**Or let it auto-shutdown** after 30 minutes idle (no Tailscale SSH, low CPU).

## 9. Backup

Weekly snapshots:
```bash
edcloud snapshot
```

List snapshots:
```bash
edcloud snapshot --list
```

## 10. Cost management

At 4 hours/day:
- Compute: ~$4.51/mo
- Storage: $6.40/mo
- Snapshots: ~$0.40/mo
- **Total: ~$11.31/mo**

The instance auto-shuts-down when idle (no active SSH + low CPU for 30 minutes).

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
