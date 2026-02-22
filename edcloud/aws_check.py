"""AWS credentials validation."""

from __future__ import annotations

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError


def check_aws_credentials() -> tuple[bool, str]:
    """Verify AWS credentials are configured and valid.

    Returns (is_valid, message).
    """
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        account = identity.get("Account", "unknown")
        user_arn = identity.get("Arn", "unknown")
        return True, f"AWS credentials OK (Account: {account}, ARN: {user_arn})"
    except NoCredentialsError:
        return False, (
            "No AWS credentials found. Run 'aws configure' or set "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )
    except ClientError as exc:
        return False, f"AWS credentials invalid: {exc}"
    except (BotoCoreError, Exception) as exc:
        return False, f"AWS connection error: {exc}"


def get_region() -> str | None:
    """Get the configured AWS region."""
    try:
        session = boto3.session.Session()
        return session.region_name
    except Exception:
        return None
