"""Tests for edcloud.backup_policy — mocked DLM calls."""

from unittest.mock import MagicMock, patch

from edcloud.backup_policy import disable_policy, ensure_policy, policy_status


class TestPolicyStatus:
    @patch("edcloud.backup_policy._dlm_client")
    def test_returns_absent_when_missing(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {"Policies": []}
        mock_dlm_client.return_value = mock_dlm

        result = policy_status()
        assert result["exists"] is False

    @patch("edcloud.backup_policy._dlm_client")
    def test_returns_policy_details_when_present(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {
            "Policies": [
                {"PolicyId": "policy-123", "Description": "edcloud-dlm-policy", "State": "ENABLED"}
            ]
        }
        mock_dlm.get_lifecycle_policy.return_value = {
            "Policy": {"PolicyDetails": {"Schedules": [{"Name": "daily-7"}]}}
        }
        mock_dlm_client.return_value = mock_dlm

        result = policy_status()
        assert result["exists"] is True
        assert result["policy_id"] == "policy-123"


class TestEnsurePolicy:
    @patch("edcloud.backup_policy._dlm_client")
    def test_creates_when_missing(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {"Policies": []}
        mock_dlm.create_lifecycle_policy.return_value = {"PolicyId": "policy-new"}
        mock_dlm_client.return_value = mock_dlm

        result = ensure_policy(
            execution_role_arn="arn:aws:iam::123:role/edcloud-dlm",
            daily_keep=7,
            weekly_keep=4,
            monthly_keep=2,
        )
        assert result["action"] == "created"
        assert result["policy_id"] == "policy-new"
        assert result["monthly_keep"] == 2

    @patch("edcloud.backup_policy._dlm_client")
    def test_updates_when_existing(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {
            "Policies": [
                {"PolicyId": "policy-123", "Description": "edcloud-dlm-policy", "State": "ENABLED"}
            ]
        }
        mock_dlm_client.return_value = mock_dlm

        result = ensure_policy(
            execution_role_arn="arn:aws:iam::123:role/edcloud-dlm",
            daily_keep=7,
            weekly_keep=4,
            monthly_keep=2,
        )
        assert result["action"] == "updated"
        mock_dlm.update_lifecycle_policy.assert_called_once()


class TestDisablePolicy:
    @patch("edcloud.backup_policy._dlm_client")
    def test_disable_when_present(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {
            "Policies": [{"PolicyId": "policy-123", "Description": "edcloud-dlm-policy"}]
        }
        mock_dlm_client.return_value = mock_dlm

        result = disable_policy()
        assert result["state"] == "DISABLED"
        mock_dlm.update_lifecycle_policy.assert_called_once_with(
            PolicyId="policy-123", State="DISABLED"
        )
