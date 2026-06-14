"""TelegramNotifier sync behavior (no credentials in test env)."""
from __future__ import annotations

from config.settings import settings
from src.notifications.telegram_bot import TelegramNotifier


def test_send_message_no_token(monkeypatch):
    # Force the no-credentials path regardless of the local .env.
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    n = TelegramNotifier()
    assert n.is_configured() is False
    assert n.send_message("hello") is False  # no raise, returns False


def test_build_trade_card_no_crash():
    text = TelegramNotifier()._build_trade_card_text({})
    assert isinstance(text, str) and len(text) > 0


def test_build_trade_card_contains_symbol():
    state = {
        "symbol": "RELIANCE",
        "technical_verdict": {"signal": "BUY", "score": 0.7, "entry_price": 100,
                              "stop_price": 93, "target_price": 114},
        "fundamental_verdict": {"score": 0.6},
        "judge_verdict": {"overall_score": 7.5, "flags": []},
        "gut_check": {"gut_score": 7.0, "gut_verdict": "YES"},
        "risk_check": {"position_size_inr": 300, "confidence_tier": "MEDIUM"},
    }
    assert "RELIANCE" in TelegramNotifier()._build_trade_card_text(state)
