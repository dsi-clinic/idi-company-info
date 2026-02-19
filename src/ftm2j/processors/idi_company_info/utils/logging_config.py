"""Logging configuration for IDI Company Information Pipeline."""

import logging

_CONFIGURED = False


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Configure logging (on first call) and return a logger for the given name.

    Call at the start of each script: logger = get_logger(__name__)
    Then use logger.info(), logger.warning(), etc. instead of logging.info().
    """
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            format="%(asctime)s,%(msecs)d %(module)s:%(lineno)d %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
            level=level,
        )
        _CONFIGURED = True
    return logging.getLogger(name)
