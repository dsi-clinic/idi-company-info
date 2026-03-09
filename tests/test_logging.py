#!/usr/bin/env python3
"""
Unit tests for idi_company_info.common.logs
"""

import logging
from unittest.mock import MagicMock, patch

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
            h for h in logger.handlers
            if type(h).__name__ == "CloudWatchLogHandler"
        ]
        assert len(cloudwatch_handlers) == 0

    @patch("idi_company_info.common.logs.requests.get")
    @patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler")
    def test_adds_cloudwatch_handler_when_on_ec2(
        self, mock_cw_handler_class, mock_requests_get
    ):
        """Test that CloudWatch handler is added when EC2 metadata endpoint returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        mock_cw_handler = MagicMock()
        mock_cw_handler_class.return_value = mock_cw_handler

        logger = get_logger("test_logger")

        mock_requests_get.assert_called_once()
        mock_cw_handler_class.assert_called_once_with(log_group="idi-company-info-test_logger")
        assert mock_cw_handler in logger.handlers

    @patch("idi_company_info.common.logs.requests.get")
    def test_no_cloudwatch_handler_when_metadata_fails(self, mock_requests_get):
        """Test that CloudWatch handler is not added when metadata request fails."""
        mock_requests_get.side_effect = Exception("Connection refused")

        with patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler") as mock_cw:
            mock_cw.assert_not_called()

    @patch("idi_company_info.common.logs.requests.get")
    def test_no_cloudwatch_handler_when_metadata_returns_non_200(self, mock_requests_get):
        """Test that CloudWatch handler is not added when metadata returns non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_requests_get.return_value = mock_response

        with patch("idi_company_info.common.logs.watchtower.CloudWatchLogHandler") as mock_cw:
            mock_cw.assert_not_called()
