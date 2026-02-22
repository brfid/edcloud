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
            "Policy": {
                "PolicyDetails": {
                    "Schedules": [
                        {"Name": "daily"},
                        {"Name": "weekly"},
                        {"Name": "monthly"},
                        {"Name": "quarterly"},
                    ]
                }
            }
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
            daily_keep=1,
            weekly_keep=1,
            monthly_keep=1,
            quarterly_keep=1,
        )
        assert result["action"] == "created"
        assert result["policy_id"] == "policy-new"
        assert result["quarterly_keep"] == 1

    @patch("edcloud.backup_policy._dlm_client")
    def test_creates_includes_quarterly_schedule(self, mock_dlm_client):
        mock_dlm = MagicMock()
        mock_dlm.get_lifecycle_policies.return_value = {"Policies": []}
        mock_dlm.create_lifecycle_policy.return_value = {"PolicyId": "policy-new"}
        mock_dlm_client.return_value = mock_dlm

        ensure_policy(execution_role_arn="arn:aws:iam::123:role/edcloud-dlm")

        call_kwargs = mock_dlm.create_lifecycle_policy.call_args[1]
        schedule_names = [s["Name"] for s in call_kwargs["PolicyDetails"]["Schedules"]]
        assert "quarterly" in schedule_names
        assert len(schedule_names) == 4

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
            daily_keep=1,
            weekly_keep=1,
            monthly_keep=1,
            quarterly_keep=1,
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
