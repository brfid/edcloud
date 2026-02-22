"""Tests for edcloud.iam — mocked IAM calls."""

from unittest.mock import MagicMock, patch

from edcloud.config import INSTANCE_PROFILE_NAME, INSTANCE_ROLE_NAME, MANAGER_TAG_KEY
from edcloud.iam import delete_instance_profile, ensure_instance_profile, find_instance_profile


class TestFindInstanceProfile:
    @patch("edcloud.iam._iam_client")
    def test_returns_arn_when_exists(self, mock_iam_client):
        mock_client = MagicMock()
        mock_client.get_instance_profile.return_value = {
            "InstanceProfile": {"Arn": "arn:aws:iam::123456789012:instance-profile/edcloud"}
        }
        mock_iam_client.return_value = mock_client

        result = find_instance_profile()
        assert result == "arn:aws:iam::123456789012:instance-profile/edcloud"

    @patch("edcloud.iam._iam_client")
    def test_returns_none_when_not_exists(self, mock_iam_client):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.get_instance_profile.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "GetInstanceProfile"
        )
        mock_iam_client.return_value = mock_client

        result = find_instance_profile()
        assert result is None


class TestEnsureInstanceProfile:
    @patch("edcloud.iam._iam_client")
    @patch("edcloud.iam._ssm_resource_arn", return_value="arn:aws:ssm:*:123:parameter/edcloud/*")
    def test_creates_role_and_profile_when_missing(self, mock_ssm_arn, mock_iam_client):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()

        # Role doesn't exist initially
        mock_client.get_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "GetRole"
        )

        # Instance profile doesn't exist initially
        mock_client.get_instance_profile.side_effect = [
            ClientError({"Error": {"Code": "NoSuchEntity"}}, "GetInstanceProfile"),
            {
                "InstanceProfile": {
                    "Arn": "arn:aws:iam::123:instance-profile/edcloud",
                    "Roles": [],
                }
            },
        ]

        mock_client.create_instance_profile.return_value = {
            "InstanceProfile": {"Arn": "arn:aws:iam::123:instance-profile/edcloud"}
        }

        mock_iam_client.return_value = mock_client

        tags = {MANAGER_TAG_KEY: "true", "Name": "edcloud"}
        result = ensure_instance_profile(tags)

        assert result == "arn:aws:iam::123:instance-profile/edcloud"
        mock_client.create_role.assert_called_once()
        mock_client.put_role_policy.assert_called_once()
        mock_client.create_instance_profile.assert_called_once()

    @patch("edcloud.iam._iam_client")
    @patch("edcloud.iam._ssm_resource_arn", return_value="arn:aws:ssm:*:123:parameter/edcloud/*")
    def test_idempotent_when_already_exists(self, mock_ssm_arn, mock_iam_client):
        mock_client = MagicMock()

        # Role exists
        mock_client.get_role.return_value = {"Role": {"RoleName": INSTANCE_ROLE_NAME}}

        # Profile exists with role already attached
        mock_client.get_instance_profile.return_value = {
            "InstanceProfile": {
                "Arn": "arn:aws:iam::123:instance-profile/edcloud",
                "Roles": [{"RoleName": INSTANCE_ROLE_NAME}],
            }
        }

        mock_iam_client.return_value = mock_client

        tags = {MANAGER_TAG_KEY: "true"}
        result = ensure_instance_profile(tags)

        assert result == "arn:aws:iam::123:instance-profile/edcloud"
        # Should not create new resources
        mock_client.create_role.assert_not_called()
        mock_client.create_instance_profile.assert_not_called()
        # Should still update policy (idempotent)
        mock_client.put_role_policy.assert_called_once()

    @patch("edcloud.iam._iam_client")
    @patch("edcloud.iam._ssm_resource_arn", return_value="arn:aws:ssm:*:123:parameter/edcloud/*")
    def test_adds_role_to_profile_if_missing(self, mock_ssm_arn, mock_iam_client):
        mock_client = MagicMock()

        # Role exists
        mock_client.get_role.return_value = {"Role": {"RoleName": INSTANCE_ROLE_NAME}}

        # Profile exists but role not attached
        mock_client.get_instance_profile.return_value = {
            "InstanceProfile": {
                "Arn": "arn:aws:iam::123:instance-profile/edcloud",
                "Roles": [],
            }
        }

        mock_iam_client.return_value = mock_client

        tags = {MANAGER_TAG_KEY: "true"}
        ensure_instance_profile(tags)

        mock_client.add_role_to_instance_profile.assert_called_once_with(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            RoleName=INSTANCE_ROLE_NAME,
        )


class TestDeleteInstanceProfile:
    @patch("edcloud.iam._iam_client")
    def test_cleans_up_profile_and_role(self, mock_iam_client):
        mock_client = MagicMock()
        mock_client.list_role_policies.return_value = {"PolicyNames": ["edcloud-ssm-read"]}

        mock_iam_client.return_value = mock_client

        delete_instance_profile()

        mock_client.remove_role_from_instance_profile.assert_called_once()
        mock_client.delete_instance_profile.assert_called_once()
        mock_client.delete_role_policy.assert_called_once()
        mock_client.delete_role.assert_called_once()

    @patch("edcloud.iam._iam_client")
    def test_handles_missing_resources_gracefully(self, mock_iam_client):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.remove_role_from_instance_profile.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "RemoveRoleFromInstanceProfile"
        )
        mock_client.delete_instance_profile.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "DeleteInstanceProfile"
        )
        mock_client.list_role_policies.return_value = {"PolicyNames": []}
        mock_client.delete_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "DeleteRole"
        )

        mock_iam_client.return_value = mock_client

        # Should not raise
        delete_instance_profile()


class TestPolicyContent:
    @patch("edcloud.iam._ssm_resource_arn", return_value="arn:aws:ssm:*:123:parameter/edcloud/*")
    def test_ssm_read_policy_structure(self, mock_ssm_arn):
        from edcloud.iam import _ssm_read_policy

        policy = _ssm_read_policy()

        assert policy["Version"] == "2012-10-17"
        assert len(policy["Statement"]) == 1
        stmt = policy["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Action"] == "ssm:GetParameter"
        assert stmt["Resource"] == "arn:aws:ssm:*:123:parameter/edcloud/*"

    def test_trust_policy_structure(self):
        from edcloud.iam import _trust_policy

        policy = _trust_policy()

        assert policy["Version"] == "2012-10-17"
        assert len(policy["Statement"]) == 1
        stmt = policy["Statement"][0]
        assert stmt["Effect"] == "Allow"
        assert stmt["Principal"]["Service"] == "ec2.amazonaws.com"
        assert stmt["Action"] == "sts:AssumeRole"
