# IAM Instance Role (Optional)

**Purpose:** Auto-configure GitHub CLI from SSM on provision.

**Without IAM role:** GitHub CLI requires manual `gh auth login` after SSH.

**With IAM role:** GitHub CLI auto-authenticates during cloud-init using token from `/edcloud/github_token` SSM parameter.

## Setup

### Create role + policy

```bash
# Trust policy
cat > trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

# Create role
aws iam create-role --role-name edcloud-instance-role \
  --assume-role-policy-document file://trust-policy.json

# SSM read policy
cat > ssm-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ssm:GetParameter", "ssm:GetParameters"],
    "Resource": ["arn:aws:ssm:*:*:parameter/edcloud/*"]
  }]
}
EOF

# Attach policy
aws iam put-role-policy --role-name edcloud-instance-role \
  --policy-name edcloud-ssm-read --policy-document file://ssm-policy.json

# Create instance profile
aws iam create-instance-profile --instance-profile-name edcloud-instance-profile
aws iam add-role-to-instance-profile --instance-profile-name edcloud-instance-profile \
  --role-name edcloud-instance-role
```

### Attach to instance

**Option A: After provision**
```bash
INSTANCE_ID=$(edc status --json | jq -r '.instance_id')
aws ec2 associate-iam-instance-profile --instance-id $INSTANCE_ID \
  --iam-instance-profile Name=edcloud-instance-profile

# Reboot or wait for next provision
edc down && edc up
```

**Option B: Modify provision code** (requires code change)
Add `IamInstanceProfile={'Name': 'edcloud-instance-profile'}` to `run_instances()` in `ec2.py`.

### Verify

```bash
edc ssh
curl -s http://169.254.169.254/latest/meta-data/iam/info  # Should show role
gh auth status  # Should show authenticated
```

## Security

**Least privilege:** Role only reads `/edcloud/*` SSM parameters. No write, no other AWS services.

**Single-operator:** Anyone with SSH can read SSM params. Not designed for multi-tenant.

## Cleanup

```bash
INSTANCE_ID=$(edc status --json | jq -r '.instance_id')
ASSOC_ID=$(aws ec2 describe-iam-instance-profile-associations \
  --filters "Name=instance-id,Values=$INSTANCE_ID" \
  --query 'IamInstanceProfileAssociations[0].AssociationId' --output text)

aws ec2 disassociate-iam-instance-profile --association-id $ASSOC_ID
aws iam remove-role-from-instance-profile --instance-profile-name edcloud-instance-profile \
  --role-name edcloud-instance-role
aws iam delete-instance-profile --instance-profile-name edcloud-instance-profile
aws iam delete-role-policy --role-name edcloud-instance-role --policy-name edcloud-ssm-read
aws iam delete-role --role-name edcloud-instance-role
```

## Alternative: Manual auth

Simpler for single-operator use case:
```bash
edc ssh
gh auth login  # Interactive
# Or use token:
aws ssm get-parameter --name /edcloud/github_token --with-decryption --query 'Parameter.Value' --output text | gh auth login --with-token
```
