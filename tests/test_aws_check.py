"""Tests for edcloud.aws_check — AWS credential validation."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from edcloud.aws_check import check_aws_credentials, get_region


class TestCheckAwsCredentials:
    @patch("edcloud.aws_check.sts_client")
    def test_success(self, mock_sts_client: MagicMock) -> None:
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
        }
        mock_sts_client.return_value = mock_sts

        valid, message = check_aws_credentials()

        assert valid is True
        assert "123456789012" in message
        assert "test" in message

    @patch("edcloud.aws_check.sts_client")
    def test_no_credentials(self, mock_sts_client: MagicMock) -> None:
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = NoCredentialsError()
        mock_sts_client.return_value = mock_sts

        valid, message = check_aws_credentials()

        assert valid is False
        assert "No AWS credentials found" in message

    @patch("edcloud.aws_check.sts_client")
    def test_invalid_credentials(self, mock_sts_client: MagicMock) -> None:
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "InvalidClientTokenId"}}, "GetCallerIdentity"
        )
        mock_sts_client.return_value = mock_sts

        valid, message = check_aws_credentials()

        assert valid is False
        assert "invalid" in message.lower()


class TestGetRegion:
    @patch("edcloud.aws_check.aws_region", return_value="us-west-2")
    def test_returns_configured_region(self, _mock_region: MagicMock) -> None:

        result = get_region()

        assert result == "us-west-2"

    @patch("edcloud.aws_check.aws_region", return_value=None)
    def test_returns_none_when_no_region(self, _mock_region: MagicMock) -> None:

        result = get_region()

        assert result is None

    @patch("edcloud.aws_check.aws_region", side_effect=BotoCoreError())
    def test_returns_none_on_exception(self, _mock_region: MagicMock) -> None:

        result = get_region()

        assert result is None
