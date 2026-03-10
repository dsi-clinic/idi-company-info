#!/usr/bin/env python3
"""
Unit tests for idi_company_info.common.logs
"""

import logging
from unittest.mock import ANY, MagicMock, patch

from idi_company_info.common.logs import get_logger


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_logger_with_expected_name(self):
        """Test that get_logger returns a logger with the given name."""
        with patch("idi_company_info.common.logs._configure_cloudwatch"):
            logger = get_logger("test_name")
            assert logger.name == "test_name"

    def test_returns_logger_with_default_level(self):
        """Test that get_logger returns a logger with INFO level by default."""
        with patch("idi_company_info.common.logs._configure_cloudwatch"):
            logger = get_logger("test_level_default")
            assert logger.level == logging.INFO

    def test_returns_logger_with_custom_level(self):
        """Test that get_logger accepts a custom level."""
        with patch("idi_company_info.common.logs._configure_cloudwatch"):
            logger = get_logger("test_level_custom", level=logging.DEBUG)
            assert logger.level == logging.DEBUG

    def test_attaches_stream_handler(self):
        """Test that get_logger attaches a StreamHandler to the logger."""
        with patch("idi_company_info.common.logs._configure_cloudwatch"):
            logger = get_logger("test_stream_handler")
            stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
            assert len(stream_handlers) >= 1
            assert stream_handlers[0].level == logging.INFO

    def test_stream_handler_has_formatter(self):
        """Test that the StreamHandler has a formatter configured."""
        with patch("idi_company_info.common.logs._configure_cloudwatch"):
            logger = get_logger("test_formatter")
            stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
            assert stream_handlers[0].formatter is not None
            assert "%(name)s" in stream_handlers[0].formatter._fmt

    def test_calls_configure_cloudwatch(self):
        """Test that get_logger calls _configure_cloudwatch with the logger and name."""
        with patch("idi_company_info.common.logs._configure_cloudwatch") as mock_configure:
            logger = get_logger("test_configure_call")
            mock_configure.assert_called_once_with(logger, "test_configure_call")


class TestConfigureCloudwatch:
    """Tests for _configure_cloudwatch behavior via get_logger."""

    @patch("idi_company_info.common.logs._configure_cloudwatch")
    def test_no_cloudwatch_handler_when_not_on_ec2(self, mock_configure):
        """CloudWatch is configured by _configure_cloudwatch; we test behavior via mock."""
        # When _configure_cloudwatch does nothing (no EC2), only StreamHandler is present
        mock_configure.side_effect = lambda logger, name: None
        logger = get_logger("test")
        cloudwatch_handlers = [
            h for h in logger.handlers if type(h).__name__ == "CloudWatchLogHandler"
        ]
        assert len(cloudwatch_handlers) == 0

    @patch("idi_company_info.common.logs.boto3.client")
    @patch("idi_company_info.common.logs.requests.get")
    @patch("idi_company_info.common.logs.requests.put")
    @patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler")
    def test_adds_cloudwatch_handler_with_instance_id_from_metadata(
        self, mock_cw_handler_class, mock_put, mock_get, mock_boto_client
    ):
        """Test that CloudWatch handler uses instance ID from EC2 metadata (IMDSv2) when available."""
        mock_token_resp = MagicMock()
        mock_token_resp.text = "test-token"
        mock_put.return_value = mock_token_resp

        mock_instance_resp = MagicMock()
        mock_instance_resp.text = "i-1234567890abcdef0"
        mock_get.return_value = mock_instance_resp

        mock_cw_handler = MagicMock()
        mock_cw_handler.level = logging.INFO
        mock_cw_handler_class.return_value = mock_cw_handler

        with patch.dict("os.environ", {"CLOUDWATCH_LOGS_ENABLED": "true"}):
            logger = get_logger("test_instance_id")

        mock_cw_handler_class.assert_called_once_with(
            log_group_name="idi-ftm2j",
            log_stream_name="/company-info/i-1234567890abcdef0",
            use_queues=False,
            boto3_client=ANY,
            log_group_retention_days=30,
        )
        assert mock_cw_handler in logger.handlers

    @patch("idi_company_info.common.logs.boto3.client")
    @patch("idi_company_info.common.logs.requests.get")
    @patch("idi_company_info.common.logs.requests.put")
    @patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler")
    def test_adds_cloudwatch_handler_when_env_enabled_uses_hostname_fallback(
        self, mock_cw_handler_class, mock_put, mock_get, mock_boto_client
    ):
        """Test that CloudWatch handler uses HOSTNAME when metadata is unreachable (e.g. Docker)."""
        mock_put.side_effect = Exception("Connection refused")

        mock_cw_handler = MagicMock()
        mock_cw_handler.level = logging.INFO
        mock_cw_handler_class.return_value = mock_cw_handler

        with patch.dict(
            "os.environ", {"CLOUDWATCH_LOGS_ENABLED": "true", "HOSTNAME": "docker-container-1"}
        ):
            logger = get_logger("test_env_enabled")

        mock_cw_handler_class.assert_called_once_with(
            log_group_name="idi-ftm2j",
            log_stream_name="/company-info/docker-container-1",
            use_queues=False,
            boto3_client=ANY,
            log_group_retention_days=30,
        )
        assert mock_cw_handler in logger.handlers

    @patch("idi_company_info.common.logs.boto3.client")
    @patch("idi_company_info.common.logs.requests.get")
    @patch("idi_company_info.common.logs.requests.put")
    @patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler")
    def test_adds_cloudwatch_handler_uses_instance_id_env_var(
        self, mock_cw_handler_class, mock_put, mock_get, mock_boto_client
    ):
        """Test that INSTANCE_ID env var takes precedence over metadata."""
        mock_cw_handler = MagicMock()
        mock_cw_handler.level = logging.INFO
        mock_cw_handler_class.return_value = mock_cw_handler

        with patch.dict(
            "os.environ",
            {"CLOUDWATCH_LOGS_ENABLED": "true", "INSTANCE_ID": "i-custom-from-env"},
        ):
            logger = get_logger("test_instance_env")

        mock_put.assert_not_called()
        mock_get.assert_not_called()
        mock_cw_handler_class.assert_called_once_with(
            log_group_name="idi-ftm2j",
            log_stream_name="/company-info/i-custom-from-env",
            use_queues=False,
            boto3_client=ANY,
            log_group_retention_days=30,
        )
        assert mock_cw_handler in logger.handlers
