"""Provides loggers for use across the application."""

# Standard library imports
import logging
import os

import requests

# Third party imports
import watchtower

EC2_METADATA_ENDPOINT = "http://169.254.169.254/latest/meta-data/instance-id"
HEADERS = {"User-Agent": "idi-company-info/1.0"}

_configured_loggers: set[str] = set()


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Creates a logger with the given name and level.

    Attaches a stream handler that prints logs in a
    standard format to the console.

    Args:
        name: The logger name.

        level: The initial level. Defaults to 20 ("INFO").

    Returns:
        The logger.
    """
    # Check if logger has already been configured
    if name in _configured_loggers:
        return logging.getLogger(name)

    # Create logger and set level
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # Prevent log messages from being propagated to the root logger

    # Create console handler and set level
    ch = logging.StreamHandler()
    ch.setLevel(level)

    # Create formatter and add to handler
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    formatter = logging.Formatter(format)
    ch.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(ch)

    # Configure CloudWatch logging if executing on AWS EC2 instance
    _configure_cloudwatch(logger, name)

    # Add logger to set of configured loggers
    _configured_loggers.add(name)

    return logger


def _get_instance_id() -> str:
    """Get EC2 instance ID from metadata, or hostname as fallback."""
    try:
        r = requests.get(EC2_METADATA_ENDPOINT, headers=HEADERS, timeout=2)
        if r.status_code == 200:
            return r.text.strip()
    except Exception:
        pass
    return os.environ.get("HOSTNAME", "local")


def _configure_cloudwatch(logger: logging.Logger, name: str) -> None:
    """Configures the logger to send logs to CloudWatch if executing in AWS.

    Enables CloudWatch when:
    - EC2 metadata endpoint is reachable, or
    - CLOUDWATCH_LOGS_ENABLED=true (e.g. when running in Docker on EC2).

    Args:
        logger: The logger to configure.
        name: The name of the logger.
    """
    # Check EC2 metadata
    try:
        r = requests.get(EC2_METADATA_ENDPOINT, headers=HEADERS, timeout=2)
        is_ec2 = r.status_code == 200
    except Exception:
        is_ec2 = False

    # Also enable when explicitly requested (e.g. Docker on EC2 where metadata may be unreachable)
    env_enabled = os.environ.get("CLOUDWATCH_LOGS_ENABLED", "").lower() in ("true", "1", "yes")

    if not (is_ec2 or env_enabled):
        return

    log_group_name = f"idi-company-info-{name.lower()}"
    instance_id = _get_instance_id()
    log_stream_name = f"{instance_id}/{name}/{os.getpid()}"

    handler = watchtower.CloudWatchLogHandler(
        log_group_name=log_group_name,
        log_stream_name=log_stream_name,
        use_queues=False,
    )
    logger.addHandler(handler)
    logger.info(
        "CloudWatch logging enabled: log_group=%s log_stream=%s",
        log_group_name,
        log_stream_name,
    )
