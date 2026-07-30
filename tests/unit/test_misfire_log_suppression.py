"""APScheduler's own 'Run time of job "X" was missed by Yh' warning is
expected under misfire_grace_time=900s (laptop asleep/busy), not a bug —
demoted to DEBUG so it doesn't read as an error alongside the more
informative custom catch-up log lines."""
from __future__ import annotations

import logging

import runner  # noqa: F401 — importing installs the filter as a side effect


def test_misfire_warning_demoted_to_debug():
    logger = logging.getLogger("apscheduler.executors")
    record = logger.makeRecord(
        logger.name, logging.WARNING, __file__, 0,
        'Run time of job "forced_trade_job" was missed by 4:22:10.123456', (), None)

    filters = [f for f in logger.filters if isinstance(f, runner._MisfireLogFilter)]
    assert filters, "misfire filter must be installed on the apscheduler.executors logger"

    result = filters[0].filter(record)
    assert result is True
    assert record.levelno == logging.DEBUG
    assert record.levelname == "DEBUG"


def test_unrelated_warning_untouched():
    logger = logging.getLogger("apscheduler.executors")
    record = logger.makeRecord(
        logger.name, logging.WARNING, __file__, 0,
        "Some other executor warning", (), None)

    filters = [f for f in logger.filters if isinstance(f, runner._MisfireLogFilter)]
    filters[0].filter(record)
    assert record.levelno == logging.WARNING
    assert record.levelname == "WARNING"
