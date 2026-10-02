import logging
from collections.abc import Iterator

import pytest

from app.core.logging import DropQueryStrings, configure_logging


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def access_log() -> Iterator[logging.Logger]:
    logger = logging.getLogger("uvicorn.access")
    saved = (list(logger.filters), list(logger.handlers), logger.level, logger.propagate)
    disabled = logger.disabled
    # Alembic's fileConfig (integration tests) disables loggers that already exist.
    # uvicorn sets this logger up itself in production; here the test owns it.
    logger.disabled = False
    yield logger
    logger.filters, logger.handlers = saved[0], saved[1]
    logger.setLevel(saved[2])
    logger.propagate = saved[3]
    logger.disabled = disabled


def test_oauth_codes_never_reach_the_access_log(access_log: logging.Logger) -> None:
    configure_logging("test")
    configure_logging("test")  # configuring twice must not add a second filter
    assert sum(isinstance(f, DropQueryStrings) for f in access_log.filters) == 1

    collect = _Collect()
    access_log.handlers = [collect]
    access_log.propagate = False
    access_log.setLevel(logging.INFO)
    # Exactly how uvicorn logs a request.
    access_log.info(
        '%s - "%s %s HTTP/%s" %d',
        "13.235.132.178:0",
        "GET",
        "/api/v1/auth/github/callback?code=3ffed9836586f790bee9&state=abc",
        "1.1",
        302,
    )
    access_log.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:0", "GET", "/api/v1/health", "1.1", 200)

    assert collect.lines == [
        '13.235.132.178:0 - "GET /api/v1/auth/github/callback HTTP/1.1" 302',
        '1.2.3.4:0 - "GET /api/v1/health HTTP/1.1" 200',
    ]


def test_other_records_pass_through_untouched() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "plain %s", ("a?b",), None)
    assert DropQueryStrings().filter(record)
    assert record.getMessage() == "plain a?b"
