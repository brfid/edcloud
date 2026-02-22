"""Shared boto3 session/client factories.

Centralizes AWS client/resource creation so modules use a single, testable
surface instead of calling ``boto3.client(...)`` directly everywhere.
"""

from __future__ import annotations

from typing import Any

import boto3


def aws_session() -> boto3.session.Session:
    """Return the default boto3 session."""
    return boto3.session.Session()


def aws_region() -> str | None:
    """Return configured AWS region from the shared session."""
    return aws_session().region_name


def aws_client(service_name: str) -> Any:
    """Return a boto3 client for ``service_name``."""
    return aws_session().client(service_name)


def aws_resource(service_name: str) -> Any:
    """Return a boto3 resource for ``service_name``."""
    return aws_session().resource(service_name)


def ec2_client() -> Any:
    return aws_client("ec2")


def ec2_resource() -> Any:
    return aws_resource("ec2")


def ssm_client() -> Any:
    return aws_client("ssm")


def sts_client() -> Any:
    return aws_client("sts")


def iam_client() -> Any:
    return aws_client("iam")


def dlm_client() -> Any:
    return aws_client("dlm")
