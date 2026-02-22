# Migration from ArpanetProductionStack to edcloud

Guide for migrating from the existing two-instance CDK stack to the single-instance edcloud setup.

## Overview

**Old stack** (brfid.github.io/infra/cdk):
- 2x t3.micro instances (VAX + PDP-11, separate hosts)
- EFS shared logs
- S3 archive bucket
- Cost: ~$17.90/mo running 24/7

**New stack** (edcloud):
- 1x t3a.medium instance (both emulators on one host)
- gp3 EBS (no EFS)
- Optional snapshots
- Cost: ~$11.31/mo at 4hrs/day, auto-shutdown when idle

## Pre-migration checklist

- [ ] Install edcloud: `pip install -e '.[dev]'`
- [ ] Configure AWS credentials (same account/region)
- [ ] Set `TAILSCALE_AUTH_KEY` environment variable
- [ ] Verify Tailscale CLI is installed: `tailscale version`

## Step 1: Back up the old stack

### SSH to the VAX instance

```bash
cd ~/brfid.github.io
./aws-start.sh  # if stopped
./aws-status.sh  # get VAX IP
ssh ubuntu@<vax-ip>
```

### Tar the SIMH disk images

```bash
cd ~/brfid.github.io
tar czf /tmp/vax-data.tar.gz build/vax/
tar czf /tmp/pdp11-data.tar.gz build/pdp11/

# Copy to your laptop
exit
scp ubuntu@<vax-ip>:/tmp/vax-data.tar.gz ~/edcloud-migration/
scp ubuntu@<vax-ip>:/tmp/pdp11-data.tar.gz ~/edcloud-migration/
```

### Save logs (optional)

If you want to keep the EFS logs:
```bash
ssh ubuntu@<vax-ip>
cd /mnt/arpanet-logs
tar czf /tmp/old-logs.tar.gz vax/ pdp11/ shared/
exit
scp ubuntu@<vax-ip>:/tmp/old-logs.tar.gz ~/edcloud-migration/
```

### List EBS snapshots (optional)

If you have snapshots you want to keep:
```bash
aws ec2 describe-snapshots \
  --owner-ids self \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=ArpanetProductionStack" \
  --query 'Snapshots[*].[SnapshotId,VolumeSize,StartTime,Description]' \
  --output table
```

## Step 2: Provision edcloud

```bash
cd ~/edcloud
source .venv/bin/activate
edcloud provision
```

Wait for instance to reach `running` state and Tailscale to connect.

## Step 3: Restore disk images

### SSH to the new instance

```bash
edcloud ssh
# or: ssh ubuntu@edcloud  (via Tailscale)
```

### Create directories

```bash
sudo mkdir -p /opt/edcloud/data/{vax,pdp11}
sudo chown -R ubuntu:ubuntu /opt/edcloud/data
```

### Upload archives

From your laptop (new terminal):
```bash
cd ~/edcloud-migration
scp vax-data.tar.gz ubuntu@edcloud:/opt/edcloud/data/
scp pdp11-data.tar.gz ubuntu@edcloud:/opt/edcloud/data/
```

### Extract

Back on the edcloud instance:
```bash
cd /opt/edcloud/data
tar xzf vax-data.tar.gz
tar xzf pdp11-data.tar.gz
ls -lh vax/ pdp11/
```

## Step 4: Update compose file

The vintage-lab.yml compose file needs to mount the restored data.

On the edcloud instance:
```bash
cd /opt/edcloud/compose
nano vintage-lab.yml
```

Update the `vax` service volumes from:
```yaml
    volumes:
      - vax-data:/machines/data
```

To:
```yaml
    volumes:
      - /opt/edcloud/data/vax:/machines/data
```

Do the same for `pdp11`:
```yaml
    volumes:
      - /opt/edcloud/data/pdp11:/machines/data
```

Save and exit.

## Step 5: Start the vintage lab

```bash
docker compose -f vintage-lab.yml up -d
docker ps
```

You should see both `vax-host` and `pdp11-host` running.

### Test console access

From your laptop:
```bash
telnet edcloud 2323   # VAX
telnet edcloud 2327   # PDP-11
```

## Step 6: Verify everything works

- [ ] VAX boots to BSD prompt
- [ ] PDP-11 boots to BSD prompt
- [ ] Portainer accessible at https://edcloud:9443
- [ ] Idle shutdown works (wait 30 min after last SSH, confirm instance stops)

## Step 7: Tear down the old stack

**Only after confirming the new stack works.**

### Stop old instances

```bash
cd ~/brfid.github.io
./aws-stop.sh
```

### Destroy the CDK stack

```bash
cd ~/brfid.github.io/infra/cdk
source ../../.venv/bin/activate  # brfid.github.io venv
cdk destroy -a "python3 app_production.py" ArpanetProductionStack
```

This will:
- Terminate both instances
- Delete security groups
- **Retain** EFS and S3 (removal_policy=RETAIN)

### Clean up retained resources (optional)

If you don't need the old EFS:
```bash
# List file systems
aws efs describe-file-systems \
  --query 'FileSystems[?Name==`ArpanetLogs`].[FileSystemId,SizeInBytes.Value]' \
  --output table

# Delete (replace fs-XXX with actual ID)
aws efs delete-file-system --file-system-id fs-XXX
```

If you don't need the old S3 bucket:
```bash
# Empty bucket first
aws s3 rm s3://arpanet-logs-972626128180/ --recursive

# Delete bucket
aws s3 rb s3://arpanet-logs-972626128180/
```

### Clean up orphaned EBS volumes

The old instances had EBS volumes with `DeleteOnTermination=false`. List them:
```bash
aws ec2 describe-volumes \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=ArpanetProductionStack" \
  --query 'Volumes[*].[VolumeId,Size,State]' \
  --output table
```

Delete if not needed:
```bash
aws ec2 delete-volume --volume-id vol-XXX
```

## Cost comparison

| Item | Old stack (24/7) | edcloud (4hrs/day) |
|------|------------------|-------------------|
| Compute | $15.00/mo | $4.51/mo |
| Storage | $2.01/mo (EFS+S3+EBS) | $6.40/mo (EBS) |
| Snapshots | $0 | $0.40/mo |
| **Total** | **$17.01/mo** | **$11.31/mo** |
| Savings | — | **34% cheaper** |

Plus: edcloud auto-shuts-down when unused, so actual compute cost may be lower.

## Rollback plan

If something goes wrong, the old stack is just stopped (not destroyed until step 7).

Restart it:
```bash
cd ~/brfid.github.io
./aws-start.sh
```

Your disk images are preserved in the migration directory at `~/edcloud-migration/`.
