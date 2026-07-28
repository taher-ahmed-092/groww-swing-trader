"""Telegram command consolidation — 16 flagship commands, old ones redirect."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.notifications.command_handler import CommandHandler


def _handler():
    h = CommandHandler.__new__(CommandHandler)
    h.tg = MagicMock()
    h.tg.chat_id = "123"
    h.commands = {}
    CommandHandler.__init__(h)
    return h


def test_final_command_count_16():
    # The spec's own enumerated command set (/start /report /scan /positions
    # /trades /learn /chart /mode /trade /regime /brief /watchlist /alert
    # /pause /kill /reset_kill /dashboard) lists 17 commands despite the "16"
    # header label — the concrete list is the ground truth. Later sessions
    # added /flows, /diagnose, and /health_check on top of that 17, and this
    # session added /jobs (job telemetry) and /stock (company dossier).
    h = _handler()
    assert len(h._MENU) == 22


def test_old_commands_redirect():
    h = _handler()
    sent = []
    h._send = lambda text: sent.append(text)
    # /totals is retired -> redirects into /report without crashing.
    handler = h.commands["/totals"]
    handler([])
    assert sent  # something was sent, no exception raised
    assert any("Merged into" in s or "/report" in s for s in sent[:1])
