"""Structured logging with structlog.

Production and CI emit one JSON object per line, which log platforms can search
by field (run_id, repo, pr...). Local development uses a coloured console renderer
because humans read those logs.
"""

import logging
import sys

import structlog

from app.core.config import AppEnv


def configure_logging(app_env: AppEnv) -> None:
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if app_env == "development"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
