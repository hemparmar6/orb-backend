"""Module 10 — logging & in-memory log buffer tests."""
from __future__ import annotations

import logging

import pytest

from app.monitoring.log_buffer import LogBuffer, log_buffer


def test_log_buffer_captures_recent_records():
    buf = LogBuffer(capacity=10)
    logger = logging.getLogger("app.test.buf")
    logger.addHandler(buf)
    logger.setLevel(logging.DEBUG)

    logger.info("hello world")
    logger.error("boom")

    app = buf.tail("application", limit=10)
    err = buf.tail("error", limit=10)

    assert any("hello world" in e["message"] for e in app)
    assert any("boom" in e["message"] for e in err)


def test_log_buffer_categorises_by_logger_name():
    buf = LogBuffer(capacity=10)
    logging.getLogger("app.security.foo").addHandler(buf)
    logging.getLogger("app.ai.bar").addHandler(buf)
    logging.getLogger("app.security.foo").setLevel(logging.INFO)
    logging.getLogger("app.ai.bar").setLevel(logging.INFO)

    logging.getLogger("app.security.foo").info("login blocked")
    logging.getLogger("app.ai.bar").info("prompt executed")

    assert any("login blocked" in e["message"] for e in buf.tail("security"))
    assert any("prompt executed" in e["message"] for e in buf.tail("ai"))


def test_log_buffer_filters_by_level_and_contains():
    buf = LogBuffer(capacity=20)
    logger = logging.getLogger("app.test.filter")
    logger.addHandler(buf)
    logger.setLevel(logging.DEBUG)

    logger.info("apple pie")
    logger.warning("banana bread")
    logger.error("cherry cake")

    only_warn = buf.tail("application", level="WARNING", limit=20)
    assert all(e["level"] == "WARNING" for e in only_warn)

    only_cherry = buf.tail("application", contains="cherry", limit=20)
    assert all("cherry" in e["message"].lower() for e in only_cherry)


@pytest.mark.asyncio
async def test_log_buffer_singleton_is_wired_via_configure_logging():
    # configure_logging() is invoked at module import; the singleton
    # should already be attached to the root logger.
    root = logging.getLogger()
    assert log_buffer in root.handlers or any(
        isinstance(h, LogBuffer) for h in root.handlers
    )
