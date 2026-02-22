"""Tests for edcloud.permissions."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from edcloud.permissions import (
    _policy_source_arn,
    policy_document,
    required_actions,
    verify_required_actions,
)


def test_required_actions_defaults_to_union_of_all_profiles():
    actions = required_actions(())
    assert "sts:GetCallerIdentity" in actions
    assert "ec2:RunInstances" in actions
    assert "dlm:UpdateLifecyclePolicy" in actions


def test_policy_document_scopes_ssm_parameter_path():
    policy = policy_document(("secrets",))
    statements = policy["Statement"]
    ssm_path_stmt = next(s for s in statements if s["Sid"] == "EdcloudSsmParameterPath")
    assert ssm_path_stmt["Resource"] == "arn:aws:ssm:*:*:parameter/edcloud/*"
    assert "ssm:GetParameter" in ssm_path_stmt["Action"]


def test_policy_source_arn_maps_assumed_role_to_iam_role():
    caller = "arn:aws:sts::123456789012:assumed-role/AdminRole/session-abc"
    assert _policy_source_arn(caller) == "arn:aws:iam::123456789012:role/AdminRole"


@patch("edcloud.permissions.iam_client")
@patch("edcloud.permissions.sts_client")
def test_verify_required_actions_reports_missing_actions(mock_sts_client, mock_iam_client):
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/test-user"}
    mock_sts_client.return_value = mock_sts

    mock_iam = MagicMock()
    mock_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "ec2:RunInstances", "EvalDecision": "allowed"},
            {"EvalActionName": "iam:PassRole", "EvalDecision": "explicitDeny"},
        ]
    }
    mock_iam_client.return_value = mock_iam

    result = verify_required_actions(["ec2:RunInstances", "iam:PassRole"])
    assert result.ok is False
    assert result.missing_actions == ("iam:PassRole",)


@patch("edcloud.permissions.iam_client")
@patch("edcloud.permissions.sts_client")
def test_verify_required_actions_reports_simulation_error(mock_sts_client, mock_iam_client):
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/test-user"}
    mock_sts_client.return_value = mock_sts

    mock_iam = MagicMock()
    mock_iam.simulate_principal_policy.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "SimulatePrincipalPolicy",
    )
    mock_iam_client.return_value = mock_iam

    result = verify_required_actions(["ec2:DescribeInstances"])
    assert result.ok is False
    assert result.missing_actions == ()
    assert "Could not verify permissions via IAM simulation" in result.detail
