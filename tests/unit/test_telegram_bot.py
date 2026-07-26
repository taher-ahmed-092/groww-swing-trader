"""Tests for src/notifications/telegram_bot.py — Markdown parse-error fallback
and the escape_markdown helper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.notifications.telegram_bot import TelegramNotifier, escape_markdown


def _make_notifier() -> TelegramNotifier:
    notifier = TelegramNotifier.__new__(TelegramNotifier)
    notifier.token = "test-token"
    notifier.chat_id = "12345"
    notifier.enabled = True
    return notifier


def _response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def test_send_message_retries_without_markdown_on_parse_error():
    notifier = _make_notifier()
    parse_error = _response(
        {"ok": False, "error_code": 400,
         "description": "Bad Request: can't parse entities: Character '_' is reserved"},
        status_code=400,
    )
    success = _response({"ok": True, "result": {"message_id": 1}})

    with patch("src.notifications.telegram_bot.requests.post",
               side_effect=[parse_error, success]) as mock_post:
        result = notifier.send_message("SHORT_SIM engine flagged this trade")

    assert result is True
    assert mock_post.call_count == 2
    first_kwargs = mock_post.call_args_list[0].kwargs["json"]
    second_kwargs = mock_post.call_args_list[1].kwargs["json"]
    assert first_kwargs.get("parse_mode") == "Markdown"
    assert "parse_mode" not in second_kwargs
    assert second_kwargs["text"] == "SHORT_SIM engine flagged this trade"


def test_send_message_does_not_retry_on_non_parse_failure():
    """A non-parse-entity failure (e.g. bad chat_id) must not trigger the
    Markdown-fallback retry — retrying would just fail identically again."""
    notifier = _make_notifier()
    other_error = _response(
        {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
        status_code=400,
    )

    with patch("src.notifications.telegram_bot.requests.post",
               return_value=other_error) as mock_post:
        result = notifier.send_message("hello")

    assert result is False
    assert mock_post.call_count == 1


def test_send_trade_card_with_keyboard_retries_without_markdown_on_parse_error():
    notifier = _make_notifier()
    parse_error = _response(
        {"ok": False, "error_code": 400,
         "description": "Bad Request: can't parse entities"},
        status_code=400,
    )
    success = _response({"ok": True, "result": {"message_id": 42}})
    state = {"symbol": "TEST_STOCK", "risk_check": {"position_size_inr": 1000}}

    with patch.object(notifier, "_build_trade_card_text", return_value="card text"), \
         patch("src.notifications.telegram_bot.requests.post",
               side_effect=[parse_error, success]) as mock_post:
        message_id = notifier.send_trade_card_with_keyboard(state)

    assert message_id == "42"
    assert mock_post.call_count == 2
    second_kwargs = mock_post.call_args_list[1].kwargs["json"]
    assert "parse_mode" not in second_kwargs
    assert second_kwargs["reply_markup"] == mock_post.call_args_list[0].kwargs["json"]["reply_markup"]


def test_escape_markdown_neutralizes_special_characters():
    assert escape_markdown("SHORT_SIM *bold* `code` [link") == (
        r"SHORT\_SIM \*bold\* \`code\` \[link"
    )


def test_escape_markdown_empty_string_passthrough():
    assert escape_markdown("") == ""
