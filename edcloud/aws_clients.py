"""Central boto3 session and client factories.

Modules use this testable surface instead of constructing clients directly.
"""

from __future__ import annotations

from typing import Any

import boto3


def aws_session() -> boto3.session.Session:
    """Return the default boto3 session."""
    return boto3.session.Session()


def aws_region() -> str | None:
    """Return the region from a default-configured boto3 session."""
    return aws_session().region_name


def aws_client(service_name: str) -> Any:
    """Return a boto3 client for ``service_name``."""
    return aws_session().client(service_name)  # type: ignore[call-overload]


def ec2_client() -> Any:
    """Return an EC2 client."""
    return aws_client("ec2")


def ssm_client() -> Any:
    """Return an SSM client."""
    return aws_client("ssm")


def sts_client() -> Any:
    """Return an STS client."""
    return aws_client("sts")


def iam_client() -> Any:
    """Return an IAM client."""
    return aws_client("iam")


def dlm_client() -> Any:
    """Return a DLM client."""
    return aws_client("dlm")
