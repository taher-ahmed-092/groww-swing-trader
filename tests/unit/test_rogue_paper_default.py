"""Paper mode defaults to ROGUE judge thresholds; live mode always stays
balanced; an explicit TRADING_MODE env var always wins either way."""
from __future__ import annotations

import os

from config.settings import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_paper_mode_defaults_to_rogue(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    s = _settings(live_trading_enabled=False)
    assert s.broker_mode == "paper"
    assert s.effective_trading_mode == "rogue"


def test_live_mode_defaults_to_balanced_even_if_paper_trading_mode_set(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    s = _settings(live_trading_enabled=True, groww_api_key="k",
                  groww_api_secret="s", groww_access_token="t", trading_mode="rogue")
    assert s.broker_mode == "live"
    assert s.effective_trading_mode == "balanced"


def test_explicit_trading_mode_env_wins_in_paper_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "conserve")
    s = _settings(live_trading_enabled=False, trading_mode="conserve")
    assert s.effective_trading_mode == "conserve"
    monkeypatch.delenv("TRADING_MODE", raising=False)
